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

# ai_client.MAX_OUTPUT_TOKENS (150000 as of Sprint 6 — see ai_client.py's
# comment on that constant for how the original 65536 figure turned out to
# be self-imposed, not DeepSeek's real ceiling) is the per-response
# completion-token budget — at ~4 chars/token that's ~590KB of raw HTML. A
# write_file call has to emit its contents as a JSON-escaped tool-call
# argument (quotes/backslashes/newlines all cost extra bytes over the raw
# source), and in thinking mode the same budget is shared with
# reasoning_content, so this default sits at 3x MAX_OUTPUT_TOKENS bytes
# rather than the full raw-HTML figure — comfortable headroom, not a hard
# physical limit. Configurable per-call via cfg["max_module_bytes"].
DEFAULT_MAX_MODULE_BYTES = ai.MAX_OUTPUT_TOKENS * 3

# Sprint 6 item D: a soft lint, not a gate. DEFAULT_MAX_MODULE_BYTES stops a
# write that can't be re-emitted at all; by the time a module is anywhere
# near it, the game has already grown past the point a whole-module rewrite
# stays cheap. Warning at half the applicable ceiling gives the model a
# nudge to split proactively, on the next unrelated edit to that module,
# well before it's forced to by a rejection. Never blocks the write.
# Configurable per-call via cfg["module_warn_bytes"].
DEFAULT_MODULE_WARN_RATIO = 0.5

# The ReAct agent defaults to v4-pro where the rest of the app defaults to
# ai.MODEL_DEFAULT (v4-flash) — this is the one pipeline with direct evidence
# against flash. Exploding a 159KB single-IIFE game failed on flash four
# times running, every time by failing to CONVERGE rather than by lacking
# capability: it split the game sensibly and then audited its own modules
# until the step budget ran out, never calling finish, so the run shipped
# nothing. v4-pro reached verification in ~18 turns and passed, and cost less
# per run ($0.35 vs $0.66) despite ~3.1x the token price.
#
# This is a CODE default rather than a config-only one on purpose: config.yaml
# is gitignored, so anything set only there is invisible to every deployment
# and every fresh clone — prod would silently run the configuration we have
# four failed runs against. Same lesson as timeout_seconds' 120s -> 1800s.
# cfg["model"] still wins, so flash remains one line away.
DEFAULT_AGENT_MODEL = "deepseek-v4-pro"

# The explode pass needs a much tighter ceiling than an ordinary edit.
# DEFAULT_MAX_MODULE_BYTES exists to stop a write that physically cannot be
# re-emitted; it is not a target, and a 159KB game splits "successfully"
# into one 151KB module well under it. That passes every gate and buys
# nothing — enhancing that fork still means rewriting 151KB, which is the
# whole-file cost this initiative exists to remove (Sprint 6 step 2 pilot,
# docs/multifile-agent/05-migration-and-pilot.md).
#
# So explode enforces its own ceiling, and the prompt states both the
# ceiling and a target module count derived from the source size — the
# ceiling is the backstop, the target is what actually shapes the split.
# Overridable via cfg["explode_max_module_bytes"].
#
# 120_000, not the 60_000 this started at: a ceiling the model has to dodge
# is actively dangerous, because it shrinks to fit rather than splitting.
# The Darkhold pilot wrote render.js at 49,874 bytes — suspiciously just
# under 60,000 — and that is exactly where it dropped renderCharacterSelect;
# the complete version came to 72,660. Re-emittability doesn't object at
# 120_000 either (~30-40K tokens, against a 150_000-token output ceiling),
# and the target count below is what actually produces granularity. The
# trade-off accepted here: at 120_000 the backstop is inert for sources
# under ~250KB, so for those the prompt's target count is the only thing
# holding the line against one-giant-module.
DEFAULT_EXPLODE_MAX_MODULE_BYTES = 120_000
EXPLODE_TARGET_MODULE_BYTES = 25_000

_MAX_NO_PROGRESS_STEPS = 5

