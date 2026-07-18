"""
One-off migration: move `web_games` from the pre-GUID schema (slug as
primary key) to the GUID schema (game_id as primary key, slug derived and
unique), and create the generation_requests/generation_attempts/ratings/
access_log tables alongside it. Mirrors game_id/parent_game_id/root_game_id
into each game's meta.json so the filesystem stays self-describing.

Safe to re-run:
- If web_games doesn't exist yet, just creates the full current schema.
- If web_games already has a game_id column, only backfills any row whose
  game_id is still NULL/empty (should be a no-op on a healthy DB) and does
  not touch already-migrated rows.
- If web_games exists in the old (pre-GUID) shape, rebuilds it in place,
  preserving created_at/updated_at and all other original column values.

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
        return

    if _has_column(conn, "web_games", "game_id"):
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
        conn.close()
        if not rows:
            print("Schema already current; nothing to backfill.")
        return

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
    conn.close()
    print(f"Migrated {migrated} row(s) to the GUID schema.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--games-dir", default="games")
    parser.add_argument("--db", default=db.DB_PATH)
    args = parser.parse_args()
    migrate(Path(args.games_dir), args.db)
