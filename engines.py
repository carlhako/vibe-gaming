"""engines.py — what a 3D (three.js) game's HTML must look like, in one place.

A game's *engine* is chosen once, when the game is created, and recorded in
meta.json ("engine": "three", "engine_version": "0.185.1"). Every fork inherits
it. There is no conversion path between 2D and 3D: the engine is a property of
the lineage, not of a request.

three.js ships ESM only (the UMD builds were removed in r161), so a 3D game
resolves the bare specifier `three` through an import map. That import map is
**injected by the platform, never written by the model** — normalize() strips
whatever import map the HTML arrived with and inserts the canonical one for the
game's pinned version. Two things follow from that:

  - The model never has to get a URL right, and cannot point one somewhere
    else. safety.scan() enforces that the only import map present is this
    exact string, which is also the fix for the hole four pre-existing games
    went through: import map URLs live in JSON, not in a src=/href= attribute,
    so the scanner never saw them at all.
  - normalize() is idempotent. It has to be: enhancing a single-file game
    resubmits the stored HTML, so the model sees the injected import map and
    echoes it back, and normalizing that must produce identical bytes.

The version is pinned per game and appears in the URL path, so vendoring a
newer three.js is purely additive — a game keeps resolving to the tree it was
generated against and can never break on an upstream API change.
"""

import re
from pathlib import Path

# A CODE default, not a config one: config.yaml is gitignored, so a config-only
# default never reaches a fresh clone or a deployment.
DEFAULT_THREE_VERSION = "0.185.1"  # three.js r185

ENGINE_THREE = "three"
VALID_ENGINES = frozenset({ENGINE_THREE})

VENDOR_ROOT = Path(__file__).resolve().parent / "vendor"

_IMPORTMAP_RE = re.compile(
    r'<script\b[^>]*\btype\s*=\s*["\']importmap["\'][^>]*>.*?</script\s*>',
    re.IGNORECASE | re.DOTALL,
)
_HEAD_OPEN_RE = re.compile(r"<head\b[^>]*>", re.IGNORECASE)


THREE_REVISION = "r185"  # the human-facing name for DEFAULT_THREE_VERSION

# Every rule below maps to a gate that rejects the submission if broken, which
# is why this text is shared rather than paraphrased per prompt: generate,
# enhance, multi-file enhance and explode all have to describe the same game.
_THREE_SHARED_RULES = (
    "`three` and those two controls addons are the ONLY modules available — "
    "no other addon, loader, or package exists on this server, and "
    "referencing three.js by URL from any CDN is rejected. Import only the "
    "controls you actually use.\n\n"
    "Because the game's code is a module, its functions are NOT global, so "
    "inline HTML event attributes (onclick=\"start()\") silently fail to "
    "resolve. Wire every control up with addEventListener instead.\n\n"
    "No external assets: the game has no network access at runtime, so build "
    "every mesh, texture and sound in code (procedural geometry, "
    "CanvasTexture, DataTexture, WebAudio) or embed it as a data: URI. Any "
    "loader that fetches a file — GLTFLoader, or TextureLoader pointed at a "
    "URL — is blocked and will fail.\n\n"
    "Verification runs on a software GPU, so keep the scene modest: no heavy "
    "post-processing, and clamp the pixel ratio with "
    "renderer.setPixelRatio(Math.min(devicePixelRatio, 2)).\n\n"
)

_THREE_IMPORT_LINES = (
    "    import * as THREE from 'three';\n"
    "    import { OrbitControls } from 'three/addons/controls/OrbitControls.js';\n"
    "    import { PointerLockControls } from "
    "'three/addons/controls/PointerLockControls.js';\n\n"
)


