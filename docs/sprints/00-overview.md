# Vibegames Sprint Overview

## Why

Vibegames currently only *serves* pre-existing games — there's no web UI to
generate or enhance a game, no way to distinguish two games with the same
title, no audit trail of what was asked for, no usage/traffic visibility,
and no way for players to signal which games are good. This roadmap turns
it into a full self-hosted platform: submit a prompt, DeepSeek generates a
game in the background, a status page shows progress, the game appears in
the sidebar, players can enhance it (forking a new version rather than
overwriting the original), rate games thumbs up/down, sort the list, and
the owner can see access stats — all backed by git/GitHub instead of an
untracked local directory.

## Locked-in decisions

- **New GitHub repo** will be created for this project (not an existing
  one) — confirm name/visibility with the user when executing Sprint 1.
- **Background jobs** run on an in-process, SQLite-polling worker thread —
  no Redis/Celery. This stays correct even under multiple gunicorn workers
  because it polls the DB rather than relying on an in-memory queue that
  would only be visible to one worker process.
- **`/admin/stats`** (access log / usage view) is protected by a
  shared-secret token (`ADMIN_TOKEN` env var, checked via a decorator) —
  not public.
- **Enhancement forking**: the enhance form has an optional "new title"
  field. If left blank, the fork is auto-labeled `"<title> (v2)"`,
  `"(v3)"`, etc. — counting existing siblings under the same
  `root_game_id`.

## Sprint sequence and dependency rationale

1. **[Sprint 1](01-git-and-schema.md) — Git/GitHub + GUID/schema
   foundation.** Nothing else can safely proceed without version control,
   and every later feature (forking, job audit trail, ratings) needs the
   `game_id`/`parent_game_id`/`root_game_id` columns to exist first. Landing
   the *entire* schema (all 5 tables) in one migration event — even though
   3 of the tables stay empty until later sprints — avoids repeated
   ALTER TABLE churn and repeated migration-script edits.
2. **[Sprint 2](02-job-runner-and-create.md) — Job runner + Create New
   Game page.** The async job runner is the single piece of new
   infrastructure everything else depends on: Sprint 3's enhance flow reuses
   it verbatim (just a different `kind`).
3. **[Sprint 3](03-fork-on-enhance.md) — Fork-on-enhance + Enhance page.**
   Depends on Sprint 1's `parent_game_id`/`root_game_id` columns and
   Sprint 2's job/status machinery. Rewrites `game_enhancer.py` to stop
   mutating the source game in place.
4. **[Sprint 4](04-ratings-and-analytics.md) — Ratings, sort, access log,
   admin stats, polish.** Purely additive once `game_id` exists (Sprint 1)
   — safest to do last. Bundles test coverage and doc refresh.

## Shared schema reference

All 4 sprint docs reference these table definitions instead of repeating
them. They are all created in Sprint 1's single migration, then populated
incrementally by later sprints.

### `web_games` (replaces the current table in `db.py`)

```sql
CREATE TABLE web_games (
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
    parent_game_id   TEXT REFERENCES web_games(game_id),  -- NULL for originals
    root_game_id     TEXT REFERENCES web_games(game_id),  -- self for originals; ancestor root for forks
    thumbs_up        INTEGER NOT NULL DEFAULT 0,
    thumbs_down      INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);
CREATE INDEX idx_web_games_parent ON web_games(parent_game_id);
CREATE INDEX idx_web_games_root ON web_games(root_game_id);
```

`game_id` = uuid4 hex, the real primary key. `slug` = directory/URL
segment, derived as `slugify(title)[:40] + "-" + game_id[:8]` — guarantees
filesystem/URL uniqueness even under duplicate titles. `thumbs_up`/
`thumbs_down` are denormalized counters maintained transactionally on each
rating insert (Sprint 4), so sidebar sort-by-rating is a plain indexed
column read.

Each game's `meta.json` gains `game_id`, `parent_game_id`, `root_game_id`
so the filesystem stays self-describing if the DB is ever lost.

### `generation_requests` + `generation_attempts` (job/audit trail)

```sql
CREATE TABLE generation_requests (
    job_id           TEXT PRIMARY KEY,      -- status-URL key
    kind             TEXT NOT NULL,          -- 'create' | 'enhance'
    prompt           TEXT NOT NULL,
    source_game_id   TEXT REFERENCES web_games(game_id),  -- NULL for 'create'
    result_game_id   TEXT REFERENCES web_games(game_id),  -- NULL until success
    requested_by     TEXT NOT NULL,
    status           TEXT NOT NULL,          -- 'queued'|'generating'|'success'|'failed'
    attempts         INTEGER NOT NULL DEFAULT 0,
    model TEXT, effort TEXT, duration_seconds REAL, error TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX idx_genreq_status ON generation_requests(status);

CREATE TABLE generation_attempts (          -- per-retry audit detail
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id         TEXT NOT NULL REFERENCES generation_requests(job_id),
    attempt_number INTEGER NOT NULL,
    outcome        TEXT NOT NULL,   -- 'ai_error'|'safety_violation'|'smoke_test_failed'|'success'
    detail         TEXT,
    tokens_used    INTEGER,
    created_at     TEXT NOT NULL
);
```

Status keys on `job_id`, not `game_id` — for `kind='create'` the game
doesn't exist until the model's reply is parsed, but the redirect to the
status page must happen the instant the request is submitted.

### `ratings`

```sql
CREATE TABLE ratings (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id    TEXT NOT NULL REFERENCES web_games(game_id),
    vote       INTEGER NOT NULL CHECK (vote IN (-1, 1)),
    client_uid TEXT NOT NULL,
    ip_address TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(game_id, client_uid),
    UNIQUE(game_id, ip_address)
);
CREATE INDEX idx_ratings_game ON ratings(game_id);
```

Two independent `UNIQUE` constraints enforce "one vote per game per
cookie" **and** "one vote per game per IP" simultaneously.

### `access_log`

```sql
CREATE TABLE access_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    method TEXT NOT NULL, path TEXT NOT NULL, status_code INTEGER NOT NULL,
    ip_address TEXT NOT NULL, user_agent TEXT, client_uid TEXT,
    duration_ms REAL NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX idx_access_log_created ON access_log(created_at);
CREATE INDEX idx_access_log_path ON access_log(path);
```

## Cookie convention (introduced Sprint 2, reused Sprint 4)

A long-lived `vg_uid` cookie (uuid4, 1yr expiry) is set on first visit.
Web-submitted `requested_by` values are formatted `"web:" + vg_uid[:12]`.
The same cookie value is reused as `client_uid` for rating anti-abuse.

## Verification approach across all sprints

Each sprint doc has its own acceptance criteria, but the general pattern:
run `python3 app.py` locally, exercise the new route(s) in a browser or via
`curl`, inspect `vibegames.db` with the `sqlite3` CLI to confirm rows land
correctly, and run `pytest` for any new/updated tests before considering
the sprint done.
