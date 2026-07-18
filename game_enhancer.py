"""
game_enhancer — the engine behind AI game enhancement.

Given the slug of an existing game and a natural-language enhancement/fix
request, drives DeepSeek (via ai_client) to produce a revised
self-contained index.html for that game, following the same
generate -> validate -> verify -> retry shape as game_generator.py: the
reply uses the identical GAME_FILE/META/NOTES marker protocol and is parsed
with game_generator.parse_generation_response, validated statically
(safety.py), and only kept if a headless-browser smoke test (smoke_test.py)
actually passes.

The current game directory (index.html + meta.json) is backed up — a full
copy under games/backups/<slug>/<timestamp>/ — before any write, and
restored automatically whenever an attempt doesn't pan out: a failed safety
scan or smoke test rolls the live files back immediately so the next retry
starts clean, and exhausting all attempts leaves the original game exactly
as it was. Up to max_attempts retries are made, each one feeding the
previous concrete failure back to the model.

The game's slug (and therefore its /play/<slug> URL) never changes across
an enhancement — only its title, description, and content can. requested_by
and created_at on the web_games row are preserved from the original
creation (db.register_web_game does not update either on conflict);
version is bumped by one on success.

# Exports:
#   class GameEnhancementError(Exception)
#   enhance_game(slug, description, requested_by, config, db_conn=None, games_dir=None, backups_dir=None) -> dict
#   resolve_target(slug, games_dir, conn=None) -> Path
#   backup_game_files(slug, game_dir, backups_dir) -> Path
#   restore_from_backup(game_dir, backup_dir) -> None
#   format_report(result) -> str
"""

from __future__ import annotations

import json
import shutil
import time
from datetime import datetime
from pathlib import Path

import ai_client as ai
import db
import game_generator as gg
import safety
import smoke_test

BACKUPS_DIR = gg.GAMES_DIR / "backups"


class GameEnhancementError(Exception):
    """Recoverable failure in the enhancement pipeline. str(exc) is fed back
    into the next retry's prompt as the concrete failure reason."""


# ---------------------------------------------------------------------------
# Target resolution
# ---------------------------------------------------------------------------

def resolve_target(slug: str, games_dir: Path, conn=None) -> Path:
    """Validate `slug` as a safe, existing, registered game and return its
    directory path. Raises GameEnhancementError otherwise. Registration in
    the web_games table (not just an on-disk directory) is the source of
    truth for "this slug can be enhanced"."""
    if not gg._SLUG_RE.match(slug):
        raise GameEnhancementError(
            f"invalid slug '{slug}' (must be lowercase alphanumeric-hyphen, 1-50 chars)"
        )
    if slug in gg.RESERVED_SLUGS:
        raise GameEnhancementError(f"'{slug}' cannot be enhanced — it's a reserved name")
    if db.get_web_game(slug, conn=conn) is None:
        raise GameEnhancementError(f"no game with slug '{slug}' exists")
    game_dir = Path(games_dir) / slug
    if not (game_dir / "index.html").exists():
        raise GameEnhancementError(f"no game with slug '{slug}' exists")
    return game_dir


# ---------------------------------------------------------------------------
# Backup / restore
# ---------------------------------------------------------------------------

def backup_game_files(slug: str, game_dir: Path, backups_dir: Path) -> Path:
    """Copy the current game directory into backups_dir/<slug>/<timestamp>/
    so a human can manually recover the pre-enhancement version later.
    Returns the backup directory path."""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = Path(backups_dir) / slug / stamp
    backup_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(game_dir, backup_dir)
    return backup_dir


def restore_from_backup(game_dir: Path, backup_dir: Path) -> None:
    """Restore game_dir to exactly match backup_dir's contents."""
    shutil.rmtree(game_dir, ignore_errors=True)
    shutil.copytree(backup_dir, game_dir)


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------

def _build_system_prompt(slug: str, existing_game_html: str) -> str:
    allowed_hosts = ", ".join(sorted(safety.ALLOWED_CDN_HOSTS))
    return (
        f"You are enhancing/fixing ONE existing browser game in the arcade, "
        f"with slug '{slug}'. The game's slug and URL never change — only "
        "its content, title, and description may.\n\n"
        "Contract: reply with exactly ONE self-contained index.html file — "
        "all HTML, CSS, and JavaScript inline in that one file, same as the "
        "original. Canvas or plain DOM, whatever suits the game. You may "
        "load external JavaScript modules or stylesheets via <script>/<link> "
        f"tags ONLY from these CDN hosts: {allowed_hosts}. Do not reference "
        "any other external host, and do not attempt any network calls back "
        "to this site or anywhere else at runtime.\n\n"
        "The game will be played inside a sandboxed <iframe> with no "
        "same-origin access: document.cookie, localStorage, sessionStorage, "
        "indexedDB, and window.parent/window.top are all unavailable — keep "
        "all game state in ordinary JavaScript variables. Do not use "
        "eval() or `new Function(...)`.\n\n"
        "The game must respond to keyboard, mouse, or touch input as "
        "appropriate, must render something immediately on load (never a "
        "blank screen), and must not throw uncaught exceptions during "
        "normal play. Apply the requested change while preserving "
        "everything else about the game that the request doesn't ask you "
        "to touch.\n\n"
        "Here is the CURRENT game:\n"
        f"```html\n{existing_game_html}\n```\n\n"
        "Reply in EXACTLY this format, with nothing outside the markers:\n\n"
        f"{gg._MARKERS[0]}\n```html\n<the complete updated index.html source>\n```\n"
        f"{gg._MARKERS[1]}\n```json\n"
        '{"title": "<short game title>", "description": "<one-sentence description>"}\n'
        "```\n"
        f"{gg._MARKERS[2]}\n<one or two sentences summarizing what changed, "
        'or the literal word "None">\n\n'
        "The NOTES section is advisory only. It is the last section — "
        "nothing should follow it."
    )


