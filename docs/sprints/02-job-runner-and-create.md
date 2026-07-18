# Sprint 2 — Job Runner + Create New Game Page

See [00-overview.md](00-overview.md) for the full schema reference.
Depends on [Sprint 1](01-git-and-schema.md)'s `game_id` schema and
`generation_requests`/`generation_attempts` tables already existing.

## Goals

1. A durable, multi-process-safe background job runner that survives
   restarts and works correctly under both `python3 app.py` and gunicorn.
2. A "Create New Game" page: submit a prompt, get redirected to a status
   page keyed by `job_id`, watch it go `queued` → `generating` →
   `success`/`failed`.

## Part A: `job_runner.py` (new file)

```python
def start_workers(config: dict, games_dir: Path, num_workers: int = 1) -> None: ...
def _worker_loop(config: dict, games_dir: Path) -> None: ...
```

- `_worker_loop` opens its own `db.get_connection()` (never share a
  connection across threads — matches the existing `check_same_thread=True`
  idiom in `db.py`) and loops: claim a queued job, dispatch to
  `game_generator.generate_game()` or `game_enhancer.enhance_game()` based
  on `kind`, update `generation_requests` with the outcome, sleep
  `poll_interval_seconds` (default 1) between polls when idle.
- Claim logic in `db.py`: `claim_next_queued_request(conn=None) -> str | None`
  — `UPDATE generation_requests SET status='generating', updated_at=?
  WHERE job_id = (SELECT job_id FROM generation_requests WHERE
  status='queued' ORDER BY created_at LIMIT 1) AND status='queued'`,
  return the claimed `job_id` only if `cursor.rowcount == 1`, else retry
  the SELECT once more or return `None`. (SQLite doesn't support
  `UPDATE ... RETURNING` uniformly across the versions this project
  targets — do the SELECT-then-conditional-UPDATE-with-rowcount-check
  pattern, or use `RETURNING` if the bundled sqlite3 version supports it;
  check `sqlite3.sqlite_version` before relying on it.)
- `start_workers()` first sweeps orphaned rows: any `status='generating'`
  row left over from a previous crash/restart gets set to
  `status='failed', error='interrupted by restart'`. Then spawns
  `num_workers` daemon threads running `_worker_loop`.
- Add `job_runner: { workers: 1, poll_interval_seconds: 1 }` to
  `config.yaml.example`.

## Part B: startup wiring

- **Dev** (`app.py` `__main__` block): call
  `job_runner.start_workers(config, games_dir)` once, before `app.run(...)`.
  Add `use_reloader=False` explicitly to `app.run(...)` so Flask's debug
  reloader fork can't double-start workers.
- **Prod**: new `gunicorn.conf.py` at repo root with a `post_fork(server,
  worker)` hook calling `job_runner.start_workers(config, games_dir)` — one
  poll loop per gunicorn worker process, which is correct under the
  DB-polling design regardless of `--workers N`.
- Both paths load `config.yaml` the same way `app.py`'s `__main__` already
  does today.

## Part C: `generate_game()` audit logging

- Add optional `job_id: str | None = None` kwarg to
  `game_generator.generate_game()`.
- Inside the existing retry loop, after each attempt (success or failure),
  insert a row into `generation_attempts` (`job_id`, `attempt_number`,
  `outcome` — one of `ai_error`/`safety_violation`/`smoke_test_failed`/
  `success`, `detail`, `tokens_used`) when `job_id` is not `None`. Leave
  behavior unchanged when `job_id is None` (direct/test callers unaffected).

## Part D: routes + UI

- `GET /games/new` — renders `new_game.html` with a prompt textarea and
  submit button.
- `POST /games/new` — reads the prompt, ensures the `vg_uid` cookie exists
  (mint one if missing, uuid4, `max_age=31536000`, `httponly=False` so JS
  isn't blocked from reading it if needed, `samesite="Lax"`), builds
  `requested_by = "web:" + vg_uid[:12]`, mints a `job_id`
  (`uuid.uuid4().hex`), inserts a `generation_requests` row with
  `kind='create'`, `status='queued'`, redirects to `/status/<job_id>`.
- `GET /status/<job_id>` — renders `status.html` with the `job_id` baked in
  (the page itself polls via JS, doesn't need server-side status at render
  time).
- `GET /api/status/<job_id>` — returns JSON:
  `{status, kind, prompt, result_slug, result_title, error}` (look up
  `result_game_id` → `web_games` row for `result_slug`/`result_title` once
  `status='success'`). 404 if `job_id` unknown.
- `static/status.js` — polls `/api/status/<job_id>` every 2s; on
  `success`, shows a "Play now" link to `/play/<result_slug>` and stops
  polling; on `failed`, shows the error and stops polling.
- Add a "+ New Game" link/button to `templates/index.html`'s sidebar
  pointing at `/games/new`.

## Acceptance criteria

- Submitting a prompt via `/games/new` immediately redirects to
  `/status/<job_id>` (no blocking wait on the request thread).
- The status page shows `queued`, then (within ~poll_interval seconds)
  `generating`, then `success` with a working play link, or `failed` with
  the error surfaced.
- Killing and restarting the app mid-generation leaves the job as
  `failed`/`interrupted by restart` on next startup rather than stuck in
  `generating` forever.
- `generation_attempts` rows accumulate per retry attempt for a job that
  needed more than one try (simulate by temporarily lowering
  `max_attempts` or forcing a safety-scan failure in a test).
- Running under `gunicorn --workers 2 -c gunicorn.conf.py app:app` still
  processes queued jobs correctly with no duplicate claims (verify by
  checking `generation_attempts`/`generation_requests` for a job never
  gets claimed twice — no two `generating` transitions for the same
  `job_id`).
