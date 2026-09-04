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

import base64
import contextlib
import hashlib
import json
import mimetypes
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import safety

_VENDOR_ROOT = Path(__file__).resolve().parent / "vendor"

# RFC 6455 handshake GUID.
_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def _ws_text_frame(payload: str) -> bytes:
    """A single unmasked server->client text frame (FIN set)."""
    data = payload.encode("utf-8")
    header = bytearray([0x81])
    n = len(data)
    if n < 126:
        header.append(n)
    elif n < 65536:
        header.append(126)
        header += n.to_bytes(2, "big")
    else:
        header.append(127)
        header += n.to_bytes(8, "big")
    return bytes(header) + data


# An unmasked, empty server->client close frame. The stub no longer sends one
# (it holds the socket open, mirroring rt_hub.py) — kept for tests that
# reconstruct the pre-fix "server closes immediately" failure mode.
_WS_CLOSE_FRAME = bytes([0x88, 0x00])

# Mirrors of rt_hub.DEFAULTS' two message-rate tiers. Duplicated rather than
# imported because importing rt_hub calls logging.basicConfig at module scope,
# which would reconfigure the root logger of whatever process runs a smoke
# test. tests/test_smoke_test.py asserts the two stay in step.
HUB_SOFT_BUDGET = 60
HUB_BURST_CEILING = 240

# A rate only counts as "sustained" if the game held it for this long. A game
# is not failed for a momentary burst — the hub would merely shed those frames.
SUSTAIN_WINDOW_S = 2.0

# The stub never keeps a frame's bytes; this only bounds what it will read
# before giving up on a frame header it cannot trust.
_MAX_STUB_FRAME_BYTES = 1 << 20


def _ws_read_frame(rfile):
    """Read one client->server frame header and skip its payload.

    Returns its opcode, or None at EOF / on a frame this stub will not read.
    The payload is read and discarded, never inspected — the stub counts
    frames, exactly as the real hub relays them, without looking inside.
    """
    header = rfile.read(2)
    if len(header) < 2:
        return None
    opcode = header[0] & 0x0F
    masked = bool(header[1] & 0x80)
    length = header[1] & 0x7F
    if length == 126:
        ext = rfile.read(2)
        if len(ext) < 2:
            return None
        length = int.from_bytes(ext, "big")
    elif length == 127:
        ext = rfile.read(8)
        if len(ext) < 8:
            return None
        length = int.from_bytes(ext, "big")
    if masked and len(rfile.read(4)) < 4:
        return None
    if length > _MAX_STUB_FRAME_BYTES:
        return None
    if length and len(rfile.read(length)) < length:
        return None
    return opcode


def _sustained_rate(times, window_s: float = SUSTAIN_WINDOW_S) -> float | None:
    """The highest per-second rate held for a full `window_s`, or None when the
    run is too short for any rate to count as sustained.

    Split out from the stub so the judgment itself is unit-testable without a
    browser or a socket.
    """
    times = sorted(times)
    if len(times) < 2 or times[-1] - times[0] < window_s:
        return None
    best = 0.0
    j = 0
    for i, start in enumerate(times):
        end = start + window_s
        if end > times[-1]:
            break
        if j < i:
            j = i
        while j < len(times) and times[j] <= end:
            j += 1
        best = max(best, (j - i) / window_s)
    return best


