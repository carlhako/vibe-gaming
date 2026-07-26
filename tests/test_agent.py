"""Sprint 2 of docs/multifile-agent/: the ReAct editing agent for
multi-file games. Mocks ai_client.ask_with_tools with a scripted sequence
of tool calls (as tests/test_generation_loop.py already does for
submit_game) and smoke_test.run_smoke_test, so no network or browser is
needed."""

import copy
import json
import shutil
from pathlib import Path
from unittest import mock

import agent
import ai_client as ai
import db

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "multifile-game"
SOURCE_GAME_ID = "6a604c1fd9a1cf932aa72764be2f14e4"

CONFIG = {
    "game_web": {"host": "localhost", "port": 8600, "base_url": ""},
    "multifile_agent": {
        "model": "", "effort": "high", "timeout_seconds": 5,
        "max_steps": 10, "max_verification_retries": 3,
        "max_module_bytes": 100_000,
    },
}


def _setup_source_game(games_dir) -> dict:
    """Copy the Sprint 1 fixture into games_dir as a registered, multi-file
    source game ready to enhance."""
    slug = "click-counter-src"
    shutil.copytree(FIXTURE_DIR, games_dir / slug)
    db.register_web_game(
        game_id=SOURCE_GAME_ID, slug=slug, title="Click Counter",
        description="Press the button, watch the number climb.",
        requested_by="web:t", status="success", attempts=1, version=1,
        model="deepseek-v4-flash", effort="high",
        parent_game_id=None, root_game_id=SOURCE_GAME_ID,
    )
    return db.get_web_game(SOURCE_GAME_ID)


def _tool_call(name, args, call_id):
    arguments = json.dumps(args)
    raw = {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}
    return raw, ai.ToolCall(id=call_id, name=name, arguments=arguments)


def _turn(calls, tokens=(5, 5)):
    """One scripted ToolAskResult carrying one or more tool calls.
    `calls` is a list of (name, args_dict)."""
    raws, tool_calls = [], []
    for i, (name, args) in enumerate(calls):
        raw, tc = _tool_call(name, args, f"call_{i}_{name}")
        raws.append(raw)
        tool_calls.append(tc)
    message = {"role": "assistant", "content": None, "tool_calls": raws}
    return ai.ToolAskResult(
        message=message, tool_calls=tool_calls, text="",
        input_tokens=tokens[0], output_tokens=tokens[1],
        model="deepseek-v4-flash", effort="high",
        raw_response={"choices": [{"message": message}],
                      "usage": {"prompt_tokens": tokens[0], "completion_tokens": tokens[1]}},
    )


def _run(games_dir, responses, config=None, **kwargs):
    """Run enhance_multifile_game against a scripted sequence of
    ToolAskResults, capturing a snapshot of the conversation passed to each
    ask_with_tools call."""
    seen_messages = []

    def scripted(messages, **_kwargs):
        seen_messages.append(copy.deepcopy(messages))
        return responses[len(seen_messages) - 1]

    with mock.patch.object(ai, "ask_with_tools", side_effect=scripted):
        result = agent.enhance_multifile_game(
            SOURCE_GAME_ID, "make the button say Punch instead of Click me",
            "web:t", config or CONFIG, games_dir=games_dir, **kwargs,
        )
    return result, seen_messages


NEW_CORE_JS = (
    '(function () {\n'
    '  var count = 0;\n'
    '  var countEl = document.getElementById("count");\n'
    '  var btn = document.getElementById("btn");\n'
    '  btn.addEventListener("click", function () {\n'
    '    count += 1;\n'
    '    countEl.textContent = String(count);\n'
    '  });\n'
    '})();\n'
)


# ---------------------------------------------------------------------------
# 1. A scripted run that reads the map, reads one module, writes it, and
#    finishes -> a built, forked game where only the targeted module changed.
# ---------------------------------------------------------------------------

