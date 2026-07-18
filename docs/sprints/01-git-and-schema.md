# Sprint 1 — Git/GitHub + GUID/Schema Foundation

See [00-overview.md](00-overview.md) for the full schema reference and
overall rationale. This sprint has no new user-visible feature — it's
deliberately the lowest-risk sprint, and everything else depends on it.

## Goals

1. Get this project under version control and pushed to GitHub.
2. Migrate `web_games` to the new `game_id`-keyed schema and create the
   other 4 tables (`generation_requests`, `generation_attempts`, `ratings`,
   `access_log`) in one migration event, even though 3 of them stay empty
   until later sprints.
3. Update `app.py` and `game_generator.py` so new games get a `game_id` and
   a GUID-suffixed slug, without breaking the existing `games/sample-game`.

## Part A: Git + GitHub

1. `git init` in `/home/carl/projects/vibegames`.
2. Create `.gitignore`:
   ```
   .env
   config.yaml
   vibegames.db
   __pycache__/
   *.pyc
   venv/
   games/backups/*
   !games/backups/.gitkeep
   ```
3. Confirm with the user: new GitHub repo name and visibility
   (public/private).
4. `git add`, first commit (message describing the pre-existing baseline —
   this is the "vendor drop" commit before any of the new work lands).
5. Create the GitHub repo (via `gh repo create` if the `gh` CLI is
   authenticated, otherwise walk the user through creating it on
   github.com and provide the `git remote add` command).
6. Push and confirm `git status` shows the branch tracking the remote.
7. From here on, each subsequent sprint should land as its own commit (or
   small set of commits) rather than one giant commit — ask the user
   whether they want a PR-per-sprint workflow or direct commits to main.

## Part B: Schema migration

1. In `db.py`:
   - Replace the `web_games` CREATE TABLE with the new schema from
     [00-overview.md](00-overview.md) (adds `game_id` as PK, `slug` as
     UNIQUE, `parent_game_id`, `root_game_id`, `thumbs_up`, `thumbs_down`).
   - Add the `generation_requests`, `generation_attempts`, `ratings`, and
     `access_log` CREATE TABLE statements to the same `SCHEMA` script (all
     `IF NOT EXISTS`, consistent with the existing style).
   - Update `register_web_game()` to accept/require `game_id`, and add
     `parent_game_id`/`root_game_id` params (default `None`/self).
   - Update `get_web_game()` to accept either `game_id` or `slug` lookup
     (needed by `/play/<slug>` which only knows the slug) — add a
     `get_web_game_by_slug(slug, conn=None)` function alongside the
     existing `get_web_game(game_id, conn=None)` (rename semantics: the
     existing function's `slug` param becomes `game_id`).
   - Add a `mint_game_id()` helper (`uuid.uuid4().hex`) and a
     `make_slug(title, game_id)` helper implementing
     `slugify(title)[:40] + "-" + game_id[:8]` (simple slugify: lowercase,
     non-alphanumeric → `-`, collapse repeats, strip leading/trailing `-`).
2. Write a one-off migration script (`migrate_to_guid_schema.py` or a
   function invoked from a `--migrate` flag) that:
   - Backs up `vibegames.db` to `vibegames.db.bak` before touching anything.
   - For any existing `web_games` row using the old schema (currently just
     `sample-game`), mints a `game_id`, sets `root_game_id = game_id`,
     `parent_game_id = NULL`, keeps `slug` unchanged (no directory rename).
   - Writes `game_id`/`parent_game_id`/`root_game_id` into that game's
     `games/<slug>/meta.json`.
   - Is idempotent — safe to re-run (skip rows that already have a
     `game_id`).
3. In `app.py`:
   - Widen `_SLUG_RE` from `{0,49}` to a length that comfortably fits
     `slugify(title)[:40] + "-" + 8 hex chars` (~50 chars) — e.g.
     `^[a-z0-9][a-z0-9-]{0,59}$`.
   - `_build_manifest()` should also surface `game_id` (from `meta.json`,
     falling back to `None` for any game missing it — shouldn't happen
     post-migration but be defensive) so the sidebar can eventually link by
     `game_id` in later sprints.
4. In `game_generator.py`: `generate_game()` mints a `game_id` and derived
   `slug` before writing files (instead of deriving `slug` from the title
   alone), passes both through to `write_game_files()` and
   `db.register_web_game()`, and writes `game_id`/`root_game_id` (=
   `game_id`, since new games have no parent) into `meta.json`.

## Out of scope for this sprint

- No new routes, no job runner, no UI changes — that's Sprint 2.
- `game_enhancer.py` is untouched here (still does in-place mutation) —
  rewritten in Sprint 3.

## Acceptance criteria

- `git log` shows at least one commit, `git remote -v` shows the GitHub
  remote, and the repo is visible on GitHub.
- Fresh `vibegames.db` (delete and let it recreate) has all 5 tables per
  the schema in `00-overview.md` — verify with
  `sqlite3 vibegames.db ".schema"`.
- Running the migration script against a DB containing the old-style
  `sample-game` row produces a `game_id`, `root_game_id = game_id`,
  `parent_game_id = NULL`, and `games/sample-game/meta.json` gains the
  same fields. Running it a second time changes nothing (idempotent).
- `/play/sample-game` still loads correctly (slug unchanged).
- Manually invoking `game_generator.generate_game(...)` against a test
  prompt produces a game directory whose slug matches
  `slugify(title)[:40]-<8 hex chars>`, and the `web_games` row has a
  populated `game_id`.
- Existing tests (if any currently pass) still pass; update any tests that
  assert on the old `web_games` schema.
