## Purpose

Defines how a sandboxed, AI-generated game opts into multiplayer and reaches
the hub: the injected `VG_RT` client and its API, the platform allowances
(game CSP, smoke test) that permit exactly that one connection, the
`meta.json` opt-in block, the new-game form controls, and the generation
prompt contract multiplayer games must satisfy.

## ADDED Requirements

### Requirement: meta.json multiplayer opt-in block

A game SHALL be treated as multiplayer if and only if its `meta.json`
contains a `multiplayer` object with an integer `max_players` of at least
2. The block SHALL be written at generation time from the new-game form
and SHALL be inherited unchanged by every fork/enhancement of that game,
the same way the `engine` field is. Absence of the block means the game
is single-player and gets no hub room.

#### Scenario: Multiplayer game recognized

- **WHEN** a game's `meta.json` has `"multiplayer": {"max_players": 4}`
- **THEN** the platform treats it as multiplayer and the hub will create a room capped at 4

#### Scenario: Fork inherits the block

- **WHEN** a multiplayer game is enhanced/forked
- **THEN** the new game's `meta.json` carries the same `multiplayer` block without the model being asked to reproduce it

#### Scenario: Invalid max_players

- **WHEN** `max_players` is missing, non-integer, or less than 2
- **THEN** the game is treated as single-player

### Requirement: New-game form controls

`new_game.html` SHALL offer a control to mark a new game as multiplayer and
a control to set its maximum player count. When multiplayer is not
selected, no `multiplayer` block is written. These controls MAY be
disabled when AI generation is disabled, consistent with the existing
engine controls.

#### Scenario: Author opts in

- **WHEN** the author enables multiplayer and sets max players to 3 on the form
- **THEN** the generated game's `meta.json` contains `"multiplayer": {"max_players": 3}`

#### Scenario: Author does not opt in

- **WHEN** the author leaves multiplayer unselected
- **THEN** the generated `meta.json` has no `multiplayer` block

### Requirement: Injected VG_RT client

The platform SHALL inject a real-time client into every multiplayer game's
served HTML; the model SHALL NOT write WebSocket code. Injection follows
the existing engine pattern: the client is served from a site path under
`/vendor/` (with `Access-Control-Allow-Origin: *`, since a sandboxed game
sends `Origin: null`), and its presence in served HTML is normalized by
the platform (inserted if absent, deduplicated if the model echoes it
back on a single-file enhance). A non-multiplayer game SHALL NOT receive
the client.

The client SHALL expose a stable API to game code covering at least:
send a game payload to the room; subscribe to roster updates; subscribe
to incoming peer payloads; read the local member's own id. The client
SHALL own the hub URL derivation (the game never hard-codes an origin),
reconnect with backoff, the ping echo, message framing, and enforcement
of the same maximum payload size the hub enforces.

#### Scenario: Client present for multiplayer game

- **WHEN** a multiplayer game's HTML is served
- **THEN** it contains exactly one copy of the injected `VG_RT` client regardless of what the model submitted

#### Scenario: Client absent for single-player game

- **WHEN** a single-player game's HTML is served
- **THEN** no `VG_RT` client is injected

#### Scenario: Game uses the API, not raw WebSocket

- **WHEN** a multiplayer game needs to send state to peers
- **THEN** it calls the `VG_RT` send API and receives peer state via the `VG_RT` subscription API

#### Scenario: Idempotent normalization

- **WHEN** a single-file multiplayer game is enhanced and the model resubmits HTML that already includes the injected client
- **THEN** normalization leaves exactly one copy present

### Requirement: Game CSP permits the hub connection only

`safety.game_csp()` SHALL emit a `connect-src` directive that names the
serving origin explicitly for `wss:` and `https:` (an opaque-origin
document cannot rely on `'self'`), and SHALL NOT add any other host. All
other directives remain as they are. The static safety scan SHALL continue
to reject external `src`/`href`/`url()` hosts that are not on the CDN
allowlist.

#### Scenario: Hub connection allowed

- **WHEN** a served multiplayer game opens a WebSocket to the serving origin's `/rt/` path
- **THEN** the browser permits the connection under the game CSP

#### Scenario: Arbitrary egress still blocked

- **WHEN** a game attempts a `fetch` or WebSocket to any host other than the serving origin
- **THEN** the game CSP blocks it

### Requirement: Smoke test tolerates the hub connection

`smoke_test.py` SHALL NOT fail a generation attempt merely because the game
opens a WebSocket to the smoke server's own origin. The smoke server SHALL
answer that connection with a minimal stub sufficient for the game to reach
its solo/waiting state. A connection or request to any other host SHALL
still fail the attempt as it does today.

#### Scenario: Multiplayer game smoke-tests solo

- **WHEN** a multiplayer game is generated and the smoke test runs it alone
- **THEN** the game connects to the stub, renders its waiting/solo state, and the attempt is not failed for that connection

#### Scenario: Egress to a third-party host still fails

- **WHEN** a game under smoke test connects to a host outside the allowlist and the serving origin
- **THEN** the smoke test fails the attempt

### Requirement: Generation prompt contract for multiplayer games

When a game is generated with the multiplayer opt-in, the generation prompt
SHALL instruct the model that: the game must render a playable or explicit
waiting state when it is the only member of the room; peer messages are
untrusted data that must not be passed to `eval` or used to build markup
(`textContent` over `innerHTML`); and real-time networking must go through
the provided `VG_RT` API rather than a hand-written WebSocket.

#### Scenario: Solo render

- **WHEN** a multiplayer game loads and no peers are present
- **THEN** it shows a waiting or single-player-playable screen rather than hanging or erroring

#### Scenario: Peer data handled as untrusted

- **WHEN** a peer sends a payload containing markup or a URL
- **THEN** the generated game does not execute it or inject it as HTML
