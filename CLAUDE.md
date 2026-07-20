# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

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

## Commands

```bash
source venv/bin/activate
pytest                                              # full suite
pytest tests/test_db.py                             # one file
pytest tests/test_db.py::test_record_rating_blocks_duplicate_cookie  # one test
pytest -k fork_linkage                               # by keyword
python3 app.py                                       # run dev server on :8600
```

Tests mock the DeepSeek client and Playwright smoke test and use an
isolated temp SQLite DB (`tests/conftest.py`'s `isolated_db` fixture) —
no network calls or browser needed. There is no linter/formatter
configured in this repo.

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
- **Idea Forge**: `/games/new/idea-forge` and
  `/games/<game_id>/enhance/idea-forge` let a user flesh out a rough idea
  before submitting it for real generation/enhancement.
  `prompt_helper.expand_prompt()` makes one plain `ai_client.ask()` call
  (not the tool-calling submit loop below) that folds a genre checklist
  (`genre_checklists.yaml` — FPS, racing, platformer, etc.; hand-edited,
  no code change needed to add a genre) into the system prompt, so
  genre conventions the user didn't think to mention (FPS lighting/
  crosshair/viewmodel, racing camera/perspective) still make it into the
  brief. Runs through the same `generation_requests`/`job_runner` async
  queue as real generation — a third kind, `"prompt_help"` — and reuses
  the `/status/<job_id>` polling page rather than a new endpoint, so this
  stays consistent with the "no blocking request waits on DeepSeek" rule
  above. The model replies with JSON (`detected_genre`/`confidence`/
  `expanded_prompt`), parsed with a fallback to treating the whole reply
  as plain text if it isn't valid JSON; `detected_genre` is persisted on
  the `generation_requests` row (for future analytics, e.g. "which
  genres fail most often"), not just used in-flight. Configured
  independently via the `ideaforge:` block in `config.yaml`. The user
  reviews/edits the expanded brief on the status page, then a
  "Continue →" button forwards it into the real `/games/new` or
  `/games/<id>/enhance` submission.
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
- **Game downloads**: `GET /games/<game_id>/download` serves a single
  game's `index.html` as an attachment named
  `<slugified-title>-v<version>.html`. `GET /admin/games/download`
  (behind `require_admin_token`) zips every game directory's
  `index.html` + `meta.json` into one `vibegames-games-<date>.zip` for
  backup.
- **Audit trail**: every generation/enhancement attempt (not just the
  final outcome) is logged to `generation_attempts` — retries included —
  keyed on `generation_requests.job_id`.
- **Game info modal**: every game card has an ℹ️ button opening a modal
  with the generation prompt, model, effort, tokens used, creator, and
  fork lineage (the ancestor chain back to the root plus a flat list of
  sibling forks) — served by `GET /api/games/<game_id>/info` and rendered
  client-side (`static/app.js`). The prompt is read from `meta.json`
  (already written per-game on disk); model/effort/tokens/creator come
  from the `web_games` row.
- **Simple UID signup**: no passwords — `POST /signup` upgrades whatever
  `vg_uid` cookie a visitor already has (minting one if needed) into a
  durable `users` row. `/u/<uid>` is the resulting bookmarkable sign-in
  link: visiting it on any browser/device sets that `vg_uid` cookie,
  making the identity portable. `/account` lets a signed-up visitor set a
  unique username and see their sign-in link/token; `/signin` is the
  form for pasting that token back in on a new device. Every game/fork
  created through the web UI is tagged with `web_games.creator_uid`
  (the requester's `vg_uid`), shown as a "by &lt;creator&gt;" caption on
  every card and surfaced in the info modal. Ratings were already keyed
  by the full `vg_uid` before this existed, so signing up doesn't change
  vote-uniqueness — it just attaches a durable, cross-device identity to
  a cookie value that was already the enforcement key.
- **Profile page**: `/profile` is the signed-up user's own dashboard —
  every game they created (including ones they've hidden), a public/hide
  toggle per game (`POST /profile/games/<game_id>/hidden`, ownership
  checked against `creator_uid` — reuses the same `web_games.hidden`
  column the admin hide control writes), totals (game count, plays,
  thumbs up/down) from `db.get_user_stats()`, and their last 20 plays
  across those games from `db.get_user_play_history()`. Separate from
  `/account`, which stays focused on identity (username, sign-in link).
  The username link in the sidebar points here once a user is signed in.
  `/leaderboard` is the public, all-users view of the same thumbs-up
  totals (`db.get_user_leaderboard()`), unauthenticated.
- `safety.py` — regex blocklist + CDN allowlist, scans generated HTML
  before it's ever written to disk.
- `smoke_test.py` — headless Playwright load of generated HTML, fails the
  attempt on any uncaught JS exception or `console.error`.
- `ai_client.py` — DeepSeek client (Chat Completions API via the `openai`
  SDK pointed at DeepSeek's base URL).
- **Bundled games**: `games/block-dodge/` and `games/connect-4-4/` ship
  in git with a `game_id` committed in their `meta.json`.
  `db.sync_games_from_disk()` (called at app startup) backfills a
  `web_games` row for any such game that has none — `vibegames.db` is
  gitignored, so this is what gives the bundled games working rate/Enhance
  controls on a fresh clone. A game directory with no `game_id` still
  lists and plays off the disk scan, but can't be rated or enhanced.

## Not done / explicitly out of scope

- No deploy tooling (the original `deploy_game_web.py` did an SSH/systemd
  deploy specific to that project's VM). Run locally with `python3 app.py`
  or `gunicorn -c gunicorn.conf.py app:app`; deploying it somewhere is a
  separate decision.
- No lineage/tree grouping in the sidebar — an original and every fork of
  it are listed as independent flat entries; the "↳ enhanced from
  &lt;parent&gt;" caption plus the info modal's ancestor/sibling lists are
  the only lineage views (no full descendant tree).
- No passwords, email, or OAuth — accounts are just a `users` row keyed on
  the same `vg_uid` cookie value already used for ratings, plus a single
  shared `ADMIN_TOKEN` for `/admin/stats`. Losing the `/u/<uid>` link means
  losing the account; there's no recovery flow.

## How the generation pipeline works

`game_generator.generate_game(description, requested_by, config,
db_conn=None, games_dir=None, job_id=None)` and
`game_enhancer.enhance_game(source_game_id, description, requested_by,
config, db_conn=None, games_dir=None, job_id=None, new_title=None)` both
return a result dict with a `message` key (human-readable report) and
`success`/`url`/`error`/`game_id` etc. They share one retry loop —
`game_generator.run_generation_attempts()` — covering: build the prompts →
call `ai_client.ask_with_tools()` → validate the `submit_game` tool call's
arguments (`parse_submission`) → run `safety.scan()` on the HTML → mint a
`game_id`/slug and write `games/<slug>/{index.html,meta.json}` →
`smoke_test.run_smoke_test()` → on any failure, delete the half-written
directory and retry, up to `max_attempts` submissions. On success,
`db.register_web_game()` inserts the registry row (`enhance_game` sets
`parent_game_id`/`root_game_id`; `generate_game` leaves them as a fresh
original).

The loop is one multi-turn, function-calling conversation per job: the
model returns work by calling a `submit_game(title, description, html,
notes)` tool (`tool_choice` forced, so it can't reply with prose), and a
rejected submission gets the concrete failure back as that tool call's
result — the model then patches the code it already has in context
rather than regenerating from scratch. There is no free-text reply format
to parse anymore; `parse_submission()` just validates the tool-call JSON
arguments.

Neither function is called directly from a request handler — `app.py`'s
`/games/new` and `/games/<game_id>/enhance` POST routes just insert a
`generation_requests` row (`status='queued'`) and redirect to the status
page; `job_runner.py`'s poll loop is what actually calls them.

`prompt_helper.py`'s `expand_prompt()` (Idea Forge) is a lighter sibling
dispatched by the same `job_runner.py`, via a third `generation_requests`
kind, `"prompt_help"` — but it's a single plain `ai_client.ask()` call,
not the tool-calling submit/retry loop above, and never writes game files
itself. Its result (`result_text`/`detected_genre`) is written straight to
the `generation_requests` row rather than to `web_games`.

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

`ai_client.ask_with_tools(messages, tools=..., tool_choice=..., ...)` is
the multi-turn function-calling entry point the generation loop uses; the
caller owns the message list and appends tool results between calls. It
strips `reasoning_content` from returned messages (DeepSeek rejects
requests that echo it back). Verified live (2026-07-20): thinking mode
accepts `tools` but 400s on any *forcing* `tool_choice` (named function
or `"required"`), so `_resolve_tool_choice()` silently downgrades those
to `"auto"` when thinking is enabled — the generation loop always asks
for the forced choice and tolerates the occasional no-tool-call reply
with a nudge. Non-thinking mode honors the forced choice.

Observability: the OpenAI client is wrapped with LangSmith's
`wrap_openai`, and `run_generation_attempts()` is `@traceable`, so with
`LANGSMITH_TRACING=true` (+ `LANGSMITH_API_KEY`) each job becomes one
LangSmith trace with every retry's DeepSeek call nested under it. With
tracing unset both are pass-through no-ops.

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
`game_id`, so a game with no `game_id` in its `meta.json` can't be rated
or enhanced; give it one (any uuid4 hex) and restart to have the startup
disk-sync register it.

## File map

```
app.py                 Flask site: menu, /games/new, /games/<id>/enhance,
                        /games/new/idea-forge, /games/<id>/enhance/idea-forge,
                        /games/<id>/download, /status/<job_id>, /api/games
                        (sort), /api/games/<id>/info (prompt/model/tokens/
                        lineage), rate endpoint, /signup, /u/<uid> (sign-in
                        link), /signin, /account, /profile, /leaderboard,
                        access-log middleware, /admin/stats,
                        /admin/games/download
job_runner.py           DB-polling background worker: claims generation_requests,
                        dispatches to game_generator/game_enhancer/prompt_helper
game_generator.py       generate_game() + shared run_generation_attempts() retry loop
game_enhancer.py        enhance_game(): forks a new game_id/slug, links parent/root
prompt_helper.py        Idea Forge: expand_prompt(), one plain ai_client.ask()
                        call, genre-aware via genre_checklists.yaml
genre_checklists.yaml   genre -> checklist data for prompt_helper.py; hand-edited,
                        no code change needed to add/tune a genre
safety.py               regex blocklist + CDN allowlist for generated HTML
smoke_test.py           headless Playwright load, fails on JS errors
ai_client.py            DeepSeek Chat Completions client (swap point for other providers)
db.py                   SQLite: web_games, generation_requests, generation_attempts,
                        ratings, plays, access_log, users; sync_games_from_disk()
                        startup backfill
gunicorn.conf.py        post_fork hook starts job_runner workers per worker process
templates/index.html    menu shell: sidebar (sort toggle, rate/enhance controls) + iframe
templates/new_game.html  "Create New Game" prompt form
templates/enhance.html  enhancement prompt + optional new-title form
templates/idea_forge.html  rough-idea input for both create/enhance modes
templates/status.html   job status page (polls static/status.js), incl. the
                        expanded-brief review/"Continue" step for prompt_help jobs
templates/account.html  set username, show /u/<uid> sign-in link + token
templates/signin.html   paste-a-token form (alternative to the /u/<uid> link)
templates/profile.html  own games w/ hide toggle, play/like stats, recent plays
templates/leaderboard.html  public all-users ranking by total thumbs_up
templates/admin_stats.html  access-log/usage dashboard, behind ADMIN_TOKEN
static/style.css        arcade-cabinet styling
static/app.js           play-on-click, thumbs-vote, sort toggle behavior
static/status.js        polls /api/status/<job_id> until success/failed
games/block-dodge/      bundled game (game_id committed in meta.json)
games/connect-4-4/      bundled game (game_id committed in meta.json)
tests/                  pytest suite: db.py, startup disk-sync, fork linkage,
                        prompt_helper
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
