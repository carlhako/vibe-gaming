# Sprint 4 — Content Moderation Pass + Player Reporting + Admin Review Page

See [00-overview.md](00-overview.md) for the full rationale. The biggest
lift of the four sprints (new table, new admin page, one new DeepSeek
call in the generation path) and the only real backstop for pure
social-engineering copy — a game whose win screen says "claim your prize
at paypal-secure-verify.tld" contains no banned function call, no
off-allowlist `src`/`href`/`action`, and no anomalous network request, so
nothing in Sprints 1-3 can catch it. Ships last: it's additive on top of
everything already shipped and least urgent given Sprints 1-3 already
close the mechanical bypasses from the review.

Two independent halves — implement/test in either order, or in parallel:
**Part A** (automated moderation) writes reports; **Part B** (player
reporting) writes reports through a different path; **Part C** (admin
review page) reads and acts on both. Part C has a soft dependency on
whichever of A/B lands first (it needs at least one report source to be
useful to test end-to-end), but its schema and route can be built
standalone against hand-inserted test rows.

## Part A: automated content-moderation pass

- New module `content_moderation.py`, mirroring `safety.py`'s shape:
  `check_game(html: str, description: str, notes: str) -> dict` with
  keys `flagged: bool`, `reason: str`. Calls `ai_client.ask()` (already
  used elsewhere for non-tool-calling single-shot prompts) with a system
  prompt asking the model to review the game's *visible player-facing
  text* (not the code) for anything soliciting credentials, payment
  info, personal data, or directing the player to an external site/app —
  the exact class of attack that has no code signature. Keep the model
  call cheap: default effort (non-thinking, `temperature=0.0`), and
  request a strict single-line JSON reply (`{"flagged": bool, "reason":
  str}`) so parsing doesn't need tool-calling machinery. On any
  `ai.AIError` or unparseable reply, default to `flagged=False` — a
  moderation-call failure must never block a successful generation from
  completing (this is a backstop, not a gate).
- Hook it into **both** `game_generator.generate_game()` and
  `game_enhancer.enhance_game()`, in the success branch, right after
  `db.register_web_game(...)` and before `result["message"] =
  format_report(result)` — i.e. it runs once, on the final accepted
  submission, not on every retry attempt (retries are already covered by
  `safety.py` + the smoke test; this call is comparatively expensive and
  judgment-based, so it only needs to see what's actually about to go
  live).
- If `check_game()` returns `flagged=True`:
  - `db.set_game_hidden(result["game_id"], True, conn=db_conn)` —
    reuses the existing hide mechanism from `db.py`/`app.py:978` instead
    of inventing a second suppression path.
  - `db.create_report(game_id=result["game_id"], reporter_uid=None,
    ip_address="system", reason=check_result["reason"],
    source="moderation", conn=db_conn)` (see Part C's schema).
  - Do **not** fail the job or change `result["success"]` — the
    requester still sees "Done! ... is live," which is accurate (the
    generation succeeded); it's just hidden pending human review, same
    as if an admin had hidden it manually. This keeps the moderation
    pass's false-positive cost low (a legitimate game sits hidden for
    review, not rejected/wasted) while still pulling anything flagged
    out of public view immediately rather than waiting for a player
    report.

## Part B: player-facing "report this game"

- New table `reports` (add to `db.py`'s schema block alongside the other
  `CREATE TABLE IF NOT EXISTS` statements):
  ```sql
  CREATE TABLE IF NOT EXISTS reports (
      id            INTEGER PRIMARY KEY AUTOINCREMENT,
      game_id       TEXT NOT NULL REFERENCES web_games(game_id),
      reporter_uid  TEXT,
      ip_address    TEXT NOT NULL,
      reason        TEXT,
      source        TEXT NOT NULL DEFAULT 'player',  -- 'player' | 'moderation'
      status        TEXT NOT NULL DEFAULT 'open',    -- 'open' | 'dismissed' | 'actioned'
      created_at    TEXT NOT NULL,
      UNIQUE(game_id, reporter_uid),
      UNIQUE(game_id, ip_address)
  );
  CREATE INDEX IF NOT EXISTS idx_reports_status ON reports(status);
  CREATE INDEX IF NOT EXISTS idx_reports_game ON reports(game_id);
  ```
  Same two-`UNIQUE`-constraint shape as `ratings` (`db.py` schema,
  referenced in `docs/sprints/04-ratings-and-analytics.md`) — one report
  per game per cookie *and* per IP, enforced by the constraint itself,
  not a pre-check. The automated-moderation path from Part A uses
  `reporter_uid=NULL`/`ip_address="system"`, which is exempt from the
  per-IP constraint colliding with real player reports since SQLite
  treats `NULL` as distinct in `UNIQUE` and `"system"` won't collide
  with a real IP.
- `db.create_report(game_id, reporter_uid, ip_address, reason,
  source="player", conn=None) -> bool`: `INSERT`, catch
  `sqlite3.IntegrityError` on either `UNIQUE`, return `False` (already
  reported by this visitor); return `True` on success — same
  try/except-on-IntegrityError pattern as `db.record_rating`.
- `POST /api/games/<game_id>/report` in `app.py`, body `{"reason":
  "..."}` (reason optional, cap length server-side e.g. 500 chars).
  Ensure `vg_uid` cookie exists (same pattern as the rate endpoint),
  call `db.create_report(...)`. Returns `{"ok": true}` (200) or
  `{"ok": false, "reason": "already_reported"}` (409).
