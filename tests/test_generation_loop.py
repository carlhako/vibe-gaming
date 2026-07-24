"""The submit_game conversation loop in run_generation_attempts():
rejected submissions come back as tool results, the model resubmits in
the same conversation, and parse_submission validates tool arguments."""

import copy
import json
from unittest import mock

import pytest

import ai_client as ai
import content_moderation
import db
import game_generator as gg

CONFIG = {
    "game_web": {"host": "localhost", "port": 8600, "base_url": ""},
    "newaiwebgame": {"model": "", "effort": "high", "timeout_seconds": 5,
                      "max_attempts": 3, "smoke_test_timeout_seconds": 5},
}

SAFE_HTML = "<!doctype html><html><body>ok</body></html>"
UNSAFE_HTML = "<html><body><script>eval('x')</script></body></html>"


def _submission(arguments, tool_call_id="call_1"):
    """ToolAskResult carrying one submit_game call with raw `arguments`."""
    tool_call_raw = {
        "id": tool_call_id,
        "type": "function",
        "function": {"name": "submit_game", "arguments": arguments},
    }
    message = {"role": "assistant", "content": None, "tool_calls": [tool_call_raw]}
    return ai.ToolAskResult(
        message=message,
        tool_calls=[ai.ToolCall(id=tool_call_id, name="submit_game", arguments=arguments)],
        text="",
        input_tokens=5,
        output_tokens=5,
        model="deepseek-v4-flash",
        effort="high",
        raw_response={"choices": [{"message": message}],
                      "usage": {"prompt_tokens": 5, "completion_tokens": 5}},
    )


def _game_args(html, title="Game"):
    return json.dumps({"title": title, "description": "d", "html": html, "notes": ""})


def _run(games_dir, responses, max_attempts=3):
    """Run the loop against a scripted sequence of ToolAskResults, capturing
    a snapshot of the conversation passed to each ask_with_tools call."""
    seen_messages = []

    def scripted(messages, **kwargs):
        seen_messages.append(copy.deepcopy(messages))
        return responses[len(seen_messages) - 1]

    cfg = dict(CONFIG["newaiwebgame"], max_attempts=max_attempts)
    with mock.patch.object(ai, "ask_with_tools", side_effect=scripted), \
         mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        outcome = gg.run_generation_attempts(
            description="desc", requested_by="web:t",
            system_prompt="system", initial_user_prompt="make a game",
            cfg=cfg, games_dir=games_dir,
        )
    return outcome, seen_messages


def test_rejection_feeds_failure_back_as_tool_result(isolated_db, games_dir):
    outcome, seen = _run(games_dir, [
        _submission(_game_args(UNSAFE_HTML)),
        _submission(_game_args(SAFE_HTML)),
    ])

    assert outcome["success"]
    assert outcome["attempts"] == 2
    assert outcome["input_tokens"] == 10
    assert outcome["output_tokens"] == 10
    assert outcome["tokens_used"] == 20

    # Second call must carry the whole conversation: the model's rejected
    # submission followed by a tool result naming the safety violation.
    second = seen[1]
    assert second[0]["role"] == "system"
    assert second[2]["role"] == "assistant"
    tool_reply = second[3]
    assert tool_reply["role"] == "tool"
    assert tool_reply["tool_call_id"] == "call_1"
    assert "REJECTED" in tool_reply["content"]
    assert "safety violation" in tool_reply["content"]


def test_malformed_arguments_rejected_then_retried(isolated_db, games_dir):
    outcome, seen = _run(games_dir, [
        _submission("{not valid json"),
        _submission(_game_args(SAFE_HTML)),
    ])

    assert outcome["success"]
    assert outcome["attempts"] == 2
    assert "malformed submission" in seen[1][3]["content"]


def test_gives_up_after_max_attempts(isolated_db, games_dir):
    outcome, seen = _run(games_dir, [
        _submission(_game_args(UNSAFE_HTML)),
        _submission(_game_args(UNSAFE_HTML)),
    ], max_attempts=2)

    assert not outcome["success"]
    assert outcome["attempts"] == 2
    assert outcome["error"].startswith("safety violation")
    assert len(seen) == 2
    assert not any(games_dir.iterdir()), "no half-written directory may survive"


def test_reply_without_tool_call_gets_a_nudge(isolated_db, games_dir):
    no_call = ai.ToolAskResult(
        message={"role": "assistant", "content": "here is your game: ..."},
        tool_calls=[], text="here is your game: ...", input_tokens=5, output_tokens=5,
        model="deepseek-v4-flash", effort="high",
        raw_response={"choices": [{"message": {"role": "assistant"}}]},
    )
    outcome, seen = _run(games_dir, [no_call, _submission(_game_args(SAFE_HTML))])

    assert outcome["success"]
    nudge = seen[1][3]
    assert nudge["role"] == "user"
    assert "submit_game" in nudge["content"]


