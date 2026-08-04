#!/usr/bin/env python3
"""
One-off sweep of orphaned generation_requests rows for cases where the
production server can't be restarted to trigger the usual
`start_workers()` -> `db.sweep_orphaned_requests()` cleanup.

A worker that crashes after claiming a job (atomic
`UPDATE ... SET status='generating' WHERE status='queued'`) but before
recording any progress leaves the row stuck in 'generating' forever.
`db.claim_next_queued_request` allows exactly one 'generating' job
site-wide, so a stuck row blocks the entire queue until something clears
it - which is normally the next `start_workers()` call, since the sweep
runs unconditionally on process startup.

This script exists for the case where that startup sweep doesn't run -
typically because the server is up and the orphan was created after
startup, or because restarting isn't desirable right now (mid-deploy,
active users, the user is still diagnosing why the worker died). It is
NOT a substitute for restarting the worker; it just unblocks the queue.

Always makes a timestamped copy of the DB file before touching anything,
whether or not any fix is applied - same policy as `db_assess.py`.

Usage:
    python3 db_cleanup.py [--db-path vibegames.db] [--yes]

    --yes   apply the sweep without per-item confirmation
            (a backup is still made first)
"""

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import db as db_module


def backup_db(db_path: Path) -> Path | None:
    if not db_path.exists():
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = db_path.with_name(f"{db_path.name}.bak-{stamp}")
    shutil.copy2(db_path, backup_path)
    return backup_path


def confirm(prompt: str, auto_yes: bool) -> bool:
    if auto_yes:
        return True
    reply = input(f"{prompt} [y/N] ").strip().lower()
    return reply in ("y", "yes")


def find_stuck(conn) -> list:
    """Every generation_requests row in status='generating'. In normal
    operation the live worker holds exactly one; more than one means
    either a worker just claimed one and hasn't written progress yet
    (safe to leave alone, transient), or one or more are orphans from
    a crashed worker. Listing them all and asking before sweeping is
    the safe default."""
    return [
        dict(r)
        for r in conn.execute(
            "SELECT job_id, kind, attempts, source_game_id, requested_by, "
            "created_at, updated_at FROM generation_requests "
            "WHERE status='generating' ORDER BY updated_at"
        ).fetchall()
    ]


def age_minutes(updated_at_iso: str) -> float:
    """Best-effort age in minutes from an ISO-8601 UTC timestamp with
    trailing 'Z'. Returns -1.0 on parse failure so it still sorts."""
    try:
        ts = datetime.fromisoformat(updated_at_iso.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return -1.0
    return (datetime.now(timezone.utc) - ts).total_seconds() / 60.0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--db-path", default=db_module.DB_PATH)
    parser.add_argument(
        "--yes", action="store_true", help="apply the sweep without prompting"
    )
    args = parser.parse_args()

    db_path = Path(args.db_path)

    if not db_path.exists():
        print(f"No DB at {db_path} - nothing to clean up.")
        return 0

    backup_path = backup_db(db_path)
    print(f"Backed up {db_path} -> {backup_path}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    stuck = find_stuck(conn)

    if not stuck:
        print("\nNo 'generating' rows are stuck. Queue is clean.")
        conn.close()
        return 0

    print(
        f"\n{len(stuck)} row(s) currently in status='generating':"
    )
    for r in stuck:
        age = age_minutes(r["updated_at"])
        age_str = f"{age:.1f} min" if age >= 0 else "?"
        print(
            f"  - job_id={r['job_id']}  kind={r['kind']}  attempts={r['attempts']}  "
            f"updated_at={r['updated_at']}  age={age_str}  "
            f"source_game_id={r['source_game_id'] or '<none>'}"
        )
    print(
        "\nA live worker holds at most one of these at any time. Anything "
        "older than a few minutes is almost certainly an orphan from a "
        "crashed worker; anything just-claimed is a normal in-flight run."
    )

    if confirm(
        f"    Sweep all {len(stuck)} stuck row(s) (set status='failed', "
        f"error='interrupted by restart')?",
        args.yes,
    ):
        now = db_module.now_iso()
        with conn:
            cur = conn.execute(
                "UPDATE generation_requests SET status='failed', "
                "error='interrupted by restart', updated_at=? "
                "WHERE status='generating'",
                (now,),
            )
        conn.commit()
        print(f"    -> swept {cur.rowcount} row(s).")
    else:
        print("    -> skipped.")

    conn.close()
    print(f"\nDone. Backup at {backup_path} if you need to roll back.")
    return 0


if __name__ == "__main__":
    sys.exit(main())