def test_targeted_edit_produces_forked_game_with_only_that_module_changed(isolated_db, games_dir):
    _setup_source_game(games_dir)
    responses = [
        _turn([("read_map", {})]),
        _turn([("read_file", {"path": "core.js"})]),
        _turn([
            ("write_file", {"path": "core.js", "contents": NEW_CORE_JS}),
            ("finish", {"summary": "no-op edit to core.js"}),
        ]),
    ]

    with mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result, seen = _run(games_dir, responses)

    assert result["success"], result["error"]
    assert result["parent_game_id"] == SOURCE_GAME_ID
    assert result["root_game_id"] == SOURCE_GAME_ID
    assert result["title"] == "Click Counter (v2)"

    fork_dir = games_dir / result["slug"]
    assert (fork_dir / "index.html").is_file()
    assert (fork_dir / "meta.json").is_file()
    meta = json.loads((fork_dir / "meta.json").read_text())
    assert meta["format"] == "multi-file"
    assert meta["parent_game_id"] == SOURCE_GAME_ID
    assert meta["root_game_id"] == SOURCE_GAME_ID

    # Only core.js changed; style.css and game.md are byte-identical to source.
    src_dir = games_dir / "click-counter-src"
    assert (fork_dir / "src" / "core.js").read_text() == NEW_CORE_JS
    assert (fork_dir / "src" / "style.css").read_text() == (src_dir / "src" / "style.css").read_text()
    assert (fork_dir / "game.md").read_text() == (src_dir / "game.md").read_text()

    # Source untouched.
    assert (src_dir / "src" / "core.js").read_text() != NEW_CORE_JS

    # No turn's conversation ever contained the whole assembled game.
    for messages in seen:
        for m in messages:
            content = m.get("content")
            if isinstance(content, str):
                assert "<html" not in content.lower()

    game = db.get_web_game(result["game_id"])
    assert game["parent_game_id"] == SOURCE_GAME_ID
    assert game["root_game_id"] == SOURCE_GAME_ID


# ---------------------------------------------------------------------------
# 2. write_file over max_module_bytes -> rejected with the split message;
#    the agent's next (smaller) write succeeds.
# ---------------------------------------------------------------------------

def test_oversized_write_rejected_then_smaller_write_succeeds(isolated_db, games_dir):
    _setup_source_game(games_dir)
    huge = "x" * 500
    small_cfg = {
        "game_web": CONFIG["game_web"],
        "multifile_agent": dict(CONFIG["multifile_agent"], max_module_bytes=100),
    }
    responses = [
        _turn([("write_file", {"path": "core.js", "contents": huge})]),
        _turn([
            ("write_file", {"path": "core.js", "contents": NEW_CORE_JS[:50]}),
            ("finish", {"summary": "shrunk core.js"}),
        ]),
    ]

    with mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result, seen = _run(games_dir, responses, config=small_cfg)

    assert result["success"], result["error"]

    # The rejection for the first (oversized) write is fed back as this
    # call's own tool result before the second turn is sent.
    second_turn = seen[1]
    rejection = next(m for m in second_turn if m.get("role") == "tool"
                      and m.get("tool_call_id") == "call_0_write_file")
    assert "REJECTED" in rejection["content"]
    assert "500 bytes" in rejection["content"]
    assert "100-byte" in rejection["content"]

    fork_dir = games_dir / result["slug"]
    assert (fork_dir / "src" / "core.js").read_text() == NEW_CORE_JS[:50]


# ---------------------------------------------------------------------------
# 2b. Context pruning (Sprint 6, docs/multifile-agent/06-streaming-and-polish.md):
#     the Sprint 5 pilot found the agent path used 5-12x more input tokens
#     than the single-file baseline, dominated by write_file calls' own
#     arguments (the complete new file contents) never being pruned out of
#     the resent-every-turn conversation. These tests cover the fix.
# ---------------------------------------------------------------------------

