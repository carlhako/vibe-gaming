"""Admin provider toggle for /admin/stats — the platform-level switch between
DeepSeek and MiniMax. Mirrors tests/test_ai_generation_toggle.py's shape
exactly: defaults, round-trip, validation, route auth, route flip, JSON
status endpoint, and admin_stats render."""

import app as app_module
import db


def make_client(games_dir, monkeypatch, admin_token="secret-token"):
    monkeypatch.setenv("ADMIN_TOKEN", admin_token)
    flask_app = app_module.create_app(games_dir=games_dir)
    flask_app.config["TESTING"] = True
    return flask_app.test_client()


# ---------------------------------------------------------------------------
# db.py helpers
# ---------------------------------------------------------------------------

def test_get_ai_provider_defaults_deepseek(isolated_db):
    assert db.get_ai_provider() == "deepseek"


def test_set_ai_provider_rejects_unknown(isolated_db):
    import pytest
    with pytest.raises(ValueError):
        db.set_ai_provider("claude")
    # Setting an unknown value must NOT have stuck.
    assert db.get_ai_provider() == "deepseek"


def test_set_ai_provider_roundtrip(isolated_db):
    db.set_ai_provider("minimax")
    assert db.get_ai_provider() == "minimax"
    db.set_ai_provider("deepseek")
    assert db.get_ai_provider() == "deepseek"


# ---------------------------------------------------------------------------
# /api/ai-status
# ---------------------------------------------------------------------------

def test_api_ai_status_includes_provider_default(isolated_db, games_dir, monkeypatch):
    client = make_client(games_dir, monkeypatch)
    resp = client.get("/api/ai-status")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ai_generation_enabled"] is True
    assert body["ai_provider"] == "deepseek"


def test_api_ai_status_reflects_toggle(isolated_db, games_dir, monkeypatch):
    db.set_ai_provider("minimax")
    client = make_client(games_dir, monkeypatch)
    resp = client.get("/api/ai-status")
    assert resp.get_json()["ai_provider"] == "minimax"


# ---------------------------------------------------------------------------
# admin POST route
# ---------------------------------------------------------------------------

def test_admin_set_provider_requires_token(isolated_db, games_dir, monkeypatch):
    client = make_client(games_dir, monkeypatch)
    resp = client.post("/admin/ai-provider", data={"provider": "minimax"})
    assert resp.status_code == 403
    assert db.get_ai_provider() == "deepseek"


def test_admin_set_provider_flips_to_minimax(isolated_db, games_dir, monkeypatch):
    client = make_client(games_dir, monkeypatch)
    resp = client.post(
        "/admin/ai-provider?token=secret-token", data={"provider": "minimax"}
    )
    assert resp.status_code == 302
    assert db.get_ai_provider() == "minimax"


def test_admin_set_provider_flips_back_to_deepseek(isolated_db, games_dir, monkeypatch):
    db.set_ai_provider("minimax")
    client = make_client(games_dir, monkeypatch)
    resp = client.post(
        "/admin/ai-provider?token=secret-token", data={"provider": "deepseek"}
    )
    assert resp.status_code == 302
    assert db.get_ai_provider() == "deepseek"


def test_admin_set_provider_rejects_unknown(isolated_db, games_dir, monkeypatch):
    client = make_client(games_dir, monkeypatch)
    resp = client.post(
        "/admin/ai-provider?token=secret-token", data={"provider": "gpt-9000"}
    )
    assert resp.status_code == 400
    assert db.get_ai_provider() == "deepseek"


# ---------------------------------------------------------------------------
# admin_stats render
# ---------------------------------------------------------------------------

def test_admin_stats_renders_selected_provider_checked(isolated_db, games_dir, monkeypatch):
    db.set_ai_provider("minimax")
    client = make_client(games_dir, monkeypatch)
    resp = client.get("/admin/stats?token=secret-token")
    assert resp.status_code == 200
    body = resp.data.decode()
    # The minimax radio is selected.
    assert 'value="minimax" checked' in body
    assert 'value="deepseek" checked' not in body


def test_admin_stats_default_provider_is_deepseek(isolated_db, games_dir, monkeypatch):
    client = make_client(games_dir, monkeypatch)
    resp = client.get("/admin/stats?token=secret-token")
    body = resp.data.decode()
    assert 'value="deepseek" checked' in body
    assert 'value="minimax" checked' not in body
