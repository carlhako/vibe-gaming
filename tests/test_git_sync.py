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
