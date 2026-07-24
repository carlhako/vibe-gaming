"""The moderation-call audit trail: db.moderation_calls, content_moderation
.check_game()'s expanded return shape, game_generator.run_moderation_pass()
logging every call (not just flagged ones), and the /admin/moderation page."""

import json
from pathlib import Path
from unittest import mock

import app as app_module
import ai_client as ai
import content_moderation
import db
import game_generator as gg


def _register_game(slug="game-one", game_id="a" * 32):
    db.register_web_game(
        game_id=game_id, slug=slug, title="Game One", description="d",
        requested_by="web:x", status="success", attempts=1,
    )
    return game_id


# ---------------------------------------------------------------------------
# db.add_moderation_call / get_moderation_history / count_moderation_calls
# ---------------------------------------------------------------------------

def test_add_and_get_moderation_history_joins_game_and_orders_newest_first(isolated_db):
    game_a = _register_game(slug="game-a", game_id="a" * 32)
    game_b = _register_game(slug="game-b", game_id="b" * 32)

    db.add_moderation_call(
        game_a, prompt="prompt A", raw_response=json.dumps({"id": "resp-a"}),
        model="deepseek-v4-flash", input_tokens=10, output_tokens=5,
        flagged=False, reason="",
    )
    db.add_moderation_call(
        game_b, prompt="prompt B", raw_response=json.dumps({"id": "resp-b"}),
        model="deepseek-v4-flash", input_tokens=20, output_tokens=8,
        flagged=True, reason="asks for a password",
    )

    assert db.count_moderation_calls() == 2
    rows = db.get_moderation_history()
    assert [r["game_id"] for r in rows] == [game_b, game_a], "newest first"
    flagged_row = rows[0]
    assert flagged_row["title"] == "Game One"
    assert flagged_row["slug"] == "game-b"
    assert flagged_row["flagged"] == 1
    assert flagged_row["reason"] == "asks for a password"
    assert json.loads(flagged_row["raw_response"]) == {"id": "resp-b"}


def test_get_moderation_history_paginates(isolated_db):
    for i in range(3):
        game_id = _register_game(slug=f"game-{i}", game_id=f"{i}" * 32)
        db.add_moderation_call(game_id, prompt=f"p{i}", flagged=False, reason="")

    page = db.get_moderation_history(limit=2, offset=2)
    assert len(page) == 1


def test_moderation_history_survives_missing_web_games_row(isolated_db):
    """A log row for a game_id with no (or no longer any) web_games row
    still surfaces — title/slug are just NULL, via the LEFT JOIN."""
    db.add_moderation_call("orphan" * 5 + "aaa", prompt="p", flagged=False, reason="")
    rows = db.get_moderation_history()
    assert rows[0]["title"] is None
    assert rows[0]["slug"] is None


# ---------------------------------------------------------------------------
# content_moderation.check_game — expanded return shape
# ---------------------------------------------------------------------------

def test_check_game_returns_prompt_and_raw_response_on_success():
    ask_result = ai.AskResult(
        text='{"flagged": false, "reason": ""}', input_tokens=12, output_tokens=4,
        model="deepseek-v4-flash", effort="non-thinking", raw_response={"id": "resp-1"},
    )
    with mock.patch.object(ai, "ask", return_value=ask_result) as mocked:
        result = content_moderation.check_game("<html>ok</html>", "a game", "")

    assert result["flagged"] is False
    assert result["raw_response"] == {"id": "resp-1"}
    assert result["model"] == "deepseek-v4-flash"
    assert result["input_tokens"] == 12
    assert result["output_tokens"] == 4
    assert "<html>ok</html>" in result["prompt"]
    mocked.assert_called_once()


def test_check_game_ai_error_still_returns_prompt_with_no_raw_response():
    with mock.patch.object(ai, "ask", side_effect=ai.AIError("outage")):
        result = content_moderation.check_game("<html>ok</html>", "a game", "")

    assert result["flagged"] is False
    assert result["raw_response"] is None
    assert "<html>ok</html>" in result["prompt"]


# ---------------------------------------------------------------------------
# game_generator.run_moderation_pass — logs every call, flagged or not
# ---------------------------------------------------------------------------

def _write_game(games_dir, slug):
    d = games_dir / slug
    d.mkdir()
    (d / "index.html").write_text("<html>ok</html>", encoding="utf-8")


