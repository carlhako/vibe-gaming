"""job_runner._run_job's kill-switch pre-check: a job claimed while AI
generation is disabled must fail immediately without ever calling
game_generator/game_enhancer, so no wasted retry loop runs against a
chokepoint guaranteed to reject it.

Also covers the git_sync dispatch at the end of a successful job: the
call site (job["kind"] in ("create", "enhance", "explode")) had no direct
test coverage, which is exactly the kind of line a future accidental
narrowing of that tuple would silently regress."""

from unittest import mock

import agent
import ai_qa
import db
import game_enhancer
import game_generator
import git_sync
import job_runner

CONFIG = {
    "game_web": {"host": "localhost", "port": 8600, "base_url": ""},
    "newaiwebgame": {"model": "", "effort": "high", "timeout_seconds": 5,
                      "max_attempts": 3, "smoke_test_timeout_seconds": 5},
}

CONFIG_GIT_SYNC = {**CONFIG, "git_sync": {"enabled": True}}


def _success_result(slug="some-game", answer=None):
    return {
        "success": True, "game_id": "g" * 32, "slug": slug,
        "attempts": 1, "model": "deepseek-v4-flash", "effort": "high",
        "duration_seconds": 1.0, "input_tokens": 10, "output_tokens": 10,
        "cached_tokens": 0, "tokens_used": 20, "error": None, "answer": answer,
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


def test_run_job_pushes_to_git_for_create_enhance_explode(isolated_db, games_dir):
    """The three kinds that produce a game directory must all reach
    git_sync.push_game when it's enabled - the dispatch tuple at
    job_runner.py must never silently narrow to exclude one of them."""
    cases = [
        ("create", game_generator, "generate_game"),
        ("enhance", agent, "enhance_game_auto_format"),
        ("explode", agent, "explode_game"),
    ]
    for kind, module, func_name in cases:
        job_id = (kind + "0" * 32)[:32]
        db.create_generation_request(
            job_id=job_id, kind=kind, prompt="a maze game", requested_by="web:x",
            source_game_id="s" * 32 if kind != "create" else None,
        )
        conn = db.get_connection()
        job = db.get_generation_request(job_id, conn=conn)

        with mock.patch.object(module, func_name, return_value=_success_result()), \
             mock.patch.object(git_sync, "push_game") as mock_push:
            job_runner._run_job(conn, job, CONFIG_GIT_SYNC, games_dir)

        mock_push.assert_called_once()
        assert mock_push.call_args[0][0] == games_dir / "some-game"

        updated = db.get_generation_request(job_id, conn=conn)
        assert updated["status"] == "success"
        assert updated["git_push_status"] == "pushed"
        assert updated["git_push_error"] is None


def test_run_job_does_not_push_for_ask_jobs(isolated_db, games_dir):
    """kind='ask' never produces a game directory, so it must never reach
    git_sync.push_game even with git_sync enabled."""
    job_id = "q" * 32
    db.create_generation_request(
        job_id=job_id, kind="ask", prompt="how do I win?", requested_by="web:x",
        source_game_id="s" * 32,
    )
    conn = db.get_connection()
    job = db.get_generation_request(job_id, conn=conn)

    ask_result = _success_result(slug=None, answer="Jump over the gap.")
    ask_result["game_id"] = None
    with mock.patch.object(ai_qa, "answer_question", return_value=ask_result), \
         mock.patch.object(git_sync, "push_game") as mock_push:
        job_runner._run_job(conn, job, CONFIG_GIT_SYNC, games_dir)

    mock_push.assert_not_called()

    updated = db.get_generation_request(job_id, conn=conn)
    assert updated["status"] == "success"
    assert updated["git_push_status"] is None


def test_run_job_records_git_push_failure(isolated_db, games_dir):
    """A git_sync.push_game failure must never fail the job (the game
    already generated/enhanced fine) but must be recorded on the row so
    it's visible on the admin History tab instead of only printed."""
    job_id = "e" * 32
    db.create_generation_request(
        job_id=job_id, kind="enhance", prompt="add double jump", requested_by="web:x",
        source_game_id="s" * 32,
    )
    conn = db.get_connection()
    job = db.get_generation_request(job_id, conn=conn)

    with mock.patch.object(agent, "enhance_game_auto_format", return_value=_success_result()), \
         mock.patch.object(git_sync, "push_game",
                            side_effect=git_sync.GitSyncError("non-fast-forward")):
        job_runner._run_job(conn, job, CONFIG_GIT_SYNC, games_dir)

    updated = db.get_generation_request(job_id, conn=conn)
    assert updated["status"] == "success"  # push failure never fails the job
    assert updated["git_push_status"] == "failed"
    assert "non-fast-forward" in updated["git_push_error"]


def test_run_job_skips_push_when_git_sync_disabled(isolated_db, games_dir):
    job_id = "d" * 32
    db.create_generation_request(
        job_id=job_id, kind="create", prompt="a maze game", requested_by="web:x",
    )
    conn = db.get_connection()
    job = db.get_generation_request(job_id, conn=conn)

    with mock.patch.object(game_generator, "generate_game", return_value=_success_result()), \
         mock.patch.object(git_sync, "push_game") as mock_push:
        job_runner._run_job(conn, job, CONFIG, games_dir)  # no git_sync block in CONFIG

    mock_push.assert_not_called()
    updated = db.get_generation_request(job_id, conn=conn)
    assert updated["git_push_status"] is None
