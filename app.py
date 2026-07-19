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

import functools
import json
import logging
import os
import re
import threading
import time
import uuid
from pathlib import Path

from dotenv import load_dotenv
from flask import (
    Flask, abort, g, jsonify, redirect, render_template, request,
    send_from_directory, url_for,
)

import db

load_dotenv()  # so ADMIN_TOKEN (and anything else in .env) is set before any request

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,59}$")
_GAME_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_BASE_DIR = Path(__file__).parent
_VG_UID_COOKIE = "vg_uid"
_VG_UID_MAX_AGE = 31536000  # 1 year

_logger = logging.getLogger(__name__)


def require_admin_token(view):
    """Gate a view behind the ADMIN_TOKEN env var, checked as a `token`
    query param or a `Bearer` Authorization header. 403 on missing/wrong
    token — there is deliberately no "unset ADMIN_TOKEN means open" case."""
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        expected = os.environ.get("ADMIN_TOKEN")
        auth_header = request.headers.get("Authorization", "")
        supplied = request.args.get("token") or auth_header.removeprefix("Bearer ").strip()
        if not expected or not supplied or supplied != expected:
            abort(403)
        return view(*args, **kwargs)
    return wrapped


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
            "root_game_id": meta.get("root_game_id"),
            "prompt": meta.get("prompt", ""),
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

    def get_games(sort: str = "alpha", include_hidden: bool = False) -> list[dict]:
        """The disk-scanned manifest (cached, mtime-keyed) stays the source
        of truth for *which* games exist — including hand-written games
        like sample-game that were never registered in web_games — but
        rating tallies live in the DB and can change without any meta.json
        rewrite, so they're merged in fresh on every call rather than
        folded into the manifest cache."""
        with cache_lock:
            key = _manifest_cache_key(games_dir)
            if key != cache["key"]:
                cache["games"] = _build_manifest(games_dir)
                cache["key"] = key
            games = list(cache["games"])

        game_ids = [g["game_id"] for g in games if g["game_id"]]
        by_id = {}
        play_counts = {}
        conn = None
        if game_ids:
            conn = db.get_connection()
            placeholders = ",".join("?" * len(game_ids))
            rows = conn.execute(
                f"SELECT game_id, thumbs_up, thumbs_down, model, effort, tokens_used, "
                f"requested_by, creator_uid, hidden FROM web_games WHERE game_id IN ({placeholders})",
                game_ids,
            ).fetchall()
            by_id = {r["game_id"]: dict(r) for r in rows}
            play_counts = db.get_play_counts(game_ids, conn=conn)

        for g in games:
            row = by_id.get(g["game_id"], {})
            g["thumbs_up"] = row.get("thumbs_up", 0)
            g["thumbs_down"] = row.get("thumbs_down", 0)
            g["model"] = row.get("model")
            g["effort"] = row.get("effort")
            g["tokens_used"] = row.get("tokens_used")
            g["requested_by"] = row.get("requested_by")
            g["creator_uid"] = row.get("creator_uid")
            g["hidden"] = bool(row.get("hidden", 0))
            g["play_count"] = play_counts.get(g["game_id"], 0)

        if not include_hidden:
            games = [g for g in games if not g["hidden"]]

        creator_uids = {g["creator_uid"] for g in games if g.get("creator_uid")}
        usernames_by_uid = {}
        if creator_uids:
            conn = conn or db.get_connection()
            placeholders = ",".join("?" * len(creator_uids))
            rows = conn.execute(
                f"SELECT uid, username FROM users WHERE uid IN ({placeholders})",
                list(creator_uids),
            ).fetchall()
            usernames_by_uid = {r["uid"]: r["username"] for r in rows}
        for g in games:
            g["creator_name"] = (
                usernames_by_uid.get(g.get("creator_uid"))
                or g.get("requested_by")
                or "anonymous"
            )

        if sort == "rating":
            games.sort(key=lambda g: (
                -(g["thumbs_up"] - g["thumbs_down"]), -g["thumbs_up"], g["title"].casefold(),
            ))
        else:
            games.sort(key=lambda g: g["title"].casefold())
        return games

    def build_lineage(games: list[dict], game_id: str) -> dict:
        """Ancestor chain (root -> ... -> parent) plus sibling forks (other
        games sharing this root_game_id), computed purely from the
        already-loaded games list — no extra DB query."""
        by_id = {g["game_id"]: g for g in games if g["game_id"]}
        game = by_id.get(game_id)
        if game is None:
            return {"ancestors": [], "siblings": []}

        ancestors = []
        seen = {game_id}
        cur = game
        while cur.get("parent_game_id") and cur["parent_game_id"] not in seen:
            parent = by_id.get(cur["parent_game_id"])
            if parent is None:
                break
            ancestors.append(parent)
            seen.add(parent["game_id"])
            cur = parent
        ancestors.reverse()

        root_id = game.get("root_game_id") or game_id
        siblings = [
            g for g in games
            if g.get("root_game_id") == root_id and g["game_id"] != game_id
            and g["game_id"] not in seen
        ]
        return {"ancestors": ancestors, "siblings": siblings}

    @app.get("/")
    def index():
        sort = request.args.get("sort", "rating")
        if sort not in ("alpha", "rating"):
            sort = "rating"
        vg_uid = request.cookies.get(_VG_UID_COOKIE)
        user = db.get_user(vg_uid) if vg_uid else None
        return render_template(
            "index.html", games=get_games(sort), sort=sort,
            user=user, signed_in=request.args.get("signed_in") == "1",
        )

    @app.get("/api/games")
    def api_games():
        sort = request.args.get("sort", "rating")
        if sort not in ("alpha", "rating"):
            sort = "rating"
        return jsonify(get_games(sort))

    @app.get("/api/games/<game_id>/info")
    def api_game_info(game_id):
        if not _GAME_ID_RE.match(game_id):
            abort(404)
        games = get_games()
        by_id = {g["game_id"]: g for g in games if g["game_id"]}
        game = by_id.get(game_id)
        if game is None:
            abort(404)
        lineage = build_lineage(games, game_id)
        return jsonify({
            "game_id": game_id,
            "title": game["title"],
            "description": game["description"],
            "prompt": game.get("prompt", ""),
            "model": game.get("model"),
            "effort": game.get("effort"),
            "tokens_used": game.get("tokens_used"),
            "created_at": game.get("created_at"),
            "version": game.get("version"),
            "creator": game.get("creator_name", "anonymous"),
            "play_count": db.get_play_count(game_id),
            "recent_plays": db.get_recent_plays(game_id, limit=20),
            "ancestors": [
                {"slug": g["slug"], "title": g["title"]} for g in lineage["ancestors"]
            ],
            "siblings": [
                {"slug": g["slug"], "title": g["title"]} for g in lineage["siblings"]
            ],
        })

    @app.post("/api/games/<game_id>/rate")
    def rate_game(game_id):
        if not _GAME_ID_RE.match(game_id):
            abort(404)
        game = db.get_web_game(game_id)
        if game is None:
            abort(404)

        payload = request.get_json(silent=True) or {}
        vote = payload.get("vote")
        if vote not in (1, -1):
            return jsonify({"ok": False, "reason": "invalid_vote"}), 400

        vg_uid = request.cookies.get(_VG_UID_COOKIE)
        set_cookie = vg_uid is None
        if vg_uid is None:
            vg_uid = uuid.uuid4().hex

        # NOTE: request.remote_addr is the enforcement's IP half — if this
        # ever sits behind a reverse proxy, wrap the app in
        # werkzeug.middleware.proxy_fix.ProxyFix or every vote will read as
        # coming from the proxy's IP, collapsing the per-IP constraint.
        ok = db.record_rating(
            game_id, vote, client_uid=vg_uid, ip_address=request.remote_addr or "unknown",
        )
        updated = db.get_web_game(game_id)
        body = {
            "ok": ok, "thumbs_up": updated["thumbs_up"], "thumbs_down": updated["thumbs_down"],
        }
        if not ok:
            body["reason"] = "already_voted"
        resp = jsonify(body)
        resp.status_code = 200 if ok else 409
        if set_cookie:
            resp.set_cookie(
                _VG_UID_COOKIE, vg_uid, max_age=_VG_UID_MAX_AGE,
                httponly=False, samesite="Lax",
            )
        return resp

    @app.get("/play/<slug>")
    def play(slug):
        if not _SLUG_RE.match(slug):
            abort(404)
        game_dir = games_dir / slug
        if not (game_dir / "index.html").exists():
            abort(404)
        game = db.get_web_game_by_slug(slug)
        if game is not None:
            db.record_play(
                game["game_id"],
                client_uid=request.cookies.get(_VG_UID_COOKIE),
                ip_address=request.remote_addr or "unknown",
            )
        return send_from_directory(game_dir, "index.html")

    @app.get("/games/new")
    def new_game_form():
        vg_uid = request.cookies.get(_VG_UID_COOKIE)
        user = db.get_user(vg_uid) if vg_uid else None
        return render_template("new_game.html", user=user)

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
            creator_uid=vg_uid,
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
        vg_uid = request.cookies.get(_VG_UID_COOKIE)
        user = db.get_user(vg_uid) if vg_uid else None
        return render_template("enhance.html", game=game, user=user)

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
            source_game_id=game_id, new_title=new_title, creator_uid=vg_uid,
        )

        resp = redirect(url_for("job_status_page", job_id=job_id))
        if set_cookie:
            resp.set_cookie(
                _VG_UID_COOKIE, vg_uid, max_age=_VG_UID_MAX_AGE,
                httponly=False, samesite="Lax",
            )
        return resp

    @app.post("/signup")
    def signup():
        """No input needed — signing up just upgrades whatever vg_uid the
        visitor already has (or mints one) into a durable users row.
        Username is set separately via POST /account."""
        vg_uid = request.cookies.get(_VG_UID_COOKIE)
        set_cookie = vg_uid is None
        if vg_uid is None:
            vg_uid = uuid.uuid4().hex
        db.ensure_user(vg_uid)
        resp = redirect(url_for("account_page"))
        if set_cookie:
            resp.set_cookie(
                _VG_UID_COOKIE, vg_uid, max_age=_VG_UID_MAX_AGE,
                httponly=False, samesite="Lax",
            )
        return resp

    @app.get("/u/<uid>")
    def sign_in(uid):
        if not _GAME_ID_RE.match(uid):
            abort(404)
        user = db.get_user(uid)
        if user is None:
            abort(404)
        resp = redirect(url_for("index", signed_in="1"))
        resp.set_cookie(
            _VG_UID_COOKIE, uid, max_age=_VG_UID_MAX_AGE,
            httponly=False, samesite="Lax",
        )
        return resp

    @app.get("/account")
    def account_page():
        vg_uid = request.cookies.get(_VG_UID_COOKIE)
        user = db.get_user(vg_uid) if vg_uid else None
        my_games = [g for g in get_games() if g.get("creator_uid") == vg_uid] if vg_uid else []
        return render_template("account.html", uid=vg_uid, user=user, my_games=my_games)

    @app.post("/account")
    def account_update():
        vg_uid = request.cookies.get(_VG_UID_COOKIE)
        set_cookie = vg_uid is None
        if vg_uid is None:
            vg_uid = uuid.uuid4().hex
        db.ensure_user(vg_uid)
        username = (request.form.get("username") or "").strip()[:40] or None
        ok = db.set_username(vg_uid, username)
        user = db.get_user(vg_uid)
        my_games = [g for g in get_games() if g.get("creator_uid") == vg_uid]
        if ok:
            resp = redirect(url_for("account_page"))
        else:
            resp = app.make_response((
                render_template(
                    "account.html", uid=vg_uid, user=user, my_games=my_games,
                    error="That username is already taken.",
                ),
                400,
            ))
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

    @app.before_request
    def _start_timer():
        g._t0 = time.monotonic()

    @app.after_request
    def _log_access(response):
        if request.path.startswith("/static/"):
            return response
        try:
            duration_ms = (time.monotonic() - getattr(g, "_t0", time.monotonic())) * 1000
            conn = db.get_connection()
            conn.execute(
                "INSERT INTO access_log "
                "(method, path, status_code, ip_address, user_agent, client_uid, "
                " duration_ms, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    request.method, request.path, response.status_code,
                    # NOTE: request.remote_addr is the raw peer IP. If this app
                    # is ever run behind a reverse proxy, wrap it in
                    # werkzeug.middleware.proxy_fix.ProxyFix or this (and the
                    # ratings IP constraint above) will see only the proxy's IP.
                    request.remote_addr or "unknown",
                    request.headers.get("User-Agent"),
                    request.cookies.get(_VG_UID_COOKIE),
                    duration_ms, db.now_iso(),
                ),
            )
            conn.commit()
        except Exception:
            # Logging must never break a real request.
            _logger.exception("failed to write access_log row for %s %s",
                               request.method, request.path)
        return response

    @app.get("/admin/stats")
    @require_admin_token
    def admin_stats():
        conn = db.get_connection()
        total_hits = conn.execute("SELECT COUNT(*) AS n FROM access_log").fetchone()["n"]
        unique_clients = conn.execute(
            "SELECT COUNT(DISTINCT client_uid) AS n FROM access_log WHERE client_uid IS NOT NULL"
        ).fetchone()["n"]
        unique_ips = conn.execute(
            "SELECT COUNT(DISTINCT ip_address) AS n FROM access_log"
        ).fetchone()["n"]
        daily_hits = conn.execute(
            "SELECT date(created_at) AS day, COUNT(*) AS n FROM access_log "
            "WHERE created_at >= date('now', '-30 days') GROUP BY day ORDER BY day"
        ).fetchall()
        top_played_rows = conn.execute(
            "SELECT path, COUNT(*) AS n FROM access_log WHERE path LIKE '/play/%' "
            "GROUP BY path ORDER BY n DESC LIMIT 10"
        ).fetchall()
        top_played = []
        for row in top_played_rows:
            slug = row["path"].removeprefix("/play/")
            game = db.get_web_game_by_slug(slug, conn=conn)
            top_played.append({"slug": slug, "title": game["title"] if game else slug, "hits": row["n"]})
        top_rated = [
            g for g in db.get_web_games(sort="rating", conn=conn)
            if (g["thumbs_up"] or g["thumbs_down"])
        ][:10]
        all_games = get_games(include_hidden=True)
        all_users = db.get_all_users(conn=conn)

        return render_template(
            "admin_stats.html",
            total_hits=total_hits, unique_clients=unique_clients, unique_ips=unique_ips,
            daily_hits=daily_hits, top_played=top_played, top_rated=top_rated,
            all_games=all_games, admin_token=request.args.get("token"),
            all_users=all_users,
        )

    @app.post("/admin/games/<game_id>/hidden")
    @require_admin_token
    def admin_set_game_hidden(game_id):
        if not _GAME_ID_RE.match(game_id):
            abort(404)
        hidden = request.form.get("hidden") == "1"
        if not db.set_game_hidden(game_id, hidden):
            abort(404)
        return redirect(url_for("admin_stats", token=request.args.get("token")))

    @app.post("/admin/games/<game_id>/rename")
    @require_admin_token
    def admin_rename_game(game_id):
        if not _GAME_ID_RE.match(game_id):
            abort(404)
        new_title = (request.form.get("title") or "").strip()[:120]
        if not new_title:
            abort(400)
        game = db.get_web_game(game_id)
        if game is None:
            abort(404)
        db.rename_game(game_id, new_title)

        meta_path = games_dir / game["slug"] / "meta.json"
        meta = {}
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                meta = {}
        meta["title"] = new_title
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

        return redirect(url_for("admin_stats", token=request.args.get("token")))

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
