"""Migration idempotency: running migrate_to_guid_schema.py twice against
the same DB must not mint duplicate game_ids or raise."""

import json
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


def test_migration_registers_hand_written_game_with_no_db_row_at_all(isolated_db, games_dir):
    """A game like games/sample-game/ - an index.html + meta.json that was
    never passed through register_web_game(), so it has no web_games row
    and no game_id at all - must get picked up and registered, not just
    rows already present in web_games with a NULL game_id."""
    game_dir = games_dir / "sample-game"
    game_dir.mkdir()
    (game_dir / "index.html").write_text("<html>snake</html>")
    (game_dir / "meta.json").write_text(json.dumps({
        "title": "Sample Snake", "description": "placeholder",
        "requested_by": "system", "created_at": "2026-07-17T00:00:00Z", "version": 1,
    }))

    migrate.migrate(games_dir, str(isolated_db))

    meta = json.loads((game_dir / "meta.json").read_text())
    assert meta["game_id"], "meta.json must gain a game_id"
    assert meta["parent_game_id"] is None
    assert meta["root_game_id"] == meta["game_id"]

    row = db.get_web_game(meta["game_id"])
    assert row is not None
    assert row["slug"] == "sample-game"
    assert row["title"] == "Sample Snake"
    assert row["root_game_id"] == meta["game_id"]

    game_id_after_first = meta["game_id"]

    migrate.migrate(games_dir, str(isolated_db))  # run again - must be a no-op

    meta_after_second = json.loads((game_dir / "meta.json").read_text())
    assert meta_after_second["game_id"] == game_id_after_first, (
        "second run must not mint a new game_id for an already-registered game"
    )
    all_rows = db.get_web_games()
    matching = [r for r in all_rows if r["slug"] == "sample-game"]
    assert len(matching) == 1, "second run must not create a duplicate row"