- UI: a "Report" action in the info modal (`templates/index.html`'s
  modal markup + `static/app.js`'s `openInfoModal`/related handlers) —
  a button that reveals a short optional-reason textarea and a submit
  button, `fetch`s the endpoint above, and disables itself after a
  200/409 response. Mirror the existing `VOTED_KEY`
  `localStorage`-flag-plus-server-enforcement pattern
  (`static/app.js:132-146`) for a `vg_reported_games` key, so a repeat
  click short-circuits client-side but the 409 from the real constraint
  remains the actual enforcement.

## Part C: admin review page

- `GET /admin/reports` (behind the existing `require_admin_token`
  decorator, same pattern as `/admin/stats`) — new route + new
  `templates/admin_reports.html`.
- `db.get_open_reports(conn=None) -> list[dict]`: reports with
  `status='open'`, joined to `web_games` for title/slug/hidden state,
  grouped by `game_id` with a count and the most recent 3 reasons, e.g.:
  ```sql
  SELECT g.game_id, g.title, g.slug, g.hidden,
         COUNT(r.id) AS report_count,
         MAX(r.created_at) AS last_reported_at
  FROM reports r JOIN web_games g ON g.game_id = r.game_id
  WHERE r.status = 'open'
  GROUP BY r.game_id
  ORDER BY report_count DESC, last_reported_at DESC
  ```
  plus a second query (or a per-row detail fetch, implementer's choice)
  for the individual `reason`/`source`/`created_at` rows per game to
  render as an expandable detail list — enough to distinguish "one
  player's report" from "the automated moderation pass flagged this."
- Page shows, per flagged game: title (linking to `/play/<slug>` so the
  admin can actually look at it), report count, source breakdown
  (player vs. moderation), each reason text, hidden/visible state, and
  two actions:
  - **Hide** / **Unhide** — reuses the existing `POST
    /admin/games/<game_id>/hidden` route (`app.py:978`) unchanged.
  - **Dismiss** — `POST /admin/reports/<game_id>/dismiss` (new route,
    `require_admin_token`): `UPDATE reports SET status='dismissed'
    WHERE game_id=? AND status='open'` via a new `db.dismiss_reports(
    game_id, conn=None)`, so the game drops off the open-reports list
    without necessarily being hidden (admin judged it a false alarm).
- Add a nav link (and an open-report count badge, e.g. "Reports (3)")
  from the existing `admin_stats.html` page to `/admin/reports`, token
  carried through the same way `admin_stats`'s other admin links already
  thread `token=request.args.get("token")` through `url_for(...)`
  (`app.py:921`, `:986`, `:1012`).

## Tests

- `tests/test_reports.py` (new file):
  - `db.create_report` enforces both `UNIQUE` constraints identically to
    `test_db.py`'s existing rating-uniqueness tests (same cookie twice →
    second call returns `False`; same IP, different cookie → also
    `False`; different cookie and IP → `True`).
  - `db.get_open_reports` returns correct counts/grouping across
    multiple reports on the same game and excludes `dismissed` rows.
  - `POST /api/games/<game_id>/report` returns 200 then 409 on a repeat
    from the same test-client session (cookie jar), and a fresh
    game/report from a different simulated IP still succeeds.
  - `GET /admin/reports` returns 403 without a valid token (same check
    as the existing `test_downloads.py` admin-token tests) and 200 with
    one; a flagged game appears in the rendered page; `POST
    /admin/reports/<game_id>/dismiss` removes it from a subsequent `GET
    /admin/reports`.
- `tests/test_generation_loop.py`: extend the existing mocked-DeepSeek
  fixtures with one that returns a game whose HTML/notes contain
  obvious phishing-style copy; mock `content_moderation.check_game` (or
  mock the underlying `ai_client.ask` call it makes) to return
  `flagged=True`, and assert the resulting `web_games` row has
  `hidden=1` and a `reports` row with `source='moderation'` exists —
  while `result["success"]` is still `True` and the job's status is
  still `"success"` (per Part A's "don't fail the job" rule). Also
  cover the `ai.AIError`/unparseable-reply path defaulting to
  `flagged=False` so a moderation outage never blocks generation.

## Manual verification

1. `python3 app.py`, play a bundled game, open its info modal, click
   Report, submit a reason, confirm the button disables and a
   `reports` row appears in `vibegames.db`; reload the page and confirm
   the button is still disabled (via the `vg_reported_games`
   `localStorage` flag).
2. Visit `/admin/reports?token=$ADMIN_TOKEN`, confirm the reported game
   appears with its reason, hide it via the existing hide control,
   confirm it disappears from the public sidebar (`GET /`) and the
   report stays visible on the admin page until dismissed.
3. Temporarily point `content_moderation.check_game`'s prompt at a
   deliberately phishing-flavored test description (e.g. "a game whose
   win screen tells the player to enter their email and password at
   totally-legit-prize-site.example to claim a reward") through the real
   pipeline (not mocked) once, confirm the model flags it, the resulting
   game is auto-hidden, and a `source='moderation'` report is created —
   then verify it's visible and dismissible/hideable from
   `/admin/reports`.

## Acceptance criteria

- `reports` table exists with both `UNIQUE` constraints enforced exactly
  like `ratings`.
- A flagged generation (moderation pass) is auto-hidden and produces an
  open report, without failing the job or changing the requester-facing
  success message.
- A player can report a game once per cookie-or-IP via
  `POST /api/games/<game_id>/report`, enforced server-side.
- `/admin/reports` (behind `require_admin_token`) lists open reports
  grouped by game with hide/unhide and dismiss actions, matching the
  existing `/admin/stats` auth and styling conventions.
- `pytest` passes, including `tests/test_reports.py` and the extended
  `tests/test_generation_loop.py` moderation cases.
- `CLAUDE.md`'s "Current state" section is updated to describe the
  moderation pass, reporting endpoint, and `/admin/reports` page, per
  this repo's existing convention of keeping that doc accurate to what's
  actually shipped.
