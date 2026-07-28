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

    # No turn's conversation ever contained the whole ASSEMBLED game — the
    # built index.html with every module inlined, which is exactly the payload
    # the single-file path has to re-emit and this one exists to avoid. The
    # split source itself is in the system prompt by design since Sprint 6a
    # (src/index.html is a shell of <script> tags, not the game), so the check
    # is against the built artifact, not against "<html".
    built = (fork_dir / "index.html").read_text()
    body = built[built.index("<body"):]
    for messages in seen:
        for m in messages:
            content = m.get("content")
            if isinstance(content, str):
                assert body not in content

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
    arguments — no synthesized ones, ever. Sprint 6a extends the same rule to
    edit_file: a small edit is deliberately RETAINED, so its arguments have to
    be the model's own, and a large one is removed outright."""
    _setup_source_game(games_dir)
    responses = [
        _turn([("write_file", {"path": "core.js", "contents": NEW_CORE_JS})]),
        _turn([("write_file", {"path": "style.css", "contents": "body { margin: 0; }\n"})]),
        _turn([("edit_file", {"path": "style.css", "old_string": "margin: 0",
                              "new_string": "margin: 1px"})]),
        _turn([("read_file", {"path": "core.js"})]),
        _turn([("finish", {"summary": "done"})]),
    ]

    with mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result, seen = _run(games_dir, responses)

    assert result["success"], result["error"]

    real_payloads = {NEW_CORE_JS, "body { margin: 0; }\n"}
    real_edits = {("margin: 0", "margin: 1px")}
    for messages in seen:
        for m in messages:
            if m.get("role") != "assistant":
                continue
            for entry in m.get("tool_calls") or []:
                name = entry["function"]["name"]
                args = json.loads(entry["function"]["arguments"])
                if name == "write_file":
                    # A surviving write call is one the loop hasn't executed
                    # yet, so its contents must be verbatim what the model
                    # produced.
                    assert args["contents"] in real_payloads
                    assert agent._PRUNE_SENTINEL not in args["contents"]
                elif name == "edit_file":
                    assert (args["old_string"], args["new_string"]) in real_edits
                    assert agent._PRUNE_SENTINEL not in args["old_string"]
                    assert agent._PRUNE_SENTINEL not in args["new_string"]


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
    # Sprint 6a: the note also reconciles the write against the source
    # snapshot in the system prompt. Naming the superseded file is only half
    # of it — the closing sentence is what stops the model generalising one
    # stale file into a stale snapshot and re-reading the whole game.
    assert "core.js" in note
    assert "Every other file in the snapshot is still exactly as shown there." in note
    assert "style.css" not in note


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
        # A retry has to actually change something — a finish that edits
        # nothing is bounced without a build (see the no-op bounce tests).
        _turn([
            ("write_file", {"path": "core.js",
                            "contents": NEW_CORE_JS.replace("count += 1", "count += 2")}),
            ("finish", {"summary": "second attempt, fixed"}),
        ]),
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
        _turn([("write_file", {"path": "core.js", "contents": NEW_CORE_JS}),
               ("finish", {"summary": "attempt 1"})]),
        _turn([("write_file", {"path": "core.js",
                               "contents": NEW_CORE_JS.replace("count += 1", "count += 2")}),
               ("finish", {"summary": "attempt 2"})]),
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


def test_search_windows_a_long_line_around_the_match(isolated_db, games_dir):
    """The bug that cost job 837b2b8c 20 of its 60 turns: on a line thousands
    of characters long, search reported a match and then showed the first 200
    characters, which did not contain it. The model could not build an
    edit_file old_string from that, re-read the whole 96KB module three times,
    and ran out of steps mid-change."""
    _setup_source_game(games_dir)
    game_dir = games_dir / "click-counter-src"
    filler = "var padding = 'xxxxxxxxxx'; "
    long_line = filler * 100 + "var needle = 42; " + filler * 100
    (game_dir / "src" / "core.js").write_text(long_line + "\n")

    out = agent._search(game_dir, "needle", "core.js")

    assert "var needle = 42;" in out, "the window must contain the match itself"
    line = next(ln for ln in out.splitlines() if ln.startswith("core.js:"))
    assert line.startswith("core.js:1: …"), "trimmed on the left, not from char 0"
    assert f"of {len(long_line)}]" in line, "the window states where it sits"
    # Still bounded — a search result rides along unpruned for the whole run.
    assert len(line) < agent._SEARCH_MAX_LINE_CHARS + 120


def test_search_leaves_a_line_that_fits_exactly_as_it_was(isolated_db, games_dir):
    """The overwhelming majority of matches; their output must not change,
    and a run that sees no long lines must not carry the windowing note."""
    _setup_source_game(games_dir)
    game_dir = games_dir / "click-counter-src"
    (game_dir / "src" / "core.js").write_text("    var needle = 1;\n")

    out = agent._search(game_dir, "needle", "core.js")

    assert out == "1 match(es) for `needle` in 'core.js':\ncore.js:1: var needle = 1;"


