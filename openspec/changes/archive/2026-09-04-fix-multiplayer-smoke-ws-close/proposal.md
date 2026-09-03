## Why

A newly generated multiplayer game fails the generation smoke test with a
burst of `console.error: WebSocket is already in CLOSING or CLOSED state.`
The smoke WS stub sends `welcome` + `roster` and then immediately closes the
socket; `vendor/rt/rt.js` guards `send()`/`sendRaw()` on `api.connected` and
`ws` truthiness, both of which lag the socket's real state, so any game that
calls `VG_RT.send()` on a tick loop keeps calling `ws.send()` during the
window between the socket entering CLOSING and rt.js's `onclose` handler
running. `ws.send()` on a CLOSING/CLOSED socket does not throw — Blink logs
the message at error level — so rt.js's `try/catch` catches nothing and the
smoke test records 10 console errors and fails the attempt. The stub's
immediate close also drives an rt.js reconnect storm across the settle
window, producing a fresh burst on each cycle. Result: multiplayer games
whose sync loop uses `VG_RT.send()` (the common case) cannot pass generation.

## What Changes

- `vendor/rt/rt.js`: `send()` and `sendRaw()` gate on
  `ws.readyState === WebSocket.OPEN` in addition to the existing checks, so a
  socket that has entered CLOSING/CLOSED is never written to. This is
  platform-injected code, so it repairs every existing and future
  multiplayer game with no regeneration.
- `smoke_test.py`: the WS stub keeps the connection open after sending
  `welcome` + `roster` (no server-initiated close frame) until the smoke
  server is torn down at the end of the run. This removes the CLOSING-window
  race and the reconnect storm, and matches how the real `rt_hub.py` behaves
  (a hub holds sockets open).
- `tests/test_multiplayer_e2e.py`: add a fixture game that calls
  `VG_RT.send()` on a `requestAnimationFrame` loop so this regression is
  covered — the existing fixture only repaints on `roster`/`welcome` and
  never sends, which is why the suite was green while real games failed.

## Capabilities

### New Capabilities

_None._

### Modified Capabilities

- `multiplayer-integration`: two ADDED requirements refining behavior first
  introduced by `add-multiplayer-games` (whose delta spec has not been
  archived into `openspec/specs/` yet, so these are expressed as ADDED, not
  MODIFIED) — the injected client must not write to a socket that is not
  OPEN, and the smoke WS stub must hold the connection open rather than
  closing it immediately.

## Impact

- Code: `vendor/rt/rt.js` (send guards), `smoke_test.py`
  (`_serve_ws_stub` lifetime), `tests/test_multiplayer_e2e.py` (new fixture).
- No wire-protocol change, no CSP change, no `meta.json` change, no change
  to `rt_hub.py`. The `MAX_FRAME_BYTES` client/hub sync is untouched.
- `add-multiplayer-games` is complete but not yet archived; this change
  layers on top of it and does not require it to be archived first.
