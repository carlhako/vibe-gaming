"""Extending a run's step budget by asking the user: the db columns and
helpers, the POST /api/jobs/<job_id>/approve-steps endpoint, the pause in
agent._run_react_loop, and the "shipped without confirming" reporting on the
forced-verification path.

Same five-layer shape as test_cancel_job.py, because this is the same kind of
thing — a second out-of-band signal from a request handler into a worker
thread that can only hear it by polling its own DB row.
"""

import shutil
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

import agent
import app as app_module
import db

from agent_harness import scripted_asks
from test_agent import _turn, NEW_CORE_JS

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "multifile-game"
SOURCE_GAME_ID = "6a604c1fd9a1cf932aa72764be2f14e4"


def _config(**overrides):
    return {
        "game_web": {"host": "localhost", "port": 8600, "base_url": ""},
        "multifile_agent": dict({
            "model": "", "effort": "high", "timeout_seconds": 5,
            "max_steps": 2, "max_verification_retries": 3,
            "max_module_bytes": 100_000,
            "extra_steps_on_approval": 3,
            # Every test here either pre-records an answer or wants the
            # timeout path; none should actually sit and wait.
            "step_approval_timeout_seconds": 0,
        }, **overrides),
    }


def make_client(games_dir):
    flask_app = app_module.create_app(games_dir=games_dir)
    flask_app.config["TESTING"] = True
    return flask_app.test_client()


def _setup_source(games_dir):
    shutil.copytree(FIXTURE_DIR, games_dir / "click-counter-src")
    db.register_web_game(
        game_id=SOURCE_GAME_ID, slug="click-counter-src", title="Click Counter",
        description="Press the button, watch the number climb.",
        requested_by="web:t", status="success", attempts=1, version=1,
        model="deepseek-v4-flash", effort="high",
        parent_game_id=None, root_game_id=SOURCE_GAME_ID,
    )


def _job(job_id, **kw):
    db.create_generation_request(
        job_id=job_id, kind="enhance", prompt="add a reset button",
        requested_by="web:t", source_game_id=SOURCE_GAME_ID, **kw,
    )
    db.update_generation_request(job_id, status="generating")


def _run(games_dir, responses, job_id, config=None):
    with scripted_asks(responses) as seen:
        result = agent.enhance_multifile_game(
            SOURCE_GAME_ID, "add a reset button", "web:t", config or _config(),
            games_dir=games_dir, job_id=job_id,
        )
    return result, seen


@contextmanager
def _responder(job_id, *, grant=None, cancel=False, timeout=120.0):
    """Answer the run's approval prompt from another thread, the way a browser
    would.

    An answer cannot simply be pre-recorded: request_step_approval clears
    extra_steps_granted precisely so a fresh prompt can't inherit a stale
    answer, so the reply has to land after the prompt does. The agent's own
    poll interval is shortened here so these tests take milliseconds rather
    than seconds — nothing about the logic depends on its real value.

    `timeout` is a hang guard, not part of any assertion — it is deliberately
    far longer than a run needs. A short one made these tests flaky under a
    full-suite load: a late answer times the pause out, which is a legitimate
    code path, so the run then ships and the test fails an assertion about
    turn counts with nothing to say about why. Whatever the thread hits is
    re-raised in the test's own thread for the same reason — a daemon thread
    that dies quietly turns a DB error into a mystery off-by-two.
    """
    stop = threading.Event()
    failure: list[BaseException] = []

    def wait_and_answer():
        deadline = time.monotonic() + timeout
        while not stop.is_set() and time.monotonic() < deadline:
            try:
                answered = db.get_step_approval(job_id)[0] is not None
            except Exception as exc:                 # noqa: BLE001 - re-raised below
                failure.append(exc)
                return
            if answered:
                try:
                    if cancel:
                        db.update_generation_request(
                            job_id, status="cancelled", error="cancelled by user")
                    else:
                        db.grant_extra_steps(job_id, grant)
                except Exception as exc:             # noqa: BLE001 - re-raised below
                    failure.append(exc)
                return
            time.sleep(0.005)
        if not stop.is_set():
            # Ran out of wall clock rather than being shut down at the end of
            # the test. A test that never expects a prompt just exits quietly.
            failure.append(AssertionError(
                f"responder for {job_id} saw no prompt within {timeout}s"))

    thread = threading.Thread(target=wait_and_answer, daemon=True)
    with mock.patch.object(agent, "_APPROVAL_POLL_SECONDS", 0.01):
        thread.start()
        try:
            yield
        finally:
            stop.set()
            thread.join(timeout=5)
        if failure:
            raise failure[0]


