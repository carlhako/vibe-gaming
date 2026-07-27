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

`vibegames.db` is gitignored and always VM-local — it won't come from
`git clone`, and there's no automated sync for it (see `git_sync` below,
which only ever pushes game *files*, never the DB). If you're moving from
an existing machine, copy `vibegames.db` over if you want to keep its
history (ratings, creator identities, generation audit trail); it must
already be on the current GUID schema (`game_id`-keyed `web_games`) — the
one-off migration script for pre-GUID databases has been removed, so a DB
from the old platform can't be upgraded from here.

Generated games under `games/` are a different story depending on whether
the *source* VM had `git_sync.enabled: true` (see step 5): if it did,
every game generated or enhanced after that was already pushed to GitHub,
so a fresh `git clone` on the new machine already has them, and the
startup `sync_games_from_disk()` reconstructs the matching `web_games`
rows the first time the app runs there — no manual copy needed. Only
games generated *before* `git_sync` was enabled (or on a VM that never
enabled it) are gitignored and VM-local, and still need a manual copy of
`games/` the same as before.

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

**Optional: auto-push generated games to GitHub.** If this VM should
commit and push every successfully generated/enhanced game directory back
to this repo's GitHub remote (commit message = the prompt that produced
it), set `git_sync.enabled: true` in `config.yaml` and generate a
fine-grained GitHub Personal Access Token scoped to just this repo
(Contents: read/write) at
https://github.com/settings/personal-access-tokens/new, then set
`GITHUB_PUSH_TOKEN` in `.env` to it. Leave `git_sync.enabled: false`
(the default) to keep this VM's generated games VM-local, exactly as
before this feature existed. If you're turning this on for a VM that
already has pre-existing games in `games/`, run
`python3 scripts/backfill_games_to_git.py` once first (see its `--dry-run`
flag) to commit/push everything already there — the live hook only
covers *future* jobs.

## 6. Database and bundled games — nothing to do

There's no migration or seeding step. On first start the app creates
`vibegames.db` with the current schema and registers the two bundled
games (Block Dodge and Connect 4×4) from their committed `meta.json`
`game_id`s, so they arrive with working rate/Enhance controls. This
disk-sync runs on every start and is a no-op once the rows exist.

If you ever drop a hand-written game directory straight into `games/`
(skipping the web UI), it lists and plays as-is. To also give it
rate/Enhance controls, add a `game_id` (any fresh uuid4 hex, e.g. from
`python3 -c "import uuid; print(uuid.uuid4().hex)"`) to its `meta.json`
and restart — the startup sync registers it.

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

- `http://<vm-ip>:8600/` — sidebar should show Block Dodge and
  Connect 4×4, each with working rate and Enhance controls.
- `http://<vm-ip>:8600/games/new` — submit a prompt, confirm it reaches
  `success` on the status page.
- `http://<vm-ip>:8600/admin/stats?token=<your ADMIN_TOKEN>` — should
  render; without `?token=` it should 403.
