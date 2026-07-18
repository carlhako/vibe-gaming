"""
SQLite database layer for vibegames.

Single source of truth for the game registry (`web_games`). Uses stdlib
sqlite3. Schema is created on first connection via get_connection().

All functions accept an optional `conn` parameter for dependency injection
(useful in tests with in-memory databases).
"""

import sqlite3
import datetime

DB_PATH = "vibegames.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS web_games (
    slug             TEXT PRIMARY KEY,
    title            TEXT NOT NULL,
    description      TEXT NOT NULL,
    requested_by     TEXT NOT NULL,
    status           TEXT NOT NULL,
    attempts         INTEGER NOT NULL,
    version          INTEGER NOT NULL DEFAULT 1,
    model            TEXT,
    effort           TEXT,
    duration_seconds REAL,
    error            TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);
"""


def _now():
    return datetime.datetime.now(datetime.UTC).isoformat().replace("+00:00", "Z")


def _c(conn):
    return conn if conn is not None else get_connection()


def get_connection(check_same_thread=True):
    conn = sqlite3.connect(DB_PATH, check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def register_web_game(slug, title, description, requested_by, status, attempts,
                       version=1, model=None, effort=None, duration_seconds=None,
                       error=None, conn=None):
    """Insert or update the live registry row for a generated web game.

    One row per slug: a re-registration (e.g. after enhancement) UPSERTs in
    place, bumping updated_at while created_at is preserved from the
    original insert.
    """
    c = _c(conn)
    now = _now()
    c.execute(
        """
        INSERT INTO web_games
            (slug, title, description, requested_by, status, attempts, version,
             model, effort, duration_seconds, error, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(slug) DO UPDATE
            SET title=excluded.title, description=excluded.description,
                status=excluded.status, attempts=excluded.attempts,
                version=excluded.version, model=excluded.model,
                effort=excluded.effort, duration_seconds=excluded.duration_seconds,
                error=excluded.error, updated_at=excluded.updated_at
        """,
        (slug, title, description, requested_by, status, attempts, version,
         model, effort, duration_seconds, error, now, now),
    )
    c.commit()


def get_web_game(slug, conn=None):
    c = _c(conn)
    row = c.execute("SELECT * FROM web_games WHERE slug=?", (slug,)).fetchone()
    return dict(row) if row else None


def get_web_games(conn=None):
    c = _c(conn)
    rows = c.execute("SELECT * FROM web_games ORDER BY slug").fetchall()
    return [dict(r) for r in rows]