def test_search_explains_its_windowing_once_not_per_line(isolated_db, games_dir):
    """'…' is the one piece of scaffolding here the model could copy into an
    edit_file old_string, so it is spelled out — but once, in the header."""
    _setup_source_game(games_dir)
    game_dir = games_dir / "click-counter-src"
    long_line = "var padding = 'x'; " * 200 + "var needle = 1;"
    (game_dir / "src" / "core.js").write_text((long_line + "\n") * 3)

    out = agent._search(game_dir, "needle", "core.js")

    assert out.count("do not include them in an edit_file") == 1
    assert len([ln for ln in out.splitlines() if ln.startswith("core.js:")]) == 3


def test_search_anchors_at_the_match_when_the_match_is_wider_than_the_window(
        isolated_db, games_dir):
    _setup_source_game(games_dir)
    game_dir = games_dir / "click-counter-src"
    (game_dir / "src" / "core.js").write_text("lead " + ("needle " * 200) + "tail\n")

    out = agent._search(game_dir, "(needle ){200}", "core.js")

    line = next(ln for ln in out.splitlines() if ln.startswith("core.js:"))
    assert "needle" in line
    assert line.endswith("]")


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
    assert not result["complete"]
    assert "ran out of turns before confirming it was done" in result["notes"]
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
        _turn([("write_file", {"path": "core.js",
                               "contents": NEW_CORE_JS.replace("count += 1", "count += 2")}),
               ("finish", {"summary": "attempt 2"})]),
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


# ---------------------------------------------------------------------------
# Sprint 6a step 2: the source snapshot
# ---------------------------------------------------------------------------

def _snapshot_of(games_dir, max_bytes=agent.DEFAULT_SNAPSHOT_MAX_BYTES):
    return agent._build_source_snapshot(games_dir / "click-counter-src", max_bytes)


def test_snapshot_carries_every_file_verbatim_in_a_deterministic_order(
        isolated_db, games_dir):
    """The snapshot replaces read_file for pre-existing files, so 'byte-for-byte
    identical to what you were shown' has to be literally true — and the order
    has to be fixed, because a stable system message is what makes a second
    enhance of the same source a prefix-cache hit."""
    _setup_source_game(games_dir)
    src = games_dir / "click-counter-src"
    snap = _snapshot_of(games_dir)

    assert snap.text is not None
    assert snap.file_count == 4  # game.md + src/{index.html,core.js,style.css}
    assert snap.paths == frozenset({"game.md", "index.html", "core.js", "style.css"})

    for rel, path in (("game.md", src / "game.md"),
                      ("index.html", src / "src" / "index.html"),
                      ("core.js", src / "src" / "core.js"),
                      ("style.css", src / "src" / "style.css")):
        contents = path.read_text()
        size = len(contents.encode("utf-8"))
        begin = f"===== BEGIN {rel} ({size} bytes) ====="
        assert begin in snap.text, rel
        block = snap.text.split(begin + "\n", 1)[1].split(f"===== END {rel} =====", 1)[0]
        assert block == contents, rel

    # game.md first (it's the map), then the index.html shell (whose <script>
    # order IS the dependency order), then the rest by posix path.
    order = [snap.text.index(f"===== BEGIN {p} ")
             for p in ("game.md", "index.html", "core.js", "style.css")]
    assert order == sorted(order)


def test_snapshot_is_byte_identical_across_two_builds_of_the_same_source(
        isolated_db, games_dir):
    """A single run-specific byte anywhere in the system message — a timestamp,
    a job id, the fork slug — costs the whole block's cross-run cache hit, and
    would do it silently. This is the guard against someone interpolating one."""
    _setup_source_game(games_dir)
    assert _snapshot_of(games_dir).text == _snapshot_of(games_dir).text

    # And it must not carry the directory it was read from: enhance reads from
    # the freshly-minted fork, whose slug differs on every single run.
    fork = games_dir / "some-other-slug-deadbeef"
    shutil.copytree(games_dir / "click-counter-src", fork)
    assert agent._build_source_snapshot(
        fork, agent.DEFAULT_SNAPSHOT_MAX_BYTES).text == _snapshot_of(games_dir).text


def test_snapshot_above_the_ceiling_degrades_to_a_manifest_not_a_truncation(
        isolated_db, games_dir):
    """A partial snapshot is worse than none: the model cannot tell which half
    it is missing, and would trust the half it has. The manifest still costs a
    few stable bytes and still saves the list_files turn."""
    _setup_source_game(games_dir)
    snap = _snapshot_of(games_dir, max_bytes=10)

    assert snap.text is None
    assert snap.total_bytes > 10
    assert "game.md" in snap.manifest and "core.js" in snap.manifest

    prompt = agent._build_system_prompt("Click Counter", snap)
    assert "===== BEGIN" not in prompt
    assert snap.manifest in prompt
    # Back to the discovery wording, not the "you already have all of it" one.
    assert "Explore before you edit" in prompt


def test_system_prompt_tells_the_model_the_snapshot_stays_authoritative(
        isolated_db, games_dir):
    """Wording is load-bearing here in the same way _compact_write_calls' note
    is. A bare hedge ('this may be out of date') reads as 'nothing here is
    trustworthy' and triggers a re-verification sweep; the prompt has to say
    what is still true and then name the exceptions."""
    _setup_source_game(games_dir)
    prompt = agent._build_system_prompt("Click Counter", _snapshot_of(games_dir))

    assert "stays authoritative for every file you do not change" in prompt
    assert "Do NOT call read_map, list_files or read_file" in prompt
    assert "scaffolding" in prompt          # the marker rule
    assert "may be out of date" not in prompt


