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
from tests.agent_harness import scripted_asks

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
    ask_with_tools call. scripted_asks also asserts the append-only
    invariant on the way out — see tests/agent_harness.py."""
    with scripted_asks(responses) as seen_messages:
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
    assert game["version"] == 2, "fork's version must be one more than its source's"
    assert meta["version"] == 2


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

    # The rejection for the first (oversized) write reaches the second turn.
    # The write call itself is compacted out of history (see
    # _compact_write_calls), so the rejection rides along in the assistant
    # note that replaces it rather than in a tool-result message.
    second_turn = seen[1]
    assert not any(m.get("tool_call_id") == "call_0_write_file" for m in second_turn)
    note = next(m for m in second_turn if m.get("role") == "assistant")["content"]
    assert "REJECTED" in note
    assert "500 bytes" in note
    assert "100-byte" in note

    fork_dir = games_dir / result["slug"]
    assert (fork_dir / "src" / "core.js").read_text() == NEW_CORE_JS[:50]


# ---------------------------------------------------------------------------
# 2a. Sprint 6 item D (docs/multifile-agent/06-streaming-and-polish.md): a
#     write past module_warn_bytes still succeeds, but carries a soft lint
#     note in its own observation so it survives compaction into the
#     transcript. A write comfortably under the threshold carries no note.
# ---------------------------------------------------------------------------

def test_write_past_warn_threshold_succeeds_with_a_soft_lint_note(isolated_db, games_dir):
    _setup_source_game(games_dir)
    small_cfg = {
        "game_web": CONFIG["game_web"],
        "multifile_agent": dict(CONFIG["multifile_agent"], max_module_bytes=100),
    }
    over_threshold = "x" * 60  # > module_warn_bytes (50 = half of 100), under the 100 ceiling
    responses = [
        _turn([("write_file", {"path": "core.js", "contents": over_threshold})]),
        _turn([("finish", {"summary": "a large-ish edit"})]),
    ]

    with mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result, seen = _run(games_dir, responses, config=small_cfg)

    assert result["success"], result["error"]
    # seen[-1] is the conversation as sent for the finish turn, i.e. after
    # the write_file call has already been compacted out of history.
    note = _assistant_notes(seen[-1])
    assert "OK: wrote 60 bytes" in note
    assert "getting large" in note
    assert "100-byte ceiling" in note


def test_write_under_warn_threshold_has_no_lint_note(isolated_db, games_dir):
    _setup_source_game(games_dir)
    small_cfg = {
        "game_web": CONFIG["game_web"],
        "multifile_agent": dict(CONFIG["multifile_agent"], max_module_bytes=100),
    }
    under_threshold = "x" * 10  # well under module_warn_bytes (50)
    responses = [
        _turn([("write_file", {"path": "core.js", "contents": under_threshold})]),
        _turn([("finish", {"summary": "a small edit"})]),
    ]

    with mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result, seen = _run(games_dir, responses, config=small_cfg)

    assert result["success"], result["error"]
    note = _assistant_notes(seen[-1])
    assert "OK: wrote 10 bytes" in note
    assert "getting large" not in note


# ---------------------------------------------------------------------------
# 2b. Context pruning (Sprint 6, docs/multifile-agent/06-streaming-and-polish.md):
#     the Sprint 5 pilot found the agent path used 5-12x more input tokens
#     than the single-file baseline, dominated by write_file calls' own
#     arguments (the complete new file contents) never being pruned out of
#     the resent-every-turn conversation. These tests cover the fix, and
#     step 2's correction to it: the pruning must REMOVE those calls, not
#     leave a placeholder in the arguments slot, because the model reads
#     anything left there as an example of a valid call and copies it.
# ---------------------------------------------------------------------------

def _assistant_notes(messages):
    return " ".join(
        m["content"] for m in messages
        if m.get("role") == "assistant" and isinstance(m.get("content"), str)
    )


def test_successful_write_file_is_compacted_out_of_history(isolated_db, games_dir):
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
    # already been processed, so this proves the compaction isn't undone or
    # skipped by later turns.
    later_turn = seen[-1]
    blob = json.dumps(later_turn)
    assert NEW_CORE_JS not in blob
    # Neither the call nor its tool result survives anywhere.
    assert "call_0_write_file" not in blob
    # ...but the observation does, as a plain assistant note.
    assert "OK: wrote" in _assistant_notes(later_turn)
    assert "core.js" in _assistant_notes(later_turn)


def test_rejected_write_file_is_also_compacted_out_of_history(isolated_db, games_dir):
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

    blob = json.dumps(seen[-1])
    assert huge not in blob
    assert "call_0_write_file" not in blob
    assert "REJECTED" in _assistant_notes(seen[-1])


def test_no_turn_ever_carries_a_synthetic_write_file_arguments_payload(isolated_db, games_dir):
    """The Sprint 6 step 2 regression guard. The first version of this
    pruning kept each write_file call and swapped its "contents" argument
    for a ~113-byte placeholder, leaving history showing an assistant call
    with tiny "contents" whose tool result read "OK: wrote 4840 bytes". Real
    pilot runs then had the model reproducing exactly that shape — stub
    writes of ~113-120 bytes for multi-KB modules, the stub's contents being
    the placeholder text itself. So: every write_file call that survives in
    any turn's history must carry the model's own real, unmodified
    arguments — no synthesized ones, ever."""
    _setup_source_game(games_dir)
    responses = [
        _turn([("write_file", {"path": "core.js", "contents": NEW_CORE_JS})]),
        _turn([("write_file", {"path": "style.css", "contents": "body { margin: 0; }\n"})]),
        _turn([("read_file", {"path": "core.js"})]),
        _turn([("finish", {"summary": "done"})]),
    ]

    with mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result, seen = _run(games_dir, responses)

    assert result["success"], result["error"]

    real_payloads = {NEW_CORE_JS, "body { margin: 0; }\n"}
    for messages in seen:
        for m in messages:
            if m.get("role") != "assistant":
                continue
            for entry in m.get("tool_calls") or []:
                if entry["function"]["name"] != "write_file":
                    continue
                args = json.loads(entry["function"]["arguments"])
                # A surviving write call is one the loop hasn't executed yet,
                # so its contents must be verbatim what the model produced.
                assert args["contents"] in real_payloads
                assert agent._PRUNE_SENTINEL not in args["contents"]


def test_src_prefixed_paths_collapse_instead_of_nesting(isolated_db, games_dir):
    """Agent paths are already rooted at src/. A real pilot run wrote its
    shell to BOTH 'index.html' and 'src/index.html' and every module to
    'src/*.js', producing two competing shells (src/index.html and
    src/src/index.html) with the modules under src/src/ — where the shell
    builder actually reads couldn't reference them. Both spellings must
    resolve to the same file."""
    _setup_source_game(games_dir)
    responses = [
        _turn([("write_file", {"path": "src/core.js", "contents": NEW_CORE_JS})]),
        _turn([("read_file", {"path": "core.js"})]),
        _turn([("finish", {"summary": "done"})]),
    ]

    with mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result, seen = _run(games_dir, responses)

    assert result["success"], result["error"]

    fork_dir = games_dir / result["slug"]
    assert (fork_dir / "src" / "core.js").read_text() == NEW_CORE_JS
    assert not (fork_dir / "src" / "src").exists(), "src/ prefix nested a second level"

    # The read of the bare name sees what the src/-prefixed write wrote...
    read_back = next(
        m for m in seen[-1]
        if m.get("role") == "tool" and m.get("tool_call_id") == "call_0_read_file"
    )
    assert read_back["content"] == NEW_CORE_JS
    # ...and the write's own observation reports the canonical (collapsed) path.
    assert "to core.js" in _assistant_notes(seen[1])


def test_compaction_note_states_the_write_succeeded(isolated_db, games_dir):
    """The note replaces a real tool result, so its wording is load-bearing.
    Saying the calls were "dropped" read to the model as "the write did not
    happen" — the pilot burned 38 of 40 turns re-checking state and never
    reached finish()."""
    _setup_source_game(games_dir)
    responses = [
        _turn([("write_file", {"path": "core.js", "contents": NEW_CORE_JS})]),
        _turn([("finish", {"summary": "done"})]),
    ]

    with mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result, seen = _run(games_dir, responses)

    assert result["success"], result["error"]
    note = _assistant_notes(seen[-1])
    assert "COMPLETED SUCCESSFULLY" in note
    assert "on disk" in note
    assert "OK: wrote" in note


def test_compaction_leaves_every_sent_conversation_structurally_valid(isolated_db, games_dir):
    """Compaction deletes tool calls AND their result messages. Get that
    pairing wrong and the real API 400s — but every test here mocks
    ask_with_tools, so nothing else would notice. Assert the invariant the
    API enforces: each assistant tool_call has exactly one matching tool
    result after it, each tool message answers a preceding call, and no
    message is left empty of both content and tool calls."""
    _setup_source_game(games_dir)
    responses = [
        _turn([("read_map", {})]),
        # Mixed turn: a write (compacted away) alongside a read (retained).
        _turn([
            ("write_file", {"path": "core.js", "contents": NEW_CORE_JS}),
            ("read_file", {"path": "style.css"}),
        ]),
        # Write-only turn: the assistant message loses tool_calls entirely.
        _turn([("write_file", {"path": "style.css", "contents": "body{}\n"})]),
        _turn([("finish", {"summary": "done"})]),
    ]

    with mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result, seen = _run(games_dir, responses)

    assert result["success"], result["error"]

    for messages in seen:
        answered = set()
        for i, m in enumerate(messages):
            role = m.get("role")
            if role == "assistant":
                calls = m.get("tool_calls") or []
                assert calls or (m.get("content") or "").strip(), \
                    "assistant message with neither content nor tool calls"
                assert "tool_calls" not in m or calls, "empty tool_calls list"
                for entry in calls:
                    matches = [
                        n for n in messages[i + 1:]
                        if n.get("role") == "tool" and n.get("tool_call_id") == entry["id"]
                    ]
                    assert len(matches) == 1, f"unpaired tool call {entry['id']}"
                    answered.add(entry["id"])
            elif role == "tool":
                assert m["tool_call_id"] in answered, \
                    f"tool result {m['tool_call_id']} answers no preceding call"


def test_write_of_a_pruning_placeholder_is_rejected_not_written_to_disk(isolated_db, games_dir):
    """Belt-and-braces for the same bug: if the model ever does copy a
    pruning placeholder into a write_file call, that must be rejected with
    an actionable message rather than silently corrupting the module — which
    is what turned the original stub bug into a self-reinforcing loop (the
    stub was written, read back, and re-copied) that burned 1-2.6M input
    tokens and shipped nothing."""
    _setup_source_game(games_dir)
    stub = (f"{agent._PRUNE_SENTINEL} The write_file call(s) below COMPLETED "
            "SUCCESSFULLY and the files are on disk.")
    responses = [
        _turn([("write_file", {"path": "core.js", "contents": stub})]),
        _turn([
            ("write_file", {"path": "core.js", "contents": NEW_CORE_JS}),
            ("finish", {"summary": "wrote the real core.js"}),
        ]),
    ]

    with mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result, seen = _run(games_dir, responses)

    assert result["success"], result["error"]

    note = _assistant_notes(seen[1])
    assert "REJECTED" in note
    assert "context-pruning placeholder" in note
    # The real edit landed; the stub never reached disk.
    assert (games_dir / result["slug"] / "src" / "core.js").read_text() == NEW_CORE_JS


def test_read_results_survive_verbatim_however_stale_and_however_rewritten(
        isolated_db, games_dir):
    """The Sprint 6a inversion. Sprint 6 replaced a read_file result with a
    placeholder once it went stale, or once the same path was rewritten;
    both mutated a message the model had already been sent, which invalidates
    DeepSeek's byte-exact prefix cache from that message onward for the rest
    of the run (measured: ~44,000 cached tokens back to ~4,500, twice in one
    run). A cached token costs 1/120th of a fresh one on v4-pro, so the
    retention those prunes bought was never worth the mutation they cost.

    This exercises both former prune triggers at once — style.css is read and
    then left to age five turns, core.js is read and then rewritten — and
    asserts the original observations are still present, byte for byte, in
    the final request. scripted_asks' append-only assertion covers the
    general case; this pins the specific behaviour that changed."""
    _setup_source_game(games_dir)
    style_css = (games_dir / "click-counter-src" / "src" / "style.css").read_text()
    core_js = (games_dir / "click-counter-src" / "src" / "core.js").read_text()
    responses = [
        _turn([("read_file", {"path": "style.css"})]),
        _turn([("read_file", {"path": "core.js"})]),
        _turn([("write_file", {"path": "core.js", "contents": NEW_CORE_JS})]),
        _turn([("read_map", {})]),
        _turn([("list_files", {})]),
        _turn([("search", {"pattern": "count"})]),
        _turn([("finish", {"summary": "renamed the button label"})]),
    ]

    with mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result, seen = _run(games_dir, responses)

    assert result["success"], result["error"]

    final = seen[-1]
    tool_results = [m["content"] for m in final if m.get("role") == "tool"]

    # style.css: read six turns earlier, never rewritten — the old staleness
    # sweep would have replaced this by now.
    assert any(style_css in c for c in tool_results)
    # core.js: read and then rewritten by a later write_file — the old
    # same-path prune fired on exactly this.
    assert any(core_js in c for c in tool_results)
    # No tool result anywhere carries a placeholder; only _compact_write_calls'
    # note (on an assistant message, in the turn that created it) still does.
    assert not any(agent._PRUNE_SENTINEL in c for c in tool_results)


def test_an_obsolete_context_prune_after_steps_key_is_ignored_with_a_warning(
        isolated_db, games_dir, caplog):
    """config.yaml is gitignored, so production's copy still sets this key
    and will keep setting it until someone edits that machine. It must be
    inert rather than quietly restoring the regression — and it must say so,
    because a silently ignored config key is how a stale setting survives."""
    _setup_source_game(games_dir)
    stale_cfg = {
        "game_web": CONFIG["game_web"],
        "multifile_agent": dict(CONFIG["multifile_agent"], context_prune_after_steps=2),
    }
    style_css = (games_dir / "click-counter-src" / "src" / "style.css").read_text()
    responses = [
        _turn([("read_file", {"path": "style.css"})]),
        _turn([("read_map", {})]),
        _turn([("read_map", {})]),
        _turn([("finish", {"summary": "no changes needed"})]),
    ]

    with caplog.at_level("WARNING", logger="agent"), \
         mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result, seen = _run(games_dir, responses, config=stale_cfg)

    assert result["success"], result["error"]
    assert any("context_prune_after_steps" in r.message for r in caplog.records)
    # Inert, not merely deprecated: the read is still there in full.
    tool_result = next(
        m for m in seen[-1]
        if m.get("role") == "tool" and m.get("tool_call_id") == "call_0_read_file"
    )
    assert style_css in tool_result["content"]


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


# ---------------------------------------------------------------------------
# 6. search(): the cheap alternative to re-reading a module. Job 79a0abbb
#    (2026-07-26) read 938KB of file contents across 33 read_file calls for a
#    six-file change and died before verifying, its last five turns spent
#    re-reading modules to settle whether a constant was TILE or TILE_SIZE.
# ---------------------------------------------------------------------------

def test_search_reports_matching_lines_across_every_file_with_path_and_lineno(
        isolated_db, games_dir):
    _setup_source_game(games_dir)
    responses = [
        _turn([("search", {"pattern": r"getElementById"})]),
        _turn([("finish", {"summary": "no changes needed"})]),
    ]

    with mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result, seen = _run(games_dir, responses)

    assert result["success"], result["error"]
    observation = next(
        m for m in seen[1]
        if m.get("role") == "tool" and m.get("tool_call_id") == "call_0_search"
    )["content"]
    # Two call sites in core.js, on their real line numbers.
    assert "core.js:3: var countEl = document.getElementById(\"count\");" in observation
    assert "core.js:4:" in observation
    assert "2 match(es)" in observation
    # And nowhere near the cost of reading the file it found them in.
    core_js = (games_dir / "click-counter-src" / "src" / "core.js").read_text()
    assert core_js not in observation


def test_search_can_be_scoped_to_one_file_and_reports_no_matches_plainly(
        isolated_db, games_dir):
    _setup_source_game(games_dir)
    responses = [
        # A name that exists in core.js but nowhere in style.css, so the
        # scoping is what makes the answer empty.
        _turn([("search", {"pattern": "addEventListener", "path": "style.css"})]),
        _turn([("finish", {"summary": "no changes needed"})]),
    ]

    with mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result, seen = _run(games_dir, responses)

    assert result["success"], result["error"]
    observation = next(
        m for m in seen[1]
        if m.get("role") == "tool" and m.get("tool_call_id") == "call_0_search"
    )["content"]
    assert observation == "No matches for `addEventListener` in 'style.css'."


def test_search_survives_a_bad_regex_and_a_missing_file_as_observations(
        isolated_db, games_dir):
    """Same discipline as every other tool: a bad argument comes back as an
    ERROR observation the model can correct, never as a failed run."""
    _setup_source_game(games_dir)
    responses = [
        _turn([("search", {"pattern": "unclosed ("})]),
        _turn([("search", {"pattern": "x", "path": "nope.js"})]),
        _turn([("finish", {"summary": "no changes needed"})]),
    ]

    with mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result, seen = _run(games_dir, responses)

    assert result["success"], result["error"]
    bad_regex = next(m for m in seen[1] if m.get("tool_call_id") == "call_0_search")
    assert bad_regex["content"].startswith("ERROR: not a valid regular expression")
    missing = next(m for m in seen[2] if m.get("tool_call_id") == "call_0_search"
                    and "not found" in m.get("content", ""))
    assert missing["content"] == "ERROR: 'nope.js' not found"


def test_search_output_is_capped_so_it_can_ride_along_unpruned(isolated_db, games_dir):
    _setup_source_game(games_dir)
    src = games_dir / "click-counter-src" / "src"
    (src / "core.js").write_text("var needle = 1;\n" * 500)

    responses = [
        _turn([("search", {"pattern": "needle"})]),
        _turn([("finish", {"summary": "no changes needed"})]),
    ]

    with mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result, seen = _run(games_dir, responses)

    assert result["success"], result["error"]
    observation = next(
        m for m in seen[1] if m.get("tool_call_id") == "call_0_search")["content"]
    match_lines = [ln for ln in observation.splitlines() if ln.startswith("core.js:")]
    assert len(match_lines) == agent._SEARCH_MAX_MATCHES
    assert "500 match(es)" in observation, "the real total is reported, not the shown count"
    assert f"showing the first {agent._SEARCH_MAX_MATCHES}" in observation


def test_search_echoes_the_pattern_verbatim_not_repr_escaped(isolated_db, games_dir):
    """This model imitates its own transcript (see _compact_write_calls), so a
    pattern echoed back as repr() would teach it to double-escape the next
    one into a literal backslash."""
    _setup_source_game(games_dir)
    responses = [
        _turn([("search", {"pattern": r"var\s+\w+"})]),
        _turn([("finish", {"summary": "no changes needed"})]),
    ]

    with mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result, seen = _run(games_dir, responses)

    assert result["success"], result["error"]
    observation = next(
        m for m in seen[1] if m.get("tool_call_id") == "call_0_search")["content"]
    assert r"var\s+\w+" in observation
    assert r"var\\s" not in observation


def test_list_files_flags_the_modules_this_run_already_rewrote(isolated_db, games_dir):
    """Written-ness is bookkeeping the model can't recover on its own once
    _compact_write_calls has removed its write calls from the conversation.
    Job 79a0abbb rewrote config.js five times for one feature."""
    _setup_source_game(games_dir)
    responses = [
        _turn([("list_files", {})]),                                        # before
        _turn([("write_file", {"path": "core.js", "contents": NEW_CORE_JS})]),
        _turn([("list_files", {})]),                                        # after
        _turn([("finish", {"summary": "rewrote core.js"})]),
    ]

    with mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result, seen = _run(games_dir, responses)

    assert result["success"], result["error"]
    def listings(messages):
        return [json.loads(m["content"]) for m in messages
                if m.get("tool_call_id") == "call_0_list_files"]

    before, after = listings(seen[3])   # both turns' listings, in order
    assert all("written_this_run" not in e for e in before)
    assert {e["path"]: e.get("written_this_run") for e in after} == {
        "core.js": True, "index.html": None, "style.css": None,
    }


# ---------------------------------------------------------------------------
# 7. The forced last-ditch verification. A run that wrote everything it
#    needed and then stalled used to be discarded outright — job 79a0abbb
#    burned 1.58M tokens and ~18 minutes, had all six modules correct on
#    disk, and shipped nothing because the one call it never made was a
#    deterministic build the loop can run itself.
# ---------------------------------------------------------------------------

def _reads(n, path="core.js"):
    return [_turn([("read_file", {"path": path})]) for _ in range(n)]


def _stalled_reads(n=1):
    """Enough repeated reads of one file to trip the guard n times over.
    The first read of a path is a genuinely new observation and so counts as
    progress; only the repeats after it are a stall (see _progress_key)."""
    return _reads(1 + n * agent._MAX_NO_PROGRESS_STEPS)


# CONFIG's max_steps is 10, which a stall (one write plus two full
# _MAX_NO_PROGRESS_STEPS runs of reads) outgrows — and running out of steps is
# a different exit path from being killed by the guard.
STALL_CONFIG = {
    "game_web": CONFIG["game_web"],
    "multifile_agent": dict(CONFIG["multifile_agent"], max_steps=30),
}


def test_a_long_exploration_before_the_first_write_is_not_a_stall(
        isolated_db, games_dir):
    """Job 73df2b10 (2026-07-27) was killed on turn 5 of a healthy enhance:
    read_map, nine read_file calls across a 13-module game, one search — no
    repeats, and its own reasoning showed it had finished planning and was
    about to write. Reading a file you have not read is progress; the guard
    counts repetition, not turns."""
    _setup_source_game(games_dir)
    responses = [
        _turn([("read_map", {})]),
        _turn([("list_files", {})]),
        _turn([("read_file", {"path": "core.js"})]),
        _turn([("read_file", {"path": "style.css"})]),
        _turn([("read_file", {"path": "index.html"})]),
        _turn([("search", {"pattern": "purpleBlock"})]),
        _turn([("search", {"pattern": "addEventListener"})]),
        _turn([("write_file", {"path": "core.js", "contents": NEW_CORE_JS})]),
        _turn([("finish", {"summary": "explored, then wrote"})]),
    ]
    assert len(responses) - 2 > agent._MAX_NO_PROGRESS_STEPS, \
        "the exploration phase must outlast the guard for this to test anything"

    with mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result, seen = _run(games_dir, responses, config=STALL_CONFIG)

    assert result["success"], result["error"]
    assert result["notes"] == "explored, then wrote"
    assert not [m for m in seen[-1] if m.get("role") == "user"
                and "turns without" in (m.get("content") or "")], \
        "a run that never repeated itself should never have been nudged"


def test_repeating_a_search_is_not_progress_but_a_new_one_is(isolated_db, games_dir):
    """The guard has to stay able to catch a run going in circles — the
    same query re-asked teaches it nothing, however cheap the turn."""
    _setup_source_game(games_dir)
    same = [_turn([("search", {"pattern": "count"})])
            for _ in range(agent._MAX_NO_PROGRESS_STEPS * 2 + 1)]

    with mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result, _seen = _run(games_dir, same, config=STALL_CONFIG)

    assert not result["success"]
    assert "no progress" in result["error"]


def test_a_stalled_run_is_verified_before_being_discarded_and_ships_if_it_passes(
        isolated_db, games_dir):
    _setup_source_game(games_dir)
    responses = (
        [_turn([("write_file", {"path": "core.js", "contents": NEW_CORE_JS})])]
        # Enough read-only turns to burn the re-armed nudge and then stall out.
        + _reads(agent._MAX_NO_PROGRESS_STEPS * 2 + 2)
    )

    with mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result, _seen = _run(games_dir, responses, config=STALL_CONFIG, job_id="job-stall")

    assert result["success"], result["error"]
    assert (games_dir / result["slug"] / "src" / "core.js").read_text() == NEW_CORE_JS
    assert "without calling finish" in result["notes"]
    # Recorded like any other verification attempt, so the job's history
    # shows what actually happened.
    assert [a["outcome"] for a in db.get_generation_attempts("job-stall")] == ["success"]


def test_a_stalled_run_that_fails_verification_reports_the_real_defect(
        isolated_db, games_dir):
    """"smoke test failed: ..." is actionable; "agent made no progress" is
    not. The stall stays in the message as context for why the run ended."""
    _setup_source_game(games_dir)
    responses = (
        [_turn([("write_file", {"path": "core.js", "contents": NEW_CORE_JS})])]
        + _reads(agent._MAX_NO_PROGRESS_STEPS * 2 + 2)
    )

    with mock.patch("smoke_test.run_smoke_test", return_value=(False, "console.error: boom")):
        result, _seen = _run(games_dir, responses, config=STALL_CONFIG)

    assert not result["success"]
    assert "console.error: boom" in result["error"]
    assert "never called finish" in result["error"]
    assert "no progress" in result["error"]
    # Still rolled back — a failed run leaves no half-written fork.
    assert {p.name for p in games_dir.iterdir()} == {"click-counter-src"}


def test_a_run_that_never_wrote_a_file_is_not_force_verified(isolated_db, games_dir):
    """Nothing was written, so the staged fork is a byte-copy of the source:
    verifying it would 'pass' and ship a pointless duplicate game."""
    _setup_source_game(games_dir)
    # Two stalls: the first is answered by the write nudge, the second aborts.
    responses = _stalled_reads(2)

    with mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")) as smoke:
        result, _seen = _run(games_dir, responses, config=STALL_CONFIG,
                             job_id="job-nowrite")

    assert not result["success"]
    assert "no progress" in result["error"]
    smoke.assert_not_called()
    assert db.get_generation_attempts("job-nowrite") == []


def test_exhausting_the_verification_retries_does_not_buy_one_more_attempt(
        isolated_db, games_dir):
    """max_verification_retries is the ceiling on build->scan->smoke runs;
    the forced attempt must respect it rather than adding a free one."""
    _setup_source_game(games_dir)
    small_cfg = {
        "game_web": CONFIG["game_web"],
        "multifile_agent": dict(CONFIG["multifile_agent"], max_verification_retries=2),
    }
    responses = [
        _turn([("write_file", {"path": "core.js", "contents": NEW_CORE_JS}),
               ("finish", {"summary": "attempt 1"})]),
        _turn([("finish", {"summary": "attempt 2"})]),
    ]

    with mock.patch("smoke_test.run_smoke_test",
                     return_value=(False, "console.error: still broken")) as smoke:
        result, _seen = _run(games_dir, responses, config=small_cfg)

    assert not result["success"]
    assert smoke.call_count == 2
    assert result["attempts"] == 2


def test_an_ai_error_after_the_work_is_done_still_verifies_what_was_written(
        isolated_db, games_dir):
    """A transport failure on the turn that would have called finish is not a
    reason to throw away a complete, correct edit."""
    _setup_source_game(games_dir)
    responses = [_turn([("write_file", {"path": "core.js", "contents": NEW_CORE_JS})])]

    def scripted(messages, **_kwargs):
        if responses:
            return responses.pop(0)
        raise ai.AIError("connection reset")

    with mock.patch.object(ai, "ask_with_tools", side_effect=scripted), \
         mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result = agent.enhance_multifile_game(
            SOURCE_GAME_ID, "make the button say Punch", "web:t", CONFIG,
            games_dir=games_dir,
        )

    assert result["success"], result["error"]
    assert (games_dir / result["slug"] / "src" / "core.js").read_text() == NEW_CORE_JS


def test_a_later_stall_gets_its_own_nudge_after_a_real_write(isolated_db, games_dir):
    """The nudge answers one specific stall. Job 79a0abbb spent its only
    nudge on an early review pause, wrote six more files, and was killed at
    the next pause — so a successful write re-arms it."""
    _setup_source_game(games_dir)
    responses = (
        [_turn([("write_file", {"path": "core.js", "contents": NEW_CORE_JS})])]
        + _stalled_reads()                          # stall 1 -> nudged
        + [_turn([("write_file", {"path": "core.js", "contents": NEW_CORE_JS})])]
        + _stalled_reads()                          # stall 2 -> nudged again
        + [_turn([("finish", {"summary": "done after two review passes"})])]
    )

    with mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result, seen = _run(games_dir, responses, config=STALL_CONFIG)

    assert result["success"], result["error"]
    assert result["notes"] == "done after two review passes"
    stall_nudges = [
        m for m in seen[-1]
        if m.get("role") == "user" and "turns without writing a file" in (m.get("content") or "")
    ]
    assert len(stall_nudges) == 2, "each stall after real progress earns its own nudge"


# ---------------------------------------------------------------------------
# 8. The re-finish budget nudge. Job d6ca3a88 (2026-07-27) fixed a failed
#    finish at ~step 54 of 60, then burned its last turns re-reading to check
#    the fix by hand instead of calling finish again — the forced verification
#    shipped it, but a nudge would have converged it cleanly. The low-budget
#    warning it needed was gated on verification_attempts == 0, so a run that
#    had already failed one finish could never get it.
#
#    CONFIG's max_steps is 10, so _finish_nudge_threshold is max(5, 10//4) == 5:
#    the nudge fires once steps_left <= 5, i.e. from step 5 on.
# ---------------------------------------------------------------------------

_REFINISH_NUDGE_MARK = "you have since edited files to fix it"


def _refinish_nudges(messages):
    return [m for m in messages if m.get("role") == "user"
            and _REFINISH_NUDGE_MARK in (m.get("content") or "")]


def test_a_failed_finish_then_an_edit_near_budget_end_nudges_to_refinish(
        isolated_db, games_dir):
    _setup_source_game(games_dir)
    responses = [
        _turn([("write_file", {"path": "core.js", "contents": NEW_CORE_JS})]),  # 1
        _turn([("finish", {"summary": "first attempt"})]),                      # 2 -> fails
        _turn([("write_file", {"path": "core.js", "contents": NEW_CORE_JS})]),  # 3 fix
        _turn([("read_file", {"path": "style.css"})]),                          # 4 steps_left 6
        _turn([("read_file", {"path": "index.html"})]),                         # 5 steps_left 5 -> nudge
        _turn([("finish", {"summary": "fixed on retry"})]),                     # 6 -> passes
    ]

    with mock.patch("smoke_test.run_smoke_test",
                     side_effect=[(False, "console.error: boom"), (True, "ok")]):
        result, seen = _run(games_dir, responses, config=CONFIG, job_id="job-refinish")

    assert result["success"], result["error"]
    # Two real verifications (fail then pass) — NOT the forced last-ditch one.
    assert result["attempts"] == 2
    assert result["notes"] == "fixed on retry"
    assert [a["outcome"] for a in db.get_generation_attempts("job-refinish")] == \
        ["smoke_test_failed", "success"]
    # The nudge was delivered on the turn after step 5, so it is in the
    # conversation sent to the finishing call.
    assert len(_refinish_nudges(seen[-1])) == 1, \
        "a failed finish + a later edit near budget end must nudge to re-finish"


def test_the_refinish_nudge_re_arms_after_each_failed_finish(isolated_db, games_dir):
    """It answers one specific fix-cycle. A run that fails finish, fixes it and
    is nudged, then fails finish AGAIN and fixes it again must earn a second
    nudge — same re-arming logic as the stall nudge."""
    _setup_source_game(games_dir)
    responses = [
        _turn([("write_file", {"path": "core.js", "contents": NEW_CORE_JS})]),  # 1
        _turn([("finish", {"summary": "attempt 1"})]),                          # 2 -> fails
        _turn([("write_file", {"path": "core.js", "contents": NEW_CORE_JS})]),  # 3 fix
        _turn([("read_file", {"path": "style.css"})]),                          # 4
        _turn([("read_file", {"path": "index.html"})]),                         # 5 -> nudge 1
        _turn([("finish", {"summary": "attempt 2"})]),                          # 6 -> fails, re-arm
        _turn([("write_file", {"path": "core.js", "contents": NEW_CORE_JS})]),  # 7 fix again
        _turn([("read_file", {"path": "style.css"})]),                          # 8 -> nudge 2
        _turn([("finish", {"summary": "attempt 3"})]),                          # 9 -> passes
    ]

    with mock.patch("smoke_test.run_smoke_test",
                     side_effect=[(False, "boom"), (False, "boom"), (True, "ok")]):
        result, seen = _run(games_dir, responses, config=CONFIG)

    assert result["success"], result["error"]
    assert result["attempts"] == 3
    assert len(_refinish_nudges(seen[-1])) == 2, \
        "each failed finish followed by an edit earns its own re-finish nudge"


def test_the_refinish_nudge_does_not_fire_before_a_finish_has_failed(
        isolated_db, games_dir):
    """The verification_attempts == 0 path owns the pre-first-finish case; the
    re-finish nudge must stay silent until a finish has actually failed."""
    _setup_source_game(games_dir)
    # Write, then read-only turns deep into the budget, then finish — a first
    # finish never fails here, so the re-finish nudge has no cycle to answer.
    responses = (
        [_turn([("write_file", {"path": "core.js", "contents": NEW_CORE_JS})])]
        + [_turn([("read_file", {"path": p})])
           for p in ("style.css", "index.html", "game.md")]
        + [_turn([("finish", {"summary": "done"})])]
    )

    with mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result, seen = _run(games_dir, responses, config=CONFIG)

    assert result["success"], result["error"]
    assert _refinish_nudges(seen[-1]) == [], \
        "no re-finish nudge before any finish has failed"


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
