"""Sprint 1 security hardening: CSP on served game HTML + baseline headers."""

import re

import app as app_module
import engines
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


def test_csp_allows_the_vendored_three_js_tree_on_this_origin(isolated_db, games_dir):
    """A 3D game resolves `three` to /vendor/three/<version>/…. 'self' cannot
    be relied on there — the game runs in a sandbox with an opaque origin — so
    the allowance is an explicit origin with a path prefix."""
    write_game(games_dir, "block-dodge", {"title": "Block Dodge", "game_id": "a" * 32})
    client = make_client(games_dir)

    csp = client.get("/play/block-dodge").headers["Content-Security-Policy"]
    assert "http://localhost/vendor/three/" in csp


def test_vendor_route_serves_three_with_cors_and_immutable_cache(isolated_db, games_dir):
    """Module scripts always fetch with CORS and a sandboxed game sends
    Origin: null, so without ACAO every 3D game fails to load its engine."""
    client = make_client(games_dir)
    version = engines.DEFAULT_THREE_VERSION

    resp = client.get(f"/vendor/three/{version}/three.module.min.js")
    assert resp.status_code == 200
    assert resp.headers["Access-Control-Allow-Origin"] == "*"
    assert "immutable" in resp.headers["Cache-Control"]
    assert "javascript" in resp.headers["Content-Type"]


def test_vendor_route_rejects_an_unvendored_version(isolated_db, games_dir):
    client = make_client(games_dir)
    assert client.get("/vendor/three/9.9.9/three.module.min.js").status_code == 404
