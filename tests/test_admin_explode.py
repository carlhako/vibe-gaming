"""The admin Games tab's explode control: the format (E/S) column, the
POST /admin/games/<id>/explode queueing route, job_runner's kind='explode'
dispatch, and the per-LLM-call 'usage' events the progress dialog reads.

Mocks ai_client.ask_with_tools and smoke_test.run_smoke_test, same
technique as tests/test_explode.py — no network or browser needed."""

import json
import re
import shutil
from pathlib import Path
from unittest import mock

import agent
import ai_client as ai
import app as app_module
import db
import job_runner

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "multifile-game"

SINGLE_GAME_ID = "1" * 32
MULTI_GAME_ID = "2" * 32

CONFIG = {
    "game_web": {"host": "localhost", "port": 8600, "base_url": ""},
    "multifile_agent": {
        "model": "", "effort": "high", "timeout_seconds": 5,
        "max_steps": 20, "max_verification_retries": 3,
        "max_module_bytes": 100_000, "explode_max_module_bytes": 100_000,
    },
}

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
SPLIT_GAME_MD = "# Old School Arcade\n\n| file | purpose |\n| --- | --- |\n"


def _setup_single_file_source(games_dir, game_id=SINGLE_GAME_ID,
                               title="Old School Arcade"):
    slug = f"old-school-arcade-{game_id[:4]}"
    game_dir = games_dir / slug
    game_dir.mkdir(parents=True)
    (game_dir / "index.html").write_text(
        "<!doctype html><html><body><div id='count'>0</div>"
        "<button id='btn'>Click</button><script>"
        "var c=0;document.getElementById('btn').onclick=function(){"
        "c+=1;document.getElementById('count').textContent=c;};"
        "</script></body></html>", encoding="utf-8")
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


def _setup_multi_file_source(games_dir):
    slug = "click-counter-src"
    shutil.copytree(FIXTURE_DIR, games_dir / slug)
    db.register_web_game(
        game_id=MULTI_GAME_ID, slug=slug, title="Click Counter",
        description="d", requested_by="web:t", status="success", attempts=1,
        version=1, model="deepseek-v4-flash", effort="high",
        parent_game_id=None, root_game_id=MULTI_GAME_ID,
    )
    return db.get_web_game(MULTI_GAME_ID)


def _tool_call(name, args, call_id):
    arguments = json.dumps(args)
    raw = {"id": call_id, "type": "function",
           "function": {"name": name, "arguments": arguments}}
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
                      "usage": {"prompt_tokens": tokens[0],
                                "completion_tokens": tokens[1]}},
    )


def _successful_explode_turns():
    return [
        _turn([
            ("write_file", {"path": "index.html", "contents": SPLIT_INDEX_HTML}),
            ("write_file", {"path": "style.css", "contents": SPLIT_STYLE_CSS}),
        ], tokens=(100, 40)),
        _turn([
            ("write_file", {"path": "core.js", "contents": SPLIT_CORE_JS}),
            ("write_file", {"path": "game.md", "contents": SPLIT_GAME_MD}),
        ], tokens=(200, 60)),
        _turn([("finish", {"summary": "split into modules"})], tokens=(300, 10)),
    ]


def make_client(games_dir, monkeypatch, admin_token="secret-token"):
    monkeypatch.setenv("ADMIN_TOKEN", admin_token)
    flask_app = app_module.create_app(games_dir=games_dir)
    flask_app.config["TESTING"] = True
    return flask_app.test_client()


# ---------------------------------------------------------------------------
# 1. The Games tab shows a per-game format badge and an Explode control only
#    for the games that can actually be exploded.
# ---------------------------------------------------------------------------

def test_games_tab_shows_format_badge_and_explode_only_for_single_file(
        isolated_db, games_dir, monkeypatch):
    _setup_single_file_source(games_dir)
    _setup_multi_file_source(games_dir)
    client = make_client(games_dir, monkeypatch)

    html = client.get("/admin/stats?token=secret-token").get_data(as_text=True)
    # Match on the title-carrying badges only: the column legend above the
    # table renders bare fmt-badge spans too, so a plain class check would
    # pass even with no games listed at all.
    badges = re.findall(r'class="fmt-badge fmt-(single|multi)" title=', html)
    assert sorted(badges) == ["multi", "single"]

    # Exactly one Explode button, and it targets the single-file game —
    # exploding an already-exploded game is nonsense, so the control isn't
    # offered for it at all rather than erroring on click.
    explode_targets = re.findall(
        r'class="explode-btn"\s+data-game-id="([0-9a-f]{32})"', html)
    assert explode_targets == [SINGLE_GAME_ID]


def test_explode_route_requires_admin_token(isolated_db, games_dir, monkeypatch):
    _setup_single_file_source(games_dir)
    client = make_client(games_dir, monkeypatch)
    assert client.post(f"/admin/games/{SINGLE_GAME_ID}/explode").status_code == 403
    assert client.post(
        f"/admin/games/{SINGLE_GAME_ID}/explode?token=wrong").status_code == 403


