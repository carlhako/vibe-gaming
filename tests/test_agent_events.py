"""Sprint 3 of docs/multifile-agent/: the agent_events transcript + its
incremental API. Reuses Sprint 2's ask_with_tools-scripting approach (see
tests/test_agent.py) so a full multi-file enhance run can be driven
end-to-end without any network or browser."""

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


def _run(games_dir, job_id, responses, config=None, **kwargs):
    db.create_generation_request(
        job_id=job_id, kind="enhance", prompt="make the button say Punch instead of Click me",
        requested_by="web:t", source_game_id=SOURCE_GAME_ID,
    )
    with mock.patch.object(ai, "ask_with_tools", side_effect=responses):
        result = agent.enhance_multifile_game(
            SOURCE_GAME_ID, "make the button say Punch instead of Click me",
            "web:t", config or CONFIG, games_dir=games_dir, job_id=job_id, **kwargs,
        )
    return result


def _successful_run(games_dir, job_id="1" * 32, **kwargs):
    responses = [
        _turn([("read_map", {})]),
        _turn([("read_file", {"path": "core.js"})]),
        _turn([
            ("write_file", {"path": "core.js", "contents": NEW_CORE_JS}),
            ("finish", {"summary": "renamed the button"}),
        ]),
    ]
    with mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        return _run(games_dir, job_id, responses, **kwargs)


def make_client(games_dir):
    flask_app = app_module.create_app(games_dir=games_dir)
    flask_app.config["TESTING"] = True
    return flask_app.test_client()


# ---------------------------------------------------------------------------
# 1. A scripted run writes ordered agent_events with contiguous seq from 1.
# ---------------------------------------------------------------------------

def test_scripted_run_writes_contiguous_ordered_events(isolated_db, games_dir):
    _setup_source_game(games_dir)
    result = _successful_run(games_dir)
    assert result["success"], result["error"]

    events = db.get_agent_events("1" * 32)
    assert len(events) > 0
    seqs = [e["seq"] for e in events]
    assert seqs == list(range(1, len(events) + 1))

    roles = [e["role"] for e in events]
    assert "tool_call" in roles
    assert "tool_result" in roles
    assert "build" in roles
    assert roles[-1] == "final"


# ---------------------------------------------------------------------------
# 2. read_file/write_file events store path+size in data, never the file
#    bytes in content.
# ---------------------------------------------------------------------------

def test_read_write_events_carry_path_and_size_not_bytes(isolated_db, games_dir):
    _setup_source_game(games_dir)
    result = _successful_run(games_dir, job_id="2" * 32)
    assert result["success"], result["error"]

    events = db.get_agent_events("2" * 32)
    read_events = [e for e in events if e["role"] == "tool_result" and e["data"] and e["data"].get("tool") == "read_file"]
    write_events = [e for e in events if e["role"] == "tool_result" and e["data"] and e["data"].get("tool") == "write_file"]
    assert read_events and write_events

    for e in read_events:
        assert e["data"]["path"] == "core.js"
        assert "bytes" in e["data"]
        assert NEW_CORE_JS not in (e["content"] or "")

    for e in write_events:
        assert e["data"]["path"] == "core.js"
        assert NEW_CORE_JS not in (e["content"] or "")

    # No event anywhere (content or JSON-dumped data) ever carries the full
    # module source.
    for e in events:
        assert NEW_CORE_JS not in (e["content"] or "")
        assert NEW_CORE_JS not in json.dumps(e["data"])


# ---------------------------------------------------------------------------
# 3. A raising emitter must not fail the job (best-effort guarantee).
# ---------------------------------------------------------------------------

def test_raising_emitter_does_not_fail_the_job(isolated_db, games_dir):
    _setup_source_game(games_dir)

    def raising_emit(role, content=None, data=None):
        raise RuntimeError("boom: emitter is broken")

    result = _successful_run(games_dir, job_id="3" * 32, emit=raising_emit)
    assert result["success"], result["error"]
    # The raising emitter means nothing was ever persisted for this job.
    assert db.get_agent_events("3" * 32) == []


# ---------------------------------------------------------------------------
# 4. GET /api/jobs/<job_id>/events — since=, terminal status + result, 404s.
# ---------------------------------------------------------------------------

def test_events_endpoint_returns_new_events_in_order_and_terminal_result(isolated_db, games_dir):
    _setup_source_game(games_dir)
    result = _successful_run(games_dir, job_id="4" * 32)
    assert result["success"], result["error"]
    db.update_generation_request(
        "4" * 32, status="success", result_game_id=result["game_id"],
        attempts=result["attempts"],
    )

    client = make_client(games_dir)
    job_id = "4" * 32

    resp_all = client.get(f"/api/jobs/{job_id}/events")
    assert resp_all.status_code == 200
    data_all = resp_all.get_json()
    assert data_all["status"] == "success"
    assert data_all["kind"] == "enhance"
    all_seqs = [e["seq"] for e in data_all["events"]]
    assert all_seqs == sorted(all_seqs)
    assert data_all["result"]["slug"] == result["slug"]
    assert data_all["result"]["title"] == result["title"]
    assert data_all["result"]["url"].endswith(f"/play/{result['slug']}")

    midpoint = all_seqs[len(all_seqs) // 2]
    resp_since = client.get(f"/api/jobs/{job_id}/events?since={midpoint}")
    data_since = resp_since.get_json()
    since_seqs = [e["seq"] for e in data_since["events"]]
    assert since_seqs == [s for s in all_seqs if s > midpoint]
    assert since_seqs, "expected at least one later event"


def test_events_endpoint_404s_for_unknown_or_malformed_job_id(isolated_db, games_dir):
    client = make_client(games_dir)
    assert client.get(f"/api/jobs/{'f' * 32}/events").status_code == 404
    assert client.get("/api/jobs/not-a-job-id/events").status_code == 404


def test_events_endpoint_empty_for_single_file_job_with_no_agent_events(isolated_db, games_dir):
    job_id = "c" * 32
    db.create_generation_request(
        job_id=job_id, kind="create", prompt="a maze game", requested_by="web:x",
    )
    client = make_client(games_dir)
    resp = client.get(f"/api/jobs/{job_id}/events")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["events"] == []
    assert data["result"] is None
    assert data["status"] == "queued"
