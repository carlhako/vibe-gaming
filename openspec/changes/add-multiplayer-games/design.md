## Context

See `proposal.md` — Why. The load-bearing constraints from the current
codebase:

- Games are served at `/play/<slug>` as one `index.html` inside
  `<iframe sandbox="allow-scripts allow-pointer-lock">` with **no
  `allow-same-origin`** → opaque origin. `allow-scripts` alone already
  permits `WebSocket`; no sandbox attribute change is needed.
- `safety.game_csp(origin)` currently sets `connect-src 'self'`. For an
  opaque-origin document `'self'` matches nothing, which is why games
  cannot reach the network today. The same file already documents this
  exact problem for `script-src` and solves it by naming the origin with a
  path prefix (`/vendor/three/`).
- `smoke_test.py` serves the game from a throwaway `127.0.0.1` origin under
  the real CSP and fails the attempt on any request to a host outside
  `ALLOWED_CDN_HOSTS`. Its `_blocked_host()` exempts the smoke origin by
  **exact origin string**, so a `ws://` URL to the same host:port does not
  match (`ws://` vs `http://`) and would be reported.
- `safety.scan()` has no pattern for `new WebSocket` — it gates on
  `src`/`href`/`url()` hosts only. Runtime-built WS URLs were never in its
  scope; the CSP and smoke test are the controls that matter here.
- Flask app is sync under gunicorn; `job_runner.py` is already a separate
  polling process. `engines.normalize()` injects the three.js import map
  into served HTML rather than trusting the model to write it.

## Goals / Non-Goals

**Goals (design-level):**

- Add real-time rooms with the smallest possible new attack surface: one
  hardened relay process, one new CSP token, one smoke-test allowance.
- Keep the hub completely game-agnostic — it never needs to know what any
  game is, so it never needs updating when games change.
- Make the client-side integration follow the existing "platform injects
  it, model calls an API" pattern so generated games can't mis-implement
  transport.

**Non-Goals (design-level):**

- No shared-scope or byte-exact-cache contract like `agent.py` has — the
  hub is a new process with no model conversation.
- No change to how slugs, forks, `game_id`, or the disk scan work beyond
  reading one new `meta.json` key.
- No reuse of the `job_runner` DB-polling model — the hub holds no queue
  and touches no table.

## Decisions

### D1: Standalone asyncio process, reverse-proxied at `/rt/`

**Choice:** `rt_hub.py` runs as its own process (asyncio, `websockets` or
Starlette), managed by its own systemd unit in production and started
alongside `python3 app.py` in dev. The reverse proxy routes `/rt/` to the
hub and everything else to Flask.

**Why over alternatives:**

- *Flask-SocketIO + eventlet/gevent worker* — forces monkey-patching the
  whole app and its blocking DB calls; turns every gunicorn worker into a
  carrier of untrusted relay traffic. Rejected.
- *uvicorn ASGI worker hosting a sub-app in the same gunicorn* — couples
  hub lifecycle and crash domain to the web tier. Rejected.
- Separate process mirrors the existing `job_runner` split: independent
  crash domain, independent resource limits, and the untrusted socket
  traffic never lands on a web worker. A hub crash cannot take down
  `/play/` or generation.

**Shared state:** the hub needs `max_players` per game. It reads it from
`games/<slug>/meta.json` on the shared filesystem (read-only), resolving
`game_id → slug` via the same disk manifest the app uses, cached with an
mtime check. No DB handle. Config lives under a new `rt_hub:` block in
`config.yaml.example` (bind host/port, per-connection limits, ping
interval, idle-room TTL).

### D2: Anonymous sockets, client-declared nicknames

**Choice:** a game connects straight to `wss://<host>/rt/<game_id>` with no
token. Identity is a hub-assigned ephemeral member id plus a nickname the
client declares in its join message. The nickname is display-only and
always treated as untrusted data.

**Why:** the opaque-origin sandbox means the iframe cannot read the
`vg_uid` cookie, and a credential-less cross-origin request from `Origin:
null` cannot carry it either. Real identity would require a parent →
iframe `postMessage` token handshake. Per the user, abuse of the relay is
not a concern for this casual platform; the two real concerns (server
compromise, malicious content reaching clients) are addressed by D5 and D6
and do not need identity. Parent-issued tokens remain a clean phase-2 add
for persistent players / multiplayer leaderboards.

### D3: One implicit room per game, server-derived key

**Choice:** room key = the validated `game_id`, computed hub-side. No
client string contributes to it. Room created on first join, destroyed
empty. No named rooms, no matchmaking.

**Why:** smallest model that satisfies "2+ players see each other." Named
rooms/matchmaking add a client-supplied identifier (injection surface) and
lifecycle complexity for no v1 benefit. Games that want private matches
can layer a room code inside their opaque payloads later without any hub
change — or it becomes a deliberate phase-2 capability.

### D4: Server-authoritative capacity from `meta.json`

**Choice:** the hub reads `multiplayer.max_players` from `meta.json` and
refuses the connection that would exceed it (close code distinguishable as
"full"). No message from a client can change capacity. A `game_id` with no
`multiplayer` block is refused outright.

**Why:** the cap is a property of the game's design, fixed at authoring
time, and must survive a tampered client — so it lives on the server side
of the boundary, read from the same file that already carries `engine`.

### D5: Hub hardening = process stability, not message policing

**Choice:** per-connection limits — max frame size (drop without full
parse), max message rate, max connections per IP; strict JSON parse for
protocol frames with no raw-error reflection; server-derived room keys;
idle-room GC; unprivileged process, no DB write access. The hub does
**not** inspect, validate, moderate, or rate-limit *payload contents*.

