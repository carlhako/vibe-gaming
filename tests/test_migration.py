"""Migration idempotency: running migrate_to_guid_schema.py twice against
the same DB must not mint duplicate game_ids or raise."""

import sqlite3

import db
import migrate_to_guid_schema as migrate


def test_migration_is_idempotent_on_fresh_db(isolated_db, games_dir):
    migrate.migrate(games_dir, str(isolated_db))
    migrate.migrate(games_dir, str(isolated_db))  # must not raise

    conn = sqlite3.connect(str(isolated_db))
    conn.row_factory = sqlite3.Row
    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    assert "web_games" in tables


def test_migration_from_old_schema_mints_one_game_id_per_row_and_is_idempotent(isolated_db, games_dir):
    conn = sqlite3.connect(str(isolated_db))
    conn.execute(
        """
        CREATE TABLE web_games (
            slug TEXT PRIMARY KEY, title TEXT, description TEXT, requested_by TEXT,
            status TEXT, attempts INTEGER, version INTEGER, model TEXT, effort TEXT,
            duration_seconds REAL, error TEXT, created_at TEXT, updated_at TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO web_games VALUES ('old-game', 'Old Game', 'd', 'sys', 'success', 1, 1, "
        "NULL, NULL, NULL, NULL, '2024-01-01T00:00:00Z', '2024-01-01T00:00:00Z')"
    )
    conn.commit()
    conn.close()

    (games_dir / "old-game").mkdir()
    (games_dir / "old-game" / "index.html").write_text("<html></html>")

    migrate.migrate(games_dir, str(isolated_db))

    conn2 = sqlite3.connect(str(isolated_db))
    conn2.row_factory = sqlite3.Row
    rows_after_first = conn2.execute("SELECT * FROM web_games").fetchall()
    assert len(rows_after_first) == 1
    game_id_after_first = rows_after_first[0]["game_id"]
    assert game_id_after_first
    conn2.close()

    migrate.migrate(games_dir, str(isolated_db))  # run again - must be a no-op

    conn3 = sqlite3.connect(str(isolated_db))
    conn3.row_factory = sqlite3.Row
    rows_after_second = conn3.execute("SELECT * FROM web_games").fetchall()
    assert len(rows_after_second) == 1, "second run must not duplicate the row"
    assert rows_after_second[0]["game_id"] == game_id_after_first, (
        "second run must not mint a new game_id for an already-migrated row"
    )
    conn3.close()
