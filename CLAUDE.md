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

Every game is **served** as one `index.html` — canvas or DOM, all HTML/CSS/JS
inline, optionally pulling a script/stylesheet from an allow-listed CDN.
Games are served inside a sandboxed `<iframe>` (`sandbox="allow-scripts
allow-forms allow-pointer-lock"`, no `allow-same-origin`), so a generated
game gets an opaque origin and cannot reach cookies, localStorage, the
parent frame, or other games. That sandbox is the primary security
boundary; the safety scanner and smoke test below are defense-in-depth on
top of it, not the main line of defense.

Most games are authored single-file too (the model emits one complete
`index.html` directly). A game whose source is genuinely too large to ever
re-emit in a single model response instead lives split across multiple
files on disk (`game.md` + `src/index.html` + `src/style.css` + `src/*.js`)
and gets deterministically inlined back into one served `index.html` by a
no-AI build step — see "Multi-file games" below. Serving, the sandbox, and
`safety.py`/`smoke_test.py` never know the difference.

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
- **Content moderation + player reporting**: `content_moderation.py`'s
  `check_game()` asks DeepSeek to review a just-accepted game's visible
  player-facing text (not the code) for phishing/social-engineering copy —
  the one class of attack `safety.py`'s static blocklist and
  `smoke_test.py`'s runtime egress check can't catch, since it's plain
  text, not a code or network signature. Run once, in the success branch
  of both `game_generator.generate_game()` and
  `game_enhancer.enhance_game()` (via the shared
  `game_generator.run_moderation_pass()`), never on a retry attempt; any
  `AIError` or unparseable reply defaults to `flagged=False` so a
  moderation-call outage never blocks a successful generation. A flagged
  game is auto-hidden (`db.set_game_hidden`) and logged to a new
  `reports` table (`source='moderation'`) — the requester still sees a
  normal success message, since generation itself did succeed. Players
  can also report a game themselves: a "Report this game" control in the
  info modal (`static/app.js`) posts to
  `POST /api/games/<game_id>/report`, enforced to one report per game per
  `vg_uid` cookie *and* per IP by two `UNIQUE` constraints on `reports` —
  the same two-constraint pattern `ratings` already uses. `GET
  /admin/reports` (behind `require_admin_token`) lists every game with an
  open report, grouped with a count and each reason/source, and lets an
  admin Hide/Unhide (reusing the existing
  `POST /admin/games/<game_id>/hidden` route) or Dismiss
  (`POST /admin/reports/<game_id>/dismiss`) a game's reports; `admin_stats.html`
  links to it with an open-report count badge.
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
- **Multi-file games + the ReAct editing agent** (`docs/multifile-agent/`):
  a game whose source is too large to ever re-emit whole in one model
  response is authored split across `game.md` + `src/index.html` +
  `src/style.css` + `src/*.js`, and `builder.py` deterministically inlines
  that split source back into the one served `index.html` — no AI
  involved in the build step itself. Enhancing a multi-file game runs
  `agent.py`'s ReAct loop (`read_map`/`list_files`/`read_file`/
  `write_file`/`finish` tools) instead of the whole-file resubmit loop, so
  the model only ever reads/rewrites the modules a change touches. See
  "Multi-file games" below for the full picture, including the live agent
  transcript UI and the explode/dual-format policy that converts an
  existing single-file game into this format.

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

