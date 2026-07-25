# Click Counter

A tiny click-counter game: press the button, watch the number go up. Used
as the Sprint 1 hand-authored fixture for `builder.py` — small enough to
read in full, real enough to build/scan/smoke like any other game.

## Modules

| file            | purpose                                          |
| ---------------- | ------------------------------------------------- |
| src/index.html   | shell — links style.css, loads core.js             |
| src/style.css    | dark-theme styling for the counter and button      |
| src/core.js      | click handler + counter state                      |

## Conventions

- No global state object: `count` is a closure variable local to core.js.
- No cross-module event bus — one button, one counter, no other modules
  need to react to state changes.
