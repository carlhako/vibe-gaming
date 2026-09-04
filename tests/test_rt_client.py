"""vendor/rt/rt.js — the injected realtime client (VG_RT).

Covers openspec change fix-multiplayer-rate-limit-disconnects, tasks 2.1-2.7:
the `sendState` coalescing channel, the discrete `send` budget, the exposed
budget properties, presence reset on disconnect, and the settle-gated
reconnect backoff.

The client is exercised in real headless Chromium — it is a browser script and
its timers, `readyState` gating and `WebSocket` interaction are the behavior
under test — but against a fake `WebSocket` installed before rt.js loads, so
the tests drive open/close/message themselves and read every frame the client
puts on the wire. No hub, no network.
"""

import json
import shutil
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")

ROOT = Path(__file__).resolve().parent.parent
RT_JS = ROOT / "vendor" / "rt" / "rt.js"
GID = "a" * 32


def _has_chromium():
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            p.chromium.launch().close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _has_chromium(), reason="Chromium not installed")


# A fake WebSocket installed before rt.js runs, plus the handles the tests
# drive it with. `data-hub` keeps hubUrl() off `location` (which is file://).
HARNESS = """<!doctype html><html><head><meta charset="utf-8"><title>rt</title>
<script>
window.__sent = [];
window.__sockets = [];
function FakeWS(url) {
  this.url = url;
  this.readyState = 0;
  this.createdAt = performance.now();
  this.onopen = this.onclose = this.onmessage = this.onerror = null;
  window.__sockets.push(this);
}
FakeWS.CONNECTING = 0; FakeWS.OPEN = 1; FakeWS.CLOSING = 2; FakeWS.CLOSED = 3;
FakeWS.prototype.send = function (text) {
  window.__sent.push({ text: text, at: performance.now() });
};
FakeWS.prototype.close = function () { this.readyState = 3; };
window.WebSocket = FakeWS;

function last() { return window.__sockets[window.__sockets.length - 1]; }
window.__open = function () { var s = last(); s.readyState = 1; if (s.onopen) s.onopen({}); };
window.__close = function () { var s = last(); s.readyState = 3;
  window.__closedAt = performance.now();
  if (s.onclose) s.onclose({}); };
window.__deliver = function (o) { var s = last(); if (s.onmessage) s.onmessage({ data: JSON.stringify(o) }); };
window.__msgs = function () {
  return window.__sent.map(function (f) { return JSON.parse(f.text); })
    .filter(function (o) { return o.t === 'msg'; });
};
window.__frames = function () {
  return window.__sent.map(function (f) { return JSON.parse(f.text); });
};
window.__socketTimes = function () {
  return window.__sockets.map(function (s) { return s.createdAt; });
};
</script>
<script src="vendor/rt/rt.js" data-game-id="%s" data-hub="ws://127.0.0.1:1"></script>
</head><body><h1>rt harness</h1></body></html>
""" % GID


@pytest.fixture
def harness(tmp_path):
    """Yields a factory: open the harness page and return (page, errors)."""
    vendor = tmp_path / "vendor" / "rt"
    vendor.mkdir(parents=True)
    shutil.copy(RT_JS, vendor / "rt.js")
    (tmp_path / "index.html").write_text(HARNESS, encoding="utf-8")
    url = (tmp_path / "index.html").as_uri()

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
            page.on("console",
                    lambda m: errors.append(f"console.{m.type}: {m.text}")
                    if m.type == "error" else None)
            page.goto(url)
            page.wait_for_function("window.__sockets.length > 0")
            yield page, errors
        finally:
            browser.close()


def _connect(page):
    """Open the socket and deliver a welcome + a two-member roster."""
    page.evaluate("window.__open()")
    page.evaluate(
        "window.__deliver(%s)" % json.dumps({"t": "welcome", "id": "me1", "max": 2}))
    page.evaluate("window.__deliver(%s)" % json.dumps({
        "t": "roster",
        "members": [{"id": "me1", "nick": "", "ping_ms": None},
                    {"id": "peer", "nick": "p", "ping_ms": 12}],
    }))


# --------------------------------------------------------------------------
# 2.1 / 2.2  sendState: latest-wins coalescing
# --------------------------------------------------------------------------

