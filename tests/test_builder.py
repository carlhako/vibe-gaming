"""Sprint 1 of docs/multifile-agent/: builder.py inlines a multi-file
game's src/ directory into one served index.html, with zero AI involved.
These tests cover the build itself (Part B) and the hand-authored fixture
(Part D) from docs/multifile-agent/01-multifile-build.md."""

import json
from pathlib import Path

import pytest

import builder
import safety
import smoke_test

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "multifile-game"


# --- build_game: inlining, ordering, determinism ---------------------------


def test_build_game_inlines_css_and_js_in_document_order():
    html = builder.build_game(FIXTURE_DIR / "src")
    style_pos = html.index("<style>")
    script_pos = html.index("<script>")
    assert style_pos < script_pos, "style.css must be inlined before core.js"
    assert "background: #111" in html
    assert 'getElementById("count")' in html
    # Original local refs are gone — nothing left pointing at src/ files.
    assert "style.css" not in html
    assert "core.js" not in html


def test_build_game_is_byte_identical_across_repeated_builds():
    first = builder.build_game(FIXTURE_DIR / "src")
    second = builder.build_game(FIXTURE_DIR / "src")
    assert first == second


def test_build_game_leaves_allowlisted_cdn_refs_external(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "index.html").write_text(
        '<html><head>'
        '<link rel="stylesheet" href="https://fonts.googleapis.com/css?family=Foo">'
        '</head><body>'
        '<script src="https://cdn.jsdelivr.net/npm/foo@1/foo.min.js"></script>'
        "</body></html>",
        encoding="utf-8",
    )
    html = builder.build_game(src)
    assert '<link rel="stylesheet" href="https://fonts.googleapis.com' in html
    assert '<script src="https://cdn.jsdelivr.net/npm/foo@1/foo.min.js">' in html
    assert "<style>" not in html


# --- build_game: error cases -------------------------------------------------


def test_build_game_raises_on_missing_local_ref(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "index.html").write_text(
        '<html><head><link rel="stylesheet" href="missing.css"></head>'
        "<body></body></html>",
        encoding="utf-8",
    )
    with pytest.raises(builder.BuildError, match="not found"):
        builder.build_game(src)