def _read(path="core.js"):
    return _turn([("read_file", {"path": path})])


def _write(contents=NEW_CORE_JS):
    return _turn([("write_file", {"path": "core.js", "contents": contents})])


# ---------------------------------------------------------------------------
# db helpers
# ---------------------------------------------------------------------------

def test_approval_columns_round_trip(isolated_db, games_dir):
    job_id = "a" * 32
    _job(job_id)
    assert db.get_step_approval(job_id) == (None, None)

    db.request_step_approval(job_id)
    waiting, decision = db.get_step_approval(job_id)
    assert waiting is not None
    assert decision is None

    db.grant_extra_steps(job_id, 40)
    assert db.get_step_approval(job_id) == (None, 40)


def test_declining_is_a_real_answer_not_an_absent_one(isolated_db, games_dir):
    """0 has to be distinguishable from "hasn't answered yet", which is why
    the waiting flag and the grant are separate columns."""
    job_id = "b" * 32
    _job(job_id)
    db.request_step_approval(job_id)
    db.grant_extra_steps(job_id, 0)

    waiting, decision = db.get_step_approval(job_id)
    assert waiting is None
    assert decision == 0


def test_clear_step_approval_drops_the_flag_without_answering(isolated_db, games_dir):
    job_id = "c" * 32
    _job(job_id)
    db.request_step_approval(job_id)
    db.clear_step_approval(job_id)
    assert db.get_step_approval(job_id) == (None, None)


def test_approval_writes_do_not_bump_updated_at(isolated_db, games_dir):
    """updated_at is served as generating_started_at, which drives the status
    page's elapsed timer — an approval must not restart it."""
    job_id = "d" * 32
    _job(job_id)
    before = db.get_generation_request(job_id)["updated_at"]

    db.request_step_approval(job_id)
    db.grant_extra_steps(job_id, 40)
    db.clear_step_approval(job_id)

    assert db.get_generation_request(job_id)["updated_at"] == before


def test_get_step_approval_is_safe_for_an_unknown_job(isolated_db, games_dir):
    assert db.get_step_approval("nonexistent" * 4) == (None, None)


# ---------------------------------------------------------------------------
# POST /api/jobs/<job_id>/approve-steps
# ---------------------------------------------------------------------------

def test_approve_endpoint_grants_steps(isolated_db, games_dir):
    client = make_client(games_dir)
    job_id = "e" * 32
    _job(job_id)
    db.request_step_approval(job_id)

    resp = client.post(f"/api/jobs/{job_id}/approve-steps", json={"extra_steps": 40})
    assert resp.status_code == 200
    assert resp.get_json()["extra_steps"] == 40
    assert db.get_step_approval(job_id) == (None, 40)


def test_approve_endpoint_records_a_decline(isolated_db, games_dir):
    client = make_client(games_dir)
    job_id = "f" * 32
    _job(job_id)
    db.request_step_approval(job_id)

    resp = client.post(f"/api/jobs/{job_id}/approve-steps", json={"extra_steps": 0})
    assert resp.status_code == 200
    assert db.get_step_approval(job_id) == (None, 0)


def test_approve_endpoint_clamps_an_oversized_request(isolated_db, games_dir):
    """The number comes from a public button on a page anyone with the job id
    can open; asking for 10,000 turns must not get 10,000 turns."""
    client = make_client(games_dir)
    job_id = "1" * 32
    _job(job_id)
    db.request_step_approval(job_id)

    resp = client.post(f"/api/jobs/{job_id}/approve-steps", json={"extra_steps": 10_000})
    assert resp.status_code == 200
    granted = resp.get_json()["extra_steps"]
    assert granted == app_module._EXTRA_STEPS_GRANT
    assert db.get_step_approval(job_id)[1] == granted


def test_approve_endpoint_ignores_junk_and_falls_back_to_the_default(
        isolated_db, games_dir):
    client = make_client(games_dir)
    job_id = "2" * 32
    _job(job_id)
    db.request_step_approval(job_id)

    resp = client.post(f"/api/jobs/{job_id}/approve-steps", json={"extra_steps": "lots"})
    assert resp.status_code == 200
    assert resp.get_json()["extra_steps"] == app_module._EXTRA_STEPS_GRANT


