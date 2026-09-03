## 1. Realtime hub process

- [x] 1.1 Add `rt_hub:` block to `config.yaml.example` (bind host/port, max_frame_bytes, max_msg_per_sec, max_conns_per_ip, ping_interval_s, dead_after_s, idle_room_ttl_s) and verify `yaml.safe_load` parses it in a unit test.
- [x] 1.2 Create `rt_hub.py` with an asyncio WebSocket server that accepts `/rt/<game_id>`, validates `game_id` against the site's uuid4-hex regex, and closes 4003 on a malformed value — verify with a test client asserting accept vs close code.
- [x] 1.3 Implement `game_id -> slug -> meta.json` resolution with an mtime-checked read cache; return `None`/"not multiplayer" for missing, unparseable, or block-less `meta.json`. Verify with unit tests over temp game dirs (valid block, no block, corrupt file).
- [x] 1.4 Implement the room model: lazily created keyed by validated `game_id`, server-derived key only, destroyed when empty, GC of idle rooms after `idle_room_ttl_s`. Verify with a test that joins/leaves and asserts room dict is empty afterward.
- [x] 1.5 Enforce capacity from `meta.json.multiplayer.max_players`: refuse the over-capacity connection with close 4001, refuse a non-multiplayer `game_id` with close 4002, ignore any client message that tries to change capacity. Verify with tests for each close code.
- [x] 1.6 Implement the wire protocol from design.md D8 (`join`/`pong`/`msg` in; `welcome`/`roster`/`ping`/`msg` out), including `welcome` + initial roster on join. Verify with a two-client test asserting exact frame shapes.
- [x] 1.7 Broadcast a roster on every membership change; nickname stored/relayed verbatim as untrusted data. Verify with a test that a joining and a leaving client each trigger a roster to all members, and a markup nickname round-trips unmodified.
- [x] 1.8 Implement server-measured ping: periodic `ping{seq,ts}`, compute RTT from hub clock on `pong`, include `ping_ms` (null until measured) in roster; drop a member that misses echoes past `dead_after_s` and re-broadcast. Verify with a test using a fake clock.
- [x] 1.9 Implement opaque `msg` relay: fan out `d` byte-for-byte to other room members only (no self-echo), never parse/transform/persist `d`. Verify with a test sending a payload containing a URL and script-like text and asserting peers get it unchanged and the hub took no action.
- [x] 1.10 Enforce process-stability limits: drop frames over `max_frame_bytes` without full parse, throttle/disconnect over `max_msg_per_sec`, cap `max_conns_per_ip`, strict JSON parse for protocol frames with no raw-error text reflected on reject. Verify with tests for oversized frame, flood, and malformed-JSON cases.
- [x] 1.11 Confirm the hub opens no DB connection and writes no game/player data to disk or logs — verify by grepping the module and a test asserting no files are created under a temp cwd during a session.

## 2. Platform allowances for the hub connection

- [x] 2.1 Change `safety.game_csp()` `connect-src` to name the serving origin explicitly for `wss:` and `https:` (no other host); update the module docstring reasoning. Verify existing `safety` tests pass and add one asserting the directive contains the origin and no third-party host.
- [x] 2.2 Add `GET /vendor/rt/<path:filename>` in `app.py` serving `vendor/rt/` with `Access-Control-Allow-Origin: *` and a long immutable cache header, mirroring `/vendor/three/`. Verify with a route test for headers and a 404 on path traversal.
- [x] 2.3 Add the `/vendor/rt/` prefix to `script-src` in `game_csp()` alongside the three vendor prefix. Verify with a `safety` test.
- [x] 2.4 Extend `smoke_test.py`: `_blocked_host()` (or the request handler) exempts a `ws://`/`wss://` URL to the smoke server's own host:port; add a minimal WS stub to `_SmokeHandler`/server that accepts the upgrade and sends `welcome` + empty `roster`. Verify with a unit test of `_blocked_host` and a smoke run of a tiny game that opens a socket to `/rt/`.
- [x] 2.5 Verify egress is still blocked: a `safety` + smoke test that a game connecting to any host other than the serving origin still fails.

