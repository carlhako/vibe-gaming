"""
game_generator — the engine behind AI game generation.

Given a natural-language description, drives DeepSeek (via `ai_client`) to
produce a single self-contained `index.html` browser game: validated
statically (safety.py), written to its real final location
(games/<slug>/), and only kept if a headless-browser smoke test
(smoke_test.py) actually passes. Up to `max_attempts` retries are made, each
one feeding the previous concrete failure back to the model.

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
#   slugify(title) -> str
#   check_slug_collision(slug, games_dir) -> str | None
#   parse_generation_response(text) -> dict
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

import ai_client as ai
import db
import safety
import smoke_test

# Must match app.py's _SLUG_RE — that's what actually gatekeeps /play/<slug>;
# this copy gatekeeps what gets written to disk in the first place.
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,59}$")
RESERVED_SLUGS = {"backups"}

_CODE_FENCE_RE = re.compile(r"```(?:\w+)?\s*\n(.*?)```", re.DOTALL)

GAMES_DIR = Path(__file__).resolve().parent / "games"


class GameGenerationError(Exception):
    """Recoverable failure in the generation pipeline. str(exc) is fed back
    into the next retry's prompt as the concrete failure reason."""


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

_MARKERS = ("===GAME_FILE===", "===META===", "===NOTES===")


def parse_generation_response(text: str) -> dict:
    """Split the model's reply into game_html / title / description / notes
    using the required sentinel markers. Raises GameGenerationError on any
    missing or out-of-order marker, a missing fenced code block, or META that
    isn't valid JSON with non-empty 'title' and 'description' strings.

    There is deliberately no required closing marker after NOTES: models
    reliably omit a trailing sentinel that has nothing following it, so the
    NOTES section simply runs to the end of the text."""
    positions = []
    for marker in _MARKERS:
        idx = text.find(marker)
        if idx == -1:
            raise GameGenerationError(f"malformed response: missing marker '{marker}'")
        positions.append(idx)

    if positions != sorted(positions):
        raise GameGenerationError("malformed response: markers are out of order")

    game_idx, meta_idx, notes_idx = positions
    game_section = text[game_idx + len(_MARKERS[0]):meta_idx]
    meta_section = text[meta_idx + len(_MARKERS[1]):notes_idx]
    notes_section = text[notes_idx + len(_MARKERS[2]):].strip()

    game_match = _CODE_FENCE_RE.search(game_section)
    if not game_match:
        raise GameGenerationError("malformed response: missing code fence in GAME_FILE section")
    meta_match = _CODE_FENCE_RE.search(meta_section)
    if not meta_match:
        raise GameGenerationError("malformed response: missing code fence in META section")

    try:
        meta = json.loads(meta_match.group(1))
    except json.JSONDecodeError as exc:
        raise GameGenerationError(f"malformed response: META is not valid JSON: {exc}")

    title = meta.get("title") if isinstance(meta, dict) else None
    description = meta.get("description") if isinstance(meta, dict) else None
    if not isinstance(title, str) or not title.strip():
        raise GameGenerationError("malformed response: META is missing a non-empty 'title'")
    if not isinstance(description, str) or not description.strip():
        raise GameGenerationError("malformed response: META is missing a non-empty 'description'")

    notes = "" if notes_section.lower() in ("", "none") else notes_section

    return {
        "game_html": game_match.group(1),
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
        "Contract: reply with exactly ONE self-contained index.html file — "
        "all HTML, CSS, and JavaScript inline in that one file. Canvas or "
        "plain DOM, whatever suits the game. You may load external "
        "JavaScript modules or stylesheets via <script>/<link> tags ONLY "
        f"from these CDN hosts: {allowed_hosts}. Do not reference any other "
        "external host, and do not attempt any network calls back to this "
        "site or anywhere else at runtime.\n\n"
        "The game will be played inside a sandboxed <iframe> with no "
        "same-origin access: document.cookie, localStorage, sessionStorage, "
        "indexedDB, and window.parent/window.top are all unavailable — keep "
        "all game state in ordinary JavaScript variables. Do not use "
        "eval() or `new Function(...)`.\n\n"
        "The game must respond to keyboard, mouse, or touch input as "
        "appropriate, must render something immediately on load (never a "
        "blank screen), and must not throw uncaught exceptions during normal "
        "play.\n\n"
        "Reply in EXACTLY this format, with nothing outside the markers:\n\n"
        f"{_MARKERS[0]}\n```html\n<the complete index.html source>\n```\n"
        f"{_MARKERS[1]}\n```json\n"
        '{"title": "<short game title>", "description": "<one-sentence description>"}\n'
        "```\n"
        f"{_MARKERS[2]}\n<one or two sentences of notes, or the literal word "
        '"None">\n\n'
        "The NOTES section is advisory only. It is the last section — "
        "nothing should follow it."
    )