Both `ask()` and `ask_with_tools()` pin `max_tokens` to
`ai_client.MAX_OUTPUT_TOKENS` (150000 as of 2026-07-26 — see that
constant's comment in `ai_client.py`: the original 65536 figure was never
DeepSeek's real ceiling, just a self-confirming number nobody had tried
exceeding; a real probe this session got clean, non-`"length"`-truncated
generations up to 150000 output tokens, and DeepSeek's own docs claim
384K) and surface the response's `finish_reason`. In thinking mode this
budget is *shared* with `reasoning_content` tokens, so a large
enhancement's chain-of-thought can push the actual game source past the
cap: the reply is cut off mid-stream (`finish_reason == "length"`),
leaving the `submit_game` arguments an incomplete JSON fragment — the
"Unterminated string ... (char N)" failure. `run_generation_attempts()`
detects that case explicitly (rather than misreporting it as generic
malformed JSON, which the model misreads as an escaping bug), records the
attempt with outcome `truncated`, feeds back a size-specific notice, and
**drops thinking mode for the retry** to hand the reasoning budget back to
the game source. Truncated and parse-failure attempts keep their raw
tool-call arguments *unredacted* in `generation_attempts.raw_response`
(the game never reached disk, so that row is the only place the actual
bytes are inspectable) — successful attempts still strip them, since the
source is on disk.

## Multi-file games: format, the ReAct agent, the live chat UI, and explode

This is the "multi-file agent" initiative (`docs/multifile-agent/`,
6 sprints), built to get around a hard structural limit: one game
(Sorcerer With A Minigun) grew past what was believed to be DeepSeek's
~65,536-completion-token ceiling, past which the model could never
re-emit a whole `index.html` again in one response, by any trick. Sprint 6
found that ceiling was largely self-imposed (see `ai_client.py`'s
`MAX_OUTPUT_TOKENS` comment — the real figure is at least 150000, possibly
384K) and raised it, which lifts the single-file path's practical size
limit substantially without removing the motivation for this initiative:
even at a higher ceiling, a whole-file resubmit still costs a full-game
read+write on every enhancement, where a multi-file source only ever
touches the modules a change actually requires. The fix has two parts — a
format that never requires a whole-game read/write, and a live transcript
UI so an edit-by-edit agent run is still legible to the requester.

**Format.** A multi-file game's *source* is split on disk:
`games/<slug>/game.md` (a prose description plus a table of every `src/`
file and its purpose) and `games/<slug>/src/{index.html,style.css,*.js}`.
`builder.build_game(src_dir)` inlines every local `<link rel="stylesheet">`
and `<script src=...>` ref (in document order) into one HTML string —
external CDN refs (`safety.ALLOWED_CDN_HOSTS`) are left alone;
`builder.write_built_index()` writes that string to the game's real,
served `index.html`. `builder.is_multi_file(game_dir)` tells the two
formats apart (authoritative `meta.json["format"]`, falling back to
"does `src/index.html` exist" for fixtures/mid-run forks that have no
`meta.json` yet). `builder.build_and_verify(game_dir)` is the shared
build → `safety.scan()` → `smoke_test.run_smoke_test()` gate both formats
go through — a no-op passthrough (read the existing `index.html` straight
through) for single-file games.

**The ReAct agent** (`agent.py`) is what enhances a multi-file game.
Instead of resubmitting the complete file, the model drives a bounded
tool loop: `read_map()`, `list_files()`, `read_file(path)` to explore, then
`write_file(path, contents)` to replace one whole module (rejected over
`max_module_bytes` — split it instead of shrinking it), then
`finish(summary)` to trigger `builder.build_and_verify()`; a failure comes
back as `finish`'s tool result so the model keeps editing and calls
`finish` again, up to `max_verification_retries`. `agent.enhance_multifile_game()`
forks exactly like `game_enhancer.enhance_game()` (new `games/<slug>/`,
`parent_game_id`/`root_game_id`, source untouched, a failed run deletes
the half-written fork) — it's the multi-file-source counterpart
`job_runner.py` dispatches to instead of `game_enhancer.enhance_game()`.
Config lives under `multifile_agent:` in `config.yaml` (model/effort/
max_steps/max_verification_retries/max_module_bytes/
context_prune_after_steps).

