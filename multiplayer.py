"""multiplayer.py — what a multiplayer game's served HTML must carry, and how
the opt-in is read, in one place.

A game is multiplayer if and only if its meta.json holds a ``multiplayer``
object with an integer ``max_players`` of at least 2 (read_max_players). The
block is written at generation time from the new-game form and inherited
unchanged by every fork/enhancement, exactly the way ``engine`` is.

Client injection mirrors engines.py's import-map handling:

  - The realtime client (VG_RT, ``vendor/rt/rt.js``) is injected by the
    platform, never written by the model. normalize() strips whatever copy of
    the client tag the HTML arrived with and inserts the canonical one
    immediately before ``</head>``.
  - normalize() is idempotent for a given game: a single-file enhance
    resubmits the stored HTML with the injected tag already present, and
    normalizing that must produce byte-identical output.
  - The tag carries the game's own ``game_id`` as a data attribute, because
    the client derives the hub URL from ``location`` and the game is served at
    ``/play/<slug>`` where the slug holds only a prefix of the id. A fork gets
    a new game_id, so the strip accepts any prior id and re-inserts the
    current one — still idempotent per game.

safety.scan() always allows a local ``/vendor/rt/`` ref (it is a real
first-party served path, like the three.js vendor prefix), and
game_csp()'s ``connect-src`` / ``script-src`` already name it.
"""

import re

RT_SRC = "/vendor/rt/rt.js"

# Whole <script ...src=.../vendor/rt/rt.js...></script>, any attribute order,
# so a model-echoed copy on an enhance is removed no matter how it was written.
_RT_TAG_RE = re.compile(
    r'<script\b[^>]*\bsrc\s*=\s*["\'][^"\']*/vendor/rt/rt\.js["\'][^>]*>\s*</script\s*>',
    re.IGNORECASE,
)
_HEAD_CLOSE_RE = re.compile(r"</head\s*>", re.IGNORECASE)


class MultiplayerError(ValueError):
    """Raised when a multiplayer game's HTML can't take the client tag — no
    </head> to inject before. The message names a fix the model can apply,
    since it comes back as a rejected submission."""


def read_max_players(meta) -> int | None:
    """``meta['multiplayer']['max_players']`` if it is an int >= 2, else None.
    A bool, a float, a string, ``< 2`` or an absent/!dict block all yield
    None — i.e. "single-player"."""
    if not isinstance(meta, dict):
        return None
    block = meta.get("multiplayer")
    if not isinstance(block, dict):
        return None
    mp = block.get("max_players")
    if isinstance(mp, bool) or not isinstance(mp, int):
        return None
    return mp if mp >= 2 else None


def is_multiplayer(meta) -> bool:
    return read_max_players(meta) is not None


def client_tag(game_id: str) -> str:
    """The canonical injected tag. Byte-stable for a given game_id, so
    strip-then-insert over already-normalized HTML is exactly reversible."""
    return f'<script src="{RT_SRC}" data-game-id="{game_id}"></script>'


def normalize(html: str, multiplayer: bool, game_id: str) -> str:
    """Return `html` with exactly one canonical VG_RT client tag before
    </head> when `multiplayer` is true; a pure passthrough otherwise.

    Idempotent: normalizing already-normalized HTML for the same game_id
    returns identical bytes. Raises MultiplayerError if there is no </head>.
    """
    if not multiplayer:
        return html
    stripped = _RT_TAG_RE.sub("", html)
    m = _HEAD_CLOSE_RE.search(stripped)
    if m is None:
        raise MultiplayerError(
            "no </head> element to attach the multiplayer client to — a "
            "multiplayer game must be a complete HTML document with <head> "
            "and <body>"
        )
    at = m.start()
    # No surrounding whitespace on purpose: strip-then-insert is then exactly
    # reversible, so a second pass produces identical bytes (see engines.py).
    return stripped[:at] + client_tag(game_id) + stripped[at:]
