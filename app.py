"""
Flask app serving the AI-generated web games arcade.

Scans `games_dir` for subdirectories containing an `index.html` (+ optional
`meta.json`) and serves them behind a menu shell: a sidebar game list on the
left, a sandboxed iframe playing the selected game on the right.

The manifest is rebuilt whenever a game directory is added, removed, or its
meta.json changes (mtime-based cache key checked on every request), so games
written by game_generator.py / game_enhancer.py appear live with no restart
of this process.
"""

import json
import re
import threading
import uuid
from pathlib import Path

from flask import (
    Flask, abort, jsonify, redirect, render_template, request,
    send_from_directory, url_for,
)

import db

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,59}$")
_GAME_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_BASE_DIR = Path(__file__).parent
_VG_UID_COOKIE = "vg_uid"
_VG_UID_MAX_AGE = 31536000  # 1 year


def _build_manifest(games_dir: Path) -> list[dict]:
    games = []
    if not games_dir.exists():
        return games
    for entry in sorted(games_dir.iterdir()):
        if not entry.is_dir() or entry.name == "backups":
            continue
        if not (entry / "index.html").exists():
            continue
        meta = {}
        meta_path = entry / "meta.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                meta = {}
        games.append({
            "slug": entry.name,
            "game_id": meta.get("game_id"),
            "title": meta.get("title", entry.name),
            "description": meta.get("description", ""),
            "created_at": meta.get("created_at", ""),
            "version": meta.get("version", 1),
            "parent_game_id": meta.get("parent_game_id"),
        })

    titles_by_id = {g["game_id"]: g["title"] for g in games if g["game_id"]}
    for g in games:
        g["parent_title"] = titles_by_id.get(g["parent_game_id"])
    return games


def _manifest_cache_key(games_dir: Path) -> tuple:
    """Cheap fingerprint of games_dir's contents: changes whenever a game is
    added, removed, or its meta.json is rewritten (index.html rewrites alone
    don't bump this — that's fine, only the listing itself needs refreshing)."""
    if not games_dir.exists():
        return (None,)
    key = [games_dir.stat().st_mtime_ns]
    for entry in sorted(games_dir.iterdir()):
        if entry.is_dir() and entry.name != "backups":
            meta_path = entry / "meta.json"
            if meta_path.exists():
                key.append((entry.name, meta_path.stat().st_mtime_ns))
            else:
                key.append((entry.name, None))
    return tuple(key)


def create_app(games_dir=None) -> Flask:
    """Build the Flask app. `games_dir` defaults to ./games/."""
    games_dir = Path(games_dir) if games_dir is not None else _BASE_DIR / "games"

    app = Flask(
        __name__,
        template_folder=str(_BASE_DIR / "templates"),
        static_folder=str(_BASE_DIR / "static"),
    )

    cache_lock = threading.Lock()
    cache = {"key": None, "games": []}

    def get_games() -> list[dict]:
        with cache_lock:
            key = _manifest_cache_key(games_dir)
            if key != cache["key"]:
                cache["games"] = _build_manifest(games_dir)
                cache["key"] = key
            return cache["games"]

    @app.get("/")
    def index():
        return render_template("index.html", games=get_games())

    @app.get("/api/games")
    def api_games():
        return jsonify(get_games())

    @app.get("/play/<slug>")
    def play(slug):
        if not _SLUG_RE.match(slug):
            abort(404)
        game_dir = games_dir / slug
        if not (game_dir / "index.html").exists():
            abort(404)
        return send_from_directory(game_dir, "index.html")

    @app.get("/games/new")
    def new_game_form():
        return render_template("new_game.html")

    @app.post("/games/new")
    def new_game_submit():
        prompt = (request.form.get("prompt") or "").strip()
        if not prompt:
            return render_template(
                "new_game.html", error="Please describe the game you want."
            ), 400

        vg_uid = request.cookies.get(_VG_UID_COOKIE)
        set_cookie = vg_uid is None
        if vg_uid is None:
            vg_uid = uuid.uuid4().hex

        requested_by = "web:" + vg_uid[:12]
        job_id = uuid.uuid4().hex
        db.create_generation_request(
            job_id=job_id, kind="create", prompt=prompt, requested_by=requested_by,
        )

        resp = redirect(url_for("job_status_page", job_id=job_id))
        if set_cookie:
            resp.set_cookie(
                _VG_UID_COOKIE, vg_uid, max_age=_VG_UID_MAX_AGE,
                httponly=False, samesite="Lax",
            )
        return resp

    @app.get("/games/<game_id>/enhance")
    def enhance_game_form(game_id):
        if not _GAME_ID_RE.match(game_id):
            abort(404)
        game = db.get_web_game(game_id)
        if game is None:
            abort(404)
        return render_template("enhance.html", game=game)

    @app.post("/games/<game_id>/enhance")
    def enhance_game_submit(game_id):
        if not _GAME_ID_RE.match(game_id):
            abort(404)
        game = db.get_web_game(game_id)
        if game is None:
            abort(404)

        description = (request.form.get("description") or "").strip()
        new_title = (request.form.get("new_title") or "").strip() or None
        if not description:
            return render_template(
                "enhance.html", game=game, error="Please describe the change you want."
            ), 400

        vg_uid = request.cookies.get(_VG_UID_COOKIE)
        set_cookie = vg_uid is None
        if vg_uid is None:
            vg_uid = uuid.uuid4().hex

        requested_by = "web:" + vg_uid[:12]
        job_id = uuid.uuid4().hex
        db.create_generation_request(
            job_id=job_id, kind="enhance", prompt=description, requested_by=requested_by,
            source_game_id=game_id, new_title=new_title,
        )

        resp = redirect(url_for("job_status_page", job_id=job_id))
        if set_cookie:
            resp.set_cookie(
                _VG_UID_COOKIE, vg_uid, max_age=_VG_UID_MAX_AGE,
                httponly=False, samesite="Lax",
            )
        return resp

    @app.get("/status/<job_id>")
    def job_status_page(job_id):
        return render_template("status.html", job_id=job_id)

    @app.get("/api/status/<job_id>")
    def api_status(job_id):
        job = db.get_generation_request(job_id)
        if job is None:
            abort(404)
        result_slug = None
        result_title = None
        if job["result_game_id"]:
            game = db.get_web_game(job["result_game_id"])
            if game:
                result_slug = game["slug"]
                result_title = game["title"]
        return jsonify({
            "status": job["status"],
            "kind": job["kind"],
            "prompt": job["prompt"],
            "result_slug": result_slug,
            "result_title": result_title,
            "error": job["error"],
        })

    return app


# Module-level instance for WSGI servers (gunicorn target: app:app).
app = create_app()


if __name__ == "__main__":
    import os

    import yaml

    import job_runner

    config_path = _BASE_DIR / "config.yaml"
    full_config = {}
    if config_path.exists():
        with open(config_path) as f:
            full_config = yaml.safe_load(f) or {}
    gw_cfg = full_config.get("game_web", {})

    job_runner.start_workers(full_config, _BASE_DIR / gw_cfg.get("games_dir", "games"))

    app.run(
        host=gw_cfg.get("host", "0.0.0.0"),
        port=gw_cfg.get("port", 8600),
        debug=os.environ.get("FLASK_DEBUG") == "1",
        use_reloader=False,
    )
