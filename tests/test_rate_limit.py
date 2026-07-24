"""Sprint 3 Part B: rate limiting on /games/new and /games/<id>/enhance."""

import json

import app as app_module
import db


def make_client(games_dir, monkeypatch, max_requests=2, window_seconds=3600, max_queue_size=100):
    monkeypatch.setattr(
        app_module, "_RATE_LIMIT",
        {
            "max_requests": max_requests,
            "window_seconds": window_seconds,
            "max_queue_size": max_queue_size,
        },
    )
    flask_app = app_module.create_app(games_dir=games_dir)
    flask_app.config["TESTING"] = True
    return flask_app.test_client()


def write_game(games_dir, slug, meta):
    d = games_dir / slug
    d.mkdir()
    (d / "index.html").write_text("<canvas></canvas>", encoding="utf-8")
    (d / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return d


def test_new_game_submit_rejects_past_threshold(isolated_db, games_dir, monkeypatch):
    client = make_client(games_dir, monkeypatch, max_requests=2)

    r1 = client.post("/games/new", data={"prompt": "a snake game"})
    assert r1.status_code == 302
    r2 = client.post("/games/new", data={"prompt": "another snake game"})
    assert r2.status_code == 302
    r3 = client.post("/games/new", data={"prompt": "yet another game"})
    assert r3.status_code == 429
    assert b"too quickly" in r3.data

    assert db.count_generation_requests() == 2, "the rejected 3rd request must not be queued"


def test_new_game_submit_allows_requests_under_threshold(isolated_db, games_dir, monkeypatch):
    client = make_client(games_dir, monkeypatch, max_requests=5)

    for _ in range(5):
        resp = client.post("/games/new", data={"prompt": "a game"})
        assert resp.status_code == 302

    assert db.count_generation_requests() == 5


def _lock_token(resp_data):
    import re
    m = re.search(rb'data-lock-token="([a-f0-9]+)"', resp_data)
    assert m, "enhance form must render a lock_token field"
    return m.group(1).decode()


def test_enhance_submit_rejects_past_threshold(isolated_db, games_dir, monkeypatch):
    # Two distinct games — only one enhance job can be active per game, so a
    # second enhance of the *same* game would 409 regardless of rate limiting.
    # Using a different game for the 2nd request isolates the rate-limit check.
    write_game(games_dir, "game-one", {"title": "Game One", "game_id": "a" * 32})
    write_game(games_dir, "game-two", {"title": "Game Two", "game_id": "b" * 32})
    db.sync_games_from_disk(games_dir)
    client = make_client(games_dir, monkeypatch, max_requests=1)

    game_a, game_b = "a" * 32, "b" * 32

    lock_resp = client.get(f"/games/{game_a}/enhance")
    assert lock_resp.status_code == 200
    r1 = client.post(
        f"/games/{game_a}/enhance",
        data={"description": "add more levels", "lock_token": _lock_token(lock_resp.data)},
    )
    assert r1.status_code == 302

    lock_resp2 = client.get(f"/games/{game_b}/enhance")
    assert lock_resp2.status_code == 200
    r2 = client.post(
        f"/games/{game_b}/enhance",
        data={"description": "add even more levels", "lock_token": _lock_token(lock_resp2.data)},
    )
    assert r2.status_code == 429
    assert b"too quickly" in r2.data


# ---------------------------------------------------------------------------
# global queue cap (max_queue_size) — independent of the per-requester limit
# above: many different requesters, each under their own rate limit, must
# still not be able to pile up an unbounded backlog of queued jobs.
# ---------------------------------------------------------------------------

def test_new_game_submit_rejects_when_queue_is_full(isolated_db, games_dir, monkeypatch):
    # max_requests is generous so only the global queue cap can be responsible
    # for the rejection; each request uses a fresh client (no shared cookie
    # jar) so every submission comes from a distinct vg_uid.
    flask_app_kwargs = dict(max_requests=100, window_seconds=3600, max_queue_size=3)

    for i in range(3):
        client = make_client(games_dir, monkeypatch, **flask_app_kwargs)
        resp = client.post("/games/new", data={"prompt": f"game {i}"})
        assert resp.status_code == 302

    assert db.count_generation_requests() == 3

    client = make_client(games_dir, monkeypatch, **flask_app_kwargs)
    resp = client.post("/games/new", data={"prompt": "one game too many"})
    assert resp.status_code == 503
    assert b"queue is full" in resp.data
    assert db.count_generation_requests() == 3, "the rejected request must not be queued"


def test_new_game_submit_allows_new_request_once_queue_drains(isolated_db, games_dir, monkeypatch):
    kwargs = dict(max_requests=100, window_seconds=3600, max_queue_size=1)
    client_a = make_client(games_dir, monkeypatch, **kwargs)
    resp = client_a.post("/games/new", data={"prompt": "game a"})
    assert resp.status_code == 302

    client_b = make_client(games_dir, monkeypatch, **kwargs)
    resp = client_b.post("/games/new", data={"prompt": "game b"})
    assert resp.status_code == 503

    # Simulate the first job finishing (job_runner marks it success/failed,
    # taking it out of ('queued', 'generating')).
    conn = db.get_connection()
    conn.execute("UPDATE generation_requests SET status='success'")
    conn.commit()

    client_c = make_client(games_dir, monkeypatch, **kwargs)
    resp = client_c.post("/games/new", data={"prompt": "game c"})
    assert resp.status_code == 302
    assert db.count_generation_requests() == 2