def test_a_write_containing_a_snapshot_marker_is_rejected_and_never_hits_disk(
        isolated_db, games_dir):
    """The snapshot introduces new scaffolding the model can copy into a file,
    which is the exact failure mode of the stub-write disaster and the dropped
    'path' key. A marker line as the first line of a module is a syntax error
    the moment the build inlines it — so reject loudly rather than write it."""
    _setup_source_game(games_dir)
    bad = f"===== BEGIN core.js (123 bytes) =====\n{NEW_CORE_JS}===== END core.js ====="
    responses = [
        _turn([("write_file", {"path": "core.js", "contents": bad})]),
        _turn([
            ("write_file", {"path": "core.js", "contents": NEW_CORE_JS}),
            ("finish", {"summary": "wrote the real core.js"}),
        ]),
    ]

    with mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result, seen = _run(games_dir, responses)

    assert result["success"], result["error"]
    note = _assistant_notes(seen[1])
    assert "REJECTED" in note and "scaffolding" in note
    assert (games_dir / result["slug"] / "src" / "core.js").read_text() == NEW_CORE_JS


def test_reading_an_unmodified_snapshot_file_is_answered_with_a_nudge(
        isolated_db, games_dir):
    """Still returns the full contents — withholding information is what sends
    this agent into state-re-checking loops. It just says the read was free of
    new information. A file the run has rewritten gets no nudge: there the
    snapshot really is superseded."""
    _setup_source_game(games_dir)
    style_css = (games_dir / "click-counter-src" / "src" / "style.css").read_text()
    responses = [
        _turn([("read_file", {"path": "style.css"})]),
        _turn([("write_file", {"path": "core.js", "contents": NEW_CORE_JS})]),
        _turn([("read_file", {"path": "core.js"})]),
        _turn([("finish", {"summary": "done"})]),
    ]

    with mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result, seen = _run(games_dir, responses)

    assert result["success"], result["error"]
    reads = [m["content"] for m in seen[-1] if m.get("role") == "tool"
             and m.get("content", "").find("NOTE:") == 0]
    assert len(reads) == 1, "only the unmodified read should be nudged"
    assert "told you nothing new" in reads[0]
    assert style_css in reads[0], "the full contents still come back"

    rewritten = [m["content"] for m in seen[-1] if m.get("role") == "tool"
                 and NEW_CORE_JS in m.get("content", "")]
    assert rewritten and not any("NOTE:" in c for c in rewritten)


def test_the_snapshot_is_emitted_as_one_summary_event_never_the_body(
        isolated_db, games_dir):
    """agent_events is a permanent archive that the chat pane replays from, so
    a few hundred KB of source per job would bloat both for nothing anyone
    would read there."""
    _setup_source_game(games_dir)
    events = []
    responses = [_turn([("finish", {"summary": "no changes"})])]

    with mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result, _seen = _run(
            games_dir, responses,
            emit=lambda role, content=None, data=None: events.append((role, content, data)),
        )

    assert result["success"], result["error"]
    snaps = [e for e in events if (e[2] or {}).get("tool") == "snapshot"]
    assert len(snaps) == 1
    role, content, data = snaps[0]
    assert role == "tool_result"
    assert content == "Loaded source snapshot: 4 files, {:,} bytes".format(data["bytes"])
    assert data["file_count"] == 4 and data["included"] is True
    assert "===== BEGIN" not in content


# ---------------------------------------------------------------------------
# Sprint 6a step 3: edit_file — exact match, exactly once
#
# The point of the tool is cost: a one-line change to a 73KB module used to
# cost 73KB of OUTPUT tokens (the expensive, per-response-capped kind). The
# point of its strictness is that the model cannot see the result of a
# mis-applied edit — every rejection below leaves the file byte-identical on
# disk, and none of them ever shows a near-miss, which would teach exactly the
# fuzzy matching the tool refuses to do.
# ---------------------------------------------------------------------------

def _tool_results(messages):
    return [m["content"] for m in messages if m.get("role") == "tool"]


def _edit_config(**overrides):
    return {
        "game_web": CONFIG["game_web"],
        "multifile_agent": dict(CONFIG["multifile_agent"], **overrides),
    }


def test_edit_file_applies_a_unique_match_and_changes_nothing_else(
        isolated_db, games_dir):
    _setup_source_game(games_dir)
    original = (games_dir / "click-counter-src" / "src" / "core.js").read_text()
    responses = [
        _turn([("edit_file", {"path": "core.js",
                              "old_string": "count += 1;",
                              "new_string": "count += 2;"})]),
        _turn([("finish", {"summary": "count by twos"})]),
    ]

    with mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result, seen = _run(games_dir, responses)

    assert result["success"], result["error"]
    fork = games_dir / result["slug"]
    assert (fork / "src" / "core.js").read_text() == original.replace(
        "count += 1;", "count += 2;")
    # Only that span moved; every other file is untouched.
    assert (fork / "src" / "style.css").read_text() == (
        games_dir / "click-counter-src" / "src" / "style.css").read_text()

    ok = [c for c in _tool_results(seen[-1]) if c.startswith("OK: edited")]
    assert len(ok) == 1
    assert "core.js" in ok[0]


