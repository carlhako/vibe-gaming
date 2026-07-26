"""
agent — ReAct (reason -> act -> observe) editing agent for multi-file games
(Sprint 2 of docs/multifile-agent/). Ships headless: verified via DB/logs,
no chat UI yet (Sprints 3-4 add the event stream and the live transcript).

Where game_generator/game_enhancer make the model resend a COMPLETE
index.html on every attempt, this agent lets the model explore a
multi-file game's `src/` tree with tools and rewrite only the modules a
change touches — read_map()/list_files()/read_file(path) to look around,
write_file(path, contents) to replace one whole module, finish(summary) to
trigger verification. No single model turn ever contains (or requires
reading) the whole game, so the output-token ceiling that motivated
Sprint 1 (see docs/multifile-agent/00-overview.md) stops being a structural
problem: a per-module size ceiling (`max_module_bytes`) is enforced on every
write_file call instead.

Enhancing a multi-file game still forks exactly like game_enhancer.enhance_game
today: a brand-new games/<slug>/ is written (new game_id/slug,
parent_game_id/root_game_id linking back to the source), the source
directory is never touched, and a failed run deletes the half-written fork.
The agent's edits apply to a COPY of the source's src/ + game.md staged in
the new directory — its own meta.json and built index.html are written only
once the run passes verification.

# Exports:
#   class AgentError(Exception)
#   enhance_multifile_game(source_game_id, description, requested_by, config,
#                          db_conn=None, games_dir=None, job_id=None,
#                          new_title=None, creator_uid=None, emit=None) -> dict
#   explode_game(source_game_id, requested_by, config, db_conn=None,
#                games_dir=None, job_id=None, new_title=None, creator_uid=None,
#                emit=None, announce_completion=True) -> dict
#     (Sprint 5: AI-assisted single-file -> multi-file split, behavior-
#     preserving. Forks like enhance_multifile_game; see docstring.)
#   enhance_game_auto_format(source_game_id, description, requested_by, config,
#                            db_conn=None, games_dir=None, job_id=None,
#                            new_title=None, creator_uid=None, emit=None) -> dict
#     (Sprint 5's dual-format policy: job_runner's single dispatch point for
#     kind='enhance' — routes to enhance_multifile_game, explode_game +
#     enhance_multifile_game, or game_enhancer.enhance_game based on the
#     source's on-disk format/size.)
#   is_multi_file_source(source_game_id, games_dir, conn=None) -> bool
#   AGENT_TOOLS, DEFAULT_MAX_MODULE_BYTES
#
# Sprint 3 (docs/multifile-agent/03-agent-event-stream.md) makes the loop
# observable: every think/act/observe/verify/finish step is emitted through
# an `emit(role, content=None, data=None)` callback. The default callback
# (built here when the caller doesn't pass one) writes a durable
# db.add_agent_event row keyed by job_id, which GET /api/jobs/<job_id>/events
# polls incrementally. Emitting is best-effort everywhere it's called — a
# raising emitter is caught and logged, never allowed to fail the job.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import time
from pathlib import Path
from typing import Callable

import ai_client as ai
import builder
import db
import game_enhancer as ge
import game_generator as gg
import safety

_logger = logging.getLogger(__name__)

# ai_client.MAX_OUTPUT_TOKENS (65536) is DeepSeek's hard per-response
# completion-token ceiling — at ~4 chars/token that's ~256KB of raw HTML
# (see docs/multifile-agent/00-overview.md). A write_file call has to emit
# its contents as a JSON-escaped tool-call argument (quotes/backslashes/
# newlines all cost extra bytes over the raw source), and in thinking mode
# the same budget is shared with reasoning_content, so this default sits at
# 3x MAX_OUTPUT_TOKENS bytes rather than the full ~256KB raw figure —
# comfortable headroom, not a hard physical limit. Configurable per-call via
# cfg["max_module_bytes"].
DEFAULT_MAX_MODULE_BYTES = ai.MAX_OUTPUT_TOKENS * 3

_MAX_NO_PROGRESS_STEPS = 5


class AgentError(Exception):
    """Recoverable failure inside one tool call. Never escapes the loop —
    always turned into an "ERROR: ..." observation fed back to the model."""


# ---------------------------------------------------------------------------
# Tools exposed to the model
# ---------------------------------------------------------------------------

READ_MAP_TOOL = {
    "type": "function",
    "function": {
        "name": "read_map",
        "description": (
            "Read game.md: this game's prose description plus a table of "
            "every src/ file and its purpose. Call this first, before "
            "reading any individual module."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

LIST_FILES_TOOL = {
    "type": "function",
    "function": {
        "name": "list_files",
        "description": (
            "List every file under src/ with its byte size, so you can "
            "budget which modules to read."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

READ_FILE_TOOL = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": (
            "Read the full current contents of one file: a src/ module "
            "path (e.g. 'core.js') or 'game.md'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "e.g. 'core.js' or 'game.md'."},
            },
            "required": ["path"],
        },
    },
}

WRITE_FILE_TOOL = {
    "type": "function",
    "function": {
        "name": "write_file",
        "description": (
            "Replace one whole file with new contents, creating it if it "
            "doesn't exist yet. Always pass the COMPLETE file, never a diff "
            "or fragment. If you add, remove, split, or rename any src/ "
            "file, also call write_file(\"game.md\", ...) to keep the map "
            "accurate. A write over the module size ceiling is rejected — "
            "split the module into smaller, cohesive files instead of "
            "trying to shrink it."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "e.g. 'core.js' or 'game.md'."},
                "contents": {"type": "string", "description": "The complete new file contents."},
            },
            "required": ["path", "contents"],
        },
    },
}

FINISH_TOOL = {
    "type": "function",
    "function": {
        "name": "finish",
        "description": (
            "Call this when the requested change is complete. Triggers a "
            "build + safety scan + smoke test of the whole game. If "
            "verification fails you'll get the concrete failure back as "
            "this call's result — fix it and call finish again."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "One or two sentences summarizing what changed.",
                },
            },
            "required": ["summary"],
        },
    },
}

AGENT_TOOLS = [READ_MAP_TOOL, LIST_FILES_TOOL, READ_FILE_TOOL, WRITE_FILE_TOOL, FINISH_TOOL]


# ---------------------------------------------------------------------------
# Path resolution — same escape/absolute-path discipline as builder.py
# ---------------------------------------------------------------------------

def _resolve_agent_path(game_dir: Path, path: str) -> Path:
    path = path.strip()
    if not path:
        raise AgentError("empty path")
    if path in ("game.md", "./game.md"):
        return game_dir / "game.md"
    if path.startswith("/"):
        raise AgentError(f"absolute path not allowed: {path!r}")
    src_dir = (game_dir / "src").resolve()
    resolved = (game_dir / "src" / path).resolve()
    try:
        resolved.relative_to(src_dir)
    except ValueError:
        raise AgentError(f"path escapes src/: {path!r}") from None
    return resolved


# ---------------------------------------------------------------------------
# Tool argument parsing
# ---------------------------------------------------------------------------

def _parse_path_arg(arguments_json: str) -> str:
    try:
        args = json.loads(arguments_json)
    except json.JSONDecodeError as exc:
        raise AgentError(f"malformed arguments: not valid JSON: {exc}") from None
    if not isinstance(args, dict):
        raise AgentError("malformed arguments: must be a JSON object")
    path = args.get("path")
    if not isinstance(path, str) or not path.strip():
        raise AgentError("malformed arguments: missing a non-empty 'path'")
    return path.strip()


def _parse_write_args(arguments_json: str) -> tuple[str, str]:
    try:
        args = json.loads(arguments_json)
    except json.JSONDecodeError as exc:
        raise AgentError(
            f"malformed write_file arguments: not valid JSON ({exc}) — if this "
            "module's contents are very large, the reply may have been cut "
            "off by the output length limit; split it into smaller files "
            "and try again."
        ) from None
    if not isinstance(args, dict):
        raise AgentError("malformed write_file arguments: must be a JSON object")
    path = args.get("path")
    contents = args.get("contents")
    if not isinstance(path, str) or not path.strip():
        raise AgentError("malformed write_file arguments: missing a non-empty 'path'")
    if not isinstance(contents, str):
        raise AgentError("malformed write_file arguments: missing 'contents'")
    return path.strip(), contents


def _parse_finish_summary(arguments_json: str) -> str:
    try:
        args = json.loads(arguments_json)
    except json.JSONDecodeError:
        return ""
    if not isinstance(args, dict):
        return ""
    summary = args.get("summary")
    return summary.strip() if isinstance(summary, str) else ""


# ---------------------------------------------------------------------------
# Tool execution
# ---------------------------------------------------------------------------

def _read_map(game_dir: Path) -> str:
    map_path = game_dir / "game.md"
    if not map_path.is_file():
        return "ERROR: game.md not found"
    return map_path.read_text(encoding="utf-8")


def _list_files(game_dir: Path) -> str:
    src_dir = game_dir / "src"
    if not src_dir.is_dir():
        return "ERROR: src/ not found"
    entries = [
        {"path": p.relative_to(src_dir).as_posix(), "bytes": p.stat().st_size}
        for p in src_dir.rglob("*") if p.is_file()
    ]
    entries.sort(key=lambda e: e["path"])
    return json.dumps(entries)


def _read_file(game_dir: Path, path: str) -> str:
    file_path = _resolve_agent_path(game_dir, path)
    if not file_path.is_file():
        return f"ERROR: {path!r} not found"
    return file_path.read_text(encoding="utf-8")


def _write_file(game_dir: Path, path: str, contents: str, max_module_bytes: int) -> str:
    file_path = _resolve_agent_path(game_dir, path)
    size = len(contents.encode("utf-8"))
    if size > max_module_bytes:
        return (
            f"REJECTED: {path!r} is {size} bytes, over the {max_module_bytes}-byte "
            "module size ceiling. Split this module into smaller, cohesive "
            "files instead of shrinking it — update game.md's file table to "
            "match if you do."
        )
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(contents, encoding="utf-8")
    return f"OK: wrote {size} bytes to {path}"


def _execute_tool(tc: ai.ToolCall, game_dir: Path, max_module_bytes: int) -> tuple[str, str | None]:
    """Run one non-finish tool call. Returns (observation, touched_path) —
    touched_path is the path read/written (for staleness pruning), or None
    for tools that don't target a single file. Never raises: an AgentError
    becomes an "ERROR: ..." observation, same as a rejected submit_game
    becomes "REJECTED: ..." in game_generator's loop."""
    try:
        if tc.name == "read_map":
            return _read_map(game_dir), None
        if tc.name == "list_files":
            return _list_files(game_dir), None
        if tc.name == "read_file":
            path = _parse_path_arg(tc.arguments)
            return _read_file(game_dir, path), path
        if tc.name == "write_file":
            path, contents = _parse_write_args(tc.arguments)
            return _write_file(game_dir, path, contents, max_module_bytes), path
        return f"ERROR: unknown tool {tc.name!r}", None
    except AgentError as exc:
        return f"ERROR: {exc}", None


