"""'Ask AI about this game': html_sanitize, ai_qa.answer_question,
job_runner's kind='ask' dispatch, the new db.py helpers/kind filters, and
the /api/games/<id>/ask, /api/status/<id>, /api/games/<id>/info, and
/admin/stats routes involved."""

import json
from unittest import mock

import app as app_module
import ai_client as ai
import ai_qa
import db
import game_enhancer
import job_runner
from html_sanitize import sanitize_answer_html

CONFIG = {
    "askaiwebgame": {"model": None, "effort": "high", "timeout_seconds": 5, "max_tokens": 500},
}


# ---------------------------------------------------------------------------
# html_sanitize.sanitize_answer_html
# ---------------------------------------------------------------------------

def test_sanitize_strips_script_and_its_content():
    out = sanitize_answer_html("<p>before</p><script>alert(1)</script><p>after</p>")
    assert "<script" not in out
    assert "alert(1)" not in out
    assert "<p>before</p>" in out
    assert "<p>after</p>" in out


def test_sanitize_strips_style_and_its_content():
    out = sanitize_answer_html("<style>body{color:red}</style><p>ok</p>")
    assert "<style" not in out
    assert "color:red" not in out
    assert "<p>ok</p>" in out


def test_sanitize_unwraps_disallowed_tags_keeping_text():
    out = sanitize_answer_html('<div class="x"><b>bold</b> and <a href="javascript:evil()">link</a></div>')
    assert "<div" not in out
    assert "<a " not in out and "<a>" not in out
    assert "<b>bold</b>" in out
    assert "link" in out


def test_sanitize_strips_all_attributes_from_kept_tags():
    out = sanitize_answer_html('<p onclick="evil()" style="color:red">hi</p>')
    assert "onclick" not in out
    assert "style" not in out
    assert out == "<p>hi</p>"


def test_sanitize_keeps_allowed_table_structure():
    out = sanitize_answer_html("<table><tr><td>1</td></tr></table>")
    assert out == "<table><tr><td>1</td></tr></table>"


def test_sanitize_handles_malformed_input_without_crashing():
    out = sanitize_answer_html("<p>unterminated <b>bold and <script>evil")
    assert "<script" not in out
    assert "unterminated" in out


def test_sanitize_empty_and_none_like_input():
    assert sanitize_answer_html("") == ""


# ---------------------------------------------------------------------------
# db.py: answer column, kind filters, get_game_questions
# ---------------------------------------------------------------------------

def _register_game(slug="game-one", game_id="a" * 32):
    db.register_web_game(
        game_id=game_id, slug=slug, title="Game One", description="d",
        requested_by="web:x", status="success", attempts=1,
    )
    return game_id


def test_update_generation_request_answer_round_trips(isolated_db):
    game_id = _register_game()
    job_id = "b" * 32
    db.create_generation_request(
        job_id=job_id, kind="ask", prompt="what does the boss do?",
        source_game_id=game_id, requested_by="web:x",
    )
    db.update_generation_request(job_id, status="success", answer="<p>It stomps.</p>")
    job = db.get_generation_request(job_id)
    assert job["answer"] == "<p>It stomps.</p>"


def test_count_and_get_generation_requests_kind_filter(isolated_db):
    game_id = _register_game()
    db.create_generation_request(
        job_id="c" * 32, kind="create", prompt="a maze game", requested_by="web:x")
    db.create_generation_request(
        job_id="d" * 32, kind="ask", prompt="q1", source_game_id=game_id, requested_by="web:x")
    db.create_generation_request(
        job_id="e" * 32, kind="ask", prompt="q2", source_game_id=game_id, requested_by="web:x")

    assert db.count_generation_requests() == 3
    assert db.count_generation_requests(kind="ask") == 2
    assert db.count_generation_requests(kind="create") == 1

    ask_rows = db.get_generation_history(kind="ask")
    assert {r["kind"] for r in ask_rows} == {"ask"}
    assert len(ask_rows) == 2
    all_rows = db.get_generation_history()
    assert len(all_rows) == 3


