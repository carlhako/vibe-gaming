## Purpose

Defines the behavior of the standalone real-time relay that multiplayer games
connect to: room membership and capacity, the presence roster, the
server-measured ping, opaque payload relaying, and the per-connection limits
that keep the relay process healthy under untrusted traffic.

## ADDED Requirements

### Requirement: Message-rate policy is graduated, not fatal

The hub SHALL apply two per-connection message-rate thresholds over a rolling
one-second window: a soft budget and a hard burst ceiling, where the hard
ceiling is greater than the soft budget. Both SHALL be configurable and SHALL
have defaults that leave a game sending one message per frame at 60 frames per
second within the soft budget.

While a connection's rate is above the soft budget but at or below the hard
ceiling, the hub SHALL discard the excess relay messages and SHALL keep the
connection open. It SHALL NOT close the connection, SHALL NOT alter the
member's room membership, and SHALL NOT alter its roster entry.

Only when a connection's rate exceeds the hard burst ceiling SHALL the hub
close it with the rate-limit close code.

Rationale: a game that synchronizes player state once per animation frame is
the ordinary case, not an attack. Closing its socket destroys the session and
triggers a reconnect loop that removes the player from every peer's roster
about once per second, whereas discarding a superseded state update costs the
game nothing it can observe. Disconnection remains reserved for a rate that
actually threatens the relay process.

#### Scenario: Sustained per-frame send rate stays connected

- **WHEN** a client sends messages at a rate above the soft budget and below the hard burst ceiling for a sustained period
- **THEN** the connection remains open, the client remains in its room, and it remains present in every roster broadcast for the duration

#### Scenario: Excess relay messages are discarded

- **WHEN** a client exceeds the soft budget within the rolling window
- **THEN** the relay messages above the budget are not forwarded to peers
- **AND** messages sent by that client while under the budget continue to be forwarded normally

#### Scenario: A genuine flood is still disconnected

- **WHEN** a client's message rate exceeds the hard burst ceiling
- **THEN** the hub closes that connection with the rate-limit close code
- **AND** other members of the same room, and members of other rooms, are unaffected

#### Scenario: A client at the soft budget is never disconnected for rate

- **WHEN** a client sends at exactly the soft budget indefinitely
- **THEN** every message is relayed and the connection is never closed for rate

### Requirement: Rate throttling never discards protocol control frames

When a connection is over the soft budget, the hub SHALL continue to process
its protocol control frames — at minimum the presence/nickname frame and the
ping response frame. Only relay (game payload) messages SHALL be eligible for
discarding. Control frames SHALL continue to count toward both rate thresholds.

A member that is over the soft budget and is answering the hub's pings SHALL
NOT be treated as unresponsive and SHALL NOT be dropped by the liveness check.

Rationale: the hub measures liveness from the client's ping responses. If
throttling discarded those responses, an over-budget client would stop
refreshing its liveness and be disconnected by the liveness timeout instead —
reinstating the disconnect loop this policy exists to remove, merely on a
slower period.

#### Scenario: Ping responses survive throttling

- **WHEN** a client is sending above the soft budget and answering every hub ping
- **THEN** its liveness is refreshed by each response, its measured ping is updated, and it is not dropped by the liveness check

#### Scenario: Nickname change survives throttling

- **WHEN** a client that is over the soft budget sends a presence/nickname frame
- **THEN** the hub applies it and broadcasts the updated roster

#### Scenario: Control frames count toward the ceiling

- **WHEN** a client floods control frames at a rate above the hard burst ceiling
- **THEN** the hub closes that connection with the rate-limit close code

### Requirement: Throttling is observable without recording game data

The hub SHALL record that a connection is being throttled, at a bounded
frequency per member, identifying the game and a count of discarded messages.
It SHALL NOT record the content of any discarded or relayed payload.

Rationale: silent discarding is otherwise indistinguishable from network loss
when diagnosing a game that feels laggy. The record must not weaken the hub's
guarantee that it never persists game or player data.

#### Scenario: Throttling is recorded

- **WHEN** a connection has relay messages discarded for exceeding the soft budget
- **THEN** the hub records the game identifier and a discarded-message count
- **AND** the record is emitted at a bounded frequency rather than once per discarded message

#### Scenario: Payloads are never recorded

- **WHEN** relay messages are discarded
- **THEN** no part of any payload appears in the hub's output
