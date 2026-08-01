"""
builder.py — deterministic build step for multi-file games (Sprint 1 of
docs/multifile-agent/).

A multi-file game keeps its source split across `src/index.html` (shell) +
`src/*.css` + `src/*.js`, so a generation/enhancement agent never has to
read or emit the whole game in one shot (see
docs/multifile-agent/00-overview.md). This module inlines that split source
back into one served `index.html`, mechanically and with no AI involved:
each local `<link rel="stylesheet" href="X.css">` becomes an inline
`<style>`, each local `<script src="X.js"></script>` becomes an inline
`<script>`, in document order. External CDN refs (an allow-listed host, per
safety.ALLOWED_CDN_HOSTS) are left as external tags — only local,
same-directory refs are inlined.

`safety.scan()` and `smoke_test.run_smoke_test()` then run against this
built artifact exactly as they do for a hand-authored single-file game —
they never need to know a game was split.

A 3D (three.js) game builds differently, because three.js is ESM-only and
`import` is a syntax error in a classic script: instead of one <script> block
per module, every local module is concatenated into ONE <script type="module">
carrying a fixed import header. See _build_module_bundle() — the important
consequence is that the multi-file format's "one shared scope" contract
survives intact, since one module scope is shared exactly the way the global
scope was.
"""

import json
import re
from pathlib import Path
from urllib.parse import urlparse

import engines
import safety
import smoke_test

_TAG_RE = re.compile(
    r'<link\b[^>]*\brel=["\']stylesheet["\'][^>]*\bhref=["\']([^"\']+)["\'][^>]*/?>'
    r"|"
    r'<script\b[^>]*\bsrc=["\']([^"\']+)["\'][^>]*>\s*</script>',
    re.IGNORECASE,
)


# The import header prepended to a 3D game's merged module. This is the whole
# reason src modules must not carry import statements of their own: they are
# concatenated after this header, and an `import` below the top of a module is
# a syntax error.
_MODULE_HEADER = (
    "import * as THREE from 'three';\n"
    "import { OrbitControls } from 'three/addons/controls/OrbitControls.js';\n"
    "import { PointerLockControls } from "
    "'three/addons/controls/PointerLockControls.js';\n"
)

# Deliberately anchored to the start of a line and requiring a following
# space/brace/quote/star, so `import(` (dynamic) and the word "import" inside
# prose or a string don't trip it.
_ESM_STATEMENT_RE = re.compile(
    r"^[ \t]*(?:import(?=[\s\"'{*])|export(?=[\s{]))", re.MULTILINE)


class BuildError(ValueError):
    """Raised when src/ can't be deterministically inlined: a referenced
    local file is missing, a ref escapes src/, or a ref is circular."""


def _is_local_ref(ref: str) -> bool:
    """True for a same-directory relative ref like "style.css". False for
    anything with a scheme or a protocol-relative host (CDN refs), which
    safety.py's allowlist governs and this builder leaves untouched."""
    return not urlparse(ref).scheme and not ref.startswith("//")


def _resolve_local(src_dir: Path, ref: str, index_path: Path) -> Path:
    if ref.startswith("/"):
        raise BuildError(f"absolute path not allowed in local ref: {ref!r}")
    resolved = (src_dir / ref).resolve()
    try:
        resolved.relative_to(src_dir.resolve())
    except ValueError:
        raise BuildError(f"ref escapes src/: {ref!r}") from None
    if resolved == index_path.resolve():
        raise BuildError(
            f"circular include: {ref!r} refers to src/index.html itself"
        )
    if not resolved.is_file():
        raise BuildError(f"referenced local file not found: {ref!r}")
    return resolved


def _reject_esm_statements(ref: str, content: str) -> None:
    """A 3D game's modules are concatenated into one <script type="module">
    below a fixed import header, so a module carrying its own import/export is
    a syntax error that would take the whole game down."""
    match = _ESM_STATEMENT_RE.search(content)
    if match is None:
        return
    line = content.count("\n", 0, match.start()) + 1
    keyword = content[match.start():match.end()].strip()
    raise BuildError(
        f"{ref} line {line}: `{keyword}` statement in a 3D game module. Every "
        f"src module is concatenated into one <script type=\"module\"> that "
        f"already imports THREE and the controls addons at the top, so modules "
        f"must not import or export anything themselves — delete this line and "
        f"use THREE (and OrbitControls / PointerLockControls) directly; every "
        f"module shares one scope."
    )


