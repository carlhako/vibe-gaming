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
from pathlib import Path

from flask import Flask, abort, jsonify, render_template, send_from_directory

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,49}$")
_BASE_DIR = Path(__file__).parent


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
            "title": meta.get("title", entry.name),
            "description": meta.get("description", ""),
            "created_at": meta.get("created_at", ""),
            "version": meta.get("version", 1),
        })
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

    return app


# Module-level instance for WSGI servers (gunicorn target: app:app).
app = create_app()


if __name__ == "__main__":
    import os

    import yaml

    config_path = _BASE_DIR / "config.yaml"
    gw_cfg = {}
    if config_path.exists():
        with open(config_path) as f:
            gw_cfg = (yaml.safe_load(f) or {}).get("game_web", {})
    app.run(
        host=gw_cfg.get("host", "0.0.0.0"),
        port=gw_cfg.get("port", 8600),
        debug=os.environ.get("FLASK_DEBUG") == "1",
    )
