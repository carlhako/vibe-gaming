"""
SQLite database layer for vibegames.

Single source of truth for the game registry (`web_games`), the async
generation/enhancement job & audit trail (`generation_requests`,
`generation_attempts`), player ratings (`ratings`), and HTTP access
logging (`access_log`). Uses stdlib sqlite3. Schema is created on first
connection via get_connection().

All functions accept an optional `conn` parameter for dependency injection
(useful in tests with in-memory databases).
"""

import re
import sqlite3
import datetime
import uuid

DB_PATH = "vibegames.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS web_games (
    game_id          TEXT PRIMARY KEY,
    slug             TEXT NOT NULL UNIQUE,
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
    parent_game_id   TEXT REFERENCES web_games(game_id),
    root_game_id     TEXT REFERENCES web_games(game_id),
    thumbs_up        INTEGER NOT NULL DEFAULT 0,
    thumbs_down      INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_web_games_parent ON web_games(parent_game_id);
CREATE INDEX IF NOT EXISTS idx_web_games_root ON web_games(root_game_id);

CREATE TABLE IF NOT EXISTS generation_requests (
    job_id           TEXT PRIMARY KEY,
    kind             TEXT NOT NULL,
    prompt           TEXT NOT NULL,
    new_title        TEXT,
    source_game_id   TEXT REFERENCES web_games(game_id),
    result_game_id   TEXT REFERENCES web_games(game_id),
    requested_by     TEXT NOT NULL,
    status           TEXT NOT NULL,
    attempts         INTEGER NOT NULL DEFAULT 0,
    model            TEXT,
    effort           TEXT,
    duration_seconds REAL,
    error            TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_genreq_status ON generation_requests(status);

CREATE TABLE IF NOT EXISTS generation_attempts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id         TEXT NOT NULL REFERENCES generation_requests(job_id),
    attempt_number INTEGER NOT NULL,
    outcome        TEXT NOT NULL,
    detail         TEXT,
    tokens_used    INTEGER,
    created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_genattempts_job ON generation_attempts(job_id);

CREATE TABLE IF NOT EXISTS ratings (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id    TEXT NOT NULL REFERENCES web_games(game_id),
    vote       INTEGER NOT NULL CHECK (vote IN (-1, 1)),
    client_uid TEXT NOT NULL,
    ip_address TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(game_id, client_uid),
    UNIQUE(game_id, ip_address)
);
CREATE INDEX IF NOT EXISTS idx_ratings_game ON ratings(game_id);

CREATE TABLE IF NOT EXISTS access_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    method      TEXT NOT NULL,
    path        TEXT NOT NULL,
    status_code INTEGER NOT NULL,
    ip_address  TEXT NOT NULL,
    user_agent  TEXT,
    client_uid  TEXT,
    duration_ms REAL NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_access_log_created ON access_log(created_at);
CREATE INDEX IF NOT EXISTS idx_access_log_path ON access_log(path);
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


def mint_game_id() -> str:
    return uuid.uuid4().hex


def make_slug(title: str, game_id: str) -> str:
    """Derive a filesystem/URL-safe slug from a title + game_id.

    Uniqueness comes from the game_id suffix, not the title, so duplicate
    titles are fine. Falls back to "game" if the title has no alphanumeric
    characters at all.
    """
    base = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40].strip("-")
    if not base:
        base = "game"
    return f"{base}-{game_id[:8]}"


def register_web_game(game_id, slug, title, description, requested_by, status, attempts,
                       version=1, model=None, effort=None, duration_seconds=None,
                       error=None, parent_game_id=None, root_game_id=None, conn=None):
    """Insert or update the live registry row for a generated web game.

    One row per game_id: a re-registration (e.g. retry of the same job)
    UPSERTs in place, bumping updated_at while created_at is preserved from
    the original insert. root_game_id defaults to game_id itself (an
    original, not a fork) when not given.
    """
    c = _c(conn)
    now = _now()
    if root_game_id is None:
        root_game_id = game_id
    c.execute(
        """
        INSERT INTO web_games
            (game_id, slug, title, description, requested_by, status, attempts, version,
             model, effort, duration_seconds, error, parent_game_id, root_game_id,
             created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(game_id) DO UPDATE
            SET slug=excluded.slug, title=excluded.title, description=excluded.description,
                status=excluded.status, attempts=excluded.attempts,
                version=excluded.version, model=excluded.model,
                effort=excluded.effort, duration_seconds=excluded.duration_seconds,
                error=excluded.error, parent_game_id=excluded.parent_game_id,
                root_game_id=excluded.root_game_id, updated_at=excluded.updated_at
        """,
        (game_id, slug, title, description, requested_by, status, attempts, version,
         model, effort, duration_seconds, error, parent_game_id, root_game_id, now, now),
    )
    c.commit()


def get_web_game(game_id, conn=None):
    c = _c(conn)
    row = c.execute("SELECT * FROM web_games WHERE game_id=?", (game_id,)).fetchone()
    return dict(row) if row else None


def get_web_game_by_slug(slug, conn=None):
    c = _c(conn)
    row = c.execute("SELECT * FROM web_games WHERE slug=?", (slug,)).fetchone()
    return dict(row) if row else None


def get_web_games(sort="alpha", conn=None):
    c = _c(conn)
    if sort == "rating":
        order_by = "(thumbs_up - thumbs_down) DESC, thumbs_up DESC, title COLLATE NOCASE"
    else:
        order_by = "title COLLATE NOCASE"
    rows = c.execute(f"SELECT * FROM web_games ORDER BY {order_by}").fetchall()
    return [dict(r) for r in rows]
