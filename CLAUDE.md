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

Everything runs inside the venv (`source venv/bin/activate`); `pytest` and
`python3 app.py` (dev server on :8600) need no extra flags.

Tests mock the DeepSeek client and Playwright smoke test and use an
isolated temp SQLite DB (`tests/conftest.py`'s `isolated_db` fixture) —
no network calls or browser needed. There is no linter/formatter
configured in this repo.

## Current state — what's wired up

Everything described below is implemented and tested (see `tests/`), not
aspirational. The routes, tables and templates are all readable from
`app.py`/`db.py`; what follows is only the part the code doesn't say out loud.

- **No blocking HTTP request ever waits on a DeepSeek call.** `/games/new`
  and `/games/<game_id>/enhance` only insert a `generation_requests` row and
  redirect to `/status/<job_id>`; `job_runner.py`'s poll loop is what calls
  the generator. Neither `generate_game()` nor `enhance_game()` is ever
  called from a request handler.
- **The job runner polls the DB — no in-memory queue, no Redis** — so it
  stays correct under multiple gunicorn worker processes. Every job is
  claimed via an atomic conditional `UPDATE ... WHERE status='queued'`, and
  a crash mid-job leaves the row `generating`, which the next
  `start_workers()` call sweeps to `failed`/`interrupted by restart`.
  `db.claim_next_queued_request` allows exactly one `generating` job
  site-wide, so a paused or long-running job blocks the whole queue.
- **Enhancing never mutates the source game.** It forks: a brand-new
  `games/<slug>/` and `web_games` row, linked by `parent_game_id`
  (immediate source) and `root_game_id` (the original ancestor, stable
  across an arbitrarily long fork chain). Source and every fork stay
  listed independently. A failed run deletes its half-written fork.
- **Identity is a `game_id` (uuid4 hex), not the title or the slug**, so two
  games can share a title; `slug` is derived as
  `slugify(title)-<game_id prefix>`. A game directory with no `game_id`
  still lists and plays off the disk scan but **cannot be rated or
  enhanced** — those live in the DB keyed by `game_id`.
- **Vote and report uniqueness is enforced by `UNIQUE` constraints, not by a
  pre-check** — two of them per table (per `vg_uid` cookie *and* per IP) on
  both `ratings` and `reports`. The constraint itself is the enforcement.
- **Accounts are just a `users` row keyed on the same `vg_uid` cookie value
  ratings already used**, so signing up doesn't change vote-uniqueness — it
  attaches a durable, cross-device identity to a value that was already the
  enforcement key. `/u/<uid>` is the portable sign-in link; losing it means
  losing the account, as there is no recovery flow.
- **`content_moderation.check_game()` fails open by design.** It asks
  DeepSeek to review a just-accepted game's visible player-facing *text*
  (not the code) for phishing/social-engineering copy — the one attack class
  `safety.py`'s static blocklist and `smoke_test.py`'s runtime egress check
  cannot catch, being plain text rather than a code or network signature.
  (`smoke_test.py` serves the game from a throwaway `127.0.0.1` origin under
  the real `safety.game_csp()` rather than opening it as `file://` — ES modules
  can't load over `file://` at all, and serving it means a game that the CSP
  would break now fails during generation instead of in the arcade. The CSP
  usually catches egress before the request watcher does; both reject it.) It
  runs once, in the success branch only, never on a retry; any `AIError` or
  unparseable reply defaults to `flagged=False`, so a moderation outage
  never blocks a successful generation. A flagged game is auto-hidden and
  logged to `reports` (`source='moderation'`), but the requester still sees
  a normal success message — generation itself did succeed.
- **`generation_attempts` logs every attempt, not just the final outcome** —
  retries included — keyed on `generation_requests.job_id`.
- **`db.sync_games_from_disk()` at startup is what makes the bundled games
  (`games/block-dodge/`, `games/connect-4-4/`) ratable on a fresh clone.**
  Their `game_id` is committed in `meta.json` but `vibegames.db` is
  gitignored, so the startup backfill is the only thing that gives them
  working rate/Enhance controls.
- **Multi-file games** are enhanced by `agent.py`'s ReAct loop instead of
  the whole-file resubmit path — see the section below.
- **3D games run on a self-hosted three.js** picked once on the new-game
  form and never changed afterwards — see "3D games" below.

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
arguments (`parse_submission`) → `engines.normalize()` the HTML → run
`safety.scan()` on the normalized bytes → mint a `game_id`/slug and write
`games/<slug>/{index.html,meta.json}` → `smoke_test.run_smoke_test()` → on any
failure, delete the half-written directory and retry, up to `max_attempts`
submissions. Normalize runs *before* scan so the scan sees exactly the bytes
that reach disk. On success,
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

## ai_client.py — the AI provider

`ai_client.ask(prompt, system_prompt=None, model=None, effort=None,
temperature=None, timeout=120)` mirrors the `AskResult`/`AIError` shape of
the original Claude-CLI wrapper it replaced. As of 2026-07, DeepSeek's own
API exposes two model families — `deepseek-v4-flash` and `deepseek-v4-pro`
(the latter is now the platform default, replacing v4-flash) — each with
a chain-of-thought "thinking" mode toggled per-request rather than picked
via model name, so `effort` no longer selects the model (`model` does);
instead `"high"`/`"max"` enable thinking mode at that depth, anything else
runs the fast non-thinking path with temperature pinned to 0.0 (DeepSeek's
documented recommendation for code/math) unless overridden. The old
`deepseek-chat`/`deepseek-reasoner` names retire 2026-07-24 — don't
reintroduce them. The platform also supports a second provider,
MiniMax (`MINIMAX_API_KEY` + base URL `https://api.minimax.io/v1`, model
id `MiniMax-M3`), selectable at runtime via the admin/stats provider
toggle (db.get_ai_provider). When the toggle is `deepseek`,
`ai_client.MODEL_DEFAULT` resolves to `deepseek-v4-pro`; when `minimax`,
it resolves to `MiniMax-M3`. **Per-pipeline `effort: "high"` is on for
every pipeline EXCEPT `enhanceaiwebgame`, which is `effort: "low"`**
(the "always thinking" posture, with enhance as the deliberate
exception, 2026-08-05). The `effort` semantic is identical on both
providers; only the API wire string for "thinking on" differs:
DeepSeek emits `thinking.type=enabled` (caller picks reasoning depth
via `reasoning_effort`); M3 emits `thinking.type=adaptive` (server
picks depth per-call, no caller knob). M3 400s on `enabled` with
`invalid params, invalid thinking.type: "enabled" (allowed: adaptive,
disabled) (2013)`. The mapping is in `_resolve_thinking` /
`_thinking_type_on`. Missing-key handling in `_client()` fails loudly
with the relevant env-var name in the message before opening any
connection.

**Why `enhanceaiwebgame` is the exception.** DeepSeek respects the
caller's `reasoning_effort`, so thinking on costs what you ask for.
M3's `adaptive` mode is server-controlled, and on an enhance system
prompt that includes the full source HTML (16-150 KB depending on the
game) it picks very deep reasoning: measured locally (2026-08-05),
a complex enhance of a 16 KB Pong clone took **876s with
`effort: "high"`** vs **195s on a 61 KB Hex & Hollow with
`effort: "low"`** — ~4.5× slowdown. With `max_attempts: 3` and the
1800s per-call timeout, an M3 + thinking-on enhance that doesn't
converge can burn 90 minutes before giving up. Create
(`newaiwebgame`) keeps thinking on because its system prompt is
~500 tokens and M3 reasoning there is fast.

`ai_client.ask_with_tools(messages, tools=..., tool_choice=..., ...)` is
the multi-turn function-calling entry point the generation loop uses; the
caller owns the message list and appends tool results between calls. It
strips `reasoning_content` from returned messages (DeepSeek rejects
requests that echo it back). Verified live (2026-07-20): thinking mode
accepts `tools` but 400s on any *forcing* `tool_choice` (named function
or `"required"`), so `_resolve_tool_choice()` silently downgrades those
to `"auto"` when thinking is on — the generation loop always asks
for the forced choice and tolerates the occasional no-tool-call reply
with a nudge. Non-thinking mode honors the forced choice. The
`_resolve_tool_choice` "thinking on" detection is provider-aware
(`enabled` for deepseek, `adaptive` for minimax) so the same downgrade
fires under either provider's wire schema.

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

## Multi-file games and the ReAct editing agent

A game whose source is too large to re-emit whole in one model response is
authored split on disk: `games/<slug>/game.md` (prose + a table of every
`src/` file and its purpose) plus `games/<slug>/src/{index.html,style.css,*.js}`.
`builder.build_game()` deterministically inlines every local
`<link rel="stylesheet">`/`<script src=...>` ref in document order into the
one served `index.html` — no AI in the build step; allow-listed CDN refs are
left alone. `builder.is_multi_file()` tells the formats apart (authoritative
`meta.json["format"]`, falling back to "does `src/index.html` exist").
`builder.build_and_verify()` is the shared build → `safety.scan()` →
`smoke_test.run_smoke_test()` gate both formats go through — a no-op
passthrough for single-file games. Serving, the sandbox, `safety.py` and
`smoke_test.py` never know the difference.

`agent.py` enhances a multi-file game with a bounded ReAct tool loop
(`read_map`/`list_files`/`read_file(path, start_line, end_line, char_start)`/
`search(pattern, path=None)`/`edit_file(path, old_string, new_string)`/
`write_file(path, contents)`/`finish(summary)`), so the model only reads and
rewrites the modules a change touches. `enhance_multifile_game()` forks
exactly like `game_enhancer.enhance_game()`; `explode_game()` converts an
existing single-file game to this format; `enhance_game_auto_format()` is
`job_runner.py`'s single dispatch point for every `kind='enhance'` job.
Config lives under `multifile_agent:` in `config.yaml`.
`agent.DEFAULT_AGENT_MODEL` is a **code** default, not a config one, because
`config.yaml` is gitignored and a config-only default never reaches a fresh
clone or a deployment.

**Hard rules — these are load-bearing, and each one exists because a real
run failed without it. The reasoning, and the incident behind every rule, is
in `docs/multifile-agent/agent-contracts.md`; read it before changing
`agent.py`, `builder.py`, the agent prompts, or any verification gate.**

- **Never leave synthesized arguments in a tool call the model can see.**
  Rewriting a call's arguments in place cannot be made safe — the model
  reads anything in an arguments slot as a worked example and copies it back.
  Only **removal** is safe: `_compact_write_calls` deletes the executed
  `write_file` call and its tool result outright, leaving a short note on the
  assistant message. Every placeholder the agent emits carries
  `_PRUNE_SENTINEL`, and `_write_file` rejects any contents containing it or
  a `===== BEGIN/END ... =====` snapshot marker.
- **The conversation is append-only: no message already sent to the model is
  ever mutated or removed.** DeepSeek's prefix cache is byte-exact and
  prefix-only and bills a resent cached token at 1/120th (v4-pro), so
  retention is nearly free and *mutation* is the expense — rewriting message
  *k* invalidates the cache from *k* onward on that turn and every turn after.
  `config.yaml`'s `context_prune_after_steps` is deliberately ignored with a
  warning. `tests/agent_harness.py`'s `scripted_asks` asserts this on every
  agent test, because a prefix-mutating run still ships — only the bill changes.
- **Nothing run-specific may ever be interpolated into the system message** —
  no timestamp, job id, or fork slug. Per-source stability is what makes a
  second enhance of the same game a cache hit on the whole source snapshot.
- **`edit_file` is exact-match-only and `old_string` must occur exactly
  once.** Zero or several matches are rejected outright — never guessed,
  never first-or-all. No error message may echo a candidate `old_string`,
  offer a "did you mean", or show a near-miss diff: all three teach exactly
  the fuzzy matching the tool refuses to do. Every rejection leaves the file
  byte-identical on disk.
- **A turn that changes nothing must never count as progress** — a no-op
  edit, a rejected edit, and a re-`finish` with nothing written since the
  last failure are all bounced, or they buy a stalling run another turn.
- **A gate must accept the fix its own message demands, and must name the
  tool that can act on what it reports.** The explode parity check
  (`_explode_declaration_check`) polices only *top-level* names via
  `_scan_scopes`, fails on names left referenced without a declaration and on
  declarations dropped with nothing replacing them, and fails a top-level
  function/class declared twice — a duplicated `function` is silent where a
  duplicated `const` is a SyntaxError.
- **Never reintroduce the `deepseek-chat`/`deepseek-reasoner` model names** —
  they retire 2026-07-24.
- **A 3D game's modules are merged into one `<script type="module">`, not
  sibling classic scripts.** `import` is a syntax error in a classic script,
  so the ordinary build would break every 3D split. The merged module is still
  one shared scope, so rules 1–4 above hold verbatim; what changes is that
  `THREE`/`OrbitControls`/`PointerLockControls` are ambient (a module declaring its own
  `import`/`export` is rejected by the build) and the Window-collision rename
  rule is inapplicable, because module scope never touches `window`. The
  engine must be passed into `build_and_verify` explicitly — a fork in
  progress has no `meta.json` to read it from.
- **A forced-verification ship must say so.** Passing build → scan → smoke
  means the code *builds*, which was never the same claim as the requested
  change having been carried out.

`agent_events` rows are **never pruned or deleted** — `/status/<job_id>`
replays a months-old run exactly as its requester watched it, and the chat
pane renders from nothing else. `emit()` truncates before it stores, so what
was displayed and what was kept are the same bytes; anything the caps trim is
gone from both.

## 3D games (three.js)

The new-game form has an engine radio: 2D (default) or 3D. A 3D game records
`"engine": "three"` + `"engine_version"` in its `meta.json` and **every fork
inherits both** — the engine belongs to the lineage, not to a request, and
there is no conversion path in either direction. `engines.py` owns everything
about what a 3D game looks like; `builder.read_engine()` is how the rest of the
code asks.

- **The runtime is vendored, not fetched.** `vendor/three/<version>/` holds
  `three.module.min.js` + `three.core.min.js` (the former imports the latter by
  relative path, so both must be present under their upstream names) and the two
  controls addons (`OrbitControls`, `PointerLockControls`) under `addons/`. `scripts/vendor_three.py` fetches and
  hash-verifies them; `--verify` re-checks on disk with no network. Adding a
  version is purely additive — the version is in the URL path, so a game keeps
  resolving to the tree it was generated against.
- **The import map is injected by the platform, never written by the model.**
  `engines.normalize()` strips whatever import map the HTML arrived with and
  inserts the canonical one. It is idempotent by construction (no surrounding
  whitespace, so strip-then-insert is exactly reversible) — which it has to be,
  because a single-file enhance resubmits the stored HTML and the model echoes
  the map back. `safety.scan()` then enforces that the only map present is that
  exact string. That closes a real hole: import map URLs are JSON values, not
  `src=`/`href=` attributes, so the scanner never saw them at all — four
  pre-existing games pull three.js from jsDelivr this way.
- **three.js is ESM-only** (the UMD builds were removed in r161), which forces
  two things. Module scripts always fetch with CORS and a sandboxed game has an
  opaque origin, so `/vendor/three/...` must answer `Access-Control-Allow-Origin:
  *`. And `'self'` cannot be relied on to match for an opaque origin, so the CSP
  names the serving origin explicitly with a path prefix — hence
  `safety.game_csp(origin)` takes an origin instead of being a constant.
- **A 3D game may not load any external script**, including from an
  allow-listed CDN. Without that rule the generic CDN allowance in the prompts
  is enough to talk a model into a jsDelivr `<script src=...three...>`, which
  every other check waves through.
- **Multi-file 3D games build into ONE `<script type="module">`**, not sibling
  classic `<script>` blocks — `import` is a syntax error in a classic script.
  `builder.build_game(src_dir, engine)` merges every local script ref at the
  position of the first, under a fixed two-line import header, so `THREE` and
  the two controls addons are ambient and the multi-file format's "one shared scope"
  contract survives intact. Consequences: a src module carrying its own
  `import`/`export` is rejected by the build, and the explode prompt's rule 5
  (rename names that collide with Window built-ins) does not apply, because
  module scope never touches `window`.
- **`build_and_verify` takes an explicit `engine` override** because a fork in
  progress has no `meta.json` yet — `_stage_fork` deliberately doesn't copy one
  and explode writes one only on success. Without it a 3D game would verify as
  2D for a whole run and fail on syntax errors pointing nowhere near the cause.
- 3D games become multi-file through the **existing** dual-format policy at the
  **existing** `ge.LARGE_SOURCE_BYTES` threshold. Nothing about 3D changes when.

## Running locally

`README.md` has the quickstart (including `ADMIN_TOKEN`, the `job_runner`
config block, and the one-time `playwright install chromium` the smoke test
needs).

`games/` is scanned on every request (mtime-cache-invalidated), so any game
directory dropped in with a valid `index.html` (+ optional `meta.json`)
shows up immediately — useful for testing without going through the
generation pipeline at all. Ratings, however, live in the DB keyed by
`game_id`, so a game with no `game_id` in its `meta.json` can't be rated
or enhanced; give it one (any uuid4 hex) and restart to have the startup
disk-sync register it.

## File map

Derivable — `ls *.py` plus the module docstrings. The non-obvious pieces are
documented in the sections above; `docs/multifile-agent/agent-contracts.md`
covers `agent.py`/`builder.py` and every verification gate.

## Provenance

Ported from `home-net/game_web/` (a home automation bot's Homebot Arcade
module, which used the `claude` CLI for generation and IRC chat commands
to trigger it). See that project's `CLAUDE.md` / `ARCHITECTURE.md` if you
want the original design context — none of that project's MQTT/IRC/SSH
machinery is relevant here, only `game_web/` was the source. The 4-sprint
plan that took this from "serves pre-existing games only" to the system
described above lives in `docs/sprints/`.
