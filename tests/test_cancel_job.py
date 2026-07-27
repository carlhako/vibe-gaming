"""Cancel-an-in-progress-enhance: db.is_job_cancelled, the
POST /api/jobs/<job_id>/cancel endpoint, the cancellation checkpoints in
game_generator.run_generation_attempts and agent._run_react_loop, and
job_runner._run_job not clobbering an already-cancelled status."""

import json
import shutil
from pathlib import Path
from unittest import mock

import pytest

import agent
import ai_client as ai
import app as app_module
import db
import game_generator as gg
import job_runner

from agent_harness import scripted_asks

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "multifile-game"
SOURCE_GAME_ID = "6a604c1fd9a1cf932aa72764be2f14e4"

GEN_CONFIG = {
    "game_web": {"host": "localhost", "port": 8600, "base_url": ""},
    "newaiwebgame": {"model": "", "effort": "high", "timeout_seconds": 5,
                      "max_attempts": 3, "smoke_test_timeout_seconds": 5},
}

AGENT_CONFIG = {
    "game_web": {"host": "localhost", "port": 8600, "base_url": ""},
    "multifile_agent": {
        "model": "", "effort": "high", "timeout_seconds": 5,
        "max_steps": 10, "max_verification_retries": 3,
        "max_module_bytes": 100_000,
    },
}


def make_client(games_dir):
    flask_app = app_module.create_app(games_dir=games_dir)
    flask_app.config["TESTING"] = True
    return flask_app.test_client()


def _setup_multifile_source(games_dir) -> dict:
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


# ---------------------------------------------------------------------------
# db.is_job_cancelled
# ---------------------------------------------------------------------------

def test_is_job_cancelled_false_until_flipped(isolated_db, games_dir):
    job_id = "a" * 32
    db.create_generation_request(job_id=job_id, kind="enhance", prompt="p", requested_by="web:t")
    assert db.is_job_cancelled(job_id) is False

    db.update_generation_request(job_id, status="cancelled", error="cancelled by user")
    assert db.is_job_cancelled(job_id) is True


def test_is_job_cancelled_false_for_unknown_job(isolated_db, games_dir):
    assert db.is_job_cancelled("nonexistent" * 4) is False


# ---------------------------------------------------------------------------
# POST /api/jobs/<job_id>/cancel
# ---------------------------------------------------------------------------

def test_cancel_endpoint_flips_queued_job(isolated_db, games_dir):
    client = make_client(games_dir)
    job_id = "b" * 32
    db.create_generation_request(job_id=job_id, kind="enhance", prompt="p", requested_by="web:t")

    resp = client.post(f"/api/jobs/{job_id}/cancel")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "cancelled"

    job = db.get_generation_request(job_id)
    assert job["status"] == "cancelled"
    assert job["error"] == "cancelled by user"


def test_cancel_endpoint_404_for_unknown_job(isolated_db, games_dir):
    client = make_client(games_dir)
    resp = client.post(f"/api/jobs/{'c' * 32}/cancel")
    assert resp.status_code == 404


def test_cancel_endpoint_409_for_terminal_job(isolated_db, games_dir):
    client = make_client(games_dir)
    job_id = "d" * 32
    db.create_generation_request(job_id=job_id, kind="enhance", prompt="p", requested_by="web:t")
    db.update_generation_request(job_id, status="success")

    resp = client.post(f"/api/jobs/{job_id}/cancel")
    assert resp.status_code == 409
    # A terminal job must not be rewritten by a losing cancel race.
    assert db.get_generation_request(job_id)["status"] == "success"


def test_cancelled_queued_job_is_never_claimed(isolated_db, games_dir):
    job_id = "e" * 32
    db.create_generation_request(job_id=job_id, kind="enhance", prompt="p", requested_by="web:t")
    db.update_generation_request(job_id, status="cancelled", error="cancelled by user")

    assert db.claim_next_queued_request() is None