def test_run_moderation_pass_logs_unflagged_call(isolated_db, games_dir):
    game_id = _register_game(slug="game-one", game_id="a" * 32)
    _write_game(games_dir, "game-one")
    check_result = {
        "flagged": False, "reason": "", "prompt": "the full prompt",
        "raw_response": {"id": "resp-x"}, "model": "deepseek-v4-flash",
        "input_tokens": 9, "output_tokens": 3,
    }
    with mock.patch.object(content_moderation, "check_game", return_value=check_result):
        gg.run_moderation_pass(game_id, "game-one", "desc", "", games_dir)

    rows = db.get_moderation_history()
    assert len(rows) == 1
    assert rows[0]["game_id"] == game_id
    assert rows[0]["flagged"] == 0
    assert rows[0]["prompt"] == "the full prompt"
    assert json.loads(rows[0]["raw_response"]) == {"id": "resp-x"}
    assert db.get_web_game(game_id)["hidden"] == 0


def test_run_moderation_pass_logs_flagged_call_and_still_hides(isolated_db, games_dir):
    game_id = _register_game(slug="game-one", game_id="a" * 32)
    _write_game(games_dir, "game-one")
    check_result = {
        "flagged": True, "reason": "asks for a password", "prompt": "the full prompt",
        "raw_response": {"id": "resp-y"}, "model": "deepseek-v4-flash",
        "input_tokens": 9, "output_tokens": 3,
    }
    with mock.patch.object(content_moderation, "check_game", return_value=check_result):
        gg.run_moderation_pass(game_id, "game-one", "desc", "", games_dir)

    rows = db.get_moderation_history()
    assert rows[0]["flagged"] == 1
    assert db.get_web_game(game_id)["hidden"] == 1


def test_run_moderation_pass_tolerates_legacy_mock_missing_new_keys(isolated_db, games_dir):
    """Old-style test doubles that only return {"flagged", "reason"} (as
    used throughout test_generation_loop.py) must not blow up on the
    prompt column's NOT NULL constraint."""
    game_id = _register_game(slug="game-one", game_id="a" * 32)
    _write_game(games_dir, "game-one")
    with mock.patch.object(
        content_moderation, "check_game", return_value={"flagged": False, "reason": ""},
    ):
        gg.run_moderation_pass(game_id, "game-one", "desc", "", games_dir)

    assert db.count_moderation_calls() == 1


def test_run_moderation_pass_swallows_logging_failure(isolated_db, games_dir):
    """A busted db.add_moderation_call must never propagate out of
    run_moderation_pass and turn a successful generation into a failed job."""
    game_id = _register_game(slug="game-one", game_id="a" * 32)
    _write_game(games_dir, "game-one")
    check_result = {"flagged": False, "reason": "", "prompt": "p", "raw_response": None,
                    "model": None, "input_tokens": None, "output_tokens": None}
    with mock.patch.object(content_moderation, "check_game", return_value=check_result), \
         mock.patch.object(db, "add_moderation_call", side_effect=RuntimeError("db exploded")):
        gg.run_moderation_pass(game_id, "game-one", "desc", "", games_dir)  # must not raise


# ---------------------------------------------------------------------------
# GET /admin/moderation
# ---------------------------------------------------------------------------

def make_client(games_dir, monkeypatch, admin_token="secret-token"):
    monkeypatch.setenv("ADMIN_TOKEN", admin_token)
    flask_app = app_module.create_app(games_dir=games_dir)
    flask_app.config["TESTING"] = True
    return flask_app.test_client()


def test_admin_moderation_requires_valid_token(isolated_db, games_dir, monkeypatch):
    client = make_client(games_dir, monkeypatch)
    assert client.get("/admin/moderation").status_code == 403
    assert client.get("/admin/moderation?token=wrong").status_code == 403
    assert client.get("/admin/moderation?token=secret-token").status_code == 200


def test_admin_moderation_lists_calls_with_prompt_and_json(isolated_db, games_dir, monkeypatch):
    game_id = _register_game(slug="game-one", game_id="a" * 32)
    db.add_moderation_call(
        game_id, prompt="the full prompt text", raw_response=json.dumps({"id": "resp-1"}),
        model="deepseek-v4-flash", input_tokens=9, output_tokens=3,
        flagged=True, reason="asks for a password",
    )

    client = make_client(games_dir, monkeypatch)
    resp = client.get("/admin/moderation?token=secret-token")
    assert resp.status_code == 200
    assert b"Game One" in resp.data
    assert b"asks for a password" in resp.data
    assert b"the full prompt text" in resp.data
    assert b"resp-1" in resp.data


def test_admin_moderation_shows_empty_state(isolated_db, games_dir, monkeypatch):
    client = make_client(games_dir, monkeypatch)
    resp = client.get("/admin/moderation?token=secret-token")
    assert resp.status_code == 200
    assert b"No moderation calls recorded yet." in resp.data