def test_send_state_coalesces_per_frame_calls(harness):
    """60 calls/sec for two seconds must produce ~20 frames/sec, each carrying
    the value from the most recent call, not a queue of all 120."""
    page, errors = harness
    _connect(page)
    page.evaluate("""() => {
      window.__n = 0;
      window.__t = setInterval(() => { window.__n++; VG_RT.sendState({ n: window.__n }); },
                               1000 / 60);
    }""")
    page.wait_for_timeout(2000)
    page.evaluate("clearInterval(window.__t)")
    calls = page.evaluate("window.__n")
    msgs = page.evaluate("window.__msgs()")

    assert calls >= 100, f"harness only made {calls} calls"
    # ~40 over two seconds. A wide band: this asserts coalescing happened, not
    # a precise timer.
    assert 28 <= len(msgs) <= 52, f"{len(msgs)} frames for {calls} calls"
    values = [m["d"]["n"] for m in msgs]
    assert values == sorted(values) and len(set(values)) == len(values)
    # Each flush carried the latest value, so consecutive frames skip the
    # calls made in between — a queue would have emitted every single one.
    assert values[-1] - values[0] > len(values)
    assert not errors, errors


def test_send_state_emits_plain_msg_frame_with_no_envelope(harness):
    page, errors = harness
    _connect(page)
    page.evaluate("VG_RT.sendState({ x: 1, y: 2 })")
    page.wait_for_timeout(200)
    msgs = page.evaluate("window.__msgs()")
    assert len(msgs) == 1
    # Identical in shape to what send() puts on the wire: {t, d} and nothing else.
    assert msgs[0] == {"t": "msg", "d": {"x": 1, "y": 2}}
    page.evaluate("VG_RT.send({ x: 1, y: 2 })")
    page.wait_for_timeout(50)
    msgs = page.evaluate("window.__msgs()")
    assert msgs[1] == msgs[0]
    assert not errors, errors


def test_send_state_emits_nothing_on_an_idle_interval(harness):
    page, errors = harness
    _connect(page)
    page.evaluate("VG_RT.sendState(1)")
    page.wait_for_timeout(150)
    assert len(page.evaluate("window.__msgs()")) == 1
    # Several flush intervals with no new value: the timer keeps running and
    # sends nothing.
    page.wait_for_timeout(600)
    assert len(page.evaluate("window.__msgs()")) == 1
    assert not errors, errors


def test_send_state_timer_is_lazy_and_stops_on_close(harness):
    page, errors = harness
    _connect(page)
    # Nothing scheduled before the first call: no frames appear on their own.
    page.wait_for_timeout(200)
    assert page.evaluate("window.__msgs().length") == 0
    page.evaluate("VG_RT.sendState(1)")
    page.wait_for_timeout(150)
    assert page.evaluate("window.__msgs().length") == 1
    page.evaluate("window.__close()")
    # A value set after the close must not be transmitted by a surviving timer.
    page.evaluate("VG_RT.sendState(2)")
    page.wait_for_timeout(300)
    assert page.evaluate("window.__msgs().length") == 1
    assert not errors, errors


# --------------------------------------------------------------------------
# 2.3 / 2.4  discrete send budget and the exposed budgets
# --------------------------------------------------------------------------

def test_send_budget_drops_over_budget_calls_locally(harness):
    page, errors = harness
    _connect(page)
    result = page.evaluate("""() => {
      var budget = VG_RT.sendBudget, ok = 0, refused = 0;
      for (var i = 0; i < budget * 4; i++) {
        if (VG_RT.send({ i: i })) ok++; else refused++;
      }
      return { ok: ok, refused: refused, budget: budget, wire: window.__msgs().length };
    }""")
    assert result["ok"] == result["budget"]
    assert result["refused"] == result["budget"] * 3
    # Only the accepted calls reached the wire — the refusals were not relayed
    # for the hub to discard.
    assert result["wire"] == result["budget"]
    assert not errors, errors


def test_under_budget_sends_are_transmitted_and_truthy(harness):
    page, errors = harness
    _connect(page)
    result = page.evaluate("""() => {
      var ok = 0;
      for (var i = 0; i < 5; i++) if (VG_RT.send({ i: i })) ok++;
      return { ok: ok, wire: window.__msgs().length };
    }""")
    assert result == {"ok": 5, "wire": 5}
    assert not errors, errors


def test_send_budget_refills_over_the_rolling_window(harness):
    page, errors = harness
    _connect(page)
    page.evaluate("""() => { for (var i = 0; i < VG_RT.sendBudget * 2; i++) VG_RT.send(i); }""")
    assert page.evaluate("VG_RT.send('blocked')") is False
    page.wait_for_timeout(1100)
    assert page.evaluate("VG_RT.send('allowed')") is True
    assert not errors, errors


