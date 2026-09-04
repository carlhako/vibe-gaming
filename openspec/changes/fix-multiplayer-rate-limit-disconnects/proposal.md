## Why

The hub disconnects any client that exceeds `max_msg_per_sec` (40). A game
that syncs player state once per animation frame — the standard idiom for
real-time multiplayer, and the only idiom the generation prompt leaves the
model — sends 60-70 messages/sec and is therefore killed roughly 0.6s after
every connect. `rt.js` reconnects, resets its backoff on the successful open,
and the cycle repeats about once a second, forever.

Because two clients thrash out of phase, `roster.length >= 2` holds only some
of the time, so both players watch the game start and then flip back to
"Waiting for player 2…" every second or two. This was reproduced with
`games/neon-pong-2-player-2bc8db46` (two real players, both affected).

Nothing upstream can catch it: `MULTIPLAYER_CONTRACT` never states a send
budget, so the model has no way to know the wall exists, and the smoke test's
WebSocket stub does not count frames, so a game that floods at 70/sec passes
generation clean and only fails once a second human joins. Every future
multiplayer game will reproduce this.

The hub's own contract says its limits exist for "keeping this process up
under untrusted frames, not as anti-abuse". Seventy small JSON frames a second
does not threaten the process — but the hub's response (closing the socket)
converts a non-problem into total game failure. The limit is currently worse
than not having one.

## What Changes

- **Hub: a message rate over budget throttles instead of disconnecting.**
  Two tiers replace the single fatal cap: frames above a *soft* per-second
  budget are dropped and the socket stays open; only a *hard* burst ceiling —
  an actual flood, far above any legitimate game loop — still closes the
  connection with `CLOSE_RATE_LIMIT`. `max_msg_per_sec` becomes the soft
  budget and its default rises 40 -> 60; a new `max_msg_burst_per_sec`
  (default 240) is the hard ceiling.
- **`VG_RT` gains `sendState(value)`** — a latest-wins coalescing channel
  flushed at a fixed, hub-safe rate (default 20Hz). Calling it every animation
  frame is correct by construction: only the most recent value is transmitted.
  This is the API a game should reach for to sync continuous state (positions,
  velocities, input), and it removes the reason games flood at all.
- **`VG_RT.send(value)` gains a client-side budget.** Over-budget discrete
  sends are dropped locally and return falsy rather than being relayed into a
  hub-side drop, so the game gets a truthful answer and the hub's throttle
  becomes a backstop rather than the primary control.
- **`VG_RT` clears presence state on disconnect.** `me`, `roster` and `peers`
  are reset and an empty `roster`/`peers` event is emitted when the socket
  closes, so a game reading `VG_RT.roster` (rather than the event) cannot see
  a peer that is no longer there.
- **`VG_RT` reconnect backoff no longer resets on open alone.** It resets only
  after a connection has been stable for a settle period, so a
  connect/close/connect loop backs off instead of thrashing at a fixed ~1s.
- **The generation prompt states the send budget** and directs continuous
  state to `sendState` and discrete events to `send`.
- **The smoke test's WS stub counts inbound frames** and fails the attempt when
  a game sustains a rate above the hard ceiling, so a flooding game is caught
  during generation instead of in the arcade.

No generated game needs editing or regenerating: the existing Pong is expected
to become playable against the fixed hub unmodified. That is the point of
fixing this at the hub rather than in the game.

## Capabilities

### New Capabilities

- `multiplayer-hub`: the relay's own behavior — rooms, capacity, roster, ping,
  opaque relay, and the process-stability limits including the rate policy
  changed here. The capability was authored in `add-multiplayer-games` but has
  not been synced into `openspec/specs/` yet, so its delta lands as a new
  spec file; only the rate-policy requirements are stated here.

### Modified Capabilities

- `multiplayer-integration`: the injected `VG_RT` client gains the `sendState`
  coalescing channel, a client-side send budget, presence-state reset on
  disconnect, and settle-gated backoff; the generation prompt contract gains a
  send-rate rule; the smoke WS stub gains rate enforcement.

## Impact

- `rt_hub.py` — `Hub.on_message` rate branch, `DEFAULTS`, close-code semantics.
- `vendor/rt/rt.js` — new `sendState` channel and flush timer, send budget,
  `onclose` presence reset, backoff settle gate. The client is served
  immutable-cached from `/vendor/rt/`; cache busting for the updated file must
  be handled (see design.md).
- `config.yaml.example` — `rt_hub.max_msg_per_sec` default and the new
  `max_msg_burst_per_sec` key, with comments.
- `game_generator.py` — `MULTIPLAYER_CONTRACT` text.
- `smoke_test.py` — WS stub frame counting and failure path.
- `tests/` — hub rate-policy tests, `rt.js` behavior tests, smoke stub tests.
- Deployment: the running `vibegames-rt-hub` service must be restarted to pick
  up the new policy. No database, schema, or `meta.json` change.
