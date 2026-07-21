"""Enhance-form locking: prevents two visitors from enhancing the same
game at once, at both the db layer (enhance_locks table) and the HTTP
layer (GET/POST /games/<id>/enhance + the ping/release endpoints)."""

import json
import threading

import app as app_module
import db


def make_client(games_dir):
    flask_app = app_module.create_app(games_dir=games_dir)
    flask_app.config["TESTING"] = True
    return flask_app.test_client()


def write_game(games_dir, slug, game_id, title="Game"):
    d = games_dir / slug
    d.mkdir()
    (d / "index.html").write_text("<canvas></canvas>", encoding="utf-8")
    (d / "meta.json").write_text(
        json.dumps({"title": title, "game_id": game_id, "root_game_id": game_id}),
        encoding="utf-8",
    )
    db.register_web_game(
        game_id=game_id, slug=slug, title=title, description="d",
        requested_by="web:x", status="success", attempts=1,
    )


# ---------------------------------------------------------------------------
# db.py
# ---------------------------------------------------------------------------

def test_acquire_enhance_lock_blocks_other_uid(isolated_db):
    game_id = "a" * 32
    won_a, lock_a = db.acquire_enhance_lock(game_id, "uid-a")
    assert won_a is True

    won_b, lock_b = db.acquire_enhance_lock(game_id, "uid-b")
    assert won_b is False
    assert lock_b["locked_by_uid"] == "uid-a"
    assert lock_b["lock_token"] == lock_a["lock_token"]


def test_acquire_enhance_lock_same_uid_renews(isolated_db):
    game_id = "a" * 32
    won_1, lock_1 = db.acquire_enhance_lock(game_id, "uid-a")
    won_2, lock_2 = db.acquire_enhance_lock(game_id, "uid-a")
    assert won_1 is True and won_2 is True
    assert lock_2["lock_token"] != lock_1["lock_token"]