def test_count_recent_and_active_generation_requests_kind_filter(isolated_db):
    game_id = _register_game()
    since_iso = db.seconds_ago_iso(3600)
    db.create_generation_request(
        job_id="c" * 32, kind="create", prompt="x", requested_by="web:x", creator_uid="u1")
    db.create_generation_request(
        job_id="d" * 32, kind="ask", prompt="q", source_game_id=game_id,
        requested_by="web:x", creator_uid="u1")

    assert db.count_recent_generation_requests("u1", "1.2.3.4", since_iso) == 2
    assert db.count_recent_generation_requests("u1", "1.2.3.4", since_iso, kind="ask") == 1
    assert db.count_active_generation_requests() == 2
    assert db.count_active_generation_requests(kind="ask") == 1


def test_get_game_questions_only_successful_answered_rows_newest_first(isolated_db):
    game_id = _register_game()
    db.create_generation_request(
        job_id="1" * 32, kind="ask", prompt="q-old", source_game_id=game_id, requested_by="web:x")
    db.update_generation_request("1" * 32, status="success", answer="<p>old answer</p>")
    db.create_generation_request(
        job_id="2" * 32, kind="ask", prompt="q-failed", source_game_id=game_id, requested_by="web:x")
    db.update_generation_request("2" * 32, status="failed", error="boom")
    db.create_generation_request(
        job_id="3" * 32, kind="ask", prompt="q-new", source_game_id=game_id, requested_by="web:x")
    db.update_generation_request("3" * 32, status="success", answer="<p>new answer</p>")

    rows = db.get_game_questions(game_id)
    assert [r["question"] for r in rows] == ["q-new", "q-old"]
    assert rows[0]["answer"] == "<p>new answer</p>"


# ---------------------------------------------------------------------------
# ai_qa.answer_question
# ---------------------------------------------------------------------------

def _write_game(games_dir, slug, html="<html>ok</html>"):
    d = games_dir / slug
    d.mkdir()
    (d / "index.html").write_text(html, encoding="utf-8")


def test_answer_question_success_sanitizes_and_shapes_result(isolated_db, games_dir):
    game_id = _register_game(slug="game-one")
    _write_game(games_dir, "game-one")
    ask_result = ai.AskResult(
        text="<p>The boss deals <b>50</b> damage.</p><script>evil()</script>",
        input_tokens=100, output_tokens=20, model="deepseek-v4-flash",
        effort="high", raw_response={"id": "x"}, cached_tokens=5,
    )
    with mock.patch.object(ai, "ask", return_value=ask_result) as mocked:
        result = ai_qa.answer_question(
            game_id, "how much damage does the boss do?", "web:x", CONFIG,
            games_dir=games_dir, job_id="j" * 32,
        )

    mocked.assert_called_once()
    assert result["success"] is True
    assert result["game_id"] is None
    assert "<script" not in result["answer"]
    assert "<b>50</b>" in result["answer"]
    assert result["input_tokens"] == 100
    assert result["output_tokens"] == 20
    assert result["tokens_used"] == 120
    assert result["cached_tokens"] == 5
    assert result["error"] is None


def test_answer_question_ai_error_returns_failed_shape(isolated_db, games_dir):
    game_id = _register_game(slug="game-one")
    _write_game(games_dir, "game-one")
    with mock.patch.object(ai, "ask", side_effect=ai.AIError("timed out")):
        result = ai_qa.answer_question(
            game_id, "a question", "web:x", CONFIG, games_dir=games_dir, job_id="j" * 32,
        )

    assert result["success"] is False
    assert result["game_id"] is None
    assert result["answer"] is None
    assert "timed out" in result["error"]


def test_answer_question_missing_game_returns_failed_shape(isolated_db, games_dir):
    result = ai_qa.answer_question(
        "f" * 32, "a question", "web:x", CONFIG, games_dir=games_dir, job_id="j" * 32,
    )
    assert result["success"] is False
    assert "no game" in result["error"]