def _build_user_prompt(description: str, attempt: int, previous_failure: str | None) -> str:
    if attempt == 1 or previous_failure is None:
        return f"Enhance/fix this game per this request: {description}"
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
    backup_label = None
    if result.get("backup_dir"):
        backup_path = Path(result["backup_dir"])
        backup_label = f"{backup_path.parent.name}/{backup_path.name}"

    if result["success"]:
        lines = [
            f"Done! '{result['title']}' has been enhanced and is live — "
            f"play it: {result['url']}"
        ]
        if backup_label:
            lines.append(f"Original backed up as: {backup_label}")
        if result.get("notes"):
            lines.append(f"Note: {result['notes']}")
        lines.append(footer)
    else:
        lines = [f"Game enhancement failed: {result['error']}", footer]
        if backup_label:
            lines.append(f"Original game restored from backup: {backup_label}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def enhance_game(slug: str, description: str, requested_by: str, config: dict,
                  db_conn=None, games_dir: Path | None = None,
                  backups_dir: Path | None = None) -> dict:
    """Drive the full enhance -> validate -> smoke-test retry loop and
    return a result dict (result["message"] is ready to display; DB
    registration is already performed once, on success, before returning)."""
    games_dir = Path(games_dir) if games_dir is not None else gg.GAMES_DIR
    backups_dir = Path(backups_dir) if backups_dir is not None else BACKUPS_DIR

    cfg = config.get("enhanceaiwebgame", {})
    max_attempts = cfg.get("max_attempts", 3)
    model = cfg.get("model", "")
    effort = cfg.get("effort", "high")
    ai_timeout = cfg.get("timeout_seconds", 120)
    smoke_timeout = cfg.get("smoke_test_timeout_seconds", 20)

    t0 = time.monotonic()

    try:
        game_dir = resolve_target(slug, games_dir, conn=db_conn)
    except GameEnhancementError as exc:
        result = {
            "success": False, "slug": slug, "title": None, "description": None,
            "attempts": 0, "tokens_used": 0, "model": "default", "effort": effort,
            "duration_seconds": time.monotonic() - t0, "error": str(exc),
            "notes": "", "backup_dir": None, "url": None,
        }
        result["message"] = format_report(result)
        return result

    existing_meta = json.loads((game_dir / "meta.json").read_text(encoding="utf-8"))
    existing_game_html = (game_dir / "index.html").read_text(encoding="utf-8")

    backup_dir = backup_game_files(slug, game_dir, backups_dir)

    system_prompt = _build_system_prompt(slug, existing_game_html)

    total_tokens = 0
    last_model = model or "default"
    last_effort = effort
    previous_failure = None
    success = False
    title = None
    description_out = None
    notes = ""
    attempt = 0

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
            continue

        total_tokens += ask_result.output_tokens
        last_model = ask_result.model or "default"
        last_effort = ask_result.effort

        try:
            parsed = gg.parse_generation_response(ask_result.text)

            violations = safety.scan(parsed["game_html"])
            if violations:
                raise GameEnhancementError("safety violation: " + "; ".join(violations))

            new_meta = {
                "title": parsed["title"],
                "description": parsed["description"],
                "requested_by": existing_meta.get("requested_by", requested_by),
                "created_at": existing_meta.get("created_at"),
                "version": existing_meta.get("version", 1) + 1,
                "prompt": existing_meta.get("prompt", description),
            }
            gg.write_game_files(slug, parsed["game_html"], new_meta, games_dir)

            passed, detail = smoke_test.run_smoke_test(game_dir / "index.html", smoke_timeout)
            if not passed:
                restore_from_backup(game_dir, backup_dir)
                raise GameEnhancementError(f"smoke test failed: {detail}")

            title = parsed["title"]
            description_out = parsed["description"]
            notes = parsed["notes"]
            success = True
            break

        except GameEnhancementError as exc:
            previous_failure = str(exc)
            continue

    duration = time.monotonic() - t0

    if success:
        new_version = existing_meta.get("version", 1) + 1
        result = {
            "success": True, "slug": slug, "title": title, "description": description_out,
            "attempts": attempt, "tokens_used": total_tokens, "model": last_model,
            "effort": last_effort, "duration_seconds": duration, "error": None,
            "notes": notes, "backup_dir": str(backup_dir),
            "url": gg.build_play_url(slug, config),
        }
        db.register_web_game(
            slug=slug, title=title, description=description_out, requested_by=requested_by,
            status="success", attempts=attempt, version=new_version,
            model=last_model, effort=last_effort, duration_seconds=duration,
            error=None, conn=db_conn,
        )
    else:
        # Belt-and-braces: guarantee the original survives even if the last
        # attempt failed before reaching the write step (e.g. a safety
        # violation), in which case this is a harmless no-op restore.
        restore_from_backup(game_dir, backup_dir)
        result = {
            "success": False, "slug": slug, "title": None, "description": None,
            "attempts": max_attempts, "tokens_used": total_tokens, "model": last_model,
            "effort": last_effort, "duration_seconds": duration, "error": previous_failure,
            "notes": "", "backup_dir": str(backup_dir), "url": None,
        }

    result["message"] = format_report(result)
    return result
