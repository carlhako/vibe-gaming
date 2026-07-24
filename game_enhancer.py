"""
game_enhancer — the engine behind AI game enhancement.

Given the game_id of an existing game and a natural-language enhancement/fix
request, drives DeepSeek (via ai_client) to produce a revised
self-contained index.html, following the same submit -> validate ->
verify -> retry shape as game_generator.py (the model hands work over via
the identical submit_game function tool, validated statically (safety.py),
and only kept if a headless-browser smoke test (smoke_test.py) passes) —
via the shared game_generator.run_generation_attempts() conversation loop,
where a rejected submission gets the concrete failure back as its
tool-call result and the model patches the code it already has in context.

Enhancing a game forks it: a brand-new game_id/slug/games/<slug>/ directory
is written, linked to its source via parent_game_id (the immediate source)
and root_game_id (the original ancestor, unchanged across an arbitrarily
long fork chain). The source game's files and web_games row are never
touched — a failed attempt just deletes the half-written new directory,
same failure path as a failed generate_game() call. Up to max_attempts
retries are made, each one feeding the previous concrete failure back to
the model.

Title: an explicit new_title is used verbatim if given; otherwise the fork
is auto-labeled "<base title> (v{n})", where n = count of existing
web_games rows sharing the source's root_game_id, plus 1 (so the first
fork of an original is "(v2)"). <base title> strips any trailing "(vN)"
the source title already carries, so enhancing a fork produces
"Tower Defence (v3)" rather than "Tower Defence (v2) (v3)".

# Exports:
#   class GameEnhancementError(Exception)
#   enhance_game(source_game_id, description, requested_by, config, db_conn=None,
#                games_dir=None, job_id=None, new_title=None) -> dict
#   resolve_target(game_id, games_dir, conn=None) -> dict  (the source's web_games row)
#   format_report(result) -> str
"""

from __future__ import annotations

import re
import time
from pathlib import Path

import db
import game_generator as gg
import safety


class GameEnhancementError(Exception):
    """Recoverable failure in the enhancement pipeline. str(exc) is fed back
    into the next retry's prompt as the concrete failure reason."""


# ---------------------------------------------------------------------------
# Target resolution
# ---------------------------------------------------------------------------

def resolve_target(game_id: str, games_dir: Path, conn=None) -> dict:
    """Validate `game_id` as a registered, on-disk game and return its
    web_games row. Raises GameEnhancementError otherwise. This only reads
    the source — enhancing never writes to or deletes the source's
    directory or row."""
    row = db.get_web_game(game_id, conn=conn)
    if row is None:
        raise GameEnhancementError(f"no game with id '{game_id}' exists")
    game_dir = Path(games_dir) / row["slug"]
    if not (game_dir / "index.html").exists():
        raise GameEnhancementError(f"no game with id '{game_id}' exists")
    return row


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------

def _build_system_prompt(source_title: str, existing_game_html: str) -> str:
    allowed_hosts = ", ".join(sorted(safety.ALLOWED_CDN_HOSTS))
    return (
        f"You are creating an enhanced/fixed version of an existing browser "
        f"game in the arcade, currently titled '{source_title}'. This "
        "produces a NEW game entry — the original is left completely "
        "untouched and stays in the arcade unchanged; you are producing "
        "revised content for a new entry that forks from it.\n\n"
        "## Contract\n"
        "Reply with exactly ONE self-contained index.html file — all HTML, "
        "CSS, and JavaScript inline in that one file, same as the original. "
        "Canvas or plain DOM, whatever suits the game. You may load "
        "external JavaScript modules or stylesheets via <script>/<link> "
        f"tags ONLY from these CDN hosts: {allowed_hosts}. Do not reference "
        "any other external host, and do not attempt any network calls back "
        "to this site or anywhere else at runtime.\n\n"
        "## Sandbox constraints\n"
        "The game will be played inside a sandboxed <iframe> with no "
        "same-origin access: document.cookie, localStorage, sessionStorage, "
        "indexedDB, and window.parent/window.top are all unavailable — keep "
        "all game state in ordinary JavaScript variables. Do not use "
        "eval() or `new Function(...)`.\n\n"
        "The game must respond to keyboard, mouse, or touch input as "
        "appropriate, must render something immediately on load (never a "
        "blank screen), and must not throw uncaught exceptions during "
        "normal play.\n\n"
        "## Quality bar\n"
        "Apply the requested change while preserving everything else about "
        "the game that the request doesn't ask you to touch — its feel, "
        "controls, and existing polish are working and should survive "
        "unrelated edits. Don't let the change regress the game to a "
        "half-finished state: it should still be complete and satisfying to "
        "play afterward, with clear feedback and an obvious way to restart.\n\n"
        "## Current game\n"
        f"```html\n{existing_game_html}\n```\n\n"
        + gg.SUBMIT_TOOL_INSTRUCTIONS
        + "\nUse `notes` for one or two sentences summarizing what changed."
    )