def test_approve_endpoint_404_for_unknown_job(isolated_db, games_dir):
    client = make_client(games_dir)
    resp = client.post(f"/api/jobs/{'3' * 32}/approve-steps", json={})
    assert resp.status_code == 404


def test_approve_endpoint_409_when_not_waiting(isolated_db, games_dir):
    """A click that lands after the run stopped waiting must not silently
    bank a grant nothing will ever read."""
    client = make_client(games_dir)
    job_id = "4" * 32
    _job(job_id)

    resp = client.post(f"/api/jobs/{job_id}/approve-steps", json={"extra_steps": 40})
    assert resp.status_code == 409
    assert db.get_step_approval(job_id) == (None, None)


def test_events_endpoint_exposes_the_waiting_flag(isolated_db, games_dir):
    client = make_client(games_dir)
    job_id = "5" * 32
    _job(job_id)

    assert client.get(f"/api/jobs/{job_id}/events").get_json()["awaiting_approval"] is False
    db.request_step_approval(job_id)
    body = client.get(f"/api/jobs/{job_id}/events").get_json()
    assert body["awaiting_approval"] is True
    assert body["approval_extra_steps"] == app_module._EXTRA_STEPS_GRANT
    assert client.get(f"/api/status/{job_id}").get_json()["awaiting_approval"] is True


# ---------------------------------------------------------------------------
# the pause in agent._run_react_loop
# ---------------------------------------------------------------------------

def test_an_approved_run_gets_its_extra_turns(isolated_db, games_dir):
    _setup_source(games_dir)
    job_id = "6" * 32
    _job(job_id)

    # max_steps=2 of reading, then the grant buys 3 more — the write and the
    # finish only happen inside the extension.
    responses = [
        _read(), _read(),
        _write(),
        _turn([("finish", {"summary": "Renamed the button."})]),
    ]

    with _responder(job_id, grant=3), \
         mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result, seen = _run(games_dir, responses, job_id,
                            config=_config(step_approval_timeout_seconds=120))

    assert result["success"], result["error"]
    assert result["complete"], "a passing finish() is a complete run"
    assert len(seen) == 4, "the run must get past its original 2-step budget"
    assert (games_dir / result["slug"] / "src" / "core.js").read_text() == NEW_CORE_JS


def test_the_grant_is_appended_as_a_user_message_the_model_can_act_on(
        isolated_db, games_dir):
    _setup_source(games_dir)
    job_id = "7" * 32
    _job(job_id)

    responses = [
        _read(), _read(),
        _write(),
        _turn([("finish", {"summary": "done"})]),
    ]
    with _responder(job_id, grant=3), \
         mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        _result, seen = _run(games_dir, responses, job_id,
                             config=_config(step_approval_timeout_seconds=120))

    # The turn right after the pause must carry the grant, and must say what
    # to do rather than that something went wrong — the wording rule the whole
    # module follows (see _compact_write_calls).
    granted = seen[2][-1]
    assert granted["role"] == "user"
    assert "granted you 3 more turns" in granted["content"]
    assert "Do not start over" in granted["content"]
    # scripted_asks asserts the append-only invariant on the way out, which is
    # what proves the grant did not invalidate the cached prefix.


def test_the_prompt_is_asked_once_and_never_again(isolated_db, games_dir):
    """One shot: a second exhaustion falls straight through to the forced
    verification rather than asking someone who has clearly stopped watching."""
    _setup_source(games_dir)
    job_id = "8" * 32
    _job(job_id)

    # 2 + 2 = 4 turns available, and the script never calls finish.
    responses = [_write(), _read(), _read(), _read(), _read(), _read()]

    with _responder(job_id, grant=2), \
         mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result, seen = _run(games_dir, responses, job_id,
                            config=_config(step_approval_timeout_seconds=120))

    assert len(seen) == 4, "budget must stop at 2 + one grant of 2"
    assert result["success"], result["error"]
    assert not result["complete"]
    events = [e for e in db.get_agent_events(job_id)
              if e["role"] == "approval_request"]
    assert len(events) == 1


def test_a_run_nobody_answers_ships_what_it_wrote(isolated_db, games_dir):
    """The timeout path is byte-for-byte the behaviour that existed before the
    prompt did — an unattended run must not be made worse by this feature."""
    _setup_source(games_dir)
    job_id = "9" * 32
    _job(job_id)

    responses = [_write(), _read(), _read()]
    with mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result, seen = _run(games_dir, responses, job_id)

    assert len(seen) == 2
    assert result["success"], result["error"]
    assert not result["complete"]
    assert "ran out of turns before confirming it was done" in result["notes"]
    outcomes = [e["data"]["outcome"] for e in db.get_agent_events(job_id)
                if e["role"] == "approval_result"]
    assert outcomes == ["timeout"]
    # The flag must not be left set, or the UI offers buttons forever.
    assert db.get_step_approval(job_id)[0] is None


