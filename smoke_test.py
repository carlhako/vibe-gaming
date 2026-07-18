"""
game_web/smoke_test.py — Playwright headless smoke test for generated games.

The JS analogue of plugin_generator.run_plugin_tests(): loads the generated
index.html in headless Chromium and fails the attempt if the page throws an
uncaught exception or logs a console error within `timeout_seconds`. This is
weaker than a real test suite — it can't assert on gameplay behavior — but it
catches the failure mode a static regex scan (safety.py) can't: real runtime
bugs (reference errors, broken renders, syntax errors) that only surface when
the page actually executes.

`sync_playwright` is imported lazily inside run_smoke_test() rather than at
module level so importing game_web.smoke_test never requires a Chromium
install to succeed — only actually calling run_smoke_test() does.
"""

from pathlib import Path


def run_smoke_test(html_path, timeout_seconds: int = 20) -> tuple[bool, str]:
    """Load html_path headless and watch for JS errors.

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

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page()
                page.on("pageerror", on_pageerror)
                page.on("console", on_console)
                page.goto(f"file://{html_path}", timeout=timeout_seconds * 1000)
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