def test_an_old_string_that_does_not_match_is_rejected_and_the_file_untouched(
        isolated_db, games_dir):
    """A rejection costs one cheap turn. A guessed match costs a broken game
    that may still pass build, scan and smoke — so nothing is guessed."""
    _setup_source_game(games_dir)
    original = (games_dir / "click-counter-src" / "src" / "core.js").read_text()
    responses = [
        _turn([("edit_file", {"path": "core.js",
                              "old_string": "count+=1;",   # real text has spaces
                              "new_string": "count += 2;"})]),
        _turn([("finish", {"summary": "nothing changed"})]),
    ]

    with mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result, seen = _run(games_dir, responses)

    assert result["success"], result["error"]
    assert (games_dir / result["slug"] / "src" / "core.js").read_text() == original

    rejection = next(c for c in _tool_results(seen[-1]) if c.startswith("REJECTED:"))
    assert "does not appear" in rejection
    assert "search(pattern" in rejection      # points at the cheap way to find it
    # Never a near-miss, a "did you mean", or a candidate string echoed back —
    # all three teach the fuzzy matching this tool exists to refuse.
    assert "did you mean" not in rejection.lower()
    assert "count += 1;" not in rejection


def test_an_ambiguous_old_string_is_rejected_and_the_file_untouched(
        isolated_db, games_dir):
    """Neither first-match nor all-matches is safe when the model cannot see
    the result: it has to say which one it means."""
    _setup_source_game(games_dir)
    original = (games_dir / "click-counter-src" / "src" / "core.js").read_text()
    assert original.count("  var ") == 3
    responses = [
        _turn([("edit_file", {"path": "core.js",
                              "old_string": "  var ", "new_string": "  let "})]),
        _turn([("finish", {"summary": "nothing changed"})]),
    ]

    with mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result, seen = _run(games_dir, responses)

    assert result["success"], result["error"]
    assert (games_dir / result["slug"] / "src" / "core.js").read_text() == original

    rejection = next(c for c in _tool_results(seen[-1]) if c.startswith("REJECTED:"))
    assert "appears 3 times" in rejection
    assert "Extend 'old_string'" in rejection


def test_an_empty_old_string_is_rejected_and_points_at_write_file(
        isolated_db, games_dir):
    _setup_source_game(games_dir)
    original = (games_dir / "click-counter-src" / "src" / "core.js").read_text()
    responses = [
        _turn([("edit_file", {"path": "core.js",
                              "old_string": "", "new_string": "// hello\n"})]),
        _turn([("finish", {"summary": "nothing changed"})]),
    ]

    with mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result, seen = _run(games_dir, responses)

    assert result["success"], result["error"]
    assert (games_dir / result["slug"] / "src" / "core.js").read_text() == original
    rejection = next(c for c in _tool_results(seen[-1]) if c.startswith("REJECTED:"))
    assert "was empty" in rejection and "write_file" in rejection


def test_editing_a_file_that_does_not_exist_points_at_write_file(
        isolated_db, games_dir):
    _setup_source_game(games_dir)
    responses = [
        _turn([("edit_file", {"path": "hud.js",
                              "old_string": "a", "new_string": "b"})]),
        _turn([("finish", {"summary": "nothing changed"})]),
    ]

    with mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result, seen = _run(games_dir, responses)

    assert result["success"], result["error"]
    assert not (games_dir / result["slug"] / "src" / "hud.js").exists()
    err = next(c for c in _tool_results(seen[-1]) if c.startswith("ERROR:"))
    assert "not found" in err and "write_file" in err


def test_an_edit_that_changes_nothing_is_rejected_and_is_not_progress(
        isolated_db, games_dir):
    """A no-op edit would otherwise burn a step AND register as progress
    against the stall guard, which is how a run goes in circles forever."""
    _setup_source_game(games_dir)
    noop = _turn([("edit_file", {"path": "core.js",
                                 "old_string": "count += 1;",
                                 "new_string": "count += 1;"})])
    # Two full runs of the guard: the first trip spends the one nudge, the
    # second ends the run. If a no-op edit counted as progress, none of this
    # would ever fire.
    responses = [noop] * (agent._MAX_NO_PROGRESS_STEPS * 2 + 1)

    with mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result, seen = _run(games_dir, responses, config=_edit_config(max_steps=30))

    assert not result["success"]
    assert "no progress" in result["error"]
    rejection = next(c for c in _tool_results(seen[-1]) if c.startswith("REJECTED:"))
    assert "identical to 'old_string'" in rejection


def test_an_empty_new_string_deletes_the_matched_span(isolated_db, games_dir):
    _setup_source_game(games_dir)
    original = (games_dir / "click-counter-src" / "src" / "core.js").read_text()
    responses = [
        _turn([("edit_file", {"path": "core.js",
                              "old_string": "  var count = 0;\n", "new_string": ""})]),
        _turn([("write_file", {"path": "core.js", "contents": NEW_CORE_JS}),
               ("finish", {"summary": "done"})]),
    ]

    with mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result, seen = _run(games_dir, responses)

    assert result["success"], result["error"]
    # The deletion landed before the later whole-file rewrite replaced it.
    deleted = next(c for c in _tool_results(seen[1]) if c.startswith("OK: edited"))
    assert "-17 +0 bytes" in deleted
    assert original.replace("  var count = 0;\n", "") != original


