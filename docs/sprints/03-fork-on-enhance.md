# Sprint 3 — Fork-on-Enhance + Enhance Page

See [00-overview.md](00-overview.md) for the full schema reference.
Depends on [Sprint 1](01-git-and-schema.md)'s `parent_game_id`/
`root_game_id` columns and [Sprint 2](02-job-runner-and-create.md)'s job
runner + status page (this sprint reuses that machinery with
`kind='enhance'`).

## Goals

Enhancing a game must never destroy the original. Today
`game_enhancer.enhance_game()` backs up the target to `games/backups/`,
overwrites it in place, and restores from backup on failure. This sprint
replaces that with: write a brand-new game directory, link it to its
parent, and leave the source completely untouched — both stay visible in
the sidebar.

## Part A: rewrite `game_enhancer.py`

- `enhance_game(slug_or_game_id, description, requested_by, config,
  db_conn=None, games_dir=None, job_id=None, new_title=None) -> dict`:
  - `resolve_target(...)` now only needs to *read* the source game's
    current `index.html` (as context fed into the enhancement prompt) and
    validate it exists/is registered — it no longer identifies "the thing
    about to be overwritten."
  - Mint a new `game_id` and derived `slug` for the fork, exactly like
    `generate_game()` does for a brand-new game (reuse
    `db.mint_game_id()`/`db.make_slug()` from Sprint 1).
  - Title: use `new_title` if provided (non-empty after stripping); else
    auto-generate `"<source title> (v{n})"` where `n` = `COUNT(*) FROM
    web_games WHERE root_game_id = <source's root_game_id>` at call time
    `+ 1` (so the first fork is "(v2)", matching "v1" being the implicit
    original).
  - Run the same generate-parse-safety-scan-write-smoke_test retry loop as
    `generate_game()` — ideally by extracting the shared attempt loop into
    a common helper in `game_generator.py` (e.g.
    `run_generation_attempts(prompt_builder, slug, title, games_dir,
    config, job_id, ...)`) that both `generate_game()` and
    `enhance_game()` call, rather than duplicating the loop. Evaluate
    during implementation whether this refactor is worth it vs. keeping
    two similar loops — prefer the shared helper if it doesn't
    over-abstract for two callers.
  - On failure of an attempt: delete the half-written *new* directory
    (reuse `game_generator.rollback_game_files()`), retry with the
    concrete failure fed into the next prompt — same pattern as
    `generate_game()`, just never touches the source directory at all.
  - On success: `db.register_web_game(game_id=new_id, slug=new_slug,
    title=..., parent_game_id=source_game_id, root_game_id=source_root_id,
    ..., conn=db_conn)`. Write `game_id`/`parent_game_id`/`root_game_id`
    into the new game's `meta.json`.
  - Result dict shape matches `generate_game()`'s (adds nothing
    enhance-specific beyond what's already implied by
    `parent_game_id`/`root_game_id` being set).
- **Delete**: `backup_game_files()`, `restore_from_backup()`, and any
  `games/backups/` usage in this module. Remove the `backups_dir` param
  from `enhance_game()`'s signature. Leave `games/backups/` directory
  itself in place (with `.gitkeep`) only if something else still
  references it — otherwise remove the dir and its `.gitignore` entry
  added in Sprint 1.

## Part B: job runner integration

- `job_runner._worker_loop` (from Sprint 2) already branches on `kind`;
  wire the `kind='enhance'` branch to call the new `enhance_game(...,
  job_id=job_id, new_title=row["new_title"])`. This means
  `generation_requests` needs a `new_title` column (nullable) — add it in
  this sprint's migration addendum (small `ALTER TABLE generation_requests
  ADD COLUMN new_title TEXT` in `db.py`'s schema setup, guarded so it's
  safe to run against a DB that already has the column — check
  `PRAGMA table_info` before altering, or just add it to the Sprint 1
  CREATE TABLE retroactively if Sprint 1 hasn't shipped to any real DB
  yet).

## Part C: routes + UI

- `POST /games/<game_id>/enhance` — reads `description` (the enhancement
  prompt) and optional `new_title` from the form, ensures `vg_uid` cookie,
  builds `requested_by`, mints `job_id`, inserts `generation_requests` row
  with `kind='enhance'`, `source_game_id=game_id`, `status='queued'`,
  redirects to `/status/<job_id>` — same status page and polling JS from
  Sprint 2, no new status UI needed.
- Sidebar (`templates/index.html`/`static/app.js`): add an "Enhance" link
  per game entry, e.g. `/games/<game_id>/enhance` rendering a small form
  (prompt textarea + optional title field) — can be a simple separate page
  `enhance.html` mirroring `new_game.html`, or a modal; prefer the simple
  separate page for consistency with Sprint 2's pattern.
- Sidebar entries for forked games: no special grouping required (out of
  scope per the overview), but consider a small visual cue (e.g. a
  "↳ enhanced from <parent title>" caption) using `parent_game_id` — nice
  to have, not required for acceptance.

## Acceptance criteria

- Enhancing a game produces a new directory under `games/`, a new
  `web_games` row with `parent_game_id` set to the source's `game_id` and
  `root_game_id` matching the source's `root_game_id`, and the **source
  game's files and `web_games` row are byte-for-byte/row-for-row
  unchanged**.
- Both the original and the fork appear as separate entries in the sidebar
  after the job completes.
- Enhancing a fork (enhancing an already-enhanced game) sets
  `root_game_id` to the original ancestor's `game_id`, not the immediate
  parent's — verify a 3-generation chain (original → v2 → v3) all share
  the same `root_game_id`.
- Leaving `new_title` blank produces `"<source title> (v2)"` on first
  fork, `"(v3)"` on a second fork of the same root, etc.
- A failed enhancement attempt leaves no partial directory behind and
  leaves the source completely untouched.
- `games/backups/` and its associated backup/restore code no longer exist
  (or are confirmed unused) in the codebase.
