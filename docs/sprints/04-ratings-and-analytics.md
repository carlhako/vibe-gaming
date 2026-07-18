# Sprint 4 — Ratings, Sort, Access Log, Admin Stats, Polish

See [00-overview.md](00-overview.md) for the full schema reference.
Purely additive on top of Sprints 1-3 — the `game_id`/`ratings`/
`access_log` tables already exist from Sprint 1's migration, `vg_uid`
cookie handling already exists from Sprint 2.

## Goals

1. Thumbs up/down rating per game, one vote per game per user (enforced by
   both cookie and IP), sidebar sortable alphabetically or by rating.
2. HTTP access logging + a password-protected admin stats page.
3. Test coverage pass + documentation refresh.

## Part A: ratings

- `db.record_rating(game_id, vote, client_uid, ip_address, conn=None) -> bool`:
  in one transaction, `INSERT INTO ratings (...)` then
  `UPDATE web_games SET thumbs_up = thumbs_up + (1 if vote=1 else 0),
  thumbs_down = thumbs_down + (1 if vote=-1 else 0) WHERE game_id=?`.
  Catch `sqlite3.IntegrityError` (either `UNIQUE` constraint), roll back,
  return `False`. Return `True` on success.
- `POST /api/games/<game_id>/rate` — body `{"vote": 1}` or `{"vote": -1}`
  (or two separate endpoints/buttons, implementer's choice). Ensures
  `vg_uid` cookie exists, calls `db.record_rating(game_id, vote,
  client_uid=vg_uid, ip_address=request.remote_addr)`. Returns
  `{"ok": true, "thumbs_up": N, "thumbs_down": N}` (200) on success, or
  `{"ok": false, "reason": "already_voted", "thumbs_up": N, "thumbs_down": N}`
  (409) if the constraint blocked it.
- Sidebar UI: thumbs-up/down buttons per game entry (in
  `templates/index.html` + `static/app.js`), showing current tallies,
  disabled/greyed after a successful vote (client-side, backed by the 409
  response as the real enforcement — don't trust client-side-only
  disabling).
- Note (document, don't implement): if this ever sits behind a reverse
  proxy, `werkzeug.middleware.proxy_fix.ProxyFix` must wrap the app or
  `request.remote_addr` will be the proxy's IP for every request, collapsing
  the IP-based constraint. Flag this in a code comment near
  `request.remote_addr` usage.

## Part B: sort

- `GET /api/games?sort=alpha|rating` (default `alpha`).
- `db.get_web_games(sort="alpha", conn=None)`:
  - `"alpha"` → `ORDER BY title COLLATE NOCASE`.
  - `"rating"` → `ORDER BY (thumbs_up - thumbs_down) DESC, thumbs_up DESC, title COLLATE NOCASE`.
- Sidebar gets a sort toggle (two buttons/tabs: "A-Z" / "Top rated").
  Client-side: either re-fetch `/api/games?sort=...` and re-render the
  list in JS, or have `GET /` accept a `?sort=` query param and
  server-render accordingly (pick whichever fits the existing
  `_build_manifest()`/`get_games()` caching pattern in `app.py` more
  cleanly — likely server-render since the site is currently
  server-rendered, not a JS SPA).
- All `web_games` rows are listed regardless of `parent_game_id` — no
  lineage grouping (explicitly out of scope, per the overview).

## Part C: access log + admin stats

- `@app.before_request` stashes `g._t0 = time.monotonic()`.
- `@app.after_request` (skip when `request.path.startswith("/static/")`):
  compute `duration_ms`, ensure `vg_uid` cookie value is readable (don't
  mint one here if request handling didn't already — just log `None` if
  absent), insert a row into `access_log` via a fresh `db.get_connection()`
  call. Must not raise on logging failure — wrap in try/except so a
  logging bug never breaks a real request; log the exception via Python's
  `logging` module instead.
- `ADMIN_TOKEN` — new required env var (document in `.env.example`).
  Decorator `require_admin_token` checks `request.args.get("token")` (or
  an `Authorization` header, implementer's choice) against
  `os.environ["ADMIN_TOKEN"]`; `abort(403)` on mismatch/missing.
- `GET /admin/stats` (behind `require_admin_token`) — renders
  `admin_stats.html` showing: total hit count, unique `client_uid` count,
  unique `ip_address` count, hits per day for the last 30 days (simple
  `GROUP BY date(created_at)` query), top 10 games by `/play/<slug>` hit
  count (parse slug out of `path`), top 10 games by
  `(thumbs_up - thumbs_down)`.

## Part D: tests + docs

- Add/update `tests/` (recreate a minimal suite — the original
  `tests/game_web/` was never ported, per `CLAUDE.md`):
  - `db.py`: schema creation, `register_web_game` upsert semantics,
    `claim_next_queued_request` race safety (spin up two threads racing to
    claim the same row, assert exactly one wins), `record_rating`
    uniqueness enforcement (cookie collision, IP collision, both blocked).
  - Migration script idempotency (run twice, assert no duplicate
    `game_id`s minted, no error).
  - Fork linkage (`enhance_game` sets `parent_game_id`/`root_game_id`
    correctly across a 2-3 generation chain) — can reuse/extend whatever
    test fixtures exist for `game_generator.py` today.
- Refresh `CLAUDE.md`: update "Current state — what's wired up vs. not" to
  reflect that generation/enhancement now have web UI entry points, the
  job runner exists, ratings/sort/access-log/admin-stats exist. Update the
  file map. Remove or rewrite the "NOT wired up yet" section since its
  premises are now stale.
- Add a top-level `README.md` if one doesn't exist, or fold a short
  "quickstart" into it, covering: `config.yaml`/`.env` setup including the
  new `ADMIN_TOKEN` and `job_runner` config block, `gunicorn -c
  gunicorn.conf.py app:app` for prod.

## Acceptance criteria

- Voting thumbs-up on a game increments `thumbs_up` and updates the
  visible tally; voting again (same browser) returns 409 and tally is
  unchanged; voting from a different browser but same IP also returns 409;
  voting from a genuinely different browser+IP succeeds.
- Sidebar sort toggle changes ordering correctly and is verifiable via
  `/api/games?sort=rating` returning games in descending net-rating order.
- Every non-static request produces one `access_log` row with a correct
  `duration_ms` and `status_code`; static asset requests produce none.
- `/admin/stats` returns 403 without a valid `ADMIN_TOKEN` and renders
  correctly with one.
- `pytest` passes, including the new race-safety and uniqueness tests.
- `CLAUDE.md` and `README.md` accurately describe the shipped system with
  no stale "not wired up" claims about features this sprint completed.