Because `ai_client.ask_with_tools()` is stateless, `_run_react_loop`
resends its whole `messages` list on every turn — so anything left in
there forever gets rebilled every turn for the rest of the run. Sprint 5's
real pilot (`docs/multifile-agent/05-migration-and-pilot.md`) measured
this costing 5-12x more input tokens than the single-file baseline for
comparable changes; Sprint 6 traced it mostly to a write_file call's own
arguments (the complete new file contents, JSON-escaped) never being
pruned from the assistant message that made the call. `_compact_write_calls`
fixes that by **removing** each executed write_file call from the
conversation outright — the tool call *and* its paired tool-result message
— leaving only a short plain-text note on the assistant message carrying
the observation verbatim (success or rejection alike). The model already
generated that content and the note still reports the outcome; `read_file`
is there if it needs the current bytes again. A second, milder fix prunes a
`read_file` result once it's gone stale (outstanding more than
`context_prune_after_steps` turns without being rewritten), on top of the
pre-existing same-path-rewrite pruning.

**Never leave synthesized arguments in a tool call the model can see.** The
first version of the above kept each write_file call and merely replaced
its `contents` argument with a short placeholder. That placeholder was
110-118 bytes, and real pilot runs then had the model *copying it back* as
the contents of modules meant to be several KB — writing the bookkeeping
text to disk, reading it back, and re-copying it, in a fixed-point loop
that burned 1-2.6M input tokens and shipped nothing. (An identical earlier
variant that dropped the `path` key taught the model to omit `path`.) The
model reads anything in an arguments slot as a worked example of a valid
call, so rewriting arguments in place cannot be made safe — only removal
can. Every placeholder the agent does emit now carries `_PRUNE_SENTINEL`,
and `_write_file` rejects any write whose contents contain it, turning a
silent corruption into a self-correcting error. Full write-up:
`docs/multifile-agent/05-migration-and-pilot.md`, "Sprint 6 step 2".

Two related lessons from the same pilot, both about the agent reading its
own transcript literally. The compaction note's wording is load-bearing: an
earlier draft said the write calls "were dropped from the conversation" and
the model read that as *the writes didn't happen*, re-checking state until
it exhausted its turn budget — it now leads with the call having completed
and the file being on disk. And `_normalize_agent_path` collapses a leading
`src/` on any agent path, because paths are already rooted there and
`write_file("src/map.js")` otherwise nests to `src/src/map.js`; prompt
wording alone did not stop the model doing this, and it left two competing
`index.html` shells where `builder.build_game` reads only one.

Verified live: exploding the 159KB Darkhold Arena source now passes on the
first `finish` attempt in 9 turns / 405K input tokens, where the same run
before these fixes burned 40 turns and 2.5M input tokens without ever
reaching verification. The built artifact's JS is byte-identical to the
original ignoring whitespace (122,333 non-whitespace bytes, all 365
declarations intact).

