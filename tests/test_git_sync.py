import subprocess
import threading
import time
from pathlib import Path
from unittest.mock import Mock

import pytest

import git_sync


@pytest.fixture()
def repo_root(tmp_path, monkeypatch):
    monkeypatch.setattr(git_sync, "REPO_ROOT", tmp_path)
    return tmp_path


def test_is_enabled_defaults_false():
    assert git_sync.is_enabled({}) is False
    assert git_sync.is_enabled({"git_sync": {}}) is False


def test_is_enabled_true():
    assert git_sync.is_enabled({"git_sync": {"enabled": True}}) is True


def test_push_game_noop_when_disabled(monkeypatch, repo_root):
    run = Mock()
    monkeypatch.setattr(git_sync, "_run", run)
    git_sync.push_game(repo_root / "games" / "foo", "a prompt", {})
    run.assert_not_called()


def test_push_game_missing_token_raises(monkeypatch, repo_root):
    monkeypatch.delenv("GITHUB_PUSH_TOKEN", raising=False)
    run = Mock()
    monkeypatch.setattr(git_sync, "_run", run)
    with pytest.raises(git_sync.GitSyncError, match="GITHUB_PUSH_TOKEN"):
        git_sync.push_game(
            repo_root / "games" / "foo", "a prompt", {"git_sync": {"enabled": True}}
        )
    run.assert_not_called()


def test_stage_and_commit_success(monkeypatch, repo_root):
    game_dir = repo_root / "games" / "foo"
    run = Mock(return_value="")
    monkeypatch.setattr(git_sync, "_run", run)
    diff_result = Mock(returncode=1)  # 1 => staged changes exist
    monkeypatch.setattr(
        git_sync.subprocess, "run", Mock(return_value=diff_result)
    )

    committed = git_sync.stage_and_commit(game_dir, "the prompt")

    assert committed is True
    add_call, commit_call = run.call_args_list
    assert add_call.args[0] == ["git", "add", "games/foo"]
    assert commit_call.args[0] == [
        "git", "commit", "-m", "the prompt", "--", "games/foo",
    ]


def test_stage_and_commit_nothing_to_commit(monkeypatch, repo_root):
    game_dir = repo_root / "games" / "foo"
    run = Mock(return_value="")
    monkeypatch.setattr(git_sync, "_run", run)
    diff_result = Mock(returncode=0)  # 0 => nothing staged
    monkeypatch.setattr(
        git_sync.subprocess, "run", Mock(return_value=diff_result)
    )

    committed = git_sync.stage_and_commit(game_dir, "the prompt")

    assert committed is False
    # only "git add" ran - no commit call
    assert run.call_args_list == [((["git", "add", "games/foo"],), {})]


def test_stage_and_commit_add_failure_raises(monkeypatch, repo_root):
    game_dir = repo_root / "games" / "foo"

    def fake_run(args):
        raise git_sync.GitSyncError("boom")

    monkeypatch.setattr(git_sync, "_run", fake_run)

    with pytest.raises(git_sync.GitSyncError):
        git_sync.stage_and_commit(game_dir, "the prompt")


def test_push_current_branch_builds_token_url(monkeypatch, repo_root):
    calls = []

    def fake_run(args):
        calls.append(args)
        if args[:2] == ["git", "rev-parse"]:
            return "main"
        if args[:2] == ["git", "remote"]:
            return "https://github.com/carlhako/vibe-gaming.git"
        return ""

    monkeypatch.setattr(git_sync, "_run", fake_run)

    git_sync.push_current_branch(token="secret123")

    push_call = calls[-1]
    assert push_call[:2] == ["git", "push"]
    pushed_url = push_call[2]
    assert "x-access-token:secret123@github.com" in pushed_url
    assert push_call[3] == "HEAD:main"


def test_push_url_with_token_rejects_non_https(monkeypatch, repo_root):
    monkeypatch.setattr(
        git_sync, "_run", Mock(return_value="git@github.com:carlhako/vibe-gaming.git")
    )
    with pytest.raises(git_sync.GitSyncError, match="HTTPS"):
        git_sync._push_url_with_token("secret123")