def build_game(src_dir, engine: str | None = None) -> str:
    """Read src_dir/index.html and inline its local stylesheet/script refs,
    in document order. Deterministic: identical src/ always yields
    byte-identical output. Raises BuildError on a missing/escaping/circular
    local ref.

    For a 3D game (engine="three") the local *script* refs are merged into a
    single <script type="module"> emitted at the position of the first one,
    prefixed by _MODULE_HEADER — three.js is ESM-only, and separate classic
    <script> blocks cannot import it. Stylesheet inlining and allow-listed CDN
    passthrough are identical in both modes, and module contents are still
    concatenated verbatim in document order.
    """
    src_dir = Path(src_dir)
    index_path = src_dir / "index.html"
    if not index_path.is_file():
        raise BuildError(f"missing src/index.html in {src_dir}")
    html = index_path.read_text(encoding="utf-8")
    bundling = engine == engines.ENGINE_THREE

    parts: list[str | None] = []
    modules: list[str] = []
    bundle_slot: int | None = None
    last = 0

    for match in _TAG_RE.finditer(html):
        parts.append(html[last:match.start()])
        last = match.end()
        tag = match.group(0)
        ref = match.group(1) if match.group(1) is not None else match.group(2)

        if not _is_local_ref(ref):
            parts.append(tag)
            continue

        content = _resolve_local(src_dir, ref, index_path).read_text(encoding="utf-8")
        if tag.lower().lstrip().startswith("<link"):
            parts.append(f"<style>{content}</style>")
            continue
        if not bundling:
            parts.append(f"<script>{content}</script>")
            continue

        _reject_esm_statements(ref, content)
        modules.append(content)
        if bundle_slot is None:
            # Reserve this position; every later module folds into the same
            # block, so the merged module lands where the first <script> was.
            bundle_slot = len(parts)
            parts.append(None)

    parts.append(html[last:])

    if bundle_slot is not None:
        parts[bundle_slot] = (
            '<script type="module">\n'
            + _MODULE_HEADER
            + "\n"
            + "\n".join(modules)
            + "\n</script>"
        )
    return "".join(part for part in parts if part is not None)


def is_multi_file(game_dir) -> bool:
    """True if game_dir is a multi-file game, false for a legacy
    single-file game. meta.json's "format" field is authoritative when
    present; otherwise fall back to whether a src/index.html shell exists
    (covers fixtures/tests that skip meta.json entirely)."""
    game_dir = Path(game_dir)
    meta_path = game_dir / "meta.json"
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            meta = {}
        fmt = meta.get("format")
        if fmt == "multi-file":
            return True
        if fmt == "single-file":
            return False
    return (game_dir / "src" / "index.html").is_file()


def read_engine(game_dir) -> tuple[str | None, str]:
    """(engine, engine_version) from game_dir/meta.json — (None, default) for
    an ordinary 2D game or an unreadable meta.json."""
    meta_path = Path(game_dir) / "meta.json"
    if not meta_path.is_file():
        return None, engines.DEFAULT_THREE_VERSION
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None, engines.DEFAULT_THREE_VERSION
    return engines.from_meta(meta)


def write_built_index(game_dir) -> Path:
    """build_game(game_dir/src) and write the result to game_dir/index.html
    (the committed, served artifact). Returns the written path."""
    game_dir = Path(game_dir)
    engine, version = read_engine(game_dir)
    built_html = engines.normalize(build_game(game_dir / "src", engine), engine, version)
    index_path = game_dir / "index.html"
    index_path.write_text(built_html, encoding="utf-8")
    return index_path


def build_and_verify(game_dir, smoke_timeout: int = 20, engine: str | None = None,
                     engine_version: str | None = None) -> tuple[bool, str, str]:
    """Shared build -> normalize -> scan -> smoke helper for the generation
    pipeline (Sprint 2 wires this into game_generator/game_enhancer for
    multi-file games). For a single-file game (no src/) the build step is a
    no-op passthrough that reads the already-authored index.html straight
    through; normalize/scan/smoke run identically for both formats.

    `engine`/`engine_version` override what meta.json says, and have to exist
    because a fork in progress has no meta.json yet: _stage_fork deliberately
    doesn't copy the source's, and explode writes one only once the run
    succeeds. Without the override a 3D game would verify as if it were 2D for
    the whole run — built as classic <script> blocks, failing every attempt on
    syntax errors that point nowhere near the real problem.

    Returns (passed, detail, built_html). `detail` explains a build, safety or
    smoke failure; on success it's smoke_test's own success string.
    """
    game_dir = Path(game_dir)
    meta_engine, meta_version = read_engine(game_dir)
    engine = engine if engine is not None else meta_engine
    version = engine_version or meta_version
    index_path = game_dir / "index.html"

    if is_multi_file(game_dir):
        try:
            built_html = build_game(game_dir / "src", engine)
        except BuildError as exc:
            return False, f"build failed: {exc}", ""
        on_disk = None
    else:
        if not index_path.is_file():
            # Reachable mid-explode (Sprint 5): the model can call finish()
            # before src/index.html exists yet, and there's no legacy
            # index.html either since this fork was never staged from one.
            return False, "build failed: no src/index.html or index.html found yet", ""
        built_html = on_disk = index_path.read_text(encoding="utf-8")

    try:
        built_html = engines.normalize(built_html, engine, version)
    except engines.EngineError as exc:
        return False, f"build failed: {exc}", built_html

    # Only rewrite when the bytes actually change: games/ is scanned with an
    # mtime cache, and a no-op rewrite would invalidate it on every verify.
    if built_html != on_disk:
        index_path.write_text(built_html, encoding="utf-8")

    violations = safety.scan(built_html, engine, version)
    if violations:
        return False, "safety violation: " + "; ".join(violations), built_html

    passed, detail = smoke_test.run_smoke_test(index_path, smoke_timeout, engine=engine)
    return passed, detail, built_html