# A run that never calls finish() ships NOTHING — every module written, every
# token spent, discarded. Two consecutive real explode pilots died exactly
# that way (docs/multifile-agent/05-migration-and-pilot.md): one was killed
# mid-review by the no-progress guard, the next burned all 60 steps
# self-auditing its split and never verified, at 4.5M tokens. The
# no-progress guard cannot catch the second case — any successful write
# resets it, and a model alternating read/write forever never trips it.
#
# So the loop also watches the step budget itself: with this many steps left
# and still no finish attempt, tell the model plainly to stop auditing and
# verify. Machine verification is both cheaper and stricter than the model
# re-reading its own modules, and a rejection comes back with the exact
# failure to fix.
def _finish_nudge_threshold(max_steps: int) -> int:
    return max(5, max_steps // 4)

# Marks every placeholder this module leaves in the conversation where real
# material was pruned away (compacted write calls, dropped read results).
# Two jobs: it makes those placeholders unmistakably bookkeeping rather than
# content, and _write_file rejects any write whose contents contain it — the
# guard against the model copying one back into a real file (Sprint 6 step 2;
# see _compact_write_calls for the run that made this necessary).
_PRUNE_SENTINEL = "[context-pruned]"


class AgentError(Exception):
    """Recoverable failure inside one tool call. Never escapes the loop —
    always turned into an "ERROR: ..." observation fed back to the model."""


# ---------------------------------------------------------------------------
# Tools exposed to the model
# ---------------------------------------------------------------------------

# Paths are already rooted at src/, so a "src/" prefix nests a second level
# ("src/map.js" -> src/src/map.js). A real pilot run did exactly that. It's
# caught downstream — builder.py resolves index.html's refs relative to src/
# too, so a mismatch surfaces as a build failure the loop can retry — but
# saying so up front is free.
_PATH_ARG_DESCRIPTION = (
    "Relative to src/, e.g. 'core.js' — do NOT prefix it with 'src/'. "
    "The one exception is 'game.md', which sits alongside src/."
)

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
            "Read the full current contents of one file: a module path "
            "relative to src/ (e.g. 'core.js') or 'game.md'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": _PATH_ARG_DESCRIPTION},
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
                "path": {"type": "string", "description": _PATH_ARG_DESCRIPTION},
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

def _normalize_agent_path(path: str) -> str:
    """Canonical form of a model-supplied path: no './' prefix, no
    redundant 'src/' prefix (see _resolve_agent_path). Every observation and
    every pruning-bookkeeping key uses this, so 'src/core.js' and 'core.js'
    can't be mistaken for two different files."""
    path = path.strip()
    if not path:
        raise AgentError("empty path")
    if path.startswith("/"):
        raise AgentError(f"absolute path not allowed: {path!r}")
    if path.startswith("./"):
        path = path[2:]
    while path.startswith("src/"):
        path = path[4:]
    if not path:
        raise AgentError("empty path")
    return path


def _resolve_agent_path(game_dir: Path, path: str) -> Path:
    """Map a model-supplied path onto a real file. A leading 'src/' is
    collapsed (see _normalize_agent_path) because agent paths are ALREADY
    rooted at src/, so 'src/core.js' would otherwise nest to src/src/core.js.

    Sprint 6 step 2's pilot showed why this can't be left to prompt wording
    (it was, and the model ignored it): the run wrote its shell to BOTH
    'index.html' and 'src/index.html', landing two competing shells at
    src/index.html and src/src/index.html with every module under src/src/.
    builder.build_game only ever reads src/index.html, so the shell it would
    have built from was the one whose sibling refs pointed nowhere — and the
    model burned 17 list_files calls trying to reconcile a tree it had no
    way to see straight. Collapsing makes the tree single-rooted: 'core.js'
    and 'src/core.js' are the same file, last write wins."""
    path = _normalize_agent_path(path)
    if path == "game.md":
        return game_dir / "game.md"
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


def _write_file(game_dir: Path, path: str, contents: str, max_module_bytes: int,
                 warn_bytes: int) -> str:
    file_path = _resolve_agent_path(game_dir, path)
    size = len(contents.encode("utf-8"))
    if _PRUNE_SENTINEL in contents:
        # Defense in depth against the Sprint 6 step 2 stub-write bug (see
        # _compact_write_calls): every placeholder this module puts into the
        # conversation carries _PRUNE_SENTINEL, so contents echoing one back
        # means the model has copied bookkeeping text into a real file
        # instead of authoring it. Reject loudly and tell it what to do —
        # silently writing it is what turned that bug into a multi-hundred-
        # thousand-token stall with a corrupt module on disk.
        return (
            f"REJECTED: the contents you passed for {path!r} are a "
            "context-pruning placeholder from this conversation's history, "
            "not real file contents. Those placeholders mark where earlier "
            "material was dropped to save context — never copy one into a "
            "file. Call read_file to see what's currently on disk, then "
            "write the complete, real contents."
        )
    if size > max_module_bytes:
        return (
            f"REJECTED: {path!r} is {size} bytes, over the {max_module_bytes}-byte "
            "module size ceiling. Split this module into smaller, cohesive "
            "files instead of shrinking it. If you do, you MUST also rewrite "
            "src/index.html so its <script> tags list the new files in place "
            "of this one — a shell still pointing at a file you never wrote "
            "fails the build — and update game.md's file table to match."
        )
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(contents, encoding="utf-8")
    ok = f"OK: wrote {size} bytes to {path}"
    if size > warn_bytes:
        # Soft lint only — the write already happened above. Folded into the
        # same observation string (not a separate event) so it survives
        # _compact_write_calls' note verbatim, same as the OK line itself.
        ok += (
            f". Note: {path!r} is getting large ({size} of a {max_module_bytes}-byte "
            "ceiling) — consider splitting it into smaller cohesive modules on "
            "your next pass through this file, before it forces a rejection."
        )
    return ok