def _build_user_prompt(description: str, attempt: int, previous_failure: str | None) -> str:
    if attempt == 1 or previous_failure is None:
        return f"Generate a browser game for this request: {description}"
    return (
        f"Attempt {attempt}: your previous attempt failed because: "
        f"{previous_failure}\n\nFix it and resubmit the game in the required "
        f"format. Original request: {description}"
    )


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
# Orchestrator
# ---------------------------------------------------------------------------

def generate_game(description: str, requested_by: str, config: dict, db_conn=None,
                   games_dir: Path | None = None, job_id: str | None = None) -> dict:
    """Drive the full generate -> validate -> smoke-test retry loop and
    return a result dict (result["message"] is ready to display; DB
    registration is already performed once, on success, before returning)."""
    games_dir = Path(games_dir) if games_dir is not None else GAMES_DIR

    cfg = config.get("newaiwebgame", {})
    max_attempts = cfg.get("max_attempts", 3)
    model = cfg.get("model", "")
    effort = cfg.get("effort", "high")
    ai_timeout = cfg.get("timeout_seconds", 120)
    smoke_timeout = cfg.get("smoke_test_timeout_seconds", 20)

    system_prompt = _build_system_prompt()

    total_tokens = 0
    last_model = model or "default"
    last_effort = effort
    previous_failure = None
    game_id = None
    slug = None
    title = None
    description_out = None
    notes = ""
    attempt = 0

    t0 = time.monotonic()

    for attempt in range(1, max_attempts + 1):
        user_prompt = _build_user_prompt(description, attempt, previous_failure)
        try:
            ask_result = ai.ask(
                user_prompt,
                system_prompt=system_prompt,
                model=model,
                effort=effort,
                timeout=ai_timeout,
            )
        except ai.AIError as exc:
            previous_failure = f"AI error: {exc}"
            if job_id is not None:
                db.add_generation_attempt(
                    job_id, attempt, "ai_error", detail=previous_failure, conn=db_conn
                )
            continue

        total_tokens += ask_result.output_tokens
        last_model = ask_result.model or "default"
        last_effort = ask_result.effort

        try:
            parsed = parse_generation_response(ask_result.text)

            violations = safety.scan(parsed["game_html"])
            if violations:
                raise GameGenerationError("safety violation: " + "; ".join(violations))

            candidate_game_id = db.mint_game_id()
            candidate_slug = db.make_slug(parsed["title"], candidate_game_id)
            collision = check_slug_collision(candidate_slug, games_dir)
            if collision:
                raise GameGenerationError(f"slug collision: {collision}")

            meta = {
                "game_id": candidate_game_id,
                "parent_game_id": None,
                "root_game_id": candidate_game_id,
                "title": parsed["title"],
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
            title = parsed["title"]
            description_out = parsed["description"]
            notes = parsed["notes"]
            if job_id is not None:
                db.add_generation_attempt(
                    job_id, attempt, "success", tokens_used=ask_result.output_tokens,
                    conn=db_conn,
                )
            break

        except GameGenerationError as exc:
            previous_failure = str(exc)
            if job_id is not None:
                msg = str(exc)
                if msg.startswith("safety violation"):
                    outcome = "safety_violation"
                elif msg.startswith("smoke test failed"):
                    outcome = "smoke_test_failed"
                else:
                    outcome = "ai_error"
                db.add_generation_attempt(
                    job_id, attempt, outcome, detail=msg,
                    tokens_used=ask_result.output_tokens, conn=db_conn,
                )
            continue

    duration = time.monotonic() - t0

    if slug is not None:
        result = {
            "success": True, "game_id": game_id, "slug": slug, "title": title,
            "description": description_out,
            "attempts": attempt, "tokens_used": total_tokens, "model": last_model,
            "effort": last_effort, "duration_seconds": duration, "error": None,
            "notes": notes, "url": build_play_url(slug, config),
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
            error=None,
            parent_game_id=None,
            root_game_id=result["game_id"],
            conn=db_conn,
        )
    else:
        result = {
            "success": False, "game_id": None, "slug": None, "title": None, "description": None,
            "attempts": max_attempts, "tokens_used": total_tokens, "model": last_model,
            "effort": last_effort, "duration_seconds": duration, "error": previous_failure,
            "notes": "", "url": None,
        }

    result["message"] = format_report(result)
    return result
