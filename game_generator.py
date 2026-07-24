"""
game_generator — the engine behind AI game generation.

Given a natural-language description, drives DeepSeek (via `ai_client`) to
produce a single self-contained `index.html` browser game. The model
submits its work by calling the `submit_game` function tool (title,
description, html, notes) — no free-text format to parse. Each submission
is validated statically (safety.py), written to its real final location
(games/<slug>/), and only kept if a headless-browser smoke test
(smoke_test.py) actually passes.

Retries are a single multi-turn conversation, not fresh one-shot prompts:
a rejected submission gets the concrete failure (safety violation, smoke
test console errors, malformed arguments) back as the tool-call result,
so the model patches its previous code — which is still in its context —
rather than regenerating from scratch. Up to `max_attempts` submissions
are accepted before giving up.

There is no Switchboard/reload step to trigger: app.py rebuilds its game
manifest on every request via an mtime-based cache key, so a newly written
game directory is live the moment write_game_files() returns.

# Exports:
#   class GameGenerationError(Exception)
#   generate_game(description, requested_by, config, db_conn=None, games_dir=None,
#                 job_id=None) -> dict
#     (result includes "game_id"; slug is derived as slugify(title)-<game_id prefix>
#     via db.make_slug() so duplicate titles never collide. job_id, when given,
#     tags each retry attempt in generation_attempts for audit/status purposes)
#   run_generation_attempts(...) -> dict
#     (shared submit -> safety-scan -> write -> smoke-test conversation loop,
#     reused by game_enhancer.enhance_game() for fork-on-enhance; see docstring)
#   SUBMIT_GAME_TOOL, SUBMIT_TOOL_INSTRUCTIONS  (shared with game_enhancer)
#   slugify(title) -> str
#   check_slug_collision(slug, games_dir) -> str | None
#   parse_submission(arguments_json) -> dict
#   write_game_files(slug, game_html, meta, games_dir) -> Path
#   rollback_game_files(game_dir) -> None
#   build_play_url(slug, config) -> str
#   format_report(result) -> str
"""

from __future__ import annotations

import datetime
import json
import re
import shutil
import time
from pathlib import Path

from langsmith import traceable

import ai_client as ai
import content_moderation
import db
import safety
import smoke_test

# Must match app.py's _SLUG_RE — that's what actually gatekeeps /play/<slug>;
# this copy gatekeeps what gets written to disk in the first place.
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,59}$")
RESERVED_SLUGS = {"backups"}

GAMES_DIR = Path(__file__).resolve().parent / "games"


class GameGenerationError(Exception):
    """Recoverable failure in the generation pipeline. str(exc) is fed back
    to the model as the tool-call result so the next submission can fix it."""


# ---------------------------------------------------------------------------
# The submit_game tool — how the model hands its work back
# ---------------------------------------------------------------------------

SUBMIT_GAME_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_game",
        "description": (
            "Submit the finished single-file HTML5 game. Always submit the "
            "complete index.html source, never a diff or fragment. If the "
            "submission is rejected, the tool result explains exactly why — "
            "fix the problem and call submit_game again with the complete "
            "corrected file."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Short game title.",
                },
                "description": {
                    "type": "string",
                    "description": "One-sentence description of the game.",
                },
                "html": {
                    "type": "string",
                    "description": "The complete index.html source, everything inline.",
                },
                "notes": {
                    "type": "string",
                    "description": "Optional: one or two sentences of notes for the requester.",
                },
            },
            "required": ["title", "description", "html"],
        },
    },
}

# Forcing the named tool means the model can never reply with prose instead
# of a submission — every turn either submits a game or errors.
SUBMIT_TOOL_CHOICE = {"type": "function", "function": {"name": "submit_game"}}

# Shared tail for both the generate and enhance system prompts.
SUBMIT_TOOL_INSTRUCTIONS = (
    "## Submitting\n"
    "Hand the finished game over by calling the submit_game tool with the "
    "COMPLETE index.html source in `html`, plus `title`, `description`, and "
    "optionally `notes`. If the submission is rejected, the tool result "
    "tells you exactly what failed (safety scan, runtime errors from a "
    "headless-browser smoke test, etc.) — fix your code and call "
    "submit_game again with the complete corrected file, never a diff or "
    "fragment."
)


