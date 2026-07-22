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

import json
import re
import sqlite3
import datetime
import uuid
from pathlib import Path

DB_PATH = "vibegames.db"

# Enhance-form lock (see acquire_enhance_lock): how long one visitor can sit
# on the enhance form before it's up for grabs again, and how long the server
# tolerates a gap between heartbeat pings before treating the tab as gone.
# Not an absolute wall-clock deadline from when the form was opened — each
# successful heartbeat (see heartbeat_enhance_lock) slides expires_at forward
# by this much again, so an actively-open tab never gets cut off mid-typing;
# only a tab that stops pinging (closed, backgrounded past the idle timeout,
# or genuinely abandoned) lets the lock lapse.
ENHANCE_LOCK_TTL_SECONDS = 600
ENHANCE_LOCK_IDLE_TIMEOUT_SECONDS = 30

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
    tokens_used      INTEGER,
    error            TEXT,
    parent_game_id   TEXT REFERENCES web_games(game_id),
    root_game_id     TEXT REFERENCES web_games(game_id),
    thumbs_up        INTEGER NOT NULL DEFAULT 0,
    thumbs_down      INTEGER NOT NULL DEFAULT 0,
    hidden           INTEGER NOT NULL DEFAULT 0,
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
    tokens_used      INTEGER,
    error            TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_genreq_status ON generation_requests(status);

