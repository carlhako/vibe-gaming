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

The game is served over a throwaway 127.0.0.1 HTTP server rather than opened
as file://, for two reasons:

  - ES modules cannot load over file:// at all — Chrome blocks them (origin
    null, no CORS), so a 3D game's `import * as THREE from 'three'` would fail
    on every attempt no matter how correct the game was.
  - It lets the page be served under the real production CSP
    (safety.game_csp), so a game that would be broken by the CSP once served
    fails here, during generation, where the retry loop can still fix it —
    instead of shipping and breaking in the arcade.

The smoke page is top-level rather than inside the production sandbox iframe,
so 'self' resolves for it where it wouldn't in production. That only makes
this load more permissive than the real thing, never less, and safety.py
forbids the local refs it would otherwise let through.

`sync_playwright` is imported lazily inside run_smoke_test() rather than at
module level so importing game_web.smoke_test never requires a Chromium
install to succeed — only actually calling run_smoke_test() does.
"""

import contextlib
import mimetypes
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import safety

_VENDOR_ROOT = Path(__file__).resolve().parent / "vendor"

# Settle window after the synthetic interaction below. A 3D game has to fetch
# ~750KB of three.js and compile shaders on a software GPU before it draws
# anything, which does not fit in the 2s that is plenty for a 2D canvas game.
_SETTLE_MS = 2000
_SETTLE_MS_3D = 5000


def _blocked_host(url: str, local_origin: str | None = None) -> str | None:
    """The disallowed host `url` points at, or None if it's same-page,
    inline, or allowlisted. Split out from the request handler below so the
    allowlist logic itself is unit-testable without spinning up a browser.

    `local_origin` is the smoke server's own origin, which is exempt — but by
    exact origin, not by host, so a game reaching for some *other* service on
    127.0.0.1 is still reported.
    """
    if url.startswith(("file://", "data:", "blob:")):
        return None
    if local_origin and url.startswith(local_origin):
        return None
    host = urlparse(url).hostname
    if host and host.lower() not in safety.ALLOWED_CDN_HOSTS:
        return host.lower()
    return None


class _SmokeHandler(BaseHTTPRequestHandler):
    """Serves exactly two things: the game at /, and the vendored engine tree
    under /vendor/. Everything else 404s, which mirrors production — only
    index.html is ever served out of a game directory."""

    protocol_version = "HTTP/1.1"

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler's interface
        self._serve(with_body=True)

    def do_HEAD(self):  # noqa: N802
        self._serve(with_body=False)

    def _serve(self, with_body: bool):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._send_file(self.server.game_path, "text/html; charset=utf-8",
                            with_body, extra={"Content-Security-Policy": self.server.csp})
            return
        if path.startswith("/vendor/"):
            target = (_VENDOR_ROOT / path[len("/vendor/"):]).resolve()
            try:
                target.relative_to(_VENDOR_ROOT.resolve())
            except ValueError:
                self.send_error(404)
                return
            if not target.is_file():
                self.send_error(404)
                return
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            # Module scripts always fetch with CORS, and a sandboxed game sends
            # Origin: null, so the real /vendor route has to allow any origin.
            # Mirror that here or the smoke test would be testing a laxer setup
            # than production.
            self._send_file(target, ctype, with_body,
                            extra={"Access-Control-Allow-Origin": "*"})
            return
        self.send_error(404)

    def _send_file(self, path: Path, ctype: str, with_body: bool, extra: dict):
        try:
            body = Path(path).read_bytes()
        except OSError:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for name, value in extra.items():
            self.send_header(name, value)
        self.end_headers()
        if with_body:
            self.wfile.write(body)

    def log_message(self, *args):
        """Silence the default stderr access log — a generation job's output is
        the attempt record, not an HTTP log."""


class _SmokeServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


@contextlib.contextmanager
def _serve_game(html_path: Path):
    """Serve `html_path` at / on an ephemeral 127.0.0.1 port. Yields the origin."""
    server = _SmokeServer(("127.0.0.1", 0), _SmokeHandler)
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    server.game_path = html_path
    server.csp = safety.game_csp(origin)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield origin
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def run_smoke_test(html_path, timeout_seconds: int = 20,
                   engine: str | None = None) -> tuple[bool, str]:
    """Load html_path headless and watch for JS errors and disallowed
    network egress.

    Returns (passed, detail): detail is either a human-readable summary of
    the errors seen (failure) or a short confirmation string (success).
    """
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    html_path = Path(html_path).resolve()
    errors: list[str] = []
    settle_ms = _SETTLE_MS_3D if engine else _SETTLE_MS

    try:
        with _serve_game(html_path) as origin:

            def on_pageerror(exc):
                errors.append(f"pageerror: {exc}")

            def on_console(msg):
                if msg.type == "error":
                    errors.append(f"console.error: {msg.text}")

            def on_request(req):
                host = _blocked_host(req.url, origin)
                if host:
                    errors.append(
                        f"blocked network request to disallowed host '{host}' ({req.url})")

            with sync_playwright() as p:
                # Headless Chromium has no GPU, so WebGL falls back to
                # SwiftShader; without this flag Chrome refuses the software
                # fallback and every 3D game fails to get a context.
                browser = p.chromium.launch(args=["--enable-unsafe-swiftshader"])
                try:
                    page = browser.new_page()
                    page.on("pageerror", on_pageerror)
                    page.on("console", on_console)
                    page.on("request", on_request)
                    page.goto(f"{origin}/", timeout=timeout_seconds * 1000)
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
                    page.wait_for_timeout(settle_ms)
                finally:
                    browser.close()
    except PlaywrightError as exc:
        return False, f"smoke test failed to load page: {exc}"
    except Exception as exc:
        return False, f"smoke test crashed: {exc}"

    if errors:
        return False, "; ".join(errors[:10])
    return True, "no console/page errors during headless load"