## 3. VG_RT injected client

- [x] 3.1 Write `vendor/rt/rt.js` exposing `VG_RT.send(d)`, `VG_RT.on('roster'|'peers'|'msg'|'welcome', cb)`, `VG_RT.me`; it derives the hub URL from `location`, does the `join` handshake, reconnect with backoff, `ping`->`pong` echo, framing, and a client-side max payload size matching the hub. Verify with a headless test pointing it at the real `rt_hub.py`.
- [x] 3.2 Add a normalization step (extend `engines.normalize()` or a new `multiplayer.normalize()` called from `run_generation_attempts()`) that, for multiplayer games only, strips any model-emitted copy of the client tag and inserts the canonical `<script src="/vendor/rt/rt.js">` before `</head>`; idempotent. Verify with unit tests: inserted when absent, deduped when the model echoes it, untouched for single-player games.
- [x] 3.3 Wire the normalizer into the generation pipeline at the same point `engines.normalize()` runs (before `safety.scan()` so the scan sees final bytes). Verify with a pipeline test that a generated multiplayer game's served HTML has exactly one client tag.

## 4. Authoring opt-in

- [x] 4.1 Define the `meta.json` `multiplayer` block (`{"max_players": int>=2}`); add a helper (e.g. `builder.read_multiplayer()` / in `db` disk manifest) that returns it or `None`, with invalid/`<2`/non-int treated as `None`. Verify with unit tests.
- [x] 4.2 Ensure fork/enhance copies the `multiplayer` block unchanged, the same code path that carries `engine`. Verify with a `game_enhancer` / `agent` test that a forked multiplayer game's `meta.json` still has the block without the model reproducing it.
- [x] 4.3 Add the multiplayer toggle + max-players input to `templates/new_game.html`, disabled when AI is disabled (consistent with the engine fieldset). Verify by rendering the template in a test and asserting the controls exist and gate correctly.
- [x] 4.4 Thread the form values through `/games/new` into the generation `config`/request and write the `multiplayer` block into `meta.json` on success; no block when unselected. Verify with a route + generator test for both branches.
- [x] 4.5 Add the multiplayer prompt-contract text to the generation prompt when the opt-in is set: solo/waiting render required, peer messages are untrusted (no `eval`, `textContent` over `innerHTML`), use `VG_RT` not raw WebSocket. Verify with a test asserting the prompt string contains these clauses only when multiplayer is requested.

## 5. Integration, docs, deploy

- [x] 5.1 End-to-end: generate a 2-player game through the real pipeline (mocked AI returning a canned multiplayer game), run it under smoke, and assert it reaches the waiting state and passes. Verify via the new integration test.
- [x] 5.2 Manual/scripted two-client check against a locally running `rt_hub.py`: two browser contexts load the same game, each sees the other in the roster with a non-null `ping_ms`, a `msg` from one appears on the other, and a disconnect updates the roster. Record the steps in the change or `docs/`.
- [x] 5.3 Update `README.md`: how to run `rt_hub.py` locally alongside `app.py`, the `rt_hub:` config block, and the reverse-proxy rule routing `/rt/` to the hub (hub binds loopback in production). Verify by following the README on a clean checkout.
- [x] 5.4 Add a production process entry (systemd unit or equivalent) for the hub with restart-on-failure; document rollback (stop hub + revert `/rt/` proxy rule → multiplayer games degrade to solo, nothing else affected). Verify the unit starts the hub and restarts it after a kill.
- [x] 5.5 Update `CLAUDE.md` with a short "Multiplayer games" section: the hub is a separate game-agnostic process, opt-in via `meta.json.multiplayer`, `VG_RT` is platform-injected like the import map, CSP/smoke allowances name only the serving origin. Verify by review.
- [x] 5.6 Run `openspec validate add-multiplayer-games --strict` and the full `pytest` suite; verify both pass.