CREATE TABLE IF NOT EXISTS generation_attempts (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id           TEXT NOT NULL REFERENCES generation_requests(job_id),
    attempt_number   INTEGER NOT NULL,
    outcome          TEXT NOT NULL,
    detail           TEXT,
    tokens_used      INTEGER,
    duration_seconds REAL,
    raw_response     TEXT,
    created_at       TEXT NOT NULL
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

CREATE TABLE IF NOT EXISTS plays (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id    TEXT NOT NULL REFERENCES web_games(game_id),
    client_uid TEXT,
    ip_address TEXT,
    played_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_plays_game ON plays(game_id);
CREATE INDEX IF NOT EXISTS idx_plays_game_played_at ON plays(game_id, played_at DESC);

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

CREATE TABLE IF NOT EXISTS users (
    uid        TEXT PRIMARY KEY,
    username   TEXT UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS enhance_locks (
    game_id       TEXT PRIMARY KEY REFERENCES web_games(game_id),
    locked_by_uid TEXT NOT NULL,
    lock_token    TEXT NOT NULL,
    acquired_at   TEXT NOT NULL,
    last_ping_at  TEXT NOT NULL,
    expires_at    TEXT NOT NULL
);
"""


def _now():
    return datetime.datetime.now(datetime.UTC).isoformat().replace("+00:00", "Z")


def now_iso() -> str:
    """Public alias of the same UTC-ISO timestamp format every table uses,
    for callers outside this module (e.g. app.py's access_log middleware)."""
    return _now()


def _c(conn):
    return conn if conn is not None else get_connection()


# Columns added after a table's initial CREATE TABLE IF NOT EXISTS shipped —
# ALTER TABLE ADD COLUMN them in on an existing DB, since SQLite has no
# "ADD COLUMN IF NOT EXISTS". Safe to re-run: skipped once the column exists.
_ADDED_COLUMNS = {
    "web_games": [
        ("tokens_used", "INTEGER"), ("creator_uid", "TEXT"),
        ("hidden", "INTEGER NOT NULL DEFAULT 0"),
    ],
    "generation_requests": [
        ("tokens_used", "INTEGER"), ("creator_uid", "TEXT"),
    ],
    "generation_attempts": [("duration_seconds", "REAL"), ("raw_response", "TEXT")],
}


def _ensure_columns(conn):
    for table, columns in _ADDED_COLUMNS.items():
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        for name, coltype in columns:
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {coltype}")


_schema_ready_paths: set[str] = set()


def get_connection(check_same_thread=True):
    """Open a new connection. Schema creation/migration only needs to run
    once per DB file (it's on-disk state, not per-connection state) — every
    connection still gets WAL mode + a busy timeout so concurrent readers/
    writers (gunicorn workers, job_runner threads) block-and-retry instead
    of raising 'database is locked' under contention. Keyed by DB_PATH
    rather than a single flag so tests, which point DB_PATH at a fresh
    tmp file per test (see tests/conftest.py's isolated_db fixture), still
    get their schema created."""
    conn = sqlite3.connect(DB_PATH, check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    if DB_PATH not in _schema_ready_paths:
        conn.executescript(SCHEMA)
        _ensure_columns(conn)
        conn.commit()
        _schema_ready_paths.add(DB_PATH)
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
                       tokens_used=None, error=None, parent_game_id=None, root_game_id=None,
                       creator_uid=None, conn=None):
    """Insert or update the live registry row for a generated web game.

    One row per game_id: a re-registration (e.g. retry of the same job)
    UPSERTs in place, bumping updated_at while created_at is preserved from
    the original insert. root_game_id defaults to game_id itself (an
    original, not a fork) when not given. creator_uid is the web vg_uid
    cookie value of whoever requested this game, or None if unknown/not
    from the web UI.
    """
    c = _c(conn)
    now = _now()
    if root_game_id is None:
        root_game_id = game_id
    c.execute(
        """
        INSERT INTO web_games
            (game_id, slug, title, description, requested_by, status, attempts, version,
             model, effort, duration_seconds, tokens_used, error, parent_game_id, root_game_id,
             creator_uid, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(game_id) DO UPDATE
            SET slug=excluded.slug, title=excluded.title, description=excluded.description,
                status=excluded.status, attempts=excluded.attempts,
                version=excluded.version, model=excluded.model,
                effort=excluded.effort, duration_seconds=excluded.duration_seconds,
                tokens_used=excluded.tokens_used,
                error=excluded.error, parent_game_id=excluded.parent_game_id,
                root_game_id=excluded.root_game_id, creator_uid=excluded.creator_uid,
                updated_at=excluded.updated_at
        """,
        (game_id, slug, title, description, requested_by, status, attempts, version,
         model, effort, duration_seconds, tokens_used, error, parent_game_id, root_game_id,
         creator_uid, now, now),
    )
    c.commit()


def sync_games_from_disk(games_dir, conn=None) -> int:
    """Insert a web_games row for any games/<slug>/ directory whose
    meta.json carries a game_id that has no row yet.

    vibegames.db is gitignored, so on a fresh clone the bundled games
    (whose game_ids ARE committed, in their meta.json) have no rows —
    without one a game still lists and plays off the disk scan, but can't
    be rated or enhanced. Called at app startup; INSERT OR IGNORE keyed on
    game_id makes it a no-op on every start after the first and safe under
    multiple gunicorn workers. Never writes to disk: a directory with no
    game_id in its meta.json is left alone (add one — any uuid4 hex — to
    opt a hand-dropped game into ratings/enhancement).

    Returns the number of rows inserted."""
    games_dir = Path(games_dir)
    if not games_dir.exists():
        return 0
    c = _c(conn)
    now = _now()
    inserted = 0
    for entry in sorted(games_dir.iterdir()):
        if not entry.is_dir() or not (entry / "index.html").exists():
            continue
        meta_path = entry / "meta.json"
        if not meta_path.exists():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        game_id = meta.get("game_id")
        if not game_id:
            continue
        cur = c.execute(
            """
            INSERT OR IGNORE INTO web_games
                (game_id, slug, title, description, requested_by, status, attempts,
                 version, parent_game_id, root_game_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'success', 1, ?, ?, ?, ?, ?)
            """,
            (
                game_id, entry.name, meta.get("title", entry.name),
                meta.get("description", ""), meta.get("requested_by") or "system",
                meta.get("version", 1), meta.get("parent_game_id"),
                meta.get("root_game_id") or game_id,
                meta.get("created_at") or now, now,
            ),
        )
        inserted += cur.rowcount
    c.commit()
    return inserted


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


def record_play(game_id, client_uid=None, ip_address=None, conn=None):
    """Log one play of game_id — called every time /play/<slug> is served."""
    c = _c(conn)
    c.execute(
        "INSERT INTO plays (game_id, client_uid, ip_address, played_at) VALUES (?, ?, ?, ?)",
        (game_id, client_uid, ip_address, _now()),
    )
    c.commit()


def get_play_count(game_id, conn=None) -> int:
    c = _c(conn)
    row = c.execute("SELECT COUNT(*) AS n FROM plays WHERE game_id=?", (game_id,)).fetchone()
    return row["n"]


def get_play_counts(game_ids, conn=None) -> dict:
    """Play counts for many games at once, keyed by game_id. Games with no
    plays are simply absent from the returned dict (caller should default
    to 0)."""
    if not game_ids:
        return {}
    c = _c(conn)
    placeholders = ",".join("?" * len(game_ids))
    rows = c.execute(
        f"SELECT game_id, COUNT(*) AS n FROM plays WHERE game_id IN ({placeholders}) "
        f"GROUP BY game_id",
        list(game_ids),
    ).fetchall()
    return {r["game_id"]: r["n"] for r in rows}


def get_recent_plays(game_id, limit=20, conn=None):
    """Most recent play timestamps for game_id, newest first."""
    c = _c(conn)
    rows = c.execute(
        "SELECT played_at FROM plays WHERE game_id=? ORDER BY played_at DESC LIMIT ?",
        (game_id, limit),
    ).fetchall()
    return [r["played_at"] for r in rows]


def count_generation_requests(conn=None) -> int:
    c = _c(conn)
    row = c.execute("SELECT COUNT(*) AS n FROM generation_requests").fetchone()
    return row["n"]


def get_generation_history(limit=20, offset=0, conn=None):
    """One page of generation/enhancement jobs, newest first — every run,
    failures included. Joins in the resulting game's title/slug (NULL for
    failed or still-running jobs) and the requester's username if their
    vg_uid has a users row."""
    c = _c(conn)
    rows = c.execute(
        """
        SELECT gr.job_id, gr.kind, gr.prompt, gr.new_title, gr.status,
               gr.requested_by, gr.creator_uid, gr.created_at,
               gr.error, gr.model, gr.effort, gr.attempts,
               gr.tokens_used, gr.duration_seconds,
               gr.source_game_id,
               wg.title AS result_title, wg.slug AS result_slug,
               u.username AS creator_username,
               src.title AS source_title
        FROM generation_requests gr
        LEFT JOIN web_games wg ON wg.game_id = gr.result_game_id
        LEFT JOIN users u ON u.uid = gr.creator_uid
        LEFT JOIN web_games src ON src.game_id = gr.source_game_id
        ORDER BY gr.created_at DESC, gr.job_id
        LIMIT ? OFFSET ?
        """,
        (limit, offset),
    ).fetchall()
    return [dict(r) for r in rows]


def count_plays(conn=None) -> int:
    c = _c(conn)
    row = c.execute("SELECT COUNT(*) AS n FROM plays").fetchone()
    return row["n"]


def get_play_history(limit=20, offset=0, conn=None):
    """One page of plays across all games, newest first (by autoincrement
    id, which is insertion order). Joins in the game's title/slug and the
    player's username if their vg_uid has a users row."""
    c = _c(conn)
    rows = c.execute(
        """
        SELECT p.played_at, p.client_uid, p.ip_address,
               wg.title AS game_title, wg.slug AS game_slug,
               u.username
        FROM plays p
        LEFT JOIN web_games wg ON wg.game_id = p.game_id
        LEFT JOIN users u ON u.uid = p.client_uid
        ORDER BY p.id DESC
        LIMIT ? OFFSET ?
        """,
        (limit, offset),
    ).fetchall()
    return [dict(r) for r in rows]


def get_user_play_history(creator_uid, limit=20, conn=None):
    """Last `limit` plays across every game this uid created, newest first.
    Mirrors get_play_history() but scoped to one creator's games."""
    c = _c(conn)
    rows = c.execute(
        """
        SELECT p.played_at, p.client_uid, p.ip_address,
               wg.title AS game_title, wg.slug AS game_slug
        FROM plays p
        JOIN web_games wg ON wg.game_id = p.game_id
        WHERE wg.creator_uid = ?
        ORDER BY p.id DESC
        LIMIT ?
        """,
        (creator_uid, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def get_user_stats(creator_uid, conn=None):
    """Totals across every game this uid created: game count, plays,
    thumbs up/down. Hidden games count toward these totals same as
    get_user_leaderboard()'s total_likes does."""
    c = _c(conn)
    row = c.execute(
        """
        SELECT COUNT(DISTINCT wg.game_id) AS game_count,
               COALESCE(SUM(wg.thumbs_up), 0) AS total_likes,
               COALESCE(SUM(wg.thumbs_down), 0) AS total_dislikes,
               (SELECT COUNT(*) FROM plays p
                JOIN web_games wg2 ON wg2.game_id = p.game_id
                WHERE wg2.creator_uid = ?) AS total_plays
        FROM web_games wg
        WHERE wg.creator_uid = ?
        """,
        (creator_uid, creator_uid),
    ).fetchone()
    return dict(row)


def rename_game(game_id, title, conn=None) -> bool:
    """Returns False if game_id has no web_games row (nothing to update)."""
    c = _c(conn)
    cur = c.execute(
        "UPDATE web_games SET title=?, updated_at=? WHERE game_id=?",
        (title, _now(), game_id),
    )
    c.commit()
    return cur.rowcount > 0


def set_game_hidden(game_id, hidden: bool, conn=None) -> bool:
    """Returns False if game_id has no web_games row (nothing to update —
    e.g. a hand-dropped game with no game_id that was never registered)."""
    c = _c(conn)
    cur = c.execute(
        "UPDATE web_games SET hidden=?, updated_at=? WHERE game_id=?",
        (1 if hidden else 0, _now(), game_id),
    )
    c.commit()
    return cur.rowcount > 0


def count_by_root(root_game_id, conn=None) -> int:
    """Number of web_games rows sharing a root_game_id (the original plus
    every fork of it). Used to auto-number a blank-titled fork as
    "<source title> (v{n})" where n = this count + 1."""
    c = _c(conn)
    row = c.execute(
        "SELECT COUNT(*) AS n FROM web_games WHERE root_game_id=?", (root_game_id,)
    ).fetchone()
    return row["n"]


def get_user(uid, conn=None):
    c = _c(conn)
    row = c.execute("SELECT * FROM users WHERE uid=?", (uid,)).fetchone()
    return dict(row) if row else None


def get_all_users(conn=None):
    c = _c(conn)
    rows = c.execute("SELECT * FROM users ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]


def get_user_leaderboard(conn=None):
    """Every user, ranked by total thumbs_up across ALL their games —
    hidden games' likes still count toward the total; it's up to the
    caller to filter hidden games out of any *displayed* per-user game
    list. Users with zero games/likes are included, sorted last."""
    c = _c(conn)
    rows = c.execute(
        """
        SELECT u.uid, u.username,
               COALESCE(SUM(wg.thumbs_up), 0) AS total_likes,
               COUNT(wg.game_id) AS game_count
        FROM users u
        LEFT JOIN web_games wg ON wg.creator_uid = u.uid
        GROUP BY u.uid
        ORDER BY total_likes DESC, u.created_at
        """
    ).fetchall()
    return [dict(r) for r in rows]


def ensure_user(uid, conn=None):
    """Idempotent: insert a users row for uid if none exists yet (username
    left NULL). This is what 'signing up' does — it upgrades the vg_uid
    cookie the visitor already has into a durable row, it does not mint a
    new identity."""
    c = _c(conn)
    c.execute(
        "INSERT INTO users (uid, username, created_at) VALUES (?, NULL, ?) "
        "ON CONFLICT(uid) DO NOTHING",
        (uid, _now()),
    )
    c.commit()


def set_username(uid, username, conn=None) -> bool:
    """Returns False (no change made) if username is already taken by a
    different uid — caller should show a 'username taken' form error."""
    c = _c(conn)
    try:
        c.execute("UPDATE users SET username=? WHERE uid=?", (username or None, uid))
    except sqlite3.IntegrityError:
        c.rollback()
        return False
    c.commit()
    return True


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
                               new_title=None, creator_uid=None, conn=None):
    """Insert a new queued job. kind is 'create' or 'enhance'."""
    c = _c(conn)
    now = _now()
    c.execute(
        """
        INSERT INTO generation_requests
            (job_id, kind, prompt, new_title, source_game_id, result_game_id,
             requested_by, creator_uid, status, attempts, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, NULL, ?, ?, 'queued', 0, ?, ?)
        """,
        (job_id, kind, prompt, new_title, source_game_id, requested_by, creator_uid, now, now),
    )
    c.commit()


def get_generation_request(job_id, conn=None):
    c = _c(conn)
    row = c.execute(
        "SELECT * FROM generation_requests WHERE job_id=?", (job_id,)
    ).fetchone()
    return dict(row) if row else None


def update_generation_request(job_id, status=None, result_game_id=None, attempts=None,
                               model=None, effort=None, duration_seconds=None,
                               tokens_used=None, error=None, conn=None):
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
    if tokens_used is not None:
        fields["tokens_used"] = tokens_used
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
        "WHERE job_id=? AND status='queued' "
        "AND NOT EXISTS (SELECT 1 FROM generation_requests WHERE status='generating')",
        (now, job_id),
    )
    c.commit()
    return job_id if cur.rowcount == 1 else None


def get_average_duration(kind=None, limit=20, conn=None) -> float | None:
    """Average duration_seconds of the most recent `limit` successful jobs,
    optionally filtered by kind ('create'/'enhance'). Recency-limited so the
    estimate tracks current model/effort config rather than old runs. None
    if no completed jobs match yet."""
    c = _c(conn)
    query = (
        "SELECT duration_seconds FROM generation_requests "
        "WHERE status='success' AND duration_seconds IS NOT NULL"
    )
    params: list = []
    if kind is not None:
        query += " AND kind=?"
        params.append(kind)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    rows = c.execute(query, params).fetchall()
    if not rows:
        return None
    durations = [r["duration_seconds"] for r in rows]
    return sum(durations) / len(durations)


def get_queue_position(job_id, conn=None) -> int:
    """Count of queued jobs ahead of this one, ordered by created_at (the
    same order claim_next_queued_request claims in). Meaningful only while
    the job itself is still 'queued'; returns 0 if the job doesn't exist."""
    c = _c(conn)
    row = c.execute(
        "SELECT created_at FROM generation_requests WHERE job_id=?", (job_id,)
    ).fetchone()
    if row is None:
        return 0
    count_row = c.execute(
        "SELECT COUNT(*) AS n FROM generation_requests "
        "WHERE status='queued' AND created_at < ?",
        (row["created_at"],),
    ).fetchone()
    return count_row["n"]


def count_generating(conn=None) -> int:
    """0 or 1 in practice under the one-at-a-time claim guard, but written
    as a count for robustness."""
    c = _c(conn)
    row = c.execute(
        "SELECT COUNT(*) AS n FROM generation_requests WHERE status='generating'"
    ).fetchone()
    return row["n"]


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
                            tokens_used=None, duration_seconds=None, raw_response=None,
                            conn=None):
    """`raw_response`, when given, should already be a JSON-serialized string
    (see game_generator._redact_raw_response) — the caller strips the
    generated game source out of it first so this audit blob stays small."""
    c = _c(conn)
    c.execute(
        """
        INSERT INTO generation_attempts
            (job_id, attempt_number, outcome, detail, tokens_used, duration_seconds,
             raw_response, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (job_id, attempt_number, outcome, detail, tokens_used, duration_seconds,
         raw_response, _now()),
    )
    c.commit()


# ---------------------------------------------------------------------------
# enhance_locks (phase A: "form open" lock — see acquire_enhance_lock)
#
# Prevents two visitors from filling out the enhance form for the same game
# at once. Once a submission succeeds, this lock is released and a
# generation_requests row with status IN ('queued', 'generating') takes over
# as the "phase B" lock (see get_active_enhance_job) — no separate table
# needed for that half, since generation_requests already carries it.
# ---------------------------------------------------------------------------

def _iso_add_seconds(iso_ts: str, seconds: float) -> str:
    dt = datetime.datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
    return (dt + datetime.timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


def get_active_enhance_lock(game_id, conn=None):
    """Current enhance_locks row for game_id, or None.

    Lazily deletes the row first if it's gone stale (past its absolute TTL,
    or the holder's heartbeat has gone quiet past
    ENHANCE_LOCK_IDLE_TIMEOUT_SECONDS) — same on-read cleanup style as the
    games/ mtime cache and sweep_orphaned_requests, rather than a background
    sweep thread."""
    c = _c(conn)
    row = c.execute(
        "SELECT * FROM enhance_locks WHERE game_id=?", (game_id,)
    ).fetchone()
    if row is None:
        return None
    now = _now()
    if row["expires_at"] <= now or row["last_ping_at"] <= _iso_add_seconds(
        now, -ENHANCE_LOCK_IDLE_TIMEOUT_SECONDS
    ):
        c.execute("DELETE FROM enhance_locks WHERE game_id=? AND lock_token=?",
                   (game_id, row["lock_token"]))
        c.commit()
        return None
    return dict(row)


def acquire_enhance_lock(game_id, vg_uid, conn=None):
    """Try to take (or renew) the phase-A lock on game_id for vg_uid.

    Returns (acquired: bool, lock: dict) — lock is always the current row
    after the attempt, whether or not the caller is the one who now holds
    it, so a caller who lost the race can show "locked by someone else
    until <expires_at>". Same atomic UPDATE-then-INSERT-on-failure shape as
    claim_next_queued_request: the UPDATE's WHERE clause only matches a
    row that's unheld or gone stale, so at most one caller's UPDATE (or
    subsequent INSERT, guarded by the PRIMARY KEY) can win a given race.
    Re-acquiring your own still-active lock (e.g. revisiting the page)
    always succeeds and issues a fresh token + a full new 10 minutes."""
    c = _c(conn)
    now = _now()
    token = uuid.uuid4().hex
    expires_at = _iso_add_seconds(now, ENHANCE_LOCK_TTL_SECONDS)
    stale_cutoff = _iso_add_seconds(now, -ENHANCE_LOCK_IDLE_TIMEOUT_SECONDS)

    cur = c.execute(
        """
        UPDATE enhance_locks
        SET locked_by_uid=?, lock_token=?, acquired_at=?, last_ping_at=?, expires_at=?
        WHERE game_id=?
          AND (expires_at<=? OR last_ping_at<=? OR locked_by_uid=?)
        """,
        (vg_uid, token, now, now, expires_at, game_id, now, stale_cutoff, vg_uid),
    )
    if cur.rowcount == 0:
        try:
            c.execute(
                "INSERT INTO enhance_locks "
                "(game_id, locked_by_uid, lock_token, acquired_at, last_ping_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (game_id, vg_uid, token, now, now, expires_at),
            )
        except sqlite3.IntegrityError:
            c.rollback()
            row = c.execute(
                "SELECT * FROM enhance_locks WHERE game_id=?", (game_id,)
            ).fetchone()
            return False, dict(row)
    c.commit()
    row = c.execute("SELECT * FROM enhance_locks WHERE game_id=?", (game_id,)).fetchone()
    won = row["lock_token"] == token
    return won, dict(row)


def heartbeat_enhance_lock(game_id, lock_token, conn=None):
    """Bump last_ping_at to prove the holder's tab is still open, and slide
    expires_at forward another ENHANCE_LOCK_TTL_SECONDS from now — a tab
    that keeps actively pinging never gets cut off by the original
    absolute deadline just because filling out the form took a while.
    Returns the new expires_at on success, or None if lock_token no longer
    matches the current row (lock expired, was reclaimed, or was
    released) — the caller (a periodic ping, or an immediate check on
    tab-refocus) uses that to tell its user they've lost the lock."""
    c = _c(conn)
    now = _now()
    expires_at = _iso_add_seconds(now, ENHANCE_LOCK_TTL_SECONDS)
    cur = c.execute(
        "UPDATE enhance_locks SET last_ping_at=?, expires_at=? WHERE game_id=? AND lock_token=?",
        (now, expires_at, game_id, lock_token),
    )
    c.commit()
    return expires_at if cur.rowcount > 0 else None


def release_enhance_lock(game_id, lock_token, conn=None) -> bool:
    """Delete the lock if lock_token still matches — called on a clean tab
    close (sendBeacon) and right after a successful submission, once
    generation_requests takes over as the phase-B lock."""
    c = _c(conn)
    cur = c.execute(
        "DELETE FROM enhance_locks WHERE game_id=? AND lock_token=?",
        (game_id, lock_token),
    )
    c.commit()
    return cur.rowcount > 0


def get_active_enhance_job(source_game_id, conn=None):
    """The in-flight enhance job for source_game_id, if any (phase B lock —
    the game is being generated, regardless of whether the submitter's tab
    is still open). None if no enhance job is currently queued/generating
    for this game."""
    c = _c(conn)
    row = c.execute(
        "SELECT * FROM generation_requests "
        "WHERE kind='enhance' AND source_game_id=? AND status IN ('queued', 'generating') "
        "ORDER BY created_at DESC LIMIT 1",
        (source_game_id,),
    ).fetchone()
    return dict(row) if row else None
