"""End-to-end: a multiplayer game generated through the real pipeline
(mocked AI, real smoke test) connects to the smoke server's WebSocket stub,
reaches its waiting state, and passes. openspec change add-multiplayer-games,
task 5.1.
"""

import base64
import hashlib
import json
from unittest import mock

import pytest

pytest.importorskip("playwright.sync_api")

import ai_client as ai
import content_moderation
import db
import game_generator as gg
import multiplayer
import smoke_test


def _has_chromium():
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            p.chromium.launch().close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _has_chromium(), reason="Chromium not installed")

CONFIG = {
    "game_web": {"host": "localhost", "port": 8600, "base_url": ""},
    "newaiwebgame": {"model": "", "effort": "high", "timeout_seconds": 10,
                      "max_attempts": 1, "smoke_test_timeout_seconds": 15},
}

# A minimal but real multiplayer game: it uses window.VG_RT (injected by the
# pipeline — the model never writes the tag), renders "waiting for players"
# until a roster arrives, and never touches innerHTML with peer data.
CANNED_GAME = """<!doctype html><html><head><meta charset="utf-8"><title>MP</title></head>
<body>
<h1 id="status">connecting…</h1>
<script>
(function () {
  function paint() {
    var n = (window.VG_RT && VG_RT.roster.length) || 0;
    document.getElementById('status').textContent =
      n < 2 ? 'waiting for players (' + n + '/2)' : 'playing';
  }
  function wire() {
    if (!window.VG_RT) { setTimeout(wire, 50); return; }
    VG_RT.on('roster', paint);
    VG_RT.on('welcome', paint);
    paint();
  }
  wire();
})();
</script>
</body></html>"""


def _submission(html):
    args = json.dumps({"title": "MP Duel", "description": "a 2p game",
                       "html": html, "notes": ""})
    tc = {"id": "c1", "type": "function",
          "function": {"name": "submit_game", "arguments": args}}
    msg = {"role": "assistant", "content": None, "tool_calls": [tc]}
    return ai.ToolAskResult(
        message=msg,
        tool_calls=[ai.ToolCall(id="c1", name="submit_game", arguments=args)],
        text="", input_tokens=5, output_tokens=5, model="m", effort="high",
        raw_response={"choices": [{"message": msg}],
                      "usage": {"prompt_tokens": 5, "completion_tokens": 5}},
    )


def test_generated_multiplayer_game_smoke_tests_solo(isolated_db, games_dir):
    with mock.patch.object(ai, "ask_with_tools",
                           side_effect=lambda messages, **kw: _submission(CANNED_GAME)), \
         mock.patch.object(content_moderation, "check_game",
                           return_value={"flagged": False, "reason": ""}):
        result = gg.generate_game("a 2-player duel", "web:t", CONFIG,
                                  games_dir=games_dir, max_players=2)

    assert result["success"] is True, result.get("error")

    slug = result["slug"]
    served = (games_dir / slug / "index.html").read_text(encoding="utf-8")
    # the client was injected exactly once, carrying this game's id
    assert served.count("/vendor/rt/rt.js") == 1
    assert f'data-game-id="{result["game_id"]}"' in served
    # and the opt-in block is on disk
    meta = json.loads((games_dir / slug / "meta.json").read_text(encoding="utf-8"))
    assert meta["multiplayer"] == {"max_players": 2}


# --------------------------------------------------------------------------
# fix-multiplayer-smoke-ws-close — task 3.1
#
# The CANNED_GAME above only repaints on roster/welcome and never sends, which
# is why the suite stayed green while real games (whose sync loop calls
# VG_RT.send() every frame) failed generation with a burst of
# `WebSocket is already in CLOSING or CLOSED state.` console errors. These two
# tests cover that regression: the injected client + held-open stub must let a
# send-every-frame game pass, and the pre-fix arrangement (client that writes
# on a truthiness guard only + stub that closes immediately) must fail.
# --------------------------------------------------------------------------

# Real multiplayer game: starts a requestAnimationFrame loop on `welcome` that
# syncs its position state every frame for the whole settle window. The call
# site is deliberately per-frame — that is the idiom a game author writes — so
# what this exercises end to end is VG_RT.sendState's coalescing, which is the
# only reason a per-frame call site is safe against the relay's rate budget.
SEND_LOOP_GAME = """<!doctype html><html><head><meta charset="utf-8"><title>MP</title></head>
<body>
<h1 id="status">connecting…</h1>
<script>
(function () {
  var sending = false;
  function tick() {
    if (window.VG_RT) VG_RT.sendState({ t: 'pos', x: Math.random(), y: Math.random() });
    requestAnimationFrame(tick);
  }
  function wire() {
    if (!window.VG_RT) { setTimeout(wire, 50); return; }
    VG_RT.on('welcome', function () {
      document.getElementById('status').textContent = 'playing';
      if (!sending) { sending = true; requestAnimationFrame(tick); }
    });
  }
  wire();
})();
</script>
</body></html>"""

