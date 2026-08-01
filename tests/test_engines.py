"""engines.py — the canonical shape of a 3D (three.js) game's HTML.

The property that matters most here is idempotency: enhancing a single-file
game resubmits the stored HTML, so the model sees the injected import map and
echoes it back. If normalize() were not exactly reversible, every enhance would
either duplicate the map or grow the file, forever.
"""

import pytest

import engines

HEAD_DOC = "<html><head><title>t</title></head><body></body></html>"


# --- normalize(): injection, idempotency, passthrough ----------------------

def test_injects_canonical_importmap_after_head():
    out = engines.normalize(HEAD_DOC, "three")
    assert engines.importmap_html(engines.DEFAULT_THREE_VERSION) in out
    assert out.index("<head>") < out.index("importmap") < out.index("<title>")


def test_normalize_is_idempotent():
    once = engines.normalize(HEAD_DOC, "three")
    assert engines.normalize(once, "three") == once
    assert engines.normalize(engines.normalize(once, "three"), "three") == once


def test_replaces_a_model_written_importmap_rather_than_appending():
    hand_written = (
        '<html><head><script type="importmap">'
        '{"imports":{"three":"https://cdn.jsdelivr.net/npm/three/build/three.module.js"}}'
        "</script></head><body></body></html>"
    )
    out = engines.normalize(hand_written, "three")
    assert "cdn.jsdelivr.net" not in out
    assert out.count('type="importmap"') == 1
    assert engines.importmap_html(engines.DEFAULT_THREE_VERSION) in out


def test_strips_several_importmaps_leaving_exactly_one():
    doc = (
        '<html><head><script type="importmap">{"imports":{}}</script>'
        '<script type="importmap">{"imports":{"a":"b"}}</script>'
        "</head><body></body></html>"
    )
    assert engines.normalize(doc, "three").count('type="importmap"') == 1


def test_2d_game_is_untouched():
    assert engines.normalize(HEAD_DOC, None) == HEAD_DOC
    assert engines.normalize(HEAD_DOC, "") == HEAD_DOC


def test_importmap_maps_both_bare_specifiers():
    imap = engines.importmap_html("0.185.1")
    assert '"three":"/vendor/three/0.185.1/three.module.min.js"' in imap
    # The trailing slash is what makes `three/addons/<path>` resolve as a prefix.
    assert '"three/addons/":"/vendor/three/0.185.1/addons/"' in imap


# --- normalize(): the three EngineError paths ------------------------------

def test_missing_head_is_rejected_with_an_actionable_message():
    with pytest.raises(engines.EngineError) as exc:
        engines.normalize("<html><body>no head here</body></html>", "three")
    assert "<head>" in str(exc.value)


def test_unvendored_version_is_rejected_and_names_what_is_available():
    with pytest.raises(engines.EngineError) as exc:
        engines.normalize(HEAD_DOC, "three", "0.1.2")
    assert "0.1.2" in str(exc.value)
    assert engines.DEFAULT_THREE_VERSION in str(exc.value)


def test_unknown_engine_is_rejected():
    with pytest.raises(engines.EngineError):
        engines.normalize(HEAD_DOC, "unreal")


# --- the vendored tree the rest of this depends on -------------------------

def test_default_version_is_actually_vendored():
    """A code default pointing at a tree nobody vendored would fail every 3D
    generation at normalize() time, on a fresh clone, with no other signal."""
    assert engines.DEFAULT_THREE_VERSION in engines.available_versions()
    vendored = engines.vendor_dir(engines.DEFAULT_THREE_VERSION)
    assert (vendored / "three.module.min.js").is_file()
    # three.module.min.js imports this by relative path; without it the engine
    # half-loads and every 3D game dies on a bare-specifier resolution error.
    assert (vendored / "three.core.min.js").is_file()
    # Both controls addons the prompts and the build header name must exist,
    # or a game that imports one 404s at runtime with no other signal.
    assert (vendored / "addons" / "controls" / "OrbitControls.js").is_file()
    assert (vendored / "addons" / "controls" / "PointerLockControls.js").is_file()


def test_from_meta_reads_engine_and_version():
    assert engines.from_meta({"engine": "three", "engine_version": "0.185.1"}) == (
        "three", "0.185.1")
    assert engines.from_meta({}) == (None, engines.DEFAULT_THREE_VERSION)
    assert engines.from_meta({"engine": "bogus"}) == (None, engines.DEFAULT_THREE_VERSION)


def test_three_contract_variants_differ_on_who_writes_the_imports():
    single = engines.three_contract()
    multi = engines.three_contract(multi_file=True)
    assert "<script type=\"module\">" in single
    assert "never declare, import or export them" in multi
    # Both must forbid the things the gates actually reject.
    for text in (single, multi):
        assert "addEventListener" in text
        assert "data: URI" in text