def test_an_edit_over_the_module_ceiling_is_rejected_and_the_file_untouched(
        isolated_db, games_dir):
    """The ceiling is checked against the RESULT, not the payload — a tiny
    edit can still push a module over — and the file is left alone."""
    _setup_source_game(games_dir)
    small = "var a = 1;\n"
    responses = [
        _turn([("write_file", {"path": "core.js", "contents": small})]),
        _turn([("edit_file", {"path": "core.js",
                              "old_string": "var a = 1;", "new_string": "x" * 120})]),
        _turn([("finish", {"summary": "done"})]),
    ]

    with mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result, seen = _run(games_dir, responses,
                            config=_edit_config(max_module_bytes=100))

    assert result["success"], result["error"]
    assert (games_dir / result["slug"] / "src" / "core.js").read_text() == small
    rejection = next(c for c in _tool_results(seen[-1]) if c.startswith("REJECTED:"))
    assert "over the 100-byte module size ceiling" in rejection
    assert "unchanged" in rejection


def test_snapshot_scaffolding_in_either_edit_string_is_rejected(
        isolated_db, games_dir):
    """Same guard as write_file's, on both strings. A marker in new_string
    would corrupt the file; a marker in old_string is the model treating the
    snapshot listing as file content, one step earlier."""
    _setup_source_game(games_dir)
    original = (games_dir / "click-counter-src" / "src" / "core.js").read_text()
    responses = [
        _turn([("edit_file", {"path": "core.js", "old_string": "count += 1;",
                              "new_string": "===== BEGIN core.js (12 bytes) ====="})]),
        _turn([("edit_file", {"path": "core.js",
                              "old_string": "===== END core.js =====",
                              "new_string": "count += 2;"})]),
        _turn([("edit_file", {"path": "core.js",
                              "old_string": "count += 1;",
                              "new_string": agent._PRUNE_SENTINEL})]),
        _turn([("finish", {"summary": "nothing changed"})]),
    ]

    with mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result, seen = _run(games_dir, responses)

    assert result["success"], result["error"]
    assert (games_dir / result["slug"] / "src" / "core.js").read_text() == original
    rejections = [c for c in _tool_results(seen[-1]) if c.startswith("REJECTED:")]
    assert len(rejections) == 3
    assert "new_string" in rejections[0] and "scaffolding" in rejections[0]
    assert "old_string" in rejections[1] and "scaffolding" in rejections[1]
    assert "context-pruning placeholder" in rejections[2]


def test_a_successful_edit_shows_as_written_this_run_in_a_later_list_files(
        isolated_db, games_dir):
    """Same bookkeeping write_file gets: the model otherwise cannot tell an
    edited module from an untouched one without reading it."""
    _setup_source_game(games_dir)
    responses = [
        _turn([("edit_file", {"path": "core.js", "old_string": "count += 1;",
                              "new_string": "count += 3;"})]),
        _turn([("list_files", {})]),
        _turn([("finish", {"summary": "done"})]),
    ]

    with mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result, seen = _run(games_dir, responses)

    assert result["success"], result["error"]
    listing = json.loads(next(m["content"] for m in seen[-1]
                              if m.get("tool_call_id") == "call_0_list_files"))
    assert {e["path"]: e.get("written_this_run") for e in listing} == {
        "core.js": True, "index.html": None, "style.css": None,
    }


def test_a_small_edit_keeps_its_real_arguments_in_history(isolated_db, games_dir):
    """The divergence from write_file, and it is deliberate. Small edits are
    small by construction and ride at the cached rate once sent; leaving them
    is what makes "this file is the snapshot's version with my edits applied,
    in order" checkable from the transcript instead of taken on faith."""
    _setup_source_game(games_dir)
    responses = [
        _turn([("edit_file", {"path": "core.js", "old_string": "count += 1;",
                              "new_string": "count += 4;"})]),
        _turn([("read_map", {})]),
        _turn([("finish", {"summary": "done"})]),
    ]

    with mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result, seen = _run(games_dir, responses)

    assert result["success"], result["error"]
    blob = json.dumps(seen[-1])
    assert "call_0_edit_file" in blob, "a small edit must survive in history"
    call = next(
        e for m in seen[-1] if m.get("role") == "assistant"
        for e in (m.get("tool_calls") or [])
        if e["function"]["name"] == "edit_file"
    )
    args = json.loads(call["function"]["arguments"])
    assert args == {"path": "core.js", "old_string": "count += 1;",
                    "new_string": "count += 4;"}
    # And its result is still there as a real tool message, not a note.
    assert any(m.get("tool_call_id") == "call_0_edit_file" for m in seen[-1])
    assert agent._PRUNE_SENTINEL not in _assistant_notes(seen[-1])