def test_successful_write_file_arguments_are_squashed_out_of_history(isolated_db, games_dir):
    _setup_source_game(games_dir)
    responses = [
        _turn([("write_file", {"path": "core.js", "contents": NEW_CORE_JS})]),
        _turn([("read_file", {"path": "style.css"})]),
        _turn([("finish", {"summary": "done"})]),
    ]

    with mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result, seen = _run(games_dir, responses)

    assert result["success"], result["error"]

    # seen[-1] is the conversation as sent for the LAST scripted response —
    # i.e. after both the write_file turn and a further read_file turn have
    # already been processed, so this proves the squash isn't undone or
    # skipped by later turns.
    later_turn = seen[-1]
    write_call_msg = next(
        m for m in later_turn
        if m.get("role") == "assistant"
        and any(tc["id"] == "call_0_write_file" for tc in (m.get("tool_calls") or []))
    )
    tc = next(tc for tc in write_call_msg["tool_calls"] if tc["id"] == "call_0_write_file")
    arguments = json.loads(tc["function"]["arguments"])
    assert NEW_CORE_JS not in json.dumps(arguments)
    assert "omitted from history" in arguments["contents"]
    # "path" MUST survive the squash — a real pilot re-run found a
    # path-less placeholder taught the model to omit "path" itself on
    # later write_file calls, since it pattern-matches its own history.
    assert arguments["path"] == "core.js"


def test_rejected_write_file_arguments_are_also_squashed(isolated_db, games_dir):
    _setup_source_game(games_dir)
    huge = "x" * 500
    small_cfg = {
        "game_web": CONFIG["game_web"],
        "multifile_agent": dict(CONFIG["multifile_agent"], max_module_bytes=100),
    }
    responses = [
        _turn([("write_file", {"path": "core.js", "contents": huge})]),
        _turn([
            ("write_file", {"path": "core.js", "contents": NEW_CORE_JS[:50]}),
            ("finish", {"summary": "shrunk core.js"}),
        ]),
    ]

    with mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result, seen = _run(games_dir, responses, config=small_cfg)

    assert result["success"], result["error"]

    write_call_msg = next(
        m for m in seen[-1]
        if m.get("role") == "assistant"
        and any(tc["id"] == "call_0_write_file" for tc in (m.get("tool_calls") or []))
    )
    tc = next(tc for tc in write_call_msg["tool_calls"] if tc["id"] == "call_0_write_file")
    arguments = json.loads(tc["function"]["arguments"])
    assert huge not in json.dumps(arguments)
    assert "omitted from history" in arguments["contents"]
    assert arguments["path"] == "core.js"


def test_stale_read_file_result_is_pruned_after_configured_step_age(isolated_db, games_dir):
    _setup_source_game(games_dir)
    style_css = (games_dir / "click-counter-src" / "src" / "style.css").read_text()
    small_cfg = {
        "game_web": CONFIG["game_web"],
        "multifile_agent": dict(CONFIG["multifile_agent"], context_prune_after_steps=2),
    }
    responses = [
        _turn([("read_file", {"path": "style.css"})]),   # step 1: read, never rewritten
        _turn([("read_map", {})]),                        # step 2: filler, not yet stale (2-1=1 < 2)
        _turn([("read_map", {})]),                        # step 3: now stale (3-1=2 >= 2) -> pruned
        _turn([("finish", {"summary": "no changes needed"})]),
    ]

    with mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result, seen = _run(games_dir, responses, config=small_cfg)

    assert result["success"], result["error"]

    tool_result = next(
        m for m in seen[-1]
        if m.get("role") == "tool" and m.get("tool_call_id") == "call_0_read_file"
    )
    assert style_css not in tool_result["content"]
    assert "pruned" in tool_result["content"]
    assert "steps ago" in tool_result["content"]


# ---------------------------------------------------------------------------
# 3. Smoke-test failure on first finish -> failure observation fed back ->
#    a second finish after an edit passes.
# ---------------------------------------------------------------------------

