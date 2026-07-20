"""Idea Forge: prompt_helper.expand_prompt(), job_runner's prompt_help
dispatch, and the Flask routes/status JSON that drive the feature."""

import json
from unittest import mock

import ai_client as ai
import app as app_module
import db
import job_runner
import prompt_helper as ph


CONFIG = {
    "game_web": {"host": "localhost", "port": 8600, "base_url": ""},
    "ideaforge": {"model": "", "effort": "low", "timeout_seconds": 5},
}


def _ask_result(text):
    return ai.AskResult(
        text=text, output_tokens=42, model="deepseek-v4-flash",
        effort="non-thinking", raw_response={},
    )


def _json_reply(expanded_prompt, detected_genre="fps", confidence="high"):
    return json.dumps({
        "detected_genre": detected_genre,
        "confidence": confidence,
        "expanded_prompt": expanded_prompt,
    })


# ---------------------------------------------------------------------------
# _parse_expansion_response
# ---------------------------------------------------------------------------

def test_parse_well_formed_json():
    raw = _json_reply("a fleshed out brief", detected_genre="racing")
    expanded, genre = ph._parse_expansion_response(raw)
    assert expanded == "a fleshed out brief"
    assert genre == "racing"


def test_parse_json_wrapped_in_code_fence():
    raw = "```json\n" + _json_reply("brief text", detected_genre="puzzle") + "\n```"
    expanded, genre = ph._parse_expansion_response(raw)
    assert expanded == "brief text"
    assert genre == "puzzle"


def test_parse_non_json_falls_back_to_raw_text():
    raw = "Sure! Here's your expanded game idea: a cool platformer with..."
    expanded, genre = ph._parse_expansion_response(raw)
    assert expanded == raw
    assert genre is None


def test_parse_json_with_null_genre():
    raw = _json_reply("brief", detected_genre=None)
    # json.dumps with detected_genre=None serializes to JSON null correctly.
    expanded, genre = ph._parse_expansion_response(raw)
    assert expanded == "brief"
    assert genre is None


# ---------------------------------------------------------------------------
# expand_prompt() — create mode
# ---------------------------------------------------------------------------

def test_expand_prompt_create_mode_success(isolated_db, games_dir):
    reply = _json_reply("A first-person shooter with lit, textured corridors...", "fps")
    with mock.patch.object(ai, "ask", return_value=_ask_result(reply)) as mock_ask:
        result = ph.expand_prompt("an fps", "web:a", CONFIG, games_dir=games_dir)

    assert result["success"] is True
    assert result["game_id"] is None
    assert result["detected_genre"] == "fps"
    assert "first-person shooter" in result["result_text"]
    assert result["tokens_used"] == 42

    # Genre checklist content made it into the system prompt.
    _, kwargs = mock_ask.call_args
    assert "fps" in kwargs["system_prompt"].lower()
    assert "crosshair" in kwargs["system_prompt"].lower()


def test_expand_prompt_ai_error(isolated_db, games_dir):
    with mock.patch.object(ai, "ask", side_effect=ai.AIError("boom")):
        result = ph.expand_prompt("an fps", "web:a", CONFIG, games_dir=games_dir)

    assert result["success"] is False
    assert result["error"] == "boom"
    assert result["detected_genre"] is None
    assert result["result_text"] is None


# ---------------------------------------------------------------------------
# expand_prompt() — enhance mode
# ---------------------------------------------------------------------------

def test_expand_prompt_enhance_mode_includes_existing_game(isolated_db, games_dir):
    slug = "maze-game-abc"
    game_dir = games_dir / slug
    game_dir.mkdir()
    (game_dir / "index.html").write_text(
        "<canvas></canvas><script>/* a maze game */</script>", encoding="utf-8"
    )
    game_id = "a" * 32
    db.register_web_game(
        game_id=game_id, slug=slug, title="Maze Runner", description="d",
        requested_by="web:a", status="success", attempts=1,
    )

    reply = _json_reply("Add a gun with lit walls, crosshair, viewmodel...", "fps")
    with mock.patch.object(ai, "ask", return_value=_ask_result(reply)) as mock_ask:
        result = ph.expand_prompt(
            "add a gun", "web:a", CONFIG, games_dir=games_dir, source_game_id=game_id,
        )

    assert result["success"] is True
    assert result["detected_genre"] == "fps"
    _, kwargs = mock_ask.call_args
    assert "Maze Runner" in kwargs["system_prompt"]
    assert "a maze game" in kwargs["system_prompt"]
    assert "fps" in kwargs["system_prompt"].lower()


def test_expand_prompt_bad_source_game_id_falls_back_to_create_framing(isolated_db, games_dir):
    reply = _json_reply("some brief", "puzzle")
    with mock.patch.object(ai, "ask", return_value=_ask_result(reply)) as mock_ask:
        result = ph.expand_prompt(
            "a rough idea", "web:a", CONFIG, games_dir=games_dir,
            source_game_id="nonexistent" * 3 + "aa",  # doesn't matter, just not found
        )

    assert result["success"] is True
    _, kwargs = mock_ask.call_args
    assert "Current game" not in kwargs["system_prompt"]


# ---------------------------------------------------------------------------
# job_runner._run_job dispatch for kind="prompt_help"
# ---------------------------------------------------------------------------

