"""Sprint 5 of docs/multifile-agent/ (05-migration-and-pilot.md): the
explode pass (Part A) and the dual-format enhance policy (Part B). Mocks
ai_client.ask_with_tools with scripted tool-call sequences and
smoke_test.run_smoke_test, same technique as tests/test_agent.py — no
network or browser needed."""

import copy
import json
import shutil
from pathlib import Path
from unittest import mock

import agent
import ai_client as ai
import builder
import db
import game_enhancer as ge

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "multifile-game"

CONFIG = {
    "game_web": {"host": "localhost", "port": 8600, "base_url": ""},
    "enhanceaiwebgame": {"model": "", "effort": "high", "timeout_seconds": 5,
                          "max_attempts": 3, "smoke_test_timeout_seconds": 5},
    "multifile_agent": {
        "model": "", "effort": "high", "timeout_seconds": 5,
        "max_steps": 20, "max_verification_retries": 3,
        "max_module_bytes": 100_000,
    },
}

SOURCE_GAME_ID = "1" * 32
MULTIFILE_SOURCE_GAME_ID = "2" * 32

SPLIT_INDEX_HTML = (
    '<!doctype html><html><head><link rel="stylesheet" href="style.css">'
    '</head><body><div id="count">0</div><button id="btn">Click</button>'
    '<script src="core.js"></script></body></html>'
)
SPLIT_STYLE_CSS = "body { background: #111; color: #eee; }\n"
SPLIT_CORE_JS = (
    '(function () {\n'
    '  var count = 0;\n'
    '  var countEl = document.getElementById("count");\n'
    '  document.getElementById("btn").addEventListener("click", function () {\n'
    '    count += 1;\n'
    '    countEl.textContent = String(count);\n'
    '  });\n'
    '})();\n'
)
SPLIT_GAME_MD = (
    "# Old School Arcade\n\nA tiny click counter.\n\n"
    "| file | purpose |\n| --- | --- |\n"
    "| src/index.html | shell |\n| src/style.css | styling |\n| src/core.js | logic |\n"
)


def _setup_single_file_source(games_dir, html=None, title="Old School Arcade",
                               game_id=SOURCE_GAME_ID) -> dict:
    slug = f"old-school-arcade-{game_id[:4]}"
    game_dir = games_dir / slug
    game_dir.mkdir(parents=True)
    html = html if html is not None else (
        "<!doctype html><html><body><div id='count'>0</div>"
        "<button id='btn'>Click</button><script>"
        "var c=0;document.getElementById('btn').onclick=function(){"
        "c+=1;document.getElementById('count').textContent=c;};"
        "</script></body></html>"
    )
    (game_dir / "index.html").write_text(html, encoding="utf-8")
    (game_dir / "meta.json").write_text(json.dumps({
        "game_id": game_id, "title": title, "description": "d",
        "requested_by": "web:a", "created_at": db.now_iso(), "version": 1,
        "prompt": "d", "format": "single-file",
    }), encoding="utf-8")
    db.register_web_game(
        game_id=game_id, slug=slug, title=title, description="d",
        requested_by="web:a", status="success", attempts=1, version=1,
        model="deepseek-v4-flash", effort="high",
        parent_game_id=None, root_game_id=game_id,
    )
    return db.get_web_game(game_id)


def _setup_multi_file_source(games_dir) -> dict:
    """Copy the Sprint 1 fixture into games_dir as a registered, multi-file
    game — same fixture tests/test_agent.py uses, duplicated locally per
    this repo's convention of not importing helpers across test files."""
    slug = "click-counter-src"
    shutil.copytree(FIXTURE_DIR, games_dir / slug)
    db.register_web_game(
        game_id=MULTIFILE_SOURCE_GAME_ID, slug=slug, title="Click Counter",
        description="Press the button, watch the number climb.",
        requested_by="web:t", status="success", attempts=1, version=1,
        model="deepseek-v4-flash", effort="high",
        parent_game_id=None, root_game_id=MULTIFILE_SOURCE_GAME_ID,
    )
    return db.get_web_game(MULTIFILE_SOURCE_GAME_ID)


def _tool_call(name, args, call_id):
    arguments = json.dumps(args)
    raw = {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}
    return raw, ai.ToolCall(id=call_id, name=name, arguments=arguments)


def _turn(calls, tokens=(5, 5)):
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


EXPLODE_RESPONSES = [
    _turn([("write_file", {"path": "index.html", "contents": SPLIT_INDEX_HTML})]),
    _turn([("write_file", {"path": "style.css", "contents": SPLIT_STYLE_CSS})]),
    _turn([("write_file", {"path": "core.js", "contents": SPLIT_CORE_JS})]),
    _turn([("write_file", {"path": "game.md", "contents": SPLIT_GAME_MD})]),
    _turn([("finish", {"summary": "split into modules"})]),
]


# ---------------------------------------------------------------------------
# Part A: explode_game()
# ---------------------------------------------------------------------------