# Pre-fix shape, inlined so the check does not depend on shipping a reverted
# vendor file: a raw socket written to on a rAF loop guarded only on
# truthiness (no `ws.readyState === OPEN`), reconnecting on every close to
# mirror rt.js's backoff storm.
RAW_SEND_LOOP_GAME = """<!doctype html><html><head><meta charset="utf-8"><title>MP</title></head>
<body>
<h1 id="status">connecting…</h1>
<script>
(function () {
  var ws = null, open = false;
  function connect() {
    ws = new WebSocket("ws://" + location.host + "/rt/" + "a".repeat(32));
    open = false;
    ws.onopen = function () { open = true; };
    ws.onerror = function () {};
    ws.onclose = function () { open = false; setTimeout(connect, 0); };
  }
  function tick() {
    if (ws && open) { try { ws.send('{"t":"msg","d":1}'); } catch (e) {} }
    requestAnimationFrame(tick);
  }
  connect();
  requestAnimationFrame(tick);
})();
</script>
</body></html>"""


def test_send_loop_multiplayer_game_passes_smoke(tmp_path):
    """With the rt.js readyState guard and the held-open stub, a game that
    syncs state on every animation frame smoke-tests clean — and coalesced,
    so the stub's rate check passes too."""
    gid = "b" * 32
    html = tmp_path / "index.html"
    html.write_text(multiplayer.normalize(SEND_LOOP_GAME, True, gid),
                    encoding="utf-8")
    passed, detail = smoke_test.run_smoke_test(str(html), timeout_seconds=15)
    assert passed, detail


# A game that bypasses VG_RT and floods a raw socket at animation-frame rate:
# what the stub's frame counting exists to catch before the game ships.
FLOOD_GAME = """<!doctype html><html><head><meta charset="utf-8"><title>MP</title></head>
<body>
<h1 id="status">flooding</h1>
<script>
(function () {
  var ws = new WebSocket("ws://" + location.host + "/rt/" + "c".repeat(32));
  var open = false;
  ws.onopen = function () { open = true; };
  ws.onerror = function () {};
  function tick() {
    if (open && ws.readyState === WebSocket.OPEN) {
      // 20 frames per animation frame: ~1200/sec, far past the hard ceiling.
      for (var i = 0; i < 20; i++) {
        try { ws.send('{"t":"msg","d":1}'); } catch (e) {}
      }
    }
    requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);
})();
</script>
</body></html>"""


def test_flooding_multiplayer_game_fails_smoke(tmp_path):
    """End-to-end proof the stub's counting is wired into the verdict: a game
    the hub would disconnect fails during generation instead."""
    html = tmp_path / "index.html"
    html.write_text(FLOOD_GAME, encoding="utf-8")
    passed, detail = smoke_test.run_smoke_test(str(html), timeout_seconds=15)
    assert passed is False, detail
    assert "send rate too high" in detail
    assert "sendState" in detail


def test_send_loop_fails_pre_fix_when_stub_closes_immediately(tmp_path, monkeypatch):
    """Guard test documenting the pre-fix failure mode: a stub that closes the
    socket right after welcome/roster, plus a client that writes without a
    readyState check, produces `WebSocket is already in CLOSING or CLOSED
    state.` console errors and fails the attempt."""
    st = smoke_test

    def closing_stub(self):
        key = self.headers.get("Sec-WebSocket-Key")
        if not key:
            self.send_error(400)
            return
        accept = base64.b64encode(
            hashlib.sha1((key + st._WS_GUID).encode("ascii")).digest()
        ).decode("ascii")
        self.send_response(101)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.end_headers()
        try:
            self.wfile.write(st._ws_text_frame(
                json.dumps({"t": "welcome", "id": "smoke", "max": 2})))
            self.wfile.write(st._ws_text_frame(
                json.dumps({"t": "roster", "members": [
                    {"id": "smoke", "nick": "", "ping_ms": None}]})))
            self.wfile.write(st._WS_CLOSE_FRAME)
            self.wfile.flush()
        except OSError:
            pass

    monkeypatch.setattr(st._SmokeHandler, "_serve_ws_stub", closing_stub)

    html = tmp_path / "index.html"
    html.write_text(RAW_SEND_LOOP_GAME, encoding="utf-8")
    passed, detail = st.run_smoke_test(str(html), timeout_seconds=15)
    assert passed is False
    assert "CLOSING or CLOSED" in detail