def _classify_failure(detail: str) -> str:
    if detail.startswith("build failed"):
        return "build_error"
    if detail.startswith("safety violation"):
        return "safety_violation"
    return "smoke_test_failed"


# ---------------------------------------------------------------------------
# Event emission (Sprint 3) — durable, ordered think/act/observe transcript
# ---------------------------------------------------------------------------

_THOUGHT_MAX_CHARS = 4000
_ASSISTANT_MAX_CHARS = 2000


def _make_emitter(job_id: str | None, db_conn) -> Callable:
    """Default emit callback: writes a db.add_agent_event row. A no-op when
    there's no job_id to key events on (e.g. headless/direct calls in
    tests) — mirrors the rest of this module's "job_id is optional,
    everything DB-shaped becomes a no-op without it" convention."""
    if job_id is None:
        return lambda role, content=None, data=None: None
    return lambda role, content=None, data=None: db.add_agent_event(
        job_id, role, content=content, data=data, conn=db_conn
    )


def _safe_emit(emit: Callable, role: str, content: str | None = None, data: dict | None = None) -> None:
    """Emitting must never fail the job — same swallow-and-log discipline as
    game_generator.run_moderation_pass. A raising emitter (or one that hits
    a transient DB error) just means this one event is lost, not the run."""
    try:
        emit(role, content, data)
    except Exception:
        _logger.exception("agent event emit failed (role=%s)", role)