def _rate_failure(times, soft: int = HUB_SOFT_BUDGET,
                  hard: int = HUB_BURST_CEILING,
                  window_s: float = SUSTAIN_WINDOW_S) -> str | None:
    """The failure detail for a game that floods, or None.

    The threshold is the hub's *hard* ceiling, not its soft budget: over the
    soft budget the hub merely sheds frames and the game stays playable, and
    failing generation for lossiness would reject playable games. Over the hard
    ceiling the hub closes the socket, which is a broken game.
    """
    rate = _sustained_rate(times, window_s)
    if rate is None or rate <= hard:
        return None
    return (
        f"multiplayer send rate too high: {rate:.0f} messages/sec sustained over "
        f"{window_s:g}s, above the relay's hard ceiling of {hard}/sec (it closes "
        f"the connection above that, and sheds messages above {soft}/sec). Do not "
        "call VG_RT.send() every animation frame — use VG_RT.sendState(value) for "
        "continuously changing state, which coalesces to a safe rate, and keep "
        "VG_RT.send() for occasional discrete events."
    )


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
    127.0.0.1 is still reported. The exemption also covers the `ws://` form of
    that origin: a multiplayer game opens a WebSocket to the serving origin's
    `/rt/` path (production CSP allows exactly that), and the smoke server
    answers it with a minimal stub — but a `ws://`/`wss://` URL to any *other*
    host still fails the attempt.
    """
    if url.startswith(("file://", "data:", "blob:")):
        return None
    if local_origin and url.startswith(local_origin):
        return None
    if local_origin and url.startswith(safety._ws_origin(local_origin)):
        return None
    host = urlparse(url).hostname
    if host and host.lower() not in safety.ALLOWED_CDN_HOSTS:
        return host.lower()
    return None


class _SmokeHandler(BaseHTTPRequestHandler):
    """Serves the game at /, the vendored engine tree under /vendor/, and a
    minimal WebSocket stub for a multiplayer game's `/rt/<game_id>` connection
    (handshake + `welcome` + empty `roster`, then held open). Everything else 404s,
    which mirrors production — only index.html is ever served out of a game
    directory."""

    protocol_version = "HTTP/1.1"

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler's interface
        self._serve(with_body=True)

    def do_HEAD(self):  # noqa: N802
        self._serve(with_body=False)

    def _serve(self, with_body: bool):
        path = urlparse(self.path).path
        if (self.headers.get("Upgrade", "").lower() == "websocket"
                and path.startswith("/rt/")):
            self._serve_ws_stub()
            return
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

    def _serve_ws_stub(self):
        """Minimal realtime-hub stand-in: complete the WebSocket handshake,
        send `welcome` + an empty `roster`, then hold the connection open —
        without a server-initiated close — until the browser closes the socket
        or the smoke server is torn down. Enough for a multiplayer game to
        reach its solo/waiting state without the smoke run failing merely for
        opening the socket — anything more (ping, msg relay) is out of scope
        for a single-client smoke test.

        The stub deliberately does NOT send a close frame: an immediate close
        pushes the injected VG_RT client into its reconnect-with-backoff loop
        for the whole settle window, and each reopened socket is closed again,
        multiplying any send-during-closing console errors and making the
        result non-deterministic. A held-open socket also matches how the real
        rt_hub.py behaves.
        """
        key = self.headers.get("Sec-WebSocket-Key")
        if not key:
            self.send_error(400)
            return
        accept = base64.b64encode(
            hashlib.sha1((key + _WS_GUID).encode("ascii")).digest()
        ).decode("ascii")
        self.send_response(101)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.end_headers()
        try:
            self.wfile.write(_ws_text_frame(
                json.dumps({"t": "welcome", "id": "smoke", "max": 2})))
            self.wfile.write(_ws_text_frame(
                json.dumps({"t": "roster", "members": [
                    {"id": "smoke", "nick": "", "ping_ms": None}]})))
            self.wfile.flush()
            # Park the handler thread here, reading and discarding whatever
            # the client frames back (its `join`, etc.) — but timestamping each
            # data frame, so a game that floods the relay fails during
            # generation rather than in the arcade. Only the arrival time is
            # kept; the payload is skipped unread, as the real hub never
            # inspects one either. _ws_read_frame returns None at EOF when the
            # browser closes the socket, and read() raises OSError if the
            # server socket is torn down under it — either way the loop ends.
            # The handler thread is daemonic (`_SmokeServer.daemon_threads`),
            # so it can never delay `_serve_game()` teardown even if it is
            # still parked when shutdown() runs.
            while True:
                opcode = _ws_read_frame(self.rfile)
                if opcode is None or opcode == 0x8:   # EOF or client close
                    break
                if opcode in (0x1, 0x2):              # text / binary
                    self.server.ws_frame_times.append(time.monotonic())
        except OSError:
            pass

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
    """Serve `html_path` at / on an ephemeral 127.0.0.1 port.

    Yields `(origin, server)`; `server.ws_frame_times` accumulates the arrival
    time of every frame the game sent over the WebSocket stub.
    """
    server = _SmokeServer(("127.0.0.1", 0), _SmokeHandler)
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    server.game_path = html_path
    server.csp = safety.game_csp(origin)
    server.ws_frame_times = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield origin, server
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
        with _serve_game(html_path) as (origin, server):

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
            # After the browser is gone, so the frame list is no longer growing.
            rate_detail = _rate_failure(list(server.ws_frame_times))
            if rate_detail:
                errors.append(rate_detail)
    except PlaywrightError as exc:
        return False, f"smoke test failed to load page: {exc}"
    except Exception as exc:
        return False, f"smoke test crashed: {exc}"

    if errors:
        return False, "; ".join(errors[:10])
    return True, "no console/page errors during headless load"
