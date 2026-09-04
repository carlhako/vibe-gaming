"""rt_hub.py — the standalone multiplayer relay.

Covers openspec change add-multiplayer-games, tasks 1.1-1.11: config parse,
game_id validation + close codes, meta.json capacity resolution, the room
model, the wire protocol, presence roster, server-measured ping, opaque
payload relay, the process-stability limits, and "no persistence".

The fast tests drive Hub.connect / on_message / disconnect / ping_round
directly with a fake websocket and an injectable clock. One slow test runs
the real asyncio server and connects a real websockets client.
"""

import asyncio
import json
import os
from pathlib import Path

import pytest

import rt_hub


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        pending = asyncio.all_tasks(loop)
        for t in pending:
            t.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()


class FakeReq:
    def __init__(self, path, headers=None):
        self.path = path
        self.headers = headers or {}


class FakeWS:
    def __init__(self, path="/rt/" + "a" * 32, ip="10.0.0.1", headers=None):
        self.request = FakeReq(path, headers)
        self.remote_address = (ip, 12345)
        self.sent: list[str] = []
        self.close_code = None
        self.close_reason = None
        self.closed = False

    async def send(self, text):
        if self.closed:
            raise ConnectionError("closed")
        self.sent.append(text)

    async def close(self, code=1000, reason=""):
        self.closed = True
        self.close_code = code
        self.close_reason = reason

    def frames(self):
        return [json.loads(s) for s in self.sent]

    def last(self, t):
        for obj in reversed(self.frames()):
            if obj.get("t") == t:
                return obj
        return None


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


