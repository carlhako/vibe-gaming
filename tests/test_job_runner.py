"""job_runner._run_job's kill-switch pre-check: a job claimed while AI
generation is disabled must fail immediately without ever calling
game_generator/game_enhancer, so no wasted retry loop runs against a
chokepoint guaranteed to reject it."""

from unittest import mock

import db
import game_enhancer
import game_generator
import job_runner

CONFIG = {
    "game_web": {"host": "localhost", "port": 8600, "base_url": ""},
    "newaiwebgame": {"model": "", "effort": "high", "timeout_seconds": 5,
                      "max_attempts": 3, "smoke_test_timeout_seconds": 5},
}


def test_run_job_short_circuits_when_ai_disabled(isolated_db, games_dir):
    job_id = "a" * 32
    db.create_generation_request(
        job_id=job_id, kind="create", prompt="a maze game", requested_by="web:x",
    )
    conn = db.get_connection()
    job = db.get_generation_request(job_id, conn=conn)

    db.set_ai_generation_enabled(False)

    with mock.patch.object(game_generator, "generate_game") as mock_generate, \
         mock.patch.object(game_enhancer, "enhance_game") as mock_enhance:
        job_runner._run_job(conn, job, CONFIG, games_dir)

    mock_generate.assert_not_called()
    mock_enhance.assert_not_called()

    updated = db.get_generation_request(job_id, conn=conn)
    assert updated["status"] == "failed"
    assert "disabled" in updated["error"]