def test_explode_produces_multifile_fork_with_lineage_to_source(isolated_db, games_dir):
    _setup_single_file_source(games_dir)

    with mock.patch.object(ai, "ask_with_tools", side_effect=EXPLODE_RESPONSES), \
         mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result = agent.explode_game(SOURCE_GAME_ID, "web:t", CONFIG, games_dir=games_dir)

    assert result["success"], result["error"]
    assert result["parent_game_id"] == SOURCE_GAME_ID
    assert result["root_game_id"] == SOURCE_GAME_ID
    assert result["title"] == "Old School Arcade"

    fork_dir = games_dir / result["slug"]
    assert builder.is_multi_file(fork_dir)
    built_html = fork_dir.joinpath("index.html").read_text(encoding="utf-8")
    assert "background: #111" in built_html, "style.css must be inlined into the build"
    assert "count += 1" in built_html, "core.js must be inlined into the build"

    row = db.get_web_game(result["game_id"])
    assert row["parent_game_id"] == SOURCE_GAME_ID
    assert row["root_game_id"] == SOURCE_GAME_ID

    # Source untouched.
    source_dir = games_dir / db.get_web_game(SOURCE_GAME_ID)["slug"]
    assert not builder.is_multi_file(source_dir)


def test_explode_rejects_an_already_multifile_source(isolated_db, games_dir):
    _setup_multi_file_source(games_dir)
    result = agent.explode_game(MULTIFILE_SOURCE_GAME_ID, "web:t", CONFIG, games_dir=games_dir)
    assert not result["success"]
    assert "already multi-file" in result["error"]


def test_explode_fails_cleanly_and_rolls_back_on_persistent_failure(isolated_db, games_dir):
    _setup_single_file_source(games_dir)
    before = {p.name for p in games_dir.iterdir()}

    bad_cfg = copy.deepcopy(CONFIG)
    bad_cfg["multifile_agent"]["max_verification_retries"] = 1
    responses = [
        _turn([("write_file", {"path": "index.html", "contents": SPLIT_INDEX_HTML})]),
        _turn([("finish", {"summary": "done"})]),
    ]
    with mock.patch.object(ai, "ask_with_tools", side_effect=responses), \
         mock.patch("smoke_test.run_smoke_test", return_value=(False, "console error: boom")):
        result = agent.explode_game(SOURCE_GAME_ID, "web:t", bad_cfg, games_dir=games_dir)

    assert not result["success"]
    after = {p.name for p in games_dir.iterdir()}
    assert before == after, "a failed explode must not leave any new directory behind"


def test_explode_announce_completion_false_emits_note_not_final(isolated_db, games_dir):
    _setup_single_file_source(games_dir)
    job_id = "9" * 32
    db.create_generation_request(
        job_id=job_id, kind="enhance", prompt="irrelevant", requested_by="web:t",
        source_game_id=SOURCE_GAME_ID,
    )

    with mock.patch.object(ai, "ask_with_tools", side_effect=EXPLODE_RESPONSES), \
         mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result = agent.explode_game(
            SOURCE_GAME_ID, "web:t", CONFIG, games_dir=games_dir, job_id=job_id,
            announce_completion=False,
        )

    assert result["success"], result["error"]
    events = db.get_agent_events(job_id)
    roles = [e["role"] for e in events]
    assert "final" not in roles
    assert "assistant" in roles
    assert any("multi-file format" in (e["content"] or "") for e in events if e["role"] == "assistant")


# ---------------------------------------------------------------------------
# Part B: enhance_game_auto_format()
# ---------------------------------------------------------------------------

def test_auto_format_routes_multifile_source_directly(isolated_db, games_dir):
    _setup_multi_file_source(games_dir)
    responses = [_turn([("finish", {"summary": "no changes"})])]

    with mock.patch.object(ai, "ask_with_tools", side_effect=responses), \
         mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result = agent.enhance_game_auto_format(
            MULTIFILE_SOURCE_GAME_ID, "make it better", "web:t", CONFIG,
            games_dir=games_dir,
        )

    assert result["success"], result["error"]
    assert result["parent_game_id"] == MULTIFILE_SOURCE_GAME_ID


def test_auto_format_falls_back_to_legacy_for_small_singlefile_source(isolated_db, games_dir):
    _setup_single_file_source(games_dir)
    args = json.dumps({
        "title": "ignored", "description": "d",
        "html": "<!doctype html><html><body>v2</body></html>", "notes": "",
    })
    raw = {"id": "c1", "type": "function", "function": {"name": "submit_game", "arguments": args}}
    message = {"role": "assistant", "content": None, "tool_calls": [raw]}
    submit_response = ai.ToolAskResult(
        message=message, tool_calls=[ai.ToolCall(id="c1", name="submit_game", arguments=args)],
        text="", input_tokens=5, output_tokens=5, model="deepseek-v4-flash", effort="high",
        raw_response={"choices": [{"message": message, "finish_reason": "tool_calls"}],
                      "usage": {"prompt_tokens": 5, "completion_tokens": 5}},
    )

    with mock.patch.object(ai, "ask_with_tools", return_value=submit_response), \
         mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result = agent.enhance_game_auto_format(
            SOURCE_GAME_ID, "polish it", "web:t", CONFIG, games_dir=games_dir,
        )

    assert result["success"], result["error"]
    fork_dir = games_dir / result["slug"]
    assert not builder.is_multi_file(fork_dir), "small sources must stay on the legacy path"