def _execute_tool(tc: ai.ToolCall, game_dir: Path, max_module_bytes: int,
                   warn_bytes: int) -> tuple[str, str | None]:
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
            path = _normalize_agent_path(_parse_path_arg(tc.arguments))
            return _read_file(game_dir, path), path
        if tc.name == "write_file":
            path, contents = _parse_write_args(tc.arguments)
            path = _normalize_agent_path(path)
            return _write_file(game_dir, path, contents, max_module_bytes, warn_bytes), path
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

# These caps apply *before* the event is stored, so the transcript a
# requester watched and the transcript replayed months later are the same
# bytes by construction — but anything trimmed here is gone from both. 4000
# was too tight to review a run by: measured over a real explode pilot, 9 of
# 10 reasoning blocks came in under 900 chars while the one that mattered
# most — the up-front "here's how I'll split this game" plan — was the
# single block that hit the ceiling and lost its tail. The chat panes
# collapse anything past 240 chars into a <details>, so a longer thought
# costs display space only when someone expands it.
_THOUGHT_MAX_CHARS = 24000
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


_INLINE_SCRIPT_RE = re.compile(
    r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.S | re.I)
_TOP_LEVEL_DECL_RE = re.compile(
    r"\b(?:function|const|let|var|class)\s+([A-Za-z_$][\w$]*)")


def _declared_names(html: str) -> set[str]:
    """Every name declared by the inline <script> blocks of `html`. Crude by
    design — a regex, not a JS parser — because it's only ever used to
    compare one program against a reorganized copy of itself, where a name
    that vanishes entirely is the signal. Over-matching (a `const` inside a
    function body) is harmless: it over-matches identically on both sides."""
    return set(_TOP_LEVEL_DECL_RE.findall(
        "".join(_INLINE_SCRIPT_RE.findall(html))))


def _missing_declarations(source_html: str, built_html: str) -> list[str]:
    return sorted(_declared_names(source_html) - _declared_names(built_html))


def _reference_count(name: str, html: str) -> int:
    """How many times `name` appears as a bare identifier in the inline
    scripts of `html`. Member accesses (`camera.screenX`) and object-literal
    keys (`{ screenX: 1 }`) don't count — neither one refers to the global
    that a rename moved away, and counting either would fail a legitimate
    rename, which is the failure this whole check exists to stop doing. The
    same exclusion does drop the rare `cond ? screenX : y`; that direction
    only ever costs a missed catch, not a false accusation. String literals
    and comments are not excluded, this being a regex rather than a parser
    for the same reason _declared_names is."""
    script = "".join(_INLINE_SCRIPT_RE.findall(html))
    return len(re.findall(
        rf"(?<![.\w$]){re.escape(name)}(?![\w$])(?!\s*:)", script))


def _declaration_parity(source_html: str,
                        built_html: str) -> tuple[list[str], list[str]]:
    """Sort the names the built result no longer declares into the only two
    cases that differ in consequence:

    `broken` — the name is undeclared but the program still references it.
    That is the real defect: those call sites now bind to a browser built-in
    or to nothing at all.

    `vanished` — the name is gone from the program entirely, declaration and
    references alike. That is exactly what a correct, consistent RENAME looks
    like from the outside, and it is what the explode prompt demands for a
    name colliding with a Window built-in, so it cannot be treated as a
    failure on its own.
    """
    missing = _missing_declarations(source_html, built_html)
    broken = [n for n in missing if _reference_count(n, built_html)]
    vanished = [n for n in missing if n not in broken]
    return broken, vanished


def _fmt_name_list(names: list[str], limit: int = 20) -> str:
    return ", ".join(names[:limit]) + ("…" if len(names) > limit else "")


