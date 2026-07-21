#!/usr/bin/env python3
"""
One-off health check for vibegames.db against the games/ directory on disk.

Cross-checks each games/<slug>/meta.json's committed game_id against the
web_games row for that slug, and flags the two ways they can drift apart:

  - stale game_id: a web_games row exists for this slug, but under a
    DIFFERENT game_id than the one now committed in meta.json. This
    happens when a game's meta.json game_id was (re)minted after the
    DB already had a row for that slug from an earlier migration/run -
    see connect-4-4 on the VM, 2026-07-21: plays/ratings recorded
    against the stale game_id are invisible everywhere else in the app,
    which always looks games up by the meta.json game_id, so play
    counts/ratings appear stuck at zero forever. Fix offered: rekey -
    UPDATE the row (and everything that references its game_id) to the
    correct id, preserving history.

  - dangling row: a web_games row whose slug has no matching
    games/<slug>/ directory on disk at all (game removed/renamed).
    Fix offered: delete the row and its plays/ratings; generation_requests
    rows are kept but have their reference to it cleared, so the audit
    trail isn't lost.

Disk games with no web_games row at all are also reported, but need no
action here - db.sync_games_from_disk() (called at every app startup)
already backfills those.

Always makes a timestamped copy of the DB file before touching anything,
whether or not any fix is applied.

Usage:
    python3 db_assess.py [--db-path vibegames.db] [--games-dir games] [--yes]

    --yes   apply every offered fix without per-item confirmation
            (a backup is still made first)
"""

import argparse
import json
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


def scan_disk(games_dir: Path) -> dict:
    """slug -> game_id (or None if meta.json is missing/unparseable/has
    no game_id), for every games/<slug>/ directory with an index.html."""
    disk = {}
    if not games_dir.exists():
        return disk
    for entry in sorted(games_dir.iterdir()):
        if not entry.is_dir() or not (entry / "index.html").exists():
            continue
        game_id = None
        meta_path = entry / "meta.json"
        if meta_path.exists():
            try:
                game_id = json.loads(meta_path.read_text(encoding="utf-8")).get("game_id")
            except (json.JSONDecodeError, OSError):
                game_id = None
        disk[entry.name] = game_id
    return disk


def confirm(prompt: str, auto_yes: bool) -> bool:
    if auto_yes:
        return True
    reply = input(f"{prompt} [y/N] ").strip().lower()
    return reply in ("y", "yes")


def dependent_counts(conn, game_id: str) -> dict:
    return {
        "plays": conn.execute(
            "SELECT COUNT(*) AS n FROM plays WHERE game_id=?", (game_id,)
        ).fetchone()["n"],
        "ratings": conn.execute(
            "SELECT COUNT(*) AS n FROM ratings WHERE game_id=?", (game_id,)
        ).fetchone()["n"],
        "children": conn.execute(
            "SELECT COUNT(*) AS n FROM web_games WHERE parent_game_id=? OR root_game_id=?",
            (game_id, game_id),
        ).fetchone()["n"],
    }


def rekey_game_id(conn, old_id: str, new_id: str) -> None:
    conn.execute("UPDATE plays SET game_id=? WHERE game_id=?", (new_id, old_id))
    conn.execute("UPDATE ratings SET game_id=? WHERE game_id=?", (new_id, old_id))
    conn.execute(
        "UPDATE generation_requests SET source_game_id=? WHERE source_game_id=?",
        (new_id, old_id),
    )
    conn.execute(
        "UPDATE generation_requests SET result_game_id=? WHERE result_game_id=?",
        (new_id, old_id),
    )
    conn.execute(
        "UPDATE web_games SET parent_game_id=? WHERE parent_game_id=?", (new_id, old_id)
    )
    conn.execute(
        "UPDATE web_games SET root_game_id=? WHERE root_game_id=?", (new_id, old_id)
    )
    conn.execute("UPDATE web_games SET game_id=? WHERE game_id=?", (new_id, old_id))


