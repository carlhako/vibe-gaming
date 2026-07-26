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
is done; this sprint is now scoped down to items B and D below. Token-level
streaming (formerly item A) and targeted diff edits (formerly item C) are
both deferred to [07-targeted-diff-edits.md](07-targeted-diff-edits.md)
until B and D are fully working — streaming in particular has real
gunicorn worker-occupancy tradeoffs worth deciding on its own, separate
from these smaller, additive items.

**Progress: D done, B not started (2026-07-26).** D's soft module-size
lint landed and is covered below. **Next up: item B (job controls —
cancel + live per-job cost).** Neither cancellation nor a `cancelled`
job status exist yet; `agent_chat.js` still has no renderer for the
`usage` event role (it silently skips it), even though the event itself
already carries running token totals per call.

## Candidate items

### B. Job controls

- **Cancel:** a stop button that flips the job to a `cancelled` state the
  agent loop checks between steps (cooperative cancellation), rolling back
  the half-written fork directory.
- **Per-job cost:** surface cumulative input/output tokens and a cost
  estimate live in the conversation pane (reuse the admin history's
  `_attach_token_costs` math). The `usage` event already carries this data
  per call — `agent_chat.js` currently has no renderer for the role and
  silently skips it.

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

- Cancel: an in-flight job stops within one step and leaves no partial game
  directory.
- Per-job cost: running token/cost totals visible in the chat pane, updating
  as `usage` events arrive.
- Module-size hygiene: a module drifting past the target size produces a
  visible transcript warning without failing the build.
