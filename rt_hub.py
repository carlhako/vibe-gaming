"""
rt_hub.py — the standalone real-time relay for multiplayer games.

This is a game-agnostic dumb relay. It runs as its own asyncio process —
exactly the way job_runner.py is its own polling process — and is
reverse-proxied at ``/rt/`` (see README.md). A hub crash cannot take down
``/play/`` or generation, and untrusted relay traffic never lands on a Flask
worker.

What it owns:

  - Room membership, one room per game, keyed by the validated ``game_id``.
    The key is derived server-side; no client-supplied string contributes to
    it. A room is created lazily on the first join and discarded the moment
    it is empty.
  - Per-game capacity, read server-side from ``games/<slug>/meta.json``'s
    ``multiplayer.max_players`` (mtime-checked read cache). A client cannot
    raise it with any message. A ``game_id`` whose meta.json has no
    ``multiplayer`` block gets no room.
  - A presence roster ``[{id, nick, ping_ms}, ...]`` re-broadcast on every
    membership change. The nickname is client-declared, display-only, and
    always relayed verbatim as untrusted data.
  - A server-measured ping: the hub periodically sends ``ping{seq,ts}`` and
    computes RTT from its own clock when the client echoes ``pong{seq}``.
    Client-reported timing is never trusted.

What it deliberately does NOT do: inspect, validate, transform, execute or
persist game payloads; open a database connection; or write game/player data
to disk or logs. Payload semantics stay the game's problem, contained by the
served-game sandbox. See openspec/changes/add-multiplayer-games/design.md.

The process-stability limits (max frame size, message rate, connections per
IP, strict JSON parse with no raw-error reflection) are framed as keeping
this process up under untrusted frames, not as anti-abuse.
"""

import argparse
import asyncio
import json
import logging
import os
import re
import secrets
import time
from collections import deque
from pathlib import Path

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s rt_hub %(levelname)s %(message)s"
)
_log = logging.getLogger("rt_hub")

# The exact format the rest of the site uses for a game_id (app.py's
# _GAME_ID_RE, db.mint_game_id -> uuid4().hex).
GAME_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_PATH_RE = re.compile(r"^/rt/([^/?#]+)")

# Close codes the client (VG_RT) distinguishes. 4001/4002/4003 are the wire
# protocol's (design.md D8); 4008-4011 are local "you tripped a stability
# limit" codes and are not part of the protocol contract.
CLOSE_ROOM_FULL = 4001
CLOSE_NOT_MULTIPLAYER = 4002
CLOSE_BAD_GAME_ID = 4003
CLOSE_CONN_LIMIT = 4008
CLOSE_FRAME_TOO_LARGE = 4009
CLOSE_RATE_LIMIT = 4010
CLOSE_PING_TIMEOUT = 4011

DEFAULTS = {
    "host": "127.0.0.1",
    "port": 8620,
    "games_dir": "games",
    "max_frame_bytes": 16384,
    "max_msg_per_sec": 40,
    "max_conns_per_ip": 16,
    "ping_interval_s": 5,
    "dead_after_s": 15,
    "idle_room_ttl_s": 60,
}


def load_config(path: str = "config.yaml") -> dict:
    """Merge the ``rt_hub:`` block of ``path`` over DEFAULTS. A missing file or
    missing block just yields DEFAULTS — same "works out of the box" posture as
    the other config blocks."""
    cfg = dict(DEFAULTS)
    try:
        import yaml
    except ImportError:
        return cfg
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except OSError:
        return cfg
    block = (raw.get("rt_hub") or {}) if isinstance(raw, dict) else {}
    for key in DEFAULTS:
        if key in block and block[key] is not None:
            cfg[key] = block[key]
    return cfg


def parse_multiplayer_block(meta: dict) -> int | None:
    """``meta['multiplayer']['max_players']`` if it is an int >= 2, else None.

    Deliberately duplicated (not imported) from builder.read_multiplayer so the
    hub stays a leaf process with no app-tier imports. The rule is one line and
    the two copies are covered by the same spec.
    """
    if not isinstance(meta, dict):
        return None
    block = meta.get("multiplayer")
    if not isinstance(block, dict):
        return None
    mp = block.get("max_players")
    if isinstance(mp, bool) or not isinstance(mp, int):
        return None
    return mp if mp >= 2 else None


