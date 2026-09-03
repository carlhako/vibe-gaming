## ADDED Requirements

### Requirement: Injected client never writes to a non-open socket

The injected `VG_RT` client SHALL NOT call `WebSocket.send()` unless the
socket's `readyState` is OPEN. The client's connected/roster state MAY lag
the socket (it is updated from the `close` event, which is delivered
asynchronously), so a truthiness check on an internal "connected" flag or on
the socket reference is NOT sufficient. Both the game-facing send path
(`VG_RT.send()`) and the client's internal control frames (join, pong) SHALL
apply this check. A send attempted while the socket is not OPEN SHALL be
dropped silently: `VG_RT.send()` SHALL return a falsy value and SHALL NOT
raise, and no console error SHALL be produced by the client for this case.

Rationale: `WebSocket.send()` on a CLOSING or CLOSED socket does not throw —
the browser logs `WebSocket is already in CLOSING or CLOSED state.` at error
level — so a `try/catch` around the call does not suppress it, and the
generation smoke test fails on any console error.

#### Scenario: Game sends during the closing window

- **WHEN** the hub (or smoke stub) has begun closing the socket but the client's `close` handler has not yet run
- **AND** game code calls `VG_RT.send(value)`
- **THEN** the client does not call `WebSocket.send()`, returns a falsy value, raises nothing, and logs no console error

#### Scenario: Game sends while connected

- **WHEN** the socket `readyState` is OPEN
- **AND** game code calls `VG_RT.send(value)` with a payload within the size cap
- **THEN** the client sends the framed message and returns a truthy value

#### Scenario: Internal control frame after close

- **WHEN** the socket is CLOSING or CLOSED
- **AND** the client would otherwise emit a `join` or `pong` frame
- **THEN** it does not call `WebSocket.send()` and produces no console error

### Requirement: Smoke WS stub holds the connection open

The `smoke_test.py` WebSocket stub SHALL, after completing the handshake and
sending its `welcome` and `roster` frames, keep the connection open without
sending a server-initiated close frame. The connection SHALL remain open
until the smoke HTTP server is shut down at the end of the smoke run, at
which point the socket is torn down as part of server teardown. The stub
SHALL NOT block or delay smoke-server shutdown.

Rationale: an immediate server close forces the injected client into its
reconnect-with-backoff loop for the duration of the settle window, and each
reconnect re-opens a socket that is then closed again — multiplying any
send-during-closing console errors and making the smoke result
non-deterministic. A held-open socket also matches how the real `rt_hub.py`
behaves.

#### Scenario: Multiplayer game smoke-tests solo without reconnect churn

- **WHEN** a multiplayer game is generated and the smoke test runs it alone
- **THEN** the game's WebSocket connects once, receives `welcome` + `roster`, stays open for the settle window, and the attempt is not failed for that connection

#### Scenario: Smoke server tears down cleanly

- **WHEN** the smoke run finishes with a stub WebSocket still open
- **THEN** the smoke server shuts down within its existing join timeout and the stub does not hang the run

#### Scenario: Egress to a third-party host still fails

- **WHEN** a game under smoke test opens a WebSocket or request to a host outside the allowlist and the serving origin
- **THEN** the smoke test still fails the attempt