def test_push_game_commits_and_pushes(monkeypatch, repo_root):
    monkeypatch.setenv("GITHUB_PUSH_TOKEN", "secret123")
    calls = []

    def fake_run(args):
        calls.append(args)
        if args[:2] == ["git", "rev-parse"]:
            return "main"
        if args[:2] == ["git", "remote"]:
            return "https://github.com/carlhako/vibe-gaming.git"
        return ""

    monkeypatch.setattr(git_sync, "_run", fake_run)
    monkeypatch.setattr(
        git_sync.subprocess, "run", Mock(return_value=Mock(returncode=1))
    )

    git_sync.push_game(
        repo_root / "games" / "foo", "a prompt", {"git_sync": {"enabled": True}}
    )

    kinds = [c[0][:2] if isinstance(c, list) else c for c in calls]
    assert ["git", "add"] in [c[:2] for c in calls]
    assert ["git", "commit"] in [c[:2] for c in calls]
    assert ["git", "push"] in [c[:2] for c in calls]


def test_push_game_skips_push_when_nothing_to_commit(monkeypatch, repo_root):
    monkeypatch.setenv("GITHUB_PUSH_TOKEN", "secret123")
    run = Mock(return_value="")
    monkeypatch.setattr(git_sync, "_run", run)
    monkeypatch.setattr(
        git_sync.subprocess, "run", Mock(return_value=Mock(returncode=0))
    )

    git_sync.push_game(
        repo_root / "games" / "foo", "a prompt", {"git_sync": {"enabled": True}}
    )

    # only "git add" ran - no commit, no push
    called_prefixes = [c.args[0][:2] for c in run.call_args_list]
    assert called_prefixes == [["git", "add"]]


def test_push_game_serializes_concurrent_calls(monkeypatch, tmp_path):
    """Two jobs finishing at the same time (two gunicorn worker processes,
    or two poll threads in one) must never interleave their git add/commit
    /push - that's exactly the race that used to make a push silently fail
    (a `.git/index.lock` collision, or a non-fast-forward push rejection)
    with only a swallowed exception and a print() to show for it. Runs
    against a real local git repo (no network) so stage_and_commit's git
    calls are real; push_current_branch is stubbed out since it would
    otherwise need a real GitHub remote."""
    repo = tmp_path
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "games").mkdir()
    (repo / "games" / "seed").mkdir()
    (repo / "games" / "seed" / "index.html").write_text("seed")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=repo, check=True)

    monkeypatch.setattr(git_sync, "REPO_ROOT", repo)
    monkeypatch.setenv("GITHUB_PUSH_TOKEN", "secret123")
    monkeypatch.setattr(git_sync, "push_current_branch", lambda token=None: None)

    real_stage_and_commit = git_sync.stage_and_commit
    concurrency_lock = threading.Lock()
    concurrent = 0
    max_concurrent = 0

    def tracked_stage_and_commit(game_dir, commit_message):
        nonlocal concurrent, max_concurrent
        with concurrency_lock:
            concurrent += 1
            max_concurrent = max(max_concurrent, concurrent)
        time.sleep(0.05)  # widen the window a real race would need
        try:
            return real_stage_and_commit(game_dir, commit_message)
        finally:
            with concurrency_lock:
                concurrent -= 1

    monkeypatch.setattr(git_sync, "stage_and_commit", tracked_stage_and_commit)

    for i in (1, 2):
        d = repo / "games" / f"game{i}"
        d.mkdir()
        (d / "index.html").write_text(f"game {i}")

    errors = []

    def run(i):
        try:
            git_sync.push_game(
                repo / "games" / f"game{i}", f"add game {i}",
                {"git_sync": {"enabled": True}},
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(i,)) for i in (1, 2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert max_concurrent == 1  # the flock serialized the two push_game calls

    log = subprocess.run(
        ["git", "log", "--oneline"], cwd=repo, capture_output=True, text=True, check=True,
    )
    assert "add game 1" in log.stdout
    assert "add game 2" in log.stdout
