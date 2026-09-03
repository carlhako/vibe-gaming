## Why

Every game on Vibegames is single-player. The security model — a sandboxed
`<iframe>` with an opaque origin plus `connect-src 'self'` and a smoke-test
egress check — exists specifically to stop untrusted AI-generated games from
reaching the network, which is also exactly what real-time multiplayer needs.
This change opens one controlled, first-party hole in that boundary so games
can opt into shared real-time rooms (2+ players seeing each other move),
without giving any game a general path to the network and without new server
compromise surface beyond a single hardened relay process.

## What Changes

- **New standalone realtime hub process** (`rt_hub.py`, asyncio WebSocket
  server, its own systemd unit like `job_runner`) reverse-proxied at `/rt/`.
  It is a game-agnostic dumb relay: it owns room membership, per-game
  capacity, a presence roster, and server-measured ping; it never inspects
  or simulates game payloads and persists nothing.
- **One auto-room per game**, keyed by the validated `game_id`. Capacity is
  read server-side from the game's `meta.json` (`multiplayer.max_players`),
  so a tampered client cannot exceed it. Joins/leaves broadcast a roster
  update `[{id, nick, ping_ms}]` to the room.
- **Platform-injected `VG_RT` client.** A small helper (served from
  `/vendor/rt/`, added to the game CSP the same way the three.js vendor
  prefix is) owns the WebSocket URL, reconnect, heartbeat, message framing,
  and size cap. Generated games call `VG_RT.send()` / `VG_RT.on(...)` and
  never hand-roll WebSocket code — mirroring how `engines.normalize()`
  injects the import map rather than trusting the model to write it.
- **`safety.game_csp()` gains an explicit `connect-src` entry** naming the
  serving origin with `wss:`/`https:` (an opaque-origin document cannot rely
  on `'self'`, same reasoning already documented for `script-src` + the
  vendor prefix). No other host is added.
- **`smoke_test.py` tolerates a WebSocket connection to the serving origin**
  so a multiplayer game does not fail generation merely for connecting; the
  smoke server answers with a minimal stub so the game reaches its
  solo/waiting state.
- **Opt-in at generation.** `new_game.html` gets a "multiplayer" toggle and a
  max-players number; both are written to `meta.json`
  (`"multiplayer": {"max_players": N}`) and injected into the generation
  prompt. Only a game with that block gets a hub room.
- **Prompt contract additions**: a multiplayer game must render a
  playable/waiting state when alone, and must treat peer messages as
  untrusted data (no `eval`, prefer `textContent` over `innerHTML`).
- **Hub hardening**: memory-safe WS library, capped frame size, strict JSON
  parse, server-derived room keys, connection and message-rate caps, idle
  room GC, no DB write handle. Framed as keeping the process up, not
  anti-abuse.

## Capabilities

### New Capabilities

- `multiplayer-hub`: The standalone realtime relay service — WebSocket
  endpoint, room model keyed by `game_id`, server-authoritative capacity
  from `meta.json`, presence roster, server-measured ping, the wire
  protocol between client and hub, and the process-hardening limits.
- `multiplayer-integration`: How a sandboxed game reaches the hub — the
  injected `VG_RT` client and its API, the `game_csp()` `connect-src`
  allowance, the `smoke_test.py` WebSocket stub, the `meta.json`
  `multiplayer` block, the `new_game.html` opt-in controls, and the
  generation-prompt contract for multiplayer games.

### Modified Capabilities

<!-- None — there are no existing specs under openspec/specs/. The CSP and
     smoke-test changes are covered as requirements of multiplayer-integration. -->

## Impact

- **New files**: `rt_hub.py`, a systemd unit / process entry, `vendor/rt/rt.js`
  (the `VG_RT` client), config block under `rt_hub:` in `config.yaml.example`.
- **Modified**: `safety.py` (`game_csp()` `connect-src`; possibly a
  `multiplayer.normalize()`-style injection point), `smoke_test.py` (WS stub
  + egress exemption), `app.py` (`/vendor/rt/` route with
  `Access-Control-Allow-Origin: *` like `/vendor/three/`; serve/read of the
  `meta.json` `multiplayer` block), `templates/new_game.html`,
  `game_generator.py` / prompt text, `meta.json` schema, `README.md`
  (new process to run, reverse-proxy note).
- **Deployment**: one more long-running process; reverse proxy must route
  `/rt/` to the hub and everything else to Flask. Local dev runs it
  alongside `python3 app.py`.
- **Out of scope / non-goals**: no WebRTC / peer-to-peer; no persistent
  player identity or parent→iframe token handshake (sockets are anonymous,
  nicknames are client-declared and display-only); no matchmaking or named
  rooms (one room per game); no multiplayer for downloaded games (no origin,
  no parent); no server-side game simulation or anti-cheat; enhancing an
  existing single-player game into a multiplayer one is not targeted in this
  change.
