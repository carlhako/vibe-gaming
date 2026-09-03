"""multiplayer.py + the authoring opt-in — openspec change add-multiplayer-games,
tasks 3.2, 3.3, 4.1, 4.2, 4.4, 4.5.

The VG_RT client normalizer (insert / dedup / passthrough / idempotent), the
meta.json ``multiplayer`` block helper, fork inheritance, the new-game route
threading, and the generation prompt contract.
"""

import copy
import json
from unittest import mock

import pytest

import ai_client as ai
import builder
import db
import game_generator as gg
import multiplayer


HEAD_DOC = "<!doctype html><html><head><title>x</title></head><body>ok</body></html>"
GID = "a" * 32


# --------------------------------------------------------------------------
# 3.2  multiplayer.normalize
# --------------------------------------------------------------------------

def test_normalize_inserts_client_when_absent():
    out = multiplayer.normalize(HEAD_DOC, True, GID)
    assert out.count("/vendor/rt/rt.js") == 1
    assert f'data-game-id="{GID}"' in out
    assert out.index("/vendor/rt/rt.js") < out.index("</head>")


def test_normalize_dedupes_model_echoed_copy():
    echoed = HEAD_DOC.replace(
        "</head>", '<script src="/vendor/rt/rt.js" data-game-id="old"></script></head>')
    out = multiplayer.normalize(echoed, True, GID)
    assert out.count("/vendor/rt/rt.js") == 1
    assert 'data-game-id="old"' not in out
    assert f'data-game-id="{GID}"' in out


def test_normalize_is_idempotent():
    once = multiplayer.normalize(HEAD_DOC, True, GID)
    twice = multiplayer.normalize(once, True, GID)
    assert once == twice


def test_normalize_passthrough_for_single_player():
    assert multiplayer.normalize(HEAD_DOC, False, GID) == HEAD_DOC
    withtag = HEAD_DOC.replace(
        "</head>", '<script src="/vendor/rt/rt.js"></script></head>')
    assert multiplayer.normalize(withtag, False, GID) == withtag


def test_normalize_dedupes_arbitrary_attribute_order_and_spacing():
    echoed = HEAD_DOC.replace(
        "</head>",
        "<script  data-game-id='zzz'   src='/vendor/rt/rt.js' ></script>\n</head>")
    out = multiplayer.normalize(echoed, True, GID)
    assert out.count("rt.js") == 1


def test_normalize_raises_without_head():
    with pytest.raises(multiplayer.MultiplayerError):
        multiplayer.normalize("<html><body>no head</body></html>", True, GID)


# --------------------------------------------------------------------------
# 4.1  meta.json multiplayer block helper
# --------------------------------------------------------------------------

@pytest.mark.parametrize("block,expected", [
    ({"max_players": 2}, 2),
    ({"max_players": 8}, 8),
    ({"max_players": 1}, None),
    ({"max_players": 0}, None),
    ({"max_players": -3}, None),
    ({"max_players": "4"}, None),
    ({"max_players": 4.0}, None),
    ({"max_players": True}, None),
    ({"players": 4}, None),
    ({}, None),
    ("notadict", None),
])
def test_read_max_players(block, expected):
    assert multiplayer.read_max_players({"multiplayer": block}) == expected


def test_read_max_players_no_block():
    assert multiplayer.read_max_players({"title": "x"}) is None
    assert multiplayer.read_max_players(None) is None


def test_builder_read_multiplayer(tmp_path):
    d = tmp_path / "g"
    d.mkdir()
    (d / "meta.json").write_text(json.dumps(
        {"game_id": GID, "multiplayer": {"max_players": 3}}), encoding="utf-8")
    assert builder.read_multiplayer(d) == {"max_players": 3}


def test_builder_read_multiplayer_none_for_invalid(tmp_path):
    d = tmp_path / "g"
    d.mkdir()
    (d / "meta.json").write_text(json.dumps(
        {"game_id": GID, "multiplayer": {"max_players": 1}}), encoding="utf-8")
    assert builder.read_multiplayer(d) is None
    (d / "meta.json").write_text("{bad", encoding="utf-8")
    assert builder.read_multiplayer(d) is None


def test_builder_read_multiplayer_missing_meta(tmp_path):
    d = tmp_path / "g"
    d.mkdir()
    assert builder.read_multiplayer(d) is None


# --------------------------------------------------------------------------
# 4.5  generation prompt contract
# --------------------------------------------------------------------------

def test_prompt_contract_present_only_when_multiplayer_requested():
    solo = gg._build_system_prompt(None, max_players=None)
    multi = gg._build_system_prompt(None, max_players=4)
    for clause in ("waiting for players", "UNTRUSTED DATA", "textContent",
                   "VG_RT", "do NOT write any", "eval"):
        assert clause in multi
    assert "VG_RT" not in solo
    assert "## Multiplayer" not in solo


# --------------------------------------------------------------------------
# 3.3 + 4.4  pipeline: exactly one client tag, block written to meta.json
# --------------------------------------------------------------------------

CONFIG_NEW = {"model": "", "effort": "high", "timeout_seconds": 5,
              "max_attempts": 2, "smoke_test_timeout_seconds": 5}