def test_auto_format_explodes_then_enhances_large_singlefile_source(isolated_db, games_dir):
    large_html = (
        "<!doctype html><html><body><div id='count'>0</div>"
        "<button id='btn'>Click</button><script>"
        "var c=0;document.getElementById('btn').onclick=function(){"
        "c+=1;document.getElementById('count').textContent=c;};"
        "</script>" + ("<!-- padding " + "x" * 200 + " -->") * (ge.LARGE_SOURCE_BYTES // 200 + 1)
        + "</body></html>"
    )
    assert len(large_html.encode("utf-8")) >= ge.LARGE_SOURCE_BYTES
    _setup_single_file_source(games_dir, html=large_html)

    enhance_responses = [
        _turn([("read_map", {})]),
        _turn([("read_file", {"path": "core.js"})]),
        _turn([("write_file", {"path": "core.js", "contents": SPLIT_CORE_JS.replace("count += 1", "count += 2")}),
               ("finish", {"summary": "double the increment"})]),
    ]
    responses = EXPLODE_RESPONSES + enhance_responses

    with mock.patch.object(ai, "ask_with_tools", side_effect=responses), \
         mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result = agent.enhance_game_auto_format(
            SOURCE_GAME_ID, "double the increment", "web:t", CONFIG, games_dir=games_dir,
        )

    assert result["success"], result["error"]
    intermediate_id = result["parent_game_id"]
    assert intermediate_id != SOURCE_GAME_ID, "parent must be the exploded fork, not the original"
    assert result["root_game_id"] == SOURCE_GAME_ID, "root must still trace to the original single-file source"

    intermediate_row = db.get_web_game(intermediate_id)
    assert intermediate_row is not None
    assert bool(intermediate_row["hidden"]) is True, "the auto-explode step must not clutter the sidebar"
    assert intermediate_row["parent_game_id"] == SOURCE_GAME_ID

    fork_dir = games_dir / result["slug"]
    assert builder.is_multi_file(fork_dir)


def test_auto_format_falls_back_to_legacy_when_explode_fails(isolated_db, games_dir):
    large_html = (
        "<!doctype html><html><body>v1"
        + ("x" * ge.LARGE_SOURCE_BYTES) + "</body></html>"
    )
    _setup_single_file_source(games_dir, html=large_html)
    before = {p.name for p in games_dir.iterdir()}

    bad_cfg = copy.deepcopy(CONFIG)
    bad_cfg["multifile_agent"]["max_verification_retries"] = 1

    args = json.dumps({
        "title": "ignored", "description": "d",
        "html": "<!doctype html><html><body>v2</body></html>", "notes": "",
    })
    raw = {"id": "c1", "type": "function", "function": {"name": "submit_game", "arguments": args}}
    message = {"role": "assistant", "content": None, "tool_calls": [raw]}
    submit_response = ai.ToolAskResult(
        message=message, tool_calls=[ai.ToolCall(id="c1", name="submit_game", arguments=args)],
        text="", input_tokens=5, output_tokens=5, model="deepseek-v4-flash", effort="high",
        raw_response={"choices": [{"message": message, "finish_reason": "tool_calls"}],
                      "usage": {"prompt_tokens": 5, "completion_tokens": 5}},
    )

    # First (explode) conversation always fails the safety scan (eval() is
    # banned — deterministic, unlike smoke_test, so the mocked smoke test
    # can stay a plain success and not also sink the legacy fallback
    # below); once enhance_game_auto_format falls back to the legacy path,
    # a fresh conversation starts and gets the submit_game response instead.
    explode_fail_responses = [
        _turn([("write_file", {"path": "index.html", "contents": "<html><script>eval('x')</script></html>"})]),
        _turn([("finish", {"summary": "done"})]),
    ]

    call_log = []

    def scripted(messages, **_kwargs):
        call_log.append(messages)
        if len(call_log) <= len(explode_fail_responses):
            return explode_fail_responses[len(call_log) - 1]
        return submit_response

    with mock.patch.object(ai, "ask_with_tools", side_effect=scripted), \
         mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result = agent.enhance_game_auto_format(
            SOURCE_GAME_ID, "polish it", "web:t", bad_cfg, games_dir=games_dir,
        )

    assert result["success"], result["error"]
    fork_dir = games_dir / result["slug"]
    assert not builder.is_multi_file(fork_dir), "fallback result must be the legacy single-file path"

    after = {p.name for p in games_dir.iterdir()}
    # Only the source + the one successful legacy fork should remain -- the
    # failed explode attempt's half-written directory must be cleaned up.
    assert len(after - before) == 1
