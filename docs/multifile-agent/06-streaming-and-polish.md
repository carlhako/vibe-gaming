# Sprint 6 — Streaming Polish + Stretch (optional)

See [00-overview.md](00-overview.md). Everything here is **optional** and
independently valuable — pull items forward or drop them based on how the
pilot (Sprint 5) feels. Nothing below is required for the initiative to be
"done"; Sprints 1–5 deliver the working system.

**Status (2026-07-26):** step 1 (investigate the Sprint 5 pilot's high
token usage, not itself a lettered item below but pulled forward first)
found and fixed two real bugs plus a major unrelated ceiling
miscalibration — see
[05-migration-and-pilot.md's "Sprint 6 step 1" section](05-migration-and-pilot.md#sprint-6-step-1-token-pruning-fixes--a-new-open-reliability-bug-2026-07-26)
for the full writeup. That same investigation also surfaced a **new,
unfixed, and still-open reliability bug** (stub-content writes during
`explode_game`, root cause unconfirmed) — read that section before picking
this sprint back up, since it may be worth resolving before continuing
into items A-D below.

## Candidate items

### A. Token-level streaming (true live feel)

- v1 shows steps as they *complete* (per-turn granularity via polling). To
  get the token-by-token Claude-chat feel, switch the agent's model calls to
  DeepSeek's streaming API (`stream=True`) and forward partial tokens.
- Transport: this is where **SSE** (Server-Sent Events) earns its keep —
  a `GET /api/jobs/<job_id>/stream` that pushes deltas. Weigh against
  gunicorn worker occupancy (long-lived connections tie up a sync worker);
  may want a dedicated async worker or a cap on concurrent streams. Keep the
  polling endpoint as the durable fallback and for replay-on-reload.

### B. Job controls

- **Cancel:** a stop button that flips the job to a `cancelled` state the
  agent loop checks between steps (cooperative cancellation), rolling back
  the half-written fork directory.
- **Per-job cost:** surface cumulative input/output tokens and a cost
  estimate live in the conversation pane (reuse the admin history's
  `_attach_token_costs` math).

### C. Targeted diff edits (only once whole-module rewrites are proven)

- Add a `replace_in_file(path, old, new)` tool for surgical edits, cutting
  output further on tiny changes. Gate it behind exact-match validation
  (reject ambiguous/zero/multi matches with a clear message) so it fails
  loudly rather than corrupting a module. Whole-module `write_file` stays the
  reliable default; diffs are an optimization for large modules where a
  one-line change shouldn't rewrite the file.

### D. Module-size hygiene

- A soft lint in the agent/build step: warn (in the transcript) when a
  module drifts past a target size, nudging the agent to split it — keeps
  every module comfortably under the ceiling as the game keeps growing.

### E. Probe the real output ceiling — DONE (2026-07-26, pulled forward into step 1 of this sprint)

Resolved the overview's open question. 65,536 was self-confirming, not a
real ceiling — every prior check passed that exact value as `max_tokens`
and observed truncation at exactly that value, without ever asking for
more. A live probe requesting `max_tokens` as high as 384001 was never
rejected, and a forced long deterministic generation with
`max_tokens=150000` produced exactly 150000 output tokens without stopping
early (`finish_reason == "length"`) — the real ceiling is at least 150000;
DeepSeek's docs claim 384K. `ai_client.MAX_OUTPUT_TOKENS` is now 150000,
and `max_module_bytes` (3x that) is now 450000 — see
`ai_client.py`/`agent.py`'s updated comments and
`05-migration-and-pilot.md`'s pilot-results section for the full probe
transcript.

## Acceptance criteria (per item, if taken)

- Streaming: tokens appear progressively in the conversation pane; polling
  fallback and reload-replay still work; no worker-starvation regression
  under `gunicorn --workers N`.
- Cancel: an in-flight job stops within one step and leaves no partial game
  directory.
- Diffs: `replace_in_file` either applies exactly or is rejected with an
  actionable message — never a silent wrong-match edit; covered by tests.
