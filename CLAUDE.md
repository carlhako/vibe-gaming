# CLAUDE.md

Guidance for Claude Code (or any AI assistant) picking up this repo cold.

## What this is

Vibegames is a self-contained Flask arcade site that hosts AI-generated
single-file HTML5/JS browser games. It was forked out of a larger home
automation project (`home-net`'s `game_web/` module) to run as its own
project, generating games with **DeepSeek** instead of Claude.

Every game is one `index.html` — canvas or DOM, all HTML/CSS/JS inline,
optionally pulling a script/stylesheet from an allow-listed CDN. Games are
served inside a sandboxed `<iframe>` (`sandbox="allow-scripts allow-forms
allow-pointer-lock"`, no `allow-same-origin`), so a generated game gets an
opaque origin and cannot reach cookies, localStorage, the parent frame, or
other games. That sandbox is the primary security boundary; the safety
scanner and smoke test below are defense-in-depth on top of it, not the
main line of defense.

## Current state — what's wired up vs. not

**Wired up (ported and working as of this writing):**
- `app.py` — the Flask site itself: menu UI, `/api/games`, `/play/<slug>`.
- `safety.py` — regex blocklist + CDN allowlist, scans generated HTML before
  it's ever written to disk.
- `smoke_test.py` — headless Playwright load of generated HTML, fails the
  attempt on any uncaught JS exception or `console.error`.
- `ai_client.py` — DeepSeek client (Chat Completions API via the `openai`
  SDK pointed at DeepSeek's base URL). Replaces home-net's
  `irc_bot/libs/ai.py` (which shelled out to the `claude` CLI).
- `db.py` — trimmed-down SQLite registry, just the `web_games` table.
- `game_generator.py` / `game_enhancer.py` — the generate/enhance pipelines,
  ported to call `ai_client` and the local `db.py` instead of the
  home-net versions.
- `games/sample-game/` — a hand-written placeholder (Snake) proving the
  pipeline end-to-end without spending an API call.

**NOT wired up yet (deliberately left for a follow-up pass):**
- There is **no UI or route to trigger generation or enhancement**. The
  original project drove `game_generator.generate_game()` /
  `game_enhancer.enhance_game()` from IRC chat commands; this project has no
  chat layer. `app.py` currently only *serves* games — it has no endpoint
  that calls into `game_generator`/`game_enhancer` at all. Adding a web
  input (a form/box on the page to request a new game or request changes to
  an existing one, calling the pipelines synchronously or via a background
  job) is the next piece of work, not yet built.
- No deploy tooling was ported (the original `deploy_game_web.py` did an
  SSH/systemd deploy specific to that project's VM). Run this locally for
  now; deploying it somewhere is a separate decision.
- The original test suite (`tests/game_web/`) was not ported — the import
  paths changed (no more `game_web.` package prefix, `db`/`ai` are now local
  modules) so the old tests won't run as-is. Worth re-creating once the
  generation endpoint exists to test against.

## How the generation pipeline works (for when you build the trigger)

Both `game_generator.generate_game(description, requested_by, config,
db_conn=None, games_dir=None)` and `game_enhancer.enhance_game(slug,
description, requested_by, config, db_conn=None, games_dir=None,
backups_dir=None)` return a result dict with a `message` key (human-readable
report) and `success`/`url`/`error` etc. Call them directly from a new Flask
route — they're synchronous and can take a while (a DeepSeek call plus a
Playwright smoke test), so a real UI will want this backgrounded (thread,
queue, whatever) rather than blocking the request.

Pipeline shape (same for both): build a prompt → call `ai_client.ask()` →
parse the reply against a strict `===GAME_FILE===` / `===META===` /
`===NOTES===` marker format (`game_generator.parse_generation_response`) →
run `safety.scan()` on the HTML → write `games/<slug>/{index.html,meta.json}`
→ run `smoke_test.run_smoke_test()` → on any failure, roll back (delete for
a new game, restore-from-backup for an enhancement) and retry with the
concrete failure fed back into the next prompt, up to `max_attempts`. On
success, `db.register_web_game()` upserts the registry row.

`config` is a plain dict matching `config.yaml.example` — pass
`yaml.safe_load(open("config.yaml"))` in. The `newaiwebgame:` /
`enhanceaiwebgame:` blocks control model/effort/attempts/timeouts.

## ai_client.py — the DeepSeek swap

`ai_client.ask(prompt, system_prompt=None, model=None, effort=None,
timeout=120)` mirrors the `AskResult`/`AIError` shape of the original
Claude-CLI wrapper it replaced, so `game_generator.py`/`game_enhancer.py`
needed almost no changes beyond the import. Key difference: DeepSeek has no
"effort" concept, so `effort` is mapped onto DeepSeek's two models instead
of being passed through — `"high"` → `deepseek-reasoner`, anything else →
`deepseek-chat`. Pass `model` explicitly to bypass that mapping. Requires
`DEEPSEEK_API_KEY` in the environment (`.env`, loaded via python-dotenv).

## Running locally

```bash
cp config.yaml.example config.yaml
cp .env.example .env        # fill in DEEPSEEK_API_KEY
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
playwright install chromium   # required once, for smoke_test.py
python3 app.py                 # serves on :8600 by default
```

`games/` is scanned on every request (mtime-cache-invalidated), so any game
directory dropped in with a valid `index.html` (+ optional `meta.json`)
shows up immediately — useful for testing without going through the
generation pipeline at all.

## File map

```
app.py               Flask site: menu UI + /api/games + /play/<slug>
game_generator.py     generate -> safety-scan -> write -> smoke-test -> retry loop (new games)
game_enhancer.py      same loop, scoped to one existing slug, with backup/restore
safety.py             regex blocklist + CDN allowlist for generated HTML
smoke_test.py         headless Playwright load, fails on JS errors
ai_client.py          DeepSeek Chat Completions client (swap point for other providers)
db.py                 SQLite: web_games registry (slug -> title/desc/version/status/...)
templates/index.html  menu shell (sidebar + sandboxed iframe)
static/style.css      arcade-cabinet styling
static/app.js         click-to-load-game-into-iframe behavior
games/sample-game/    hand-written placeholder game (proves the serving path works)
games/backups/        pre-enhancement snapshots, created by game_enhancer.py
config.yaml.example   copy to config.yaml
.env.example           copy to .env, fill in DEEPSEEK_API_KEY
```

## Provenance

Ported from `home-net/game_web/` (a home automation bot's Homebot Arcade
module, which used the `claude` CLI for generation). See that project's
`CLAUDE.md` / `ARCHITECTURE.md` if you want the original design context —
none of that project's MQTT/IRC/SSH machinery is relevant here, only
`game_web/` was the source.