def _explode_declaration_check(source_html: str):
    """An extra explode-only verification gate: no name the single-file
    original declared may end up referenced-but-undeclared in the split, and
    no name may disappear without a replacement taking its place.

    This exists because build->scan->smoke cannot catch the worst outcome
    this pass has. Splitting a game whose whole program sat in one IIFE
    moves its names into global scope, where some of them collide with
    read-only Window built-ins — `screenX`, `screenY`, `name`, `status`,
    `length`, `top`, `closed`, `origin`, and friends. The Darkhold pilot hit
    exactly that, resolved the collision by DELETING
    `function screenX(wx) {...}` and `function screenY(wy) {...}`, and still
    passed every gate: `screenX` kept resolving — to the built-in number —
    so the 22 surviving call sites raise TypeError at call time rather than
    ReferenceError at load, and a page-load smoke test never runs world
    rendering. The result was a green build and a game that breaks the
    instant you start playing.

    Why this is a reference check and not a set difference over declared
    names, which is what it was first: the prompt's own remedy for such a
    collision is to RENAME the declaration and every call site, and a
    successful rename removes the original name from the declaration set.
    A plain set difference therefore failed the fix it had just asked for,
    with a message repeating the demand — an unsatisfiable loop that burned
    every verification attempt on two real Sorcerer With A Minigun explodes
    (2026-07-26) without the model ever being able to comply. What actually
    distinguishes the delete from the rename is the call sites: deleting
    `screenX` leaves 22 of them behind, renaming it leaves none. So the gate
    fails on names that are still referenced, and separately on names that
    vanish with nothing new declared to stand in for them (which is the
    other real defect — code dropped to fit the module ceiling).
    """
    def check(game_dir, built_html):
        broken, vanished = _declaration_parity(source_html, built_html)
        problems = []
        if broken:
            sites = ", ".join(
                f"{n} ({_reference_count(n, built_html)} reference(s) left)"
                for n in broken[:20]
            ) + ("…" if len(broken) > 20 else "")
            problems.append(
                f"{len(broken)} name(s) the original declared are no longer "
                "declared anywhere in src/, while the built game still "
                f"references them: {sites}. Restore each one as a real "
                "declaration. If you renamed it to dodge a browser-global "
                "collision (screenX, screenY, name, status, length, top, …), "
                "that was right — but you missed those references, so rename "
                "them to match. If you deleted it, put it back under a "
                "non-colliding name and update every reference. Do not leave "
                "the call sites bound to the built-in: `screenX` still "
                "resolves, to a number, so nothing fails on load and it "
                "throws only once that code runs."
            )
        if vanished:
            # A rename replaces one name with another, so the built result
            # declares something the original didn't. Nothing new at all means
            # the code went away rather than moved — the drop-to-fit failure.
            replacements = _declared_names(built_html) - _declared_names(source_html)
            if len(replacements) < len(vanished):
                problems.append(
                    f"{len(vanished)} name(s) the original declared are gone "
                    "from the split entirely, with no new declaration to "
                    f"replace them: {_fmt_name_list(vanished)}. Every "
                    "function/const/let/class in the original must still be "
                    "declared exactly once across src/ — under its own name, "
                    "or under a new one applied consistently at the "
                    "declaration and every reference. Never omit code to fit "
                    "the module size ceiling; split the module instead."
                )
        return " ".join(problems) or None
    return check


def _explode_target_module_count(source_bytes: int) -> int:
    """Roughly how many JS modules a source of this size should split into.
    Deliberately a range-anchor for the prompt, not a rule the loop
    enforces — the enforced number is the byte ceiling."""
    return max(3, round(source_bytes / EXPLODE_TARGET_MODULE_BYTES))


