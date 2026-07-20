"""db.sync_games_from_disk(): the startup backfill that gives bundled
games (game_id committed in meta.json, vibegames.db gitignored) their
web_games row on a fresh clone."""

import json

import db


def write_game(games_dir, slug, meta):
    d = games_dir / slug
    d.mkdir()
    (d / "index.html").write_text("<canvas></canvas>", encoding="utf-8")
    if meta is not None:
        (d / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return d


def test_registers_game_with_meta_game_id(isolated_db, games_dir):
    write_game(games_dir, "block-dodge", {
        "title": "Block Dodge",
        "description": "Dodge blocks.",
        "game_id": "a" * 32,
        "root_game_id": "a" * 32,
        "version": 2,
    })
    conn = db.get_connection()

    assert db.sync_games_from_disk(games_dir, conn=conn) == 1
    row = db.get_web_game("a" * 32, conn=conn)
    assert row["slug"] == "block-dodge"
    assert row["title"] == "Block Dodge"
    assert row["root_game_id"] == "a" * 32
    assert row["version"] == 2
    assert row["status"] == "success"


def test_rerun_is_a_noop_and_preserves_row_changes(isolated_db, games_dir):
    write_game(games_dir, "block-dodge", {
        "title": "Block Dodge", "game_id": "a" * 32,
    })
    conn = db.get_connection()
    db.sync_games_from_disk(games_dir, conn=conn)
    db.record_rating("a" * 32, 1, "uid-1", "10.0.0.1", conn=conn)
    db.rename_game("a" * 32, "Renamed", conn=conn)

    assert db.sync_games_from_disk(games_dir, conn=conn) == 0
    row = db.get_web_game("a" * 32, conn=conn)
    assert row["title"] == "Renamed"
    assert row["thumbs_up"] == 1


def test_skips_games_without_game_id_or_meta(isolated_db, games_dir):
    write_game(games_dir, "no-id", {"title": "No Id"})
    write_game(games_dir, "no-meta", None)
    (games_dir / "not-a-game").mkdir()  # no index.html
    conn = db.get_connection()

    assert db.sync_games_from_disk(games_dir, conn=conn) == 0
    assert conn.execute("SELECT COUNT(*) FROM web_games").fetchone()[0] == 0


def test_missing_games_dir_returns_zero(isolated_db, tmp_path):
    assert db.sync_games_from_disk(tmp_path / "nope") == 0
