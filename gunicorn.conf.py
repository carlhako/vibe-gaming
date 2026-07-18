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


def post_fork(server, worker):
    import job_runner

    config_path = _BASE_DIR / "config.yaml"
    config = {}
    if config_path.exists():
        with open(config_path) as f:
            config = yaml.safe_load(f) or {}
    games_dir = _BASE_DIR / config.get("game_web", {}).get("games_dir", "games")
    job_runner.start_workers(config, games_dir)