def _build_explode_system_prompt(source_title: str, source_html: str,
                                  max_module_bytes: int = DEFAULT_EXPLODE_MAX_MODULE_BYTES) -> str:
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
        "plus src/style.css and SEVERAL cohesive src/*.js modules, and "
        "author game.md (a prose description of the game plus a table of "
        "every src/ file and its purpose). This MUST be behavior-preserving: "
        "every mechanic, visual, and control must work identically to the "
        "original — you are re-organizing the code, not rewriting the "
        "game, adding features, or fixing bugs you happen to notice.\n\n"
        "## How many modules\n"
        f"This game's source is {len(source_html):,} bytes. Split its "
        f"JavaScript across roughly {_explode_target_module_count(len(source_html))} "
        f"modules, none larger than {max_module_bytes:,} bytes — that ceiling "
        "is enforced, and an oversized write_file is rejected. Divide along "
        "the seams the code already has, one module per subsystem: "
        "constants/config, entity definitions, world or level generation, "
        "combat, input handling, the update loop, rendering, UI/HUD, and a "
        "small entry point that wires them together are typical. Name each "
        "file for what it holds.\n\n"
        "Putting all the logic into one big module DEFEATS THE ENTIRE "
        "PURPOSE of this conversion. The point is that a later edit only "
        "has to read and rewrite the one module it touches; a single "
        "large module leaves that edit exactly as expensive as it was "
        "before the split.\n\n"
        "Use write_file(path, contents) for every file you create — the "
        "src/ shell as write_file(\"index.html\", ...), the stylesheet as "
        "write_file(\"style.css\", ...), each logic module under its own "
        "descriptive name (write_file(\"entities.js\", ...), "
        "write_file(\"render.js\", ...), write_file(\"input.js\", ...), and "
        "so on), and the map as write_file(\"game.md\", ...). Paths are already rooted "
        "at src/, so write bare filenames and never a \"src/\" prefix; "
        "src/index.html must likewise reference its siblings by bare "
        "filename, one <script> tag per module in dependency order "
        "(<link rel=\"stylesheet\" href=\"style.css\">, then "
        "<script src=\"entities.js\"></script>"
        "<script src=\"render.js\"></script> and so on), never "
        "\"src/style.css\". You may use "
        "read_file(path)/list_files() to check back on files you've "
        "already written. Call finish(summary) only once EVERY file "
        "(including src/index.html and game.md) has been written — it "
        "triggers a build + safety scan + smoke test of the assembled "
        "result; a failure comes back as this call's result so you can "
        "keep editing and call finish again.\n\n"
        "## One shared scope — read this before you split anything\n"
        "The build concatenates your modules into sibling <script> blocks in "
        "the order src/index.html lists them. There is no module system, no "
        "import/export, and NO per-file scope: every module's top-level "
        "declarations land in one shared global scope. That makes four rules "
        "non-negotiable.\n"
        "1. If the original wraps its whole program in one IIFE — "
        "`(function () { ... })();` around everything — DELETE that single "
        "outer wrapper and distribute its body across your modules "
        "unchanged. Do not give each module its own IIFE: names declared "
        "inside one are invisible to every other module, which is the single "
        "most common way this conversion fails.\n"
        "2. Declare every identifier EXACTLY ONCE across all modules "
        "combined. The same const/let/class name in two files is a fatal "
        "redeclaration error that kills the entire game on load — it is not "
        "a per-file shadow. If two subsystems both need a constant, it "
        "belongs in exactly one module.\n"
        "3. Never invent `window.foo = foo` bridges to pass things between "
        "modules. Top-level const/let/class bindings never become window "
        "properties, so `window.foo` reads undefined even where bare `foo` "
        "works fine. Modules share a scope already — just use the bare name.\n"
        "4. Order the <script> tags so anything executed at load time comes "
        "after what it reads. Functions may call across modules freely once "
        "everything has loaded, but a top-level const/let must be declared "
        "before the first module that reads it while loading. Put the entry "
        "point — the module that actually starts the game — last.\n"
        "5. Dropping the outer IIFE puts every name into global scope, where "
        "a few collide with read-only Window built-ins: screenX, screenY, "
        "name, status, length, top, self, closed, origin, history, location, "
        "focus, close, open, event. If the original declares one of those, "
        "RENAME it (e.g. screenX -> toScreenX) at its declaration AND at "
        "every call site. Never resolve such a collision by deleting the "
        "declaration: the name still resolves — to the built-in — so nothing "
        "fails on load and the game breaks only once that code actually "
        "runs. Renaming is fully expected here and verification allows it — "
        "it checks that no name is left referenced without a declaration, "
        "not that the original spelling survived.\n\n"
        "## Never drop code to fit\n"
        "This conversion is behavior-preserving, and the module ceiling is "
        "never a reason to omit anything. If a subsystem's code exceeds the "
        "ceiling, SPLIT it across two modules (render_world.js + "
        "render_ui.js) — never abbreviate it, summarize it, stub it, or "
        "leave a function out to make the file fit. Every function and "
        "constant in the original must appear exactly once across your "
        "modules — under its own name, or under a rename applied "
        "consistently at the declaration and every reference. The split is "
        "checked for this: code that simply disappears fails "
        "verification.\n\n"
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
        "Split this game into the multi-file format described above, across "
        "several cohesive modules rather than one large one. Do "
        "not add, remove, or change any feature, visual, or control — the "
        "played game must behave exactly as it does now."
    )


# ---------------------------------------------------------------------------
# The ReAct loop
# ---------------------------------------------------------------------------

