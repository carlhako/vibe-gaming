## ADDED Requirements

### Requirement: Injected client offers a coalescing state channel

The injected client SHALL expose a state-sending operation, distinct from its
discrete send operation, with latest-wins semantics: repeated calls SHALL
replace the pending value rather than queueing it, and the client SHALL
transmit at most the most recent pending value per flush interval. The flush
rate SHALL default to a value comfortably within the hub's soft message budget
and SHALL be readable by the game.

A value sent through this channel SHALL be delivered to peers through the same
peer-message event as a discrete send, carrying no additional wire envelope, so
that receiving games and the relay are unchanged.

Calling this operation once per animation frame SHALL NOT cause the client to
exceed the hub's soft message budget, at any frame rate.

Rationale: synchronizing continuous state every frame is the ordinary idiom for
a real-time game, and the older values in such a stream are superseded rather
than lost. Coalescing at the client is the only layer that can distinguish
supersedable state from discrete events, because the relay treats payloads as
opaque.

#### Scenario: Per-frame state calls are coalesced

- **WHEN** a game calls the state operation on every animation frame at 60 frames per second
- **THEN** the client transmits at approximately the configured flush rate, not once per call
- **AND** each transmission carries the value from the most recent call

#### Scenario: Coalescing holds at a high frame rate

- **WHEN** a game calls the state operation on every animation frame on a 120Hz display
- **THEN** the client's transmission rate is unchanged and remains within the hub's soft budget

#### Scenario: State values reach peers as ordinary peer messages

- **WHEN** a value is sent through the state channel
- **THEN** a peer receives it through the peer-message event exactly as it would receive a discrete send

#### Scenario: No transmission without a pending value

- **WHEN** no state call has been made since the previous flush
- **THEN** the client transmits nothing on that interval

### Requirement: Discrete sends are budgeted at the client

The injected client SHALL enforce its own message-rate budget on the discrete
send operation. A discrete send that would exceed the budget SHALL be dropped
by the client, SHALL return a falsy value, SHALL NOT be transmitted, and SHALL
NOT raise or produce a console error.

Rationale: the discrete send operation already reports success or failure to
the game. If the client transmitted an over-budget message for the hub to
discard, that report would be untrue. Enforcing the budget locally keeps the
return value honest and leaves the hub's throttle as a backstop.

#### Scenario: Over-budget discrete send is refused locally

- **WHEN** a game calls the discrete send operation at a rate above the client's budget
- **THEN** the calls above the budget return a falsy value and are not transmitted, with no console error

#### Scenario: Under-budget discrete sends are unaffected

- **WHEN** a game calls the discrete send operation within the budget
- **THEN** each call is transmitted and returns a truthy value

### Requirement: Presence state is cleared when the connection drops

When the client's socket closes, the client SHALL reset its exposed presence
state — its own member identity, the roster, and the peer list — and SHALL emit
an empty roster event and an empty peers event.

Rationale: a game that reads the roster directly rather than listening for the
event would otherwise continue to observe a peer that has been absent since the
socket dropped, and would keep rendering an active session against a peer that
can no longer receive anything.

#### Scenario: Roster is emptied on disconnect

- **WHEN** the client's socket closes for any reason
- **THEN** the exposed roster and peer list are empty, the exposed member identity is absent, and an empty roster event and empty peers event are emitted

#### Scenario: Game reading state directly sees the disconnect

- **WHEN** a game polls the roster length rather than subscribing to the roster event
- **AND** the socket has closed
- **THEN** the game observes a roster length of zero

#### Scenario: Presence is repopulated on reconnect

- **WHEN** the client reconnects and is re-admitted to the room
- **THEN** the exposed member identity and roster are repopulated from the hub and the corresponding events are emitted

### Requirement: Reconnect backoff resets only after a stable connection

The client SHALL NOT reset its reconnect backoff solely because a connection
opened. It SHALL reset the backoff only after a connection has remained open
for a settle period. A connection that closes before the settle period elapses
SHALL leave the backoff to continue growing toward its ceiling.