def _reasoning_content(ask_result: ai.ToolAskResult) -> str | None:
    """Thinking-mode chain-of-thought, when present, pulled from the raw
    response — ai_client.ask_with_tools() strips reasoning_content from the
    returned .message (DeepSeek rejects it echoed back), so the raw payload
    is the only place left carrying it."""
    try:
        choices = ask_result.raw_response.get("choices") or []
        message = choices[0].get("message") or {}
    except (AttributeError, IndexError, TypeError):
        return None
    text = message.get("reasoning_content")
    return text.strip() if isinstance(text, str) and text.strip() else None


def _summarize_tool_call(tc: ai.ToolCall) -> tuple[str, dict]:
    """A tool_call event's (content, data) — never the full write_file
    contents, just the path + byte count, so nothing here duplicates the
    file bytes that already live in src/."""
    if tc.name == "read_file":
        try:
            path = _parse_path_arg(tc.arguments)
            return f"read_file({path!r})", {"tool": tc.name, "path": path}
        except AgentError:
            return "read_file(...)", {"tool": tc.name}
    if tc.name == "write_file":
        try:
            path, contents = _parse_write_args(tc.arguments)
            size = len(contents.encode("utf-8"))
            return f"write_file({path!r}, {size} bytes)", {"tool": tc.name, "path": path, "bytes": size}
        except AgentError:
            return "write_file(...)", {"tool": tc.name}
    if tc.name == "finish":
        summary = _parse_finish_summary(tc.arguments)
        data = {"tool": tc.name}
        if summary:
            data["summary"] = summary
        return "finish(...)", data
    return f"{tc.name}()", {"tool": tc.name}


def _summarize_observation(tc_name: str, path: str | None, observation: str) -> tuple[str, dict]:
    """A tool_result event's (content, data) for a non-finish tool call.
    read_map/read_file observations ARE the file's full contents, so those
    get replaced with a short "read N bytes" summary; write_file's
    observation is already just an OK/REJECTED line (safe to keep
    verbatim); list_files' observation is a small path+size listing, not
    file bytes, so it's also kept verbatim."""
    data: dict = {"tool": tc_name}
    if path:
        data["path"] = path
    if tc_name in ("read_file", "read_map"):
        if observation.startswith("ERROR:"):
            return observation, data
        size = len(observation.encode("utf-8"))
        data["bytes"] = size
        label = path if tc_name == "read_file" else "game.md"
        return f"Read {label} ({size} bytes)", data
    if tc_name == "write_file":
        data["outcome"] = "ok" if observation.startswith("OK:") else "rejected"
        return observation, data
    if tc_name == "list_files":
        try:
            data["file_count"] = len(json.loads(observation))
        except (json.JSONDecodeError, TypeError):
            pass
        return observation, data
    return observation, data


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------

def _build_system_prompt(source_title: str) -> str:
    allowed_hosts = ", ".join(sorted(safety.ALLOWED_CDN_HOSTS))
    return (
        f"You are enhancing an existing multi-file browser game in the "
        f"arcade, currently titled '{source_title}'. This produces a NEW "
        "game entry — the original is left completely untouched; you are "
        "editing a fresh copy that becomes a new forked entry once your "
        "changes pass verification.\n\n"
        "## How this game is structured\n"
        "The game's source is split across multiple files instead of one "
        "big index.html, so you never have to read or rewrite the whole "
        "game at once. Explore before you edit, using these tools:\n"
        "- read_map() — game.md: a prose description plus a table of every "
        "src/ file and its purpose.\n"
        "- list_files() — every src/ file with its byte size.\n"
        "- read_file(path) — the full contents of one src/ file (or "
        "game.md).\n"
        "- write_file(path, contents) — replace ONE WHOLE file (or create a "
        "new one). Always the complete file, never a diff. Rejected if over "
        "the module size ceiling — split it instead of shrinking it.\n"
        "- finish(summary) — call once you're done. Triggers a build + "
        "safety scan + smoke test; a failure comes back as this call's "
        "result so you can keep editing and call finish again.\n\n"
        "## Contract\n"
        "All HTML/CSS/JS stays inline within the src/ files (no separate "
        "asset files). You may load external JavaScript modules or "
        "stylesheets via <script>/<link> tags ONLY from these CDN hosts: "
        f"{allowed_hosts}. Do not reference any other external host, and do "
        "not attempt any network calls back to this site or anywhere else "
        "at runtime.\n\n"
        "## Sandbox constraints\n"
        "The game is played inside a sandboxed <iframe> with no "
        "same-origin access: document.cookie, localStorage, sessionStorage, "
        "indexedDB, and window.parent/window.top are all unavailable — keep "
        "all game state in ordinary JavaScript variables. Do not use "
        "eval() or `new Function(...)`.\n\n"
        "## Structural changes\n"
        "If you add, remove, split, or rename any src/ file, you MUST "
        "update game.md via write_file(\"game.md\", ...) so the map stays "
        "accurate for the next enhancement.\n\n"
        "## Quality bar\n"
        "Apply the requested change while preserving everything else about "
        "the game the request doesn't ask you to touch — its feel, "
        "controls, and existing polish are working and should survive "
        "unrelated edits. Only touch the module(s) the change actually "
        "requires; leave everything else byte-for-byte as it was.\n"
    )