def test_answer_question_multi_file_game_uses_builder(isolated_db, games_dir):
    """A multi-file game's combined source (not the on-disk built
    index.html directly) is what gets sent to the model — confirmed via
    builder.build_game being called rather than reading src/index.html raw."""
    game_id = _register_game(slug="game-multi")
    game_dir = games_dir / "game-multi"
    game_dir.mkdir()
    (game_dir / "meta.json").write_text(json.dumps({"format": "multi-file"}), encoding="utf-8")
    src = game_dir / "src"
    src.mkdir()
    (src / "index.html").write_text("<html>shell</html>", encoding="utf-8")
    (game_dir / "index.html").write_text("<html>built</html>", encoding="utf-8")

    ask_result = ai.AskResult(
        text="<p>ok</p>", input_tokens=1, output_tokens=1,
        model="deepseek-v4-flash", effort="high", raw_response={},
    )
    with mock.patch("builder.build_game", return_value="<html>combined</html>") as mocked_build, \
         mock.patch.object(ai, "ask", return_value=ask_result) as mocked_ask:
        result = ai_qa.answer_question(
            game_id, "q", "web:x", CONFIG, games_dir=games_dir, job_id="j" * 32,
        )

    mocked_build.assert_called_once()
    assert result["success"] is True
    sent_prompt = mocked_ask.call_args.args[0]
    assert "combined" in sent_prompt


# ---------------------------------------------------------------------------
# job_runner._run_job dispatches kind='ask'
# ---------------------------------------------------------------------------

def test_run_job_dispatches_ask_and_persists_answer_on_success(isolated_db, games_dir):
    game_id = _register_game(slug="game-one")
    _write_game(games_dir, "game-one")
    job_id = "j" * 32
    db.create_generation_request(
        job_id=job_id, kind="ask", prompt="q", source_game_id=game_id, requested_by="web:x")
    conn = db.get_connection()
    job = db.get_generation_request(job_id, conn=conn)

    fake_result = {
        "success": True, "game_id": None, "answer": "<p>ok</p>", "attempts": 1,
        "model": "deepseek-v4-flash", "effort": "high", "duration_seconds": 0.1,
        "input_tokens": 5, "output_tokens": 2, "tokens_used": 7, "cached_tokens": 0,
        "error": None,
    }
    with mock.patch.object(ai_qa, "answer_question", return_value=fake_result) as mocked:
        job_runner._run_job(conn, job, CONFIG, games_dir)

    mocked.assert_called_once()
    updated = db.get_generation_request(job_id, conn=conn)
    assert updated["status"] == "success"
    assert updated["answer"] == "<p>ok</p>"
    assert updated["result_game_id"] is None


def test_run_job_dispatches_ask_and_persists_error_on_failure(isolated_db, games_dir):
    game_id = _register_game(slug="game-one")
    _write_game(games_dir, "game-one")
    job_id = "j" * 32
    db.create_generation_request(
        job_id=job_id, kind="ask", prompt="q", source_game_id=game_id, requested_by="web:x")
    conn = db.get_connection()
    job = db.get_generation_request(job_id, conn=conn)

    fake_result = {
        "success": False, "game_id": None, "answer": None, "attempts": 1,
        "model": None, "effort": "high", "duration_seconds": 0.1,
        "input_tokens": None, "output_tokens": None, "tokens_used": None,
        "cached_tokens": None, "error": "DeepSeek timed out",
    }
    with mock.patch.object(ai_qa, "answer_question", return_value=fake_result):
        job_runner._run_job(conn, job, CONFIG, games_dir)

    updated = db.get_generation_request(job_id, conn=conn)
    assert updated["status"] == "failed"
    assert updated["error"] == "DeepSeek timed out"
    assert updated["answer"] is None


# ---------------------------------------------------------------------------
# app.py routes
# ---------------------------------------------------------------------------

def make_client(games_dir, monkeypatch, admin_token="secret-token",
                 ask_rate_limit=None):
    monkeypatch.setenv("ADMIN_TOKEN", admin_token)
    if ask_rate_limit is not None:
        monkeypatch.setattr(app_module, "_ASK_RATE_LIMIT", ask_rate_limit)
    flask_app = app_module.create_app(games_dir=games_dir)
    flask_app.config["TESTING"] = True
    return flask_app.test_client()


def test_ask_game_enqueues_job_and_returns_job_id(isolated_db, games_dir, monkeypatch):
    game_id = _register_game(slug="game-one")
    _write_game(games_dir, "game-one")
    client = make_client(games_dir, monkeypatch)
    resp = client.post(f"/api/games/{game_id}/ask", json={"question": "how do I win?"})
    assert resp.status_code == 202
    body = resp.get_json()
    assert body["ok"] is True
    job = db.get_generation_request(body["job_id"])
    assert job["kind"] == "ask"
    assert job["prompt"] == "how do I win?"
    assert job["source_game_id"] == game_id


