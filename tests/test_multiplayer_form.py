"""New-game form multiplayer controls + /games/new threading — openspec change
add-multiplayer-games, tasks 4.3 and 4.4."""

import app as app_module
import db


def make_client(games_dir, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "secret-token")
    flask_app = app_module.create_app(games_dir=games_dir)
    flask_app.config["TESTING"] = True
    return flask_app.test_client()


# --------------------------------------------------------------------------
# 4.3  form controls + gating
# --------------------------------------------------------------------------

def test_form_renders_multiplayer_controls(isolated_db, games_dir, monkeypatch):
    client = make_client(games_dir, monkeypatch)
    html = client.get("/games/new").data.decode()
    assert 'name="multiplayer"' in html
    assert 'name="max_players"' in html
    assert "Shared room" in html


def test_multiplayer_controls_disabled_when_ai_disabled(isolated_db, games_dir, monkeypatch):
    db.set_ai_generation_enabled(False)
    client = make_client(games_dir, monkeypatch)
    html = client.get("/games/new").data.decode()
    # the fieldset wrapping the controls carries `disabled`, same as Engine
    frag = html[html.index("Multiplayer</legend>") - 200: html.index("Multiplayer</legend>")]
    assert "disabled" in frag


# --------------------------------------------------------------------------
# 4.4  route threads the values into the generation request
# --------------------------------------------------------------------------

def test_submit_with_multiplayer_writes_max_players(isolated_db, games_dir, monkeypatch):
    client = make_client(games_dir, monkeypatch)
    resp = client.post("/games/new", data={
        "prompt": "a co-op maze", "multiplayer": "1", "max_players": "4"})
    assert resp.status_code == 302
    conn = db.get_connection()
    row = conn.execute(
        "SELECT multiplayer_max_players FROM generation_requests").fetchone()
    assert row["multiplayer_max_players"] == 4


def test_submit_clamps_out_of_range_player_count(isolated_db, games_dir, monkeypatch):
    client = make_client(games_dir, monkeypatch)
    client.post("/games/new", data={
        "prompt": "x", "multiplayer": "1", "max_players": "999"})
    conn = db.get_connection()
    row = conn.execute(
        "SELECT multiplayer_max_players FROM generation_requests").fetchone()
    assert row["multiplayer_max_players"] == 8


def test_submit_without_multiplayer_leaves_column_null(isolated_db, games_dir, monkeypatch):
    client = make_client(games_dir, monkeypatch)
    client.post("/games/new", data={"prompt": "a solo game", "max_players": "4"})
    conn = db.get_connection()
    row = conn.execute(
        "SELECT multiplayer_max_players FROM generation_requests").fetchone()
    assert row["multiplayer_max_players"] is None


def test_submit_with_junk_player_count_falls_back_to_minimum(isolated_db, games_dir, monkeypatch):
    client = make_client(games_dir, monkeypatch)
    client.post("/games/new", data={
        "prompt": "x", "multiplayer": "1", "max_players": "abc"})
    conn = db.get_connection()
    row = conn.execute(
        "SELECT multiplayer_max_players FROM generation_requests").fetchone()
    assert row["multiplayer_max_players"] == 2
