"""Fork-on-enhance linkage: parent_game_id/root_game_id across a multi-
generation chain, and auto-naming of blank-titled forks."""

import json
from unittest import mock

import ai_client as ai
import db
import game_enhancer as ge
import game_generator as gg

CONFIG = {
    "game_web": {"host": "localhost", "port": 8600, "base_url": ""},
    "newaiwebgame": {"model": "", "effort": "high", "timeout_seconds": 5,
                      "max_attempts": 3, "smoke_test_timeout_seconds": 5},
    "enhanceaiwebgame": {"model": "", "effort": "high", "timeout_seconds": 5,
                          "max_attempts": 3, "smoke_test_timeout_seconds": 5},
}


def _submission(title, html=None):
    """A ToolAskResult that submits a game via the submit_game tool."""
    html = html if html is not None else f"<!doctype html><html><body>{title}</body></html>"
    args = json.dumps({"title": title, "description": "d", "html": html, "notes": ""})
    tool_call_raw = {
        "id": "call_1",
        "type": "function",
        "function": {"name": "submit_game", "arguments": args},
    }
    message = {"role": "assistant", "content": None, "tool_calls": [tool_call_raw]}
    return ai.ToolAskResult(
        message=message,
        tool_calls=[ai.ToolCall(id="call_1", name="submit_game", arguments=args)],
        text="",
        input_tokens=5,
        output_tokens=5,
        model="deepseek-v4-flash",
        effort="high",
        raw_response={
            "id": "mock-completion",
            "model": "deepseek-v4-flash",
            "choices": [{"message": message, "finish_reason": "tool_calls"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        },
    )


def test_three_generation_fork_chain_shares_root(isolated_db, games_dir):
    with mock.patch.object(ai, "ask_with_tools", return_value=_submission("Original")), \
         mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        original = gg.generate_game("desc", "web:a", CONFIG, games_dir=games_dir)
    assert original["success"]

    with mock.patch.object(ai, "ask_with_tools", return_value=_submission("ignored")), \
         mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        v2 = ge.enhance_game(original["game_id"], "improve it", "web:b", CONFIG, games_dir=games_dir)
    assert v2["success"]
    assert v2["title"] == "Original (v2)"
    assert v2["parent_game_id"] == original["game_id"]
    assert v2["root_game_id"] == original["game_id"]

    with mock.patch.object(ai, "ask_with_tools", return_value=_submission("ignored")), \
         mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        v3 = ge.enhance_game(v2["game_id"], "improve it more", "web:c", CONFIG,
                              games_dir=games_dir, new_title="Explicit V3")
    assert v3["success"]
    assert v3["title"] == "Explicit V3"
    assert v3["parent_game_id"] == v2["game_id"], "parent must be the immediate source"
    assert v3["root_game_id"] == original["game_id"], (
        "root must stay the original ancestor across the whole chain"
    )

    # Source untouched at every step.
    orig_row = db.get_web_game(original["game_id"])
    assert orig_row["title"] == "Original"
    assert (games_dir / original["slug"] / "index.html").exists()
    assert (games_dir / v2["slug"] / "index.html").exists()
    assert (games_dir / v3["slug"] / "index.html").exists()


def test_second_fork_of_original_is_v3(isolated_db, games_dir):
    with mock.patch.object(ai, "ask_with_tools", return_value=_submission("Original")), \
         mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        original = gg.generate_game("desc", "web:a", CONFIG, games_dir=games_dir)

    with mock.patch.object(ai, "ask_with_tools", return_value=_submission("ignored")), \
         mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        fork_a = ge.enhance_game(original["game_id"], "req1", "web:b", CONFIG, games_dir=games_dir)
        fork_b = ge.enhance_game(original["game_id"], "req2", "web:c", CONFIG, games_dir=games_dir)

    assert fork_a["title"] == "Original (v2)"
    assert fork_b["title"] == "Original (v3)"


def test_failed_enhance_leaves_no_partial_directory(isolated_db, games_dir):
    with mock.patch.object(ai, "ask_with_tools", return_value=_submission("Original")), \
         mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        original = gg.generate_game("desc", "web:a", CONFIG, games_dir=games_dir)

    before = {p.name for p in games_dir.iterdir()}

    unsafe = _submission(
        "Bad", html="<html><body><script>eval('x')</script></body></html>"
    )
    bad_cfg = dict(CONFIG)
    bad_cfg["enhanceaiwebgame"] = dict(CONFIG["enhanceaiwebgame"], max_attempts=1)
    with mock.patch.object(ai, "ask_with_tools", return_value=unsafe):
        result = ge.enhance_game(original["game_id"], "break it", "web:b", bad_cfg, games_dir=games_dir)

    assert not result["success"]
    after = {p.name for p in games_dir.iterdir()}
    assert before == after, "a failed enhance must not leave any new directory behind"