def _submission(html, title="MP Game"):
    args = json.dumps({"title": title, "description": "d", "html": html, "notes": ""})
    tc_raw = {"id": "c1", "type": "function",
              "function": {"name": "submit_game", "arguments": args}}
    msg = {"role": "assistant", "content": None, "tool_calls": [tc_raw]}
    return ai.ToolAskResult(
        message=msg,
        tool_calls=[ai.ToolCall(id="c1", name="submit_game", arguments=args)],
        text="", input_tokens=5, output_tokens=5, model="m", effort="high",
        raw_response={"choices": [{"message": msg}],
                      "usage": {"prompt_tokens": 5, "completion_tokens": 5}},
    )


def _run_attempts(games_dir, responses, **kw):
    calls = []

    def scripted(messages, **kwargs):
        calls.append(copy.deepcopy(messages))
        return responses[len(calls) - 1]

    with mock.patch.object(ai, "ask_with_tools", side_effect=scripted), \
         mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        return gg.run_generation_attempts(
            description="desc", requested_by="web:t", system_prompt="system",
            initial_user_prompt="go", cfg=dict(CONFIG_NEW), games_dir=games_dir, **kw)


def test_pipeline_injects_exactly_one_client_and_writes_block(isolated_db, games_dir):
    outcome = _run_attempts(games_dir, [_submission(HEAD_DOC)], max_players=3)
    assert outcome["success"], outcome["error"]
    slug = outcome["slug"]
    served = (games_dir / slug / "index.html").read_text(encoding="utf-8")
    assert served.count("/vendor/rt/rt.js") == 1
    assert f'data-game-id="{outcome["game_id"]}"' in served
    meta = json.loads((games_dir / slug / "meta.json").read_text(encoding="utf-8"))
    assert meta["multiplayer"] == {"max_players": 3}


def test_pipeline_dedupes_model_echoed_client(isolated_db, games_dir):
    echoed = HEAD_DOC.replace(
        "</head>", '<script src="/vendor/rt/rt.js" data-game-id="wrong"></script></head>')
    outcome = _run_attempts(games_dir, [_submission(echoed)], max_players=2)
    assert outcome["success"], outcome["error"]
    served = (games_dir / outcome["slug"] / "index.html").read_text(encoding="utf-8")
    assert served.count("/vendor/rt/rt.js") == 1
    assert 'data-game-id="wrong"' not in served


def test_pipeline_no_client_and_no_block_for_single_player(isolated_db, games_dir):
    outcome = _run_attempts(games_dir, [_submission(HEAD_DOC)], max_players=None)
    assert outcome["success"], outcome["error"]
    slug = outcome["slug"]
    served = (games_dir / slug / "index.html").read_text(encoding="utf-8")
    assert "/vendor/rt/rt.js" not in served
    meta = json.loads((games_dir / slug / "meta.json").read_text(encoding="utf-8"))
    assert "multiplayer" not in meta


# --------------------------------------------------------------------------
# 4.2  fork / enhance inherits the block without the model reproducing it
# --------------------------------------------------------------------------

def test_enhance_fork_inherits_multiplayer_block(isolated_db, games_dir, monkeypatch):
    import game_enhancer as ge

    # a source multiplayer game on disk + in the registry
    src_id = db.mint_game_id()
    src_slug = db.make_slug("Source", src_id)
    d = games_dir / src_slug
    d.mkdir()
    (d / "index.html").write_text(
        multiplayer.normalize(HEAD_DOC, True, src_id), encoding="utf-8")
    (d / "meta.json").write_text(json.dumps({
        "game_id": src_id, "parent_game_id": None, "root_game_id": src_id,
        "title": "Source", "version": 1, "multiplayer": {"max_players": 5},
    }), encoding="utf-8")
    db.register_web_game(
        game_id=src_id, slug=src_slug, title="Source", description="d",
        requested_by="web:t", status="success", attempts=1, version=1,
        model="m", effort="high", duration_seconds=1.0, input_tokens=1,
        output_tokens=1, tokens_used=2, error=None, parent_game_id=None,
        root_game_id=src_id, creator_uid=None)

    # the model resubmits HTML WITHOUT any multiplayer block or client tag
    plain = "<!doctype html><html><head><title>v2</title></head><body>v2</body></html>"

    def scripted(messages, **kwargs):
        return _submission(plain, title="Source (v2)")

    cfg = {"enhanceaiwebgame": dict(CONFIG_NEW), "game_web": {"base_url": ""}}
    with mock.patch.object(ai, "ask_with_tools", side_effect=scripted), \
         mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result = ge.enhance_game(src_id, "make it better", "web:t", cfg,
                                 games_dir=games_dir)

    assert result["success"], result.get("error")
    fork_slug = result["slug"]
    fork_meta = json.loads(
        (games_dir / fork_slug / "meta.json").read_text(encoding="utf-8"))
    assert fork_meta["multiplayer"] == {"max_players": 5}
    served = (games_dir / fork_slug / "index.html").read_text(encoding="utf-8")
    assert served.count("/vendor/rt/rt.js") == 1
    assert f'data-game-id="{result["game_id"]}"' in served