def test_declining_ships_immediately_without_waiting(isolated_db, games_dir):
    _setup_source(games_dir)
    job_id = "b" * 31 + "1"
    _job(job_id)

    responses = [_write(), _read(), _read()]
    with _responder(job_id, grant=0), \
         mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result, seen = _run(games_dir, responses, job_id,
                            config=_config(step_approval_timeout_seconds=120))

    assert len(seen) == 2
    assert result["success"], result["error"]
    assert not result["complete"]
    outcomes = [e["data"]["outcome"] for e in db.get_agent_events(job_id)
                if e["role"] == "approval_result"]
    assert outcomes == ["declined"]


def test_a_cancel_during_the_pause_discards_the_run(isolated_db, games_dir):
    """A cancel skips the forced final verification, exactly as the mid-run
    cancel checkpoint does — the user asked for it to stop, not to ship."""
    _setup_source(games_dir)
    job_id = "c" * 31 + "1"
    _job(job_id)

    responses = [_write(), _read(), _read()]
    with _responder(job_id, cancel=True), \
         mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result, seen = _run(games_dir, responses, job_id,
                            config=_config(step_approval_timeout_seconds=120))

    assert len(seen) == 2
    assert result["success"] is False
    assert result["error"] == "cancelled by user"
    forks = [p for p in games_dir.iterdir() if p.name != "click-counter-src"]
    assert forks == []


def test_the_prompt_is_disabled_by_a_zero_grant(isolated_db, games_dir):
    """The headless escape hatch: extra_steps_on_approval=0 means never pause."""
    _setup_source(games_dir)
    job_id = "d" * 31 + "1"
    _job(job_id)

    responses = [_write(), _read(), _read()]
    with mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result, seen = _run(games_dir, responses, job_id,
                            config=_config(extra_steps_on_approval=0))

    assert len(seen) == 2
    assert result["success"], result["error"]
    assert db.get_agent_events(job_id) is not None
    assert not [e for e in db.get_agent_events(job_id)
                if e["role"] == "approval_request"]


def test_a_job_id_with_no_row_never_blocks(isolated_db, games_dir):
    """A job_id that doesn't resolve to a row has nobody to ask, so the run
    must fall through rather than sit out the whole timeout."""
    _setup_source(games_dir)

    responses = [_write(), _read(), _read()]
    with mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result, seen = _run(games_dir, responses, "no-such-job",
                            config=_config(step_approval_timeout_seconds=3600))

    assert len(seen) == 2
    assert result["success"], result["error"]
    assert not result["complete"]


def test_request_step_approval_reports_whether_a_row_matched(isolated_db, games_dir):
    job_id = "9" * 32
    _job(job_id)
    assert db.request_step_approval(job_id) is True
    assert db.request_step_approval("no-such-job") is False


def test_an_answer_landing_the_instant_the_prompt_appears_is_not_lost(
        isolated_db, games_dir):
    """The "is there anybody to ask" check must come from the UPDATE's own
    rowcount, not a re-read of awaiting_approval_at.

    Answering clears that column, so a click that lands in between reads back
    as NULL — which is also what an unknown job looks like. That misread the
    grant as "nobody home", dropped it, and shipped the run two turns early;
    it surfaced as a ~50% flake in the one-shot test above, where the
    responder thread happened to win that window.
    """
    _setup_source(games_dir)
    job_id = "f" * 32
    _job(job_id)
    real_request = db.request_step_approval

    def answer_immediately(jid, conn=None):
        matched = real_request(jid, conn=conn)
        db.grant_extra_steps(jid, 2)      # the browser, winning the race
        return matched

    responses = [_write(), _read(), _read(), _read()]
    with mock.patch.object(db, "request_step_approval", side_effect=answer_immediately), \
         mock.patch.object(agent, "_APPROVAL_POLL_SECONDS", 0.01), \
         mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result, seen = _run(games_dir, responses, job_id,
                            config=_config(step_approval_timeout_seconds=120))

    assert len(seen) == 4, "the grant must survive landing before the guard looks"
    assert result["success"], result["error"]
