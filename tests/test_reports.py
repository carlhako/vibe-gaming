"""Sprint 4 Part B/C: the `reports` table, player reporting endpoint, and
the /admin/reports review page."""

import json

import app as app_module
import db


def _register_game(slug="game-one", game_id="a" * 32):
    db.register_web_game(
        game_id=game_id, slug=slug, title="Game One", description="d",
        requested_by="web:x", status="success", attempts=1,
    )
    return game_id


# ---------------------------------------------------------------------------
# db.create_report — UNIQUE enforcement, same shape as ratings
# ---------------------------------------------------------------------------

def test_create_report_blocks_duplicate_reporter_uid(isolated_db):
    game_id = _register_game()
    assert db.create_report(game_id, reporter_uid="clientA", ip_address="1.1.1.1",
                             reason="r1") is True
    assert db.create_report(game_id, reporter_uid="clientA", ip_address="2.2.2.2",
                             reason="r2") is False


def test_create_report_blocks_duplicate_ip(isolated_db):
    game_id = _register_game()
    assert db.create_report(game_id, reporter_uid="clientA", ip_address="1.1.1.1",
                             reason="r1") is True
    assert db.create_report(game_id, reporter_uid="clientB", ip_address="1.1.1.1",
                             reason="r2") is False


def test_create_report_allows_different_reporter_and_ip(isolated_db):
    game_id = _register_game()
    assert db.create_report(game_id, reporter_uid="clientA", ip_address="1.1.1.1",
                             reason="r1") is True
    assert db.create_report(game_id, reporter_uid="clientB", ip_address="2.2.2.2",
                             reason="r2") is True


def test_moderation_reports_exempt_from_player_uniqueness(isolated_db):
    """reporter_uid=None (moderation) never collides with a real player's
    NULL-free UNIQUE, and repeated 'system' ip_address entries for
    *different* games must not collide with each other either."""
    game_a = _register_game(slug="game-a", game_id="a" * 32)
    game_b = _register_game(slug="game-b", game_id="b" * 32)
    assert db.create_report(game_a, reporter_uid=None, ip_address="system",
                             reason="phish", source="moderation") is True
    assert db.create_report(game_b, reporter_uid=None, ip_address="system",
                             reason="phish", source="moderation") is True
    # A real player report on game_a is unaffected by the moderation row.
    assert db.create_report(game_a, reporter_uid="clientA", ip_address="1.1.1.1",
                             reason="bad") is True


# ---------------------------------------------------------------------------
# db.get_open_reports / dismiss_reports
# ---------------------------------------------------------------------------

def test_get_open_reports_groups_and_counts(isolated_db):
    game_id = _register_game()
    db.create_report(game_id, reporter_uid="c1", ip_address="1.1.1.1", reason="r1")
    db.create_report(game_id, reporter_uid="c2", ip_address="2.2.2.2", reason="r2",
                      source="moderation")

    open_reports = db.get_open_reports()
    assert len(open_reports) == 1
    entry = open_reports[0]
    assert entry["game_id"] == game_id
    assert entry["title"] == "Game One"
    assert entry["report_count"] == 2
    assert {r["reason"] for r in entry["reports"]} == {"r1", "r2"}


def test_get_open_reports_excludes_dismissed(isolated_db):
    game_id = _register_game()
    db.create_report(game_id, reporter_uid="c1", ip_address="1.1.1.1", reason="r1")

    assert len(db.get_open_reports()) == 1
    dismissed = db.dismiss_reports(game_id)
    assert dismissed == 1
    assert db.get_open_reports() == []


def test_get_open_reports_orders_by_count_desc(isolated_db):
    game_a = _register_game(slug="game-a", game_id="a" * 32)
    game_b = _register_game(slug="game-b", game_id="b" * 32)
    db.create_report(game_a, reporter_uid="c1", ip_address="1.1.1.1", reason="r1")
    db.create_report(game_b, reporter_uid="c2", ip_address="2.2.2.2", reason="r2")
    db.create_report(game_b, reporter_uid="c3", ip_address="3.3.3.3", reason="r3")

    open_reports = db.get_open_reports()
    assert [r["game_id"] for r in open_reports] == [game_b, game_a]


# ---------------------------------------------------------------------------
# POST /api/games/<game_id>/report
# ---------------------------------------------------------------------------

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


def test_report_endpoint_200_then_409_same_session(isolated_db, games_dir, monkeypatch):
    write_game(games_dir, "game-one", {"title": "Game One", "game_id": "a" * 32})
    db.sync_games_from_disk(games_dir)
    client = make_client(games_dir, monkeypatch)
    game_id = "a" * 32

    r1 = client.post(f"/api/games/{game_id}/report", json={"reason": "bad game"})
    assert r1.status_code == 200
    assert r1.get_json()["ok"] is True

    r2 = client.post(f"/api/games/{game_id}/report", json={"reason": "still bad"})
    assert r2.status_code == 409
    assert r2.get_json()["ok"] is False
    assert r2.get_json()["reason"] == "already_reported"


def test_report_endpoint_different_ip_and_game_succeeds(isolated_db, games_dir, monkeypatch):
    write_game(games_dir, "game-one", {"title": "Game One", "game_id": "a" * 32})
    write_game(games_dir, "game-two", {"title": "Game Two", "game_id": "b" * 32})
    db.sync_games_from_disk(games_dir)
    client_a = make_client(games_dir, monkeypatch)
    client_b = make_client(games_dir, monkeypatch)

    r1 = client_a.post(f"/api/games/{'a' * 32}/report",
                        json={"reason": "bad"},
                        environ_overrides={"REMOTE_ADDR": "1.1.1.1"})
    assert r1.status_code == 200

    r2 = client_b.post(f"/api/games/{'b' * 32}/report",
                        json={"reason": "also bad"},
                        environ_overrides={"REMOTE_ADDR": "2.2.2.2"})
    assert r2.status_code == 200


def test_report_endpoint_unknown_game_404s(isolated_db, games_dir, monkeypatch):
    client = make_client(games_dir, monkeypatch)
    assert client.post(f"/api/games/{'f' * 32}/report", json={}).status_code == 404


# ---------------------------------------------------------------------------
# GET /admin/reports, POST /admin/reports/<game_id>/dismiss
# ---------------------------------------------------------------------------

def test_admin_reports_requires_valid_token(isolated_db, games_dir, monkeypatch):
    client = make_client(games_dir, monkeypatch)
    assert client.get("/admin/reports").status_code == 403
    assert client.get("/admin/reports?token=wrong").status_code == 403
    assert client.get("/admin/reports?token=secret-token").status_code == 200


def test_admin_reports_lists_flagged_game_and_dismiss_removes_it(isolated_db, games_dir, monkeypatch):
    write_game(games_dir, "game-one", {"title": "Game One", "game_id": "a" * 32})
    db.sync_games_from_disk(games_dir)
    game_id = "a" * 32
    db.create_report(game_id, reporter_uid="c1", ip_address="1.1.1.1", reason="phishy")

    client = make_client(games_dir, monkeypatch)
    resp = client.get("/admin/reports?token=secret-token")
    assert resp.status_code == 200
    assert b"Game One" in resp.data
    assert b"phishy" in resp.data

    dismiss_resp = client.post(
        f"/admin/reports/{game_id}/dismiss?token=secret-token",
    )
    assert dismiss_resp.status_code == 302

    resp2 = client.get("/admin/reports?token=secret-token")
    assert b"Game One" not in resp2.data
