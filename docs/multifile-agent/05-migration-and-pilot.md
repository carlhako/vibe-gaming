# Sprint 5 — Explode + Pilot on Tower Maze

See [00-overview.md](00-overview.md). Depends on Sprints 1–4. This sprint
turns the one real problem game into a multi-file game and proves the whole
initiative on it — with measured numbers, not vibes.

## Goals

1. An AI-assisted "explode" pass that splits a single-file `index.html` into
   `game.md` + `src/` modules.
2. Dual-format enhance: a single-file game can become multi-file on its next
   enhancement.
3. Pilot on the Tower Maze Defense chain and measure the token/reliability
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
  point (the current Tower Maze game can't be re-emitted whole, but it *can*
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

- Explode the current head of the Tower Maze chain; enhance it a few times
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

- The Tower Maze head is a working multi-file game: it builds, scans,
  smoke-tests, and plays identically to its single-file predecessor.
- Enhancing it through the agent no longer truncates, touches only relevant
  modules, and uses materially fewer tokens than the single-file baseline —
  with the numbers recorded.
- Fork lineage survives the single→multi transition (info modal + sidebar
  lineage correct).
- `CLAUDE.md` and the File map reflect the new architecture; `pytest` green.