def parse_submission(arguments_json: str) -> dict:
    """Validate a submit_game tool call's raw JSON arguments into
    game_html / title / description / notes. Raises GameGenerationError
    (with a model-facing message) on invalid JSON or missing/empty fields."""
    try:
        args = json.loads(arguments_json)
    except json.JSONDecodeError as exc:
        raise GameGenerationError(f"malformed submission: arguments are not valid JSON: {exc}")
    if not isinstance(args, dict):
        raise GameGenerationError("malformed submission: arguments must be a JSON object")

    title = args.get("title")
    description = args.get("description")
    html = args.get("html")
    if not isinstance(title, str) or not title.strip():
        raise GameGenerationError("malformed submission: missing a non-empty 'title'")
    if not isinstance(description, str) or not description.strip():
        raise GameGenerationError("malformed submission: missing a non-empty 'description'")
    if not isinstance(html, str) or not html.strip():
        raise GameGenerationError("malformed submission: missing a non-empty 'html'")

    notes = args.get("notes")
    notes = notes.strip() if isinstance(notes, str) else ""
    if notes.lower() == "none":
        notes = ""

    return {
        "game_html": html,
        "title": title.strip(),
        "description": description.strip(),
        "notes": notes,
    }


# ---------------------------------------------------------------------------
# Slug derivation / collision checking
# ---------------------------------------------------------------------------

def slugify(title: str) -> str:
    slug = title.strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return slug[:50]


def check_slug_collision(slug: str, games_dir: Path) -> str | None:
    """Return a reason string if `slug` can't be used, or None if it's OK."""
    if not slug or not _SLUG_RE.match(slug):
        return (
            f"invalid slug '{slug}' derived from title (must be lowercase "
            "alphanumeric-hyphen, 1-60 chars)"
        )
    if slug in RESERVED_SLUGS:
        return f"'{slug}' is a reserved directory name"
    if (Path(games_dir) / slug).exists():
        return f"a game with slug '{slug}' already exists"
    return None


# ---------------------------------------------------------------------------
# File writing / rollback
# ---------------------------------------------------------------------------