**Why:** the relay-as-message-bus concern is explicitly out of scope. What
remains is "don't let untrusted frames crash or exhaust the process,"
which is a finite, testable checklist. Payload semantics stay the game's
problem, contained by the sandbox (D6).

### D6: "Malicious content to clients" is already contained by the sandbox

**Analysis, not new mechanism:** peers in a room run *identical*,
already-`safety.scan()`'d + smoke-tested game code. A peer can inject
**data**, never new code. If a game renders peer data as markup with a
link, the existing sandbox (`allow-scripts allow-pointer-lock`, i.e. no
`allow-top-navigation`, no `allow-popups`, no `allow-same-origin`) plus the
unchanged CSP (`connect-src` still only the serving origin) plus
`safety.py`'s ban on `location.href/.assign` mean the worst outcome is
garbage drawn on a peer's canvas. The only additive control is a **prompt
contract** line (spec: multiplayer-integration) telling the model to treat
peer messages as untrusted — belt-and-suspenders, not the primary
boundary.

### D7: `VG_RT` client injected like the three.js import map

**Choice:** ship `vendor/rt/rt.js`; serve it from `/vendor/rt/<path>` with
`Access-Control-Allow-Origin: *` (sandbox sends `Origin: null`, exactly as
`/vendor/three/` already handles); add the `/vendor/rt/` prefix to
`script-src` in `game_csp()` alongside the three vendor prefix. A
normalization step (a `multiplayer.normalize()` sibling to
`engines.normalize()`, or an extension of it) strips any copy of the
client tag the model emitted and inserts the canonical one before
`</head>` for multiplayer games only; idempotent by construction so a
single-file enhance that echoes it back stays at one copy.

**Why:** identical rationale to the import map — a resubmitted enhance puts
the injected code back in front of the model, and hand-rolled transport
code in generated games would be un-versionable and frequently broken. The
game calls `VG_RT.send()` / `VG_RT.on('roster'|'peers'|'msg', …)` /
`VG_RT.me`; the wire protocol can change without regenerating a single
game.

### D8: Wire protocol

JSON text frames, two namespaces:

```
  client -> hub
    { "t": "join", "nick": "<string>" }        once, right after open
    { "t": "pong", "seq": <n> }                 echo of hub ping
    { "t": "msg",  "d": <any JSON> }            opaque game payload

  hub -> client
    { "t": "welcome", "id": "<member-id>", "max": <n> }
    { "t": "roster", "members": [ { "id", "nick", "ping_ms" }, ... ] }
    { "t": "ping", "seq": <n>, "ts": <server-ms> }
    { "t": "msg", "from": "<member-id>", "d": <any JSON> }
    close 4001 "room full" | 4002 "not a multiplayer game" | 4003 "bad game_id"
```

`d` is never read by the hub. Frame size cap applies to the whole frame,
so `d` is bounded. `VG_RT` mirrors the cap client-side and rejects
oversized `send()` locally.

## Risks / Trade-offs

- **`'self'`/opaque-origin CSP matching is subtle** → name the serving
  origin explicitly for both `wss:` and `https:` in `connect-src`, exactly
  as `script-src` already does for the vendor prefix; add a smoke-style
  assertion that a real served multiplayer game's socket actually opens.
- **Smoke server must now speak a WS handshake** → keep the stub minimal:
  accept the upgrade, send `welcome` + an empty `roster`, answer `pong` to
  nothing (no ping needed), ignore `msg`. Enough for the game to hit its
  waiting state; anything more is scope creep.
- **Reverse-proxy misroute** (e.g. `/rt/` falling through to Flask, or the
  hub reachable without the proxy) → document the exact proxy rule in
  `README.md`; hub binds loopback in production so only the proxy reaches
  it.
- **`meta.json` read races the generation write** → hub resolves + reads
  lazily on connect with an mtime-checked cache and treats a missing/
  unparseable file as "not multiplayer" (refuse), never as a crash.
- **A multiplayer game that genuinely can't render solo** would fail smoke
  → the prompt contract makes solo/waiting a hard requirement; the smoke
  stub gives it a real (empty) roster so "waiting for players" is the
  natural render.
- **Process supervision** → the hub needs restart-on-crash (systemd
  `Restart=on-failure`); until that unit exists, a crash means multiplayer
  is down site-wide even though single-player is fine. Acceptable for the
  casual platform; note it in the migration steps.

## Migration Plan

1. Land `rt_hub.py` + config + `vendor/rt/rt.js` + the `VG_RT` normalizer,
   all inert while no game has a `multiplayer` block.
2. Land `game_csp()` `connect-src` change and the `/vendor/rt/` route +
   `script-src` prefix. Existing games unaffected (no game connects).
3. Land `smoke_test.py` WS stub + egress exemption. Re-run the existing
   game corpus through smoke to confirm no regression.
4. Land `new_game.html` controls + `meta.json` schema + prompt contract.
5. Deploy: add the systemd unit, add the `/rt/` proxy rule, start the hub.
6. Generate one 2-player test game end to end; verify two browsers see each
   other's roster and ping.

**Rollback:** stop the hub process and revert the `/rt/` proxy rule —
multiplayer games degrade to their solo/waiting state, everything else is
untouched. The CSP/smoke/form changes are backward-compatible and can stay.

## Open Questions

- **Spectator overflow vs hard refuse** when a room is full — spec allows
  either; default to hard refuse (close 4001) for v1 unless a test game
  shows spectators are trivially free.
- **Ping cadence and disconnect timeout** exact values — pick sane
  defaults in `config.yaml.example` (e.g. 5 s ping, 15 s dead) during
  implementation; does not affect specs or task breakdown.
- **Where the `VG_RT` normalizer lives** — extend `engines.normalize()` vs
  a new `multiplayer.normalize()` called from the same site in
  `run_generation_attempts()`. Implementation detail; both satisfy the
  idempotency spec.
