# Sprint 7 — Streaming + targeted diff edits for the ReAct agent

See [00-overview.md](00-overview.md). Split out of
[06-streaming-and-polish.md](06-streaming-and-polish.md)'s items A and C,
deferred until Sprint 6's items (B: job controls, D: module-size hygiene)
are fully working — both items here touch the same tool-call loop and
transcript UI those are still landing in.

## Item 1: Token-level streaming (true live feel)

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

## Item 2: Targeted diff edits (only once whole-module rewrites are proven)

Add a `replace_in_file(path, old, new)` tool for surgical edits, so a
one-line change to a large module doesn't require re-emitting the whole
file. Whole-module `write_file` stays the default and fallback; this is an
optimization for large modules where a targeted change is cheap and a full
rewrite isn't.

## Design constraints

- **Exact-match only.** `old` must match the file's current contents
  exactly once. Zero matches or multiple matches must be rejected outright
  with a clear, actionable message — never guess, never apply to the first
  or all matches. This is the same lesson as the stub-write bug in
  [05-migration-and-pilot.md](05-migration-and-pilot.md): a tool that
  silently does something plausible-but-wrong is worse than one that fails
  loudly.
- Whole-module `write_file` remains available and is still what the model
  should reach for by default; `replace_in_file` is additive, not a
  replacement.
- Needs the same context-compaction treatment `write_file` got in Sprint 6
  step 2 — the `old`/`new` arguments are synthesized content too, and
  should not be left to rot in the conversation history after the call
  executes, for the same reasons `_compact_write_calls` exists.

## Acceptance criteria

- `replace_in_file` either applies exactly or is rejected with an
  actionable message — never a silent wrong-match edit.
- Covered by tests: zero-match rejection, multi-match rejection, successful
  single-match replacement, and that executed calls get compacted out of
  history the same way `write_file` calls do.
- A live pilot run shows the agent choosing `replace_in_file` for a small
  targeted change on a large module, with a measurable reduction in output
  tokens versus the equivalent whole-module `write_file`.
