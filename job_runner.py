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

import db
import game_enhancer
import game_generator


def _run_job(conn, job: dict, config: dict, games_dir: Path) -> None:
    job_id = job["job_id"]
    t0 = time.monotonic()
    try:
        if job["kind"] == "create":
            result = game_generator.generate_game(
                job["prompt"], job["requested_by"], config,
                db_conn=conn, games_dir=games_dir, job_id=job_id,
            )
        elif job["kind"] == "enhance":
            result = game_enhancer.enhance_game(
                job["source_game_id"], job["prompt"], job["requested_by"], config,
                db_conn=conn, games_dir=games_dir, job_id=job_id,
                new_title=job.get("new_title"),
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
            duration_seconds=result["duration_seconds"], tokens_used=result["tokens_used"],
            conn=conn,
        )
    else:
        db.update_generation_request(
            job_id, status="failed", attempts=result["attempts"], model=result["model"],
            effort=result["effort"], duration_seconds=result["duration_seconds"],
            tokens_used=result["tokens_used"], error=result["error"] or "unknown error",
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
