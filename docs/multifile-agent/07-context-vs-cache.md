# Sprint 7 — Context pruning vs. prompt caching

See [00-overview.md](00-overview.md). This sprint exists because Sprint 6's
context pruning and DeepSeek's prompt cache work against each other, and
nobody has measured which one is winning. Everything here is a
**measurement first, change second** sprint: the current defaults were set
by reasoning about resent bytes, without ever looking at
`prompt_cache_hit_tokens`. Now that the `usage` event records that per call
(2026-07-26), the data is sitting in `agent_events` waiting to be read.

## The tension

`ai_client.ask_with_tools()` is stateless, so `_run_react_loop` resends its
whole `messages` list every turn. Sprint 6 attacked that two ways, both of
which **mutate messages already sent**:

- `_compact_write_calls` removes each executed `write_file` call — the tool
  call and its paired result — from the assistant message that made it,
  leaving a short note.
- the staleness pruner rewrites a `read_file` result into a
  `_PRUNE_SENTINEL` placeholder once it's older than
  `context_prune_after_steps` (default 3) turns.

Both are correct about resent bytes. But DeepSeek's cache is a **prefix**
cache: editing a message at position *k* invalidates the cached prefix from
*k* onward, so every turn after a prune re-bills at full price what would
otherwise have been a cache hit. And the pruned read is not free either —
the model frequently re-reads the same file a few turns later (see the
`read_file` counts in [05-migration-and-pilot.md](05-migration-and-pilot.md)
and the job below), so those bytes get paid for twice over: once as an
uncached prefix, once as a fresh read.

Which side wins is an empirical question. It has never been asked.

## What the existing data already says

Two real runs from 2026-07-26, both `deepseek-v4-pro`, both from the
production DB. "Re-billed" below is `max(0, input[n] - cached[n+1])` summed
over the run: in a strictly append-only conversation, turn *n+1*'s prefix
contains everything sent at turn *n*, so anything short of that is prefix
the cache should have covered and didn't.

| job | shape | turns | input | cache hit | re-billed |
|---|---|---|---|---|---|
| `84f45cf5` | explode (write-heavy, few reads) | 33 | 2,497,880 | **89%** | 200,372 (8%) |
| `79a0abbb` | enhance (read-heavy; the 1.58M-token loop) | 41 | 1,477,902 | **53%** | 614,795 (42%) |

The write-heavy explode keeps a nearly intact prefix. The read-heavy
enhance — the one that looped over the same six files and shipped nothing —
loses 42% of its input to prefix invalidation. That is 614K tokens on one
job, and the shape of the run that loses them is exactly the shape pruning
was written to help.

Worst individual turns in `79a0abbb`, by re-billed prefix:

| turn | input | cached | re-billed | previous turn's tools |
|---|---|---|---|---|
| 6 | 49,209 | 2,816 | 57,087 | `write_file` |
| 28 | 49,617 | 10,752 | 50,759 | `write_file` |
| 41 | 94,689 | 43,392 | 49,177 | `read_file` |
| 7 | 16,266 | 2,816 | 46,393 | `write_file` |
| 20 | 45,221 | 7,168 | 44,973 | `write_file` |

Both mutation sites show up. This is consistent with the prefix-cache
explanation but does **not** prove it — a turn that writes a large file also
changes the conversation legitimately, and DeepSeek's cache is best-effort
with its own eviction. Establishing causation is item 1.

## Item 1: Measure it properly

- Add a way to attribute cache misses to mutation. Cheapest honest version:
  a run-level counter of "bytes mutated in already-sent messages this turn"
  emitted alongside the existing `usage` event, so the correlation can be
  computed per turn rather than inferred from tool names.
- Confirm the actual DeepSeek cache-hit vs cache-miss price ratio at the
  time of running this sprint (it has historically been roughly an order of
  magnitude, but do not carry that number forward on trust — this repo has
  already been burned once by a self-confirming constant nobody re-checked;
  see `ai_client.MAX_OUTPUT_TOKENS`). Everything below is only worth doing
  in proportion to that ratio.
- Re-run a comparable enhance twice against the same source and prompt — one
  with pruning as-is, one with pruning disabled entirely
  (`context_prune_after_steps` huge, `_compact_write_calls` a no-op) — and
  compare total *billed* cost, not total tokens. The disabled run resends
  more bytes but should hit cache on nearly all of them.
- Acceptance: a table in this file with real numbers from both runs, and a
  stated verdict on whether pruning currently costs or saves money.

## Item 2: If pruning loses — prune without breaking the prefix

Candidate designs, in rough order of how much they change:

- **Mutate only the tail.** A prune's cache damage is bounded by how far
  back it reaches. Never rewriting anything older than the last N messages
  turns an unbounded invalidation into a bounded one, at the cost of holding
  more history.
- **Batch prunes.** Prune on a byte budget rather than per turn: take one
  large cache break every ~10 turns instead of a small one every turn.
- **Reconsider read pruning entirely.** Its whole job was to stop stale
  whole-file reads riding along. `search` (added 2026-07-27) means a narrow
  question no longer needs a whole-file read at all, so the accumulation
  pressure may already be much lower than when
  `context_prune_after_steps: 3` was chosen. Measure the read volume of a
  post-`search` run before tuning a knob that may no longer bind.
- **Leave `_compact_write_calls` alone unless the data says otherwise.** It
  exists for a correctness reason as much as a cost one — the Sprint 6 stub-
  write bug — and *anything* left in an arguments slot gets imitated. If the
  cache math argues for keeping write calls in history, that argument has to
  clear the imitation problem first, and removal is the only form that has
  ever been safe. Read `_compact_write_calls`' docstring in full before
  touching it.

## Item 3: Bill cached tokens at the cached rate

`app.py`'s `_attach_token_costs` and `static/admin_explode.js`'s `cost()`
both charge every input token at `DEEPSEEK_INPUT_COST_PER_MILLION`, with no
cache-hit rate. The `cached_tokens` column and the `usage` event's
`call_cached_tokens` are already there and already displayed — they just
aren't priced. On the explode above that overstates the bill on 2.2M of
2.5M input tokens, which is most of the run. This matters independently of
items 1–2: without it, any before/after cost comparison made from the admin
History tab is measuring the wrong thing.

- Add `DEEPSEEK_CACHED_INPUT_COST_PER_MILLION` (defaulting to the input rate
  so nothing changes until it is set), thread it through the same two call
  sites, and show the split in the History tab.
- Acceptance: a job whose input was 89% cached reports a materially lower
  cost than the same token count uncached, and the dialog's live readout
  agrees with the History row.

## Notes / prior art

- Sprint 6's pruning work and the stub-write bug that shaped it:
  [05-migration-and-pilot.md](05-migration-and-pilot.md), "Sprint 6 step 2".
- The run that motivated this sprint (`79a0abbb`) also motivated the forced
  last-ditch verification, the `search` tool, and `list_files`'
  `written_this_run` flag, all landed 2026-07-27 — see `CLAUDE.md`'s
  multi-file section. Those reduce how *often* a read-heavy loop happens;
  this sprint is about what it costs when it does.
- Targeted diff edits moved to
  [08-targeted-diff-edits.md](08-targeted-diff-edits.md) and should land
  after this: whether shaving output tokens on a large module is worth the
  complexity depends on what the input side actually costs.
