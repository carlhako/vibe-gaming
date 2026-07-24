"""GET /admin/generation/<job_id>/attempts — the admin history page's
payload-browser API, letting the Prompt/JSON dialog page through every
retry attempt of a job instead of only the most recent one."""

import json

import app as app_module
import db


def make_client(games_dir, monkeypatch, admin_token="secret-token"):
    monkeypatch.setenv("ADMIN_TOKEN", admin_token)
    flask_app = app_module.create_app(games_dir=games_dir)
    flask_app.config["TESTING"] = True
    return flask_app.test_client()


def test_requires_valid_token(isolated_db, games_dir, monkeypatch):
    db.create_generation_request(
        job_id="a" * 32, kind="create", prompt="a maze game", requested_by="web:x",
    )
    client = make_client(games_dir, monkeypatch)
    assert client.get(f"/admin/generation/{'a' * 32}/attempts").status_code == 403
    assert client.get(f"/admin/generation/{'a' * 32}/attempts?token=wrong").status_code == 403


def test_404_for_bad_job_id_shape(isolated_db, games_dir, monkeypatch):
    client = make_client(games_dir, monkeypatch)
    resp = client.get("/admin/generation/not-a-job-id/attempts?token=secret-token")
    assert resp.status_code == 404


def test_404_for_unknown_job(isolated_db, games_dir, monkeypatch):
    client = make_client(games_dir, monkeypatch)
    resp = client.get(f"/admin/generation/{'f' * 32}/attempts?token=secret-token")
    assert resp.status_code == 404


def test_returns_prompt_and_every_attempt_in_order(isolated_db, games_dir, monkeypatch):
    job_id = "b" * 32
    db.create_generation_request(
        job_id=job_id, kind="create", prompt="a maze game", requested_by="web:x",
    )
    db.add_generation_attempt(job_id, 1, "ai_error", detail="timeout", duration_seconds=1.0)
    db.add_generation_attempt(
        job_id, 2, "success", input_tokens=11, tokens_used=13, duration_seconds=1.0,
        raw_response=json.dumps({"id": "resp-2"}),
    )

    client = make_client(games_dir, monkeypatch)
    resp = client.get(f"/admin/generation/{job_id}/attempts?token=secret-token")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["prompt"] == "a maze game"
    assert [a["attempt_number"] for a in data["attempts"]] == [1, 2]
    assert data["attempts"][0]["raw_response"] is None
    assert json.loads(data["attempts"][1]["raw_response"]) == {"id": "resp-2"}
