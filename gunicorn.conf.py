"""Gunicorn config: starts one job_runner poll loop per worker process.

Run with: gunicorn --workers 2 -c gunicorn.conf.py app:app

The DB-polling design (see job_runner.py) makes this correct regardless of
--workers N — every worker process independently polls the same
generation_requests table and claims jobs via an atomic conditional UPDATE,
so there's no risk of two workers processing the same job.
"""

from pathlib import Path

import yaml

_BASE_DIR = Path(__file__).parent

# gthread instead of the default sync worker class: a sync worker blocks its
# entire process in socket recv() while waiting on a slow/stalled client
# (e.g. a flaky WAN link that stops sending mid-request), so with only 2
# worker processes, 2 stalled clients is enough to make the whole site
# deaf to new connections until gunicorn's --timeout watchdog (30s default)
# kills and respawns the wedged worker. With threads, a stalled client only
# ties up one thread — the other threads in that process keep serving.
worker_class = "gthread"
threads = 4


def _load_config() -> dict:
    config_path = _BASE_DIR / "config.yaml"
    if not config_path.exists():
        return {}
    with open(config_path) as f:
        return yaml.safe_load(f) or {}


# Bind to the same host/port config.yaml already gives the dev server
# (`python3 app.py`), so switching to gunicorn doesn't silently move the
# site to gunicorn's own default of 127.0.0.1:8000.
_gw_cfg = _load_config().get("game_web", {})
bind = f"{_gw_cfg.get('host', '0.0.0.0')}:{_gw_cfg.get('port', 8600)}"


def post_fork(server, worker):
    import job_runner

    config = _load_config()
    games_dir = _BASE_DIR / config.get("game_web", {}).get("games_dir", "games")
    job_runner.start_workers(config, games_dir)
