"""Sprint 1 security hardening: CSP on served game HTML + baseline headers."""

import re

import app as app_module
import safety


def write_game(games_dir, slug, meta):
    d = games_dir / slug
    d.mkdir()
    (d / "index.html").write_text("<canvas></canvas>", encoding="utf-8")
    if meta is not None:
        import json
        (d / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return d


def make_client(games_dir):
    flask_app = app_module.create_app(games_dir=games_dir)
    flask_app.config["TESTING"] = True
    return flask_app.test_client()


def test_play_route_carries_game_csp(isolated_db, games_dir):
    write_game(games_dir, "block-dodge", {"title": "Block Dodge", "game_id": "a" * 32})
    client = make_client(games_dir)

    resp = client.get("/play/block-dodge")

    assert resp.status_code == 200
    csp = resp.headers.get("Content-Security-Policy", "")
    assert "form-action 'none'" in csp
    assert "connect-src 'self'" in csp
    assert "frame-ancestors 'self'" in csp


def test_menu_page_carries_frame_and_content_type_headers(isolated_db, games_dir):
    client = make_client(games_dir)

    resp = client.get("/")

    assert resp.headers.get("X-Frame-Options") == "DENY"
    assert resp.headers.get("X-Content-Type-Options") == "nosniff"


def test_play_route_does_not_carry_x_frame_options(isolated_db, games_dir):
    write_game(games_dir, "block-dodge", {"title": "Block Dodge", "game_id": "a" * 32})
    client = make_client(games_dir)

    resp = client.get("/play/block-dodge")

    assert "X-Frame-Options" not in resp.headers


def test_all_responses_carry_content_type_options_and_referrer_policy(isolated_db, games_dir):
    write_game(games_dir, "block-dodge", {"title": "Block Dodge", "game_id": "a" * 32})
    client = make_client(games_dir)

    for path in ("/", "/play/block-dodge", "/games/new"):
        resp = client.get(path)
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"
        assert resp.headers.get("Referrer-Policy") == "same-origin"


def test_csp_script_src_hosts_match_allowed_cdn_hosts_exactly(isolated_db, games_dir):
    write_game(games_dir, "block-dodge", {"title": "Block Dodge", "game_id": "a" * 32})
    client = make_client(games_dir)

    resp = client.get("/play/block-dodge")
    csp = resp.headers["Content-Security-Policy"]

    directives = dict(
        (d.strip().split(" ", 1)[0], d.strip())
        for d in csp.split(";") if d.strip()
    )
    script_src = directives["script-src"]
    hosts_in_csp = set(re.findall(r"https://([a-z0-9.\-]+)", script_src))

    assert hosts_in_csp == safety.ALLOWED_CDN_HOSTS