def _build_user_prompt(description: str) -> str:
    return f"Enhance/fix this game per this request: {description}"


def _build_explode_system_prompt(source_title: str, source_html: str) -> str:
    """Sprint 5's explode pass (docs/multifile-agent/05-migration-and-pilot.md
    Part A): the model is handed the ENTIRE original single-file game as
    plain input context (reading it costs input tokens only, never subject
    to the output-token ceiling that motivates this whole initiative) and
    re-emits it split across several bounded write_file calls instead of
    one whole-game submission — the same trick that lets explode work even
    on a game already too large to ever be resubmitted whole."""
    allowed_hosts = ", ".join(sorted(safety.ALLOWED_CDN_HOSTS))
    return (
        f"You are converting an existing single-file browser game, "
        f"currently titled '{source_title}', into this arcade's multi-file "
        "format. This produces a NEW game entry — the original single-file "
        "game is left completely untouched.\n\n"
        "## Task\n"
        "Split the single index.html below into a shell src/index.html "
        "plus cohesive src/style.css and src/*.js modules, and author "
        "game.md (a prose description of the game plus a table of every "
        "src/ file and its purpose). This MUST be behavior-preserving: "
        "every mechanic, visual, and control must work identically to the "
        "original — you are re-organizing the code, not rewriting the "
        "game, adding features, or fixing bugs you happen to notice.\n\n"
        "Use write_file(path, contents) for every file you create — e.g. "
        "write_file(\"index.html\", ...) for the src/ shell, "
        "write_file(\"style.css\", ...), write_file(\"core.js\", ...), and "
        "write_file(\"game.md\", ...) for the map. You may use "
        "read_file(path)/list_files() to check back on files you've "
        "already written. Call finish(summary) only once EVERY file "
        "(including src/index.html and game.md) has been written — it "
        "triggers a build + safety scan + smoke test of the assembled "
        "result; a failure comes back as this call's result so you can "
        "keep editing and call finish again.\n\n"
        "## Contract\n"
        "All HTML/CSS/JS stays inline within the src/ files (no separate "
        "asset files). You may load external JavaScript modules or "
        "stylesheets via <script>/<link> tags ONLY from these CDN hosts: "
        f"{allowed_hosts}. Do not reference any other external host, and do "
        "not attempt any network calls back to this site or anywhere else "
        "at runtime.\n\n"
        "## Sandbox constraints\n"
        "The game is played inside a sandboxed <iframe> with no "
        "same-origin access: document.cookie, localStorage, sessionStorage, "
        "indexedDB, and window.parent/window.top are all unavailable — keep "
        "all game state in ordinary JavaScript variables. Do not use "
        "eval() or `new Function(...)`.\n\n"
        "## Original single-file game\n"
        f"```html\n{source_html}\n```\n"
    )


def _build_explode_user_prompt() -> str:
    return (
        "Split this game into the multi-file format described above. Do "
        "not add, remove, or change any feature, visual, or control — the "
        "played game must behave exactly as it does now."
    )


# ---------------------------------------------------------------------------
# The ReAct loop
# ---------------------------------------------------------------------------