def test_failed_verification_feeds_back_then_retry_succeeds(isolated_db, games_dir):
    _setup_source_game(games_dir)
    responses = [
        _turn([
            ("write_file", {"path": "core.js", "contents": NEW_CORE_JS}),
            ("finish", {"summary": "first attempt"}),
        ]),
        _turn([("finish", {"summary": "second attempt, fixed"})]),
    ]

    with mock.patch("smoke_test.run_smoke_test",
                     side_effect=[(False, "console.error: boom"), (True, "ok")]):
        result, seen = _run(games_dir, responses, job_id="job-1")

    assert result["success"], result["error"]
    assert result["attempts"] == 2

    second_turn_finish_reply = next(
        m for m in seen[1] if m.get("role") == "tool" and "finish" in m.get("tool_call_id", ""))
    # seen[1] is the messages SENT on the second call, i.e. it already
    # contains the first finish's rejection appended as that call's result.
    rejections = [m for m in seen[1] if m.get("role") == "tool" and "REJECTED" in m.get("content", "")]
    assert rejections, "first (failing) finish must be fed back as REJECTED"
    assert "console.error: boom" in rejections[0]["content"]

    attempts = db.get_generation_attempts("job-1")
    assert [a["outcome"] for a in attempts] == ["smoke_test_failed", "success"]


def test_verification_gives_up_after_max_retries_and_rolls_back(isolated_db, games_dir):
    _setup_source_game(games_dir)
    small_cfg = {
        "game_web": CONFIG["game_web"],
        "multifile_agent": dict(CONFIG["multifile_agent"], max_verification_retries=2),
    }
    responses = [
        _turn([("finish", {"summary": "attempt 1"})]),
        _turn([("finish", {"summary": "attempt 2"})]),
    ]

    with mock.patch("smoke_test.run_smoke_test", return_value=(False, "console.error: still broken")):
        result, _seen = _run(games_dir, responses, config=small_cfg)

    assert not result["success"]
    assert "still broken" in result["error"]
    # No half-written fork directory survives, and the source is untouched.
    remaining = {p.name for p in games_dir.iterdir()}
    assert remaining == {"click-counter-src"}


# ---------------------------------------------------------------------------
# 4. Structure change requires a game.md update: a run that edits game.md
#    persists it in the fork.
# ---------------------------------------------------------------------------

def test_game_md_edit_is_persisted_in_the_fork(isolated_db, games_dir):
    _setup_source_game(games_dir)
    new_map = "# Click Counter\n\nUpdated map after adding a new module.\n"
    responses = [
        _turn([
            ("write_file", {"path": "game.md", "contents": new_map}),
            ("finish", {"summary": "updated the map"}),
        ]),
    ]

    with mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result, _seen = _run(games_dir, responses)

    assert result["success"], result["error"]
    fork_dir = games_dir / result["slug"]
    assert fork_dir.joinpath("game.md").read_text() == new_map


# ---------------------------------------------------------------------------
# 5. Fork linkage / rollback edge cases already covered above; a couple more
#    targeted checks on is_multi_file_source and title numbering.
# ---------------------------------------------------------------------------

def test_is_multi_file_source_true_for_multifile_false_for_missing(isolated_db, games_dir):
    _setup_source_game(games_dir)
    assert agent.is_multi_file_source(SOURCE_GAME_ID, games_dir) is True
    assert agent.is_multi_file_source("no-such-id", games_dir) is False


def test_explicit_new_title_overrides_auto_numbering(isolated_db, games_dir):
    _setup_source_game(games_dir)
    responses = [_turn([("finish", {"summary": "no changes"})])]

    with mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result, _seen = _run(games_dir, responses, new_title="Punch Counter")

    assert result["success"], result["error"]
    assert result["title"] == "Punch Counter"


def test_resolve_failure_for_unknown_source_returns_clean_error(isolated_db, games_dir):
    # No source registered at all -> resolve_target fails before any
    # ask_with_tools call is made.
    with mock.patch.object(ai, "ask_with_tools") as mock_ask:
        result = agent.enhance_multifile_game(
            "no-such-id", "do something", "web:t", CONFIG, games_dir=games_dir,
        )
    mock_ask.assert_not_called()
    assert not result["success"]
    assert "no game with id" in result["error"]
