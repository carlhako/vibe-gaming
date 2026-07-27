# Sprint 6 — Job Controls + Module-size Hygiene (optional)

See [00-overview.md](00-overview.md). Everything here is **optional** and
independently valuable. Nothing below is required for the initiative to be
"done"; Sprints 1–5 deliver the working system.

**Status (2026-07-26):** step 1 (investigate the Sprint 5 pilot's high
token usage, not itself a lettered item below but pulled forward first)
found and fixed two real bugs plus a major unrelated ceiling
miscalibration — see
[05-migration-and-pilot.md's "Sprint 6 step 1" section](05-migration-and-pilot.md#sprint-6-step-1-token-pruning-fixes--a-new-open-reliability-bug-2026-07-26)
for the full writeup, including the subsequent stub-write root-cause and
fix (step 2) and the explode reliability work that followed it. That work
is done; this sprint is now scoped down to items A, B and D below.

**Item C (targeted diff edits) has left this sprint for good.** It was
briefly split out to a Sprint 8 alongside item A, then absorbed into
[06a-cache-snapshot-and-edits.md](06a-cache-snapshot-and-edits.md) as
`edit_file` — its cost case was settled by 6a's measurement, which found
output tokens cost 240x a cache-hit input token. Item A came back here
when that Sprint 8 was deleted; this file's name was always about
streaming, and streaming's real gunicorn worker-occupancy tradeoff is
worth deciding on its own, separate from these smaller additive items.

**Progress: D and E done; B half done; A not started (revised 2026-07-27,
after Sprint 6a's five steps landed).** Sprint 6a is implemented
(commits `4d98a40`..`95d43f1`) and pending live verification, so its step 0
prerequisite below is satisfied — cached input now bills at the cached rate
everywhere, and a live cost readout will no longer show the ~4x
overstatement the admin History tab used to.

Two corrections to the previous status line, both of which claimed less
was done than actually is:

- **Item B's cost half is largely already shipped**, contrary to the
  earlier note that `agent_chat.js` "still has no renderer for the `usage`
  event role". It has had one since `1d3cfd3` (2026-07-26), the day before
  that line was written: an always-on running-total bar (`#chat-usage-bar`)
  plus a terminal summary, both fed by `usage` events. `admin_explode.js`
  goes further and shows live **USD** using the admin page's per-million
  rate attributes.
- **What is actually left of B is (i) cancel, entirely, and (ii) the
  decision of whether the public status page should show USD at all.** The
  rates are admin-page data attributes behind `ADMIN_TOKEN`; exposing a
  requester's spend to them is a product call, not a missing renderer. If
  the answer is no, B's cost half is done and only cancel remains.

**Next up: item B's cancel.** No `cancelled` job status, stop button, or
cooperative check exists anywhere in the codebase (grep for "cancel" finds
only an unrelated admin rename dialog). The nearest existing machinery is
the global AI kill switch, which `ai_client.py:147` checks per call and
which therefore already aborts an in-flight agent run mid-loop — a per-job
cancel is the same shape scoped to one `job_id`, plus the fork-directory
rollback the agent's failure path already performs.

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
- Acceptance: tokens appear progressively in the conversation pane; polling
  fallback and reload-replay still work; no worker-starvation regression
  under `gunicorn --workers N`.

### B. Job controls

- **Cancel:** a stop button that flips the job to a `cancelled` state the
  agent loop checks between steps (cooperative cancellation), rolling back
  the half-written fork directory.
- **Per-job cost — mostly DONE.** `agent_chat.js` renders `usage` events
  into a live running-total bar plus a terminal summary (step, in, out,
  cached, total), and `admin_explode.js`'s dialog adds a live USD figure
  off the admin page's per-million rate attributes, cached-rate-aware since
  Sprint 6a step 0. The only open piece is whether `/status/<job_id>` — a
  public page with no access to those rates — should show USD too.

### D. Module-size hygiene — DONE (2026-07-26)

A soft lint, not a gate: `agent._write_file` now appends a note to its own
`OK: wrote N bytes to <path>` observation once a write lands past
`module_warn_bytes` (default half of whichever `max_module_bytes` ceiling
applies to the current pass — `agent.DEFAULT_MODULE_WARN_RATIO`, so it
scales down automatically during explode's tighter 120,000 ceiling too).
The write always succeeds; only the note is new. Folding the warning into
the same observation string, rather than a separate event, means it
survives `_compact_write_calls`' note verbatim — the model sees it inline
in the transcript the same turn it wrote the module, and the requester sees
it too, since the transcript is the archive. Configurable via
`multifile_agent.module_warn_bytes` in `config.yaml`. Covered by
`tests/test_agent.py::test_write_past_warn_threshold_succeeds_with_a_soft_lint_note`
and `::test_write_under_warn_threshold_has_no_lint_note`.

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

- Streaming: tokens render progressively, polling replay still works, and no
  worker starvation under `gunicorn --workers N`.
- Cancel: an in-flight job stops within one step and leaves no partial game
  directory.
- Per-job cost: running token/cost totals visible in the chat pane, updating
  as `usage` events arrive.
- Module-size hygiene: a module drifting past the target size produces a
  visible transcript warning without failing the build.
