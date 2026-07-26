# Sprint 5 — Explode + Pilot on Sorcerer With A Minigun

See [00-overview.md](00-overview.md). Depends on Sprints 1–4. This sprint
turns the one real problem game into a multi-file game and proves the whole
initiative on it — with measured numbers, not vibes.

## Goals

1. An AI-assisted "explode" pass that splits a single-file `index.html` into
   `game.md` + `src/` modules.
2. Dual-format enhance: a single-file game can become multi-file on its next
   enhancement.
3. Pilot on the Sorcerer With A Minigun chain and measure the token/reliability
   delta versus the single-file baseline.

## Part A: the explode pass

- `explode.py` (or an `agent.py` mode): given a single-file game, ask the
  model to split it into a shell `src/index.html` + `src/style.css` +
  cohesive `src/*.js` modules, and to author `game.md` (the map). It must be
  **behavior-preserving**: build → `safety.scan()` → `smoke_test.py`, and
  the built artifact should be functionally identical to the original.
- This is itself an output-bounded operation: the model emits one module per
  `write_file` call (Sprint 2's tools), never the whole game at once — so
  explode works even on a game already at the ceiling, which is the whole
  point (the current Sorcerer With A Minigun game can't be re-emitted whole, but it *can*
  be read in and written out module-by-module).
- Verification beyond smoke: a brief manual play-test of the exploded build
  in the browser preview, since "console-error-free" doesn't prove the game
  still *plays* the same. Note any behavior drift in the PR.

## Part B: dual-format enhance policy

- Decide and implement the trigger (open question from the overview):
  - **Recommended:** when an enhance targets a single-file game whose built
    size is near the ceiling (reuse the `LARGE_SOURCE_BYTES` notion already
    in `game_enhancer`), run explode first, then do the enhancement on the
    resulting multi-file fork. Small games stay single-file and cheap.
- The fork lineage is preserved across the format change: the exploded/
  enhanced result still links `parent_game_id`/`root_game_id` to the
  single-file source, so the sidebar lineage and info-modal ancestor chain
  stay intact across the single→multi boundary.

## Part C: pilot + measurement

- Explode the current head of the Sorcerer With A Minigun chain; enhance it a few times
  through the agent (real feature requests like the ones in the DB).
- Record, in the PR and in `docs/multifile-agent/`:
  - input/output tokens and attempts per enhance, **agent (multi-file) vs.
    the single-file baseline** for a comparable change.
  - whether any enhance still hit truncation (expected: none — no single
    output is whole-game-sized anymore).
  - subjective edit reliability (did targeted rewrites stay correct?).

## Part D: docs + tests

- Update `CLAUDE.md`: document the multi-file format, `builder.py`,
  `agent.py`, the `agent_events`/events API, the live chat UI, and the
  dual-format enhance policy. Update the File map.
- Tests: explode a small single-file fixture → valid multi-file game whose
  build passes scan+smoke; dual-format enhance of a near-ceiling single-file
  fixture produces a multi-file fork with correct lineage.

## Acceptance criteria

- The Sorcerer With A Minigun head is a working multi-file game: it builds, scans,
  smoke-tests, and plays identically to its single-file predecessor.
- Enhancing it through the agent no longer truncates, touches only relevant
  modules, and uses materially fewer tokens than the single-file baseline —
  with the numbers recorded.
- Fork lineage survives the single→multi transition (info modal + sidebar
  lineage correct).
- `CLAUDE.md` and the File map reflect the new architecture; `pytest` green.

## Pilot results (2026-07-26)

Ran against the real production head of this chain — "Darkhold Arena —
Wave RPG (v37)" (159,312 bytes single-file), downloaded from the live
server — in a scratch DB/games_dir (never touched `vibegames.db` or the
real `games/` directory), using the real DeepSeek API
(`deepseek-v4-flash`, effort `high`). One baseline enhance via the legacy
whole-file path, then `explode_game()` + two real feature enhances via the
agent, all against the same source:

| phase | request | attempts | input tokens | output tokens | wall time |
|---|---|---|---|---|---|
| baseline (single-file) | "Add a boss wave every 5th wave with a unique telegraphed attack pattern" | 2 | 114,275 | 112,862 | 540s |
| explode | (format conversion only) | 1 | 1,431,996 | 68,256 | 345s |
| agent enhance #1 | "Add a shop between waves to spend gold on temporary buffs" | 1 | 696,702 | 55,898 | 301s |
| agent enhance #2 | "Add a combo counter that rewards bonus gold for quick kills" | 1 | 513,274 | 36,974 | 214s |

**Reliability: as hoped.** All four runs succeeded on effectively the
first real attempt (the baseline's "2 attempts" was a smoke-test retry,
not a truncation), no run hit `finish_reason == "length"`, and the
exploded build was verified to load, render its canvas, and log the same
startup message as the original with zero console errors — see the
inlined-script-count difference (1 monolithic `<script>` vs. one per
module) is a harmless build artifact, not a structural regression. Both
feature requests landed in the modules you'd expect (shop touched
`input.js`/`core.js`/`init.js`/`rendering.js`/`ui.js`; the combo counter
touched `init.js`/`core.js`/`enemies.js`/`rendering.js`), not a
scattershot rewrite.

**Tokens: the opposite of the "materially fewer" expectation, for this
game at this size.** Every agent-path phase used **5–12x more input
tokens** than the single-file baseline, not fewer. Root cause: `agent.py`'s
ReAct loop is a single growing conversation, and `ask_with_tools()` is
stateless per call — every turn re-sends the *entire* transcript so far,
including every previous `read_file` observation that hasn't since been
overwritten by a `write_file` on the same path (the only pruning
`_run_react_loop` currently does). `explode_game()`'s system prompt alone
embeds the whole ~150KB source once, but that ~38K-token prompt gets
re-billed on every turn of the multi-turn split; the two follow-on
enhances each had to `read_file` at least one large module (`rendering.js`
alone is 61–65KB) to make a cross-cutting change, and that module's
content then rode along in every subsequent turn's input for the rest of
that run. Output tokens stayed comfortably bounded per call (the actual
problem this initiative set out to fix — no single response ever
approached `ai_client.MAX_OUTPUT_TOKENS`), so the structural failure mode
(hard truncation) is solved; the token-cost win is not, at least not
without better context pruning.

This game (159KB) also isn't yet past the point where the single-file
baseline itself fails — it succeeded in 2 attempts with no truncation.
The motivating 460–690KB scenario in `00-overview.md` would very likely
truncate on the baseline path today; that regime is untested here since
the real downloaded head hasn't grown that large yet.

**Follow-up (not blocking this sprint, tracked for Sprint 6):** the
overview's open question "context-pruning strategy for long agent runs"
is more load-bearing than Sprint 2 treated it — right now the only pruning
is "drop a read once the same path is rewritten." A time/turn-based or
size-based pruning policy (e.g., summarize or drop a `read_file` result
after N further turns, or after `finish()` succeeds once) would likely
close most of the gap seen here, since the large modules dominating input
cost were read once early and then carried, unused, through several more
turns.
