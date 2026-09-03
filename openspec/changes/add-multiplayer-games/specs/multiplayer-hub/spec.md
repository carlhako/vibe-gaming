## Purpose

Defines the standalone real-time relay service that lets clients of the same
game share a room: how clients connect, how the room enforces capacity, what
presence and latency information the server broadcasts, the client/server
wire protocol, and the limits that keep the relay process stable under
untrusted input.

## ADDED Requirements

### Requirement: WebSocket connection endpoint

The hub SHALL accept WebSocket connections at a path that identifies the
target game by its `game_id`. The `game_id` component MUST be validated
against the same format the rest of the site uses (uuid4 hex); a
malformed `game_id` MUST be rejected at the handshake with no room
created.

The hub SHALL serve no other routes — no file serving, no HTTP proxying,
no endpoints beyond the WebSocket upgrade.

#### Scenario: Well-formed game_id connects

- **WHEN** a client opens a WebSocket to the hub with a valid `game_id`
- **THEN** the handshake completes and the client is placed in that game's room

#### Scenario: Malformed game_id rejected

- **WHEN** a client opens a WebSocket with a `game_id` that fails format validation
- **THEN** the hub closes the connection during/immediately after the handshake
- **AND** no room is created for that value

#### Scenario: Non-WebSocket request

- **WHEN** any plain HTTP request other than a WebSocket upgrade reaches the hub
- **THEN** the hub does not serve file content or proxy the request

### Requirement: One room per game, server-derived key

The hub SHALL maintain at most one room per `game_id`. The room key MUST
be derived server-side from the validated `game_id` and MUST NOT be
constructed from any other client-supplied string. Rooms SHALL be created
lazily on first join and destroyed once empty.

#### Scenario: Second player joins the same game

- **WHEN** two clients connect for the same `game_id`
- **THEN** both are members of the same single room and can exchange messages

#### Scenario: Room is garbage-collected when empty

- **WHEN** the last member of a room disconnects
- **THEN** the hub discards all room state and retains nothing on disk or in memory

#### Scenario: Client cannot address another room

- **WHEN** a client sends a message naming or targeting a different room or `game_id`
- **THEN** the message is only ever delivered within the sender's own room

### Requirement: Server-authoritative capacity

The room's maximum player count SHALL be read server-side from the game's
`meta.json` `multiplayer.max_players` value. A connection that would
exceed capacity SHALL be refused (or admitted only as a non-playing
spectator if the game opts into that later — not required here). A client
MUST NOT be able to raise its room's capacity by any message it sends.

A `game_id` whose `meta.json` has no `multiplayer` block SHALL NOT get a
room; its connection attempts are refused.

#### Scenario: Capacity reached

- **WHEN** a room already holds `max_players` members and another client connects
- **THEN** the new connection is refused with a distinguishable "room full" close reason

#### Scenario: Non-multiplayer game

- **WHEN** a client connects for a `game_id` whose `meta.json` lacks a `multiplayer` block
- **THEN** the connection is refused

#### Scenario: Client tries to widen capacity

- **WHEN** a client sends any message attempting to change `max_players`
- **THEN** the hub ignores it and capacity stays as read from `meta.json`

### Requirement: Presence roster

On every membership change (join, leave, disconnect) the hub SHALL
broadcast a roster message to all room members. Each roster entry SHALL
contain a hub-assigned member id, the member's client-declared nickname
(treated as an untrusted display string), and the member's latest
server-measured ping in milliseconds (null until first measured).

A newly joined client SHALL receive a roster reflecting current members
as part of joining.

#### Scenario: Join broadcasts roster

- **WHEN** a client joins a room
- **THEN** every member (including the newcomer) receives a roster listing all current members

#### Scenario: Leave broadcasts roster

- **WHEN** a member disconnects
- **THEN** the remaining members receive a roster no longer listing that member

#### Scenario: Nickname is not trusted

- **WHEN** a client declares a nickname containing markup or control characters
- **THEN** the hub stores and relays it verbatim as data and never interprets it

### Requirement: Server-measured ping

The hub SHALL periodically send each member a ping frame carrying a
sequence number and a server timestamp, and the client SHALL echo it.
The hub SHALL compute round-trip time from its own clock and include each
member's most recent RTT in subsequent roster broadcasts. Ping
measurement MUST NOT depend on client-reported timing.

#### Scenario: RTT appears in roster

- **WHEN** a member has completed at least one ping/echo exchange
- **THEN** that member's roster entry shows a non-null `ping_ms` derived from the hub's clock

#### Scenario: Unresponsive member

- **WHEN** a member stops echoing ping frames beyond a timeout
- **THEN** the hub treats the member as disconnected and broadcasts an updated roster

### Requirement: Opaque payload relay

For any non-protocol message a client sends, the hub SHALL fan it out
unchanged to the other members of the same room. The hub MUST NOT parse,
transform, validate the semantics of, execute, or persist game payloads.
The sender SHALL NOT receive an echo of its own relayed payload.

#### Scenario: Payload reaches peers only

- **WHEN** member A sends a game payload
- **THEN** members B..N in the same room receive it byte-for-byte and A does not

#### Scenario: Payload is never interpreted

- **WHEN** a relayed payload contains a URL, script-like text, or serialized code
- **THEN** the hub relays it as opaque data without acting on its contents

### Requirement: Process-stability limits

The hub SHALL enforce, per connection: a maximum single-frame size (oversized
frames dropped without full parsing), a maximum message rate, and a maximum
number of concurrent connections per client IP. Protocol messages SHALL be
parsed with a memory-safe JSON parser; malformed protocol messages SHALL be
rejected without reflecting the raw parser error back to the client. The hub
SHALL run as its own unprivileged process, separate from the Flask
application, with no database write access.

#### Scenario: Oversized frame

- **WHEN** a client sends a frame larger than the configured limit
- **THEN** the hub drops it (and MAY close the connection) without allocating a full parse of it

#### Scenario: Message flood

- **WHEN** a client exceeds the configured message rate
- **THEN** the hub throttles or disconnects that client and other rooms are unaffected

#### Scenario: Malformed protocol message

- **WHEN** a client sends a protocol frame that is not valid JSON of the expected shape
- **THEN** the hub rejects it and does not echo the raw parser exception text

#### Scenario: Hub crash isolation

- **WHEN** the hub process crashes or is restarted
- **THEN** the Flask application and job runner continue serving unaffected

### Requirement: No persistence

The hub SHALL keep all room, member, roster, and ping state in memory only
for the lifetime of the room and SHALL NOT write game or player data to
disk, a database, or logs beyond ephemeral operational logging.

#### Scenario: Restart clears all rooms

- **WHEN** the hub restarts
- **THEN** all rooms are empty and no prior room or player data is recoverable