def _run_react_loop(*, game_dir: Path, system_prompt: str, user_prompt: str,
                     cfg: dict, job_id: str | None, db_conn, emit: Callable | None = None) -> dict:
    """Drive the read_map/list_files/read_file/write_file/finish loop
    against game_dir until finish() passes build->scan->smoke, the
    verification-retry budget is exhausted, or the step budget runs out.
    game_dir is either an already-staged copy of a multi-file source's
    src/+game.md (enhance_multifile_game) or an empty directory the model
    populates from scratch via write_file (explode_game, Sprint 5) —
    `_write_file`/`builder.is_multi_file`'s src/index.html check both cope
    with the latter starting out empty.

    Returns a dict: success/summary/attempts/input_tokens/output_tokens/
    tokens_used/model/effort/error. Does not touch games_dir bookkeeping,
    the DB registry, or rollback — the caller (enhance_multifile_game,
    explode_game) owns all of that, same division of labor as
    game_generator.run_generation_attempts() vs. its callers.

    `emit(role, content=None, data=None)` (Sprint 3) is called at every
    think/act/observe/verify step for the durable agent_events transcript;
    defaults to a DB-writing emitter keyed on job_id (a no-op if job_id is
    None). Every call is wrapped in _safe_emit so a raising emitter can't
    fail the run.
    """
    max_steps = cfg.get("max_steps", 40)
    max_verification_retries = cfg.get("max_verification_retries", 3)
    max_module_bytes = cfg.get("max_module_bytes", DEFAULT_MAX_MODULE_BYTES)
    model = cfg.get("model", "")
    effort = cfg.get("effort", "high")
    ai_timeout = cfg.get("timeout_seconds", 120)
    if emit is None:
        emit = _make_emitter(job_id, db_conn)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    total_input_tokens = 0
    total_output_tokens = 0
    last_model = model or "default"
    last_effort = effort
    verification_attempts = 0
    tokens_at_last_attempt = (0, 0)
    last_read_message: dict[str, dict] = {}
    consecutive_no_progress = 0
    summary = ""
    error = None
    success = False

    def record_verification(outcome: str, detail: str | None) -> None:
        nonlocal tokens_at_last_attempt
        if job_id is not None:
            in_delta = total_input_tokens - tokens_at_last_attempt[0]
            out_delta = total_output_tokens - tokens_at_last_attempt[1]
            db.add_generation_attempt(
                job_id, verification_attempts, outcome, detail=detail,
                input_tokens=in_delta, tokens_used=out_delta, conn=db_conn,
            )
        tokens_at_last_attempt = (total_input_tokens, total_output_tokens)

    for _step in range(1, max_steps + 1):
        try:
            ask_result = ai.ask_with_tools(
                messages, tools=AGENT_TOOLS, tool_choice="auto",
                model=model, effort=effort, timeout=ai_timeout,
            )
        except ai.AIError as exc:
            error = f"AI error: {exc}"
            break

        total_input_tokens += ask_result.input_tokens
        total_output_tokens += ask_result.output_tokens
        last_model = ask_result.model or "default"
        last_effort = ask_result.effort
        messages.append(ask_result.message)

        thought = _reasoning_content(ask_result)
        if thought:
            _safe_emit(emit, "thought", thought[:_THOUGHT_MAX_CHARS])
        if ask_result.text:
            _safe_emit(emit, "assistant", ask_result.text[:_ASSISTANT_MAX_CHARS])

        if not ask_result.tool_calls:
            messages.append({
                "role": "user",
                "content": "You must call one of your tools (read_map/list_files/"
                           "read_file/write_file/finish) every turn.",
            })
            consecutive_no_progress += 1
            if consecutive_no_progress >= _MAX_NO_PROGRESS_STEPS:
                error = (
                    f"agent made no progress: {_MAX_NO_PROGRESS_STEPS} "
                    "consecutive turns with no tool call"
                )
                break
            continue

        finish_calls = [tc for tc in ask_result.tool_calls if tc.name == "finish"]
        other_calls = [tc for tc in ask_result.tool_calls if tc.name != "finish"]
        made_progress = False

        for tc in other_calls:
            call_content, call_data = _summarize_tool_call(tc)
            _safe_emit(emit, "tool_call", call_content, call_data)
            content, touched_path = _execute_tool(tc, game_dir, max_module_bytes)
            obs_content, obs_data = _summarize_observation(tc.name, touched_path, content)
            _safe_emit(emit, "tool_result", obs_content, obs_data)
            msg = {"role": "tool", "tool_call_id": tc.id, "content": content}
            messages.append(msg)
            if tc.name == "read_file" and touched_path and not content.startswith("ERROR:"):
                last_read_message[touched_path] = msg
            elif tc.name == "write_file" and touched_path and content.startswith("OK:"):
                made_progress = True
                stale = last_read_message.pop(touched_path, None)
                if stale is not None:
                    stale["content"] = (
                        f"[pruned: {touched_path} was rewritten by a later "
                        "write_file — re-read it if you need the current contents]"
                    )

        for extra in finish_calls[1:]:
            messages.append({
                "role": "tool", "tool_call_id": extra.id,
                "content": "Ignored: only one finish call per turn.",
            })

        if finish_calls:
            finish_call = finish_calls[0]
            call_content, call_data = _summarize_tool_call(finish_call)
            _safe_emit(emit, "tool_call", call_content, call_data)
            candidate_summary = _parse_finish_summary(finish_call.arguments)
            passed, detail, _built_html = builder.build_and_verify(game_dir)
            verification_attempts += 1
            if passed:
                record_verification("success", None)
                _safe_emit(emit, "build",
                           "Verification passed: build, safety scan, and smoke test all succeeded.",
                           {"outcome": "success", "attempt": verification_attempts})
                messages.append({
                    "role": "tool", "tool_call_id": finish_call.id,
                    "content": "Verification passed: build, safety scan, and smoke test all succeeded.",
                })
                summary = candidate_summary
                success = True
                break
            outcome = _classify_failure(detail)
            record_verification(outcome, detail)
            _safe_emit(emit, "build", f"Verification failed: {detail}",
                       {"outcome": outcome, "attempt": verification_attempts})
            error = detail
            if verification_attempts >= max_verification_retries:
                messages.append({
                    "role": "tool", "tool_call_id": finish_call.id,
                    "content": f"REJECTED: {detail}",
                })
                break
            messages.append({
                "role": "tool", "tool_call_id": finish_call.id,
                "content": f"REJECTED: {detail}\n\nFix the problem and call finish again.",
            })
            continue

        consecutive_no_progress = 0 if made_progress else consecutive_no_progress + 1
        if consecutive_no_progress >= _MAX_NO_PROGRESS_STEPS:
            error = (
                f"agent made no progress: {_MAX_NO_PROGRESS_STEPS} consecutive "
                "turns without a successful write_file"
            )
            break

    return {
        "success": success,
        "summary": summary,
        "attempts": verification_attempts,
        "input_tokens": total_input_tokens,
        "output_tokens": total_output_tokens,
        "tokens_used": total_input_tokens + total_output_tokens,
        "model": last_model,
        "effort": last_effort,
        "error": None if success else (error or "agent gave up without a passing finish"),
    }


