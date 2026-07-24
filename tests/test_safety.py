"""Sprint 2 security hardening: safety.scan() coverage for form action,
CSS url(), meta-refresh, and script navigation — plus regression coverage
for every pre-existing banned pattern."""

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
