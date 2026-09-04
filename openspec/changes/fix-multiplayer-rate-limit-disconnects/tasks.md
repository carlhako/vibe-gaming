## 1. Hub: graduated rate policy

- [x] 1.1 Add `max_msg_burst_per_sec` (default 240) to `rt_hub.DEFAULTS` and raise `max_msg_per_sec` default 40 -> 60; verify `load_config()` merges both from a `rt_hub:` block and falls back to the new defaults when the block or file is absent (extend the existing config tests in `tests/test_rt_hub.py`)
- [x] 1.2 Rework the rate branch in `Hub.on_message` so the rolling-window count selects one of three outcomes — relay, discard the relay frame while keeping the socket open, or close with `CLOSE_RATE_LIMIT` — per the `multiplayer-hub` "Message-rate policy is graduated, not fatal" requirement; verify with a fake-clock test that a client held between the soft budget and the hard ceiling for several windows stays in `room.members` and is never closed
- [x] 1.3 Make throttling exempt control frames: `join` and `pong` are processed even when over the soft budget, and only `msg` frames are eligible for discarding, while all frame types still count toward both windows; verify with a test that an over-budget client answering pings keeps `last_seen` refreshed, has `ping_ms` updated, and survives a `ping_round()` past `dead_after_s`
- [x] 1.4 Verify a discarded `msg` frame is not forwarded to any peer while the same client's under-budget frames still are, with a two-member room test asserting exact peer receipt counts
- [x] 1.5 Verify the hard ceiling still closes with `CLOSE_RATE_LIMIT` and leaves other members of the room and other rooms untouched; update or replace `tests/test_rt_hub.py::test_message_flood_disconnects_only_that_client` to drive the rate above the new hard ceiling rather than the old fatal cap
- [x] 1.6 Add bounded per-member throttle logging (game id and discarded count only, never payload bytes) at a rate far below one record per discarded frame; verify with a caplog test that a sustained over-budget run emits a small bounded number of records and that no payload content appears in them
- [x] 1.7 Update the `rt_hub:` block in `config.yaml.example` to document the two tiers, the new key, and that the soft budget is non-fatal; verify the example still parses and every documented key exists in `DEFAULTS`

## 2. Client: coalescing state channel and send budget

- [x] 2.1 Add `VG_RT.sendState(value)` to `vendor/rt/rt.js` — latest-wins pending slot plus a flush timer at `VG_RT.stateHz` (default 20), started lazily on first call and stopped on socket close; verify a headless test that calls it 60 times/sec for two seconds produces ~20 frames/sec on the wire, each carrying the most recent value
- [x] 2.2 Verify `sendState` emits an ordinary `{t:"msg", d:value}` frame with no extra envelope, so a peer receives it through `on('msg')` identically to a `send()` value, and that no frame is emitted on an interval with no pending value
- [x] 2.3 Add a client-side rate budget to `VG_RT.send()` that drops over-budget calls locally, returns falsy, raises nothing and logs no console error; verify under-budget calls still return truthy and are transmitted
- [x] 2.4 Expose the budgets the game can design against (`VG_RT.stateHz` and the discrete send budget) as readable properties; verify they are present and match the values the flush timer and bucket actually enforce
- [x] 2.5 Reset `me`, `roster` and `peers` in `onclose` and emit empty `roster` and `peers` events; verify a game polling `VG_RT.roster.length` observes zero after a close and that reconnect repopulates both the properties and the events
- [x] 2.6 Replace the reset-on-open backoff with a settle-gated reset (5s stable before the backoff returns to its floor); verify repeated open-then-immediate-close cycles produce increasing reconnect delays, and that a connection open longer than the settle period restores the shortest delay
- [x] 2.7 Confirm `MAX_FRAME_BYTES` and the client budget stay consistent with `rt_hub`'s `max_frame_bytes` and soft budget; verify with a test that asserts the client's defaults do not exceed the hub's defaults

## 3. Serving: make a client fix deliverable

- [x] 3.1 Change `app.py`'s `vendor_rt` route from `public, max-age=31536000, immutable` to a short revalidating policy, leaving `vendor_three` untouched; verify with a test asserting `/vendor/rt/rt.js` is not immutable and does not carry a long max-age, that it still sets `Access-Control-Allow-Origin: *`, and that a versioned `/vendor/three/<version>/` asset is still immutable

## 4. Generation: state the budget and enforce it

- [x] 4.1 Extend `game_generator.MULTIPLAYER_CONTRACT` with the send-rate rule: the relay enforces a budget, continuous per-frame state goes through `VG_RT.sendState`, discrete events through `VG_RT.send`, and calling `VG_RT.send` every animation frame is incorrect; verify the built system prompt contains the rule when `max_players` is set and is unchanged when it is not
- [x] 4.2 Make `smoke_test.py`'s WS stub count inbound frames and fail the attempt on a sustained rate above the hard burst ceiling, with a failure message naming the measured rate and the budget; verify with unit tests over the counting logic that a flooding rate fails, a rate between the soft budget and the ceiling passes, an in-budget rate passes, and a game that sends nothing passes
- [x] 4.3 Update `tests/test_multiplayer_e2e.py`'s per-frame `VG_RT.send()` fixture game to use `sendState` for its position stream, keeping a per-frame call site so the coalescing path is what the end-to-end run exercises; verify the smoke test still passes clean

## 5. Verification

- [x] 5.1 Run the full suite (`pytest`) and confirm no regression in `test_rt_hub.py`, `test_multiplayer.py`, `test_multiplayer_e2e.py`, `test_smoke_test.py` or `test_security_headers.py`
- [x] 5.2 Add an integration test driving two simulated members of one room, both sending above the soft budget for several seconds, asserting both stay in the roster continuously and neither is closed — the direct regression test for the reported failure
- [x] 5.3 Manually verify against the unmodified `games/neon-pong-2-player-2bc8db46`: start `app.py` and `rt_hub.py`, open two browsers, confirm the game reaches `playing` and holds it for at least 60 seconds with no reappearance of the "Waiting for player 2…" overlay and no reconnects in the hub log
- [x] 5.4 Confirm the deployment steps in design.md - Migration Plan are accurate: both `vibegames-rt-hub` and the Flask app need restarting, and a browser holding the old cached rt.js recovers on reload
