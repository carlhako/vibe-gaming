"""Tests for db.py: schema creation, upsert semantics, race-safe job
claiming, and rating anti-abuse enforcement."""

import threading

import pytest

import db


def test_schema_creates_all_tables(isolated_db):
    conn = db.get_connection()
    tables = {
        r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert {"web_games", "generation_requests", "generation_attempts",
            "ratings", "access_log"} <= tables


def test_register_web_game_upsert_preserves_created_at(isolated_db):
    game_id = db.mint_game_id()
    db.register_web_game(
        game_id=game_id, slug="foo-12345678", title="Foo", description="d",
        requested_by="web:x", status="success", attempts=1,
    )
    first = db.get_web_game(game_id)
    assert first["title"] == "Foo"

    db.register_web_game(
        game_id=game_id, slug="foo-12345678", title="Foo Renamed", description="d2",
        requested_by="web:x", status="success", attempts=2, version=2,
    )
    second = db.get_web_game(game_id)
    assert second["title"] == "Foo Renamed"
    assert second["version"] == 2
    assert second["created_at"] == first["created_at"], "created_at must survive an upsert"
    assert second["updated_at"] >= first["updated_at"]


def test_register_web_game_root_defaults_to_self(isolated_db):
    game_id = db.mint_game_id()
    db.register_web_game(
        game_id=game_id, slug="bar-12345678", title="Bar", description="d",
        requested_by="web:x", status="success", attempts=1,
    )
    row = db.get_web_game(game_id)
    assert row["root_game_id"] == game_id
    assert row["parent_game_id"] is None


def test_claim_next_queued_request_race_safety(isolated_db):
    """Two threads racing to claim the same queued job: exactly one must
    win, the other must get None back — this is what makes the DB-polling
    job runner safe under multiple gunicorn worker processes."""
    job_id = "race-job-1"
    db.create_generation_request(
        job_id=job_id, kind="create", prompt="p", requested_by="web:x",
    )

    results = []
    barrier = threading.Barrier(2)

    def claim():
        conn = db.get_connection()
        barrier.wait()
        results.append(db.claim_next_queued_request(conn=conn))

    threads = [threading.Thread(target=claim) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    winners = [r for r in results if r == job_id]
    losers = [r for r in results if r is None]
    assert len(winners) == 1, results
    assert len(losers) == 1, results

    row = db.get_generation_request(job_id)
    assert row["status"] == "generating"


def test_claim_next_queued_request_one_at_a_time(isolated_db):
    """While one job is 'generating', a second queued job must not be
    claimable — this is what caps the whole system to one running job."""
    conn = db.get_connection()
    db.create_generation_request(job_id="j1", kind="create", prompt="p", requested_by="web:x")
    db.create_generation_request(job_id="j2", kind="create", prompt="p", requested_by="web:x")

    assert db.claim_next_queued_request(conn=conn) == "j1"
    assert db.get_generation_request("j1", conn=conn)["status"] == "generating"

    assert db.claim_next_queued_request(conn=conn) is None
    assert db.get_generation_request("j2", conn=conn)["status"] == "queued"

    db.update_generation_request("j1", status="success", conn=conn)
    assert db.claim_next_queued_request(conn=conn) == "j2"


def test_claim_next_queued_request_ignores_non_queued(isolated_db):
    db.create_generation_request(
        job_id="already-done", kind="create", prompt="p", requested_by="web:x",
    )
    db.update_generation_request("already-done", status="success")
    assert db.claim_next_queued_request() is None


def test_get_average_duration_empty(isolated_db):
    assert db.get_average_duration() is None
    assert db.get_average_duration(kind="create") is None


def test_get_average_duration_filters_by_kind_and_success(isolated_db):
    db.create_generation_request(job_id="c1", kind="create", prompt="p", requested_by="web:x")
    db.update_generation_request("c1", status="success", duration_seconds=100)
    db.create_generation_request(job_id="c2", kind="create", prompt="p", requested_by="web:x")
    db.update_generation_request("c2", status="success", duration_seconds=200)
    db.create_generation_request(job_id="e1", kind="enhance", prompt="p", requested_by="web:x")
    db.update_generation_request("e1", status="success", duration_seconds=50)
    db.create_generation_request(job_id="c3", kind="create", prompt="p", requested_by="web:x")
    db.update_generation_request("c3", status="failed", duration_seconds=999)

    assert db.get_average_duration(kind="create") == 150
    assert db.get_average_duration(kind="enhance") == 50
    assert db.get_average_duration(kind=None) == pytest.approx((100 + 200 + 50) / 3)


def test_get_queue_position(isolated_db):
    db.create_generation_request(job_id="q1", kind="create", prompt="p", requested_by="web:x")
    db.create_generation_request(job_id="q2", kind="create", prompt="p", requested_by="web:x")
    db.create_generation_request(job_id="q3", kind="create", prompt="p", requested_by="web:x")

    assert db.get_queue_position("q1") == 0
    assert db.get_queue_position("q2") == 1
    assert db.get_queue_position("q3") == 2

    db.claim_next_queued_request()
    assert db.get_queue_position("q2") == 0
    assert db.get_queue_position("q3") == 1


def test_count_generating(isolated_db):
    db.create_generation_request(job_id="g1", kind="create", prompt="p", requested_by="web:x")
    assert db.count_generating() == 0
    db.claim_next_queued_request()
    assert db.count_generating() == 1


def test_sweep_orphaned_requests(isolated_db):
    conn = db.get_connection()
    db.create_generation_request(job_id="j1", kind="create", prompt="p", requested_by="web:x")
    db.claim_next_queued_request(conn=conn)
    swept = db.sweep_orphaned_requests(conn=conn)
    assert swept == 1
    row = db.get_generation_request("j1", conn=conn)
    assert row["status"] == "failed"
    assert row["error"] == "interrupted by restart"


def _make_game(conn=None):
    game_id = db.mint_game_id()
    db.register_web_game(
        game_id=game_id, slug=f"g-{game_id[:8]}", title="G", description="d",
        requested_by="web:x", status="success", attempts=1, conn=conn,
    )
    return game_id


def test_record_rating_success_bumps_counter(isolated_db):
    game_id = _make_game()
    ok = db.record_rating(game_id, 1, client_uid="clientA", ip_address="1.1.1.1")
    assert ok is True
    row = db.get_web_game(game_id)
    assert row["thumbs_up"] == 1
    assert row["thumbs_down"] == 0


def test_record_rating_blocks_duplicate_cookie(isolated_db):
    game_id = _make_game()
    assert db.record_rating(game_id, 1, client_uid="clientA", ip_address="1.1.1.1") is True
    # Same cookie, different IP - still blocked.
    assert db.record_rating(game_id, 1, client_uid="clientA", ip_address="2.2.2.2") is False
    row = db.get_web_game(game_id)
    assert row["thumbs_up"] == 1, "a blocked duplicate must not double-count"


def test_record_rating_blocks_duplicate_ip(isolated_db):
    game_id = _make_game()
    assert db.record_rating(game_id, 1, client_uid="clientA", ip_address="1.1.1.1") is True
    # Different cookie, same IP - still blocked.
    assert db.record_rating(game_id, -1, client_uid="clientB", ip_address="1.1.1.1") is False
    row = db.get_web_game(game_id)
    assert row["thumbs_up"] == 1
    assert row["thumbs_down"] == 0


def test_record_rating_allows_different_client_and_ip(isolated_db):
    game_id = _make_game()
    assert db.record_rating(game_id, 1, client_uid="clientA", ip_address="1.1.1.1") is True
    assert db.record_rating(game_id, -1, client_uid="clientB", ip_address="2.2.2.2") is True
    row = db.get_web_game(game_id)
    assert row["thumbs_up"] == 1
    assert row["thumbs_down"] == 1


def test_count_by_root(isolated_db):
    root_id = _make_game()
    assert db.count_by_root(root_id) == 1
    fork_id = db.mint_game_id()
    db.register_web_game(
        game_id=fork_id, slug=f"g-{fork_id[:8]}", title="Fork", description="d",
        requested_by="web:x", status="success", attempts=1,
        parent_game_id=root_id, root_game_id=root_id,
    )
    assert db.count_by_root(root_id) == 2


def test_get_web_games_sort_rating(isolated_db):
    low = _make_game()
    high = _make_game()
    db.record_rating(low, 1, client_uid="c1", ip_address="1.1.1.1")
    db.record_rating(high, 1, client_uid="c2", ip_address="2.2.2.2")
    db.record_rating(high, 1, client_uid="c3", ip_address="3.3.3.3")
    ordered = db.get_web_games(sort="rating")
    ids_in_order = [g["game_id"] for g in ordered]
    assert ids_in_order.index(high) < ids_in_order.index(low)


def test_generation_history_pagination_and_join(isolated_db):
    db.create_generation_request(
        job_id="job-1", kind="create", prompt="a snake game", requested_by="web:aaa",
        creator_uid="uid-signed-up",
    )
    db.create_generation_request(
        job_id="job-2", kind="enhance", prompt="make it faster", requested_by="web:bbb",
        new_title="Snake II",
    )
    db.create_generation_request(
        job_id="job-3", kind="create", prompt="a pong game", requested_by="web:ccc",
    )

    game_id = db.mint_game_id()
    db.register_web_game(
        game_id=game_id, slug=f"snake-ii-{game_id[:8]}", title="Snake II",
        description="d", requested_by="web:bbb", status="success", attempts=1,
    )
    db.update_generation_request("job-2", status="success", result_game_id=game_id)
    db.update_generation_request("job-3", status="failed", error="boom")

    db.ensure_user("uid-signed-up")
    db.set_username("uid-signed-up", "carl")

    assert db.count_generation_requests() == 3

    rows = db.get_generation_history()
    assert [r["job_id"] for r in rows] == ["job-3", "job-2", "job-1"], "newest first"

    failed, success, first = rows
    assert failed["status"] == "failed"
    assert failed["result_title"] is None
    assert failed["result_slug"] is None
    assert success["kind"] == "enhance"
    assert success["result_title"] == "Snake II"
    assert success["result_slug"] == f"snake-ii-{game_id[:8]}"
    assert first["creator_username"] == "carl"

    page = db.get_generation_history(limit=2, offset=2)
    assert [r["job_id"] for r in page] == ["job-1"]


def test_play_history_joins_and_paginates(isolated_db):
    game_id = _make_game()
    db.ensure_user("player-uid")
    db.set_username("player-uid", "alice")

    db.record_play(game_id, client_uid="player-uid", ip_address="1.1.1.1")
    db.record_play(game_id, client_uid=None, ip_address="2.2.2.2")
    db.record_play("no-such-game", client_uid="anon-uid", ip_address="3.3.3.3")

    assert db.count_plays() == 3

    rows = db.get_play_history()
    assert [r["ip_address"] for r in rows] == ["3.3.3.3", "2.2.2.2", "1.1.1.1"], "newest first"

    orphan, anon, named = rows
    assert orphan["game_title"] is None, "play of an unregistered game_id joins to NULL"
    assert orphan["username"] is None
    assert anon["client_uid"] is None
    assert named["username"] == "alice"
    assert named["game_title"] is not None
    assert named["game_slug"] is not None

    page = db.get_play_history(limit=2, offset=2)
    assert [r["ip_address"] for r in page] == ["1.1.1.1"]


def test_get_user_leaderboard_sums_and_ranks(isolated_db):
    uid_high = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    uid_low = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    db.ensure_user(uid_high)
    db.ensure_user(uid_low)

    g1 = db.mint_game_id()
    db.register_web_game(
        game_id=g1, slug=f"g1-{g1[:8]}", title="G1", description="d",
        requested_by="web:x", status="success", attempts=1, creator_uid=uid_high,
    )
    g2 = db.mint_game_id()
    db.register_web_game(
        game_id=g2, slug=f"g2-{g2[:8]}", title="G2", description="d",
        requested_by="web:x", status="success", attempts=1, creator_uid=uid_high,
    )
    g3 = db.mint_game_id()
    db.register_web_game(
        game_id=g3, slug=f"g3-{g3[:8]}", title="G3", description="d",
        requested_by="web:x", status="success", attempts=1, creator_uid=uid_low,
    )
    db.record_rating(g1, 1, client_uid="c1", ip_address="1.1.1.1")
    db.record_rating(g2, 1, client_uid="c2", ip_address="2.2.2.2")
    db.record_rating(g3, 1, client_uid="c3", ip_address="3.3.3.3")

    board = db.get_user_leaderboard()
    by_uid = {r["uid"]: r for r in board}
    assert by_uid[uid_high]["total_likes"] == 2
    assert by_uid[uid_low]["total_likes"] == 1
    assert board[0]["uid"] == uid_high, "higher-liked user ranks first"


def test_get_user_leaderboard_includes_hidden_games_in_total(isolated_db):
    uid = "cccccccccccccccccccccccccccccccc"[:32]
    db.ensure_user(uid)
    game_id = db.mint_game_id()
    db.register_web_game(
        game_id=game_id, slug=f"hidden-{game_id[:8]}", title="Hidden", description="d",
        requested_by="web:x", status="success", attempts=1, creator_uid=uid,
    )
    db.record_rating(game_id, 1, client_uid="c1", ip_address="1.1.1.1")
    db.set_game_hidden(game_id, True)

    row = next(r for r in db.get_user_leaderboard() if r["uid"] == uid)
    assert row["total_likes"] == 1, "a hidden game's likes still count toward the total"


def test_get_user_leaderboard_includes_zero_game_users(isolated_db):
    uid = "dddddddddddddddddddddddddddddddd"[:32]
    db.ensure_user(uid)
    board = db.get_user_leaderboard()
    row = next(r for r in board if r["uid"] == uid)
    assert row["total_likes"] == 0
    assert row["game_count"] == 0
