"""Sprint 2 security hardening: safety.scan() coverage for form action,
CSS url(), meta-refresh, and script navigation — plus regression coverage
for every pre-existing banned pattern."""

import engines
import safety


def _flagged(html, needle):
    violations = safety.scan(html)
    return any(needle in v for v in violations)


# --- Part A: form action ---------------------------------------------------

def test_flags_form_with_external_action():
    html = '<form action="https://evil.tld/x"><input></form>'
    assert _flagged(html, "form with external action")


def test_does_not_flag_form_with_hash_action():
    html = '<form action="#"><input></form>'
    assert safety.scan(html) == []


def test_does_not_flag_form_with_no_action():
    html = "<form><input></form>"
    assert safety.scan(html) == []


# --- Part B: CSS url() -------------------------------------------------

def test_flags_inline_style_url_to_disallowed_host():
    html = '<div style="background:url(\'https://evil.tld/x.png\')"></div>'
    assert _flagged(html, "external resource from disallowed host")


def test_flags_style_block_url_to_disallowed_host():
    html = "<style>body{background:url(https://evil.tld/x.png)}</style>"
    assert _flagged(html, "external resource from disallowed host")


def test_does_not_flag_data_uri_css_url():
    html = "<style>body{background:url(data:image/png;base64,AAAA)}</style>"
    assert safety.scan(html) == []


def test_does_not_flag_css_url_to_allowed_cdn_host():
    html = "<style>body{background:url('https://cdn.jsdelivr.net/npm/foo/bar.png')}</style>"
    assert safety.scan(html) == []


# --- Part C: meta-refresh + script navigation -------------------------

def test_flags_meta_refresh():
    html = '<meta http-equiv="refresh" content="0;url=https://evil.tld">'
    assert _flagged(html, "meta refresh redirect")


def test_flags_location_href_assignment():
    assert _flagged("location.href = 'https://evil.tld';", "script-based page navigation")


def test_flags_window_location_assignment():
    assert _flagged("window.location = 'https://evil.tld';", "script-based page navigation")


def test_flags_location_replace():
    assert _flagged("location.replace('https://evil.tld');", "script-based page navigation")


def test_does_not_flag_location_search_read():
    assert safety.scan("const q = location.search;") == []


def test_does_not_flag_location_hash_read():
    assert safety.scan("if (location.hash === '#level2') {}") == []


# --- Regression: pre-existing banned patterns --------------------------

def test_flags_eval():
    assert _flagged("eval('2+2')", "eval()")


def test_flags_new_function():
    assert _flagged("new Function('return 1')", "Function constructor")


def test_flags_document_cookie():
    assert _flagged("document.cookie", "document.cookie")


def test_flags_local_storage():
    assert _flagged("localStorage.setItem('a', 'b')", "localStorage")


def test_flags_window_parent():
    assert _flagged("window.parent.postMessage('x')", "window.parent")


def test_flags_window_top():
    assert _flagged("window.top.location", "window.top")


def test_flags_javascript_url():
    assert _flagged('<a href="javascript:alert(1)">x</a>', "javascript: URL")


def test_flags_off_allowlist_script_src():
    html = '<script src="https://evil.tld/x.js"></script>'
    assert _flagged(html, "external resource from disallowed host")


def test_does_not_flag_allowlisted_script_src():
    html = '<script src="https://cdn.jsdelivr.net/npm/foo/bar.js"></script>'
    assert safety.scan(html) == []


# --- import maps: the class of ref the src=/href= checks cannot see ---------

def _canonical_map():
    return engines.importmap_html(engines.DEFAULT_THREE_VERSION)


def test_flags_importmap_in_a_non_engine_game():
    """Four pre-existing games pulled three.js through an import map and were
    never seen by this scanner: the URLs are JSON values, not attributes."""
    assert _flagged(_canonical_map(), "import map")


def test_accepts_the_canonical_importmap_for_a_3d_game():
    html = "<html><head>" + _canonical_map() + "</head><body></body></html>"
    assert safety.scan(html, "three", engines.DEFAULT_THREE_VERSION) == []


def test_flags_a_tampered_importmap_for_a_3d_game():
    tampered = _canonical_map().replace(
        "/vendor/three/", "https://evil.tld/vendor/three/")
    violations = safety.scan(tampered, "three", engines.DEFAULT_THREE_VERSION)
    assert any("non-canonical import map" in v for v in violations)


def test_flags_importmap_pinning_a_version_other_than_the_games_own():
    other = engines.importmap_html("0.0.1")
    violations = safety.scan(other, "three", engines.DEFAULT_THREE_VERSION)
    assert any("non-canonical import map" in v for v in violations)


# --- local refs: nothing but index.html is served from a game directory ----

def test_flags_local_script_ref():
    assert _flagged('<script src="core.js"></script>', "local script reference")


def test_flags_local_stylesheet_ref_regardless_of_attribute_order():
    assert _flagged('<link href="game.css" rel="stylesheet">', "local stylesheet reference")


def test_allows_vendor_ref_in_a_3d_game():
    html = f'<script src="/vendor/three/{engines.DEFAULT_THREE_VERSION}/three.module.min.js"></script>'
    assert safety.scan(html, "three", engines.DEFAULT_THREE_VERSION) == []


def test_flags_vendor_ref_in_a_2d_game():
    html = f'<script src="/vendor/three/{engines.DEFAULT_THREE_VERSION}/three.module.min.js"></script>'
    assert _flagged(html, "local script reference")


def test_does_not_flag_fragment_or_image_refs():
    assert safety.scan('<a href="#restart">r</a><img src="sprite.png">') == []


# --- a 3D game's engine is served here, not fetched from a CDN -------------

def test_flags_cdn_three_script_in_a_3d_game_even_though_the_host_is_allowlisted():
    """The generic CDN allowance in the prompts is enough to talk a model into
    a jsdelivr <script> tag; every other check waves it through."""
    html = '<script src="https://cdn.jsdelivr.net/npm/three@0.185.1/build/three.module.min.js"></script>'
    violations = safety.scan(html, "three", engines.DEFAULT_THREE_VERSION)
    assert any("3D game loads an external script" in v for v in violations)


def test_still_allows_cdn_script_in_a_2d_game():
    html = '<script src="https://cdn.jsdelivr.net/npm/foo/bar.js"></script>'
    assert safety.scan(html) == []


# --- game_csp ---------------------------------------------------------------

def test_game_csp_scopes_the_vendor_allowance_to_a_path_prefix():
    csp = safety.game_csp("https://arcade.example")
    assert "https://arcade.example/vendor/three/" in csp
    # Not a blanket allowance for the whole origin.
    assert "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://arcade.example;" not in csp


def test_game_csp_still_blocks_runtime_egress():
    csp = safety.game_csp("https://arcade.example")
    assert "connect-src 'self'" in csp
    assert "form-action 'none'" in csp
