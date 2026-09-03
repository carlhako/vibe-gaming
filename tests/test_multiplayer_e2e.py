"""End-to-end: a multiplayer game generated through the real pipeline
(mocked AI, real smoke test) connects to the smoke server's WebSocket stub,
reaches its waiting state, and passes. openspec change add-multiplayer-games,
task 5.1.
"""

import json
from unittest import mock

import pytest

pytest.importorskip("playwright.sync_api")

import ai_client as ai
import content_moderation
import db
import game_generator as gg


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
