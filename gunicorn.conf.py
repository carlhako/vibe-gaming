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
