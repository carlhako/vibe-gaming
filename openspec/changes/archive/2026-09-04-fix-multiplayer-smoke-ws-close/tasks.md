## 1. rt.js send guards

- [x] 1.1 In `vendor/rt/rt.js`, gate `api.send()` on `ws && ws.readyState === WebSocket.OPEN` (in addition to `api.connected`), keeping the existing size checks and falsy return. Verify by unit/integration: a `send()` call made after the socket is CLOSING/CLOSED returns falsy, does not call `ws.send()`, and logs no console error.
- [x] 1.2 In `vendor/rt/rt.js`, gate `sendRaw()` on `ws && ws.readyState === WebSocket.OPEN` so `join`/`pong` control frames are also suppressed after close. Verify: driving `onMessage` with a `ping` frame after the socket is closed produces no `ws.send()` call and no console error.
- [x] 1.3 Confirm `multiplayer.normalize()` output is unchanged (tag bytes are identical) and `tests/test_multiplayer.py` still passes — rt.js content is not hashed by normalize, so this is a no-op check that nothing else references the old behavior.

## 2. Smoke WS stub lifetime

- [x] 2.1 In `smoke_test.py` `_serve_ws_stub()`, after writing the `welcome` and `roster` frames, stop sending `_WS_CLOSE_FRAME`; instead block the handler thread reading from the socket (swallowing `OSError`) until the client or server teardown closes it. Verify: a game that opens `/rt/<id>` sees the socket stay OPEN for the full settle window (assert via a fixture game that records `VG_RT.connected` after the wait).
- [x] 2.2 Ensure `_serve_game()` teardown still completes within its `thread.join(timeout=5)` with a stub socket held open — `server.shutdown()` + `server.server_close()` must drop the parked handler. Verify: `run_smoke_test()` on a multiplayer fixture returns within the normal time budget and the process leaves no lingering thread.

## 3. Regression fixture

- [x] 3.1 Add a fixture game to `tests/test_multiplayer_e2e.py` that calls `VG_RT.send({...})` from a `requestAnimationFrame` loop starting on `welcome`. Verify: `run_smoke_test()` on this fixture returns `(True, ...)` with the rt.js + stub fixes, and (guard test) returns `False` with a stub that closes immediately + unpatched rt.js, documenting the pre-fix failure mode.

## 4. Full verification

- [x] 4.1 Run the whole suite (`pytest`) and confirm `tests/test_multiplayer.py`, `tests/test_multiplayer_e2e.py`, `tests/test_rt_hub.py`, and `tests/test_smoke_test*.py` all pass.
- [x] 4.2 Generate a real multiplayer game locally (or replay the failing description through `run_generation_attempts` with mocked AI returning a send-loop game) and confirm the smoke step passes with no `WebSocket is already in CLOSING or CLOSED state.` console errors.
