"""Sprint 4 of docs/multifile-agent/: the live chat UI shell. The actual
chat rendering is JS-driven and verified via the browser preview per the
sprint doc, not here — these are the "practical" server-side checks: the
status page renders the two-pane shell, and a job's transcript replays
identically from since=0 across repeated fetches (simulating a reload)."""

import json
import shutil
from pathlib import Path
from unittest import mock

import app as app_module
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


def _setup_source_game(games_dir) -> dict:
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


def make_client(games_dir):
    flask_app = app_module.create_app(games_dir=games_dir)
    flask_app.config["TESTING"] = True
    return flask_app.test_client()


def test_status_page_renders_two_pane_chat_shell(isolated_db, games_dir):
    client = make_client(games_dir)
    job_id = "5" * 32

    resp = client.get(f"/status/{job_id}")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'class="job-shell"' in html
    assert f'id="chat-pane" data-job-id="{job_id}"' in html
    assert "agent_chat.js" in html
    # status.js must still be present unchanged — single-file jobs rely on it.
    assert "status.js" in html


def test_events_replay_from_since_zero_is_stable_across_reloads(isolated_db, games_dir):
    _setup_source_game(games_dir)
    job_id = "6" * 32
    db.create_generation_request(
        job_id=job_id, kind="enhance", prompt="make the button say Punch instead of Click me",
        requested_by="web:t", source_game_id=SOURCE_GAME_ID,
    )
    responses = [
        _turn([("read_map", {})]),
        _turn([("read_file", {"path": "core.js"})]),
        _turn([
            ("write_file", {"path": "core.js", "contents": NEW_CORE_JS}),
            ("finish", {"summary": "renamed the button"}),
        ]),
    ]
    with mock.patch.object(ai, "ask_with_tools", side_effect=responses), \
         mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result = agent.enhance_multifile_game(
            SOURCE_GAME_ID, "make the button say Punch instead of Click me",
            "web:t", CONFIG, games_dir=games_dir, job_id=job_id,
        )
    assert result["success"], result["error"]
    db.update_generation_request(
        job_id, status="success", result_game_id=result["game_id"], attempts=result["attempts"],
    )

    client = make_client(games_dir)
    first = client.get(f"/api/jobs/{job_id}/events?since=0").get_json()
    second = client.get(f"/api/jobs/{job_id}/events?since=0").get_json()

    assert first == second
    assert len(first["events"]) > 0
    assert first["status"] == "success"
    assert first["result"]["slug"] == result["slug"]
