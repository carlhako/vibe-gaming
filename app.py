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
import hmac
import io
import json
import logging
import math
import os
import re
import threading
import time
import uuid
import zipfile
from pathlib import Path

from dotenv import load_dotenv
from flask import (
    Flask, abort, g, jsonify, make_response, redirect, render_template,
    request, send_file, send_from_directory, url_for,
)

import db
import game_generator

load_dotenv()  # so ADMIN_TOKEN (and anything else in .env) is set before any request

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,59}$")
_GAME_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_BASE_DIR = Path(__file__).parent
_VG_UID_COOKIE = "vg_uid"
_VG_UID_MAX_AGE = 31536000  # 1 year
_ADMIN_PAGE_SIZES = (20, 50, 100, 1000)

_logger = logging.getLogger(__name__)


def get_db():
    """One SQLite connection per request, reused by every db.* call in that
    request and closed in teardown — instead of each call opening (and
    never closing) its own connection, which under sustained/concurrent
    traffic multiplies both open-file-descriptor usage and lock contention
    on the single on-disk vibegames.db."""
    if "db_conn" not in g:
        g.db_conn = db.get_connection()
    return g.db_conn


def require_admin_token(view):
    """Gate a view behind the ADMIN_TOKEN env var, checked as a `token`
    query param or a `Bearer` Authorization header. 403 on missing/wrong
    token — there is deliberately no "unset ADMIN_TOKEN means open" case."""
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        expected = os.environ.get("ADMIN_TOKEN")
        auth_header = request.headers.get("Authorization", "")
        supplied = request.args.get("token") or auth_header.removeprefix("Bearer ").strip()
        if not expected or not supplied or not hmac.compare_digest(supplied, expected):
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def _page_params(prefix):
    """Read `{prefix}_page` / `{prefix}_per` pagination query params,
    falling back to page 1 / 20-per-page on anything missing or invalid."""
    try:
        per = int(request.args.get(f"{prefix}_per", 20))
    except (TypeError, ValueError):
        per = 20
    if per not in _ADMIN_PAGE_SIZES:
        per = 20
    try:
        page = max(1, int(request.args.get(f"{prefix}_page", 1)))
    except (TypeError, ValueError):
        page = 1
    return page, per


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


_VERSION_SUFFIX_RE = re.compile(r"\s*\(v(\d+)\)\s*$", re.IGNORECASE)


def _split_version_suffix(title: str) -> tuple[str, str | None]:
    """Split a trailing '(vN)' fork marker off a title, e.g.
    'Tower Defence (v3)' -> ('Tower Defence', 'v3'). Titles with no such
    marker (originals, or hand-written games) return (title, None)."""
    m = _VERSION_SUFFIX_RE.search(title)
    if not m:
        return title, None
    return title[:m.start()].rstrip(), f"v{m.group(1)}"


def _stable_hue(key: str) -> int:
    """Deterministic 0-4 index from a string, independent of Python's
    per-process hash randomization (so a game's spine color doesn't shift
    on every restart) — used to color every game in a fork family the same
    hue so siblings visually cluster on the shelf."""
    return sum(ord(c) for c in key) % 5