def test_build_game_raises_on_path_escaping_src(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (tmp_path / "outside.css").write_text("body{}", encoding="utf-8")
    (src / "index.html").write_text(
        '<html><head><link rel="stylesheet" href="../outside.css"></head>'
        "<body></body></html>",
        encoding="utf-8",
    )
    with pytest.raises(builder.BuildError, match="escapes src/"):
        builder.build_game(src)


def test_build_game_raises_on_circular_include(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "index.html").write_text(
        '<html><head><script src="index.html"></script></head>'
        "<body></body></html>",
        encoding="utf-8",
    )
    with pytest.raises(builder.BuildError, match="circular include"):
        builder.build_game(src)


def test_build_game_raises_on_missing_index(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    with pytest.raises(builder.BuildError, match="missing src/index.html"):
        builder.build_game(src)


# --- is_multi_file -----------------------------------------------------------


def test_is_multi_file_true_for_fixture():
    assert builder.is_multi_file(FIXTURE_DIR) is True


def test_is_multi_file_false_for_single_file_game(tmp_path):
    game_dir = tmp_path / "some-game"
    game_dir.mkdir()
    (game_dir / "index.html").write_text("<html></html>", encoding="utf-8")
    (game_dir / "meta.json").write_text(json.dumps({"title": "x"}), encoding="utf-8")
    assert builder.is_multi_file(game_dir) is False


def test_is_multi_file_respects_explicit_single_file_format_over_src_dir(tmp_path):
    # Defensive: an explicit "single-file" in meta.json wins even if a
    # (stale) src/ directory happens to be sitting next to it.
    game_dir = tmp_path / "some-game"
    (game_dir / "src").mkdir(parents=True)
    (game_dir / "src" / "index.html").write_text("<html></html>", encoding="utf-8")
    (game_dir / "meta.json").write_text(
        json.dumps({"title": "x", "format": "single-file"}), encoding="utf-8"
    )
    assert builder.is_multi_file(game_dir) is False


# --- write_built_index -------------------------------------------------------


def test_write_built_index_writes_inlined_html(tmp_path):
    game_dir = tmp_path / "game"
    src = game_dir / "src"
    src.mkdir(parents=True)
    (src / "index.html").write_text(
        '<html><head><link rel="stylesheet" href="s.css"></head>'
        '<body><script src="c.js"></script></body></html>',
        encoding="utf-8",
    )
    (src / "s.css").write_text("body{color:red}", encoding="utf-8")
    (src / "c.js").write_text("console.log('hi')", encoding="utf-8")

    index_path = builder.write_built_index(game_dir)

    assert index_path == game_dir / "index.html"
    written = index_path.read_text(encoding="utf-8")
    assert "<style>body{color:red}</style>" in written
    assert "<script>console.log('hi')</script>" in written


# --- build_and_verify ---------------------------------------------------------


def test_build_and_verify_multi_file_builds_scans_and_smokes(tmp_path):
    game_dir = tmp_path / "game"
    src = game_dir / "src"
    src.mkdir(parents=True)
    (src / "index.html").write_text(
        '<html><head><link rel="stylesheet" href="s.css"></head>'
        '<body><script src="c.js"></script></body></html>',
        encoding="utf-8",
    )
    (src / "s.css").write_text("body{color:red}", encoding="utf-8")
    (src / "c.js").write_text("console.log('hi')", encoding="utf-8")
    (game_dir / "meta.json").write_text(
        json.dumps({"title": "x", "format": "multi-file"}), encoding="utf-8"
    )

    passed, detail, built_html = builder.build_and_verify(game_dir)

    assert passed, detail
    assert "<style>body{color:red}</style>" in built_html
    assert (game_dir / "index.html").is_file()


def test_build_and_verify_flags_unsafe_built_html(tmp_path):
    game_dir = tmp_path / "game"
    src = game_dir / "src"
    src.mkdir(parents=True)
    (src / "index.html").write_text(
        '<html><body><script src="c.js"></script></body></html>',
        encoding="utf-8",
    )
    (src / "c.js").write_text("eval('2+2')", encoding="utf-8")
    (game_dir / "meta.json").write_text(
        json.dumps({"title": "x", "format": "multi-file"}), encoding="utf-8"
    )

    passed, detail, built_html = builder.build_and_verify(game_dir)

    assert not passed
    assert "eval" in detail


def test_build_and_verify_single_file_is_passthrough(tmp_path):
    game_dir = tmp_path / "game"
    game_dir.mkdir()
    (game_dir / "index.html").write_text(
        "<html><body>hi</body></html>", encoding="utf-8"
    )
    (game_dir / "meta.json").write_text(
        json.dumps({"title": "x"}), encoding="utf-8"
    )

    passed, detail, built_html = builder.build_and_verify(game_dir)

    assert passed, detail
    assert built_html == "<html><body>hi</body></html>"


# --- the fixture itself, end to end ------------------------------------------


def test_fixture_builds_and_passes_safety_scan():
    html = builder.build_game(FIXTURE_DIR / "src")
    assert safety.scan(html) == []


def test_fixture_committed_index_matches_fresh_build():
    committed = (FIXTURE_DIR / "index.html").read_text(encoding="utf-8")
    fresh = builder.build_game(FIXTURE_DIR / "src")
    assert committed == fresh


def _has_chromium():
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch()
            browser.close()
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _has_chromium(), reason="Chromium not installed")
def test_fixture_passes_real_smoke_test():
    passed, detail = smoke_test.run_smoke_test(FIXTURE_DIR / "index.html")
    assert passed, detail