# ---------------------------------------------------------------------------
# 2. The route queues a real generation_requests job rather than doing the
#    work inline — so the job runner, the kill switch, and the History tab's
#    token/cost accounting all apply to it unchanged.
# ---------------------------------------------------------------------------

def test_explode_queues_a_job_and_returns_its_id(isolated_db, games_dir, monkeypatch):
    _setup_single_file_source(games_dir)
    client = make_client(games_dir, monkeypatch)

    resp = client.post(f"/admin/games/{SINGLE_GAME_ID}/explode?token=secret-token")
    assert resp.status_code == 202
    job_id = resp.get_json()["job_id"]

    job = db.get_generation_request(job_id)
    assert job["kind"] == "explode"
    assert job["status"] == "queued"
    assert job["source_game_id"] == SINGLE_GAME_ID
    assert job["requested_by"] == "admin"


def test_explode_rejects_an_already_multi_file_game(isolated_db, games_dir, monkeypatch):
    _setup_multi_file_source(games_dir)
    client = make_client(games_dir, monkeypatch)

    resp = client.post(f"/admin/games/{MULTI_GAME_ID}/explode?token=secret-token")
    assert resp.status_code == 409
    assert "already multi-file" in resp.get_json()["error"]
    assert db.count_generation_requests() == 0


def test_explode_is_blocked_by_an_in_flight_job_and_reports_it(
        isolated_db, games_dir, monkeypatch):
    _setup_single_file_source(games_dir)
    client = make_client(games_dir, monkeypatch)

    first = client.post(f"/admin/games/{SINGLE_GAME_ID}/explode?token=secret-token")
    first_job = first.get_json()["job_id"]

    second = client.post(f"/admin/games/{SINGLE_GAME_ID}/explode?token=secret-token")
    assert second.status_code == 409
    # The in-flight job's id comes back so the dialog can attach to the run
    # already going instead of the admin having to hunt for it.
    assert second.get_json()["job_id"] == first_job
    assert db.count_generation_requests() == 1


def test_explode_respects_the_ai_kill_switch(isolated_db, games_dir, monkeypatch):
    _setup_single_file_source(games_dir)
    client = make_client(games_dir, monkeypatch)
    db.set_ai_generation_enabled(False)

    resp = client.post(f"/admin/games/{SINGLE_GAME_ID}/explode?token=secret-token")
    assert resp.status_code == 503
    assert db.count_generation_requests() == 0


def test_explode_404s_for_an_unknown_game(isolated_db, games_dir, monkeypatch):
    client = make_client(games_dir, monkeypatch)
    assert client.post(f"/admin/games/{'f' * 32}/explode?token=secret-token").status_code == 404
    assert client.post("/admin/games/not-an-id/explode?token=secret-token").status_code == 404


# ---------------------------------------------------------------------------
# 3. job_runner dispatches kind='explode' to agent.explode_game, and the
#    resulting fork is a visible multi-file game (unlike the hidden
#    intermediate enhance_game_auto_format makes for itself).
# ---------------------------------------------------------------------------

def test_job_runner_runs_an_explode_job_end_to_end(isolated_db, games_dir):
    source = _setup_single_file_source(games_dir)
    job_id = "e" * 32
    db.create_generation_request(
        job_id=job_id, kind="explode", prompt="explode it",
        requested_by="admin", source_game_id=SINGLE_GAME_ID,
    )
    conn = db.get_connection()
    job = db.get_generation_request(job_id, conn=conn)

    with mock.patch.object(ai, "ask_with_tools",
                           side_effect=_successful_explode_turns()), \
         mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        job_runner._run_job(conn, job, CONFIG, games_dir)

    done = db.get_generation_request(job_id, conn=conn)
    assert done["status"] == "success", done["error"]
    # Every LLM call's tokens are accounted for on the job row: 600 in, 110 out.
    assert done["input_tokens"] == 600
    assert done["output_tokens"] == 110
    assert done["tokens_used"] == 710

    fork = db.get_web_game(done["result_game_id"], conn=conn)
    assert fork["parent_game_id"] == SINGLE_GAME_ID
    assert not fork["hidden"]
    fork_dir = games_dir / fork["slug"]
    assert (fork_dir / "src" / "index.html").is_file()
    assert (fork_dir / "index.html").is_file()   # the built, served artifact
    # The single-file source is untouched — explode forks, never converts.
    assert not (games_dir / source["slug"] / "src").exists()


def test_job_runner_fails_an_explode_job_when_the_source_is_already_multi_file(
        isolated_db, games_dir):
    _setup_multi_file_source(games_dir)
    job_id = "f" * 32
    db.create_generation_request(
        job_id=job_id, kind="explode", prompt="explode it",
        requested_by="admin", source_game_id=MULTI_GAME_ID,
    )
    conn = db.get_connection()
    job = db.get_generation_request(job_id, conn=conn)

    with mock.patch.object(ai, "ask_with_tools") as ask:
        job_runner._run_job(conn, job, CONFIG, games_dir)
    ask.assert_not_called()   # rejected before a single token was spent

    done = db.get_generation_request(job_id, conn=conn)
    assert done["status"] == "failed"
    assert "already multi-file" in done["error"]


