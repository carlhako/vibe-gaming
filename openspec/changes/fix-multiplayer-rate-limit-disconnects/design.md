## Context

See proposal.md - Why for the failure and its reproduction.

Three constraints shape the approach:

1. **`VG_RT.send()` is an opaque relay.** The hub and the client both treat the
   payload as an uninspected JSON value, deliberately (`rt_hub.py` docstring;
   `multiplayer-hub` "Opaque payload relay"). Neither layer can know which of a
   game's messages are droppable, so no throttle placed *below* the game can
   choose intelligently what to shed.
2. **`rt.js` is served from a single unversioned URL** (`/vendor/rt/rt.js`,
   `multiplayer.RT_SRC`) with `Cache-Control: public, max-age=31536000,
   immutable` (`app.py` `vendor_rt`). The tag is baked into each game's HTML at
   generation time and only rewritten when that game is enhanced, so the URL
   cannot carry a version or hash without stranding every already-generated
   game on the old client.
3. **The hub's limits are justified as process stability, not policy** — so any
   response harsher than what stability requires needs its own justification.

## Goals / Non-Goals

**Goals:**

- A game loop that sends every animation frame stays connected and playable.
- The existing `neon-pong-2-player-2bc8db46` works unmodified after the hub and
  client are fixed — no regeneration, no hand-editing of generated games.
- Give the model a networking idiom that is correct at 60fps, so the flood does
  not need to be caught after the fact.
- Keep a real flood from threatening the hub process.

**Non-Goals:**

- Per-message priority, channels, reliability tiers, or ordering guarantees in
  the wire protocol. The relay stays dumb.
- Server-side interpolation, prediction, lag compensation, or any awareness of
  game semantics in the hub.
- Stable player identity across a reconnect. A reconnecting member still gets a
  fresh id; games that key state on member id are unchanged by this work and
  remain the author's problem.
- Retrofitting existing generated games.

## Decisions

### D1: Throttle over budget; disconnect only over a hard burst ceiling

Replace the single fatal `max_msg_per_sec` with two thresholds:

| Rate (msgs/sec, rolling 1s) | Hub response |
| --- | --- |
| <= `max_msg_per_sec` (60) | relay normally |
| > `max_msg_per_sec`, <= `max_msg_burst_per_sec` (240) | drop the excess relay frame; socket stays open |
| > `max_msg_burst_per_sec` (240) | close `CLOSE_RATE_LIMIT` (4010) |

*Why:* dropping a frame costs the game one stale position update; closing the
socket costs it the entire session plus a reconnect storm. The hub's stated
purpose for this limit is keeping the process up, and a 60-70/sec JSON relay
does not endanger it. The soft default rises 40 -> 60 so the single most common
idiom (one send per frame at 60fps) sits inside the budget rather than one
frame outside it.

*Alternative rejected — raise `max_msg_per_sec` and keep the close.* Moves the
cliff without removing it; a 120Hz display or a game with two per-frame sends
walks straight back into it. The failure mode, not the threshold, is the defect.

*Alternative rejected — drop everything over budget with no ceiling.* Leaves no
protection against an actual flood, which is the limit's original and valid
purpose.

### D2: Throttling drops only relay (`msg`) frames; control frames always process

`join` and `pong` are processed even when the member is over the soft budget.

*Why:* this is load-bearing and easy to get wrong. `pong` is what refreshes
`member.last_seen`; if the throttle dropped pongs, a flooding client would stop
answering pings and `ping_round` would disconnect it at `dead_after_s` — the
exact bug this change removes, reintroduced through a side door with a
15-second period instead of a 0.6-second one. Control frames still count toward
both rate windows (so a control-frame flood still trips the hard ceiling); they
are simply never the frames that get shed.

### D3: `VG_RT.sendState(value)` — a latest-wins coalescing channel

`sendState` stores the value and a timer flushes the most recent one at
`stateHz` (default 20). Successive calls within a flush interval overwrite;
nothing queues. On the wire it is an ordinary `{t:"msg", d:value}` frame, so
the hub, the protocol, and receiving games are unchanged — a peer still gets it
through `VG_RT.on('msg', ...)`.

*Why:* this addresses the actual root cause rather than the symptom. "Send my
position every frame" is what a game author (and a model) naturally writes, and
with `sendState` that instinct is *correct* — the coalescer makes 60 calls/sec
produce 20 sends/sec of the freshest data, which is what the game wanted
anyway. Continuous state is latest-wins by nature; the older values were
already worthless.

*Why not a plain token bucket on `send()` instead.* A bucket drops whichever
message happens to arrive when the bucket is empty. The reproduction case
multiplexes two logical streams through one `send()` — `{y}` at 60/sec (lossy,
latest-wins, safe to drop) and `{ball}` at 10/sec (authoritative, dropping it
desyncs both clients). An opaque bucket sheds them indiscriminately and would
sometimes eat the ball. Splitting the *call sites* by the game's own knowledge
of which stream is which is the only place the distinction exists. `send()`
keeps a bucket as a backstop (D4), but it is no longer the mechanism a
well-written game relies on.

*Why not a hub-side coalescer.* It would have to inspect and replace payloads,
breaking the opaque-relay contract, and it cannot know which sends supersede
each other.

