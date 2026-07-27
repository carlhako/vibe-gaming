"""
job_runner — background execution for generation_requests.

A DB-polling worker, not an in-memory queue.Queue: gunicorn's multi-process
model means a queue held in one worker process's memory would be invisible
to requests handled by another process, so every worker thread/process
instead polls the same durable `generation_requests` table and claims work
via an atomic conditional UPDATE (db.claim_next_queued_request). That makes
every process/thread an interchangeable, race-safe consumer.

start_workers() should be called exactly once per process (dev: in
app.py's __main__ before app.run(); prod: from gunicorn.conf.py's
post_fork hook, so it runs once per worker process).
"""

from __future__ import annotations

import threading
import time
import traceback
from pathlib import Path

import agent
import ai_qa
import db
import game_generator
import git_sync


def _run_job(conn, job: dict, config: dict, games_dir: Path) -> None:
    job_id = job["job_id"]
    t0 = time.monotonic()
    if not db.is_ai_generation_enabled(conn=conn):
        # Job was queued before an admin flipped the kill switch off — fail
        # it immediately rather than burning a full retry loop against a
        # chokepoint (ai_client._client()) that's guaranteed to reject it.
        db.update_generation_request(
            job_id, status="failed", attempts=job.get("attempts", 0) + 1,
            duration_seconds=time.monotonic() - t0,
            error="AI generation is currently disabled by an admin", conn=conn,
        )
        db.add_generation_attempt(
            job_id, job.get("attempts", 0) + 1, "ai_error",
            detail="AI generation is currently disabled by an admin", conn=conn,
        )
        return
    try:
        if job["kind"] == "create":
            result = game_generator.generate_game(
                job["prompt"], job["requested_by"], config,
                db_conn=conn, games_dir=games_dir, job_id=job_id,
                creator_uid=job.get("creator_uid"),
            )
        elif job["kind"] == "enhance":
            # agent.enhance_game_auto_format is the single dispatch point:
            # multi-file sources go through the ReAct editing agent (Sprint
            # 2), single-file sources near the output-token ceiling get
            # auto-exploded into multi-file first (Sprint 5's dual-format
            # policy), and everything else keeps using the existing
            # whole-file resubmit loop unchanged.
            result = agent.enhance_game_auto_format(
                job["source_game_id"], job["prompt"], job["requested_by"], config,
                db_conn=conn, games_dir=games_dir, job_id=job_id,
                new_title=job.get("new_title"), creator_uid=job.get("creator_uid"),
            )
        elif job["kind"] == "explode":
            # Admin-triggered format conversion (/admin/stats' Games tab):
            # the same explode pass enhance_game_auto_format runs internally
            # for an oversized single-file source, but requested on its own
            # so the resulting multi-file fork is a visible arcade entry
            # rather than a hidden intermediate.
            result = agent.explode_game(
                job["source_game_id"], job["requested_by"], config,
                db_conn=conn, games_dir=games_dir, job_id=job_id,
                new_title=job.get("new_title"), creator_uid=job.get("creator_uid"),
            )
        elif job["kind"] == "ask":
            # A single quick DeepSeek call, not a forking retry loop — see
            # ai_qa.py. Never produces a game_id (result["game_id"] stays
            # None below, so result_game_id is never set on this job).
            result = ai_qa.answer_question(
                job["source_game_id"], job["prompt"], job["requested_by"], config,
                db_conn=conn, games_dir=games_dir, job_id=job_id,
                creator_uid=job.get("creator_uid"),
            )
        else:
            raise ValueError(f"unknown job kind: {job['kind']!r}")
    except Exception as exc:  # noqa: BLE001 - a job must never take the worker thread down
        db.update_generation_request(
            job_id, status="failed", attempts=job.get("attempts", 0) + 1,
            duration_seconds=time.monotonic() - t0,
            error=f"internal error: {exc}", conn=conn,
        )
        db.add_generation_attempt(
            job_id, job.get("attempts", 0) + 1, "ai_error",
            detail=f"internal error: {exc}\n{traceback.format_exc()}", conn=conn,
        )
        return

    if result["success"]:
        db.update_generation_request(
            job_id, status="success", result_game_id=result["game_id"],
            attempts=result["attempts"], model=result["model"], effort=result["effort"],
            duration_seconds=result["duration_seconds"],
            input_tokens=result["input_tokens"], output_tokens=result["output_tokens"],
            tokens_used=result["tokens_used"], cached_tokens=result.get("cached_tokens"),
            answer=result.get("answer"),
            conn=conn,
        )
        # Best-effort: push the finished game directory to GitHub. Never
        # lets a git failure (bad token, network blip, GitHub outage) turn
        # a successful generation/enhancement into a failed job — the game
        # already generated and smoke-tested fine.
        if job["kind"] in ("create", "enhance", "explode") and git_sync.is_enabled(config):
            try:
                git_sync.push_game(games_dir / result["slug"], job["prompt"], config)
            except git_sync.GitSyncError as exc:
                print(f"job_runner: git push failed for job {job_id}: {exc}")
    else:
        db.update_generation_request(
            job_id, status="failed", attempts=result["attempts"], model=result["model"],
            effort=result["effort"], duration_seconds=result["duration_seconds"],
            input_tokens=result["input_tokens"], output_tokens=result["output_tokens"],
            tokens_used=result["tokens_used"], cached_tokens=result.get("cached_tokens"),
            error=result["error"] or "unknown error",
            answer=result.get("answer"),
            conn=conn,
        )


def _worker_loop(config: dict, games_dir: Path) -> None:
    conn = db.get_connection()  # one connection per thread, never shared
    poll_interval = config.get("job_runner", {}).get("poll_interval_seconds", 1)
    while True:
        job_id = db.claim_next_queued_request(conn=conn)
        if job_id is None:
            time.sleep(poll_interval)
            continue
        job = db.get_generation_request(job_id, conn=conn)
        _run_job(conn, job, config, games_dir)


def start_workers(config: dict, games_dir: Path, num_workers: int | None = None) -> None:
    """Sweep any jobs orphaned by a previous crash/restart, then spawn
    num_workers daemon poll-loop threads. Call once per process."""
    if num_workers is None:
        num_workers = config.get("job_runner", {}).get("workers", 1)

    swept = db.sweep_orphaned_requests()
    if swept:
        print(f"job_runner: swept {swept} orphaned job(s) from a previous run")

    for _ in range(num_workers):
        t = threading.Thread(target=_worker_loop, args=(config, games_dir), daemon=True)
        t.start()
