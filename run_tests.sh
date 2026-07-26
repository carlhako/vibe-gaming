#!/usr/bin/env bash
# Runs the test suite. Safe to run on production after a git pull - tests
# mock the DeepSeek client and Playwright smoke test and use an isolated
# temp SQLite DB (tests/conftest.py's isolated_db fixture), so this never
# touches vibegames.db, the network, or a running gunicorn process.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"
source venv/bin/activate
exec pytest "$@"