def test_extra_tool_calls_are_answered_but_ignored(isolated_db, games_dir):
    first = _submission(_game_args(UNSAFE_HTML))
    dup_raw = {"id": "call_2", "type": "function",
               "function": {"name": "submit_game", "arguments": _game_args(SAFE_HTML)}}
    first.message["tool_calls"].append(dup_raw)
    first.tool_calls.append(ai.ToolCall(id="call_2", name="submit_game",
                                         arguments=_game_args(SAFE_HTML)))

    outcome, seen = _run(games_dir, [first, _submission(_game_args(SAFE_HTML))])

    assert outcome["success"]
    assert outcome["attempts"] == 2, "only the first call per turn is evaluated"
    replies = {m["tool_call_id"]: m["content"] for m in seen[1] if m.get("role") == "tool"}
    assert set(replies) == {"call_1", "call_2"}
    assert "Ignored" in replies["call_2"]


# ---------------------------------------------------------------------------
# parse_submission
# ---------------------------------------------------------------------------

def test_parse_submission_valid():
    parsed = gg.parse_submission(json.dumps({
        "title": " T ", "description": "d", "html": SAFE_HTML, "notes": "None",
    }))
    assert parsed == {"game_html": SAFE_HTML, "title": "T",
                      "description": "d", "notes": ""}


@pytest.mark.parametrize("arguments", [
    "{broken",
    json.dumps(["not", "an", "object"]),
    json.dumps({"description": "d", "html": SAFE_HTML}),          # no title
    json.dumps({"title": "T", "html": SAFE_HTML}),                # no description
    json.dumps({"title": "T", "description": "d"}),               # no html
    json.dumps({"title": "", "description": "d", "html": SAFE_HTML}),
    json.dumps({"title": "T", "description": "d", "html": "   "}),
])
def test_parse_submission_rejects(arguments):
    with pytest.raises(gg.GameGenerationError):
        gg.parse_submission(arguments)


# ---------------------------------------------------------------------------
# ai_client._resolve_tool_choice (DeepSeek thinking-mode quirk)
# ---------------------------------------------------------------------------

def test_forcing_tool_choice_downgraded_only_in_thinking_mode():
    forced = {"type": "function", "function": {"name": "submit_game"}}
    thinking = {"thinking": {"type": "enabled"}, "reasoning_effort": "high"}
    non_thinking = {"thinking": {"type": "disabled"}}

    assert ai._resolve_tool_choice(forced, thinking) == "auto"
    assert ai._resolve_tool_choice("required", thinking) == "auto"
    assert ai._resolve_tool_choice("auto", thinking) == "auto"
    assert ai._resolve_tool_choice(None, thinking) is None
    assert ai._resolve_tool_choice(forced, non_thinking) == forced
    assert ai._resolve_tool_choice("required", non_thinking) == "required"


# ---------------------------------------------------------------------------
# Sprint 4 Part A: automated content-moderation pass, hooked into
# generate_game()'s success branch (run_generation_attempts itself is
# unaware of moderation — only the orchestrator is)
# ---------------------------------------------------------------------------

def _generate_with(games_dir, moderation_patch):
    responses = [_submission(_game_args(SAFE_HTML))]

    def scripted(messages, **kwargs):
        return responses[0]

    with mock.patch.object(ai, "ask_with_tools", side_effect=scripted), \
         mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")), \
         moderation_patch:
        return gg.generate_game("desc", "web:t", CONFIG, games_dir=games_dir)


def test_moderation_flag_hides_game_and_creates_report(isolated_db, games_dir):
    patch = mock.patch.object(
        content_moderation, "check_game",
        return_value={"flagged": True, "reason": "asks player for a password"},
    )
    result = _generate_with(games_dir, patch)

    assert result["success"] is True, "a flagged game still reports success to the requester"
    game = db.get_web_game(result["game_id"])
    assert game["hidden"] == 1

    open_reports = db.get_open_reports()
    assert len(open_reports) == 1
    assert open_reports[0]["game_id"] == result["game_id"]
    assert open_reports[0]["reports"][0]["source"] == "moderation"
    assert open_reports[0]["reports"][0]["reason"] == "asks player for a password"


def test_moderation_unflagged_leaves_game_visible(isolated_db, games_dir):
    patch = mock.patch.object(
        content_moderation, "check_game", return_value={"flagged": False, "reason": ""},
    )
    result = _generate_with(games_dir, patch)

    assert result["success"] is True
    game = db.get_web_game(result["game_id"])
    assert game["hidden"] == 0
    assert db.get_open_reports() == []


def test_moderation_ai_error_defaults_to_unflagged(isolated_db, games_dir):
    """content_moderation.check_game itself swallows AIError -> flagged=False
    (see content_moderation.py); assert that outage never blocks or hides a
    successful generation."""
    patch = mock.patch.object(ai, "ask", side_effect=ai.AIError("moderation outage"))
    result = _generate_with(games_dir, patch)

    assert result["success"] is True
    game = db.get_web_game(result["game_id"])
    assert game["hidden"] == 0
    assert db.get_open_reports() == []


def test_moderation_unparseable_reply_defaults_to_unflagged(isolated_db, games_dir):
    bad_ask_result = ai.AskResult(
        text="not json at all", input_tokens=1, output_tokens=1,
        model="deepseek-v4-flash", effort="non-thinking", raw_response={},
    )
    patch = mock.patch.object(ai, "ask", return_value=bad_ask_result)
    result = _generate_with(games_dir, patch)

    assert result["success"] is True
    game = db.get_web_game(result["game_id"])
    assert game["hidden"] == 0
    assert db.get_open_reports() == []