def _group_and_sort_games(games: list[dict], sort: str) -> list[dict]:
    """Cluster games sharing a root_game_id (an original plus all its
    forks) so they sit adjacent on the shelf instead of being scattered by
    rating, and annotate each with the fields the sidebar needs to render
    that grouping: base_title/version_label (the '(vN)' suffix pulled out
    so it can't be truncated away), family_size/family_index/family_last
    (position within its family — index 0 is the newest version, shown
    as the head card; the rest are older versions nested in the shelf's
    per-family accordion), and family_hue (shared spine color). Works
    fine when some family members are hidden — grouping is keyed on
    root_game_id, not on any particular member being present."""
    families: dict[str, list[dict]] = {}
    for g in games:
        base_title, version_label = _split_version_suffix(g["title"])
        g["base_title"] = base_title
        g["version_label"] = version_label
        family_key = g.get("root_game_id") or g.get("game_id") or g["slug"]
        families.setdefault(family_key, []).append((family_key, g))

    ranked_families = []
    for family_key, keyed_members in families.items():
        members = [g for _, g in keyed_members]
        # Newest first: index 0 is the family "head" card shown on the
        # shelf, everything else is an older version tucked in the
        # per-family accordion.
        members.sort(key=lambda m: (m.get("created_at") or "", m["title"].casefold()), reverse=True)
        if len(members) > 1:
            for m in members:
                if m["version_label"] is None:
                    m["version_label"] = "Original"
        hue = _stable_hue(family_key)
        for idx, m in enumerate(members):
            m["family_size"] = len(members)
            m["family_index"] = idx
            m["family_last"] = idx == len(members) - 1
            m["family_hue"] = hue

        base_for_sort = members[0]["base_title"].casefold()
        latest_created = max(m.get("created_at") or "" for m in members)
        if sort == "rating":
            best_score = max(m["thumbs_up"] - m["thumbs_down"] for m in members)
            best_up = max(m["thumbs_up"] for m in members)
            rank_key = (-best_score, -best_up, base_for_sort)
        else:
            rank_key = (base_for_sort,)
        ranked_families.append((rank_key, latest_created, members))

    if sort == "date":
        # Stable two-pass sort: alpha tie-break first (ascending), then
        # the primary date key (descending, newest family first) — sort's
        # stability preserves the tie-break order within equal dates.
        ranked_families.sort(key=lambda item: item[0])
        ranked_families.sort(key=lambda item: item[1], reverse=True)
    else:
        ranked_families.sort(key=lambda item: item[0])
    result = []
    for _, _, members in ranked_families:
        result.extend(members)
    return result


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

    # Bundled games ship their game_id in meta.json but vibegames.db is
    # gitignored, so a fresh clone has no web_games rows for them until
    # this backfills one per game. No-op on every start after the first.
    db.sync_games_from_disk(games_dir)

    cache_lock = threading.Lock()
    cache = {"key": None, "games": []}

    def get_games(sort: str = "alpha", include_hidden: bool = False) -> list[dict]:
        """The disk-scanned manifest (cached, mtime-keyed) stays the source
        of truth for *which* games exist — including hand-dropped games
        with no game_id that were never registered in web_games — but
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
        conn = get_db()
        if game_ids:
            placeholders = ",".join("?" * len(game_ids))
            rows = conn.execute(
                f"SELECT game_id, thumbs_up, thumbs_down, model, effort, tokens_used, "
                f"duration_seconds, requested_by, creator_uid, hidden "
                f"FROM web_games WHERE game_id IN ({placeholders})",
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
            g["duration_seconds"] = row.get("duration_seconds")
            g["requested_by"] = row.get("requested_by")
            g["creator_uid"] = row.get("creator_uid")
            g["hidden"] = bool(row.get("hidden", 0))
            g["play_count"] = play_counts.get(g["game_id"], 0)

        if not include_hidden:
            games = [g for g in games if not g["hidden"]]

        creator_uids = {g["creator_uid"] for g in games if g.get("creator_uid")}
        usernames_by_uid = {}
        if creator_uids:
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

        return _group_and_sort_games(games, sort)

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
        sort = request.args.get("sort", "date")
        if sort not in ("alpha", "rating", "date"):
            sort = "date"
        vg_uid = request.cookies.get(_VG_UID_COOKIE)
        user = db.get_user(vg_uid, conn=get_db()) if vg_uid else None
        return render_template(
            "index.html", games=get_games(sort), sort=sort,
            user=user, signed_in=request.args.get("signed_in") == "1",
        )

    @app.get("/api/games")
    def api_games():
        sort = request.args.get("sort", "date")
        if sort not in ("alpha", "rating", "date"):
            sort = "date"
        return jsonify(get_games(sort))

    @app.get("/api/games/<game_id>/info")
    def api_game_info(game_id):
        if not _GAME_ID_RE.match(game_id):
            abort(404)
        games = get_games(include_hidden=True)
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
            "duration_seconds": game.get("duration_seconds"),
            "created_at": game.get("created_at"),
            "version": game.get("version"),
            "creator": game.get("creator_name", "anonymous"),
            "play_count": db.get_play_count(game_id, conn=get_db()),
            "recent_plays": db.get_recent_plays(game_id, limit=20, conn=get_db()),
            "ancestors": [
                {"slug": g["slug"], "title": g["title"], "hidden": g.get("hidden", False)}
                for g in lineage["ancestors"]
            ],
            "siblings": [
                {"slug": g["slug"], "title": g["title"], "hidden": g.get("hidden", False)}
                for g in lineage["siblings"]
            ],
        })

    @app.post("/api/games/<game_id>/rate")
    def rate_game(game_id):
        if not _GAME_ID_RE.match(game_id):
            abort(404)
        conn = get_db()
        game = db.get_web_game(game_id, conn=conn)
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
            conn=conn,
        )
        updated = db.get_web_game(game_id, conn=conn)
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
        # Look up the game_id the same way the rest of the app does - from
        # meta.json via the disk manifest - rather than a separate
        # slug-keyed DB query. A VM whose vibegames.db predates a game's
        # committed meta.json game_id (e.g. connect-4-4, migrated onto the
        # GUID schema before its game_id existed) can end up with a stale
        # duplicate web_games row under the same slug but a different
        # game_id; querying by slug there silently records plays against
        # the wrong row while the UI displays counts for the meta.json one.
        meta_path = game_dir / "meta.json"
        game_id = None
        if meta_path.exists():
            try:
                game_id = json.loads(meta_path.read_text(encoding="utf-8")).get("game_id")
            except (json.JSONDecodeError, OSError):
                game_id = None
        conn = get_db()
        if game_id and db.get_web_game(game_id, conn=conn):
            db.record_play(
                game_id,
                client_uid=request.cookies.get(_VG_UID_COOKIE),
                ip_address=request.remote_addr or "unknown",
                conn=conn,
            )
        return send_from_directory(game_dir, "index.html")

    @app.get("/games/<game_id>/download")
    def download_game(game_id):
        if not _GAME_ID_RE.match(game_id):
            abort(404)
        game = db.get_web_game(game_id, conn=get_db())
        if game is None:
            abort(404)
        game_dir = games_dir / game["slug"]
        if not (game_dir / "index.html").exists():
            abort(404)
        safe_title = game_generator.slugify(game["title"]) or "game"
        filename = f"{safe_title}-v{game['version'] or 1}.html"
        return send_from_directory(
            game_dir, "index.html", as_attachment=True, download_name=filename,
        )

    @app.get("/games/new")
    def new_game_form():
        vg_uid = request.cookies.get(_VG_UID_COOKIE)
        user = db.get_user(vg_uid, conn=get_db()) if vg_uid else None
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
            creator_uid=vg_uid, conn=get_db(),
        )

        resp = redirect(url_for("job_status_page", job_id=job_id))
        if set_cookie:
            resp.set_cookie(
                _VG_UID_COOKIE, vg_uid, max_age=_VG_UID_MAX_AGE,
                httponly=False, samesite="Lax",
            )
        return resp

    def _enhance_lock_context(game_id, vg_uid):
        """Build the template context describing whether game_id's enhance
        form is available to vg_uid right now.

        Phase B (a generation_requests row already generating/queued for
        this game) always wins and blocks everyone, including the
        submitter's own other tabs — only one enhance in flight per game.
        Otherwise phase A (enhance_locks) is checked/acquired: acquiring
        always succeeds for the uid that already holds it (a reload just
        renews the 10 minutes) and otherwise only for whoever's request
        wins the race in db.acquire_enhance_lock."""
        job = db.get_active_enhance_job(game_id, conn=get_db())
        if job is not None:
            return {
                "locked": True,
                "lock_phase": "job",
                "lock_held_by_me": job.get("creator_uid") == vg_uid,
                "job_status": job["status"],
                "job_started_at": job["updated_at"] if job["status"] == "generating" else None,
                "lock_token": None,
                "lock_expires_at": None,
            }
        won, lock = db.acquire_enhance_lock(game_id, vg_uid, conn=get_db())
        return {
            "locked": not won,
            "lock_phase": "form",
            "lock_held_by_me": won,
            "job_status": None,
            "job_started_at": None,
            "lock_token": lock["lock_token"] if won else None,
            "lock_expires_at": lock["expires_at"],
        }

    @app.get("/games/<game_id>/enhance")
    def enhance_game_form(game_id):
        if not _GAME_ID_RE.match(game_id):
            abort(404)
        game = db.get_web_game(game_id, conn=get_db())
        if game is None:
            abort(404)

        vg_uid = request.cookies.get(_VG_UID_COOKIE)
        set_cookie = vg_uid is None
        if vg_uid is None:
            vg_uid = uuid.uuid4().hex
        user = db.get_user(vg_uid, conn=get_db())

        lock_ctx = _enhance_lock_context(game_id, vg_uid)
        resp = make_response(render_template("enhance.html", game=game, user=user, **lock_ctx))
        if set_cookie:
            resp.set_cookie(
                _VG_UID_COOKIE, vg_uid, max_age=_VG_UID_MAX_AGE,
                httponly=False, samesite="Lax",
            )
        return resp

    @app.post("/games/<game_id>/enhance")
    def enhance_game_submit(game_id):
        if not _GAME_ID_RE.match(game_id):
            abort(404)
        game = db.get_web_game(game_id, conn=get_db())
        if game is None:
            abort(404)

        description = (request.form.get("description") or "").strip()
        new_title = (request.form.get("new_title") or "").strip() or None
        lock_token = request.form.get("lock_token") or ""

        vg_uid = request.cookies.get(_VG_UID_COOKIE)
        set_cookie = vg_uid is None
        if vg_uid is None:
            vg_uid = uuid.uuid4().hex
        user = db.get_user(vg_uid, conn=get_db())

        error = None
        status = 400
        if not description:
            error = "Please describe the change you want."
        elif db.get_active_enhance_job(game_id, conn=get_db()) is not None:
            error = "Someone already started an enhancement for this game — wait for it to finish."
            status = 409
        elif not db.heartbeat_enhance_lock(game_id, lock_token, conn=get_db()):
            error = "Your lock on this game expired. Reopen this page to try again."
            status = 409

        if error:
            lock_ctx = _enhance_lock_context(game_id, vg_uid)
            return render_template("enhance.html", game=game, user=user, error=error, **lock_ctx), status

        requested_by = "web:" + vg_uid[:12]
        job_id = uuid.uuid4().hex
        db.create_generation_request(
            job_id=job_id, kind="enhance", prompt=description, requested_by=requested_by,
            source_game_id=game_id, new_title=new_title, creator_uid=vg_uid, conn=get_db(),
        )
        db.release_enhance_lock(game_id, lock_token, conn=get_db())

        resp = redirect(url_for("job_status_page", job_id=job_id))
        if set_cookie:
            resp.set_cookie(
                _VG_UID_COOKIE, vg_uid, max_age=_VG_UID_MAX_AGE,
                httponly=False, samesite="Lax",
            )
        return resp

    @app.post("/games/<game_id>/enhance/lock/ping")
    def enhance_lock_ping(game_id):
        if not _GAME_ID_RE.match(game_id):
            abort(404)
        lock_token = request.form.get("lock_token") or ""
        expires_at = bool(lock_token) and db.heartbeat_enhance_lock(game_id, lock_token, conn=get_db())
        return jsonify(ok=bool(expires_at), expires_at=expires_at or None)

    @app.post("/games/<game_id>/enhance/lock/release")
    def enhance_lock_release(game_id):
        if not _GAME_ID_RE.match(game_id):
            abort(404)
        lock_token = request.form.get("lock_token") or ""
        if lock_token:
            db.release_enhance_lock(game_id, lock_token, conn=get_db())
        return "", 204

    @app.get("/games/new/idea-forge")
    def idea_forge_new_form():
        vg_uid = request.cookies.get(_VG_UID_COOKIE)
        user = db.get_user(vg_uid, conn=get_db()) if vg_uid else None
        prefill = request.args.get("prompt", "")
        return render_template(
            "idea_forge.html", mode="create", game=None, user=user, prefill=prefill
        )

    @app.post("/games/new/idea-forge")
    def idea_forge_new_submit():
        rough = (request.form.get("rough_prompt") or "").strip()
        if not rough:
            return render_template(
                "idea_forge.html", mode="create", game=None,
                error="Please describe your rough idea first.",
            ), 400

        vg_uid = request.cookies.get(_VG_UID_COOKIE)
        set_cookie = vg_uid is None
        if vg_uid is None:
            vg_uid = uuid.uuid4().hex

        requested_by = "web:" + vg_uid[:12]
        job_id = uuid.uuid4().hex
        db.create_generation_request(
            job_id=job_id, kind="prompt_help", prompt=rough, requested_by=requested_by,
            creator_uid=vg_uid, conn=get_db(),
        )

        resp = redirect(url_for("job_status_page", job_id=job_id))
        if set_cookie:
            resp.set_cookie(
                _VG_UID_COOKIE, vg_uid, max_age=_VG_UID_MAX_AGE,
                httponly=False, samesite="Lax",
            )
        return resp

    @app.get("/games/<game_id>/enhance/idea-forge")
    def idea_forge_enhance_form(game_id):
        if not _GAME_ID_RE.match(game_id):
            abort(404)
        game = db.get_web_game(game_id, conn=get_db())
        if game is None:
            abort(404)
        vg_uid = request.cookies.get(_VG_UID_COOKIE)
        user = db.get_user(vg_uid, conn=get_db()) if vg_uid else None
        prefill = request.args.get("description", "")
        return render_template(
            "idea_forge.html", mode="enhance", game=game, user=user, prefill=prefill
        )

    @app.post("/games/<game_id>/enhance/idea-forge")
    def idea_forge_enhance_submit(game_id):
        if not _GAME_ID_RE.match(game_id):
            abort(404)
        game = db.get_web_game(game_id, conn=get_db())
        if game is None:
            abort(404)

        rough = (request.form.get("rough_prompt") or "").strip()
        if not rough:
            return render_template(
                "idea_forge.html", mode="enhance", game=game,
                error="Please describe your rough idea first.",
            ), 400

        vg_uid = request.cookies.get(_VG_UID_COOKIE)
        set_cookie = vg_uid is None
        if vg_uid is None:
            vg_uid = uuid.uuid4().hex

        requested_by = "web:" + vg_uid[:12]
        job_id = uuid.uuid4().hex
        db.create_generation_request(
            job_id=job_id, kind="prompt_help", prompt=rough, requested_by=requested_by,
            source_game_id=game_id, creator_uid=vg_uid, conn=get_db(),
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
        db.ensure_user(vg_uid, conn=get_db())
        resp = redirect(url_for("account_page"))
        if set_cookie:
            resp.set_cookie(
                _VG_UID_COOKIE, vg_uid, max_age=_VG_UID_MAX_AGE,
                httponly=False, samesite="Lax",
            )
        return resp

    def _do_sign_in(uid):
        """Validate uid format + existence; on success return a redirect
        response with the vg_uid cookie set, else (None, error_message)."""
        if not _GAME_ID_RE.match(uid) or db.get_user(uid, conn=get_db()) is None:
            return None, "Token not recognized. Double-check what you pasted."
        resp = redirect(url_for("index", signed_in="1"))
        resp.set_cookie(
            _VG_UID_COOKIE, uid, max_age=_VG_UID_MAX_AGE,
            httponly=False, samesite="Lax",
        )
        return resp, None

    @app.get("/u/<uid>")
    def sign_in(uid):
        resp, _error = _do_sign_in(uid)
        if resp is None:
            abort(404)
        return resp

    @app.get("/signin")
    def sign_in_form():
        return render_template("signin.html")

    @app.post("/signin")
    def sign_in_submit():
        token = (request.form.get("token") or "").strip()
        resp, error = _do_sign_in(token)
        if resp is None:
            return render_template("signin.html", error=error, token=token), 400
        return resp

    @app.get("/account")
    def account_page():
        vg_uid = request.cookies.get(_VG_UID_COOKIE)
        user = db.get_user(vg_uid, conn=get_db()) if vg_uid else None
        return render_template("account.html", uid=vg_uid, user=user)

    @app.post("/account")
    def account_update():
        conn = get_db()
        vg_uid = request.cookies.get(_VG_UID_COOKIE)
        set_cookie = vg_uid is None
        if vg_uid is None:
            vg_uid = uuid.uuid4().hex
        db.ensure_user(vg_uid, conn=conn)
        username = (request.form.get("username") or "").strip()[:40] or None
        ok = db.set_username(vg_uid, username, conn=conn)
        user = db.get_user(vg_uid, conn=conn)
        if ok:
            resp = redirect(url_for("account_page"))
        else:
            resp = app.make_response((
                render_template(
                    "account.html", uid=vg_uid, user=user,
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

    @app.get("/profile")
    def profile_page():
        conn = get_db()
        vg_uid = request.cookies.get(_VG_UID_COOKIE)
        if not vg_uid or db.get_user(vg_uid, conn=conn) is None:
            return redirect(url_for("account_page"))
        user = db.get_user(vg_uid, conn=conn)
        my_games = [g for g in get_games(include_hidden=True) if g.get("creator_uid") == vg_uid]
        stats = db.get_user_stats(vg_uid, conn=conn)
        recent_plays = db.get_user_play_history(vg_uid, limit=20, conn=conn)
        return render_template(
            "profile.html", uid=vg_uid, user=user, my_games=my_games,
            stats=stats, recent_plays=recent_plays,
        )

    @app.post("/profile/games/<game_id>/hidden")
    def profile_set_game_hidden(game_id):
        if not _GAME_ID_RE.match(game_id):
            abort(404)
        conn = get_db()
        vg_uid = request.cookies.get(_VG_UID_COOKIE)
        game = db.get_web_game(game_id, conn=conn)
        if game is None or not vg_uid or game.get("creator_uid") != vg_uid:
            abort(404)
        hidden = request.form.get("hidden") == "1"
        db.set_game_hidden(game_id, hidden, conn=conn)
        return redirect(url_for("profile_page"))

    @app.get("/leaderboard")
    def leaderboard():
        ranking = db.get_user_leaderboard(conn=get_db())
        games_by_uid = {}
        for game in get_games():  # default include_hidden=False
            uid = game.get("creator_uid")
            if uid:
                games_by_uid.setdefault(uid, []).append(game)
        return render_template("leaderboard.html", ranking=ranking, games_by_uid=games_by_uid)

    @app.get("/status/<job_id>")
    def job_status_page(job_id):
        return render_template("status.html", job_id=job_id)

    @app.get("/api/status/<job_id>")
    def api_status(job_id):
        conn = get_db()
        job = db.get_generation_request(job_id, conn=conn)
        if job is None:
            abort(404)
        result_slug = None
        result_title = None
        if job["result_game_id"]:
            game = db.get_web_game(job["result_game_id"], conn=conn)
            if game:
                result_slug = game["slug"]
                result_title = game["title"]

        queue_position = None
        eta_seconds = None
        avg_duration_seconds = db.get_average_duration(kind=job["kind"], conn=conn)
        if job["status"] == "queued":
            queue_position = db.get_queue_position(job_id, conn=conn)
            blended_avg = db.get_average_duration(kind=None, conn=conn)
            if blended_avg is not None:
                jobs_ahead = queue_position + (1 if db.count_generating(conn=conn) else 0)
                eta_seconds = blended_avg * jobs_ahead

        return jsonify({
            "status": job["status"],
            "kind": job["kind"],
            "prompt": job["prompt"],
            "result_slug": result_slug,
            "result_title": result_title,
            "error": job["error"],
            "queue_position": queue_position,
            "eta_seconds": eta_seconds,
            "avg_duration_seconds": avg_duration_seconds,
            # updated_at is bumped exactly when claim_next_queued_request()
            # flips status to 'generating', so it doubles as that
            # transition's timestamp — the client derives its live elapsed
            # timer from this rather than counting ticks, so it's still
            # correct after the tab was backgrounded/throttled.
            "generating_started_at": job["updated_at"] if job["status"] == "generating" else None,
            "tokens_used": job["tokens_used"],
            "duration_seconds": job["duration_seconds"],
            "source_game_id": job["source_game_id"],
            "result_text": job.get("result_text"),
            "detected_genre": job.get("detected_genre"),
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
            conn = get_db()
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
        conn = get_db()
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

        admin_token = request.args.get("token")
        history_page, history_per = _page_params("history")
        plays_page, plays_per = _page_params("plays")

        history_total = db.count_generation_requests(conn=conn)
        history_pages = max(1, math.ceil(history_total / history_per))
        history_page = min(history_page, history_pages)
        history_rows = db.get_generation_history(
            limit=history_per, offset=(history_page - 1) * history_per, conn=conn)

        plays_total = db.count_plays(conn=conn)
        plays_pages = max(1, math.ceil(plays_total / plays_per))
        plays_page = min(plays_page, plays_pages)
        plays_rows = db.get_play_history(
            limit=plays_per, offset=(plays_page - 1) * plays_per, conn=conn)

        def _stats_url(**overrides):
            params = dict(token=admin_token,
                          history_page=history_page, history_per=history_per,
                          plays_page=plays_page, plays_per=plays_per)
            params.update(overrides)
            return url_for("admin_stats",
                           **{k: v for k, v in params.items() if v is not None})

        def _pager(tab, page, pages, per, total, keep):
            return {
                "tab": tab, "page": page, "pages": pages,
                "per": per, "total": total,
                "prev_url": _stats_url(**{f"{tab}_page": page - 1}) + f"#{tab}"
                            if page > 1 else None,
                "next_url": _stats_url(**{f"{tab}_page": page + 1}) + f"#{tab}"
                            if page < pages else None,
                # Params the page-size GET form must re-send as hidden inputs
                # (a GET form replaces the whole query string): the token and
                # the *other* tab's state. Its own page is deliberately absent
                # so changing the page size resets to page 1.
                "keep": {k: v for k, v in keep.items() if v is not None},
            }

        history_pager = _pager(
            "history", history_page, history_pages, history_per, history_total,
            keep={"token": admin_token, "plays_page": plays_page, "plays_per": plays_per})
        plays_pager = _pager(
            "plays", plays_page, plays_pages, plays_per, plays_total,
            keep={"token": admin_token, "history_page": history_page, "history_per": history_per})

        return render_template(
            "admin_stats.html",
            total_hits=total_hits, unique_clients=unique_clients, unique_ips=unique_ips,
            daily_hits=daily_hits, top_played=top_played, top_rated=top_rated,
            all_games=all_games, admin_token=admin_token,
            all_users=all_users,
            history_rows=history_rows, history_pager=history_pager,
            plays_rows=plays_rows, plays_pager=plays_pager,
            page_sizes=_ADMIN_PAGE_SIZES,
        )

    @app.get("/admin/games/download")
    @require_admin_token
    def admin_download_games():
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for entry in sorted(games_dir.iterdir()):
                if not entry.is_dir() or entry.name == "backups":
                    continue
                if not (entry / "index.html").exists():
                    continue
                zf.write(entry / "index.html", arcname=f"{entry.name}/index.html")
                meta_path = entry / "meta.json"
                if meta_path.exists():
                    zf.write(meta_path, arcname=f"{entry.name}/meta.json")
        buf.seek(0)
        filename = f"vibegames-games-{db.now_iso()[:10]}.zip"
        return send_file(buf, mimetype="application/zip",
                          as_attachment=True, download_name=filename)

    @app.post("/admin/games/<game_id>/hidden")
    @require_admin_token
    def admin_set_game_hidden(game_id):
        if not _GAME_ID_RE.match(game_id):
            abort(404)
        hidden = request.form.get("hidden") == "1"
        if not db.set_game_hidden(game_id, hidden, conn=get_db()):
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
        conn = get_db()
        game = db.get_web_game(game_id, conn=conn)
        if game is None:
            abort(404)
        db.rename_game(game_id, new_title, conn=conn)

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

    @app.teardown_appcontext
    def _close_db(exception=None):
        conn = g.pop("db_conn", None)
        if conn is not None:
            conn.close()

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