Rationale: when every reconnect succeeds and is then closed shortly after, a
reset-on-open backoff never grows, and the client retries at a fixed short
interval indefinitely. Backoff must respond to connections that do not last,
not merely to connections that fail to open.

#### Scenario: Repeated short-lived connections back off

- **WHEN** each connection opens successfully and is closed before the settle period
- **THEN** the delay before each subsequent reconnect attempt increases toward the ceiling

#### Scenario: A stable connection restores fast reconnect

- **WHEN** a connection has remained open for longer than the settle period and is then closed
- **THEN** the next reconnect attempt uses the shortest delay

### Requirement: The injected client is served revalidatable, not immutable

The realtime client SHALL be served from its fixed path with a caching policy
that causes browsers to revalidate it within a short period. It SHALL NOT be
served as immutable or with a long-lived expiry.

Versioned vendor assets whose path contains the version SHALL keep their
long-lived immutable caching policy; this requirement applies only to assets
served from a fixed, unversioned path.

Rationale: the client's script tag is written into each game's HTML at
generation time and rewritten only when that game is regenerated, so the URL
cannot carry a version without stranding already-generated games on an old
client. With a fixed URL, a long-lived immutable policy makes any correction to
the client undeliverable to browsers that have already loaded a multiplayer
game.

#### Scenario: A client update reaches an existing game

- **WHEN** the realtime client file is updated and a player reloads a previously played multiplayer game
- **THEN** the browser revalidates and obtains the updated client rather than serving a long-cached copy

#### Scenario: Versioned vendor assets keep immutable caching

- **WHEN** a versioned vendor asset is requested from its version-bearing path
- **THEN** it is still served with a long-lived immutable caching policy

### Requirement: Generation prompt states the send-rate budget

When a game is generated with the multiplayer opt-in, the generation prompt
SHALL state that the relay enforces a message-rate budget, SHALL direct
continuous per-frame state synchronization to the coalescing state operation,
and SHALL direct discrete events to the ordinary send operation. It SHALL state
that calling the ordinary send operation on every animation frame is incorrect.

Rationale: without a stated budget the model has no reason not to send once per
animation frame through the discrete operation, which is what produced the
disconnect loop this change addresses.

#### Scenario: Generated game syncs continuous state correctly

- **WHEN** a multiplayer game is generated that must synchronize a continuously changing value such as a player position
- **THEN** the generated game uses the coalescing state operation for it rather than calling the discrete send operation every frame

#### Scenario: Generated game sends discrete events normally

- **WHEN** a multiplayer game is generated that must communicate an occasional event such as a score change
- **THEN** the generated game uses the ordinary send operation for it

### Requirement: Smoke WS stub fails a game that floods

The generation-time WebSocket stub SHALL count the frames a game sends and
SHALL fail the generation attempt when the game sustains a message rate above
the hub's hard burst ceiling. The failure reported back SHALL state the
measured rate and the budget, so the model can correct the send loop on the
retry.

A game whose rate is above the hub's soft budget but at or below the hard
ceiling SHALL NOT fail the attempt.

Rationale: a game that would be disconnected by the hub must fail during
generation rather than in the arcade, in the same way a content-security
violation or a console error already does. The threshold is the hard ceiling,
not the soft budget, because exceeding the soft budget is merely lossy and a
lossy game is still playable.

#### Scenario: A flooding game fails generation

- **WHEN** a generated multiplayer game sustains a send rate above the hard burst ceiling during the smoke run
- **THEN** the attempt fails and the reported reason names the measured rate and the budget

#### Scenario: A game within the soft budget passes

- **WHEN** a generated multiplayer game sends within the soft budget during the smoke run
- **THEN** the attempt is not failed for its send rate

#### Scenario: A merely lossy game passes

- **WHEN** a generated multiplayer game sustains a rate above the soft budget but below the hard burst ceiling
- **THEN** the attempt is not failed for its send rate

#### Scenario: A silent game passes

- **WHEN** a generated multiplayer game sends no frames during the smoke run
- **THEN** the attempt is not failed for its send rate
