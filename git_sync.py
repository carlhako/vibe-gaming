"""git_sync — best-effort auto-commit/push of generated game directories
to the repo's GitHub remote.

Off by default (config.git_sync.enabled), so a checkout with no
GITHUB_PUSH_TOKEN configured behaves exactly as before this module
existed. When enabled, job_runner.py calls push_game() after a
successful create/enhance/explode job; a failure here is logged and
never flips the job's own success/failure status — the game already
generated and smoke-tested fine, a git push failing (bad token, network
blip, GitHub outage) is a separate concern.

The GitHub push token is read from the GITHUB_PUSH_TOKEN environment
variable and only ever used to build a one-off push URL passed as a
subprocess argv element — never written into .git/config, so
`git remote -v` never leaks it.
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import subprocess
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

REPO_ROOT = Path(__file__).resolve().parent


class GitSyncError(Exception):
    """Raised when a git operation needed to sync a game to GitHub fails."""


@contextlib.contextmanager
def _push_lock():
    """Hold an exclusive, cross-process lock around a push_game() call so
    two jobs finishing at the same time (two gunicorn worker processes, or
    two poll threads) never interleave their git add/commit/push. flock
    (not threading.Lock) is what actually serializes them: it's held by
    the OS per open file descriptor and blocks across process boundaries,
    not just threads within one process. The path is computed here (not
    cached at import time) so it always tracks REPO_ROOT, including tests
    that monkeypatch it to an isolated tmp dir."""
    lock_path = REPO_ROOT / ".git" / "vibegames-push.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def is_enabled(config: dict) -> bool:
    return bool((config or {}).get("git_sync", {}).get("enabled", False))


def _run(args: list[str]) -> str:
    proc = subprocess.run(
        args, cwd=REPO_ROOT, capture_output=True, text=True, timeout=60,
    )
    if proc.returncode != 0:
        raise GitSyncError(
            f"`{' '.join(args)}` failed (exit {proc.returncode}): {proc.stderr.strip()}"
        )
    return proc.stdout.strip()


def _push_url_with_token(token: str) -> str:
    """Rewrite the existing `origin` remote URL to embed `token` as an
    x-access-token HTTPS credential, without ever persisting it into
    .git/config."""
    origin_url = _run(["git", "remote", "get-url", "origin"])
    parts = urlsplit(origin_url)
    if parts.scheme not in ("http", "https"):
        raise GitSyncError(
            f"origin remote {origin_url!r} is not an HTTPS URL — "
            "git_sync only supports HTTPS + token auth"
        )
    netloc = parts.hostname or ""
    if parts.port:
        netloc += f":{parts.port}"
    authed_netloc = f"x-access-token:{token}@{netloc}"
    return urlunsplit((parts.scheme, authed_netloc, parts.path, "", ""))


def require_token() -> str:
    """Return GITHUB_PUSH_TOKEN, raising GitSyncError if it's unset."""
    token = os.environ.get("GITHUB_PUSH_TOKEN")
    if not token:
        raise GitSyncError(
            "git_sync.enabled is true but GITHUB_PUSH_TOKEN is not set in the environment"
        )
    return token


def stage_and_commit(game_dir: Path, commit_message: str) -> bool:
    """git add + commit `game_dir` (relative to the repo root) with
    `commit_message`. Returns False (not an error) if there was nothing to
    commit, True if a commit was made. Raises GitSyncError on git failure.
    Does not push - see push_current_branch()/push_game()."""
    game_dir = Path(game_dir)
    rel_dir = game_dir.resolve().relative_to(REPO_ROOT)

    _run(["git", "add", str(rel_dir)])

    diff = subprocess.run(
        ["git", "diff", "--cached", "--quiet", "--", str(rel_dir)],
        cwd=REPO_ROOT, timeout=60,
    )
    if diff.returncode == 0:
        return False  # nothing staged for this directory - nothing to commit

    _run(["git", "commit", "-m", commit_message, "--", str(rel_dir)])
    return True


def push_current_branch(token: str | None = None) -> None:
    """Push HEAD to origin over HTTPS using an x-access-token URL built at
    call time - never persisted into .git/config."""
    token = token or require_token()
    branch = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    push_url = _push_url_with_token(token)
    _run(["git", "push", push_url, f"HEAD:{branch}"])


def push_game(game_dir: Path, commit_message: str, config: dict) -> None:
    """git add + commit + push `game_dir` (relative to the repo root) with
    `commit_message`. No-op (not an error) if there is nothing to commit.
    Raises GitSyncError on any git failure.

    Holds a cross-process lock for the whole add+commit+push sequence -
    without it, two jobs finishing around the same time (two gunicorn
    worker processes, or two poll threads) can race: a concurrent `git
    commit` collides on `.git/index.lock`, or two pushes land back to
    back and the second is rejected as non-fast-forward. Either failure
    was previously swallowed by job_runner's best-effort except-and-log,
    so the push silently never happened."""
    if not is_enabled(config):
        return

    token = require_token()
    with _push_lock():
        if stage_and_commit(game_dir, commit_message):
            push_current_branch(token)