# ---------------------------------------------------------------------------
# Fork staging
# ---------------------------------------------------------------------------

def _stage_fork(source_dir: Path, dest_dir: Path) -> None:
    """Copy only the editable multi-file source (src/ + game.md) into a
    fresh fork directory — never the source's meta.json (the fork mints its
    own identity only once the agent's edits pass verification) or its
    built index.html (build_and_verify regenerates that from the agent's
    own edits)."""
    dest_dir.mkdir(parents=True, exist_ok=False)
    shutil.copytree(source_dir / "src", dest_dir / "src")
    game_md = source_dir / "game.md"
    if game_md.is_file():
        shutil.copy2(game_md, dest_dir / "game.md")


# ---------------------------------------------------------------------------
# job_runner dispatch check
# ---------------------------------------------------------------------------

def is_multi_file_source(source_game_id: str, games_dir, conn=None) -> bool:
    """Whether source_game_id's on-disk game is multi-file, for job_runner's
    enhance dispatch (route here vs. game_enhancer.enhance_game()). Returns
    False — routing to the legacy path — if the game can't be resolved at
    all; that path's own resolve_target() raises the real, user-facing
    error, so this never needs to duplicate it."""
    row = db.get_web_game(source_game_id, conn=conn)
    if row is None:
        return False
    return builder.is_multi_file(Path(games_dir) / row["slug"])


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def _failure_result(exc: Exception, cfg: dict, t0: float) -> dict:
    return {
        "success": False, "game_id": None, "slug": None, "title": None,
        "description": None, "attempts": 0,
        "input_tokens": 0, "output_tokens": 0, "tokens_used": 0, "model": "default",
        "effort": cfg.get("effort", "high"), "duration_seconds": time.monotonic() - t0,
        "error": str(exc), "notes": "", "url": None,
        "parent_game_id": None, "root_game_id": None,
    }


