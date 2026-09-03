# Multiplayer: manual two-client check

A scripted end-to-end sanity check for the realtime path, against a **real**
`rt_hub.py` and two real browser contexts. Run this after any change to
`rt_hub.py`, `vendor/rt/rt.js`, `multiplayer.py`, or the CSP/smoke allowances.

The automated equivalents are `tests/test_rt_hub.py::test_vg_rt_client_against_real_hub`
(two Playwright pages, real hub) and
`tests/test_multiplayer_e2e.py` (a generated multiplayer game under the smoke
stub). This doc is the human version for a locally running stack.

## Setup

```bash
source venv/bin/activate
python3 rt_hub.py            # terminal 1 — binds 127.0.0.1:8620
python3 app.py               # terminal 2 — http://localhost:8600
```

Because the browser derives the hub URL from the page origin + `/rt/`, you
need `/rt/` routed to the hub. Either:

- put a one-line Caddy/nginx proxy in front (see README.md), **or**
- for a quick local check, edit the target game's `games/<slug>/index.html`
  and add `data-hub="ws://localhost:8620"` to its
  `<script src="/vendor/rt/rt.js" ...>` tag.

Generate a 2-player game first if you don't have one: `/games/new`, tick
**Multiplayer**, max players 2, prompt e.g. *"a 2-player pong where each
player controls one paddle; show 'waiting for player 2' when alone"*.

## Steps

1. **Solo render.** Open `/play/<slug>` in window A. It must render a
   playable or explicit "waiting for players" state — never a blank screen or
   a console error. Check DevTools console is clean.
2. **Peer appears.** Open the same `/play/<slug>` in window B (a second
   profile / private window so it's a distinct socket). Within a second or
   two, window A's roster/presence should show **2** members, and so should
   B's. `VG_RT.roster` in each console has two `{id, nick, ping_ms}` entries.
3. **Ping is measured.** After ~5–10s (`ping_interval_s`), each roster entry's
   `ping_ms` is a **non-null** number in both windows. It comes from the
   hub's clock, not the client's.
4. **Message relay.** Do something in A that calls `VG_RT.send(...)` (move a
   paddle). B receives it via its `VG_RT.on('msg', ...)` handler and reacts.
   Then the reverse, B → A. The sender never receives its own message back.
5. **Disconnect updates the roster.** Close window B. Within `dead_after_s`
   (or immediately on a clean close), window A's roster drops back to 1
   member and the game returns to its solo/waiting state without erroring.
6. **Reconnect.** Reopen window B — it rejoins the same room (one room per
   game, keyed by `game_id`) and the roster goes back to 2.

## Pass criteria

All six steps behave as described, with a clean console in both windows
throughout. Any `console.error`, a hung "connecting…" state, a `ping_ms` that
stays null, or a message the sender receives back is a failure.
