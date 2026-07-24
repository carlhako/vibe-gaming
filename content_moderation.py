"""
content_moderation — automated review pass for pure social-engineering copy.

safety.py's regex blocklist and smoke_test.py's runtime egress check both
catch mechanical bypasses (banned JS calls, off-allowlist src/href/action,
anomalous network requests). Neither can catch a game whose win screen just
tells the player, in plain visible text, to enter their password at some
other site — there's no code signature for that. check_game() asks the
model itself to review the game's player-facing text for exactly that class
of attack, as a single cheap, judgment-based backstop run once on the final
accepted submission (not on every retry attempt).

This is a backstop, not a gate: any failure to get a clean flagged/reason
verdict (AIError, unparseable reply) defaults to flagged=False so a
moderation-call outage never blocks a successful generation from
completing. See docs/security-fix/04-moderation-and-reporting.md.

# Exports:
#   check_game(html: str, description: str, notes: str) -> dict
#     ({"flagged": bool, "reason": str})
"""

import json
import logging

import ai_client as ai

_logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are a content moderator reviewing a browser game before it goes "
    "live on a public arcade site. You are given the game's full HTML "
    "source, the one-sentence description the requester gave, and any "
    "notes from whoever built it.\n\n"
    "Review only the game's VISIBLE, PLAYER-FACING TEXT — what a player "
    "actually reads on screen (titles, win/lose messages, dialogue, "
    "instructions, prizes, etc.) — not the code itself. Flag it if that "
    "text solicits credentials (passwords, login info), payment or card "
    "details, personal data (SSN, address, phone number), or directs the "
    "player to visit an external site/app/link to 'claim a reward', "
    "'verify their account', or similar — the hallmarks of phishing or "
    "social engineering. An ordinary game's normal UI text (score, lives, "
    "'game over', 'you win', instructions to restart, etc.) is never "
    "flagged.\n\n"
    "Reply with EXACTLY ONE LINE of JSON and nothing else: "
    '{"flagged": true or false, "reason": "one short sentence"}. '
    'If not flagged, reason can be an empty string.'
)


def _build_user_prompt(html: str, description: str, notes: str) -> str:
    return (
        f"Description: {description}\n"
        f"Notes: {notes or '(none)'}\n\n"
        f"HTML source:\n```html\n{html}\n```"
    )


def check_game(html: str, description: str, notes: str) -> dict:
    """Ask DeepSeek to review a game's player-facing text for
    phishing/social-engineering copy. Returns {"flagged": bool, "reason":
    str}. Never raises — any AIError or unparseable reply is treated as
    flagged=False, since this is a backstop and must never block a
    successful generation."""
    try:
        result = ai.ask(
            _build_user_prompt(html, description, notes),
            system_prompt=_SYSTEM_PROMPT,
            model=ai.MODEL_DEFAULT,  # pin to the cheap/fast model regardless of what "default" means elsewhere
            effort=None,  # non-thinking: no reasoning tokens for a pass this cheap and frequent
            temperature=0.0,
            response_format={"type": "json_object"},
        )
    except ai.AIError as exc:
        _logger.warning("content moderation call failed, defaulting to unflagged: %s", exc)
        return {"flagged": False, "reason": ""}

    try:
        parsed = json.loads(result.text)
        flagged = bool(parsed.get("flagged"))
        reason = parsed.get("reason") or ""
        if not isinstance(reason, str):
            reason = str(reason)
        return {"flagged": flagged, "reason": reason}
    except (json.JSONDecodeError, AttributeError) as exc:
        _logger.warning("content moderation reply unparseable, defaulting to unflagged: %s (%r)",
                         exc, result.text)
        return {"flagged": False, "reason": ""}