def enhance_multifile_game(source_game_id: str, description: str, requested_by: str,
                            config: dict, db_conn=None, games_dir: Path | None = None,
                            job_id: str | None = None, new_title: str | None = None,
                            creator_uid: str | None = None, emit: Callable | None = None) -> dict:
    """Drive the fork -> ReAct-edit -> verify loop for a multi-file source
    game and return a result dict in the same shape as
    game_enhancer.enhance_game() (result["message"] is ready to display; DB
    registration already performed on success before returning).

    On success this writes a brand-new games/<slug>/ directory and
    web_games row (parent_game_id=source_game_id,
    root_game_id=source's root_game_id) — the source game is never
    modified. On failure the half-written fork directory is deleted.

    `emit` (Sprint 3) is passed straight through to _run_react_loop and is
    also used here for the terminal 'final'/'error' event, once the fork's
    slug/title/url are known — see _make_emitter/_safe_emit."""
    games_dir = Path(games_dir) if games_dir is not None else gg.GAMES_DIR
    cfg = config.get("multifile_agent", {})
    t0 = time.monotonic()
    if emit is None:
        emit = _make_emitter(job_id, db_conn)

    try:
        source_row = ge.resolve_target(source_game_id, games_dir, conn=db_conn)
    except ge.GameEnhancementError as exc:
        result = _failure_result(exc, cfg, t0)
        result["message"] = ge.format_report(result)
        _safe_emit(emit, "error", result["error"])
        return result

    source_dir = games_dir / source_row["slug"]
    if not builder.is_multi_file(source_dir):
        # job_runner only dispatches here for multi-file sources — but fail
        # loudly rather than silently mis-editing a single-file game if it
        # ever does.
        exc = ge.GameEnhancementError(f"'{source_game_id}' is not a multi-file game")
        result = _failure_result(exc, cfg, t0)
        result["message"] = ge.format_report(result)
        _safe_emit(emit, "error", result["error"])
        return result

    title_override = (new_title or "").strip() or None
    if title_override is None:
        n = db.count_by_root(source_row["root_game_id"], conn=db_conn) + 1
        base_title = re.sub(r"\s*\(v\d+\)$", "", source_row["title"]).strip()
        title_override = f"{base_title} (v{n})"

    dest_game_id = db.mint_game_id()
    dest_slug = db.make_slug(title_override, dest_game_id)
    collision = gg.check_slug_collision(dest_slug, games_dir)
    if collision:
        exc = ge.GameEnhancementError(f"slug collision: {collision}")
        result = _failure_result(exc, cfg, t0)
        result["message"] = ge.format_report(result)
        _safe_emit(emit, "error", result["error"])
        return result

    dest_dir = games_dir / dest_slug
    _stage_fork(source_dir, dest_dir)

    outcome = _run_react_loop(
        game_dir=dest_dir,
        system_prompt=_build_system_prompt(source_row["title"]),
        user_prompt=_build_user_prompt(description),
        cfg=cfg, job_id=job_id, db_conn=db_conn, emit=emit,
    )
    duration = time.monotonic() - t0

    if not outcome["success"]:
        gg.rollback_game_files(dest_dir)
        result = {
            "success": False, "game_id": None, "slug": None, "title": None,
            "description": None, "attempts": outcome["attempts"],
            "input_tokens": outcome["input_tokens"], "output_tokens": outcome["output_tokens"],
            "tokens_used": outcome["tokens_used"], "model": outcome["model"],
            "effort": outcome["effort"], "duration_seconds": duration,
            "error": outcome["error"], "notes": "", "url": None,
            "parent_game_id": None, "root_game_id": None,
        }
        result["message"] = ge.format_report(result)
        _safe_emit(emit, "error", result["error"])
        return result

    meta = {
        "game_id": dest_game_id,
        "parent_game_id": source_row["game_id"],
        "root_game_id": source_row["root_game_id"],
        "title": title_override,
        "description": source_row["description"],
        "requested_by": requested_by,
        "created_at": db.now_iso(),
        "version": 1,
        "prompt": description,
        "format": "multi-file",
    }
    (dest_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    result = {
        "success": True, "game_id": dest_game_id, "slug": dest_slug,
        "title": title_override, "description": source_row["description"],
        "attempts": outcome["attempts"],
        "input_tokens": outcome["input_tokens"], "output_tokens": outcome["output_tokens"],
        "tokens_used": outcome["tokens_used"],
        "model": outcome["model"], "effort": outcome["effort"],
        "duration_seconds": duration, "error": None, "notes": outcome["summary"],
        "url": gg.build_play_url(dest_slug, config),
        "parent_game_id": source_row["game_id"], "root_game_id": source_row["root_game_id"],
    }
    db.register_web_game(
        game_id=result["game_id"], slug=result["slug"], title=result["title"],
        description=result["description"], requested_by=requested_by, status="success",
        attempts=result["attempts"], version=1, model=result["model"],
        effort=result["effort"], duration_seconds=duration,
        input_tokens=result["input_tokens"], output_tokens=result["output_tokens"],
        tokens_used=result["tokens_used"], error=None,
        parent_game_id=result["parent_game_id"], root_game_id=result["root_game_id"],
        creator_uid=creator_uid, conn=db_conn,
    )
    gg.run_moderation_pass(
        result["game_id"], result["slug"], result["description"], result["notes"],
        games_dir, db_conn=db_conn,
    )
    result["message"] = ge.format_report(result)
    _safe_emit(emit, "final", result["notes"] or "Enhancement complete.",
               {"slug": result["slug"], "title": result["title"], "url": result["url"]})
    return result


# ---------------------------------------------------------------------------
# Sprint 5 — the explode pass (docs/multifile-agent/05-migration-and-pilot.md
# Part A) and the dual-format enhance policy (Part B)
# ---------------------------------------------------------------------------

def explode_game(source_game_id: str, requested_by: str, config: dict, db_conn=None,
                  games_dir: Path | None = None, job_id: str | None = None,
                  new_title: str | None = None, creator_uid: str | None = None,
                  emit: Callable | None = None, announce_completion: bool = True) -> dict:
    """Convert an existing single-file game into a multi-file fork,
    behavior-preserving: the model is handed the whole original index.html
    as input context and re-emits it split across src/index.html +
    src/style.css + src/*.js + game.md via the same write_file/finish tools
    _run_react_loop already drives, so this works even on a game already at
    the output-token ceiling (the whole point — see
    docs/multifile-agent/00-overview.md). Build -> safety scan -> smoke
    test must all pass, same gate as any other change; a manual play-test
    is still the only check that the game *plays* identically, not just
    that it's console-error-free (see the Sprint 5 doc's Part A).

    Forks exactly like enhance_multifile_game: a brand-new games/<slug>/ is
    written (parent_game_id=source_game_id, root_game_id=source's
    root_game_id), the source is never touched, and a failed run deletes
    the half-written fork. Unlike an enhance, the default title is the
    source's own title verbatim (this is a format change, not a content
    change) unless new_title overrides it.

    `announce_completion=False` (used by enhance_game_auto_format's
    dual-format policy, Part B) suppresses the terminal 'final' event and
    its Play link — that fork is an internal implementation detail the
    requester didn't directly ask to see — emitting a short 'assistant'
    note instead so the transcript still explains why an extra step ran
    before the caller continues on with the actual requested enhancement.
    """
    games_dir = Path(games_dir) if games_dir is not None else gg.GAMES_DIR
    cfg = config.get("multifile_agent", {})
    t0 = time.monotonic()
    if emit is None:
        emit = _make_emitter(job_id, db_conn)

    try:
        source_row = ge.resolve_target(source_game_id, games_dir, conn=db_conn)
    except ge.GameEnhancementError as exc:
        result = _failure_result(exc, cfg, t0)
        result["message"] = ge.format_report(result)
        _safe_emit(emit, "error", result["error"])
        return result

    source_dir = games_dir / source_row["slug"]
    if builder.is_multi_file(source_dir):
        exc = ge.GameEnhancementError(f"'{source_game_id}' is already multi-file")
        result = _failure_result(exc, cfg, t0)
        result["message"] = ge.format_report(result)
        _safe_emit(emit, "error", result["error"])
        return result

    source_html = (source_dir / "index.html").read_text(encoding="utf-8")
    title_override = (new_title or "").strip() or source_row["title"]

    dest_game_id = db.mint_game_id()
    dest_slug = db.make_slug(title_override, dest_game_id)
    collision = gg.check_slug_collision(dest_slug, games_dir)
    if collision:
        exc = ge.GameEnhancementError(f"slug collision: {collision}")
        result = _failure_result(exc, cfg, t0)
        result["message"] = ge.format_report(result)
        _safe_emit(emit, "error", result["error"])
        return result

    dest_dir = games_dir / dest_slug
    dest_dir.mkdir(parents=True, exist_ok=False)

    outcome = _run_react_loop(
        game_dir=dest_dir,
        system_prompt=_build_explode_system_prompt(source_row["title"], source_html),
        user_prompt=_build_explode_user_prompt(),
        cfg=cfg, job_id=job_id, db_conn=db_conn, emit=emit,
    )
    duration = time.monotonic() - t0

    if not outcome["success"]:
        gg.rollback_game_files(dest_dir)
        result = {
            "success": False, "game_id": None, "slug": None, "title": None,
            "description": None, "attempts": outcome["attempts"],
            "input_tokens": outcome["input_tokens"], "output_tokens": outcome["output_tokens"],
            "tokens_used": outcome["tokens_used"], "model": outcome["model"],
            "effort": outcome["effort"], "duration_seconds": duration,
            "error": outcome["error"], "notes": "", "url": None,
            "parent_game_id": None, "root_game_id": None,
        }
        result["message"] = ge.format_report(result)
        _safe_emit(emit, "error", result["error"])
        return result

    meta = {
        "game_id": dest_game_id,
        "parent_game_id": source_row["game_id"],
        "root_game_id": source_row["root_game_id"],
        "title": title_override,
        "description": source_row["description"],
        "requested_by": requested_by,
        "created_at": db.now_iso(),
        "version": 1,
        "prompt": "explode: split single-file game into multi-file modules (behavior-preserving)",
        "format": "multi-file",
    }
    (dest_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    result = {
        "success": True, "game_id": dest_game_id, "slug": dest_slug,
        "title": title_override, "description": source_row["description"],
        "attempts": outcome["attempts"],
        "input_tokens": outcome["input_tokens"], "output_tokens": outcome["output_tokens"],
        "tokens_used": outcome["tokens_used"],
        "model": outcome["model"], "effort": outcome["effort"],
        "duration_seconds": duration, "error": None,
        "notes": outcome["summary"] or "Converted to the multi-file format.",
        "url": gg.build_play_url(dest_slug, config),
        "parent_game_id": source_row["game_id"], "root_game_id": source_row["root_game_id"],
    }
    db.register_web_game(
        game_id=result["game_id"], slug=result["slug"], title=result["title"],
        description=result["description"], requested_by=requested_by, status="success",
        attempts=result["attempts"], version=1, model=result["model"],
        effort=result["effort"], duration_seconds=duration,
        input_tokens=result["input_tokens"], output_tokens=result["output_tokens"],
        tokens_used=result["tokens_used"], error=None,
        parent_game_id=result["parent_game_id"], root_game_id=result["root_game_id"],
        creator_uid=creator_uid, conn=db_conn,
    )
    gg.run_moderation_pass(
        result["game_id"], result["slug"], result["description"], result["notes"],
        games_dir, db_conn=db_conn,
    )
    result["message"] = ge.format_report(result)
    if announce_completion:
        _safe_emit(emit, "final", result["notes"],
                   {"slug": result["slug"], "title": result["title"], "url": result["url"]})
    else:
        _safe_emit(
            emit, "assistant",
            "Converted this game to a multi-file format so future edits can "
            "target individual files — continuing with your requested change…",
        )
    return result


def enhance_game_auto_format(source_game_id: str, description: str, requested_by: str,
                              config: dict, db_conn=None, games_dir: Path | None = None,
                              job_id: str | None = None, new_title: str | None = None,
                              creator_uid: str | None = None, emit: Callable | None = None) -> dict:
    """job_runner's single dispatch point for kind='enhance' (Sprint 5's
    dual-format policy, docs/multifile-agent/05-migration-and-pilot.md Part
    B): decides among three enhance paths based only on the source game's
    on-disk format and size, never a per-request choice.

    - Multi-file source -> enhance_multifile_game() directly (unchanged
      from Sprint 2).
    - Single-file source at/over ge.LARGE_SOURCE_BYTES (the same size
      ceiling game_enhancer already uses to trigger its own compactness
      nudge) -> explode_game() first, then enhance_multifile_game() on the
      resulting fork for the actual requested change. The intermediate
      exploded fork is hidden (db.set_game_hidden) — it's an internal
      formatting step, not something the requester asked to see as its own
      arcade entry — but its parent_game_id/root_game_id still chain back
      through it to the original single-file source, so the info modal's
      ancestor chain and sidebar lineage stay correct across the
      single-to-multi boundary. If the explode step itself fails, falls
      back to the legacy single-file path below rather than failing the
      whole job over an internal step the user never directly asked for.
    - Otherwise -> the legacy game_enhancer.enhance_game() whole-file path,
      unchanged.
    """
    games_dir = Path(games_dir) if games_dir is not None else gg.GAMES_DIR

    if is_multi_file_source(source_game_id, games_dir, conn=db_conn):
        return enhance_multifile_game(
            source_game_id, description, requested_by, config,
            db_conn=db_conn, games_dir=games_dir, job_id=job_id,
            new_title=new_title, creator_uid=creator_uid, emit=emit,
        )

    row = db.get_web_game(source_game_id, conn=db_conn)
    if row is not None:
        index_path = games_dir / row["slug"] / "index.html"
        if index_path.is_file() and index_path.stat().st_size >= ge.LARGE_SOURCE_BYTES:
            exploded = explode_game(
                source_game_id, requested_by, config, db_conn=db_conn,
                games_dir=games_dir, job_id=job_id, emit=emit,
                announce_completion=False,
            )
            if exploded["success"]:
                db.set_game_hidden(exploded["game_id"], True, conn=db_conn)
                return enhance_multifile_game(
                    exploded["game_id"], description, requested_by, config,
                    db_conn=db_conn, games_dir=games_dir, job_id=job_id,
                    new_title=new_title, creator_uid=creator_uid, emit=emit,
                )
            # Explode failed -- fall through to the legacy single-file path.

    return ge.enhance_game(
        source_game_id, description, requested_by, config,
        db_conn=db_conn, games_dir=games_dir, job_id=job_id,
        new_title=new_title, creator_uid=creator_uid,
    )