def _compact_write_calls(messages: list[dict], assistant_msg: dict,
                          records: list[tuple[str, str]]) -> None:
    """Drop executed write_file calls out of the conversation entirely,
    replacing them with a short plain-text note on the assistant message
    that made them.

    Why they must go: a write_file call's own arguments carry the COMPLETE
    new file contents (potentially tens of KB) inside the assistant message
    that requested it. `ask_with_tools()` is stateless, so anything left in
    `messages` is resent on every subsequent turn for the rest of the run —
    the Sprint 5 pilot measured this dominating the agent path's
    5-12x-larger-than-baseline input token cost, well ahead of stale reads.
    The model already generated that content, and the observation (byte
    count, success/rejection) is preserved in the note; read_file is there
    if it ever needs the current bytes again.

    Why they're REMOVED rather than squashed in place: Sprint 6's first
    attempt kept the tool call and only replaced its "contents" argument
    with a short placeholder string. That placeholder was ~113 bytes, and
    it sat in history paired with a tool result reading "OK: wrote 4840
    bytes" — which taught the model, by example from its own prior turns,
    that a ~113-byte placeholder-shaped "contents" is a legitimate call
    that produces a multi-KB file. Real pilot runs then had it emitting
    exactly that: ~113-120 byte stub writes for modules meant to be several
    KB, whose contents were the placeholder text itself, in a
    self-reinforcing loop that burned 1-2.6M input tokens and shipped
    nothing. (The same run's "confabulated" reasoning — the model insisting
    a tool result said "wrote 4840 bytes" when it plainly said 114 — was
    not confabulation at all: it had read the stub file back, and the
    stub's contents literally contained that sentence.) See
    docs/multifile-agent/05-migration-and-pilot.md. An earlier variant of
    the same placeholder, which also dropped the "path" key, taught the
    model to omit "path" the same way. Both are the identical failure mode:
    anything left in the arguments slot is read as an example of what a
    valid call looks like. The only safe amount of fake tool-call arguments
    to leave in history is none.

    Removing a tool call means also removing its tool-result message, or
    the API's assistant-tool_calls/tool-result pairing breaks. Both happen
    here. An assistant message left with no tool calls at all keeps the
    note as its `content`, which is a perfectly ordinary assistant turn.
    Mutates `messages` and `assistant_msg` in place; a no-op when there are
    no write calls to compact."""
    if not records:
        return
    ids = {call_id for call_id, _ in records}
    remaining = [e for e in (assistant_msg.get("tool_calls") or []) if e.get("id") not in ids]
    if remaining:
        assistant_msg["tool_calls"] = remaining
    else:
        assistant_msg.pop("tool_calls", None)

    # Wording matters more than it looks. The first version said the calls
    # "were dropped from the conversation to save context", and the Sprint 6
    # step 2 pilot showed the model reading that as "my writes did not take
    # effect" — three separate "it seems like my write_file calls are being
    # dropped" reasoning turns, each kicking off a list_files/read_file sweep
    # to re-check state. That run spent 38 of its 40 turns on exploration and
    # ran out of budget before ever calling finish. So: lead with the write
    # having SUCCEEDED, and be explicit that only the bulky argument was
    # trimmed and that the file is on disk.
    note = (
        f"{_PRUNE_SENTINEL} The write_file call(s) below COMPLETED SUCCESSFULLY and "
        "the files are on disk. Only the bulky 'contents' argument has been trimmed "
        "from this transcript, to save context. Results: "
        + "; ".join(observation for _, observation in records)
        + ". There is no need to re-write or re-check these files; call read_file "
          "only if you need to see their current contents again."
    )
    existing = (assistant_msg.get("content") or "").strip()
    assistant_msg["content"] = f"{existing}\n{note}" if existing else note

    messages[:] = [
        m for m in messages
        if not (m.get("role") == "tool" and m.get("tool_call_id") in ids)
    ]


