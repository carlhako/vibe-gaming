# Sprint 6a — Cache discipline, source snapshot, targeted edits

**STATUS: DONE (2026-07-27).** All five steps shipped (commits
`4d98a40`..`95d43f1`), verified live against production job `3beb6dc1`, and
play-tested. Every target below was met or beaten; the projected ~$0.28 → 
~$0.06-0.10 per enhance landed at **$0.047**. See
[Outcome](#outcome-measured-2026-07-27) for the measured before/after and
which of the predicted risks actually bit.

See [00-overview.md](00-overview.md). This sprint **absorbs the whole of the
former Sprint 7 (context pruning vs. prompt caching) and item 2 of the former
Sprint 8 (targeted diff edits)**, both of which are deleted; Sprint 8's item 1
(token-level streaming) went back to
[06-streaming-and-polish.md](06-streaming-and-polish.md) as item A. It is
numbered 6a rather than 7 because Sprint 6's item B (job controls) is still
open and this runs ahead of it.

Sprint 7 was written as a "measurement first, change second" sprint: it laid out
the tension between context pruning and DeepSeek's prefix cache and said nobody
had ever looked at `prompt_cache_hit_tokens` to see which was winning. **That
measurement has now been done** (2026-07-27, against the production DB). The
answer is decisive enough that the sprint collapses from "measure, then maybe
tune a knob" into "delete the pruning and restructure the conversation around
the cache."

---

## The measurement (2026-07-27)

Three consecutive successful enhances of *Sorcerer With A Minigun* (a ~175KB
multi-file game), all `deepseek-v4-pro`, all from the production DB. Per-call
figures come from the `usage` events in `agent_events`, which is the only
per-call accounting that exists.

| job | request | steps | input | cache hit | cache miss | output |
|---|---|---|---|---|---|---|
| `63a41edd` | amulet equip button always shows | 18 | 777,531 | 513,408 | **264,123** | 42,959 |
| `d59b2a37` | investigate amulet freeze bug | 35 | 1,167,160 | 448,768 | **718,392** | 61,239 |
| `d5279657` | add the amulet equip feature | 48 | 1,772,525 | 1,255,296 | **517,229** | 118,106 |

### The price ratio is the whole story

DeepSeek pricing for `deepseek-v4-pro`, **checked 2026-07-27**:

| | per 1M tokens |
|---|---|
| input, cache **miss** | $0.435 |
| input, cache **hit** | **$0.003625** |
| output | $0.870 |

A cache hit costs **1/120th** of a cache miss. (Flash is 1/50: $0.0028 vs
$0.14.) **Re-verify this before trusting it in future work.** This repo has
already been burned once by a self-confirming constant nobody re-checked — see
`ai_client.MAX_OUTPUT_TOKENS`, which sat at 65,536 for months on no evidence.
Everything in this sprint is worth doing only in proportion to that ratio, and
the ratio is a vendor pricing decision that can change.

Costing the three runs at those rates:

| job | miss $ | hit $ | output $ | total | miss = % of cost |
|---|---|---|---|---|---|
| `63a41edd` | 0.1149 | 0.0019 | 0.0374 | **$0.154** | 74.5% |
| `d59b2a37` | 0.3125 | 0.0016 | 0.0533 | **$0.367** | 85.1% |
| `d5279657` | 0.2250 | 0.0046 | 0.1028 | **$0.332** | 66.4% |
| **total** | **0.6524** | **0.0080** | **0.1934** | **$0.854** | **76.4%** |

**Cached input is 0.9% of the combined bill.** Cache-miss input is 76%.

This inverts the premise the pruning was built on. Sprint 6 optimised *resent
bytes*, reasoning that anything left in `messages` gets resent every turn for
the rest of the run. That is true, and nearly irrelevant: a resent byte that is
still byte-identical costs 1/120th. **Retention is not a cost. Mutation is.**

### Verdict on Sprint 7 item 1: pruning costs money

DeepSeek's cache is automatic, byte-exact, and **prefix**-only — a hit requires
the first N tokens of a request to match a prior request exactly, and their own
guidance is not to change the top of the prompt unless necessary
([Context Caching](https://api-docs.deepseek.com/guides/kv_cache/)). Editing a
message at position *k* therefore invalidates everything from *k* onward, for
every remaining turn.

Two sites in `_run_react_loop` do exactly that. Both hold aliased dict
references from `last_read_message` into messages sent many turns ago:

- **`agent.py:1847-1853`** — rewrite a `read_file` result into a
  `_PRUNE_SENTINEL` placeholder when the same path is later written.
- **`agent.py:1856-1865`** — rewrite it when it is simply older than
  `context_prune_after_steps` (6) turns.

The per-step trace of `63a41edd` shows the damage directly. `cached` falling
back to ~4,500 means the cache matched nothing beyond the system + user prompt:

```
step  1  in=  1709  cached=     0  fresh= 1709  read_map
step  3  in=  4385  cached=  2944  fresh= 1441  read_file render.js, combat.js, config.js
step  4  in= 42554  cached=  4480  fresh=38074  search, read_file input.js
step  5  in= 45774  cached= 43776  fresh= 1998  write_file config.js
step  6  in= 44686  cached= 33152  fresh=11534  search
step  7  in=  8334  cached=  4480  fresh= 3854  <-- CACHE COLLAPSE
step  8  in= 39796  cached=  4480  fresh=35316  read_file combat.js — at full price
step  9  in= 45123  cached= 40192  fresh= 4931  read_file render.js
step 10  in= 79790  cached= 47872  fresh=31918  write_file render.js   (out=32,486)
step 11  in= 46891  cached=  5888  fresh=41003  <-- CACHE COLLAPSE
step 12  in= 43956  cached= 32768  fresh=11188  search
step 16  in= 76803  cached= 44672  fresh=32131  read_file input.js
step 17  in= 77464  cached= 42112  fresh=35352  read_file combat.js
```

Each collapse follows a `write_file`, because a write prunes the read it was
based on — and that read is always *early* in the prefix, since the model reads
a file shortly before rewriting it. So the cheapest possible prune site is
chosen every time, and the prune is the deepest possible cut.

Sprint 7's own re-billed-prefix metric, `Σ max(0, input[n] − cached[n+1])`,
computed over two earlier runs, pointed the same way and is preserved here as
prior evidence:

| job | shape | turns | input | cache hit | re-billed |
|---|---|---|---|---|---|
| `84f45cf5` | explode (write-heavy, few reads) | 33 | 2,497,880 | **89%** | 200,372 (8%) |
| `79a0abbb` | enhance (read-heavy; the 1.58M-token loop) | 41 | 1,477,902 | **53%** | 614,795 (42%) |

The write-heavy explode keeps a nearly intact prefix. The read-heavy enhance —
the shape pruning was written to help — loses 42% of its input to prefix
invalidation.

### `_compact_write_calls` is not implicated

It only touches `assistant_msg` and tool results created in the **current**
turn, which no previous request contained. Its `messages[:] = [...]` rebuild can
only drop tool results whose `tool_call_id` is in this turn's `write_records`,
so the surviving prefix is identical object-for-object. It is cache-safe by
construction and **stays**, along with its `_PRUNE_SENTINEL` write-echo guard.

`ai_client.ask_with_tools()` passes `messages` straight through without copying
or reordering, and strips `reasoning_content` from the *returned* message before
the caller appends it. So the two sites above are the only prefix mutations in
the entire system.

### The other two cost drivers

**Whole-file re-reads dominate cache-miss input.** `_read_file` returns entire
files untruncated. `63a41edd` read `render.js` 4x @ 81,655 bytes (327KB of
re-reads for one change); `d5279657` made 36 `read_file` calls, `render.js` 6x
and `player.js` 9x. `search` (2026-07-27) reduced how often this happens but did
not change what a read costs when it does.

**Whole-module rewrites dominate output.** `write_file` requires the COMPLETE
file, so single steps burn 32,486 / 40,913 / 31,777 / 32,218 output tokens to
change a few functions in an 80KB module. At $0.87/M, one output token costs
**240x** a cache-hit input token.

### Why the source snapshot follows

`render.js` is ~46% of that game. The model reads it, rewrites it whole, and
re-reads it — and the run pays cache-miss rates each time because the prune
already killed the prefix. Meanwhile the entire 175KB source is ~44K tokens,
**4% of v4-pro's 1,048,576-token window**
([DeepSeek-V4](https://huggingface.co/blog/deepseekv4)). Handing the model all
of it once, in a block that never changes, costs about what step 4 alone already
costs today (`fresh=38,074`) and removes the reason for every subsequent read.

This is also the shape Anthropic's
[context-engineering guidance](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
recommends: not pure just-in-time retrieval, but a hybrid — Claude Code drops
`CLAUDE.md` into context up front and keeps glob/grep for retrieval at the
agent's discretion. Today's enhance path is pure JIT, which is the wrong end of
that spectrum for a source tree that fits comfortably in the window. **The
explode pass in this very codebase already does the hybrid thing**
(`_build_explode_system_prompt`, `agent.py:1476-1478`, embeds the entire source
HTML in the system message) and is the more reliable of the two passes.

---

## The invariant this sprint establishes

> **No message that has already been included in a request to the model is ever
> mutated or removed.**

Everything below either follows from it or protects it. It is exactly testable
(see the flagship test in "Tests"), and it should be wired into the existing
test runners so all ~35 current agent scenarios enforce it for free.

---

## Step 0 — Price cached tokens (do this first: it is the instrument)

This was Sprint 7 item 3, unstarted. **Without it none of the rest is
measurable.** `app.py:164`'s `_attach_token_costs` charges every input token at
`DEEPSEEK_INPUT_COST_PER_MILLION`, with no cached rate at all — it reports job
`d5279657` at roughly 4x its real cost, and the `84f45cf5` explode (89% cached)
at nearly 10x.

- `app.py:156` `_token_cost` — unchanged.
- `app.py:164-177` `_attach_token_costs` — add a `cached_input_cost_per_million`
  argument. Split: `fresh = max(0, input_tokens - (cached_tokens or 0))`, then
  `input_cost = _token_cost(fresh, input_rate) + _token_cost(cached_tokens,
  cached_rate)`. Add `row["fresh_input_tokens"]` and `row["cached_input_cost"]`
  for display.
- `app.py:1211-1218` — read `DEEPSEEK_CACHED_INPUT_COST_PER_MILLION`,
  **defaulting to `input_cost_per_million`** so nothing changes until it is set.
  Thread it to both call sites.
- `templates/admin_stats.html` — the "Estimated cost assumes $X / 1M input"
  footnote must show the split rate; token cells show `fresh / cached`. Add
  `data-cached-input-cost-per-million` alongside the existing
  `data-input-cost-per-million`.
- `static/admin_explode.js` — `cost(inputTokens, outputTokens)` gains a
  `cachedTokens` argument.
- `.env.example` — add `DEEPSEEK_CACHED_INPUT_COST_PER_MILLION=0.003625` with a
  comment recording the 1/120 ratio, its 2026-07 measurement date, and the
  instruction to re-check it.

`db.py` needs nothing — `cached_tokens` is already a `generation_requests`
column, persisted by `job_runner.py` and returned by `db.get_generation_history`.

**Also add `agent_cost_report.py`** (read-only, no app wiring). Given a
`job_id`, it reads the `usage` events and prints the per-step table used
throughout this document (`step / in / cached / fresh / out / tools`), run
totals, the three-way USD split, and the re-billed-prefix figure
`Σ max(0, input[n] − cached[n+1])`. That last number is the direct read-out of
step 1's effect: it should go to approximately zero. You will run this against
every phase below, so build it before changing anything.

**Acceptance:** a job whose input was 89% cached reports a materially lower cost
than the same token count uncached, and the explode dialog's live readout agrees
with the History row.

---

## Step 1 — Make the conversation append-only

### Deletions

1. **`agent.py:1847-1853`** — the same-path rewrite in the `write_file` branch.
   Delete the whole `stale = last_read_message.pop(...)` block. What it
   communicated ("this path was rewritten") is already carried, better, by two
   surviving mechanisms that mutate nothing: `_compact_write_calls`' note
   (`agent.py:1556-1563`) and `_list_files`' `written_this_run` flag.
2. **`agent.py:1856-1865`** — the staleness sweep. Delete.
3. **`agent.py:1832-1833`** and the `last_read_message` / `last_read_step`
   declarations — now dead. Delete.
4. **`max_read_age_steps`** and its 12-line comment block. Delete.
5. The docstring's "Context pruning" paragraph (`agent.py:1604-1618`) — replace
   with the cache-discipline paragraph below.

### Remove `context_prune_after_steps` outright — do not keep a disabled hatch

`config.yaml` is gitignored and **production's copy already sets
`context_prune_after_steps: 6`**. A hatch that still reads the key would
silently restore the exact regression on the exact machine that matters. This is
the same trap `DEFAULT_AGENT_MODEL`'s comment was written about: anything set
only in `config.yaml` is invisible to every fresh clone and every deployment.
The escape hatch is `git revert`.

Two rails so the removal isn't silent:

- In `_run_react_loop`, if `"context_prune_after_steps" in cfg`, log a warning
  naming this document. Production's stale config then announces itself once per
  run instead of doing nothing mysteriously.
- In `config.yaml.example`, replace the key's comment block with a gravestone
  explaining why it is gone, so nobody re-derives it from first principles in
  six months.

### The docstring paragraph (replaces `agent.py:1604-1618`)

```
Cache discipline (Sprint 6a, docs/multifile-agent/06a-cache-snapshot-and-edits.md):
`ask_with_tools()` is stateless, so every turn resends the whole `messages`
list — but DeepSeek's prefix cache bills a byte-identical resent prefix at
1/120th of a fresh token, so RESENDING IS NEARLY FREE AND MUTATING IS NOT.
Editing a message at position k invalidates the cache from k onward, so one
prune re-bills the entire rest of the conversation at full price, every turn
after it. Measured on job 63a41edd (2026-07-27): pruning a read collapsed
cached tokens from ~44,000 back to ~4,500 — system + user prompt only —
twice in one run. Across three production enhances, cache-MISS input was
68-85% of total cost and cache-HIT input was under 2%.

So this loop is APPEND-ONLY: no message that has already been sent to the
model is ever mutated or removed. `_compact_write_calls` is the one thing
that rewrites anything, and it is cache-safe by construction — it only
touches the assistant message and tool results created in the CURRENT turn,
which no previous request contained. Sprint 6's two read-pruning sites did
not have that property and are gone.
```

### Answering "context will grow unbounded"

State the arithmetic in the doc and the docstring, because this is the one
decision a future reader will want to reverse:

- A retained token costs 1/120th of a fresh one **per subsequent turn**. Held
  for 40 more turns, that is 40/120 = **0.33x** of re-reading it once. Pruning
  to avoid resending only pays if the material is never wanted again — and the
  measured runs re-read `render.js` 4-6x per job.
- The window is 1,048,576 tokens; the pilot game is ~44K. The worst measured run
  was 48 steps.
- The one honest failure mode — a run genuinely approaching the window — is
  handled by **step 4**, append-only.

**Expected result:** re-billed prefix → ~0; cached share up from 66% / 38% / 71%
to >90%; cache-miss input down 35-50%. Steps and output unchanged.

---

## Step 2 — Seed the conversation with a full source snapshot

### Placement

Build it in `enhance_multifile_game`, immediately after `_stage_fork`, reading
from **`dest_dir`** (the staged copy the agent will actually edit). Append it as
the **last section of the system message**, exactly as
`_build_explode_system_prompt` does with `## Original single-file game`.
`_run_react_loop` stays unaware of it.

Order is instructions → snapshot → user request, for two reasons:

1. **Attention.** The request is the last thing read, immediately after the
   material it applies to — the same layout explode uses and passes with.
2. **Cross-run caching.** The system message becomes stable *per source game*,
   with only the user message varying. A second enhance of the same source
   within the cache's lifetime gets the whole 44K block at hit price. To
   preserve this, **never interpolate anything run-specific** — no timestamp, no
   job id, no fork slug — into the system message.

### `_build_source_snapshot(game_dir, max_bytes)`

Returns `(snapshot_text | None, snapshot_paths: frozenset[str], total_bytes)`.

- **Deterministic order** (required for cross-run cache hits): `game.md` first
  (it is the map), then `src/index.html` (whose `<script>` order *is* the
  dependency order), then remaining `src/**` sorted by posix path.
- **Bare tool-call paths** — `render.js`, not `src/render.js`. This reinforces
  `_normalize_agent_path`'s convention instead of fighting it; the documented
  pilot bug where the model created both `index.html` and `src/index.html` came
  from exactly this ambiguity.
- Read with `Path.read_text(encoding="utf-8")` — the same call `_read_file` and
  `_edit_file` use, so "byte-for-byte identical to what you were shown" is a
  well-defined claim. A `UnicodeDecodeError` file is listed in the manifest with
  its body skipped and marked `(not text — use read_file)`.
- Guarantee a newline before each END marker, and annotate
  `(no trailing newline)` in the header when one was added, so the model does
  not invent one.
- Over `max_bytes` → return `None`.

### Marker format

```
===== BEGIN render.js (81655 bytes) =====
<file contents verbatim>
===== END render.js =====
```

Line-anchored, unmistakable, greppable, and carrying the byte count (which feeds
the model's `edit_file`-vs-`write_file` decision). Deliberately **not** a fenced
code block: nesting fences breaks the moment `game.md` contains one, and
`game.md` is a prose document that routinely does.

### Marker imitation is the top predictable failure of this design

The recurring lesson of this initiative is that **the model treats anything in
its transcript as a worked example** — the stub-write disaster, the dropped
`path` key, the "were dropped from the conversation" misreading. A snapshot
introduces a new piece of scaffolding the model can copy into a file. Two
defenses, both borrowed from the `_PRUNE_SENTINEL` precedent:

1. An explicit prompt rule that markers are scaffolding, never file content.
2. A **hard reject** in `_write_file` and `_edit_file` — alongside the existing
   sentinel check — of any `contents` / `new_string` containing a marker line
   (`^=====\s+(?:BEGIN|END)\b.*=====\s*$`, multiline). Same shape of message the
   sentinel rejection uses: loud, actionable, says what to do instead. This
   turns a silent corruption into a self-correcting error, which is the whole
   point of the sentinel guard it copies.

### Prompt wording

Rewrite `_build_system_prompt`'s "## How this game is structured" section. The
load-bearing sentences:

- *"Below is the ENTIRE current source of this game — every src/ file plus
  game.md, complete and unabridged, exactly as it is on disk right now at the
  start of this run. You are not exploring a codebase you cannot see."*
- *"Do NOT call read_map, list_files or read_file for anything the snapshot
  already contains."* — with the two narrow remaining uses named: a file created
  later in this run, and re-reading after a wholesale `write_file`.
- *"The snapshot is authoritative as of the start of this run, and **stays
  authoritative for every file you do not change**."*
- The marker rule.

That last phrasing is deliberate. **Never write a bare hedge** like "the
snapshot may be out of date" — the documented failure mode is the model reading
a hedge as *nothing here is trustworthy* and launching a re-verification sweep,
which is exactly what the earlier "were dropped from the conversation" wording
caused (`agent.py:1546-1554`). Say what is still true, then name the exceptions.

### Fallback above the ceiling

If the snapshot is skipped, still emit the **manifest** line
(`Files (6, 175,410 bytes total): game.md 4,102 · render.js 81,655 · …`) and
keep today's discovery wording. That costs a few hundred bytes, is fully
cache-stable, and already saves the `list_files` turn.

### Transcript emission

Emit **one summary event**, never the body — 175KB per job into `agent_events`
would bloat the events endpoint and the replay path for no benefit, and
`agent_events` is a permanent archive:

```
tool_result: "Loaded source snapshot: 6 files, 175,410 bytes"
data: {"tool": "snapshot", "file_count": 6, "bytes": 175410}
```

Add a `snapshot` icon to `static/agent_chat.js`'s `TOOL_ICON`. Its
`renderToolResult` already falls through safely for unknown tools, so replay of
older jobs is unaffected either way.

### Optional: turn `read_file` into a nudge site

When the model reads a path that is in the snapshot and has *not* been written
this run, prepend one line to the observation: *"this file is unchanged since
the source snapshot in your instructions, which already contains it in full —
this read told you nothing new."* Still return the full contents; refusing
information is what historically triggers state-re-checking loops. This is an
*observation*, not an arguments slot, so it is in the safe category — same shape
as `_write_file`'s soft lint.

### Explode is untouched

`explode_game` calls `_build_explode_system_prompt`, which embeds its own
source, never `_build_system_prompt`, and starts with an **empty** `dest_dir`
where a snapshot builder would find nothing anyway. Add a regression test
asserting explode's system prompt contains no `===== BEGIN` marker.

**Expected result:** ~0 reads of pre-existing files; steps down from 18/35/48
toward 8-15; one ~44K cache-miss block at step 1, then flat.

---

## Step 3 — `edit_file(path, old_string, new_string)`

This is the former Sprint 8 item 2. Its design constraints are adopted verbatim:
**exact-match only**, `old_string` must match exactly once, zero or multiple
matches rejected outright, never guess, never apply to the first or all matches,
`write_file` remains available and is still the tool for creating a file or
genuinely rewriting most of one.

### Renamed from `replace_in_file`

Use `edit_file` / `old_string` / `new_string`. This model imitates patterns it
recognises — the same mechanism behind every transcript bug in this initiative —
and these are the names and semantics it has seen most in training. Matching the
industry-standard shape is a free correctness gain.

### Order of checks in `_edit_file`

1. `_resolve_agent_path`; file must exist → `ERROR: … not found — use write_file
   to create a new file.`
2. Empty `old_string` → rejected, pointing at `write_file`.
3. Marker and `_PRUNE_SENTINEL` guard on **both** strings.
4. `count = text.count(old_string)`; `0` or `>1` → the messages below.
5. `new_string == old_string` → rejected as a no-op. Otherwise it consumes a
   step and, worse, registers as progress.
6. `text.replace(old_string, new_string, 1)`; enforce `max_module_bytes` on the
   **result**, rejecting and leaving the file untouched if over — otherwise a
   module can creep past the ceiling in small increments without ever tripping
   `write_file`'s gate.
7. Write; return `OK: edited {path} (−{a} +{b} bytes, now {size} bytes)`, plus
   the same soft-lint suffix `_write_file` appends past `warn_bytes`.

### Error messages that do not teach bad patterns

- **Never echo a candidate `old_string` back**, and never print "did you mean" —
  anything shaped like a valid argument gets copied.
- **Never show a near-miss diff.** That teaches fuzzy matching, which is the
  exact failure this tool's constraints forbid.
- Do give counts and a directive.

Zero matches should say the text must match the file's **current** contents
character for character; that if the file has already been edited this run, the
text must match it as it is **now**, not as the snapshot showed it; and point at
`search(pattern, path=…)` as the cheap way to find the current text. Multiple
matches should give the count and say to extend `old_string` with the lines
above and below the intended occurrence until it is unique.

### Do **not** compact small edits out of history

This is the one place this sprint deliberately diverges from Sprint 8, which
said `replace_in_file` "needs the same context-compaction treatment `write_file`
got … for the same reasons `_compact_write_calls` exists." The reasons do not
transfer:

1. The arguments are small by construction — that is the entire point of the
   tool. There is nothing meaningful to reclaim.
2. Leaving them is what makes *"the current contents of a file you have edited
   are the snapshot's version with your edits applied, in order"* a **true**
   statement the model can act on. Compacting them away would break the
   snapshot-honesty story from step 2 and force re-reads — reintroducing the
   cost this sprint exists to remove.
3. The Sprint 6 lesson is narrower than "compact everything." It is: **never
   leave *synthesized* arguments in a tool call the model can see.** A
   placeholder written by the harness into an arguments slot gets imitated. A
   genuine, unmodified tool call the model itself emitted is not that — it is
   just ordinary history, which is what every agent keeps. Removal was the only
   safe treatment for a *rewritten* argument; it was never required for an
   honest one.

**But** add a size fuse: an `edit_file` call whose `old_string + new_string`
exceeds `edit_compact_bytes` (default 8,000) is routed through
`_compact_write_calls` alongside the write records, so a pathological 40KB
"edit" cannot ride along for 50 turns. Crucially that uses the **removal** path,
never a rewritten arguments slot — the only form that has ever been safe.

### The compaction note gains a snapshot clause

Extend `_compact_write_calls`' note only when the written paths were actually in
the snapshot, so explode's note stays byte-identical to today's. After the
existing text, add: *"Because you replaced these files outright, the source
snapshot in your instructions no longer shows the current text of: `render.js`.
For those paths, what you just wrote is the current contents. **Every other file
in the snapshot is still exactly as shown there.**"*

The final clause is load-bearing. Without it the model generalises one
invalidation into a blanket one and re-reads everything — the documented
behaviour under the old "were dropped" wording.

### Loop wiring

- `_execute_tool` — dispatch via a new `_parse_edit_args`, mirroring
  `_parse_write_args` including its "the reply may have been cut off" hint for
  malformed JSON.
- `_progress_key` — return `None` for `edit_file`, matching `write_file`: it is
  a mutation, not an observation.
- The write branch — an `OK:` edit sets `made_progress`, `wrote_anything`,
  `edited_since_finish`, adds to `written_paths`, and re-arms `nudged_to_finish`.
  A rejected edit sets none of them, so a model looping on a non-matching
  `old_string` is caught by the existing stall guard exactly as a looping bad
  read is. **Do not weaken the stall guard for edits** — a failed edit costs a
  tiny turn, so five in a row genuinely means stuck.
- `_summarize_tool_call` / `_summarize_observation` — treat like `write_file`.
  **Never put `old_string` or `new_string` in an event**; only byte counts.
  `agent_events` is permanent and rendered in the transcript UI.
- `AGENT_TOOLS` — insert `EDIT_FILE_TOOL` before `WRITE_FILE_TOOL`; array order
  is a weak prior on preference.
- `WRITE_FILE_TOOL`'s description gains: *"For a change to an existing file,
  prefer edit_file — this tool re-emits the whole file and is for creating a
  file or genuinely rewriting most of one."*
- `static/agent_chat.js` — add an `edit_file` icon.

### Module-size hygiene under `edit_file`

Sprint 6 item D's soft lint at half the ceiling was justified by "by the time a
module is near it, a whole-module rewrite has stopped being cheap." `edit_file`
weakens that justification but does not remove it — a 200KB module is still bad
for explode quality, for the eventual forced rewrite, and for human legibility.
**Keep the lint unchanged**; note the shifted rationale in its comment.

**Expected result:** no single step over ~5K output unless a module was
genuinely rewritten; `edit_file` calls outnumber `write_file` calls.

---

## Step 4 — Append-only context guard

This replaces pruning as the answer to unbounded growth, and never touches the
prefix.

Add `CONTEXT_WINDOW_TOKENS = 1_048_576` to `ai_client.py` beside
`MAX_OUTPUT_TOKENS`, with the same "this is what the docs claim as of 2026-07 —
re-verify before trusting it" comment that constant earned.

In `_run_react_loop`, right after the usage accounting, using the authoritative
`ask_result.input_tokens` rather than a byte estimate:

- Past `context_soft_limit_tokens` (default 700,000) and not yet warned, append
  **one** user message: *"CONTEXT WARNING: this conversation is now N tokens of a
  1,048,576 limit. Stop exploring, make the edits that remain, and call
  finish(summary)."*
- Past 95% of the window, `break` with an error. The **forced final
  verification** then still ships whatever is on disk, which is strictly better
  than the API 400-ing mid-run.

---

## Config

Under `multifile_agent:` in `config.yaml.example` (the authoritative,
non-gitignored record):

```yaml
  snapshot_max_bytes: 400000    # src/ + game.md total above which the run falls
                                # back to just-in-time discovery and only a file
                                # manifest is inlined. 400000 B ~= 100K tokens,
                                # ~10% of the 1,048,576-token window, leaving
                                # ample room for 60 turns of transcript. The
                                # pilot game is ~175KB, so 2.3x headroom.
                                # 0 disables the snapshot entirely.
  edit_compact_bytes: 8000      # an edit_file call whose old_string+new_string
                                # exceed this is REMOVED from the transcript
                                # after it executes, exactly as write_file calls
                                # are. Smaller edits stay visible on purpose:
                                # that is what makes "current contents =
                                # snapshot + your edits" true.
  context_soft_limit_tokens: 700000   # append a "wrap it up" nudge past this;
                                # hard-stop at 95% of the context window.
  # context_prune_after_steps   # REMOVED 2026-07. Rewriting an already-sent
                                # read_file result collapsed DeepSeek's prefix
                                # cache; cache-hit tokens bill at 1/120th, so
                                # every prune re-billed the whole rest of the
                                # run at full price to avoid resending bytes
                                # that were nearly free. Setting it again does
                                # nothing (agent.py logs a warning).
```

Mirror all three as code defaults in `agent.py` beside `DEFAULT_AGENT_MODEL`,
**for the gitignored-config reason that constant documents** — a config-only
default never reaches a fresh clone or a deployment.

---

## Tests

### Updated

| test | change |
|---|---|
| `test_agent.py::test_stale_read_file_result_is_pruned_after_configured_step_age` | **delete** — it tests the removed behaviour |
| `test_agent.py::test_no_turn_ever_carries_a_synthetic_write_file_arguments_payload` | extend to `edit_file` |
| `test_agent.py::test_compaction_note_states_the_write_succeeded` | assert the new snapshot-superseded sentence |
| `test_agent.py`'s `_run` and `test_explode.py`'s runner | call `_assert_append_only(seen)` on **every** scenario |
| `test_agent.py`'s `CONFIG` | add `snapshot_max_bytes` so the fixture game is snapshotted by default |

### The flagship test

`_run`'s `scripted()` already deep-copies `messages` before each call, so turn
*i*'s new messages land strictly after `len(seen[i])`. The invariant is
therefore exact:

```python
def _assert_append_only(seen):
    for i in range(len(seen) - 1):
        prev, nxt = seen[i], seen[i + 1]
        assert len(nxt) >= len(prev)
        assert nxt[:len(prev)] == prev, (
            f"turn {i+1} mutated the prefix already sent at turn {i} — "
            "this collapses DeepSeek's prompt cache"
        )
```

It holds even through `_compact_write_calls`, including its `messages[:] = [...]`
removal, because everything that touches is beyond `len(seen[i])`. Wire it into
both runners so every existing scenario enforces it, plus a dedicated test over
a read-heavy script (read, read, write same path, read, read, write) that would
have tripped **both** deleted prune sites.

### New — snapshot

- every source file appears verbatim between its BEGIN/END markers, with correct
  byte counts, in the documented order
- marker lines use bare tool-call paths (no `src/` prefix)
- above `snapshot_max_bytes`, the snapshot is skipped and only a manifest is sent
- **the snapshot is byte-identical across two runs of the same source** — the
  cross-run cache property; this guards against someone later interpolating a
  timestamp or slug
- a write containing a marker line is rejected and never reaches disk
- the compaction note names only the written paths as superseded
- explode's system prompt has no snapshot and its note never mentions one

### New — `edit_file`

Unique match applies; zero matches and multiple matches are each rejected with
the file **byte-identical on disk**; empty `old_string` rejected; identical
old/new rejected as a no-op **and not counted as progress**; empty `new_string`
deletes the span; post-edit size over `max_module_bytes` rejected with the file
untouched; marker or sentinel in `new_string` rejected; a successful edit shows
up as `written_this_run` in a later `list_files`; **a small edit call is still
present, with its real arguments, in the next turn's messages**; an edit over
`edit_compact_bytes` is removed leaving no synthetic arguments; five consecutive
failed edits trip the stall guard and the run is still force-verified.

### New — cost accounting

`_attach_token_costs` unit tests: a row with 1.77M input / 1.26M cached bills the
517K fresh at the miss rate and the rest at the hit rate; with
`DEEPSEEK_CACHED_INPUT_COST_PER_MILLION` unset, today's numbers reproduce exactly.

---

## Live verification

Baseline is the three jobs at the top. Run each phase against the **same source
game and a request of comparable scope**, then `agent_cost_report.py <job_id>`.

| after | primary signal | target |
|---|---|---|
| step 0 | admin History cost | `d5279657` reports ~$0.33, not ~$0.79 |
| step 1 | re-billed prefix; cached % | re-billed <5% of input; cached >90%; **no step drops back to ~4.5K cached** |
| step 2 | `read_file` count; steps | ~0 reads of pre-existing files; steps 18/35/48 → 8-15 |
| step 3 | output tokens; tool mix | no step over ~5K output without a genuine rewrite; `edit_file` > `write_file` |

Report per run: cache-miss / cache-hit / output tokens and USD at all three real
rates, plus steps and the per-step table. Projected combined effect: from ~$0.28
average per enhance to **~$0.06-0.10**, with the residual dominated by output.

**Non-negotiable qualitative gate: play-test the enhanced game.** Every
optimisation here changes what the model can see, and
[05-migration-and-pilot.md](05-migration-and-pilot.md) is explicit that
build → scan → smoke does not prove a game still *plays* the same.

---

## Outcome (measured 2026-07-27)

All five steps were deployed together rather than verified one at a time, so
the figures below are the combined effect. Job `3beb6dc1`, *Sorcerer With A
Minigun* v45, `deepseek-v4-pro`: *"the glowing purple square that is meant to
show randomly after wave 5 has a bug — the square is invisible"*. Same game as
all three baselines (successive versions v41→v45) and, like two of them, a bug
fix — so this is a like-for-like comparison, not a favourable one.

| | `63a41edd` *(bugfix)* | `d59b2a37` *(bugfix)* | `d5279657` *(feature)* | **`3beb6dc1` (6a)** |
|---|---|---|---|---|
| steps | 18 | 35 | 48 | **13** |
| `read_file` | 13 | 29 | 36 | **2** |
| `write_file` | 2 | 4 | 11 | **0** |
| `edit_file` | — | — | — | **4** |
| output tokens | 42,959 | 61,239 | 118,106 | **8,364** |
| cache hit | 66.0% | 38.4% | 70.8% | **92.2%** |
| re-billed prefix | 24.0% | 60.4% | 24.9% | **0.0%** |
| duration | 394s | 576s | 1046s | **141s** |
| **cost** | $0.1541 | $0.3674 | $0.3323 | **$0.0470** |

Against the targets: re-billed prefix `<5%` → **0 tokens exactly**; cached
`>90%` → 92.2%; steps `8-15` → 13; `edit_file > write_file` → 4 vs 0; no step
over ~5K output → max 2,346. Against the nearest-scope baseline (`63a41edd`,
same game, also a bug fix): **3.3x cheaper, 5.1x less output, 1.4x fewer
steps**; against the mean of both bug-fix baselines, 5.5x cheaper. Gameplay
was confirmed correct by hand.

The cached-token column is a clean monotonic staircase (68,352 → 90,240),
never once dropping — the append-only invariant holding under production
conditions rather than only in `scripted_asks`. **Zero re-billed prefix
tokens** is the direct read-out of step 1 and the single cleanest number here:
the run mutated nothing it had already sent.

The shape of the run is worth recording, because it is the shape the sprint was
designed to produce and nothing in the prompt asks for it explicitly:

```
3 x search  ->  2 x read_file  ->  4 x edit_file  ->  3 x search  ->  finish (passed first try)
```

Locate with `search`, edit in place, verify with `search`. The run touched
three modules of a 13-file, 182KB game — including adding 5,053 bytes of new
code to an 87KB `render.js` — and its **entire** output budget was 8,364
tokens. Under whole-module rewrites, the two `render.js` edits alone would have
cost ~87KB of output each. That single substitution is where most of the saving
lives; the cache work is what makes the *input* side stop mattering.

### Which risks actually bit

- **1 (marker imitation)** — did not occur. No write or edit was rejected for a
  snapshot marker.
- **2 (exact-match reproduction from a large snapshot is unproven on v4-pro)** —
  **did not bite, and this was the sprint's biggest open bet.** 4 of 4
  `edit_file` calls matched on the first try; the rejection rate was 0%, against
  a "reconsider above ~30%" threshold. Reproducing an exact span from a
  68K-token snapshot is evidently within v4-pro's reach.
- **3 (the model re-reads anyway)** — **bit, mildly.** It read `combat.js` and
  `enemies.js` (36,783 bytes) at step 4 despite both being in the snapshot,
  costing 12,367 fresh tokens at step 5: ~15% of the run's fresh input and ~11%
  of its cost. The read nudge fired and did not deter it. This is the one
  measured inefficiency left, and it is small. **Do not escalate to withholding
  bytes on one data point** — that is exactly the refusing-information move this
  file warns causes state-re-checking loops. If the pattern repeats across
  several runs, the lever is prompt wording.
- **4 (cache eviction between long thinking-mode turns)** — did not occur; the
  staircase above is the evidence.
- **5 (a larger system prompt buries the instructions)** — did not occur. The
  model used `search` first rather than exploring blindly, which is snapshot-era
  behaviour, not pre-snapshot behaviour.
- **6 (production's `config.yaml` still sets `context_prune_after_steps`)** —
  **confirmed as designed.** The ignore-and-warn rail fired on the first
  production run after deploy, with the key still present. That warning was also
  the cheapest available proof the new code was actually live.

---

## Risks, in the order they are likely to bite

*(Written before the sprint. See [Outcome](#outcome-measured-2026-07-27) above
for which of these actually bit — only 3 and 6 did, and 6 as designed.)*

1. **Marker imitation** — the model writes `===== BEGIN render.js =====` into a
   file. Mitigated by the hard reject plus the explicit prompt rule. Same class
   as the stub-write disaster; deserves the same paranoia.
2. **Exact-match reproduction from a 44K-token snapshot is unproven for
   v4-pro.** Claude Code's experience does not transfer automatically. Mitigated
   by: a failed edit costs a tiny turn (versus a failed 32K-token write), the
   zero-match message points at `search`, and `write_file` remains the fallback.
   *Watch the edit-rejection rate.* Above ~30%, the fix is to tell the model to
   anchor `old_string` on a function signature line — not to abandon the tool.
3. **The model re-reads anyway**, ignoring the snapshot. Mitigated by the read
   nudge and `_progress_key`. Only escalate to withholding bytes if the trace
   shows it, since refusing information is what historically triggered
   state-re-checking loops.
4. **Cache eviction between long thinking-mode turns** would collapse cached
   tokens even with a perfect prefix. Detect it as a cached-token drop on a turn
   where the code mutated nothing. It argues for shorter runs, which the
   snapshot already delivers.
5. **A larger system prompt buries the instructions.** Mitigated by snapshot
   last, request after it — the layout explode already passes with.
6. **Production's `config.yaml` still sets `context_prune_after_steps: 6`.**
   Mitigated by the ignore-and-warn rail; verify the warning appears in the
   first production run after deploy.

---

## Documentation to update **as part of this sprint** — DONE

All three `CLAUDE.md` items below landed with their respective commits, and the
measured before/after is folded in above.

`CLAUDE.md`'s multi-file section currently documents pruning,
`context_prune_after_steps`, and whole-file `write_file` as live behaviour —
which is accurate today and must stay accurate. Update it **when the code
changes, not before**:

- replace the pruning paragraph with the append-only invariant and the 1/120
  arithmetic
- add the source snapshot and `edit_file` to the tool list and the file map
- add the marker guard to the existing "Never leave synthesized arguments in a
  tool call the model can see" section — it is the same lesson, and that section
  is where a future reader will look for it

Also fold the measured before/after into this file once the pilot runs, so the
verdict is recorded next to the prediction.
