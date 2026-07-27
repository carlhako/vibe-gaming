#!/usr/bin/env python3
"""One-time backfill: commit every pre-existing games/<slug>/ directory
that isn't already fully tracked in git, then push once.

git_sync.py's live hook (job_runner.py) only pushes games generated or
enhanced *after* git_sync.enabled is turned on - anything already sitting
in games/ from before that point never enters git history on its own.
Run this once, by hand, right before flipping git_sync.enabled: true for
the first time on a given VM:

    python3 scripts/backfill_games_to_git.py [--dry-run]

Requires the same GITHUB_PUSH_TOKEN / config.yaml git_sync block as the
live hook. One commit per game directory (commit message = that game's
original generation prompt from meta.json, falling back to a generic
message if meta.json has none), then a single push at the end so a VM
with hundreds of games doesn't hammer GitHub with hundreds of pushes.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()  # GITHUB_PUSH_TOKEN lives in .env, same as app.py/ai_client.py

import git_sync

GAMES_DIR = git_sync.REPO_ROOT / "games"


def _commit_message_for(game_dir: Path) -> str:
    meta_path = game_dir / "meta.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            meta = {}
        prompt = meta.get("prompt")
        if prompt:
            return prompt
    return f"backfill: {game_dir.name}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="list what would be committed without touching git",
    )
    args = parser.parse_args()

    if not GAMES_DIR.exists():
        print(f"no games/ directory at {GAMES_DIR}")
        return 0

    token = None
    if not args.dry_run:
        token = git_sync.require_token()  # fail fast, before doing any work

    committed = 0
    for game_dir in sorted(p for p in GAMES_DIR.iterdir() if p.is_dir()):
        if not (game_dir / "index.html").exists():
            continue
        message = _commit_message_for(game_dir)
        if args.dry_run:
            print(f"would commit: games/{game_dir.name}  ({message!r})")
            continue
        if git_sync.stage_and_commit(game_dir, message):
            print(f"committed: games/{game_dir.name}")
            committed += 1
        else:
            print(f"nothing to commit: games/{game_dir.name}")

    if args.dry_run:
        return 0

    if committed:
        print(f"pushing {committed} commit(s) to origin...")
        git_sync.push_current_branch(token)
        print("done")
    else:
        print("nothing new to push")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
