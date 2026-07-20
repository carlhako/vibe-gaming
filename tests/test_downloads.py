"""Admin "download all games" zip and per-game offline HTML download."""

import io
import json
import zipfile

import app as app_module
import db


def write_game(games_dir, slug, meta):
    d = games_dir / slug
    d.mkdir()
    (d / "index.html").write_text("<canvas></canvas>", encoding="utf-8")
    if meta is not None:
        (d / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return d


def make_client(games_dir, monkeypatch, admin_token="secret-token"):
    monkeypatch.setenv("ADMIN_TOKEN", admin_token)
    flask_app = app_module.create_app(games_dir=games_dir)
    flask_app.config["TESTING"] = True
    return flask_app.test_client()


def test_admin_download_zips_every_game(isolated_db, games_dir, monkeypatch):
    write_game(games_dir, "block-dodge", {
        "title": "Block Dodge", "game_id": "a" * 32, "root_game_id": "a" * 32,
    })
    write_game(games_dir, "connect-4-4", {
        "title": "Connect 4", "game_id": "b" * 32, "root_game_id": "b" * 32,
    })

    client = make_client(games_dir, monkeypatch)
    resp = client.get("/admin/games/download?token=secret-token")

    assert resp.status_code == 200
    assert resp.mimetype == "application/zip"
    assert "attachment" in resp.headers["Content-Disposition"]

    zf = zipfile.ZipFile(io.BytesIO(resp.data))
    names = set(zf.namelist())
    assert "block-dodge/index.html" in names
    assert "block-dodge/meta.json" in names
    assert "connect-4-4/index.html" in names
    assert "connect-4-4/meta.json" in names


def test_admin_download_requires_valid_token(isolated_db, games_dir, monkeypatch):
    write_game(games_dir, "block-dodge", {"title": "Block Dodge", "game_id": "a" * 32})
    client = make_client(games_dir, monkeypatch)

    assert client.get("/admin/games/download").status_code == 403
    assert client.get("/admin/games/download?token=wrong").status_code == 403


def test_game_download_uses_title_and_version_in_filename(isolated_db, games_dir, monkeypatch):
    write_game(games_dir, "space-blaster", {
        "title": "Space Blaster", "game_id": "c" * 32, "root_game_id": "c" * 32,
        "version": 3,
    })
    db.sync_games_from_disk(games_dir)

    client = make_client(games_dir, monkeypatch)
    resp = client.get(f"/games/{'c' * 32}/download")

    assert resp.status_code == 200
    disposition = resp.headers["Content-Disposition"]
    assert "attachment" in disposition
    assert "space-blaster-v3.html" in disposition


def test_game_download_missing_id_404s(isolated_db, games_dir, monkeypatch):
    client = make_client(games_dir, monkeypatch)
    assert client.get(f"/games/{'f' * 32}/download").status_code == 404