# ---------------------------------------------------------------------------
# 4. Per-LLM-call token logging: the 'usage' events the progress dialog's
#    live counter reads, plus the job totals on the events endpoint.
# ---------------------------------------------------------------------------

def test_every_llm_call_emits_a_usage_event_with_running_totals(
        isolated_db, games_dir):
    _setup_single_file_source(games_dir)
    job_id = "a" * 32
    db.create_generation_request(
        job_id=job_id, kind="explode", prompt="explode it",
        requested_by="admin", source_game_id=SINGLE_GAME_ID,
    )
    with mock.patch.object(ai, "ask_with_tools",
                           side_effect=_successful_explode_turns()), \
         mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result = agent.explode_game(
            SINGLE_GAME_ID, "admin", CONFIG, games_dir=games_dir, job_id=job_id)
    assert result["success"], result["error"]

    usage = [e for e in db.get_agent_events(job_id) if e["role"] == "usage"]
    assert len(usage) == 3          # one per ask_with_tools call, no more
    assert [e["data"]["step"] for e in usage] == [1, 2, 3]
    assert [e["data"]["call_input_tokens"] for e in usage] == [100, 200, 300]
    assert [e["data"]["input_tokens"] for e in usage] == [100, 300, 600]
    assert [e["data"]["tokens_used"] for e in usage] == [140, 400, 710]
    # Totals reconcile with what the job row will report.
    assert usage[-1]["data"]["tokens_used"] == result["tokens_used"]


def test_events_endpoint_exposes_job_token_totals_for_the_dialog(
        isolated_db, games_dir, monkeypatch):
    _setup_single_file_source(games_dir)
    job_id = "b" * 32
    db.create_generation_request(
        job_id=job_id, kind="explode", prompt="explode it",
        requested_by="admin", source_game_id=SINGLE_GAME_ID,
    )
    db.update_generation_request(
        job_id, status="success", attempts=1, model="deepseek-v4-flash",
        effort="high", duration_seconds=12.5,
        input_tokens=600, output_tokens=110, tokens_used=710,
    )

    client = make_client(games_dir, monkeypatch)
    data = client.get(f"/api/jobs/{job_id}/events").get_json()
    assert data["kind"] == "explode"
    assert data["input_tokens"] == 600
    assert data["output_tokens"] == 110
    assert data["tokens_used"] == 710
    assert data["duration_seconds"] == 12.5
    assert data["model"] == "deepseek-v4-flash"


def test_explode_job_shows_up_in_the_admin_generation_history(
        isolated_db, games_dir, monkeypatch):
    _setup_single_file_source(games_dir)
    job_id = "c" * 32
    db.create_generation_request(
        job_id=job_id, kind="explode", prompt="explode it",
        requested_by="admin", source_game_id=SINGLE_GAME_ID,
    )
    db.update_generation_request(
        job_id, status="success", attempts=1, model="deepseek-v4-flash",
        effort="high", duration_seconds=12.5,
        input_tokens=600, output_tokens=110, tokens_used=710,
    )
    monkeypatch.setenv("DEEPSEEK_INPUT_COST_PER_MILLION", "0.14")
    monkeypatch.setenv("DEEPSEEK_OUTPUT_COST_PER_MILLION", "0.28")

    client = make_client(games_dir, monkeypatch)
    html = client.get("/admin/stats?token=secret-token").get_data(as_text=True)
    assert "explode" in html
    assert "of Old School Arcade" in html
    assert "710" in html


def test_history_offers_a_transcript_replay_only_for_jobs_that_have_one(
        isolated_db, games_dir, monkeypatch):
    """The agent transcript is otherwise only reachable from /status/<job_id>
    while the job runs; History's Transcript button is how an admin reads it
    back afterwards. Single-file jobs emit no agent_events, so they must not
    offer a button that would open an empty dialog."""
    _setup_single_file_source(games_dir)
    agent_job, plain_job = "a" * 32, "b" * 32
    for job_id, kind in ((agent_job, "explode"), (plain_job, "create")):
        db.create_generation_request(
            job_id=job_id, kind=kind, prompt="p", requested_by="admin",
            source_game_id=SINGLE_GAME_ID if kind == "explode" else None,
        )
        db.update_generation_request(job_id, status="success", attempts=1)
    db.add_agent_event(agent_job, "tool_call", "write_file core.js",
                       {"tool": "write_file", "path": "core.js"})

    rows = {r["job_id"]: r for r in db.get_generation_history()}
    assert rows[agent_job]["has_agent_events"]
    assert not rows[plain_job]["has_agent_events"]

    client = make_client(games_dir, monkeypatch)
    html = client.get("/admin/stats?token=secret-token").get_data(as_text=True)
    transcript_jobs = re.findall(
        r'class="link-btn transcript-btn"\s+data-job-id="([0-9a-f]{32})"', html)
    assert transcript_jobs == [agent_job]