def three_contract(multi_file: bool = False) -> str:
    """The `## 3D with three.js` prompt section. `multi_file` picks the variant
    for the split format, where the build supplies the import header and each
    module must therefore carry no import/export of its own."""
    header = (
        f"## 3D with three.js\n"
        f"This is a 3D game built with three.js {THREE_REVISION}, self-hosted "
        f"by this site.\n\n"
    )
    if multi_file:
        return (
            header
            + "The build concatenates your src/ modules into ONE "
            "<script type=\"module\"> whose first two lines are already:\n\n"
            + _THREE_IMPORT_LINES
            + "So THREE, OrbitControls and PointerLockControls are ambient — "
            "use them from any "
            "module, and never declare, import or export them. A module "
            "carrying its own `import` or `export` statement is rejected by "
            "the build, because it would land in the middle of the merged "
            "module where imports are a syntax error. src/index.html must not "
            "contain an import map either; the site injects it.\n\n"
            + _THREE_SHARED_RULES
        )
    return (
        header
        + "Put your game code in a <script type=\"module\"> and import what "
        "you need:\n\n"
        + _THREE_IMPORT_LINES
        + "The import map that resolves those two specifiers is injected into "
        "your page automatically — do NOT write a "
        "<script type=\"importmap\"> block yourself.\n\n"
        + _THREE_SHARED_RULES
    )


class EngineError(ValueError):
    """Raised when HTML can't be normalized for its engine — no <head> to
    inject into, an unknown engine, or a version that was never vendored."""


def vendor_dir(version: str) -> Path:
    return VENDOR_ROOT / "three" / version


def available_versions() -> set[str]:
    """three.js versions vendored on disk. Used by the /vendor route to reject
    anything that isn't a real directory rather than probing the filesystem."""
    root = VENDOR_ROOT / "three"
    if not root.is_dir():
        return set()
    return {p.name for p in root.iterdir() if p.is_dir()}


def vendor_url_prefix(version: str) -> str:
    """The one URL prefix a 3D game is allowed to reference locally."""
    return f"/vendor/three/{version}/"


def importmap_html(version: str) -> str:
    """The canonical import map block. Byte-stable: safety.scan() compares
    against this exact string, so any change here is a change to what counts
    as a legal 3D game."""
    prefix = vendor_url_prefix(version)
    return (
        '<script type="importmap">'
        '{"imports":{'
        f'"three":"{prefix}three.module.min.js",'
        f'"three/addons/":"{prefix}addons/"'
        "}}</script>"
    )


def from_meta(meta: dict) -> tuple[str | None, str]:
    """(engine, version) out of a meta.json dict. Engine is None for an
    ordinary 2D game; version is meaningless in that case but defaulted so
    callers never have to special-case it."""
    if not isinstance(meta, dict):
        return None, DEFAULT_THREE_VERSION
    engine = meta.get("engine") or None
    if engine not in VALID_ENGINES:
        return None, DEFAULT_THREE_VERSION
    return engine, meta.get("engine_version") or DEFAULT_THREE_VERSION


def normalize(html: str, engine: str | None,
              version: str = DEFAULT_THREE_VERSION) -> str:
    """Return `html` with the canonical import map for `engine` in place.

    A passthrough for a 2D game (engine None/empty). For a 3D game: strip every
    existing import map, then insert the canonical one immediately after the
    <head> open tag. Idempotent — normalizing already-normalized HTML returns
    identical bytes.

    Raises EngineError for an unknown engine, a version that isn't vendored, or
    HTML with no <head> to inject into. All three messages name a fix the model
    can actually apply, since they come back as a rejected submission.
    """
    if not engine:
        return html
    if engine not in VALID_ENGINES:
        raise EngineError(
            f"unknown engine {engine!r} (supported: {', '.join(sorted(VALID_ENGINES))})"
        )
    if not vendor_dir(version).is_dir():
        raise EngineError(
            f"three.js {version} is not vendored on this server "
            f"(available: {', '.join(sorted(available_versions())) or 'none'})"
        )

    stripped = _IMPORTMAP_RE.sub("", html)
    head = _HEAD_OPEN_RE.search(stripped)
    if head is None:
        raise EngineError(
            "no <head> element to attach the three.js import map to — a 3D "
            "game must be a complete HTML document with <head> and <body>"
        )
    # Injected with no surrounding whitespace on purpose: strip-then-insert is
    # then exactly reversible, so a second pass over already-normalized HTML
    # produces identical bytes. Padding it with a newline instead would leave
    # that newline behind on the strip and grow the file by one byte per
    # enhance, forever.
    insert_at = head.end()
    return stripped[:insert_at] + importmap_html(version) + stripped[insert_at:]
