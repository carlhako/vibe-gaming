"""
One-off migration: move `web_games` from the pre-GUID schema (slug as
primary key) to the GUID schema (game_id as primary key, slug derived and
unique), and create the generation_requests/generation_attempts/ratings/
access_log tables alongside it. Mirrors game_id/parent_game_id/root_game_id
into each game's meta.json so the filesystem stays self-describing.

Also picks up games that were never registered in the DB at all - e.g. a
hand-written game like games/sample-game/, which has an index.html and a
meta.json but no web_games row and no game_id, because nothing ever called
register_web_game() for it. Without this step such a game keeps working
(it still lists and plays fine off the disk scan) but has no game_id, so
it can't be rated or enhanced - see _sync_unregistered_games().

Safe to re-run:
- If web_games doesn't exist yet, just creates the full current schema.
- If web_games already has a game_id column, only backfills any row whose
  game_id is still NULL/empty (should be a no-op on a healthy DB) and does
  not touch already-migrated rows.
- If web_games exists in the old (pre-GUID) shape, rebuilds it in place,
  preserving created_at/updated_at and all other original column values.
- Any games/<slug>/ directory whose meta.json already has a game_id is
  left untouched by the disk-sync step.

Usage: python3 migrate_to_guid_schema.py [--games-dir games] [--db vibegames.db]
"""

import argparse
import json
import shutil
import sqlite3
from pathlib import Path

import db


def _table_exists(conn, name) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _has_column(conn, table, column) -> bool:
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    return column in cols


def _update_meta_json(games_dir: Path, slug: str, game_id: str,
                       parent_game_id: str | None, root_game_id: str) -> None:
    meta_path = games_dir / slug / "meta.json"
    if not meta_path.exists():
        return
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        meta = {}
    meta["game_id"] = game_id
    meta["parent_game_id"] = parent_game_id
    meta["root_game_id"] = root_game_id
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    print(f"  updated {meta_path}")


def _sync_unregistered_games(conn, games_dir: Path) -> int:
    """Register any games/<slug>/ directory that has no game_id yet -
    whether that's because it predates web_games entirely (no row by that
    slug at all, e.g. a hand-written game) or because it just never got a
    meta.json rewrite. Matches an existing web_games row by slug first (so
    this never mints a second game_id for something already registered),
    then inserts a fresh row only if nothing matched. Returns the count of
    games touched."""
    if not games_dir.exists():
        return 0
    synced = 0
    for entry in sorted(games_dir.iterdir()):
        if not entry.is_dir() or entry.name == "backups":
            continue
        if not (entry / "index.html").exists():
            continue
        slug = entry.name
        meta_path = entry / "meta.json"
        meta = {}
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                meta = {}
        if meta.get("game_id"):
            continue  # already has an identity - nothing to do

        existing = conn.execute(
            "SELECT * FROM web_games WHERE slug=?", (slug,)
        ).fetchone()
        if existing is not None:
            game_id = existing["game_id"]
            parent_game_id = existing["parent_game_id"]
            root_game_id = existing["root_game_id"]
        else:
            game_id = db.mint_game_id()
            parent_game_id = None
            root_game_id = game_id
            now = db.now_iso()
            conn.execute(
                """
                INSERT INTO web_games
                    (game_id, slug, title, description, requested_by, status, attempts,
                     version, model, effort, duration_seconds, error, parent_game_id,
                     root_game_id, thumbs_up, thumbs_down, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'success', 1, ?, NULL, NULL, NULL, NULL, NULL, ?,
                        0, 0, ?, ?)
                """,
                (
                    game_id, slug, meta.get("title", slug), meta.get("description", ""),
                    meta.get("requested_by") or "system", meta.get("version", 1),
                    root_game_id, meta.get("created_at") or now, now,
                ),
            )
            conn.commit()
            print(f"Registered previously-unregistered game '{slug}' -> game_id={game_id}")

        _update_meta_json(games_dir, slug, game_id, parent_game_id, root_game_id)
        synced += 1
    return synced


def migrate(games_dir: Path, db_path: str) -> None:
    db_file = Path(db_path)
    if db_file.exists():
        backup_path = db_file.with_suffix(db_file.suffix + ".bak")
        shutil.copy2(db_file, backup_path)
        print(f"Backed up {db_file} -> {backup_path}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    if not _table_exists(conn, "web_games"):
        conn.close()
        print("web_games table does not exist yet; creating current schema.")
        db.DB_PATH = db_path
        db.get_connection().close()
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

    elif _has_column(conn, "web_games", "game_id"):
        # Already on the new schema shape; just ensure the other new
        # tables exist and backfill any stray NULL game_id rows.
        conn.executescript(db.SCHEMA)
        conn.commit()
        rows = conn.execute(
            "SELECT * FROM web_games WHERE game_id IS NULL OR game_id = ''"
        ).fetchall()
        for row in rows:
            game_id = db.mint_game_id()
            conn.execute(
                "UPDATE web_games SET game_id=?, root_game_id=?, parent_game_id=NULL "
                "WHERE slug=?",
                (game_id, game_id, row["slug"]),
            )
            _update_meta_json(games_dir, row["slug"], game_id, None, game_id)
            print(f"Backfilled stray row {row['slug']} -> game_id={game_id}")
        conn.commit()
        if not rows:
            print("Schema already current; nothing to backfill.")

    else:
        # Old (pre-GUID) schema: slug is the primary key, no game_id column.
        # Rebuild the table under a new name, migrate rows across, drop the old one.
        print("Old schema detected (web_games keyed on slug); rebuilding table.")
        old_rows = conn.execute("SELECT * FROM web_games").fetchall()
        conn.execute("ALTER TABLE web_games RENAME TO web_games_old")
        conn.executescript(db.SCHEMA)  # creates the new web_games + all other tables

        migrated = 0
        for row in old_rows:
            game_id = db.mint_game_id()
            conn.execute(
                """
                INSERT INTO web_games
                    (game_id, slug, title, description, requested_by, status, attempts,
                     version, model, effort, duration_seconds, error, parent_game_id,
                     root_game_id, thumbs_up, thumbs_down, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, 0, 0, ?, ?)
                """,
                (game_id, row["slug"], row["title"], row["description"], row["requested_by"],
                 row["status"], row["attempts"], row["version"], row["model"], row["effort"],
                 row["duration_seconds"], row["error"], game_id,
                 row["created_at"], row["updated_at"]),
            )
            _update_meta_json(games_dir, row["slug"], game_id, None, game_id)
            print(f"Migrated {row['slug']} -> game_id={game_id}")
            migrated += 1

        conn.execute("DROP TABLE web_games_old")
        conn.commit()
        print(f"Migrated {migrated} row(s) to the GUID schema.")

    synced = _sync_unregistered_games(conn, games_dir)
    if synced:
        print(f"Synced {synced} previously-unregistered game(s) from disk into the DB.")

    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--games-dir", default="games")
    parser.add_argument("--db", default=db.DB_PATH)
    args = parser.parse_args()
    migrate(Path(args.games_dir), args.db)