class MetaResolver:
    """game_id -> slug -> meta.json.multiplayer.max_players, with an
    mtime-checked read cache. Read-only; never writes anything."""

    def __init__(self, games_dir):
        self.games_dir = Path(games_dir)
        self._slug: dict[str, str] = {}
        self._cache: dict[str, tuple[int, int | None]] = {}

    def _meta_path(self, slug: str) -> Path:
        return self.games_dir / slug / "meta.json"

    def _resolve_slug(self, game_id: str) -> str | None:
        slug = self._slug.get(game_id)
        if slug and self._meta_path(slug).is_file():
            return slug
        try:
            entries = list(self.games_dir.iterdir())
        except OSError:
            return None
        for d in entries:
            meta_path = d / "meta.json"
            if not meta_path.is_file():
                continue
            try:
                gid = json.loads(meta_path.read_text(encoding="utf-8")).get("game_id")
            except (OSError, ValueError):
                continue
            if gid:
                self._slug[gid] = d.name
        return self._slug.get(game_id)

    def max_players(self, game_id: str) -> int | None:
        """None means "not a multiplayer game" — missing dir, missing/corrupt
        meta.json, or no valid multiplayer block. Never raises."""
        slug = self._resolve_slug(game_id)
        if not slug:
            return None
        meta_path = self._meta_path(slug)
        try:
            mtime = meta_path.stat().st_mtime_ns
        except OSError:
            return None
        cached = self._cache.get(game_id)
        if cached is not None and cached[0] == mtime:
            return cached[1]
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            result = None
        else:
            result = parse_multiplayer_block(meta)
        self._cache[game_id] = (mtime, result)
        return result


class Member:
    __slots__ = (
        "id", "ws", "ip", "room", "nick", "ping_ms", "last_seen", "ping_seq",
        "pending_ping", "_msg_times", "closing",
    )

    def __init__(self, member_id: str, ws, ip: str, now: float):
        self.id = member_id
        self.ws = ws
        self.ip = ip
        self.room: "Room | None" = None
        self.nick = ""
        self.ping_ms: int | None = None
        self.last_seen = now
        self.ping_seq = 0
        self.pending_ping: dict[int, float] = {}
        self._msg_times: deque[float] = deque()
        self.closing = False


class Room:
    __slots__ = ("game_id", "max_players", "members", "empty_since")

    def __init__(self, game_id: str, max_players: int):
        self.game_id = game_id
        self.max_players = max_players
        self.members: dict[str, Member] = {}
        self.empty_since: float | None = None