That successful run split the game into a single 151KB `core.js` rather than
cohesive modules — passing every gate while buying nothing over staying
single-file. Cause: the same imitate-the-example mechanism as the stub bug.
The explode prompt described "cohesive modules" in prose while demonstrating
a four-file split with exactly one JS module named `core.js`, and the model
reproduced the demonstration. Explode now names several modules in its
example, states a size-derived target count (`source_bytes / 25,000`, min 3)
and why one big module defeats the purpose, and enforces its own tighter
`explode_max_module_bytes` ceiling (60,000, vs `max_module_bytes`'s 450,000)
via a cfg override, so `_run_react_loop` stays unaware of which pass it
drives. Not yet verified live.

**The agent event stream + live chat UI.** Every think/act/observe/verify
step is emitted through an `emit(role, content, data)` callback
(`agent.py`'s default writes a `db.add_agent_event` row keyed by
`job_id`). `GET /api/jobs/<job_id>/events?since=<seq>` returns everything
newer than `seq` — the incremental slice `static/agent_chat.js` polls
every second on the job status page. `templates/status.html` is a
two-pane `.job-shell`: the left pane is the original `status.js`-driven
queue/ETA/timer panel (unchanged), the right pane (`#chat-pane`) is
`agent_chat.js`'s independent, Claude-chat-style transcript — thoughts
collapse past 240 chars, tool calls/results get an icon and a short
summary (never the full file contents), a `build` event renders ✅/❌ with
the attempt number, and a terminal `final` event renders the notes plus a
Play link. Legacy single-file jobs have no agent events at all — the pane
just shows "No live transcript for this job" and the left pane alone
carries the whole experience, exactly as before this feature existed.

A `usage` event is also emitted once per LLM call, carrying that call's own
token counts plus the run's running totals. It's the only per-call
accounting there is: the `generation_requests` row only gets a total when
the whole job ends, and `generation_attempts` only gets a row per `finish()`
verification, so without it a 60-turn agent run's spend is invisible until
it's over. `agent_chat.js` has no renderer for the role and silently skips
it, so the job status page is unchanged.

**Exploding a game from the admin page.** `/admin/stats`' Games tab shows a
per-game **Fmt** badge (`S` single-file / `E` exploded) resolved through
`builder.is_multi_file()`, and an Explode button on every single-file game
with a `game_id`. It posts to `POST /admin/games/<game_id>/explode` (behind
`require_admin_token`), which queues a `kind='explode'` `generation_requests`
row and returns the `job_id` as JSON instead of redirecting; `job_runner.py`
dispatches that kind to `agent.explode_game()`. Going through the normal job
queue is the point — the AI kill switch, the worker's crash sweep, and the
History tab's model/effort/token/cost accounting all apply to it with no
special-casing. The button's dialog (`static/admin_explode.js`) then follows
the run on the same `/api/jobs/<job_id>/events` feed, rendering the
transcript plus a live token/cost readout off the `usage` events; the
endpoint also returns the job-level totals for the terminal summary. A 409
from an already-in-flight job comes back with that job's `job_id`, so the
dialog attaches to the running job rather than erroring. Unlike
`enhance_game_auto_format`'s internal explode, this fork is **not** hidden —
the admin asked for the format change itself, so the multi-file version is a
visible arcade entry (the same row's Hide toggle is right there). The
single-file original is untouched either way.

History rows for a job that has agent events get a **Transcript** button
that replays it into the same dialog — otherwise a finished agent run's
transcript is only reachable from `/status/<job_id>` while it's still live.
`db.get_generation_history()` returns a `has_agent_events` flag so the
button isn't offered on single-file jobs, which would open an empty dialog.

**Explode + the dual-format enhance policy** (`agent.explode_game()`,
`agent.enhance_game_auto_format()`) convert an *existing single-file* game
into this format. `explode_game()` hands the model the whole original
`index.html` as input context (input tokens aren't subject to the output
ceiling) and has it re-emit the same game split across `write_file` calls
— behavior-preserving by contract and gated by the same
`build_and_verify()`, but only a manual play-test actually confirms the
game still *plays* identically, not just "console-error-free." It forks
like any other enhance (`parent_game_id`/`root_game_id` back to the
single-file source) except the title defaults to the source's own title
verbatim, since this is a format change, not a content change.
`enhance_game_auto_format()` is `job_runner.py`'s single dispatch point
for every `kind='enhance'` job: a multi-file source goes straight to
`enhance_multifile_game()`; a single-file source at or over
`game_enhancer.LARGE_SOURCE_BYTES` gets auto-exploded first (the resulting
intermediate fork is hidden via `db.set_game_hidden` — it's an
implementation detail, not something the requester asked to see as its
own arcade entry, though its lineage still chains through it to the
original single-file source) and then enhanced on that fork for the
actual requested change; everything else keeps using the legacy
`game_enhancer.enhance_game()` path untouched. If the explode step itself
fails, the whole request falls back to the legacy single-file path rather
than failing over an internal step the user never directly asked for.
`builder.build_and_verify()` treats a not-yet-written `index.html` (either
format, mid-explode) as a normal failed-verification result, not a crash.

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
                        /games/<id>/download, /status/<job_id>, /api/games
                        (sort), /api/games/<id>/info (prompt/model/tokens/
                        lineage), /api/jobs/<job_id>/events (agent transcript),
                        rate endpoint, report endpoint, /signup,
                        /u/<uid> (sign-in link), /signin, /account, /profile,
                        /leaderboard, access-log middleware, /admin/stats,
                        /admin/games/download, /admin/games/<id>/explode,
                        /admin/reports
job_runner.py           DB-polling background worker: claims generation_requests,
                        dispatches to game_generator/agent.enhance_game_auto_format/
                        agent.explode_game (kind='explode')
game_generator.py       generate_game() + shared run_generation_attempts() retry loop,
                        run_moderation_pass() (shared with game_enhancer/agent)
game_enhancer.py        enhance_game(): forks a new game_id/slug, links parent/root
                        (legacy single-file whole-file resubmit path);
                        LARGE_SOURCE_BYTES (dual-format explode trigger)
builder.py              multi-file build-and-inline: build_game()/write_built_index(),
                        is_multi_file(), build_and_verify() (shared scan+smoke gate)
agent.py                ReAct editing agent for multi-file games:
                        enhance_multifile_game(), explode_game() (single-file ->
                        multi-file, behavior-preserving), enhance_game_auto_format()
                        (job_runner's dual-format dispatch point), agent_events emission
safety.py               regex blocklist + CDN allowlist for generated HTML
content_moderation.py   DeepSeek-judged check_game(): flags phishing/social-engineering
                        player-facing text safety.py/smoke_test.py can't catch
smoke_test.py           headless Playwright load, fails on JS errors
ai_client.py            DeepSeek Chat Completions client (swap point for other providers);
                        ask_with_tools() also drives agent.py's ReAct loop
db.py                   SQLite: web_games, generation_requests, generation_attempts,
                        agent_events, ratings, reports, plays, access_log, users;
                        sync_games_from_disk() startup backfill
gunicorn.conf.py        post_fork hook starts job_runner workers per worker process
templates/index.html    menu shell: sidebar (sort toggle, rate/enhance controls) + iframe,
                        info modal w/ Report control
templates/new_game.html  "Create New Game" prompt form
templates/enhance.html  enhancement prompt + optional new-title form
templates/status.html   two-pane job status page: status.js panel + live agent
                        chat transcript pane (agent_chat.js)
templates/account.html  set username, show /u/<uid> sign-in link + token
templates/signin.html   paste-a-token form (alternative to the /u/<uid> link)
templates/profile.html  own games w/ hide toggle, play/like stats, recent plays
templates/leaderboard.html  public all-users ranking by total thumbs_up
templates/admin_stats.html  access-log/usage dashboard, behind ADMIN_TOKEN;
                        Games tab has the Fmt (S/E) column + Explode button,
                        History tab the Transcript button
templates/admin_reports.html  open-reports review page, behind ADMIN_TOKEN
static/style.css        arcade-cabinet styling + two-pane job-shell/chat-log styling
static/app.js           play-on-click, thumbs-vote, report-this-game, sort toggle behavior
static/status.js        polls /api/status/<job_id> until success/failed
static/agent_chat.js    polls /api/jobs/<job_id>/events, renders the live agent transcript
static/admin_explode.js  admin Games-tab explode button + its live progress/token
                        dialog, and the History tab's transcript replay
games/block-dodge/      bundled game (game_id committed in meta.json)
games/connect-4-4/      bundled game (game_id committed in meta.json)
tests/                  pytest suite: db.py, startup disk-sync, fork linkage, reports,
                        builder, agent (ReAct loop), explode/dual-format, job UI,
                        admin explode control
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