def test_an_edit_over_the_compaction_fuse_is_removed_like_a_write(
        isolated_db, games_dir):
    """A near-whole-module replacement expressed as one exact match is a
    write_file wearing another name, and gets write_file's treatment:
    REMOVED, never a rewritten arguments slot."""
    _setup_source_game(games_dir)
    big = "x" * 200
    responses = [
        _turn([("edit_file", {"path": "core.js", "old_string": "count += 1;",
                              "new_string": big})]),
        _turn([("read_map", {})]),
        _turn([("finish", {"summary": "done"})]),
    ]

    with mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result, seen = _run(games_dir, responses,
                            config=_edit_config(edit_compact_bytes=50))

    assert result["success"], result["error"]
    blob = json.dumps(seen[-1])
    assert big not in blob
    assert "call_0_edit_file" not in blob
    note = _assistant_notes(seen[-1])
    assert "edit_file call(s) below COMPLETED SUCCESSFULLY" in note
    assert "OK: edited core.js" in note
    assert "Every other file in the snapshot is still exactly as shown there." in note


def test_repeated_failed_edits_trip_the_stall_guard_and_the_run_still_ships(
        isolated_db, games_dir):
    """A rejected edit is not progress — a model looping on an old_string that
    never matches has to trip the existing guard rather than being kept alive
    by cheap turns. The work it did land is still force-verified."""
    _setup_source_game(games_dir)
    responses = (
        [_turn([("edit_file", {"path": "core.js", "old_string": "count += 1;",
                               "new_string": "count += 5;"})])]
        + [_turn([("edit_file", {"path": "core.js", "old_string": "nope",
                                 "new_string": "also nope"})])
           for _ in range(agent._MAX_NO_PROGRESS_STEPS * 2 + 1)]
    )

    with mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result, _seen = _run(games_dir, responses, config=_edit_config(max_steps=30))

    assert result["success"], result["error"]
    assert not result["complete"]
    assert "ran out of turns before confirming it was done" in result["notes"]
    assert "count += 5;" in (games_dir / result["slug"] / "src" / "core.js").read_text()


def test_edit_file_events_never_carry_the_edited_text(isolated_db, games_dir):
    """agent_events is a permanent, publicly replayable archive. Byte counts
    only — the same rule write_file's contents follow."""
    _setup_source_game(games_dir)
    events = []
    responses = [
        _turn([("edit_file", {"path": "core.js", "old_string": "count += 1;",
                              "new_string": "count += 6;"})]),
        _turn([("finish", {"summary": "done"})]),
    ]

    with mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result, _seen = _run(
            games_dir, responses,
            emit=lambda role, content=None, data=None: events.append((role, content, data)),
        )

    assert result["success"], result["error"]
    edits = [e for e in events if (e[2] or {}).get("tool") == "edit_file"]
    assert len(edits) == 2      # the call and its result
    call, obs = edits
    assert call[1] == "edit_file('core.js', -11 +11 bytes)"
    assert call[2] == {"tool": "edit_file", "path": "core.js",
                       "removed": 11, "added": 11}
    assert obs[1].startswith("OK: edited core.js")
    assert obs[2]["outcome"] == "ok"
    for _role, content, data in edits:
        blob = json.dumps([content, data])
        assert "count += 1;" not in blob and "count += 6;" not in blob


# ---------------------------------------------------------------------------
# Sprint 6a step 4: the append-only context guard
#
# Append-only means the conversation only ever grows, so the context window is
# now the run's real ceiling. Running into it is an API 400 mid-run, which
# ships nothing even when every module is already written and correct — the
# guard's whole job is to convert that into an orderly stop that still reaches
# the forced final verification.
# ---------------------------------------------------------------------------

def test_a_run_past_the_context_soft_limit_is_told_once_to_stop_exploring(
        isolated_db, games_dir):
    _setup_source_game(games_dir)
    responses = [
        _turn([("read_map", {})], tokens=(900, 5)),
        _turn([("read_file", {"path": "core.js"})], tokens=(950, 5)),
        _turn([("edit_file", {"path": "core.js", "old_string": "count += 1;",
                              "new_string": "count += 3;"})], tokens=(980, 5)),
        _turn([("finish", {"summary": "count by threes"})], tokens=(990, 5)),
    ]

    with mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result, seen = _run(games_dir, responses,
                            config=_edit_config(context_soft_limit_tokens=800))

    assert result["success"], result["error"]
    # First call is never blocked or nudged (last_input_tokens starts at 0),
    # and the nudge lands before the call after the one that crossed 800.
    assert not [m for m in seen[0] if "CONTEXT WARNING" in str(m.get("content"))]
    warnings = [m for m in seen[-1] if "CONTEXT WARNING" in str(m.get("content"))]
    assert len(warnings) == 1, "the context nudge must fire exactly once per run"
    warning = warnings[0]
    assert warning["role"] == "user"
    assert "900" in warning["content"]
    assert "do not call read_file or search again" in warning["content"]
    assert "finish(summary)" in warning["content"]

    # It has to be appended where a user message is legal — never between an
    # assistant message carrying tool_calls and its tool results.
    before = seen[1][seen[1].index(warning) - 1]
    assert before["role"] in ("tool", "user")