def test_ask_game_rejects_empty_question(isolated_db, games_dir, monkeypatch):
    game_id = _register_game(slug="game-one")
    _write_game(games_dir, "game-one")
    client = make_client(games_dir, monkeypatch)
    resp = client.post(f"/api/games/{game_id}/ask", json={"question": "   "})
    assert resp.status_code == 400


def test_ask_game_404_for_unknown_game(isolated_db, games_dir, monkeypatch):
    client = make_client(games_dir, monkeypatch)
    resp = client.post(f"/api/games/{'f' * 32}/ask", json={"question": "hi"})
    assert resp.status_code == 404


def test_ask_game_disabled_when_ai_generation_off(isolated_db, games_dir, monkeypatch):
    game_id = _register_game(slug="game-one")
    _write_game(games_dir, "game-one")
    db.set_ai_generation_enabled(False)
    client = make_client(games_dir, monkeypatch)
    resp = client.post(f"/api/games/{game_id}/ask", json={"question": "hi"})
    assert resp.status_code == 503


def test_ask_game_rate_limited(isolated_db, games_dir, monkeypatch):
    game_id = _register_game(slug="game-one")
    _write_game(games_dir, "game-one")
    client = make_client(
        games_dir, monkeypatch,
        ask_rate_limit={"max_requests": 1, "window_seconds": 3600, "max_queue_size": 100},
    )
    r1 = client.post(f"/api/games/{game_id}/ask", json={"question": "q1"})
    assert r1.status_code == 202
    r2 = client.post(f"/api/games/{game_id}/ask", json={"question": "q2"})
    assert r2.status_code == 429


def test_api_status_includes_answer_field_for_ask_job(isolated_db, games_dir, monkeypatch):
    game_id = _register_game(slug="game-one")
    _write_game(games_dir, "game-one")
    job_id = "j" * 32
    db.create_generation_request(
        job_id=job_id, kind="ask", prompt="q", source_game_id=game_id, requested_by="web:x")
    db.update_generation_request(job_id, status="success", answer="<p>the answer</p>")

    client = make_client(games_dir, monkeypatch)
    resp = client.get(f"/api/status/{job_id}")
    assert resp.status_code == 200
    assert resp.get_json()["answer"] == "<p>the answer</p>"


def test_game_info_includes_ai_questions_history(isolated_db, games_dir, monkeypatch):
    game_id = _register_game(slug="game-one")
    d = games_dir / "game-one"
    d.mkdir()
    (d / "index.html").write_text("<html>ok</html>", encoding="utf-8")
    (d / "meta.json").write_text(
        json.dumps({"title": "Game One", "game_id": game_id}), encoding="utf-8")
    db.create_generation_request(
        job_id="1" * 32, kind="ask", prompt="what's the win condition?",
        source_game_id=game_id, requested_by="web:x")
    db.update_generation_request("1" * 32, status="success", answer="<p>collect 10 gems</p>")

    client = make_client(games_dir, monkeypatch)
    resp = client.get(f"/api/games/{game_id}/info")
    assert resp.status_code == 200
    questions = resp.get_json()["ai_questions"]
    assert len(questions) == 1
    assert questions[0]["question"] == "what's the win condition?"
    assert questions[0]["answer"] == "<p>collect 10 gems</p>"


def test_admin_stats_ai_qa_tab_renders_rows(isolated_db, games_dir, monkeypatch):
    game_id = _register_game(slug="game-one")
    _write_game(games_dir, "game-one")
    db.create_generation_request(
        job_id="1" * 32, kind="ask", prompt="how many enemies are there?",
        source_game_id=game_id, requested_by="web:x")
    db.update_generation_request(
        "1" * 32, status="success", answer="<p>Three enemy types.</p>",
        model="deepseek-v4-flash", input_tokens=10, output_tokens=4, tokens_used=14)

    client = make_client(games_dir, monkeypatch)
    resp = client.get("/admin/stats?token=secret-token")
    assert resp.status_code == 200
    assert b"AI Q&amp;A" in resp.data
    assert b"how many enemies are there?" in resp.data
    assert b"Three enemy types." in resp.data


def test_admin_stats_ai_qa_tab_empty_state(isolated_db, games_dir, monkeypatch):
    client = make_client(games_dir, monkeypatch)
    resp = client.get("/admin/stats?token=secret-token")
    assert resp.status_code == 200
    assert b"No questions asked yet." in resp.data
