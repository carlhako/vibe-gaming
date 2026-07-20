"""
prompt_helper — the engine behind Idea Forge's prompt expansion.

One plain ai_client.ask() call that rewrites a rough game idea into a
fuller game-design brief, informed by a genre checklist
(genre_checklists.yaml) so genre-specific conventions the user didn't think
to mention (FPS lighting, racing camera/perspective, etc.) still make it
into the brief. Not a submit/retry tool-calling loop like
game_generator/game_enhancer — the user reviews and edits the result
client-side before it ever drives real generation, so output-format
enforcement here is best-effort (a JSON-mode request plus a tolerant
fallback parser), not the strict tool-call contract used elsewhere.

To add/adjust a genre, edit genre_checklists.yaml — no code change needed.

Note: prompt_help jobs share the same global one-at-a-time "generating"
slot (db.claim_next_queued_request) as create/enhance jobs, so a queued
prompt_help job can wait behind a long-running full game build. This is
inherent to the existing job-queue design, not a bug to fix here.

# Exports:
#   expand_prompt(rough_prompt, requested_by, config, db_conn=None,
#                 games_dir=None, job_id=None, source_game_id=None,
#                 creator_uid=None) -> dict
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import yaml

import ai_client as ai
import db

GAMES_DIR = Path(__file__).resolve().parent / "games"
_GENRE_CHECKLISTS_PATH = Path(__file__).resolve().parent / "genre_checklists.yaml"


def _load_genre_checklists() -> dict:
    if not _GENRE_CHECKLISTS_PATH.exists():
        return {}
    with open(_GENRE_CHECKLISTS_PATH) as f:
        return yaml.safe_load(f) or {}


# Loaded once at import time, like config.yaml elsewhere in this app — no
# hot-reload; restart the process after editing genre_checklists.yaml.
_GENRE_CHECKLISTS = _load_genre_checklists()


def _render_genre_checklist_block(checklists: dict) -> str:
    lines = []
    for key, entry in checklists.items():
        label = entry.get("label", key)
        bullets = "\n".join(f"  - {b}" for b in entry.get("checklist", []))
        lines.append(f"- {label} ({key}):\n{bullets}")
    return "\n".join(lines)


def _build_system_prompt(source_row: dict | None, existing_game_html: str | None) -> str:
    base = (
        "You help turn a rough, one-line browser-game idea into a clear, "
        "detailed brief. That brief will be handed directly to another AI "
        "model that writes the actual game, so your job is to remove "
        "ambiguity, not to change the concept.\n\n"
        "## What the game-writing model can build\n"
        "A single self-contained HTML5 file: all HTML, CSS, and JavaScript "
        "inline in one page, rendered with Canvas or plain DOM. It responds "
        "to keyboard, mouse, or touch input. It cannot use a server, a "
        "database, multiplayer networking, or any account/login system — "
        "everything must run entirely in the browser with no backend. It "
        "may load a script or stylesheet from a small allowlist of CDN "
        "hosts, but nothing else external.\n\n"
    )
    if source_row is not None and existing_game_html:
        base += (
            f"## Your task\n"
            f"The user wants to change an existing game, currently titled "
            f"\"{source_row['title']}\". Its current source is below. Take "
            f"their rough change request and rewrite it as a fuller, "
            f"clearer brief for the model that will implement the change — "
            f"keep their original intent intact, but ground the brief in "
            f"what the game actually currently does (its existing "
            f"mechanics, controls, and visual style) so the rewritten "
            f"request doesn't contradict or ignore them. Fill in specifics "
            f"such as:\n"
            f"- exactly what should change and what should stay the same\n"
            f"- how the change fits into the existing controls and mechanics\n"
            f"- any new win/lose/scoring implications\n"
            f"- visual/audio details if the change is visual\n"
            f"- how it should interact with existing difficulty progression, if at all\n\n"
            f"## Current game\n"
            f"```html\n{existing_game_html}\n```\n\n"
        )
    else:
        base += (
            "## Your task\n"
            "Take the rough idea below and rewrite it as a fuller "
            "game-design brief, in plain prose, that keeps the user's "
            "original concept intact but fills in the details a builder "
            "would need:\n"
            "- the core mechanic and moment-to-moment loop\n"
            "- controls (what keys/mouse/touch actions do what)\n"
            "- win/lose conditions and how scoring or progress works\n"
            "- visual style and mood, described concretely enough to picture\n"
            "- how difficulty ramps up over time, if that applies\n"
            "- one standout feature that would make this particular game memorable\n\n"
            "Only add details that are reasonable, natural extensions of "
            "what the user described — don't invent an unrelated genre or "
            "mechanic, and don't pad with generic filler. If the idea is "
            "already detailed, tighten and clarify it rather than "
            "inflating it.\n\n"
        )

    if _GENRE_CHECKLISTS:
        base += (
            "## Genre checklists\n"
            "Some game genres have conventions that are easy to forget "
            "unless called out explicitly — real games in this genre "
            "always have them, but a rough request rarely mentions them by "
            "name. Below is a checklist per genre this site sees often. "
            "Identify which genre (if any) the request most closely "
            "matches — using the change request AND the existing game's "
            "source when one is shown above — and make sure your rewritten "
            "brief covers every checklist item for that genre that isn't "
            "already specified differently. Never contradict something the "
            "user (or the existing game) already specifies. If the idea "
            "clearly blends two genres, apply both relevant checklists. If "
            "it doesn't match any listed genre well, ignore this section "
            "and rely on the general guidance above instead.\n\n"
            f"{_render_genre_checklist_block(_GENRE_CHECKLISTS)}\n\n"
        )

    genre_keys = ", ".join(_GENRE_CHECKLISTS.keys()) or "none configured"
    base += (
        "## Output format\n"
        "Reply with ONLY a single JSON object — no markdown fences, no "
        "commentary before or after — matching exactly this shape:\n"
        '{"detected_genre": "<one of: ' + genre_keys + ', or null>", '
        '"confidence": "<high|medium|low>", '
        '"expanded_prompt": "<the rewritten brief as plain prose, no markdown>"}\n'
        "Set detected_genre to null only if the idea doesn't meaningfully "
        "match any listed genre and isn't a clear blend of them. "
        "expanded_prompt must always be present and non-empty, and must "
        "never itself contain JSON or markdown — plain prose only."
    )
    return base


def _build_user_prompt(rough_prompt: str) -> str:
    return f"Rough idea: {rough_prompt}"


def _parse_expansion_response(raw_text: str) -> tuple[str, str | None]:
    """Returns (expanded_prompt, detected_genre). DeepSeek's JSON mode is
    "designed to," not guaranteed to, return valid JSON — this tolerates a
    stray code fence or outright non-JSON reply by falling back to treating
    the whole response as the expanded prompt with no detected genre,
    rather than failing the job."""
    text = raw_text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        parsed = json.loads(text)
        expanded = parsed.get("expanded_prompt")
        if isinstance(expanded, str) and expanded.strip():
            genre = parsed.get("detected_genre")
            return expanded.strip(), (genre if isinstance(genre, str) and genre else None)
    except (json.JSONDecodeError, AttributeError):
        pass
    return raw_text.strip(), None


def expand_prompt(rough_prompt: str, requested_by: str, config: dict, db_conn=None,
                   games_dir: Path | None = None, job_id: str | None = None,
                   source_game_id: str | None = None, creator_uid: str | None = None) -> dict:
    games_dir = Path(games_dir) if games_dir is not None else GAMES_DIR
    cfg = config.get("ideaforge", {})
    t0 = time.monotonic()

    source_row = None
    existing_game_html = None
    if source_game_id:
        source_row = db.get_web_game(source_game_id, conn=db_conn)
        if source_row is not None:
            game_dir = games_dir / source_row["slug"]
            index_path = game_dir / "index.html"
            if index_path.exists():
                existing_game_html = index_path.read_text(encoding="utf-8")
        # A stale/bad source_game_id or missing index.html falls back to
        # create-mode framing rather than failing outright — the user still
        # gets *some* expanded brief instead of a hard error on what already
        # looks like a running job to them.

    system_prompt = _build_system_prompt(source_row, existing_game_html)
    model = cfg.get("model")
    effort = cfg.get("effort", "low")
    timeout = cfg.get("timeout_seconds", 60)

    try:
        ask_result = ai.ask(
            _build_user_prompt(rough_prompt), system_prompt=system_prompt,
            model=model, effort=effort, timeout=timeout,
            response_format={"type": "json_object"},
        )
    except ai.AIError as exc:
        return {
            "success": False, "game_id": None, "result_text": None,
            "detected_genre": None,
            "attempts": 1, "tokens_used": None, "model": model or ai.MODEL_DEFAULT,
            "effort": effort, "duration_seconds": time.monotonic() - t0,
            "error": str(exc),
        }

    expanded_prompt, detected_genre = _parse_expansion_response(ask_result.text)
    return {
        "success": True, "game_id": None, "result_text": expanded_prompt,
        "detected_genre": detected_genre,
        "attempts": 1, "tokens_used": ask_result.output_tokens,
        "model": ask_result.model, "effort": ask_result.effort,
        "duration_seconds": time.monotonic() - t0, "error": None,
    }
