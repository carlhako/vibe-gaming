"""
game_web/smoke_test.py — Playwright headless smoke test for generated games.

The JS analogue of plugin_generator.run_plugin_tests(): loads the generated
index.html in headless Chromium and fails the attempt if the page throws an
uncaught exception, logs a console error, or makes a network request to a
host outside safety.ALLOWED_CDN_HOSTS, within `timeout_seconds`. This is
weaker than a real test suite — it can't assert on gameplay behavior — but it
catches failure modes a static regex scan (safety.py) can't: real runtime
bugs (reference errors, broken renders, syntax errors) that only surface when
the page actually executes, and runtime-constructed URLs (atob(...), string
concatenation) that never appear as a literal string in the HTML source for
safety.py to match.

`sync_playwright` is imported lazily inside run_smoke_test() rather than at
module level so importing game_web.smoke_test never requires a Chromium
install to succeed — only actually calling run_smoke_test() does.
"""

from pathlib import Path
from urllib.parse import urlparse

import safety


def _blocked_host(url: str) -> str | None:
    """The disallowed host `url` points at, or None if it's same-page,
    inline, or allowlisted. Split out from the request handler below so the
    allowlist logic itself is unit-testable without spinning up a browser."""
    if url.startswith(("file://", "data:", "blob:")):
        return None
    host = urlparse(url).hostname
    if host and host.lower() not in safety.ALLOWED_CDN_HOSTS:
        return host.lower()
    return None


def run_smoke_test(html_path, timeout_seconds: int = 20) -> tuple[bool, str]:
    """Load html_path headless and watch for JS errors and disallowed
    network egress.

    Returns (passed, detail): detail is either a human-readable summary of
    the errors seen (failure) or a short confirmation string (success).
    """
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    html_path = Path(html_path).resolve()
    errors: list[str] = []

    def on_pageerror(exc):
        errors.append(f"pageerror: {exc}")

    def on_console(msg):
        if msg.type == "error":
            errors.append(f"console.error: {msg.text}")

    def on_request(req):
        host = _blocked_host(req.url)
        if host:
            errors.append(f"blocked network request to disallowed host '{host}' ({req.url})")

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page()
                page.on("pageerror", on_pageerror)
                page.on("console", on_console)
                page.on("request", on_request)
                page.goto(f"file://{html_path}", timeout=timeout_seconds * 1000)
                # Malicious navigation/exfiltration code is often gated behind
                # a user action ("on win, redirect to bonus site") rather than
                # firing on load, so exercise the page's input handlers before
                # the wait below — a pure load-and-wait test would never
                # trigger it. Arbitrary but common game inputs; this doesn't
                # need to "win" the game, just wake up its event listeners.
                try:
                    viewport = page.viewport_size or {"width": 1280, "height": 720}
                    page.mouse.click(viewport["width"] / 2, viewport["height"] / 2)
                    page.keyboard.press("Space")
                except PlaywrightError:
                    pass
                page.wait_for_timeout(2000)
            finally:
                browser.close()
    except PlaywrightError as exc:
        return False, f"smoke test failed to load page: {exc}"
    except Exception as exc:
        return False, f"smoke test crashed: {exc}"

    if errors:
        return False, "; ".join(errors[:10])
    return True, "no console/page errors during headless load"
