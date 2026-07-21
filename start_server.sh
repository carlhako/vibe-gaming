#!/usr/bin/env bash
# Starts vibegames in production mode (gunicorn, not the python3 app.py dev
# server) - see gunicorn.conf.py for the bind address/port (read from
# config.yaml's game_web.host/port) and the per-worker job_runner startup.
#
# Meant to be the systemd ExecStart target:
#   ExecStart=/home/<your-user>/vibegames/start_server.sh
# or run directly for a foreground/screen-session launch.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"
source venv/bin/activate
mkdir -p logs
exec gunicorn --workers 2 -c gunicorn.conf.py \
    --access-logfile logs/gunicorn-access.log \
    --error-logfile logs/gunicorn-error.log \
    --capture-output \
    app:app
