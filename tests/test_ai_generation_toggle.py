"""Admin kill switch for AI generation: /api/ai-status, the admin toggle
route, and enforcement on the /games/new and /games/<id>/enhance forms."""

import json

import app as app_module
import db


def make_client(games_dir, monkeypatch, admin_token="secret-token"):
    monkeypatch.setenv("ADMIN_TOKEN", admin_token)
    flask_app = app_module.create_app(games_dir=games_dir)
    flask_app.config["TESTING"] = True
    return flask_app.test_client()


def write_game(games_dir, slug, meta):
    d = games_dir / slug
    d.mkdir()
    (d / "index.html").write_text("<canvas></canvas>", encoding="utf-8")
    (d / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return d


def _lock_token(resp_data):
    import re
    m = re.search(rb'data-lock-token="([a-f0-9]+)"', resp_data)
    assert m, "enhance form must render a lock_token field"
    return m.group(1).decode()


# ---------------------------------------------------------------------------
# db.py helpers
# ---------------------------------------------------------------------------

def test_is_ai_generation_enabled_defaults_true(isolated_db):
    assert db.is_ai_generation_enabled() is True


def test_set_ai_generation_enabled_roundtrip(isolated_db):
    db.set_ai_generation_enabled(False)
    assert db.is_ai_generation_enabled() is False
    db.set_ai_generation_enabled(True)
    assert db.is_ai_generation_enabled() is True


# ---------------------------------------------------------------------------
# /api/ai-status
# ---------------------------------------------------------------------------

def test_api_ai_status_default_enabled(isolated_db, games_dir, monkeypatch):
    client = make_client(games_dir, monkeypatch)
    resp = client.get("/api/ai-status")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ai_generation_enabled"] is True
    assert body["ai_provider"] == "deepseek"


def test_api_ai_status_reflects_toggle(isolated_db, games_dir, monkeypatch):
    db.set_ai_generation_enabled(False)
    client = make_client(games_dir, monkeypatch)
    resp = client.get("/api/ai-status")
    body = resp.get_json()
    assert body["ai_generation_enabled"] is False
    assert body["ai_provider"] == "deepseek"


# ---------------------------------------------------------------------------
# new_game form + submit
# ---------------------------------------------------------------------------

def test_new_game_form_shows_disabled_message(isolated_db, games_dir, monkeypatch):
    db.set_ai_generation_enabled(False)
    client = make_client(games_dir, monkeypatch)
    resp = client.get("/games/new")
    assert resp.status_code == 200
    assert b"currently disabled" in resp.data


def test_new_game_submit_blocked_when_disabled(isolated_db, games_dir, monkeypatch):
    db.set_ai_generation_enabled(False)
    client = make_client(games_dir, monkeypatch)
    resp = client.post("/games/new", data={"prompt": "a snake game"})
    assert resp.status_code == 503
    assert b"currently disabled" in resp.data
    assert db.count_generation_requests() == 0


def test_new_game_submit_allowed_when_enabled(isolated_db, games_dir, monkeypatch):
    client = make_client(games_dir, monkeypatch)
    resp = client.post("/games/new", data={"prompt": "a snake game"})
    assert resp.status_code == 302
    assert db.count_generation_requests() == 1


# ---------------------------------------------------------------------------
# enhance form + submit
# ---------------------------------------------------------------------------

def test_enhance_form_shows_disabled_message(isolated_db, games_dir, monkeypatch):
    write_game(games_dir, "game-one", {"title": "Game One", "game_id": "a" * 32})
    db.sync_games_from_disk(games_dir)
    db.set_ai_generation_enabled(False)
    client = make_client(games_dir, monkeypatch)
    resp = client.get(f"/games/{'a' * 32}/enhance")
    assert resp.status_code == 200
    assert b"currently disabled" in resp.data


def test_enhance_submit_blocked_when_disabled(isolated_db, games_dir, monkeypatch):
    write_game(games_dir, "game-one", {"title": "Game One", "game_id": "a" * 32})
    db.sync_games_from_disk(games_dir)
    client = make_client(games_dir, monkeypatch)

    lock_resp = client.get(f"/games/{'a' * 32}/enhance")
    assert lock_resp.status_code == 200

    db.set_ai_generation_enabled(False)
    resp = client.post(
        f"/games/{'a' * 32}/enhance",
        data={"description": "add more levels", "lock_token": _lock_token(lock_resp.data)},
    )
    assert resp.status_code == 503
    assert b"currently disabled" in resp.data
    assert db.count_generation_requests() == 0


# ---------------------------------------------------------------------------
# admin toggle route
# ---------------------------------------------------------------------------

def test_admin_toggle_requires_token(isolated_db, games_dir, monkeypatch):
    client = make_client(games_dir, monkeypatch)
    resp = client.post("/admin/ai-generation-enabled", data={"enabled": "0"})
    assert resp.status_code == 403
    assert db.is_ai_generation_enabled() is True


def test_admin_toggle_flips_setting(isolated_db, games_dir, monkeypatch):
    client = make_client(games_dir, monkeypatch)
    resp = client.post(
        "/admin/ai-generation-enabled?token=secret-token", data={"enabled": "0"}
    )
    assert resp.status_code == 302
    assert db.is_ai_generation_enabled() is False

    resp = client.post(
        "/admin/ai-generation-enabled?token=secret-token", data={"enabled": "1"}
    )
    assert resp.status_code == 302
    assert db.is_ai_generation_enabled() is True


def test_admin_stats_reflects_current_toggle_state(isolated_db, games_dir, monkeypatch):
    db.set_ai_generation_enabled(False)
    client = make_client(games_dir, monkeypatch)
    resp = client.get("/admin/stats?token=secret-token")
    assert resp.status_code == 200