def test_acquire_enhance_lock_race_safety(isolated_db):
    """Two threads racing to acquire the same fresh lock: exactly one
    must win, mirroring test_claim_next_queued_request_race_safety's
    approach for the job-claim queue."""
    game_id = "a" * 32
    db.get_connection()  # prime the schema on this fresh tmp DB before the
    # threads race — two cold sqlite3.connect()+"PRAGMA journal_mode=WAL"
    # calls hitting a brand-new file at once can otherwise collide (a
    # pre-existing quirk of get_connection(), not specific to this lock).
    results = []
    barrier = threading.Barrier(2)

    def acquire(uid):
        conn = db.get_connection()
        barrier.wait()
        results.append(db.acquire_enhance_lock(game_id, uid, conn=conn))

    threads = [
        threading.Thread(target=acquire, args=("uid-a",)),
        threading.Thread(target=acquire, args=("uid-b",)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    winners = [r for r in results if r[0] is True]
    assert len(winners) == 1, results


def test_heartbeat_keeps_lock_alive_and_rejects_wrong_token(isolated_db):
    game_id = "a" * 32
    _, lock = db.acquire_enhance_lock(game_id, "uid-a")
    assert db.heartbeat_enhance_lock(game_id, lock["lock_token"]) is not None
    assert db.heartbeat_enhance_lock(game_id, "not-the-token") is None


def test_heartbeat_extends_expiry_past_the_original_deadline(isolated_db):
    game_id = "a" * 32
    _, lock = db.acquire_enhance_lock(game_id, "uid-a")
    original_expiry = lock["expires_at"]
    new_expiry = db.heartbeat_enhance_lock(game_id, lock["lock_token"])
    assert new_expiry is not None
    assert new_expiry >= original_expiry


def test_release_enhance_lock_frees_it_for_others(isolated_db):
    game_id = "a" * 32
    _, lock = db.acquire_enhance_lock(game_id, "uid-a")
    assert db.release_enhance_lock(game_id, lock["lock_token"]) is True

    won_b, _ = db.acquire_enhance_lock(game_id, "uid-b")
    assert won_b is True


def test_get_active_enhance_lock_lazily_clears_idle_lock(isolated_db):
    game_id = "a" * 32
    conn = db.get_connection()
    now = db.now_iso()
    stale = db._iso_add_seconds(now, -(db.ENHANCE_LOCK_IDLE_TIMEOUT_SECONDS + 5))
    conn.execute(
        "INSERT INTO enhance_locks (game_id, locked_by_uid, lock_token, acquired_at, "
        "last_ping_at, expires_at) VALUES (?, 'uid-a', 'tok', ?, ?, ?)",
        (game_id, stale, stale, db._iso_add_seconds(now, 500)),
    )
    conn.commit()

    assert db.get_active_enhance_lock(game_id, conn=conn) is None
    # And a fresh acquire should now succeed for a different uid.
    won, _ = db.acquire_enhance_lock(game_id, "uid-b", conn=conn)
    assert won is True


def test_get_active_enhance_job_matches_only_queued_or_generating_enhance_jobs(isolated_db):
    game_id = "a" * 32
    assert db.get_active_enhance_job(game_id) is None

    db.create_generation_request(
        job_id="j1", kind="enhance", prompt="p", requested_by="web:x",
        source_game_id=game_id, creator_uid="uid-a",
    )
    job = db.get_active_enhance_job(game_id)
    assert job is not None and job["job_id"] == "j1"

    db.update_generation_request(job_id="j1", status="success")
    assert db.get_active_enhance_job(game_id) is None


# ---------------------------------------------------------------------------
# HTTP layer
# ---------------------------------------------------------------------------

def test_second_visitor_sees_locked_form(isolated_db, games_dir):
    game_id = "a" * 32
    write_game(games_dir, "game-a", game_id)
    user_a = make_client(games_dir)
    user_b = make_client(games_dir)

    resp_a = user_a.get(f"/games/{game_id}/enhance")
    assert resp_a.status_code == 200
    assert b'data-locked="false"' in resp_a.data
    assert b'data-held-by-me="true"' in resp_a.data

    resp_b = user_b.get(f"/games/{game_id}/enhance")
    assert resp_b.status_code == 200
    assert b'data-locked="true"' in resp_b.data
    assert b'data-held-by-me="false"' in resp_b.data


def test_second_visitor_post_is_rejected(isolated_db, games_dir):
    game_id = "a" * 32
    write_game(games_dir, "game-a", game_id)
    user_a = make_client(games_dir)
    user_b = make_client(games_dir)

    user_a.get(f"/games/{game_id}/enhance")
    user_b.get(f"/games/{game_id}/enhance")

    resp = user_b.post(f"/games/{game_id}/enhance", data={
        "description": "add lasers", "lock_token": "whatever-b-guesses",
    })
    assert resp.status_code == 409
    assert db.count_generation_requests() == 0


def test_holder_ping_then_submit_releases_lock_and_creates_job(isolated_db, games_dir):
    game_id = "a" * 32
    write_game(games_dir, "game-a", game_id)
    user_a = make_client(games_dir)

    get_resp = user_a.get(f"/games/{game_id}/enhance")
    token = get_resp.data.decode().split('data-lock-token="')[1].split('"')[0]

    ping_resp = user_a.post(f"/games/{game_id}/enhance/lock/ping", data={"lock_token": token})
    assert ping_resp.status_code == 200
    assert ping_resp.get_json()["ok"] is True

    post_resp = user_a.post(f"/games/{game_id}/enhance", data={
        "description": "add lasers", "lock_token": token,
    })
    assert post_resp.status_code == 302
    assert db.get_active_enhance_lock(game_id) is None

    # A generation_requests row now exists and blocks a fresh visitor even
    # though the phase-A lock is gone.
    user_b = make_client(games_dir)
    resp_b = user_b.get(f"/games/{game_id}/enhance")
    assert b'data-locked="true"' in resp_b.data
    assert b'data-lock-phase="job"' in resp_b.data


def test_expired_token_is_rejected_on_submit(isolated_db, games_dir):
    game_id = "a" * 32
    write_game(games_dir, "game-a", game_id)
    user_a = make_client(games_dir)
    user_a.get(f"/games/{game_id}/enhance")

    resp = user_a.post(f"/games/{game_id}/enhance", data={
        "description": "add lasers", "lock_token": "totally-wrong-token",
    })
    assert resp.status_code == 409
    assert db.count_generation_requests() == 0


def test_release_endpoint_frees_lock_for_next_visitor(isolated_db, games_dir):
    game_id = "a" * 32
    write_game(games_dir, "game-a", game_id)
    user_a = make_client(games_dir)
    user_b = make_client(games_dir)

    get_resp = user_a.get(f"/games/{game_id}/enhance")
    token = get_resp.data.decode().split('data-lock-token="')[1].split('"')[0]

    user_a.post(f"/games/{game_id}/enhance/lock/release", data={"lock_token": token})

    resp_b = user_b.get(f"/games/{game_id}/enhance")
    assert b'data-locked="false"' in resp_b.data
    assert b'data-held-by-me="true"' in resp_b.data