def make_game(games_dir: Path, game_id: str, *, multiplayer=None) -> str:
    slug = f"game-{game_id[:8]}"
    d = games_dir / slug
    d.mkdir(parents=True)
    meta = {"game_id": game_id, "title": "T"}
    if multiplayer is not None:
        meta["multiplayer"] = multiplayer
    (d / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    (d / "index.html").write_text("<!doctype html><body>", encoding="utf-8")
    return slug


GID = "abcdef01" + "0" * 24          # valid uuid4-hex shape
GID2 = "beefbeef" + "1" * 24


def make_hub(games_dir, clock, **overrides):
    cfg = dict(rt_hub.DEFAULTS)
    cfg["games_dir"] = str(games_dir)
    cfg.update(overrides)
    return rt_hub.Hub(cfg, clock=clock)


# --------------------------------------------------------------------------
# 1.1  config block
# --------------------------------------------------------------------------

def test_config_example_rt_hub_block_parses():
    import yaml

    root = Path(__file__).resolve().parent.parent
    raw = yaml.safe_load((root / "config.yaml.example").read_text(encoding="utf-8"))
    block = raw["rt_hub"]
    for key in ("host", "port", "max_frame_bytes", "max_msg_per_sec",
                "max_msg_burst_per_sec", "max_conns_per_ip", "ping_interval_s",
                "dead_after_s", "idle_room_ttl_s"):
        assert key in block, key
    # Every key the example documents must be a real knob, or the comment is
    # describing something the hub does not read.
    for key in block:
        assert key in rt_hub.DEFAULTS, key
    assert block["max_msg_burst_per_sec"] > block["max_msg_per_sec"]


def test_rate_defaults_leave_60fps_inside_the_soft_budget():
    """One send per animation frame at 60fps is the ordinary idiom; it must sit
    inside the soft budget rather than one frame outside it."""
    assert rt_hub.DEFAULTS["max_msg_per_sec"] >= 60
    assert (rt_hub.DEFAULTS["max_msg_burst_per_sec"]
            > rt_hub.DEFAULTS["max_msg_per_sec"])


def test_load_config_merges_both_rate_tiers(tmp_path):
    (tmp_path / "config.yaml").write_text(
        "rt_hub:\n  max_msg_per_sec: 99\n  max_msg_burst_per_sec: 500\n",
        encoding="utf-8")
    cfg = rt_hub.load_config(str(tmp_path / "config.yaml"))
    assert cfg["max_msg_per_sec"] == 99
    assert cfg["max_msg_burst_per_sec"] == 500


def test_load_config_without_rt_hub_block_uses_new_rate_defaults(tmp_path):
    (tmp_path / "config.yaml").write_text("game_web:\n  port: 1\n", encoding="utf-8")
    cfg = rt_hub.load_config(str(tmp_path / "config.yaml"))
    assert cfg["max_msg_per_sec"] == rt_hub.DEFAULTS["max_msg_per_sec"]
    assert cfg["max_msg_burst_per_sec"] == rt_hub.DEFAULTS["max_msg_burst_per_sec"]


def test_load_config_merges_over_defaults(tmp_path):
    (tmp_path / "config.yaml").write_text(
        "rt_hub:\n  port: 9999\n  max_conns_per_ip: 3\n", encoding="utf-8")
    cfg = rt_hub.load_config(str(tmp_path / "config.yaml"))
    assert cfg["port"] == 9999
    assert cfg["max_conns_per_ip"] == 3
    assert cfg["ping_interval_s"] == rt_hub.DEFAULTS["ping_interval_s"]


def test_load_config_missing_file_is_defaults(tmp_path):
    assert rt_hub.load_config(str(tmp_path / "nope.yaml")) == rt_hub.DEFAULTS


# --------------------------------------------------------------------------
# 1.2  game_id validation
# --------------------------------------------------------------------------

def test_valid_game_id_connects(tmp_path):
    make_game(tmp_path, GID, multiplayer={"max_players": 2})
    hub = make_hub(tmp_path, Clock())
    ws = FakeWS(path=f"/rt/{GID}")
    member = run(hub.connect(ws))
    assert member is not None
    assert not ws.closed
    assert ws.last("welcome")["id"] == member.id


def test_malformed_game_id_closes_4003_no_room(tmp_path):
    hub = make_hub(tmp_path, Clock())
    ws = FakeWS(path="/rt/not-a-valid-id")
    member = run(hub.connect(ws))
    assert member is None
    assert ws.close_code == rt_hub.CLOSE_BAD_GAME_ID
    assert hub.rooms == {}


def test_uppercase_game_id_rejected(tmp_path):
    hub = make_hub(tmp_path, Clock())
    ws = FakeWS(path="/rt/" + "A" * 32)
    assert run(hub.connect(ws)) is None
    assert ws.close_code == rt_hub.CLOSE_BAD_GAME_ID


# --------------------------------------------------------------------------
# 1.3  meta.json resolution + mtime cache
# --------------------------------------------------------------------------

def test_meta_resolver_valid_block(tmp_path):
    make_game(tmp_path, GID, multiplayer={"max_players": 4})
    r = rt_hub.MetaResolver(tmp_path)
    assert r.max_players(GID) == 4


def test_meta_resolver_no_block(tmp_path):
    make_game(tmp_path, GID)
    assert rt_hub.MetaResolver(tmp_path).max_players(GID) is None


def test_meta_resolver_corrupt_file(tmp_path):
    slug = make_game(tmp_path, GID, multiplayer={"max_players": 2})
    (tmp_path / slug / "meta.json").write_text("{not json", encoding="utf-8")
    r = rt_hub.MetaResolver(tmp_path)
    r._slug[GID] = slug
    assert r.max_players(GID) is None


def test_meta_resolver_missing_game(tmp_path):
    assert rt_hub.MetaResolver(tmp_path).max_players(GID) is None


def test_meta_resolver_mtime_cache_refreshes(tmp_path):
    slug = make_game(tmp_path, GID, multiplayer={"max_players": 2})
    r = rt_hub.MetaResolver(tmp_path)
    assert r.max_players(GID) == 2
    mp = tmp_path / slug / "meta.json"
    st = mp.stat()
    mp.write_text(json.dumps({"game_id": GID, "multiplayer": {"max_players": 6}}),
                  encoding="utf-8")
    os.utime(mp, ns=(st.st_mtime_ns + 1_000_000_000, st.st_mtime_ns + 1_000_000_000))
    assert r.max_players(GID) == 6


@pytest.mark.parametrize("block", [
    {"max_players": 1}, {"max_players": "3"}, {"max_players": 2.0},
    {"max_players": True}, {"players": 2}, {}, "nope",
])
def test_parse_multiplayer_block_rejects_invalid(block):
    assert rt_hub.parse_multiplayer_block({"multiplayer": block}) is None


# --------------------------------------------------------------------------
# 1.4  room model
# --------------------------------------------------------------------------

def test_room_created_lazily_and_destroyed_when_empty(tmp_path):
    make_game(tmp_path, GID, multiplayer={"max_players": 2})
    hub = make_hub(tmp_path, Clock())
    assert hub.rooms == {}
    a = run(hub.connect(FakeWS(path=f"/rt/{GID}")))
    b = run(hub.connect(FakeWS(path=f"/rt/{GID}", ip="10.0.0.2")))
    assert set(hub.rooms) == {GID}
    assert len(hub.rooms[GID].members) == 2
    run(hub.disconnect(a))
    assert GID in hub.rooms
    run(hub.disconnect(b))
    assert hub.rooms == {}


def test_gc_drops_stale_room(tmp_path):
    make_game(tmp_path, GID, multiplayer={"max_players": 2})
    clk = Clock()
    hub = make_hub(tmp_path, clk, idle_room_ttl_s=30)
    run(hub.connect(FakeWS(path=f"/rt/{GID}")))
    hub.rooms[GID].empty_since = clk.t          # simulate a member vanished
    clk.advance(31)
    hub.gc_rooms()
    assert hub.rooms == {}


# --------------------------------------------------------------------------
# 1.5  server-authoritative capacity
# --------------------------------------------------------------------------

def test_over_capacity_refused_4001(tmp_path):
    make_game(tmp_path, GID, multiplayer={"max_players": 2})
    hub = make_hub(tmp_path, Clock())
    run(hub.connect(FakeWS(path=f"/rt/{GID}", ip="1.1.1.1")))
    run(hub.connect(FakeWS(path=f"/rt/{GID}", ip="1.1.1.2")))
    ws3 = FakeWS(path=f"/rt/{GID}", ip="1.1.1.3")
    assert run(hub.connect(ws3)) is None
    assert ws3.close_code == rt_hub.CLOSE_ROOM_FULL


def test_non_multiplayer_game_refused_4002(tmp_path):
    make_game(tmp_path, GID)  # no multiplayer block
    hub = make_hub(tmp_path, Clock())
    ws = FakeWS(path=f"/rt/{GID}")
    assert run(hub.connect(ws)) is None
    assert ws.close_code == rt_hub.CLOSE_NOT_MULTIPLAYER


def test_client_cannot_widen_capacity(tmp_path):
    make_game(tmp_path, GID, multiplayer={"max_players": 2})
    hub = make_hub(tmp_path, Clock())
    a = run(hub.connect(FakeWS(path=f"/rt/{GID}")))
    run(hub.on_message(a, json.dumps({"t": "config", "max_players": 99})))
    run(hub.on_message(a, json.dumps({"t": "welcome", "max": 99})))
    assert hub.rooms[GID].max_players == 2


# --------------------------------------------------------------------------
# 1.6  wire protocol shapes
# --------------------------------------------------------------------------

def test_welcome_and_initial_roster_on_join(tmp_path):
    make_game(tmp_path, GID, multiplayer={"max_players": 3})
    hub = make_hub(tmp_path, Clock())
    ws_a = FakeWS(path=f"/rt/{GID}")
    a = run(hub.connect(ws_a))
    welcome = ws_a.frames()[0]
    assert welcome == {"t": "welcome", "id": a.id, "max": 3}
    roster = ws_a.last("roster")
    assert roster["members"] == [{"id": a.id, "nick": "", "ping_ms": None}]


def test_two_client_roster_shapes(tmp_path):
    make_game(tmp_path, GID, multiplayer={"max_players": 3})
    hub = make_hub(tmp_path, Clock())
    ws_a = FakeWS(path=f"/rt/{GID}", ip="2.0.0.1")
    a = run(hub.connect(ws_a))
    ws_b = FakeWS(path=f"/rt/{GID}", ip="2.0.0.2")
    b = run(hub.connect(ws_b))
    run(hub.on_message(a, json.dumps({"t": "join", "nick": "Ann"})))
    run(hub.on_message(b, json.dumps({"t": "join", "nick": "Bo"})))
    roster = ws_a.last("roster")["members"]
    by_id = {m["id"]: m["nick"] for m in roster}
    assert by_id == {a.id: "Ann", b.id: "Bo"}


# --------------------------------------------------------------------------
# 1.7  roster on membership change + untrusted nickname
# --------------------------------------------------------------------------

def test_join_and_leave_each_broadcast_roster(tmp_path):
    make_game(tmp_path, GID, multiplayer={"max_players": 3})
    hub = make_hub(tmp_path, Clock())
    ws_a = FakeWS(path=f"/rt/{GID}", ip="3.0.0.1")
    a = run(hub.connect(ws_a))
    n_after_a = len([f for f in ws_a.frames() if f["t"] == "roster"])
    b = run(hub.connect(FakeWS(path=f"/rt/{GID}", ip="3.0.0.2")))
    assert len([f for f in ws_a.frames() if f["t"] == "roster"]) == n_after_a + 1
    run(hub.disconnect(b))
    rosters = [f for f in ws_a.frames() if f["t"] == "roster"]
    assert len(rosters) == n_after_a + 2
    assert [m["id"] for m in rosters[-1]["members"]] == [a.id]


def test_markup_nickname_round_trips_verbatim(tmp_path):
    make_game(tmp_path, GID, multiplayer={"max_players": 2})
    hub = make_hub(tmp_path, Clock())
    ws_a = FakeWS(path=f"/rt/{GID}", ip="3.1.0.1")
    a = run(hub.connect(ws_a))
    b = run(hub.connect(FakeWS(path=f"/rt/{GID}", ip="3.1.0.2")))
    evil = '<img src=x onerror=alert(1)>'
    run(hub.on_message(b, json.dumps({"t": "join", "nick": evil})))
    entry = next(m for m in ws_a.last("roster")["members"] if m["id"] == b.id)
    assert entry["nick"] == evil


# --------------------------------------------------------------------------
# 1.8  server-measured ping (fake clock)
# --------------------------------------------------------------------------

def test_ping_rtt_from_hub_clock_appears_in_roster(tmp_path):
    make_game(tmp_path, GID, multiplayer={"max_players": 2})
    clk = Clock()
    hub = make_hub(tmp_path, clk)
    ws_a = FakeWS(path=f"/rt/{GID}", ip="4.0.0.1")
    a = run(hub.connect(ws_a))
    b = run(hub.connect(FakeWS(path=f"/rt/{GID}", ip="4.0.0.2")))
    assert a.ping_ms is None
    run(hub.ping_round())
    ping = ws_a.last("ping")
    assert ping["seq"] == 1 and "ts" in ping
    clk.advance(0.123)                     # 123 ms later the client echoes
    run(hub.on_message(a, json.dumps({"t": "pong", "seq": 1})))
    assert a.ping_ms == 123
    run(hub.disconnect(b))                 # forces a fresh roster to A...
    # roster after a completed exchange shows the non-null ping
    run(hub.connect(FakeWS(path=f"/rt/{GID}", ip="4.0.0.3")))
    entry = next(m for m in ws_a.last("roster")["members"] if m["id"] == a.id)
    assert entry["ping_ms"] == 123


def test_unresponsive_member_dropped_and_roster_rebroadcast(tmp_path):
    make_game(tmp_path, GID, multiplayer={"max_players": 3})
    clk = Clock()
    hub = make_hub(tmp_path, clk, dead_after_s=15)
    ws_a = FakeWS(path=f"/rt/{GID}", ip="4.1.0.1")
    a = run(hub.connect(ws_a))
    ws_b = FakeWS(path=f"/rt/{GID}", ip="4.1.0.2")
    b = run(hub.connect(ws_b))
    clk.advance(20)                        # both now past dead_after_s...
    run(hub.on_message(a, json.dumps({"t": "pong", "seq": 0})))  # A speaks, stays
    run(hub.ping_round())
    assert b.id not in hub.rooms[GID].members
    assert ws_b.close_code == rt_hub.CLOSE_PING_TIMEOUT
    assert [m["id"] for m in ws_a.last("roster")["members"]] == [a.id]


# --------------------------------------------------------------------------
# 1.9  opaque payload relay
# --------------------------------------------------------------------------

def test_msg_relayed_to_peers_only_no_self_echo(tmp_path):
    make_game(tmp_path, GID, multiplayer={"max_players": 3})
    hub = make_hub(tmp_path, Clock())
    ws_a = FakeWS(path=f"/rt/{GID}", ip="5.0.0.1")
    a = run(hub.connect(ws_a))
    ws_b = FakeWS(path=f"/rt/{GID}", ip="5.0.0.2")
    b = run(hub.connect(ws_b))
    ws_c = FakeWS(path=f"/rt/{GID}", ip="5.0.0.3")
    c = run(hub.connect(ws_c))

    payload = {"x": 1, "link": "https://evil.example/x?a=1",
               "code": "<script>alert(1)</script>", "eval": "eval('2+2')"}
    a_frames_before = len(ws_a.sent)
    run(hub.on_message(a, json.dumps({"t": "msg", "d": payload})))

    for ws, mem in ((ws_b, b), (ws_c, c)):
        got = ws.last("msg")
        assert got["from"] == a.id
        assert got["d"] == payload           # structurally unchanged
    assert len(ws_a.sent) == a_frames_before  # sender got no echo


def test_hub_never_acts_on_payload_contents(tmp_path, monkeypatch):
    make_game(tmp_path, GID, multiplayer={"max_players": 2})
    hub = make_hub(tmp_path, Clock())
    a = run(hub.connect(FakeWS(path=f"/rt/{GID}", ip="5.1.0.1")))
    b = run(hub.connect(FakeWS(path=f"/rt/{GID}", ip="5.1.0.2")))
    # a payload naming max_players / a room key must not change hub state
    run(hub.on_message(a, json.dumps(
        {"t": "msg", "d": {"t": "join", "game_id": GID2, "max_players": 50}})))
    assert set(hub.rooms) == {GID}
    assert hub.rooms[GID].max_players == 2


# --------------------------------------------------------------------------
# 1.10  process-stability limits
# --------------------------------------------------------------------------

def test_oversized_frame_dropped_and_closed(tmp_path):
    make_game(tmp_path, GID, multiplayer={"max_players": 2})
    hub = make_hub(tmp_path, Clock(), max_frame_bytes=64)
    a = run(hub.connect(FakeWS(path=f"/rt/{GID}", ip="6.0.0.1")))
    b = run(hub.connect(FakeWS(path=f"/rt/{GID}", ip="6.0.0.2")))
    ws_b = b.ws
    big = json.dumps({"t": "msg", "d": {"blob": "z" * 500}})
    run(hub.on_message(b, big))
    assert ws_b.close_code == rt_hub.CLOSE_FRAME_TOO_LARGE
    assert a.ws.last("msg") is None          # never relayed


def test_message_flood_disconnects_only_that_client(tmp_path):
    """Only the hard burst ceiling is fatal; the soft budget above it is not.
    Driven past `max_msg_burst_per_sec` rather than the old single fatal cap."""
    make_game(tmp_path, GID, multiplayer={"max_players": 3})
    make_game(tmp_path, GID2, multiplayer={"max_players": 2})
    hub = make_hub(tmp_path, Clock(), max_msg_per_sec=5, max_msg_burst_per_sec=20)
    a = run(hub.connect(FakeWS(path=f"/rt/{GID}", ip="6.1.0.1")))
    b = run(hub.connect(FakeWS(path=f"/rt/{GID}", ip="6.1.0.2")))
    other = run(hub.connect(FakeWS(path=f"/rt/{GID2}", ip="6.1.0.3")))
    for _ in range(30):
        run(hub.on_message(b, json.dumps({"t": "msg", "d": 1})))
    assert b.ws.close_code == rt_hub.CLOSE_RATE_LIMIT
    # Same room and a different room are both untouched.
    assert not a.ws.closed
    assert a.id in hub.rooms[GID].members
    assert not other.ws.closed
    assert other.id in hub.rooms[GID2].members


def _spam(hub, member, n, *, t="msg"):
    frame = json.dumps({"t": t, "d": 1} if t == "msg" else {"t": t})
    for _ in range(n):
        run(hub.on_message(member, frame))


def test_over_soft_budget_stays_connected_across_windows(tmp_path):
    """A client held between the soft budget and the hard ceiling for several
    rolling windows is throttled, never closed, and never leaves its room."""
    make_game(tmp_path, GID, multiplayer={"max_players": 2})
    clock = Clock()
    hub = make_hub(tmp_path, clock, max_msg_per_sec=10, max_msg_burst_per_sec=100)
    a = run(hub.connect(FakeWS(path=f"/rt/{GID}", ip="6.4.0.1")))
    b = run(hub.connect(FakeWS(path=f"/rt/{GID}", ip="6.4.0.2")))
    for _window in range(5):
        # 30/sec: over the soft budget of 10, well under the ceiling of 100.
        for _ in range(30):
            _spam(hub, b, 1)
            clock.advance(1.0 / 30.0)
        assert not b.ws.closed
        assert b.id in hub.rooms[GID].members
        assert b.ws.close_code is None
    assert b.discarded > 0
    # And its roster entry is unchanged.
    roster = a.ws.last("roster")
    assert {m["id"] for m in roster["members"]} == {a.id, b.id}


def test_at_exactly_the_soft_budget_nothing_is_shed(tmp_path):
    make_game(tmp_path, GID, multiplayer={"max_players": 2})
    clock = Clock()
    hub = make_hub(tmp_path, clock, max_msg_per_sec=10, max_msg_burst_per_sec=100)
    a = run(hub.connect(FakeWS(path=f"/rt/{GID}", ip="6.5.0.1")))
    b = run(hub.connect(FakeWS(path=f"/rt/{GID}", ip="6.5.0.2")))
    before = len([f for f in a.ws.frames() if f["t"] == "msg"])
    for _window in range(4):
        for _ in range(10):
            _spam(hub, b, 1)
            clock.advance(0.1)
    relayed = len([f for f in a.ws.frames() if f["t"] == "msg"]) - before
    assert relayed == 40
    assert b.discarded == 0
    assert not b.ws.closed


def test_throttled_frames_are_not_relayed_but_in_budget_ones_are(tmp_path):
    """Exact peer receipt counts: the shed frames reach nobody, and the same
    client's under-budget frames still reach the peer."""
    make_game(tmp_path, GID, multiplayer={"max_players": 2})
    clock = Clock()
    hub = make_hub(tmp_path, clock, max_msg_per_sec=5, max_msg_burst_per_sec=100)
    a = run(hub.connect(FakeWS(path=f"/rt/{GID}", ip="6.6.0.1")))
    b = run(hub.connect(FakeWS(path=f"/rt/{GID}", ip="6.6.0.2")))

    def a_msgs():
        return [f for f in a.ws.frames() if f["t"] == "msg"]

    base = len(a_msgs())
    _spam(hub, b, 12)                       # 5 relayed, 7 shed
    assert len(a_msgs()) - base == 5
    assert b.discarded == 7
    # A fresh window: the same client is back under budget and relays again.
    clock.advance(1.1)
    _spam(hub, b, 3)
    assert len(a_msgs()) - base == 8
    assert b.discarded == 7
    assert not b.ws.closed


def test_over_budget_client_still_gets_its_budget_through(tmp_path):
    """Only the EXCESS is shed, not everything.

    Measured against arrivals rather than admissions, a rolling window stays
    saturated once the rate passes the budget, so every subsequent frame reads
    as over-budget and ~nothing is relayed. A client at 70/sec against a
    60/sec budget must get ~60/sec through and shed ~10/sec.
    """
    make_game(tmp_path, GID, multiplayer={"max_players": 2})
    clock = Clock()
    hub = make_hub(tmp_path, clock, max_msg_per_sec=60, max_msg_burst_per_sec=240)
    a = run(hub.connect(FakeWS(path=f"/rt/{GID}", ip="6.11.0.1")))
    b = run(hub.connect(FakeWS(path=f"/rt/{GID}", ip="6.11.0.2")))
    base = len([f for f in a.ws.frames() if f["t"] == "msg"])

    for _ in range(700):                      # 10 seconds at 70/sec
        _spam(hub, b, 1)
        clock.advance(1.0 / 70.0)

    relayed = len([f for f in a.ws.frames() if f["t"] == "msg"]) - base
    assert relayed + b.discarded == 700
    assert 570 <= relayed <= 610, relayed     # ~60/sec got through
    assert 90 <= b.discarded <= 130, b.discarded


def test_control_frames_survive_throttling_and_keep_member_alive(tmp_path):
    """`pong` is what refreshes liveness. If throttling shed it, an over-budget
    client would be dropped by the liveness check instead — the same disconnect
    loop on a slower period."""
    make_game(tmp_path, GID, multiplayer={"max_players": 2})
    clock = Clock()
    hub = make_hub(tmp_path, clock, max_msg_per_sec=5, max_msg_burst_per_sec=1000,
                   dead_after_s=15)
    a = run(hub.connect(FakeWS(path=f"/rt/{GID}", ip="6.7.0.1")))
    b = run(hub.connect(FakeWS(path=f"/rt/{GID}", ip="6.7.0.2")))

    # Four ping rounds 5s apart — 20s in total, well past dead_after_s — with
    # b over the soft budget in every one of them.
    for _round in range(4):
        run(hub.ping_round())
        seq_a = a.ws.last("ping")["seq"]
        seq_b = b.ws.last("ping")["seq"]
        clock.advance(0.02)
        _spam(hub, b, 20)                    # flood past the soft budget
        run(hub.on_message(a, json.dumps({"t": "pong", "seq": seq_a})))
        run(hub.on_message(b, json.dumps({"t": "pong", "seq": seq_b})))
        assert b.last_seen == clock.t        # the pong was processed, not shed
        assert b.ping_ms == 20
        clock.advance(5.0)

    run(hub.ping_round())
    assert not b.ws.closed
    assert b.id in hub.rooms[GID].members
    assert b.discarded > 0
    assert not a.ws.closed


def test_nickname_change_survives_throttling(tmp_path):
    make_game(tmp_path, GID, multiplayer={"max_players": 2})
    hub = make_hub(tmp_path, Clock(), max_msg_per_sec=3, max_msg_burst_per_sec=1000)
    a = run(hub.connect(FakeWS(path=f"/rt/{GID}", ip="6.8.0.1")))
    b = run(hub.connect(FakeWS(path=f"/rt/{GID}", ip="6.8.0.2")))
    _spam(hub, b, 10)                        # push well over the soft budget
    run(hub.on_message(b, json.dumps({"t": "join", "nick": "zed"})))
    assert b.nick == "zed"
    entry = [m for m in a.ws.last("roster")["members"] if m["id"] == b.id][0]
    assert entry["nick"] == "zed"
    assert not b.ws.closed


def test_control_frame_flood_still_trips_the_ceiling(tmp_path):
    """Control frames are exempt from *discarding*, not from counting."""
    make_game(tmp_path, GID, multiplayer={"max_players": 2})
    hub = make_hub(tmp_path, Clock(), max_msg_per_sec=5, max_msg_burst_per_sec=20)
    run(hub.connect(FakeWS(path=f"/rt/{GID}", ip="6.9.0.1")))
    b = run(hub.connect(FakeWS(path=f"/rt/{GID}", ip="6.9.0.2")))
    for _ in range(30):
        run(hub.on_message(b, json.dumps({"t": "pong", "seq": 1})))
    assert b.ws.close_code == rt_hub.CLOSE_RATE_LIMIT


def test_throttling_is_logged_boundedly_and_without_payloads(tmp_path, caplog):
    make_game(tmp_path, GID, multiplayer={"max_players": 2})
    clock = Clock()
    hub = make_hub(tmp_path, clock, max_msg_per_sec=5, max_msg_burst_per_sec=10000)
    run(hub.connect(FakeWS(path=f"/rt/{GID}", ip="6.10.0.1")))
    b = run(hub.connect(FakeWS(path=f"/rt/{GID}", ip="6.10.0.2")))
    secret = "PAYLOAD-SECRET-a1b2c3"
    caplog.set_level("INFO", logger="rt_hub")
    frame = json.dumps({"t": "msg", "d": {"blob": secret}})
    # ~30s of sustained over-budget traffic: thousands of shed frames.
    for _ in range(3000):
        run(hub.on_message(b, frame))
        clock.advance(0.01)
    assert b.discarded > 2000
    records = [r for r in caplog.records if "throttle" in r.getMessage()]
    assert 1 <= len(records) <= 6, [r.getMessage() for r in records]
    for r in records:
        msg = r.getMessage()
        assert GID in msg
        assert secret not in msg
        assert "blob" not in msg


def test_conns_per_ip_capped(tmp_path):
    make_game(tmp_path, GID, multiplayer={"max_players": 9})
    hub = make_hub(tmp_path, Clock(), max_conns_per_ip=2)
    run(hub.connect(FakeWS(path=f"/rt/{GID}", ip="6.2.0.9")))
    run(hub.connect(FakeWS(path=f"/rt/{GID}", ip="6.2.0.9")))
    ws3 = FakeWS(path=f"/rt/{GID}", ip="6.2.0.9")
    assert run(hub.connect(ws3)) is None
    assert ws3.close_code == rt_hub.CLOSE_CONN_LIMIT


def test_malformed_json_no_raw_error_reflected(tmp_path):
    make_game(tmp_path, GID, multiplayer={"max_players": 2})
    hub = make_hub(tmp_path, Clock())
    a = run(hub.connect(FakeWS(path=f"/rt/{GID}", ip="6.3.0.1")))
    b = run(hub.connect(FakeWS(path=f"/rt/{GID}", ip="6.3.0.2")))
    sent_before = list(a.ws.sent)
    run(hub.on_message(b, "{ this is not json"))
    run(hub.on_message(b, "[1,2,3]"))          # valid json, wrong shape
    assert a.ws.sent == sent_before            # nothing relayed
    assert not b.ws.closed                     # a bad frame is dropped, not fatal


# --------------------------------------------------------------------------
# 1.11  no persistence / no DB
# --------------------------------------------------------------------------

def test_module_opens_no_db():
    src = Path(rt_hub.__file__).read_text(encoding="utf-8")
    assert "import db" not in src
    assert "sqlite3" not in src


def test_session_creates_no_files(tmp_path, monkeypatch):
    games = tmp_path / "games"
    games.mkdir()
    make_game(games, GID, multiplayer={"max_players": 2})
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    hub = make_hub(games, Clock())
    a = run(hub.connect(FakeWS(path=f"/rt/{GID}", ip="7.0.0.1")))
    b = run(hub.connect(FakeWS(path=f"/rt/{GID}", ip="7.0.0.2")))
    run(hub.on_message(a, json.dumps({"t": "join", "nick": "x"})))
    run(hub.on_message(a, json.dumps({"t": "msg", "d": {"k": "v"}})))
    run(hub.ping_round())
    run(hub.disconnect(a))
    run(hub.disconnect(b))
    assert list(work.iterdir()) == []


# --------------------------------------------------------------------------
# real asyncio server + real websockets client (accept vs close code)
# --------------------------------------------------------------------------

def test_vg_rt_client_against_real_hub(tmp_path):
    """3.1 verification: vendor/rt/rt.js, loaded in real headless Chromium and
    pointed at a real rt_hub.py, completes the join handshake, exposes
    VG_RT.me, sees a peer in the roster with a non-null ping_ms, and relays a
    msg between the two clients."""
    pytest.importorskip("playwright.sync_api")
    websockets = pytest.importorskip("websockets")
    import threading
    import time as _time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as p:
            p.chromium.launch().close()
    except Exception:
        pytest.skip("Chromium not installed")

    root = Path(__file__).resolve().parent.parent
    rt_js = (root / "vendor" / "rt" / "rt.js").read_text(encoding="utf-8")
    make_game(tmp_path, GID, multiplayer={"max_players": 4})

    # -- real hub on an ephemeral port -----------------------------------
    hub = make_hub(tmp_path, _time.monotonic, ping_interval_s=1, dead_after_s=30)
    loop = asyncio.new_event_loop()
    hub_port = {}

    async def _boot():
        server = await websockets.serve(
            hub._handler, "127.0.0.1", 0,
            max_size=hub.cfg["max_frame_bytes"], ping_interval=None)
        hub_port["p"] = server.sockets[0].getsockname()[1]
        hub_port["s"] = server
        hub_port["timers"] = asyncio.create_task(hub._timers())

    loop.run_until_complete(_boot())
    hub_thread = threading.Thread(target=loop.run_forever, daemon=True)
    hub_thread.start()

    # -- tiny page + rt.js server --------------------------------------
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path == "/rt.js":
                body = rt_js.encode("utf-8")
                ctype = "text/javascript"
            else:
                body = (
                    f'<!doctype html><meta charset=utf-8><body>'
                    f'<script src="/rt.js" data-game-id="{GID}" '
                    f'data-hub="ws://127.0.0.1:{hub_port["p"]}"></script>'
                ).encode("utf-8")
                ctype = "text/html"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    page_srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    page_srv.daemon_threads = True
    threading.Thread(target=page_srv.serve_forever, daemon=True).start()
    page_url = f"http://127.0.0.1:{page_srv.server_address[1]}/"

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                a = browser.new_page()
                b = browser.new_page()
                a.goto(page_url)
                a.wait_for_function("window.VG_RT && VG_RT.me", timeout=5000)
                a.evaluate("VG_RT.setNick('Ann')")
                b.goto(page_url)
                b.wait_for_function("window.VG_RT && VG_RT.me", timeout=5000)
                b.evaluate("VG_RT.setNick('Bo')")

                # both see two members
                a.wait_for_function("VG_RT.roster.length === 2", timeout=5000)
                b.wait_for_function("VG_RT.roster.length === 2", timeout=5000)

                # a non-null ping_ms shows up after a ping round or two
                a.wait_for_function(
                    "VG_RT.roster.some(m => m.ping_ms !== null)", timeout=8000)

                # msg relay a -> b
                b.evaluate("window.__got = null; VG_RT.on('msg', e => window.__got = e)")
                a.evaluate("VG_RT.send({hello: 'world'})")
                b.wait_for_function("window.__got && window.__got.d.hello === 'world'",
                                    timeout=5000)
                assert b.evaluate("window.__got.from") == a.evaluate("VG_RT.me")
            finally:
                browser.close()
    finally:
        page_srv.shutdown()

        async def _shutdown():
            hub_port["timers"].cancel()
            hub_port["s"].close()
            await hub_port["s"].wait_closed()

        fut = asyncio.run_coroutine_threadsafe(_shutdown(), loop)
        try:
            fut.result(timeout=5)
        except Exception:
            pass
        loop.call_soon_threadsafe(loop.stop)
        hub_thread.join(timeout=5)
        loop.call_soon_threadsafe(loop.close)


def test_real_server_accept_and_close_codes(tmp_path):
    websockets = pytest.importorskip("websockets")
    from websockets.asyncio.client import connect as ws_connect
    from websockets.exceptions import ConnectionClosed

    make_game(tmp_path, GID, multiplayer={"max_players": 2})

    async def scenario():
        hub = make_hub(tmp_path, __import__("time").monotonic, host="127.0.0.1", port=0)
        async with await websockets.serve(
            hub._handler, "127.0.0.1", 0, max_size=hub.cfg["max_frame_bytes"],
            ping_interval=None,
        ) as server:
            port = server.sockets[0].getsockname()[1]
            # good id: handshake completes, welcome arrives
            async with ws_connect(f"ws://127.0.0.1:{port}/rt/{GID}") as c:
                welcome = json.loads(await c.recv())
                assert welcome["t"] == "welcome"
            # bad id: closed with 4003
            c = await ws_connect(f"ws://127.0.0.1:{port}/rt/bad-id")
            with pytest.raises(ConnectionClosed) as ei:
                await c.recv()
            assert ei.value.rcvd.code == rt_hub.CLOSE_BAD_GAME_ID
        await asyncio.sleep(0)

    run(scenario())


# --------------------------------------------------------------------------
# fix-multiplayer-rate-limit-disconnects 5.2 — the direct regression test for
# the reported failure: two real players in one room, both syncing state above
# the soft budget, both watching the game flip back to "waiting for player 2".
# --------------------------------------------------------------------------

def test_two_over_budget_members_both_stay_in_the_roster(tmp_path):
    make_game(tmp_path, GID, multiplayer={"max_players": 2})
    clock = Clock()
    hub = make_hub(tmp_path, clock, max_msg_per_sec=60, max_msg_burst_per_sec=240,
                   dead_after_s=15, ping_interval_s=5)
    a = run(hub.connect(FakeWS(path=f"/rt/{GID}", ip="7.0.0.1")))
    b = run(hub.connect(FakeWS(path=f"/rt/{GID}", ip="7.0.0.2")))

    frame = json.dumps({"t": "msg", "d": {"y": 0.5}})
    ping_due = clock.t + 5.0
    # a's very first roster listed only itself (it joined first); everything
    # from here on must name both.
    base = {m.id: len(m.ws.sent) for m in (a, b)}
    # Ten seconds of both clients sending 70/sec — the measured rate of the
    # reported game, above the 60/sec soft budget and far below the ceiling.
    for step in range(700):
        run(hub.on_message(a, frame))
        run(hub.on_message(b, frame))
        clock.advance(1.0 / 70.0)
        if clock.t >= ping_due:
            run(hub.ping_round())
            for m in (a, b):
                run(hub.on_message(m, json.dumps(
                    {"t": "pong", "seq": m.ws.last("ping")["seq"]})))
            ping_due = clock.t + 5.0
        # Continuously, not just at the end: neither socket is ever closed and
        # the room never drops below two members.
        assert not a.ws.closed and not b.ws.closed, step
        assert set(hub.rooms[GID].members) == {a.id, b.id}, step

    # And every roster either side saw named both of them throughout.
    for m in (a, b):
        later = [json.loads(x) for x in m.ws.sent[base[m.id]:]]
        rosters = [f for f in later if f["t"] == "roster"]
        assert rosters
        for r in rosters:
            assert {e["id"] for e in r["members"]} == {a.id, b.id}
    # Both were throttled — the shedding really did happen, in place of a close.
    assert a.discarded > 0 and b.discarded > 0
