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


def now_iso() -> str:
    """Public alias of the same UTC-ISO timestamp format every table uses,
    for callers outside this module (e.g. app.py's access_log middleware)."""
    return _now()


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


def record_rating(game_id, vote, client_uid, ip_address, conn=None) -> bool:
    """Record a thumbs up (vote=1) / down (vote=-1) for game_id. Returns
    True on success, False if this client_uid or ip_address already voted
    on this game — the two UNIQUE constraints on `ratings` are the actual
    enforcement (not a pre-check SELECT), so this is race-safe under
    concurrent requests for the same game/client."""
    c = _c(conn)
    try:
        c.execute(
            "INSERT INTO ratings (game_id, vote, client_uid, ip_address, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (game_id, vote, client_uid, ip_address, _now()),
        )
        c.execute(
            "UPDATE web_games SET "
            "thumbs_up = thumbs_up + (CASE WHEN ? = 1 THEN 1 ELSE 0 END), "
            "thumbs_down = thumbs_down + (CASE WHEN ? = -1 THEN 1 ELSE 0 END) "
            "WHERE game_id = ?",
            (vote, vote, game_id),
        )
    except sqlite3.IntegrityError:
        c.rollback()
        return False
    c.commit()
    return True


def count_by_root(root_game_id, conn=None) -> int:
    """Number of web_games rows sharing a root_game_id (the original plus
    every fork of it). Used to auto-number a blank-titled fork as
    "<source title> (v{n})" where n = this count + 1."""
    c = _c(conn)
    row = c.execute(
        "SELECT COUNT(*) AS n FROM web_games WHERE root_game_id=?", (root_game_id,)
    ).fetchone()
    return row["n"]


def get_web_games(sort="alpha", conn=None):
    c = _c(conn)
    if sort == "rating":
        order_by = "(thumbs_up - thumbs_down) DESC, thumbs_up DESC, title COLLATE NOCASE"
    else:
        order_by = "title COLLATE NOCASE"
    rows = c.execute(f"SELECT * FROM web_games ORDER BY {order_by}").fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# generation_requests / generation_attempts (async job + audit trail)
# ---------------------------------------------------------------------------

def create_generation_request(job_id, kind, prompt, requested_by, source_game_id=None,
                               new_title=None, conn=None):
    """Insert a new queued job. kind is 'create' or 'enhance'."""
    c = _c(conn)
    now = _now()
    c.execute(
        """
        INSERT INTO generation_requests
            (job_id, kind, prompt, new_title, source_game_id, result_game_id,
             requested_by, status, attempts, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, NULL, ?, 'queued', 0, ?, ?)
        """,
        (job_id, kind, prompt, new_title, source_game_id, requested_by, now, now),
    )
    c.commit()


def get_generation_request(job_id, conn=None):
    c = _c(conn)
    row = c.execute(
        "SELECT * FROM generation_requests WHERE job_id=?", (job_id,)
    ).fetchone()
    return dict(row) if row else None


def update_generation_request(job_id, status=None, result_game_id=None, attempts=None,
                               model=None, effort=None, duration_seconds=None, error=None,
                               conn=None):
    """Sparse update: only columns explicitly passed (non-None) are touched,
    except `error` which can be intentionally cleared by passing an empty
    string — pass None to leave it alone."""
    c = _c(conn)
    fields = {"updated_at": _now()}
    if status is not None:
        fields["status"] = status
    if result_game_id is not None:
        fields["result_game_id"] = result_game_id
    if attempts is not None:
        fields["attempts"] = attempts
    if model is not None:
        fields["model"] = model
    if effort is not None:
        fields["effort"] = effort
    if duration_seconds is not None:
        fields["duration_seconds"] = duration_seconds
    if error is not None:
        fields["error"] = error
    set_clause = ", ".join(f"{k}=?" for k in fields)
    c.execute(
        f"UPDATE generation_requests SET {set_clause} WHERE job_id=?",
        (*fields.values(), job_id),
    )
    c.commit()


def claim_next_queued_request(conn=None) -> str | None:
    """Atomically claim the oldest queued job, marking it 'generating'.
    Returns the claimed job_id, or None if no queued job is available.
    Safe under concurrent callers (multiple worker threads/processes)
    because the UPDATE's WHERE clause re-checks status='queued' and only
    one caller's UPDATE can affect the row."""
    c = _c(conn)
    now = _now()
    row = c.execute(
        "SELECT job_id FROM generation_requests WHERE status='queued' "
        "ORDER BY created_at LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    job_id = row["job_id"]
    cur = c.execute(
        "UPDATE generation_requests SET status='generating', updated_at=? "
        "WHERE job_id=? AND status='queued'",
        (now, job_id),
    )
    c.commit()
    return job_id if cur.rowcount == 1 else None


def sweep_orphaned_requests(conn=None) -> int:
    """Mark any job stuck in 'generating' (left over from a crash/restart)
    as failed. Returns the number of rows swept."""
    c = _c(conn)
    now = _now()
    cur = c.execute(
        "UPDATE generation_requests SET status='failed', "
        "error='interrupted by restart', updated_at=? WHERE status='generating'",
        (now,),
    )
    c.commit()
    return cur.rowcount


def add_generation_attempt(job_id, attempt_number, outcome, detail=None,
                            tokens_used=None, conn=None):
    c = _c(conn)
    c.execute(
        """
        INSERT INTO generation_attempts
            (job_id, attempt_number, outcome, detail, tokens_used, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (job_id, attempt_number, outcome, detail, tokens_used, _now()),
    )
    c.commit()