def test_run_job_prompt_help_success(isolated_db, games_dir):
    job_id = "job1"
    db.create_generation_request(
        job_id=job_id, kind="prompt_help", prompt="an fps", requested_by="web:a",
    )
    job = db.get_generation_request(job_id)
    conn = db.get_connection()

    fake_result = {
        "success": True, "game_id": None, "result_text": "expanded brief",
        "detected_genre": "fps", "attempts": 1, "tokens_used": 10,
        "model": "deepseek-v4-flash", "effort": "non-thinking",
        "duration_seconds": 0.1, "error": None,
    }
    with mock.patch.object(ph, "expand_prompt", return_value=fake_result):
        job_runner._run_job(conn, job, CONFIG, games_dir)

    row = db.get_generation_request(job_id)
    assert row["status"] == "success"
    assert row["result_text"] == "expanded brief"
    assert row["detected_genre"] == "fps"


def test_run_job_prompt_help_failure(isolated_db, games_dir):
    job_id = "job2"
    db.create_generation_request(
        job_id=job_id, kind="prompt_help", prompt="an fps", requested_by="web:a",
    )
    job = db.get_generation_request(job_id)
    conn = db.get_connection()

    fake_result = {
        "success": False, "game_id": None, "result_text": None,
        "detected_genre": None, "attempts": 1, "tokens_used": None,
        "model": "deepseek-v4-flash", "effort": "non-thinking",
        "duration_seconds": 0.1, "error": "boom",
    }
    with mock.patch.object(ph, "expand_prompt", return_value=fake_result):
        job_runner._run_job(conn, job, CONFIG, games_dir)

    row = db.get_generation_request(job_id)
    assert row["status"] == "failed"
    assert row["error"] == "boom"
    assert row["result_text"] is None


# ---------------------------------------------------------------------------
# Flask routes
# ---------------------------------------------------------------------------

def make_client(games_dir):
    flask_app = app_module.create_app(games_dir=games_dir)
    flask_app.config["TESTING"] = True
    return flask_app.test_client()


def test_idea_forge_new_form_renders(isolated_db, games_dir):
    client = make_client(games_dir)
    resp = client.get("/games/new/idea-forge")
    assert resp.status_code == 200


def test_idea_forge_new_form_prefill(isolated_db, games_dir):
    client = make_client(games_dir)
    resp = client.get("/games/new/idea-forge?prompt=a+rough+idea")
    assert resp.status_code == 200
    assert b"a rough idea" in resp.data


def test_idea_forge_new_submit_creates_prompt_help_job(isolated_db, games_dir):
    client = make_client(games_dir)
    resp = client.post("/games/new/idea-forge", data={"rough_prompt": "an fps"})
    assert resp.status_code == 302
    job_id = resp.headers["Location"].rsplit("/", 1)[-1]

    row = db.get_generation_request(job_id)
    assert row["kind"] == "prompt_help"
    assert row["source_game_id"] is None
    assert row["prompt"] == "an fps"


def test_idea_forge_new_submit_requires_prompt(isolated_db, games_dir):
    client = make_client(games_dir)
    resp = client.post("/games/new/idea-forge", data={"rough_prompt": ""})
    assert resp.status_code == 400


def test_idea_forge_enhance_form_404s_for_bad_game_id(isolated_db, games_dir):
    client = make_client(games_dir)
    assert client.get("/games/deadbeef/enhance/idea-forge").status_code == 404


def test_idea_forge_enhance_form_renders_for_existing_game(isolated_db, games_dir):
    game_id = "b" * 32
    slug = "some-game-bbb"
    (games_dir / slug).mkdir()
    (games_dir / slug / "index.html").write_text("<canvas></canvas>", encoding="utf-8")
    db.register_web_game(
        game_id=game_id, slug=slug, title="Some Game", description="d",
        requested_by="web:a", status="success", attempts=1,
    )
    client = make_client(games_dir)
    resp = client.get(f"/games/{game_id}/enhance/idea-forge")
    assert resp.status_code == 200


def test_idea_forge_enhance_submit_creates_job_with_source_game_id(isolated_db, games_dir):
    game_id = "c" * 32
    slug = "some-game-ccc"
    (games_dir / slug).mkdir()
    (games_dir / slug / "index.html").write_text("<canvas></canvas>", encoding="utf-8")
    db.register_web_game(
        game_id=game_id, slug=slug, title="Some Game", description="d",
        requested_by="web:a", status="success", attempts=1,
    )
    client = make_client(games_dir)
    resp = client.post(
        f"/games/{game_id}/enhance/idea-forge", data={"rough_prompt": "add a gun"}
    )
    assert resp.status_code == 302
    job_id = resp.headers["Location"].rsplit("/", 1)[-1]

    row = db.get_generation_request(job_id)
    assert row["kind"] == "prompt_help"
    assert row["source_game_id"] == game_id


def test_api_status_includes_prompt_help_fields(isolated_db, games_dir):
    client = make_client(games_dir)
    job_id = "statusjob1"
    db.create_generation_request(
        job_id=job_id, kind="prompt_help", prompt="an fps", requested_by="web:a",
        source_game_id=None,
    )
    db.update_generation_request(
        job_id, status="success", result_text="expanded brief", detected_genre="fps",
        attempts=1, model="deepseek-v4-flash", effort="non-thinking",
        duration_seconds=1.0, tokens_used=10,
    )

    resp = client.get(f"/api/status/{job_id}")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["result_text"] == "expanded brief"
    assert body["detected_genre"] == "fps"
    assert body["source_game_id"] is None