# ---------------------------------------------------------------------------
# game_generator.run_generation_attempts checkpoint
# ---------------------------------------------------------------------------

def test_run_generation_attempts_stops_when_cancelled(isolated_db, games_dir):
    job_id = "f" * 32
    db.create_generation_request(job_id=job_id, kind="create", prompt="p", requested_by="web:t")
    db.update_generation_request(job_id, status="cancelled", error="cancelled by user")

    with mock.patch.object(ai, "ask_with_tools") as mock_ask:
        outcome = gg.run_generation_attempts(
            description="desc", requested_by="web:t",
            system_prompt="system", initial_user_prompt="make a game",
            cfg=GEN_CONFIG["newaiwebgame"], games_dir=games_dir, job_id=job_id,
        )

    mock_ask.assert_not_called()
    assert outcome["success"] is False
    assert outcome["error"] == "cancelled by user"


# ---------------------------------------------------------------------------
# agent._run_react_loop checkpoint
# ---------------------------------------------------------------------------

def test_enhance_multifile_stops_when_cancelled_and_cleans_up_fork(isolated_db, games_dir):
    _setup_multifile_source(games_dir)
    job_id = "1" * 32
    db.create_generation_request(
        job_id=job_id, kind="enhance", prompt="add a reset button",
        requested_by="web:t", source_game_id=SOURCE_GAME_ID,
    )
    db.update_generation_request(job_id, status="cancelled", error="cancelled by user")

    with scripted_asks(return_value=None) as seen, \
         mock.patch("smoke_test.run_smoke_test", return_value=(True, "ok")):
        result = agent.enhance_multifile_game(
            SOURCE_GAME_ID, "add a reset button", "web:t", AGENT_CONFIG,
            games_dir=games_dir, job_id=job_id,
        )

    assert seen == []
    assert result["success"] is False
    assert result["error"] == "cancelled by user"
    # The half-written fork must be cleaned up, same as any other failed run.
    forks = [p for p in games_dir.iterdir() if p.name != "click-counter-src"]
    assert forks == []


# ---------------------------------------------------------------------------
# job_runner._run_job must not clobber a cancelled status
# ---------------------------------------------------------------------------

def test_run_job_does_not_overwrite_cancelled_status_on_success(isolated_db, games_dir):
    job_id = "2" * 32
    db.create_generation_request(job_id=job_id, kind="create", prompt="p", requested_by="web:t")
    conn = db.get_connection()
    job = db.get_generation_request(job_id, conn=conn)

    fake_result = {
        "success": True, "game_id": "g" * 32, "slug": "some-game", "title": "Some Game",
        "description": "d", "version": 1, "notes": "", "attempts": 1,
        "input_tokens": 1, "output_tokens": 1, "cached_tokens": 0, "tokens_used": 2,
        "model": "m", "effort": "high", "error": None, "duration_seconds": 0.1,
    }

    def fake_generate(*args, **kwargs):
        # Simulate a cancel racing in after generate_game() already
        # committed to success, but before job_runner writes the final row.
        db.update_generation_request(job_id, status="cancelled", error="cancelled by user", conn=conn)
        return fake_result

    with mock.patch.object(gg, "generate_game", side_effect=fake_generate):
        job_runner._run_job(conn, job, GEN_CONFIG, games_dir)

    updated = db.get_generation_request(job_id, conn=conn)
    assert updated["status"] == "cancelled"
    assert updated["result_game_id"] is None


def test_run_job_skips_already_cancelled_job_entirely(isolated_db, games_dir):
    job_id = "3" * 32
    db.create_generation_request(job_id=job_id, kind="create", prompt="p", requested_by="web:t")
    conn = db.get_connection()
    db.update_generation_request(job_id, status="cancelled", error="cancelled by user", conn=conn)
    job = db.get_generation_request(job_id, conn=conn)

    with mock.patch.object(gg, "generate_game") as mock_generate:
        job_runner._run_job(conn, job, GEN_CONFIG, games_dir)

    mock_generate.assert_not_called()
