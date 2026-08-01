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


def test_build_and_verify_reports_failure_instead_of_crashing_when_nothing_written_yet(tmp_path):
    """Sprint 5's explode pass (agent.explode_game) can stage a brand-new,
    completely empty fork directory and have the model call finish() before
    any write_file has landed — neither src/index.html nor a legacy
    index.html exists yet. This must come back as a normal failed
    verification, not an unhandled FileNotFoundError."""
    game_dir = tmp_path / "game"
    game_dir.mkdir()

    passed, detail, built_html = builder.build_and_verify(game_dir)

    assert not passed
    assert "index.html" in detail
    assert built_html == ""


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


# --- 3D games: one merged module instead of sibling classic scripts --------

def _write_3d_src(tmp_path, index_html, **modules):
    game_dir = tmp_path / "game"
    src = game_dir / "src"
    src.mkdir(parents=True)
    (game_dir / "meta.json").write_text(json.dumps(
        {"format": "multi-file", "engine": "three", "engine_version": "0.185.1"}))
    (src / "index.html").write_text(index_html)
    for name, body in modules.items():
        (src / name.replace("__", ".")).write_text(body)
    return game_dir


def test_merges_every_local_script_into_one_module(tmp_path):
    game_dir = _write_3d_src(
        tmp_path,
        '<html><head><title>t</title></head><body>'
        '<script src="a.js"></script><script src="b.js"></script>'
        '<script src="c.js"></script></body></html>',
        a__js="const A = 1;", b__js="const B = 2;", c__js="const C = 3;",
    )
    out = builder.build_game(game_dir / "src", "three")
    assert out.count('<script type="module">') == 1
    assert "<script>" not in out
    # Document order preserved, header first.
    assert out.index("import * as THREE") < out.index("const A") < out.index("const B") < out.index("const C")


def test_merged_module_lands_where_the_first_script_was(tmp_path):
    game_dir = _write_3d_src(
        tmp_path,
        '<html><head></head><body><div id="hud"></div>'
        '<script src="a.js"></script><footer>f</footer>'
        '<script src="b.js"></script></body></html>',
        a__js="const A = 1;", b__js="const B = 2;",
    )
    out = builder.build_game(game_dir / "src", "three")
    assert out.index("hud") < out.index('<script type="module">') < out.index("<footer>")


def test_stylesheets_still_inline_separately_in_3d(tmp_path):
    game_dir = _write_3d_src(
        tmp_path,
        '<html><head><link rel="stylesheet" href="style.css"></head><body>'
        '<script src="a.js"></script></body></html>',
        style__css="body{margin:0}", a__js="const A = 1;",
    )
    out = builder.build_game(game_dir / "src", "three")
    assert "<style>body{margin:0}</style>" in out


def test_cdn_refs_are_still_left_alone_in_3d(tmp_path):
    game_dir = _write_3d_src(
        tmp_path,
        '<html><head><link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=X">'
        '</head><body><script src="a.js"></script></body></html>',
        a__js="const A = 1;",
    )
    out = builder.build_game(game_dir / "src", "three")
    assert "https://fonts.googleapis.com/css2?family=X" in out


def test_module_carrying_its_own_import_is_rejected(tmp_path):
    game_dir = _write_3d_src(
        tmp_path,
        '<html><head></head><body><script src="a.js"></script></body></html>',
        a__js="import * as THREE from 'three';\nconst A = 1;",
    )
    with pytest.raises(builder.BuildError) as exc:
        builder.build_game(game_dir / "src", "three")
    assert "a.js line 1" in str(exc.value)
    assert "import" in str(exc.value)


def test_module_carrying_an_export_is_rejected(tmp_path):
    game_dir = _write_3d_src(
        tmp_path,
        '<html><head></head><body><script src="a.js"></script></body></html>',
        a__js="const A = 1;\nexport { A };",
    )
    with pytest.raises(builder.BuildError):
        builder.build_game(game_dir / "src", "three")


def test_dynamic_import_and_the_word_import_do_not_trip_the_gate(tmp_path):
    game_dir = _write_3d_src(
        tmp_path,
        '<html><head></head><body><script src="a.js"></script></body></html>',
        a__js="// we import nothing\nconst load = () => import('./x.js');\nconst s = 'export this';",
    )
    assert '<script type="module">' in builder.build_game(game_dir / "src", "three")


def test_2d_build_is_unchanged_by_the_3d_code_path(tmp_path):
    game_dir = _write_3d_src(
        tmp_path,
        '<html><head></head><body><script src="a.js"></script>'
        '<script src="b.js"></script></body></html>',
        a__js="const A = 1;", b__js="const B = 2;",
    )
    out = builder.build_game(game_dir / "src")
    assert out.count("<script>") == 2
    assert 'type="module"' not in out


# --- engine resolution: explicit override beats (absent) meta.json ---------

def test_read_engine_reads_meta_json(tmp_path):
    game_dir = _write_3d_src(
        tmp_path, "<html><head></head><body></body></html>")
    assert builder.read_engine(game_dir) == ("three", "0.185.1")


def test_read_engine_defaults_to_2d_without_meta(tmp_path):
    (tmp_path / "bare").mkdir()
    assert builder.read_engine(tmp_path / "bare")[0] is None


def test_build_and_verify_honours_the_engine_override_when_meta_is_absent(tmp_path):
    """A fork in progress has no meta.json — _stage_fork doesn't copy one and
    explode writes one only on success — so without the override a 3D game
    would verify as 2D for the whole run."""
    game_dir = tmp_path / "fork"
    src = game_dir / "src"
    src.mkdir(parents=True)
    (src / "index.html").write_text(
        '<html><head><title>t</title></head><body><script src="a.js"></script></body></html>')
    (src / "a.js").write_text("const A = 1;")

    built = builder.build_game(src, "three")
    assert '<script type="module">' in built
    # …and with no engine at all it would have been a classic script.
    assert "<script>" in builder.build_game(src)


@pytest.mark.skipif(not _has_chromium(), reason="Chromium not installed")
def test_split_3d_game_builds_and_verifies_end_to_end(tmp_path):
    """The multi-file 3D story in one test: modules split across files still
    share one scope (main.js calls a function and reads consts declared in
    scene.js), the build merges them into one module, and the result renders."""
    game_dir = _write_3d_src(
        tmp_path,
        '<!DOCTYPE html><html><head><meta charset="utf-8"><title>split</title>'
        '<link rel="stylesheet" href="style.css"></head><body>'
        '<script src="scene.js"></script><script src="main.js"></script>'
        "</body></html>",
        style__css="body{margin:0;background:#111}",
        scene__js=(
            "const renderer = new THREE.WebGLRenderer();\n"
            "renderer.setSize(200, 200);\n"
            "document.body.appendChild(renderer.domElement);\n"
            "const scene = new THREE.Scene();\n"
            "const camera = new THREE.PerspectiveCamera(70, 1, 0.1, 100);\n"
            "camera.position.z = 4;\n"
            "const controls = new OrbitControls(camera, renderer.domElement);\n"
            "function makePlayer() {\n"
            "  return new THREE.Mesh(new THREE.IcosahedronGeometry(1),\n"
            "                        new THREE.MeshBasicMaterial({color: 0x66ccff}));\n"
            "}\n"
        ),
        main__js=(
            "const player = makePlayer();\n"
            "scene.add(player);\n"
            "controls.update();\n"
            "renderer.render(scene, camera);\n"
        ),
    )
    passed, detail, built = builder.build_and_verify(game_dir)
    assert passed, detail
    assert built.count('<script type="module">') == 1
    assert 'type="importmap"' in (game_dir / "index.html").read_text()