class Hub:
    def __init__(self, config: dict | None = None, clock=time.monotonic):
        self.cfg = dict(DEFAULTS)
        if config:
            self.cfg.update(config)
        self.clock = clock
        self.resolver = MetaResolver(self.cfg["games_dir"])
        self.rooms: dict[str, Room] = {}
        self._conns_per_ip: dict[str, int] = {}

    # -- outbound -----------------------------------------------------------

    async def _send(self, member: Member, obj: dict) -> None:
        await self._send_text(member, json.dumps(obj, separators=(",", ":")))

    async def _send_text(self, member: Member, text: str) -> None:
        try:
            await member.ws.send(text)
        except Exception:
            # A dead socket is reaped by the read loop / ping round; a failed
            # send here must not abort a broadcast to the rest of the room.
            pass

    def _roster(self, room: Room) -> dict:
        return {
            "t": "roster",
            "members": [
                {"id": m.id, "nick": m.nick, "ping_ms": m.ping_ms}
                for m in room.members.values()
            ],
        }

    async def _broadcast_roster(self, room: Room) -> None:
        payload = json.dumps(self._roster(room), separators=(",", ":"))
        for m in list(room.members.values()):
            await self._send_text(m, payload)

    # -- lifecycle --------------------------------------------------------

    @staticmethod
    def _peer_ip(ws) -> str:
        try:
            fwd = ws.request.headers.get("X-Forwarded-For")
        except Exception:
            fwd = None
        if fwd:
            return fwd.split(",")[0].strip()
        addr = getattr(ws, "remote_address", None)
        if addr:
            return addr[0]
        return "unknown"

    async def connect(self, ws, path: str | None = None) -> Member | None:
        """Validate the handshake and place the client in its room, or close
        the socket with a distinguishable code and return None."""
        if path is None:
            path = ws.request.path
        m = _PATH_RE.match(path or "")
        game_id = m.group(1) if m else ""
        if not GAME_ID_RE.match(game_id):
            await ws.close(CLOSE_BAD_GAME_ID, "bad game_id")
            return None

        ip = self._peer_ip(ws)
        if self._conns_per_ip.get(ip, 0) >= self.cfg["max_conns_per_ip"]:
            await ws.close(CLOSE_CONN_LIMIT, "too many connections")
            return None

        max_players = self.resolver.max_players(game_id)
        if max_players is None:
            await ws.close(CLOSE_NOT_MULTIPLAYER, "not a multiplayer game")
            return None

        room = self.rooms.get(game_id)
        if room is None:
            room = self.rooms[game_id] = Room(game_id, max_players)
        else:
            # Capacity is a property of the game's meta.json, re-read on every
            # join so an author bump takes effect without a hub restart.
            room.max_players = max_players
        if len(room.members) >= room.max_players:
            await ws.close(CLOSE_ROOM_FULL, "room full")
            return None

        member = Member(secrets.token_hex(6), ws, ip, self.clock())
        member.room = room
        room.members[member.id] = member
        room.empty_since = None
        self._conns_per_ip[ip] = self._conns_per_ip.get(ip, 0) + 1

        await self._send(member, {"t": "welcome", "id": member.id, "max": room.max_players})
        await self._broadcast_roster(room)
        _log.info("join game=%s members=%d", game_id, len(room.members))
        return member

    async def disconnect(self, member: Member) -> None:
        n = self._conns_per_ip.get(member.ip, 0) - 1
        if n <= 0:
            self._conns_per_ip.pop(member.ip, None)
        else:
            self._conns_per_ip[member.ip] = n
        room = member.room
        member.room = None
        if room is None or member.id not in room.members:
            return
        del room.members[member.id]
        if room.members:
            await self._broadcast_roster(room)
        else:
            room.empty_since = self.clock()
            self.rooms.pop(room.game_id, None)
        _log.info("leave game=%s members=%d", room.game_id, len(room.members))

    # -- inbound ----------------------------------------------------------

    async def on_message(self, member: Member, raw) -> None:
        """One inbound frame. Enforces the stability limits, then dispatches a
        well-formed protocol frame. A frame that fails any check is dropped (and
        may close the socket); the raw parser error is never reflected back."""
        size = len(raw) if isinstance(raw, (bytes, bytearray)) else len(raw.encode("utf-8"))
        if size > self.cfg["max_frame_bytes"]:
            member.closing = True
            await member.ws.close(CLOSE_FRAME_TOO_LARGE, "frame too large")
            return

        now = self.clock()
        member._msg_times.append(now)
        while member._msg_times and member._msg_times[0] <= now - 1.0:
            member._msg_times.popleft()
        if len(member._msg_times) > self.cfg["max_msg_per_sec"]:
            member.closing = True
            await member.ws.close(CLOSE_RATE_LIMIT, "rate limit")
            return

        try:
            frame = json.loads(raw)
        except (ValueError, TypeError):
            return
        if not isinstance(frame, dict):
            return

        room = member.room
        if room is None:
            return
        t = frame.get("t")
        if t == "join":
            nick = frame.get("nick")
            member.nick = nick if isinstance(nick, str) else ""
            member.last_seen = now
            await self._broadcast_roster(room)
        elif t == "pong":
            sent = member.pending_ping.pop(frame.get("seq"), None)
            member.last_seen = now
            if sent is not None:
                member.ping_ms = max(0, int(round((now - sent) * 1000)))
        elif t == "msg":
            if "d" not in frame:
                return
            member.last_seen = now
            out = json.dumps(
                {"t": "msg", "from": member.id, "d": frame["d"]},
                separators=(",", ":"),
            )
            for other in list(room.members.values()):
                if other.id != member.id:
                    await self._send_text(other, out)
        # Anything else — including a frame trying to change max_players — is
        # ignored outright.

    # -- periodic --------------------------------------------------------

    async def ping_round(self) -> None:
        """Send every live member a fresh ping and drop anyone past
        dead_after_s. Split out from the timer loop so a test can drive it with
        a fake clock."""
        now = self.clock()
        dead_after = self.cfg["dead_after_s"]
        for room in list(self.rooms.values()):
            for member in list(room.members.values()):
                if now - member.last_seen > dead_after:
                    member.closing = True
                    try:
                        await member.ws.close(CLOSE_PING_TIMEOUT, "ping timeout")
                    except Exception:
                        pass
                    room.members.pop(member.id, None)
                    member.room = None
                    n = self._conns_per_ip.get(member.ip, 0) - 1
                    if n <= 0:
                        self._conns_per_ip.pop(member.ip, None)
                    else:
                        self._conns_per_ip[member.ip] = n
                    continue
                member.ping_seq += 1
                member.pending_ping[member.ping_seq] = now
                # Bound the outstanding-ping map so a client that never echoes
                # cannot grow it without limit before dead_after_s fires.
                if len(member.pending_ping) > 64:
                    for seq in list(member.pending_ping)[:-64]:
                        del member.pending_ping[seq]
                await self._send(
                    member,
                    {"t": "ping", "seq": member.ping_seq, "ts": int(now * 1000)},
                )
            if not room.members:
                room.empty_since = now
                self.rooms.pop(room.game_id, None)
            else:
                # Re-broadcast every round: this is how an updated ping_ms
                # (measured from a pong to an earlier round) reaches clients,
                # and how a reaped member is dropped from everyone's roster.
                await self._broadcast_roster(room)

    def gc_rooms(self) -> None:
        """Drop any room that is empty or whose last member vanished (without a
        clean close) longer than idle_room_ttl_s ago."""
        now = self.clock()
        ttl = self.cfg["idle_room_ttl_s"]
        for key, room in list(self.rooms.items()):
            if not room.members:
                self.rooms.pop(key, None)
            elif room.empty_since is not None and now - room.empty_since > ttl:
                self.rooms.pop(key, None)

    # -- server ---------------------------------------------------------

    async def _handler(self, ws) -> None:
        member = await self.connect(ws)
        if member is None:
            return
        try:
            async for raw in ws:
                await self.on_message(member, raw)
        except Exception:
            pass
        finally:
            await self.disconnect(member)

    async def _timers(self) -> None:
        while True:
            await asyncio.sleep(self.cfg["ping_interval_s"])
            try:
                await self.ping_round()
                self.gc_rooms()
            except Exception:
                _log.exception("timer round failed")

    async def serve_forever(self) -> None:
        import websockets

        async with websockets.serve(
            self._handler,
            self.cfg["host"],
            self.cfg["port"],
            max_size=self.cfg["max_frame_bytes"],
            ping_interval=None,  # the hub runs its own application-level ping
        ):
            _log.info("listening on ws://%s:%d/rt/<game_id>",
                      self.cfg["host"], self.cfg["port"])
            timers = asyncio.create_task(self._timers())
            try:
                await asyncio.Future()
            finally:
                timers.cancel()


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Vibegames real-time multiplayer hub")
    ap.add_argument("--config", default=os.environ.get("VG_CONFIG", "config.yaml"))
    args = ap.parse_args(argv)
    hub = Hub(load_config(args.config))
    try:
        asyncio.run(hub.serve_forever())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