def test_the_context_nudge_stays_silent_below_the_soft_limit(isolated_db, games_dir):
    _setup_source_game(games_dir)
    responses = [
        _turn([("edit_file", {"path": "core.js", "old_string": "count += 1;",
                              "new_string": "count += 4;"})], tokens=(500, 5)),
        _turn([("finish", {"summary": "count by fours"})], tokens=(600, 5)),
    ]

    with mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result, seen = _run(games_dir, responses,
                            config=_edit_config(context_soft_limit_tokens=800))

    assert result["success"], result["error"]
    assert not [m for m in seen[-1] if "CONTEXT WARNING" in str(m.get("content"))]


def test_a_run_past_the_context_hard_limit_stops_and_ships_what_it_wrote(
        isolated_db, games_dir):
    """The point of stopping ourselves rather than letting the API 400: the
    edit already on disk is still verified and still ships."""
    _setup_source_game(games_dir)
    huge = int(ai.CONTEXT_WINDOW_TOKENS * agent._CONTEXT_HARD_LIMIT_RATIO) + 1
    responses = [
        _turn([("edit_file", {"path": "core.js", "old_string": "count += 1;",
                              "new_string": "count += 7;"})], tokens=(huge, 5)),
        # Never reached — the guard stops the loop before this request.
        _turn([("read_file", {"path": "core.js"})], tokens=(huge, 5)),
    ]

    with mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result, seen = _run(games_dir, responses, config=_edit_config(max_steps=30))

    assert len(seen) == 1, "the guard must stop before making a second request"
    assert result["success"], result["error"]
    assert not result["complete"]
    assert "ran out of turns before confirming it was done" in result["notes"]
    assert "count += 7;" in (games_dir / result["slug"] / "src" / "core.js").read_text()


def test_a_run_that_hits_the_context_ceiling_with_nothing_written_says_so(
        isolated_db, games_dir):
    _setup_source_game(games_dir)
    huge = int(ai.CONTEXT_WINDOW_TOKENS * agent._CONTEXT_HARD_LIMIT_RATIO) + 1
    responses = [
        _turn([("read_map", {})], tokens=(huge, 5)),
        _turn([("read_file", {"path": "core.js"})], tokens=(huge, 5)),
    ]

    result, seen = _run(games_dir, responses, config=_edit_config(max_steps=30))

    assert len(seen) == 1
    assert not result["success"]
    assert "context window nearly full" in result["error"]
    assert f"{ai.CONTEXT_WINDOW_TOKENS:,}" in result["error"]
    # A failed run leaves no half-written fork behind.
    assert not any(p.name.startswith("click-counter-v2") for p in games_dir.iterdir())


# ---------------------------------------------------------------------------
# 14. Attributing a parse error to a file and line. The smoke test only ever
#     reports what Chromium says about the BUILT html ("Unexpected end of
#     input"), which names no module — job 0cf766d0 spent all three
#     verification attempts guessing at that string.
# ---------------------------------------------------------------------------

BROKEN_CORE_JS = (
    'function ok() { return "}"; }   // } ] )\n'
    'function broken(x) {\n'
    '  if (x) {\n'
    '    ok();\n'
    '  }\n'
)


def _src(tmp_path, **files):
    """A game directory with a src/ holding `files` (name -> contents)."""
    src = tmp_path / "src"
    src.mkdir(parents=True, exist_ok=True)
    for name, contents in files.items():
        (src / name).write_text(contents)
    return tmp_path


def test_locate_syntax_faults_names_the_module_and_the_unclosed_line(tmp_path):
    game_dir = _src(tmp_path, **{"core.js": BROKEN_CORE_JS})
    note = agent._locate_syntax_faults(game_dir)
    assert "core.js" in note
    # The function's brace on line 2, not the `if`'s on line 3, which closes.
    assert "opened at line 2" in note
    assert "never closed" in note


def test_locate_syntax_faults_is_quiet_when_everything_balances(tmp_path):
    game_dir = _src(tmp_path, **{
        # Every closer here lives inside a string, a template, a regex or a
        # comment: an unmasked walk would report all of them.
        "core.js": ('const s = "){]";\n'
                    'const t = `a ${ {b: 1}.b } z`;\n'
                    'const r = /[)}\\]]/;\n'
                    '/* } ) ] */\n'
                    'function f() { return 1; }\n'),
        "index.html": "<html><script>let a = { b: 1 };</script></html>",
    })
    assert agent._locate_syntax_faults(game_dir) == ""


def test_locate_syntax_faults_reports_every_broken_module_not_just_the_first(tmp_path):
    game_dir = _src(tmp_path, **{
        "core.js": BROKEN_CORE_JS,
        "render.js": "function draw() {\n  ctx.fill();\n",
        "fine.js": "function fine() { return 1; }\n",
    })
    note = agent._locate_syntax_faults(game_dir)
    assert "core.js" in note and "render.js" in note
    assert "fine.js" not in note


def test_locate_syntax_faults_counts_html_lines_from_the_top_of_the_file(tmp_path):
    game_dir = _src(tmp_path, **{
        "index.html": ("<html>\n"
                       "<body>\n"
                       "<script src='core.js'></script>\n"
                       "<script>\n"
                       "function boot() {\n"
                       "  start();\n"
                       "</script>\n"
                       "</body></html>\n"),
        "core.js": "function start() { return 1; }\n",
    })
    note = agent._locate_syntax_faults(game_dir)
    # Line 5 of index.html, not line 2 of the inline block.
    assert "index.html" in note and "opened at line 5" in note