def _run_react_loop(*, game_dir: Path, system_prompt: str, user_prompt: str,
                     cfg: dict, job_id: str | None, db_conn, emit: Callable | None = None,
                     extra_verify: Callable | None = None) -> dict:
    """Drive the read_map/list_files/read_file/write_file/finish loop
    against game_dir until finish() passes build->scan->smoke, the
    verification-retry budget is exhausted, or the step budget runs out.
    game_dir is either an already-staged copy of a multi-file source's
    src/+game.md (enhance_multifile_game) or an empty directory the model
    populates from scratch via write_file (explode_game, Sprint 5) —
    `_write_file`/`builder.is_multi_file`'s src/index.html check both cope
    with the latter starting out empty.

    Returns a dict: success/summary/attempts/input_tokens/output_tokens/
    cached_tokens/tokens_used/model/effort/error. Does not touch games_dir bookkeeping,
    the DB registry, or rollback — the caller (enhance_multifile_game,
    explode_game) owns all of that, same division of labor as
    game_generator.run_generation_attempts() vs. its callers.

    `emit(role, content=None, data=None)` (Sprint 3) is called at every
    think/act/observe/verify step for the durable agent_events transcript,
    plus a 'usage' event per LLM call carrying that call's own token counts
    and the run's running totals (the job row only gets a total at the very
    end, and generation_attempts only gets a row per finish() verification,
    so 'usage' is the only per-call accounting there is). Defaults to a
    DB-writing emitter keyed on job_id (a no-op if job_id is None). Every
    call is wrapped in _safe_emit so a raising emitter can't fail the run.

    Context pruning (Sprint 6, following up on the Sprint 5 pilot's token
    measurements — see docs/multifile-agent/05-migration-and-pilot.md):
    `ask_with_tools()` is stateless, so every turn resends the whole
    `messages` list built up so far. Two things stop riding along once
    they're no longer needed, so the resent-every-turn cost stops growing
    without bound:
      - every executed write_file call is removed from the conversation
        outright — the tool call and its result both — leaving only a short
        plain-text note carrying the observation (see _compact_write_calls,
        and note carefully why this removes rather than rewrites them);
      - a read_file result is replaced with a _PRUNE_SENTINEL placeholder
        once either the same path is later rewritten (unchanged from
        Sprint 2) or it's simply gone stale — outstanding for more than
        `cfg["context_prune_after_steps"]` steps (default 3) without being
        rewritten.

    `extra_verify(game_dir, built_html) -> str | None` is an optional gate
    run only after build->scan->smoke has already passed; returning a string
    turns that finish() into a failure carrying it as the detail. explode
    uses it for the declaration-parity check (see
    _explode_declaration_check), which catches a whole class of breakage the
    standard gate structurally cannot. The loop itself stays unaware of
    which pass it drives.
    """
    # 40 was too tight for a real explode: Sprint 6 step 2's pilot split a
    # 159KB game into 12 modules and hit the cap having never once called
    # finish, so it verified nothing and shipped nothing despite every write
    # succeeding. There's no partial credit here — running out of steps
    # before finish() throws away the whole run — and the dominant fix is
    # cutting wasted exploration turns (see _compact_write_calls' note
    # wording and _normalize_agent_path), but the cap needs real headroom
    # above "one write per module" too.
    max_steps = cfg.get("max_steps", 60)
    max_verification_retries = cfg.get("max_verification_retries", 3)
    max_module_bytes = cfg.get("max_module_bytes", DEFAULT_MAX_MODULE_BYTES)
    module_warn_bytes = cfg.get(
        "module_warn_bytes", int(max_module_bytes * DEFAULT_MODULE_WARN_RATIO)
    )
    max_read_age_steps = cfg.get("context_prune_after_steps", 3)
    # `or` rather than a get() default, so an explicitly blank model in a
    # config still lands on the agent's own default instead of falling
    # through to ai_client's app-wide one (see DEFAULT_AGENT_MODEL).
    model = cfg.get("model") or DEFAULT_AGENT_MODEL
    effort = cfg.get("effort", "high")
    # 1800s, matching config.yaml.example and the two single-file pipelines —
    # NOT ai_client's own 120s default. A config.yaml predating the
    # multifile_agent block (this repo's own did) silently got 120s here,
    # which is short enough to time out a thinking-mode turn emitting a large
    # write_file argument; real explode runs emit 100KB+ in a single call.
    ai_timeout = cfg.get("timeout_seconds", 1800)
    if emit is None:
        emit = _make_emitter(job_id, db_conn)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    total_input_tokens = 0
    total_output_tokens = 0
    total_cached_tokens = 0
    last_model = model or "default"
    last_effort = effort
    verification_attempts = 0
    tokens_at_last_attempt = (0, 0)
    last_read_message: dict[str, dict] = {}
    last_read_step: dict[str, int] = {}
    consecutive_no_progress = 0
    wrote_anything = False
    nudged_to_finish = False
    nudged_low_budget = False
    finish_nudge_at = _finish_nudge_threshold(max_steps)
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

    for step_num in range(1, max_steps + 1):
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
        total_cached_tokens += ask_result.cached_tokens
        last_model = ask_result.model or "default"
        last_effort = ask_result.effort
        messages.append(ask_result.message)
        assistant_msg = ask_result.message

        # One 'usage' event per LLM call, carrying that call's own token
        # counts and the run's running totals. The generation_requests row
        # only gets a total once the whole job is over, and
        # generation_attempts only records a row per finish() verification,
        # so without this a long agent run's token spend is invisible until
        # it ends — and invisible per-turn even then.
        _safe_emit(
            emit, "usage",
            f"LLM call {step_num}: {ask_result.input_tokens:,} in / "
            f"{ask_result.output_tokens:,} out "
            f"(running total {total_input_tokens + total_output_tokens:,})",
            {
                "step": step_num,
                "call_input_tokens": ask_result.input_tokens,
                "call_output_tokens": ask_result.output_tokens,
                "call_cached_tokens": ask_result.cached_tokens,
                "input_tokens": total_input_tokens,
                "output_tokens": total_output_tokens,
                "cached_tokens": total_cached_tokens,
                "tokens_used": total_input_tokens + total_output_tokens,
                "model": last_model,
                "effort": last_effort,
            },
        )

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

        write_records: list[tuple[str, str]] = []
        for tc in other_calls:
            call_content, call_data = _summarize_tool_call(tc)
            _safe_emit(emit, "tool_call", call_content, call_data)
            content, touched_path = _execute_tool(tc, game_dir, max_module_bytes, module_warn_bytes)
            obs_content, obs_data = _summarize_observation(tc.name, touched_path, content)
            _safe_emit(emit, "tool_result", obs_content, obs_data)
            msg = {"role": "tool", "tool_call_id": tc.id, "content": content}
            messages.append(msg)
            if tc.name == "read_file" and touched_path and not content.startswith("ERROR:"):
                last_read_message[touched_path] = msg
                last_read_step[touched_path] = step_num
            elif tc.name == "write_file":
                write_records.append((tc.id, content))
                if touched_path and content.startswith("OK:"):
                    made_progress = True
                    wrote_anything = True
                    stale = last_read_message.pop(touched_path, None)
                    last_read_step.pop(touched_path, None)
                    if stale is not None:
                        stale["content"] = (
                            f"{_PRUNE_SENTINEL} {touched_path} was rewritten by a later "
                            "write_file — re-read it if you need the current contents."
                        )
        _compact_write_calls(messages, assistant_msg, write_records)

        stale_paths = [
            path for path, read_at in last_read_step.items()
            if step_num - read_at >= max_read_age_steps
        ]
        for path in stale_paths:
            read_at = last_read_step.pop(path)
            last_read_message.pop(path)["content"] = (
                f"{_PRUNE_SENTINEL} {path} was read {step_num - read_at} steps ago — "
                "re-read it if you need the current contents."
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
            passed, detail, built_html = builder.build_and_verify(game_dir)
            if passed and extra_verify is not None:
                extra_detail = extra_verify(game_dir, built_html)
                if extra_detail:
                    passed, detail = False, extra_detail
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

        # Running out of budget with nothing verified yet — independent of
        # write cadence, which is why this is separate from the no-progress
        # guard below (see _finish_nudge_threshold).
        steps_left = max_steps - step_num
        if (verification_attempts == 0 and wrote_anything
                and not nudged_low_budget and steps_left <= finish_nudge_at):
            nudged_low_budget = True
            messages.append({
                "role": "user",
                "content": (
                    f"BUDGET WARNING: {steps_left} turn(s) remain, and you have "
                    "not called finish yet. A run that ends without a passing "
                    "finish ships NOTHING — every file you have written is "
                    "discarded. Stop reviewing and call finish(summary) on your "
                    "next turn. It runs the build, safety scan and smoke test, "
                    "which check your split far more strictly than re-reading "
                    "the modules can; if anything is wrong you get the exact "
                    "failure back and can keep editing from there."
                ),
            })

        consecutive_no_progress = 0 if made_progress else consecutive_no_progress + 1
        if consecutive_no_progress >= _MAX_NO_PROGRESS_STEPS:
            # Reading isn't writing, but it isn't necessarily stalling
            # either: a run that has written files and is now re-reading
            # them is doing a final consistency pass before finish. Killing
            # that throws away a complete split for being careful — a real
            # explode pilot wrote all 10 modules, spent its last turns
            # reviewing them, and was aborted having never called finish
            # (which the "your split is checked for missing declarations"
            # prompt rule actively encourages). So spend one nudge before
            # giving up; only a stall that survives the nudge is a stall.
            if wrote_anything and not nudged_to_finish:
                nudged_to_finish = True
                consecutive_no_progress = 0
                messages.append({
                    "role": "user",
                    "content": (
                        f"You have gone {_MAX_NO_PROGRESS_STEPS} turns without "
                        "writing a file. Reading cannot verify anything — only "
                        "finish(summary) runs the build, safety scan and smoke "
                        "test. If every file is written, call finish(summary) "
                        "NOW. If something is still missing or wrong, write it "
                        "with write_file first, then call finish."
                    ),
                })
                continue
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
        "cached_tokens": total_cached_tokens,
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
        "input_tokens": 0, "output_tokens": 0, "cached_tokens": 0, "tokens_used": 0, "model": "default",
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

    # Explode runs under its own, much tighter module ceiling than an
    # ordinary edit — see DEFAULT_EXPLODE_MAX_MODULE_BYTES. Overriding the
    # key the loop already reads keeps _run_react_loop unaware of which pass
    # it's driving.
    explode_max_module_bytes = cfg.get(
        "explode_max_module_bytes", DEFAULT_EXPLODE_MAX_MODULE_BYTES
    )
    explode_cfg = dict(cfg, max_module_bytes=explode_max_module_bytes)

    outcome = _run_react_loop(
        game_dir=dest_dir,
        system_prompt=_build_explode_system_prompt(
            source_row["title"], source_html, explode_max_module_bytes
        ),
        user_prompt=_build_explode_user_prompt(),
        cfg=explode_cfg, job_id=job_id, db_conn=db_conn, emit=emit,
        extra_verify=_explode_declaration_check(source_html),
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