def delete_dangling(conn, game_id: str) -> None:
    conn.execute("DELETE FROM plays WHERE game_id=?", (game_id,))
    conn.execute("DELETE FROM ratings WHERE game_id=?", (game_id,))
    conn.execute(
        "UPDATE generation_requests SET source_game_id=NULL WHERE source_game_id=?",
        (game_id,),
    )
    conn.execute(
        "UPDATE generation_requests SET result_game_id=NULL WHERE result_game_id=?",
        (game_id,),
    )
    conn.execute("DELETE FROM web_games WHERE game_id=?", (game_id,))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db-path", default=db_module.DB_PATH)
    parser.add_argument("--games-dir", default="games")
    parser.add_argument("--yes", action="store_true", help="apply every offered fix without prompting")
    args = parser.parse_args()

    db_path = Path(args.db_path)
    games_dir = Path(args.games_dir)

    if not db_path.exists():
        print(f"No DB at {db_path} - nothing to assess.")
        return 0

    backup_path = backup_db(db_path)
    print(f"Backed up {db_path} -> {backup_path}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    disk = scan_disk(games_dir)
    db_rows = {
        r["slug"]: dict(r)
        for r in conn.execute("SELECT game_id, slug, title FROM web_games").fetchall()
    }

    stale = []      # (slug, disk_game_id, db_row)
    dangling = []   # db_row whose slug isn't on disk
    unregistered = []  # disk slug/game_id with no db row (informational only)

    for slug, game_id in disk.items():
        row = db_rows.get(slug)
        if row is None:
            if game_id:
                unregistered.append((slug, game_id))
            continue
        if game_id and row["game_id"] != game_id:
            stale.append((slug, game_id, row))

    for slug, row in db_rows.items():
        if slug not in disk:
            dangling.append(row)

    if unregistered:
        print(f"\n{len(unregistered)} disk game(s) with no web_games row yet (no action needed - "
              f"db.sync_games_from_disk() backfills these on next app startup):")
        for slug, game_id in unregistered:
            print(f"  - {slug} ({game_id})")

    if not stale and not dangling:
        print("\nNo orphaned rows found. DB and games/ agree on every slug.")
        conn.close()
        return 0

    made_changes = False

    if stale:
        print(f"\n{len(stale)} stale game_id row(s) - DB slug matches disk, but the game_id differs "
              f"from what's committed in meta.json:")
        for slug, disk_game_id, row in stale:
            counts = dependent_counts(conn, row["game_id"])
            print(f"\n  slug: {slug}")
            print(f"    db game_id:   {row['game_id']}  (title: {row['title']!r})")
            print(f"    disk game_id: {disk_game_id}  (from meta.json)")
            print(f"    dependent rows under db game_id: {counts['plays']} plays, "
                  f"{counts['ratings']} ratings, {counts['children']} fork(s) referencing it as parent/root")
            if confirm("    Rekey this row (and its plays/ratings/lineage refs) to the meta.json game_id?", args.yes):
                with conn:
                    rekey_game_id(conn, row["game_id"], disk_game_id)
                made_changes = True
                print("    -> rekeyed.")
            else:
                print("    -> skipped.")

    if dangling:
        print(f"\n{len(dangling)} dangling row(s) - web_games row with no matching games/<slug>/ on disk:")
        for row in dangling:
            counts = dependent_counts(conn, row["game_id"])
            print(f"\n  slug: {row['slug']}  title: {row['title']!r}  game_id: {row['game_id']}")
            print(f"    dependent rows: {counts['plays']} plays, {counts['ratings']} ratings, "
                  f"{counts['children']} fork(s) referencing it as parent/root")
            if counts["children"]:
                print("    -> skipped: other games list this as their parent/root; "
                      "resolve that lineage manually before deleting.")
                continue
            if confirm("    Delete this row and its plays/ratings? (generation_requests rows are kept, "
                       "reference cleared)", args.yes):
                with conn:
                    delete_dangling(conn, row["game_id"])
                made_changes = True
                print("    -> deleted.")
            else:
                print("    -> skipped.")

    conn.close()
    if made_changes:
        print(f"\nDone. Original DB backed up at {backup_path} if you need to roll back.")
    else:
        print(f"\nNo changes applied. Backup at {backup_path} can be discarded.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
