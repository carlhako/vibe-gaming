# Sprint 1 — Multi-File Format + Build-and-Inline (no AI)

See [00-overview.md](00-overview.md) for the full design and locked-in
decisions. This sprint has **zero AI involvement** — it is pure plumbing and
the single biggest de-risk: prove a split source assembles into a served
`index.html` that passes `safety.scan()` and `smoke_test.py` and runs
identically in the sandbox.

## Goals

1. A defined on-disk multi-file game format that coexists with today's
   single-file games (dual-format).
2. A deterministic build step that inlines `src/` into one served
   `index.html`.
3. Generation/serving/scan/smoke all operate on the built artifact, with
   single-file games unchanged.

## Part A: on-disk format

A multi-file game directory:

```
games/<slug>/
  meta.json          # gains "format": "multi-file"
  game.md            # high-level map (see below)
  src/
    index.html       # shell: <link rel="stylesheet" href="style.css">,
                     #        <script src="core.js"></script>, etc.
    style.css
    core.js
    enemies.js       # one or more JS modules
    ...
  index.html         # BUILT artifact (inlined, gitignored-if-derivable? no:
                     # committed so a fresh clone serves without a build run)
```

- `meta.json` gains `"format"`: `"single-file"` (default/legacy) or
  `"multi-file"`. Absent = `"single-file"` so every existing game and
  bundled game keeps working with no migration.
- `game.md` is the map the agent reads first (Sprint 2): a short prose
  description of the game, then a table of `src/` files each with a
  one-line purpose, plus any cross-module conventions (global state object,
  event bus, coordinate system). Authored by hand for the Sprint-1 fixture;
  produced by the explode pass in Sprint 5.
- The built `index.html` **is committed** (not gitignored): a fresh clone
  and the existing disk-scan menu must serve the game with no build step
  having run. The builder is re-run on every successful edit to regenerate
  it.

## Part B: `builder.py` (new file)

```python
def build_game(src_dir: Path) -> str: ...        # returns inlined HTML
def write_built_index(game_dir: Path) -> Path: ... # build_game(src) -> index.html
def is_multi_file(game_dir: Path) -> bool: ...
```

- `build_game` reads `src/index.html` and inlines, **in document order**:
  each `<link rel="stylesheet" href="X.css">` → `<style>…</style>` with the
  file contents; each `<script src="X.js">` → `<script>…</script>` with the
  file contents. External CDN `<link>`/`<script>` (allow-listed hosts) are
  left as-is. Only local, relative, same-directory refs are inlined.
- Deterministic: identical `src/` always yields byte-identical output
  (stable ordering, no timestamps). This matters so the committed artifact
  is diff-stable and tests can assert exact output.
- Refuse-and-report on: a referenced local file missing, a ref escaping
  `src/` (`../`, absolute path), or a nested/circular include. These become
  agent-facing error strings in Sprint 2.
- No minification, no transform — inline verbatim so the served code still
  matches the source line-for-line (keeps `smoke_test.py` console-error line
  numbers meaningful and keeps future edits readable).

## Part C: serving + scan/smoke wiring

- **Serving** (`app.py` `/play/<slug>` and the disk-scan menu): unchanged —
  they already serve `games/<slug>/index.html`. Since the built artifact
  lives at exactly that path, multi-file games serve with no route change.
- **Scan/smoke** now run against the built artifact for multi-file games.
  Factor a small helper the generation pipeline (Sprint 2) will call:
  build `src/` → write `index.html` → `safety.scan(built_html)` →
  `smoke_test.run_smoke_test(index.html)`. For single-file games this is a
  no-op passthrough (there's no `src/`; `index.html` is authored directly).
- `safety.scan()` runs on the **assembled** HTML (that's what ships to the
  browser), so an allow-list/blocklist violation split across modules is
  still caught.

## Part D: fixture + tests

- Add a hand-authored multi-file fixture game under `tests/fixtures/` (a
  tiny real game: `game.md` + `src/index.html` + `src/style.css` +
  `src/core.js`).
- Tests (`tests/test_builder.py`):
  - `build_game` inlines CSS and JS in document order, verbatim.
  - Output is byte-identical across repeated builds (determinism).
  - CDN `<script src="https://…">` from an allow-listed host is left as an
    external tag, not inlined.
  - Missing local ref / `../` escape / nested include each raise the
    documented error.
  - The built fixture passes `safety.scan()` (returns no violations) and,
    with the real Playwright smoke test, loads without console errors.
  - `is_multi_file()` correctly distinguishes the fixture from a single-file
    game.

## Acceptance criteria

- A hand-authored multi-file game builds to a single `index.html` that is
  byte-stable, passes `safety.scan()`, passes `smoke_test.py`, and plays in
  the sandboxed iframe indistinguishably from a single-file game.
- Every existing single-file game and both bundled games still list, serve,
  rate, and enhance exactly as before (dual-format, no regression).
- `pytest` green, including the new `tests/test_builder.py`.
- No AI code paths touched — `ai_client`, `game_generator`,
  `game_enhancer` are unchanged this sprint except for the shared
  build→scan→smoke helper stub that Sprint 2 fills in.