### D4: `send()` gets a client-side budget that returns falsy

Discrete `send()` calls above the client's budget are dropped locally and
return falsy, rather than being transmitted for the hub to drop.

*Why:* `send()` already returns a boolean that a game may check. Dropping at
the hub makes that return value a lie — the game is told the message went out.
Dropping locally keeps the contract honest and turns the hub throttle into a
backstop against a client that predates this change.

### D5: Serve `rt.js` with a short cache lifetime instead of `immutable`

Change `vendor_rt`'s `Cache-Control` from `public, max-age=31536000, immutable`
to a short revalidating policy, leaving `/vendor/three/<version>/` untouched.

*Why:* the year-long immutable cache was copied from the three.js route, where
it is correct because the *path contains the version*. `/vendor/rt/rt.js` has
no version in its path, so `immutable` means a client fix cannot reach any
browser that has already loaded a multiplayer game — for a year. Without this
change, every other fix here silently fails to deploy for existing players.
This is a latent defect in its own right, exposed by being the first rt.js
update.

*Alternative rejected — version the path or add a hash query.* The tag is
written into each game's HTML at generation time and re-normalized only on
enhance, so already-generated games would pin the old client permanently. That
is the opposite of what is needed.

*Trade-off accepted:* rt.js is a few KB and gains a revalidation request per
game load. Negligible against a self-hosted arcade's traffic.

### D6: Backoff resets only after a connection proves stable

`onopen` no longer resets `backoff` to its floor. A timer set on open resets it
after a settle period (5s); a close before that leaves the backoff doubling.

*Why:* the current reset-on-open makes backoff useless against exactly the
failure being fixed — every reconnect *succeeded*, so the backoff never grew
and the client thrashed at a fixed ~1.1s indefinitely. Whatever future cause
produces short-lived connections should degrade into a slow retry, not a storm.

### D7: Presence state is cleared on close

`onclose` sets `me = null`, `roster = []`, `peers = []` and emits empty
`roster`/`peers` events.

*Why:* a game that polls `VG_RT.roster.length` rather than listening for the
event currently sees a peer that has been gone since the socket dropped. This
is independent of the rate bug but is in the same failure story — it is why a
disconnected client can keep rendering a live game.

### D8: The smoke stub enforces the hard ceiling

The stub counts inbound frames and fails the attempt when a game sustains a
rate above `max_msg_burst_per_sec`, reporting the measured rate and the budget.

*Why:* this is what makes the prompt's send-rate rule real rather than
advisory. The codebase already trusts the smoke test to convert production
failures into generation failures (CSP violations, console errors, egress); an
unplayable send rate belongs in the same gate. It is deliberately set at the
*hard* ceiling, not the soft budget: exceeding the soft budget is now merely
lossy, and failing generation for lossiness would reject playable games.

## Risks / Trade-offs

- **Silent drops are harder to debug than a disconnect.** A game shedding
  frames looks "laggy" with no error anywhere. → The hub logs a throttle event
  per member at a low frequency (game_id and a count only, never payload
  bytes, preserving "no game data to logs"), and `VG_RT.send()` returning falsy
  gives the game a local signal.
- **A pre-existing game that multiplexes streams through `send()` still gets
  indiscriminate drops** when it exceeds the soft budget, because it does not
  use `sendState`. → It stays connected and playable, which is the goal; the
  reproduction case sends 70/sec against a 60/sec budget, so ~14% of frames
  shed, mostly paddle updates. Regenerating or enhancing such a game picks up
  the new prompt contract and the `sendState` idiom.
- **`sendState` adds a timer to every multiplayer game.** → One interval,
  started lazily on first `sendState` call, stopped when the socket closes.
  Games that never call it pay nothing.
- **Dropping `immutable` on rt.js could mask a caching regression elsewhere.**
  → The change is scoped to the `vendor_rt` route only; `/vendor/three/` keeps
  its versioned-path immutable policy, and a test asserts both.
- **Config keys change meaning.** `max_msg_per_sec` stops being fatal. → An
  operator reading the old value as "the disconnect threshold" now has a
  different, gentler behavior; `config.yaml.example` comments state the two
  tiers explicitly, and the default change (40 -> 60) is noted in the migration
  step.

## Migration Plan

1. Land the code and tests; no schema, no `meta.json`, no data migration.
2. Update `config.yaml.example`. A deployment whose `config.yaml` pins
   `max_msg_per_sec: 40` keeps 40 as its *soft* budget — still non-fatal, so
   the bug is fixed either way — but should be raised to 60 to avoid needless
   shedding.
3. Restart the `vibegames-rt-hub` service. The Flask app must also be restarted
   (or reloaded) to pick up the `vendor_rt` cache-header change.
4. Verify with the unmodified `neon-pong-2-player-2bc8db46` and two browsers:
   the game must reach and hold `playing` with no reconnects for a sustained
   period. Players who loaded a multiplayer game before step 3 keep the cached
   old rt.js until it revalidates; a hard reload is the workaround for the
   one-time transition.

**Rollback:** revert the commit and restart both services. Clients holding the
new rt.js are compatible with the old hub — `sendState` frames are ordinary
`msg` frames, and the client-side budget keeps them under the old fatal cap, so
a rolled-back hub does not resurrect the disconnect loop for them.
