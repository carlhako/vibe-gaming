# Setting up Vibegames on a fresh VM

Assumes a brand-new Linux VM with Python 3.11+ already installed (check
with `python3 --version`) and nothing else set up yet.

## 1. System packages

```bash
sudo apt update
sudo apt install -y git python3-venv python3-pip
```

## 2. Get the code

```bash
git clone https://github.com/carlhako/vibe-gaming.git vibegames
cd vibegames
```

If you're moving from an existing machine instead of a fresh clone, copy
the whole `vibegames/` directory over (including `games/` and, if you want
to keep history, `vibegames.db`) rather than starting from git alone —
`vibegames.db` and any generated games under `games/` are gitignored and
won't come from `git clone`.

## 3. Python environment

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## 4. Playwright browser (needed by the smoke test)

On a bare VM, Playwright's browser binary AND its system-level shared
libraries are both missing — `--with-deps` installs both in one shot:

```bash
playwright install --with-deps chromium
```

(If `--with-deps` fails because `apt` needs `sudo` and you're not root,
run `sudo playwright install-deps chromium` first, then
`playwright install chromium` without `--with-deps`.)

## 5. Configuration

```bash
cp config.yaml.example config.yaml
cp .env.example .env
```

Edit `.env` and set:
- `DEEPSEEK_API_KEY` — from https://platform.deepseek.com
- `ADMIN_TOKEN` — any long random string; gates `/admin/stats`. Generate
  one with `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`

`config.yaml` defaults are fine to start (`game_web.host: 0.0.0.0`,
`port: 8600`). If this VM sits behind a domain/reverse proxy, set
`game_web.base_url` so generated "play it" links point at the public URL
instead of `localhost`.

## 6. Migrate/register existing games

**Run this even on a first-ever install** — it's what creates
`vibegames.db` and its schema in the first place, and it's always safe to
re-run (idempotent).

```bash
python3 migrate_to_guid_schema.py
```

What this does, and why it matters beyond a fresh install:

- Creates `vibegames.db` with the current schema if it doesn't exist yet.
- If you copied over an **older** `vibegames.db` (pre-GUID schema, `slug`
  as the primary key), rebuilds `web_games` into the current `game_id`-keyed
  schema, preserving every row's data.
- If you copied over games under `games/` that were never registered in
  the DB at all — most notably the bundled `games/sample-game/`, which is
  hand-written and has no `game_id` in its `meta.json` — this mints a
  `game_id` for each one, writes it into `meta.json`, and inserts the
  corresponding `web_games` row. **Without this step, such games still
  list and play fine, but show no Enhance link and can't be rated** (both
  require a `game_id`).
- Backs up `vibegames.db` to `vibegames.db.bak` before touching anything.

You'll see output like:

```
web_games table does not exist yet; creating current schema.
Registered previously-unregistered game 'sample-game' -> game_id=1474f6d8...
  updated games/sample-game/meta.json
Synced 1 previously-unregistered game(s) from disk into the DB.
```

If you ever drop a *new* hand-written game directory straight into
`games/` (skipping the web UI), re-run this script afterward to give it a
`game_id` too.

## 7. Run it

**Quick/dev:**

```bash
python3 app.py
```

Serves on `http://<vm-ip>:8600` and starts the background job-runner
worker thread(s) in the same process — no separate step needed.

**Production (gunicorn):**

```bash
gunicorn --workers 2 -c gunicorn.conf.py app:app
```

`gunicorn.conf.py` starts one set of job-runner worker threads per
gunicorn worker process — this is safe and correct because the job runner
polls the database (not an in-memory queue), so multiple workers never
double-process the same job.

To keep it running after you log out, use a systemd unit, e.g.
`/etc/systemd/system/vibegames.service`:

```ini
[Unit]
Description=Vibegames arcade
After=network.target

[Service]
Type=simple
User=<your-user>
WorkingDirectory=/home/<your-user>/vibegames
Environment=PATH=/home/<your-user>/vibegames/venv/bin
ExecStart=/home/<your-user>/vibegames/venv/bin/gunicorn --workers 2 -c gunicorn.conf.py app:app
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now vibegames
```

## 8. Open the port

```bash
sudo ufw allow 8600/tcp   # or whatever port you set in config.yaml
```

If you're running behind a reverse proxy (nginx, Caddy, etc.) instead of
exposing the port directly, wrap the app in
`werkzeug.middleware.proxy_fix.ProxyFix` — otherwise `request.remote_addr`
(used for both the ratings anti-abuse IP check and the access log) will
see only the proxy's IP for every visitor. This isn't wired up by default
since it depends on your proxy setup; see the comments near
`request.remote_addr` in `app.py`.

## 9. Verify

- `http://<vm-ip>:8600/` — sidebar should show your games (including
  Sample Snake with a working Enhance button if you ran step 6).
- `http://<vm-ip>:8600/games/new` — submit a prompt, confirm it reaches
  `success` on the status page.
- `http://<vm-ip>:8600/admin/stats?token=<your ADMIN_TOKEN>` — should
  render; without `?token=` it should 403.