def write_game_files(slug: str, game_html: str, meta: dict, games_dir: Path) -> Path:
    """Write index.html + meta.json to their real final location. Only ever
    called after safety + collision checks pass."""
    games_dir = Path(games_dir)
    games_dir.mkdir(parents=True, exist_ok=True)
    game_dir = games_dir / slug
    game_dir.mkdir(parents=True, exist_ok=True)
    (game_dir / "index.html").write_text(game_html, encoding="utf-8")
    (game_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return game_dir


def rollback_game_files(game_dir: Path) -> None:
    """Delete the game directory if present. Idempotent — safe to call when
    it's already missing."""
    shutil.rmtree(Path(game_dir), ignore_errors=True)


# ---------------------------------------------------------------------------
# Automated content-moderation pass (shared by generate_game / enhance_game)
# ---------------------------------------------------------------------------

def run_moderation_pass(game_id: str, slug: str, description: str, notes: str,
                         games_dir: Path, db_conn=None) -> None:
    """Run content_moderation.check_game() against the just-written game and,
    if flagged, hide it and log a report — called once on the final accepted
    submission, never on a retry attempt. Never raises and never changes the
    caller's success result: a moderation-call failure (see
    content_moderation.check_game's own AIError/unparseable-reply handling)
    just means the game stays visible, same as if the pass had never run."""
    html = (Path(games_dir) / slug / "index.html").read_text(encoding="utf-8")
    check_result = content_moderation.check_game(html, description, notes)
    if check_result["flagged"]:
        db.set_game_hidden(game_id, True, conn=db_conn)
        db.create_report(
            game_id=game_id, reporter_uid=None, ip_address="system",
            reason=check_result["reason"], source="moderation", conn=db_conn,
        )


# ---------------------------------------------------------------------------
# Play URL
# ---------------------------------------------------------------------------

def build_play_url(slug: str, config: dict) -> str:
    gw_cfg = config.get("game_web", {})
    base_url = (gw_cfg.get("base_url") or "").rstrip("/")
    if base_url:
        return f"{base_url}/play/{slug}"
    host = gw_cfg.get("host") or "localhost"
    if host == "0.0.0.0":
        host = "localhost"
    port = gw_cfg.get("port", 8600)
    return f"http://{host}:{port}/play/{slug}"


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------

def _build_system_prompt() -> str:
    allowed_hosts = ", ".join(sorted(safety.ALLOWED_CDN_HOSTS))
    return (
        "You are generating a new browser game for an arcade site that "
        "hosts single-file HTML5/JavaScript games.\n\n"
        "## Contract\n"
        "Reply with exactly ONE self-contained index.html file — all HTML, "
        "CSS, and JavaScript inline in that one file. Canvas or plain DOM, "
        "whatever suits the game. You may load external JavaScript modules "
        "or stylesheets via <script>/<link> tags ONLY from these CDN hosts: "
        f"{allowed_hosts}. Do not reference any other external host, and do "
        "not attempt any network calls back to this site or anywhere else "
        "at runtime.\n\n"
        "## Sandbox constraints\n"
        "The game will be played inside a sandboxed <iframe> with no "
        "same-origin access: document.cookie, localStorage, sessionStorage, "
        "indexedDB, and window.parent/window.top are all unavailable — keep "
        "all game state in ordinary JavaScript variables. Do not use "
        "eval() or `new Function(...)`.\n\n"
        "The game must respond to keyboard, mouse, or touch input as "
        "appropriate, must render something immediately on load (never a "
        "blank screen), and must not throw uncaught exceptions during normal "
        "play.\n\n"
        "## Quality bar\n"
        "Make it a complete, satisfying game, not a bare-bones prototype: "
        "give the player clear feedback (score, lives, or similar), a "
        "sensible difficulty curve, an explicit win/lose or game-over state, "
        "and an obvious way to restart without reloading the page. A small, "
        "focused game that fully works and feels polished beats an "
        "ambitious one that's half-finished.\n\n"
        + SUBMIT_TOOL_INSTRUCTIONS
    )


def _build_user_prompt(description: str) -> str:
    return f"Generate a browser game for this request: {description}"


# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------

def format_report(result: dict) -> str:
    footer = (
        f"[tokens: {result['tokens_used']} | model: {result['model'] or 'default'} | "
        f"effort: {result['effort']} | time: {result['duration_seconds']:.1f}s | "
        f"attempts: {result['attempts']}]"
    )
    if result["success"]:
        lines = [f"Done! '{result['title']}' is live — play it: {result['url']}"]
        if result.get("notes"):
            lines.append(f"Note: {result['notes']}")
        lines.append(footer)
    else:
        lines = [f"Game generation failed: {result['error']}", footer]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Shared retry loop (used by generate_game() and game_enhancer.enhance_game())
# ---------------------------------------------------------------------------

def _redact_raw_response(raw_response: dict) -> str:
    """JSON-serialize ai_client's raw API response for the generation_attempts
    audit trail, with each submit_game call's arguments blanked out (via
    ai_client.redact_tool_call_arguments) — the game source in there is
    already on disk (or, on a failed attempt, already discarded), so
    keeping a second copy in every attempt row would make the table
    balloon for no benefit. Everything else (ids, timestamps, finish_reason,
    usage, reasoning_content if thinking mode was on) is kept as-is."""
    return json.dumps(ai.redact_tool_call_arguments(raw_response), default=str)


# @traceable makes each job one LangSmith parent trace, so every retry's
# DeepSeek call (traced inside ai_client via wrap_openai) nests under it
# instead of appearing as disconnected calls. Pass-through no-op unless
# LANGSMITH_TRACING is enabled in the environment.
@traceable(name="run_generation_attempts")
def run_generation_attempts(*, description: str, requested_by: str, system_prompt: str,
                             initial_user_prompt: str, cfg: dict, games_dir: Path,
                             job_id: str | None = None, db_conn=None,
                             parent_game_id: str | None = None,
                             root_game_id: str | None = None,
                             title_override: str | None = None) -> dict:
    """Drive the submit -> safety-scan -> mint id/slug -> write ->
    smoke-test loop shared by a brand-new game and an enhancement fork,
    as ONE multi-turn conversation: each rejected submit_game call gets
    the concrete failure back as its tool result, so the model fixes the
    code it already has in context instead of regenerating from scratch.
    Every submission is written to the real final games/<slug>/ location
    (never a temp/staging dir) and rolled back on failure, so a caller
    never has to reconcile a half-written directory.

    `title_override`, when given, replaces the model's own title in the
    written meta.json/slug/result — used by enhance_game() to apply its
    "<source title> (vN)" / user-supplied fork title instead of whatever
    the model calls the revised game. `parent_game_id`/`root_game_id` are
    written into meta.json as-is (None/None for a brand-new original,
    which write_game_files' caller then treats as "root_game_id = self").

    Does not touch the web_games table — callers register the result
    themselves once they've computed their own bookkeeping (duration,
    status, etc.) around this loop.

    Returns a dict: success/game_id/slug/title/description/notes/attempts/
    tokens_used/model/effort/error.
    """
    max_attempts = cfg.get("max_attempts", 3)
    model = cfg.get("model", "")
    effort = cfg.get("effort", "high")
    ai_timeout = cfg.get("timeout_seconds", 120)
    smoke_timeout = cfg.get("smoke_test_timeout_seconds", 20)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": initial_user_prompt},
    ]

    total_input_tokens = 0
    total_output_tokens = 0
    last_model = model or "default"
    last_effort = effort
    previous_failure = None
    game_id = None
    slug = None
    title = None
    description_out = None
    notes = ""
    attempt = 0

    def record_attempt(attempt, outcome, *, detail=None, input_tokens=None, tokens_used=None,
                        duration_seconds=None, raw_response=None):
        if job_id is not None:
            db.add_generation_attempt(
                job_id, attempt, outcome, detail=detail, input_tokens=input_tokens,
                tokens_used=tokens_used,
                duration_seconds=duration_seconds, raw_response=raw_response,
                conn=db_conn,
            )

    def reject(tool_call_id, reason):
        """Feed a failure back to the model as the tool-call result."""
        messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": (
                f"REJECTED: {reason}\n\nFix the problem and call submit_game "
                "again with the complete corrected index.html source."
            ),
        })

    for attempt in range(1, max_attempts + 1):
        attempt_t0 = time.monotonic()
        try:
            ask_result = ai.ask_with_tools(
                messages,
                tools=[SUBMIT_GAME_TOOL],
                tool_choice=SUBMIT_TOOL_CHOICE,
                model=model,
                effort=effort,
                timeout=ai_timeout,
            )
        except ai.AIError as exc:
            # Transport/API failure: nothing to append, same conversation
            # state is retried on the next attempt.
            previous_failure = f"AI error: {exc}"
            record_attempt(attempt, "ai_error", detail=previous_failure,
                           duration_seconds=time.monotonic() - attempt_t0)
            continue

        total_input_tokens += ask_result.input_tokens
        total_output_tokens += ask_result.output_tokens
        last_model = ask_result.model or "default"
        last_effort = ask_result.effort
        redacted = _redact_raw_response(ask_result.raw_response)
        messages.append(ask_result.message)

        if not ask_result.tool_calls:
            previous_failure = "malformed submission: no submit_game tool call in reply"
            messages.append({
                "role": "user",
                "content": "You must call the submit_game tool to hand the game over. "
                           "Call it now with the complete index.html source.",
            })
            record_attempt(attempt, "ai_error", detail=previous_failure,
                           input_tokens=ask_result.input_tokens,
                           tokens_used=ask_result.output_tokens,
                           duration_seconds=time.monotonic() - attempt_t0,
                           raw_response=redacted)
            continue

        # Every tool_call_id needs a tool reply before the next model turn;
        # only the first submission is evaluated.
        submission, extras = ask_result.tool_calls[0], ask_result.tool_calls[1:]
        for extra in extras:
            messages.append({
                "role": "tool",
                "tool_call_id": extra.id,
                "content": "Ignored: submit one game per turn. Only the first "
                           "submit_game call was evaluated.",
            })

        try:
            parsed = parse_submission(submission.arguments)

            violations = safety.scan(parsed["game_html"])
            if violations:
                raise GameGenerationError("safety violation: " + "; ".join(violations))

            final_title = title_override if title_override else parsed["title"]
            candidate_game_id = db.mint_game_id()
            candidate_slug = db.make_slug(final_title, candidate_game_id)
            collision = check_slug_collision(candidate_slug, games_dir)
            if collision:
                raise GameGenerationError(f"slug collision: {collision}")

            meta = {
                "game_id": candidate_game_id,
                "parent_game_id": parent_game_id,
                "root_game_id": root_game_id if root_game_id is not None else candidate_game_id,
                "title": final_title,
                "description": parsed["description"],
                "requested_by": requested_by,
                "created_at": datetime.datetime.now(datetime.UTC).isoformat().replace("+00:00", "Z"),
                "version": 1,
                "prompt": description,
            }
            game_dir = write_game_files(candidate_slug, parsed["game_html"], meta, games_dir)

            passed, detail = smoke_test.run_smoke_test(game_dir / "index.html", smoke_timeout)
            if not passed:
                rollback_game_files(game_dir)
                raise GameGenerationError(f"smoke test failed: {detail}")

            game_id = candidate_game_id
            slug = candidate_slug
            title = final_title
            description_out = parsed["description"]
            notes = parsed["notes"]
            record_attempt(attempt, "success", input_tokens=ask_result.input_tokens,
                           tokens_used=ask_result.output_tokens,
                           duration_seconds=time.monotonic() - attempt_t0,
                           raw_response=redacted)
            break

        except GameGenerationError as exc:
            previous_failure = str(exc)
            reject(submission.id, previous_failure)
            if previous_failure.startswith("safety violation"):
                outcome = "safety_violation"
            elif previous_failure.startswith("smoke test failed"):
                outcome = "smoke_test_failed"
            else:
                outcome = "ai_error"
            record_attempt(attempt, outcome, detail=previous_failure,
                           input_tokens=ask_result.input_tokens,
                           tokens_used=ask_result.output_tokens,
                           duration_seconds=time.monotonic() - attempt_t0,
                           raw_response=redacted)
            continue

    return {
        "success": slug is not None,
        "game_id": game_id, "slug": slug, "title": title, "description": description_out,
        "notes": notes, "attempts": attempt,
        "input_tokens": total_input_tokens, "output_tokens": total_output_tokens,
        "tokens_used": total_input_tokens + total_output_tokens,
        "model": last_model, "effort": last_effort, "error": previous_failure,
    }


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def generate_game(description: str, requested_by: str, config: dict, db_conn=None,
                   games_dir: Path | None = None, job_id: str | None = None,
                   creator_uid: str | None = None) -> dict:
    """Drive the full generate -> validate -> smoke-test retry loop and
    return a result dict (result["message"] is ready to display; DB
    registration is already performed once, on success, before returning)."""
    games_dir = Path(games_dir) if games_dir is not None else GAMES_DIR
    cfg = config.get("newaiwebgame", {})
    system_prompt = _build_system_prompt()

    t0 = time.monotonic()
    outcome = run_generation_attempts(
        description=description, requested_by=requested_by, system_prompt=system_prompt,
        initial_user_prompt=_build_user_prompt(description),
        cfg=cfg, games_dir=games_dir, job_id=job_id, db_conn=db_conn,
    )
    duration = time.monotonic() - t0

    if outcome["success"]:
        result = {
            "success": True, "game_id": outcome["game_id"], "slug": outcome["slug"],
            "title": outcome["title"], "description": outcome["description"],
            "attempts": outcome["attempts"],
            "input_tokens": outcome["input_tokens"], "output_tokens": outcome["output_tokens"],
            "tokens_used": outcome["tokens_used"],
            "model": outcome["model"], "effort": outcome["effort"],
            "duration_seconds": duration, "error": None, "notes": outcome["notes"],
            "url": build_play_url(outcome["slug"], config),
        }
        db.register_web_game(
            game_id=result["game_id"],
            slug=result["slug"],
            title=result["title"],
            description=result["description"],
            requested_by=requested_by,
            status="success",
            attempts=result["attempts"],
            version=1,
            model=result["model"],
            effort=result["effort"],
            duration_seconds=result["duration_seconds"],
            input_tokens=result["input_tokens"],
            output_tokens=result["output_tokens"],
            tokens_used=result["tokens_used"],
            error=None,
            parent_game_id=None,
            root_game_id=result["game_id"],
            creator_uid=creator_uid,
            conn=db_conn,
        )
        run_moderation_pass(
            result["game_id"], result["slug"], result["description"], result["notes"],
            games_dir, db_conn=db_conn,
        )
    else:
        result = {
            "success": False, "game_id": None, "slug": None, "title": None, "description": None,
            "attempts": outcome["attempts"],
            "input_tokens": outcome["input_tokens"], "output_tokens": outcome["output_tokens"],
            "tokens_used": outcome["tokens_used"],
            "model": outcome["model"], "effort": outcome["effort"],
            "duration_seconds": duration, "error": outcome["error"],
            "notes": "", "url": None,
        }

    result["message"] = format_report(result)
    return result