def test_locate_syntax_faults_flags_a_closer_that_matches_nothing(tmp_path):
    game_dir = _src(tmp_path, **{"core.js": "function f() {\n  return 1;\n}\n}\n"})
    note = agent._locate_syntax_faults(game_dir)
    assert "line 4" in note and "closes nothing" in note


def test_locate_syntax_faults_flags_a_mismatched_pair(tmp_path):
    game_dir = _src(tmp_path, **{"core.js": "function f() {\n  const a = [1, 2;\n}\n"})
    note = agent._locate_syntax_faults(game_dir)
    assert "does not match the '[' opened at line 2" in note


def test_locate_syntax_faults_says_nothing_about_a_single_file_game(tmp_path):
    (tmp_path / "index.html").write_text("<html><script>function f() {</script></html>")
    assert agent._locate_syntax_faults(tmp_path) == ""


def test_a_failed_verification_tells_the_model_which_file_is_unbalanced(
        isolated_db, games_dir):
    """End to end: the note reaches the model as the finish() rejection, and
    the job's recorded error, not just the bare browser string."""
    _setup_source_game(games_dir)
    responses = [
        _turn([("write_file", {"path": "core.js", "contents": BROKEN_CORE_JS}),
               ("finish", {"summary": "attempt 1"})]),
        _turn([("write_file", {"path": "core.js", "contents": NEW_CORE_JS}),
               ("finish", {"summary": "attempt 2"})]),
    ]

    with mock.patch("smoke_test.run_smoke_test",
                    side_effect=[(False, "pageerror: Unexpected end of input"),
                                 (True, "ok")]):
        result, seen = _run(games_dir, responses, job_id="job-parse")

    assert result["success"], result["error"]
    rejection = next(m for m in seen[1]
                     if m.get("role") == "tool" and "REJECTED" in m.get("content", ""))
    assert "Unexpected end of input" in rejection["content"]
    assert "core.js" in rejection["content"]
    assert "opened at line 2" in rejection["content"]
    # The recorded attempt keeps its classification from the browser's words.
    assert db.get_generation_attempts("job-parse")[0]["outcome"] == "smoke_test_failed"


# ---------------------------------------------------------------------------
# 15. A re-finish that changed nothing costs a turn, not a verification
#     attempt — job 0cf766d0's "maybe the error was transient" retry.
# ---------------------------------------------------------------------------

def test_a_refinish_with_no_edits_is_bounced_without_spending_an_attempt(
        isolated_db, games_dir):
    _setup_source_game(games_dir)
    responses = [
        _turn([("write_file", {"path": "core.js", "contents": NEW_CORE_JS}),
               ("finish", {"summary": "attempt 1"})]),
        _turn([("finish", {"summary": "maybe it was transient"})]),
        _turn([("write_file", {"path": "core.js", "contents": NEW_CORE_JS}),
               ("finish", {"summary": "actually fixed it"})]),
    ]

    with mock.patch("smoke_test.run_smoke_test",
                    side_effect=[(False, "console.error: boom"), (True, "ok")]) as smoke:
        result, seen = _run(games_dir, responses, job_id="job-noop")

    assert result["success"], result["error"]
    # Two builds for three finish calls: the middle one never reached one.
    assert smoke.call_count == 2
    assert result["attempts"] == 2
    assert len(db.get_generation_attempts("job-noop")) == 2
    bounce = next(m for m in seen[2]
                  if m.get("role") == "tool"
                  and "nothing on disk has changed" in m.get("content", ""))
    assert "not transient" in bounce["content"]


def test_the_model_can_insist_and_get_its_build_after_one_bounce(
        isolated_db, games_dir):
    """The bounce is one-shot per failure window, so a run that genuinely
    believes the build is wrong is never locked out of re-verifying."""
    _setup_source_game(games_dir)
    responses = [
        _turn([("write_file", {"path": "core.js", "contents": NEW_CORE_JS}),
               ("finish", {"summary": "attempt 1"})]),
        _turn([("finish", {"summary": "bounced"})]),
        _turn([("finish", {"summary": "no really, build it"})]),
    ]

    with mock.patch("smoke_test.run_smoke_test",
                    side_effect=[(False, "console.error: boom"), (True, "ok")]) as smoke:
        result, _seen = _run(games_dir, responses, job_id="job-insist")

    assert result["success"], result["error"]
    assert smoke.call_count == 2


def test_an_edit_between_finishes_is_never_bounced(isolated_db, games_dir):
    _setup_source_game(games_dir)
    responses = [
        _turn([("write_file", {"path": "core.js", "contents": BROKEN_CORE_JS}),
               ("finish", {"summary": "attempt 1"})]),
        _turn([("edit_file", {"path": "core.js", "old_string": "  }\n",
                              "new_string": "  }\n}\n"}),
               ("finish", {"summary": "attempt 2"})]),
    ]

    with mock.patch("smoke_test.run_smoke_test",
                    side_effect=[(False, "pageerror: Unexpected end of input"),
                                 (True, "ok")]) as smoke:
        result, _seen = _run(games_dir, responses)

    assert result["success"], result["error"]
    assert smoke.call_count == 2
