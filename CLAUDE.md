# CLAUDE.md

Guidance for Claude Code (or any AI assistant) picking up this repo cold.

## What this is

Vibegames is a self-hosted Flask arcade site that hosts AI-generated
single-file HTML5/JS browser games, generated on demand by **DeepSeek**
from a web form — no chat client or IRC bot required. It was forked out of
a larger home automation project (`home-net`'s `game_web/` module) and has
since grown its own web UI, background job runner, fork-on-enhance model,
ratings, and access-log/admin-stats page across four sprints (see
`docs/sprints/`).

Every game is one `index.html` — canvas or DOM, all HTML/CSS/JS inline,
optionally pulling a script/stylesheet from an allow-listed CDN. Games are
served inside a sandboxed `<iframe>` (`sandbox="allow-scripts allow-forms
allow-pointer-lock"`, no `allow-same-origin`), so a generated game gets an
opaque origin and cannot reach cookies, localStorage, the parent frame, or
other games. That sandbox is the primary security boundary; the safety
scanner and smoke test below are defense-in-depth on top of it, not the
main line of defense.

## Current state — what's wired up

Everything described below is implemented and tested (see `tests/`), not
aspirational:

- **Menu + play**: `app.py` scans `games/` (mtime-cache-invalidated) and
  serves a sidebar + sandboxed-iframe menu (`templates/index.html`),
  `/api/games`, `/play/<slug>`.
- **Generation, from the web**: `/games/new` (form) → `POST /games/new`
  queues a `generation_requests` row and redirects to `/status/<job_id>`,
  which polls `/api/status/<job_id>` (`static/status.js`) until the job
  hits `success`/`failed`. No blocking HTTP request ever waits on a
  DeepSeek call.
- **Background job runner** (`job_runner.py`): DB-polling worker
  threads — no in-memory queue, no Redis — so it stays correct under
  multiple gunicorn worker processes. Every job is claimed via an atomic
  conditional `UPDATE ... WHERE status='queued'`; a crash mid-job leaves
  the row `generating`, which the next `start_workers()` call sweeps to
  `failed`/`interrupted by restart`.
- **Fork-on-enhance**: `/games/<game_id>/enhance` never mutates the
  source game. `game_enhancer.enhance_game()` writes a brand-new
  `games/<slug>/` and `web_games` row, linked via `parent_game_id`
  (immediate source) and `root_game_id` (the original ancestor, stable
  across an arbitrarily long fork chain). Both the source and every fork
  stay visible in the sidebar independently.
- **GUID identity**: every game has a real `game_id` (uuid4 hex) primary
  key, so two games can share a title without colliding. `slug` (the
  filesystem/URL segment) is derived as `slugify(title)-<game_id prefix>`.
- **Ratings**: thumbs up/down per game, enforced to one vote per game per
  browser (`vg_uid` cookie) **and** per IP via two `UNIQUE` constraints on
  `ratings` — not a pre-check, the constraint itself is the enforcement.
  `POST /api/games/<game_id>/rate`.
- **Sort**: sidebar toggle between alphabetical and top-rated
  (`GET /` and `GET /api/games` both take `?sort=alpha|rating`).
- **Access log + admin stats**: every non-static request is logged to
  `access_log` (method/path/status/IP/user-agent/`vg_uid`/duration).
  `GET /admin/stats?token=...` (or `Authorization: Bearer ...`), gated by
  the `ADMIN_TOKEN` env var, shows hit counts, daily traffic, and top
  games by plays/rating.
- **Audit trail**: every generation/enhancement attempt (not just the
  final outcome) is logged to `generation_attempts` — retries included —
  keyed on `generation_requests.job_id`.
- `safety.py` — regex blocklist + CDN allowlist, scans generated HTML
  before it's ever written to disk.
- `smoke_test.py` — headless Playwright load of generated HTML, fails the
  attempt on any uncaught JS exception or `console.error`.
- `ai_client.py` — DeepSeek client (Chat Completions API via the `openai`
  SDK pointed at DeepSeek's base URL).
- `games/sample-game/` — a hand-written placeholder (Snake) proving the
  serving path works without spending an API call; note it predates the
  GUID schema and has no `game_id` in its `meta.json`, so it has no
  Enhance/rate controls in the sidebar (both require a `game_id`) — it
  still lists and plays fine.

## Not done / explicitly out of scope

- No deploy tooling (the original `deploy_game_web.py` did an SSH/systemd
  deploy specific to that project's VM). Run locally with `python3 app.py`
  or `gunicorn -c gunicorn.conf.py app:app`; deploying it somewhere is a
  separate decision.
- No lineage/tree grouping in the sidebar — an original and every fork of
  it are listed as independent flat entries (a small "↳ enhanced from
  &lt;parent&gt;" caption is the only lineage hint).
- No auth/accounts beyond the anonymous `vg_uid` cookie and the single
  shared `ADMIN_TOKEN` for `/admin/stats`.

## How the generation pipeline works

`game_generator.generate_game(description, requested_by, config,
db_conn=None, games_dir=None, job_id=None)` and
`game_enhancer.enhance_game(source_game_id, description, requested_by,
config, db_conn=None, games_dir=None, job_id=None, new_title=None)` both
return a result dict with a `message` key (human-readable report) and
`success`/`url`/`error`/`game_id` etc. They share one retry loop —
`game_generator.run_generation_attempts()` — covering: build a prompt →
call `ai_client.ask()` → parse the reply against a strict
`===GAME_FILE===` / `===META===` / `===NOTES===` marker format
(`parse_generation_response`) → run `safety.scan()` on the HTML → mint a
`game_id`/slug and write `games/<slug>/{index.html,meta.json}` →
`smoke_test.run_smoke_test()` → on any failure, delete the half-written
directory and retry with the concrete failure fed back into the next
prompt, up to `max_attempts`. On success, `db.register_web_game()` inserts
the registry row (`enhance_game` sets `parent_game_id`/`root_game_id`;
`generate_game` leaves them as a fresh original).

Neither function is called directly from a request handler — `app.py`'s
`/games/new` and `/games/<game_id>/enhance` POST routes just insert a
`generation_requests` row (`status='queued'`) and redirect to the status
page; `job_runner.py`'s poll loop is what actually calls them.

`config` is a plain dict matching `config.yaml.example` — pass
`yaml.safe_load(open("config.yaml"))` in. `newaiwebgame:` /
`enhanceaiwebgame:` control model/effort/attempts/timeouts;
`job_runner:` controls worker thread count and poll interval.

## ai_client.py — the DeepSeek swap

`ai_client.ask(prompt, system_prompt=None, model=None, effort=None,
temperature=None, timeout=120)` mirrors the `AskResult`/`AIError` shape of
the original Claude-CLI wrapper it replaced. As of 2026-07, DeepSeek's own
API exposes exactly two model families — `deepseek-v4-flash` (default) and
`deepseek-v4-pro` — each with a chain-of-thought "thinking" mode toggled
per-request rather than picked via model name, so `effort` no longer
selects the model (`model` does); instead `"high"`/`"max"` enable thinking
mode at that depth, anything else runs the fast non-thinking path with
temperature pinned to 0.0 (DeepSeek's documented recommendation for
code/math) unless overridden. The old `deepseek-chat`/`deepseek-reasoner`
names retire 2026-07-24 — don't reintroduce them. Requires
`DEEPSEEK_API_KEY` in the environment (`.env`, loaded via python-dotenv).

## Running locally

See `README.md` for the full quickstart (including `ADMIN_TOKEN` and the
`job_runner` config block). Short version:

```bash
cp config.yaml.example config.yaml
cp .env.example .env        # fill in DEEPSEEK_API_KEY and ADMIN_TOKEN
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
playwright install chromium   # required once, for smoke_test.py
python3 app.py                 # serves on :8600, starts job_runner workers too
```

`games/` is scanned on every request (mtime-cache-invalidated), so any game
directory dropped in with a valid `index.html` (+ optional `meta.json`)
shows up immediately — useful for testing without going through the
generation pipeline at all. Ratings, however, live in the DB keyed by
`game_id`, so a game with no `game_id` in its `meta.json` (like
`sample-game`) can't be rated or enhanced.

## File map

```
app.py                 Flask site: menu, /games/new, /games/<id>/enhance,
                        /status/<job_id>, /api/games (sort), rate endpoint,
                        access-log middleware, /admin/stats
job_runner.py           DB-polling background worker: claims generation_requests,
                        dispatches to game_generator/game_enhancer
game_generator.py       generate_game() + shared run_generation_attempts() retry loop
game_enhancer.py        enhance_game(): forks a new game_id/slug, links parent/root
safety.py               regex blocklist + CDN allowlist for generated HTML
smoke_test.py           headless Playwright load, fails on JS errors
ai_client.py            DeepSeek Chat Completions client (swap point for other providers)
db.py                   SQLite: web_games, generation_requests, generation_attempts,
                        ratings, access_log
migrate_to_guid_schema.py  one-time migration from the pre-GUID schema (idempotent)
gunicorn.conf.py        post_fork hook starts job_runner workers per worker process
templates/index.html    menu shell: sidebar (sort toggle, rate/enhance controls) + iframe
templates/new_game.html  "Create New Game" prompt form
templates/enhance.html  enhancement prompt + optional new-title form
templates/status.html   job status page (polls static/status.js)
templates/admin_stats.html  access-log/usage dashboard, behind ADMIN_TOKEN
static/style.css        arcade-cabinet styling
static/app.js           play-on-click, thumbs-vote, sort toggle behavior
static/status.js        polls /api/status/<job_id> until success/failed
games/sample-game/      hand-written placeholder game (no game_id; proves serving path)
tests/                  pytest suite: db.py, migration idempotency, fork linkage
config.yaml.example     copy to config.yaml
.env.example            copy to .env: DEEPSEEK_API_KEY, ADMIN_TOKEN
```

## Provenance

Ported from `home-net/game_web/` (a home automation bot's Homebot Arcade
module, which used the `claude` CLI for generation and IRC chat commands
to trigger it). See that project's `CLAUDE.md` / `ARCHITECTURE.md` if you
want the original design context — none of that project's MQTT/IRC/SSH
machinery is relevant here, only `game_web/` was the source. The 4-sprint
plan that took this from "serves pre-existing games only" to the system
described above lives in `docs/sprints/`.
