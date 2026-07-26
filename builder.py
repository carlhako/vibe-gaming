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
"""

import json
import re
from pathlib import Path
from urllib.parse import urlparse

import safety
import smoke_test

_TAG_RE = re.compile(
    r'<link\b[^>]*\brel=["\']stylesheet["\'][^>]*\bhref=["\']([^"\']+)["\'][^>]*/?>'
    r"|"
    r'<script\b[^>]*\bsrc=["\']([^"\']+)["\'][^>]*>\s*</script>',
    re.IGNORECASE,
)


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


def build_game(src_dir) -> str:
    """Read src_dir/index.html and inline its local stylesheet/script refs,
    in document order. Deterministic: identical src/ always yields
    byte-identical output. Raises BuildError on a missing/escaping/circular
    local ref."""
    src_dir = Path(src_dir)
    index_path = src_dir / "index.html"
    if not index_path.is_file():
        raise BuildError(f"missing src/index.html in {src_dir}")
    html = index_path.read_text(encoding="utf-8")

    def replace(match: re.Match) -> str:
        tag = match.group(0)
        ref = match.group(1) if match.group(1) is not None else match.group(2)
        if not _is_local_ref(ref):
            return tag
        file_path = _resolve_local(src_dir, ref, index_path)
        content = file_path.read_text(encoding="utf-8")
        if tag.lower().lstrip().startswith("<link"):
            return f"<style>{content}</style>"
        return f"<script>{content}</script>"

    return _TAG_RE.sub(replace, html)


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


def write_built_index(game_dir) -> Path:
    """build_game(game_dir/src) and write the result to game_dir/index.html
    (the committed, served artifact). Returns the written path."""
    game_dir = Path(game_dir)
    built_html = build_game(game_dir / "src")
    index_path = game_dir / "index.html"
    index_path.write_text(built_html, encoding="utf-8")
    return index_path


def build_and_verify(game_dir) -> tuple[bool, str, str]:
    """Shared build -> scan -> smoke helper for the generation pipeline
    (Sprint 2 wires this into game_generator/game_enhancer for multi-file
    games). For a single-file game (no src/) this is a no-op passthrough
    that reads the already-authored index.html straight through.

    Returns (passed, detail, built_html). `detail` explains a safety or
    smoke failure; on success it's smoke_test's own success string.
    """
    game_dir = Path(game_dir)
    if is_multi_file(game_dir):
        try:
            built_html = build_game(game_dir / "src")
        except BuildError as exc:
            return False, f"build failed: {exc}", ""
        (game_dir / "index.html").write_text(built_html, encoding="utf-8")
    else:
        index_path = game_dir / "index.html"
        if not index_path.is_file():
            # Reachable mid-explode (Sprint 5): the model can call finish()
            # before src/index.html exists yet, and there's no legacy
            # index.html either since this fork was never staged from one.
            return False, "build failed: no src/index.html or index.html found yet", ""
        built_html = index_path.read_text(encoding="utf-8")

    violations = safety.scan(built_html)
    if violations:
        return False, "safety violation: " + "; ".join(violations), built_html

    passed, detail = smoke_test.run_smoke_test(game_dir / "index.html")
    return passed, detail, built_html
