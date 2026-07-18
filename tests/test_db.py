"""Tests for db.py: schema creation, upsert semantics, race-safe job
claiming, and rating anti-abuse enforcement."""

import threading

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


def test_claim_next_queued_request_ignores_non_queued(isolated_db):
    db.create_generation_request(
        job_id="already-done", kind="create", prompt="p", requested_by="web:x",
    )
    db.update_generation_request("already-done", status="success")
    assert db.claim_next_queued_request() is None


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