def _build_user_prompt(description: str) -> str:
    return f"Enhance/fix this game per this request: {description}"


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
        lines = [
            f"Done! '{result['title']}' is live as a new entry — "
            f"play it: {result['url']}"
        ]
        if result.get("notes"):
            lines.append(f"Note: {result['notes']}")
        lines.append(footer)
    else:
        lines = [f"Game enhancement failed: {result['error']}", footer]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def enhance_game(source_game_id: str, description: str, requested_by: str, config: dict,
                  db_conn=None, games_dir: Path | None = None, job_id: str | None = None,
                  new_title: str | None = None, creator_uid: str | None = None) -> dict:
    """Drive the full enhance -> validate -> smoke-test retry loop and
    return a result dict (result["message"] is ready to display; DB
    registration is already performed once, on success, before returning).

    On success this writes a brand-new games/<slug>/ directory and
    web_games row (parent_game_id=source_game_id, root_game_id=source's
    root_game_id) — the source game is never modified."""
    games_dir = Path(games_dir) if games_dir is not None else gg.GAMES_DIR
    cfg = config.get("enhanceaiwebgame", {})

    t0 = time.monotonic()

    try:
        source_row = resolve_target(source_game_id, games_dir, conn=db_conn)
    except GameEnhancementError as exc:
        result = {
            "success": False, "game_id": None, "slug": None, "title": None,
            "description": None, "attempts": 0,
            "input_tokens": 0, "output_tokens": 0, "tokens_used": 0, "model": "default",
            "effort": cfg.get("effort", "high"), "duration_seconds": time.monotonic() - t0,
            "error": str(exc), "notes": "", "url": None,
            "parent_game_id": None, "root_game_id": None,
        }
        result["message"] = format_report(result)
        return result

    existing_game_html = (games_dir / source_row["slug"] / "index.html").read_text(encoding="utf-8")

    title_override = (new_title or "").strip() or None
    if title_override is None:
        n = db.count_by_root(source_row["root_game_id"], conn=db_conn) + 1
        base_title = re.sub(r"\s*\(v\d+\)$", "", source_row["title"]).strip()
        title_override = f"{base_title} (v{n})"

    system_prompt = _build_system_prompt(source_row["title"], existing_game_html)

    outcome = gg.run_generation_attempts(
        description=description, requested_by=requested_by, system_prompt=system_prompt,
        initial_user_prompt=_build_user_prompt(description),
        cfg=cfg, games_dir=games_dir, job_id=job_id, db_conn=db_conn,
        parent_game_id=source_row["game_id"], root_game_id=source_row["root_game_id"],
        title_override=title_override,
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
            "url": gg.build_play_url(outcome["slug"], config),
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
            creator_uid=creator_uid,
            conn=db_conn,
        )
    else:
        result = {
            "success": False, "game_id": None, "slug": None, "title": None,
            "description": None, "attempts": outcome["attempts"],
            "input_tokens": outcome["input_tokens"], "output_tokens": outcome["output_tokens"],
            "tokens_used": outcome["tokens_used"], "model": outcome["model"],
            "effort": outcome["effort"], "duration_seconds": duration,
            "error": outcome["error"], "notes": "", "url": None,
            "parent_game_id": None, "root_game_id": None,
        }

    result["message"] = format_report(result)
    return result