def test_budget_properties_are_present_and_match_what_is_enforced(harness):
    page, errors = harness
    _connect(page)
    budgets = page.evaluate("({ stateHz: VG_RT.stateHz, sendBudget: VG_RT.sendBudget })")
    assert budgets["stateHz"] == 20
    assert budgets["sendBudget"] == 30

    # The send bucket really enforces `sendBudget`.
    accepted = page.evaluate("""() => {
      var ok = 0;
      for (var i = 0; i < 200; i++) if (VG_RT.send(i)) ok++;
      return ok;
    }""")
    assert accepted == budgets["sendBudget"]

    # And the flush timer really runs at `stateHz`.
    page.evaluate("""() => {
      window.__t = setInterval(() => VG_RT.sendState(Math.random()), 5);
    }""")
    before = page.evaluate("window.__msgs().length")
    page.wait_for_timeout(1000)
    page.evaluate("clearInterval(window.__t)")
    flushed = page.evaluate("window.__msgs().length") - before
    assert abs(flushed - budgets["stateHz"]) <= 6, flushed
    assert not errors, errors


# --------------------------------------------------------------------------
# 2.5  presence reset on disconnect
# --------------------------------------------------------------------------

def test_presence_is_cleared_on_close_and_events_are_emitted(harness):
    page, errors = harness
    page.evaluate("""() => {
      window.__ev = { roster: [], peers: [] };
      VG_RT.on('roster', r => window.__ev.roster.push(r.length));
      VG_RT.on('peers', p => window.__ev.peers.push(p.length));
    }""")
    _connect(page)
    assert page.evaluate("VG_RT.roster.length") == 2
    assert page.evaluate("VG_RT.peers.length") == 1
    assert page.evaluate("VG_RT.me") == "me1"

    page.evaluate("window.__close()")
    # A game polling the property, rather than subscribing, sees the drop.
    assert page.evaluate("VG_RT.roster.length") == 0
    assert page.evaluate("VG_RT.peers.length") == 0
    assert page.evaluate("VG_RT.me") is None
    ev = page.evaluate("window.__ev")
    assert ev["roster"][-1] == 0 and ev["peers"][-1] == 0
    assert not errors, errors


def test_presence_is_repopulated_on_reconnect(harness):
    page, errors = harness
    _connect(page)
    page.evaluate("window.__close()")
    assert page.evaluate("VG_RT.roster.length") == 0
    # rt.js's backoff floor is 500ms; wait for it to open a fresh socket.
    page.wait_for_function("window.__sockets.length > 1", timeout=5000)
    page.evaluate("""() => {
      window.__ev2 = [];
      VG_RT.on('roster', r => window.__ev2.push(r.length));
    }""")
    _connect(page)
    assert page.evaluate("VG_RT.me") == "me1"
    assert page.evaluate("VG_RT.roster.length") == 2
    assert page.evaluate("VG_RT.peers.length") == 1
    assert page.evaluate("window.__ev2") == [2]
    assert not errors, errors


# --------------------------------------------------------------------------
# 2.6  settle-gated backoff
# --------------------------------------------------------------------------

def test_short_lived_connections_back_off(harness):
    """Every reconnect succeeds and is then closed at once — the exact shape
    that a reset-on-open backoff never grew out of."""
    page, errors = harness
    for _ in range(3):
        n = page.evaluate("window.__sockets.length")
        page.evaluate("window.__open()")
        page.evaluate("window.__close()")
        page.wait_for_function("window.__sockets.length > %d" % n, timeout=10000)
    times = page.evaluate("window.__socketTimes()")
    gaps = [round(b - a) for a, b in zip(times, times[1:])]
    assert len(gaps) >= 3, gaps
    # 500 -> 1000 -> 2000: each wait is longer than the last.
    for earlier, later in zip(gaps, gaps[1:]):
        assert later > earlier * 1.4, gaps
    assert not errors, errors


def test_a_stable_connection_restores_the_shortest_delay(harness):
    page, errors = harness
    # Two quick cycles first, so the backoff has grown past its floor.
    for _ in range(2):
        n = page.evaluate("window.__sockets.length")
        page.evaluate("window.__open()")
        page.evaluate("window.__close()")
        page.wait_for_function("window.__sockets.length > %d" % n, timeout=10000)
    grown = page.evaluate("window.__socketTimes()")
    assert round(grown[-1] - grown[-2]) > 700, grown   # backoff really did grow

    # Now hold one open past the 5s settle period before closing it.
    n = page.evaluate("window.__sockets.length")
    page.evaluate("window.__open()")
    page.wait_for_timeout(5600)
    page.evaluate("window.__close()")
    page.wait_for_function("window.__sockets.length > %d" % n, timeout=10000)
    times = page.evaluate("window.__socketTimes()")
    delay = page.evaluate("window.__socketTimes().slice(-1)[0] - window.__closedAt")
    assert round(delay) < 900, (delay, times)
    assert not errors, errors
