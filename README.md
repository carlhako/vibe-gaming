# Vibegames

A self-hosted arcade of AI-generated browser games. Describe a game, DeepSeek
writes a single self-contained `index.html`, it's safety-scanned and
smoke-tested, and it shows up in the sidebar — playable in a sandboxed
iframe. Enhance any game and you get a brand-new fork alongside the
original (never overwritten), rate games thumbs up/down, and sort the
shelf by rating.

## Quickstart

```bash
git clone <this repo>
cd vibegames

cp config.yaml.example config.yaml
cp .env.example .env
# edit .env: set DEEPSEEK_API_KEY (from https://platform.deepseek.com)
#            and ADMIN_TOKEN (any long random string - gates /admin/stats)

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium   # one-time, needed by the smoke test

python3 app.py                 # serves on http://localhost:8600
```

First start creates `vibegames.db` and registers the two bundled games
(Block Dodge and Connect 4×4) automatically — no migration step.

That's it — `python3 app.py` starts both the Flask app and the background
job-runner worker thread(s) that actually talk to DeepSeek.

Setting up on a brand-new VM (system packages, Playwright's OS
dependencies, systemd, firewall)? See **[VM-SETUP.md](VM-SETUP.md)** for
the full step-by-step, including what to do if you're copying over an
existing `games/` directory or an older `vibegames.db`.

## Configuration

`config.yaml` (copied from `config.yaml.example`):

- `game_web:` — host/port/`base_url`/`games_dir`.
- `newaiwebgame:` / `enhanceaiwebgame:` — model, effort, timeouts, and
  `max_attempts` for the generate/enhance retry loops.
- `job_runner:` — `workers` (poll-loop threads per process) and
  `poll_interval_seconds`.

`.env` (copied from `.env.example`):

- `DEEPSEEK_API_KEY` — required, from DeepSeek's platform.
- `ADMIN_TOKEN` — required to view `/admin/stats`
  (`?token=...` or `Authorization: Bearer ...`).

## Running in production

```bash
gunicorn --workers 2 -c gunicorn.conf.py app:app
```

`gunicorn.conf.py`'s `post_fork` hook starts one set of job-runner worker
threads per gunicorn worker process. The job runner polls the
`generation_requests` table and claims work with an atomic conditional
`UPDATE`, so this is correct regardless of `--workers N` — there's no
in-memory queue that would only be visible to one process.

If you put this behind a reverse proxy, wrap the app in
`werkzeug.middleware.proxy_fix.ProxyFix` — otherwise `request.remote_addr`
(used for both the ratings anti-abuse IP constraint and the access log)
will read as the proxy's IP for every request.

## Development

```bash
source venv/bin/activate
pytest
```

The suite covers `db.py` (schema, upsert semantics, race-safe job
claiming, rating uniqueness enforcement), the startup disk-sync that
registers bundled games on a fresh clone, and fork-on-enhance linkage
across a multi-generation chain. Tests use an
isolated temp SQLite DB and mock both the DeepSeek client and the
Playwright smoke test — no network calls or browser required.

## How it works

See `CLAUDE.md` for the full architecture writeup (pipeline internals,
schema, file map) and `docs/sprints/` for the design history across the
four sprints that built this out.
