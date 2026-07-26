# Sprint 5 — Explode + Pilot on Sorcerer With A Minigun

See [00-overview.md](00-overview.md). Depends on Sprints 1–4. This sprint
turns the one real problem game into a multi-file game and proves the whole
initiative on it — with measured numbers, not vibes.

## Goals

1. An AI-assisted "explode" pass that splits a single-file `index.html` into
   `game.md` + `src/` modules.
2. Dual-format enhance: a single-file game can become multi-file on its next
   enhancement.
3. Pilot on the Sorcerer With A Minigun chain and measure the token/reliability
   delta versus the single-file baseline.

## Part A: the explode pass

- `explode.py` (or an `agent.py` mode): given a single-file game, ask the
  model to split it into a shell `src/index.html` + `src/style.css` +
  cohesive `src/*.js` modules, and to author `game.md` (the map). It must be
  **behavior-preserving**: build → `safety.scan()` → `smoke_test.py`, and
  the built artifact should be functionally identical to the original.
- This is itself an output-bounded operation: the model emits one module per
  `write_file` call (Sprint 2's tools), never the whole game at once — so
  explode works even on a game already at the ceiling, which is the whole
  point (the current Sorcerer With A Minigun game can't be re-emitted whole, but it *can*
  be read in and written out module-by-module).
- Verification beyond smoke: a brief manual play-test of the exploded build
  in the browser preview, since "console-error-free" doesn't prove the game
  still *plays* the same. Note any behavior drift in the PR.

## Part B: dual-format enhance policy

- Decide and implement the trigger (open question from the overview):
  - **Recommended:** when an enhance targets a single-file game whose built
    size is near the ceiling (reuse the `LARGE_SOURCE_BYTES` notion already
    in `game_enhancer`), run explode first, then do the enhancement on the
    resulting multi-file fork. Small games stay single-file and cheap.
- The fork lineage is preserved across the format change: the exploded/
  enhanced result still links `parent_game_id`/`root_game_id` to the
  single-file source, so the sidebar lineage and info-modal ancestor chain
  stay intact across the single→multi boundary.

## Part C: pilot + measurement

- Explode the current head of the Sorcerer With A Minigun chain; enhance it a few times
  through the agent (real feature requests like the ones in the DB).
- Record, in the PR and in `docs/multifile-agent/`:
  - input/output tokens and attempts per enhance, **agent (multi-file) vs.
    the single-file baseline** for a comparable change.
  - whether any enhance still hit truncation (expected: none — no single
    output is whole-game-sized anymore).
  - subjective edit reliability (did targeted rewrites stay correct?).

## Part D: docs + tests

- Update `CLAUDE.md`: document the multi-file format, `builder.py`,
  `agent.py`, the `agent_events`/events API, the live chat UI, and the
  dual-format enhance policy. Update the File map.
- Tests: explode a small single-file fixture → valid multi-file game whose
  build passes scan+smoke; dual-format enhance of a near-ceiling single-file
  fixture produces a multi-file fork with correct lineage.

## Acceptance criteria

- The Sorcerer With A Minigun head is a working multi-file game: it builds, scans,
  smoke-tests, and plays identically to its single-file predecessor.
- Enhancing it through the agent no longer truncates, touches only relevant
  modules, and uses materially fewer tokens than the single-file baseline —
  with the numbers recorded.
- Fork lineage survives the single→multi transition (info modal + sidebar
  lineage correct).
- `CLAUDE.md` and the File map reflect the new architecture; `pytest` green.

## Pilot results (2026-07-26)

Ran against the real production head of this chain — "Darkhold Arena —
Wave RPG (v37)" (159,312 bytes single-file), downloaded from the live
server — in a scratch DB/games_dir (never touched `vibegames.db` or the
real `games/` directory), using the real DeepSeek API
(`deepseek-v4-flash`, effort `high`). One baseline enhance via the legacy
whole-file path, then `explode_game()` + two real feature enhances via the
agent, all against the same source:

| phase | request | attempts | input tokens | output tokens | wall time |
|---|---|---|---|---|---|
| baseline (single-file) | "Add a boss wave every 5th wave with a unique telegraphed attack pattern" | 2 | 114,275 | 112,862 | 540s |
| explode | (format conversion only) | 1 | 1,431,996 | 68,256 | 345s |
| agent enhance #1 | "Add a shop between waves to spend gold on temporary buffs" | 1 | 696,702 | 55,898 | 301s |
| agent enhance #2 | "Add a combo counter that rewards bonus gold for quick kills" | 1 | 513,274 | 36,974 | 214s |

**Reliability: as hoped.** All four runs succeeded on effectively the
first real attempt (the baseline's "2 attempts" was a smoke-test retry,
not a truncation), no run hit `finish_reason == "length"`, and the
exploded build was verified to load, render its canvas, and log the same
startup message as the original with zero console errors — see the
inlined-script-count difference (1 monolithic `<script>` vs. one per
module) is a harmless build artifact, not a structural regression. Both
feature requests landed in the modules you'd expect (shop touched
`input.js`/`core.js`/`init.js`/`rendering.js`/`ui.js`; the combo counter
touched `init.js`/`core.js`/`enemies.js`/`rendering.js`), not a
scattershot rewrite.

**Tokens: the opposite of the "materially fewer" expectation, for this
game at this size.** Every agent-path phase used **5–12x more input
tokens** than the single-file baseline, not fewer. Root cause: `agent.py`'s
ReAct loop is a single growing conversation, and `ask_with_tools()` is
stateless per call — every turn re-sends the *entire* transcript so far,
including every previous `read_file` observation that hasn't since been
overwritten by a `write_file` on the same path (the only pruning
`_run_react_loop` currently does). `explode_game()`'s system prompt alone
embeds the whole ~150KB source once, but that ~38K-token prompt gets
re-billed on every turn of the multi-turn split; the two follow-on
enhances each had to `read_file` at least one large module (`rendering.js`
alone is 61–65KB) to make a cross-cutting change, and that module's
content then rode along in every subsequent turn's input for the rest of
that run. Output tokens stayed comfortably bounded per call (the actual
problem this initiative set out to fix — no single response ever
approached `ai_client.MAX_OUTPUT_TOKENS`), so the structural failure mode
(hard truncation) is solved; the token-cost win is not, at least not
without better context pruning.

This game (159KB) also isn't yet past the point where the single-file
baseline itself fails — it succeeded in 2 attempts with no truncation.
The motivating 460–690KB scenario in `00-overview.md` would very likely
truncate on the baseline path today; that regime is untested here since
the real downloaded head hasn't grown that large yet.

**Follow-up (not blocking this sprint, tracked for Sprint 6):** the
overview's open question "context-pruning strategy for long agent runs"
is more load-bearing than Sprint 2 treated it — right now the only pruning
is "drop a read once the same path is rewritten." A time/turn-based or
size-based pruning policy (e.g., summarize or drop a `read_file` result
after N further turns, or after `finish()` succeeds once) would likely
close most of the gap seen here, since the large modules dominating input
cost were read once early and then carried, unused, through several more
turns.

## Sprint 6 step 1: token-pruning fixes + a new open reliability bug (2026-07-26)

Followed up on the token-cost finding above. Three real, verified fixes
landed; one new, unrelated, and still-open reliability bug was found along
the way and is **not** fixed — flagged here for whoever picks it up next
(the plan is a fresh session, since this one is deep in used-up context).

### Fixed: write_file arguments were never pruned (the dominant cost)

`_run_react_loop` only ever pruned a `read_file` *result* once the same
path was rewritten. It never touched a `write_file` call's own
**arguments** — the complete new file contents, sitting inside the
assistant message that made the call — so every module a run wrote (tens
of KB, JSON-escaped) rode along resent in full on every subsequent turn
for the rest of the run. This is almost certainly what dominated the
5-12x token blowup measured above, well ahead of stale reads. Fixed in
`agent.py`'s `_squash_write_call_arguments`: immediately after any
`write_file` call executes (success or rejection), its arguments in the
assistant message are replaced with a short placeholder. Covered by
`tests/test_agent.py::test_successful_write_file_arguments_are_squashed_out_of_history`
and `::test_rejected_write_file_arguments_are_also_squashed`.

Also added, more modestly: a `read_file` result now also gets pruned once
it goes *stale* (outstanding more than `cfg["context_prune_after_steps"]`
steps, default 3, without being rewritten) — not just once the same path
is rewritten. Covered by
`::test_stale_read_file_result_is_pruned_after_configured_step_age`.

### Fixed: the first version of that fix broke write_file reliability

A real re-pilot of the same explode (same downloaded Darkhold Arena
source) with the first version of the squash immediately regressed: every
large `write_file` call started failing with `"malformed write_file
arguments: missing a non-empty 'path'"`, retrying successfully, then
failing again on the next large write — until it ran out of retries
without ever finishing. Root cause: the placeholder dropped `"path"`
entirely (`{"contents": "[omitted...]"}"`), and the model very likely
pattern-matched its own prior tool-call shape from further up the same
conversation — seeing its own past self "successfully" call `write_file`
with no `"path"` key taught it that shape was fine to repeat. Fixed by
keeping `"path"` in the placeholder and only squashing `"contents"`.
Covered by an assertion in both squash tests above
(`arguments["path"] == "core.js"`).

### Fixed: `ai_client.MAX_OUTPUT_TOKENS` was never really 65536

Unrelated to the pruning work, but found while chasing the same failures:
the 65536 figure this whole initiative was built around was self-
confirming, not a real ceiling. Every prior "verification" (including the
one `ai_client.py` cited as "verified live") always passed
`max_tokens=65536` explicitly — every caller's default *is* this constant
— so of course every truncation landed at exactly 65536; nobody had ever
asked the API for more. A direct live probe this session:

- Requested `max_tokens` up to 384001 (one over DeepSeek's own documented
  384K ceiling per `api-docs.deepseek.com`, fetched live) — never
  rejected, in both thinking and non-thinking mode.
- Forced a long, deterministic generation ("count from 1 to 100000, one
  per line") with `max_tokens=150000` — got back exactly 150000 output
  tokens, `finish_reason == "length"`, i.e. still generating and not
  stopping early on its own.

So the real ceiling is confirmed at **at least 150000** (DeepSeek's docs
claim 384K, but that number itself is unverified here — 150000 is the one
actually proven live). Raised `ai_client.MAX_OUTPUT_TOKENS` to 150000 and
`agent.DEFAULT_MAX_MODULE_BYTES`/`config.yaml.example`'s
`max_module_bytes` (3x that) to 450000 accordingly. This doesn't remove
the motivation for the multi-file initiative — a multi-file source still
avoids a whole-game read+write on every enhancement — but it does mean
single-file games have roughly 2.3x more headroom than assumed before
ever hitting the wall this initiative exists to solve. See
`00-overview.md`'s now-resolved open question and `ai_client.py`'s comment
on `MAX_OUTPUT_TOKENS` for the full detail.

### Stub-content writes during explode (ROOT-CAUSED AND FIXED in step 2 below — this section is the evidence as it was recorded, including two conclusions that turned out to be wrong)

Across three real re-runs of the same explode tonight — before any fix,
after the path fix, and after raising the output ceiling — the model
repeatedly writes suspiciously small (~100-120 byte) **stub content** for
modules clearly intended to be several KB, then its own chain-of-thought
contradicts the tool result it just received, e.g.:

```
[ 70] tool_call    write_file('src/map.js', 113 bytes)
[ 71] tool_result  OK: wrote 113 bytes to src/map.js
[ 72] thought      Let me write the full map.js file now.
[ 73] tool_call    write_file('src/map.js', 114 bytes)
[ 74] tool_result  OK: wrote 114 bytes to src/map.js
[ 75] thought      Hmm, it keeps saying 113 or 114 bytes. Let me check what's actually in the file.
[ 76] tool_call    read_file('src/map.js')
[ 77] tool_result  Read src/map.js (114 bytes)
[ 78] thought      Wait, it said "OK: wrote 4840 bytes" now! So it did write the larger file.
[ 79] tool_call    read_file('src/map.js')
[ 80] tool_result  Read src/map.js (114 bytes)
[ 81] thought      It seems like read_file is returning the history of writes rather than
                   the actual content. This is odd. But the files are being written.
```

The model's own narration ("it said wrote 4840 bytes") does not match the
actual tool result shown two lines above it (114 bytes) — a genuine
confabulation, not a display/logging artifact (`_summarize_observation`
and the raw `content` string agree with each other; the model is simply
wrong about what it's looking at). This happens on **first writes of
brand-new files** with nothing yet squashed/pruned, so it cannot be caused
by either fix above. All three re-runs eventually stall out (`agent gave
up without a passing finish` / no-progress budget exhausted) with 1-2.6M
input tokens burned and nothing shipped.

> **Correction (step 2).** Two claims in the paragraph above are wrong, and
> they're what kept this open. It is *not* a confabulation — the model had
> read the stub file back, and the stub's contents literally contained the
> sentence "OK: wrote 4840 bytes". And "nothing yet squashed/pruned" is
> true only of *that file*; other files' write calls had already been
> squashed earlier in the same conversation, which is where the stub text
> came from. See the next section.

Leading hypothesis, unverified: DeepSeek's thinking mode shares one output
budget between `reasoning_content` and the actual response (including
tool-call arguments — see `ai_client.py`). `explode_game`'s system prompt
alone embeds the whole ~150KB original source, and the conversation keeps
growing across a long tool loop; it's plausible the model is running out
of *effective* per-turn budget for a large `write_file` argument after
spending most of a turn's allowance on reasoning, and silently defaulting
to a short stub rather than erroring — with its own reasoning trace (which
isn't sent back to the API, so it never "sees" its own contradiction next
turn) narrating success it didn't actually achieve. Not confirmed: this
could equally be something else about very long tool-calling conversations
combined with a huge embedded source, unrelated to thinking-mode budget
specifically.

**Not fixed. Explicitly left open for a fresh investigation session** (this
session is deep enough into used context that continuing here isn't
efficient). Suggested starting points for whoever picks this up:
- Re-run the same explode with thinking mode forced off (`effort` other
  than `"high"`/`"max"`) to see if the stub-writing stops — cheap,
  isolates the leading hypothesis directly.
- Try explode on a smaller single-file game to see if the failure rate
  scales with source size / conversation length, or is present regardless.
- Consider restructuring `explode_game` so the original source isn't
  embedded whole in the system prompt on every turn (e.g. feed it via a
  prunable early message, or chunk it) — independent of whether it turns
  out to be the actual cause, it's the single biggest fixed cost in every
  turn of that specific path.

## Sprint 6 step 2: the stub-write bug, root-caused (2026-07-26)

**The stub content *was* the pruning placeholder, copied back by the
model.** The thinking-mode output-budget hypothesis above was wrong, and no
API calls were needed to establish that — the arithmetic settles it.

Step 1's `_squash_write_call_arguments` replaced an executed `write_file`
call's arguments with:

```
[omitted from history after execution: OK: wrote 4840 bytes to src/map.js; call read_file to see current contents]
```

That string is **exactly 114 bytes**. Built from a 113-byte write it is
**exactly 113 bytes**. Across plausible filenames and byte counts the
placeholder spans **110–118 bytes** — precisely the reported "~100-120 byte
stub" band, hitting both observed values (113, 114) on the nose. The
generating expression is `79 + len(observation)`, and the observation is
itself `OK: wrote <n> bytes to <path>`.

With that, every line of the transcript excerpt above reads literally, with
no confabulation anywhere:

- `[73] write_file('src/map.js', 114 bytes)` — the model copied the
  placeholder text into the `contents` argument.
- `[77] Read src/map.js (114 bytes)` — reading it back returns that
  placeholder text, because that is now genuinely the file's content.
- `[78] "Wait, it said 'OK: wrote 4840 bytes' now!"` — **the file's own
  contents contain that sentence.** The model is quoting the file it just
  read.
- `[81] "read_file is returning the history of writes rather than the
  actual content"` — the model was *correct*. The pruning code had put
  write-history text in the `contents` slot; the model wrote it to disk;
  `read_file` faithfully returned it.

### Why it was self-reinforcing

The loop has a fixed point. A 113-byte stub write produces the observation
`OK: wrote 113 bytes to src/map.js`, whose placeholder is itself exactly
113 bytes — so the next copy reproduces the same size forever. That is the
mechanism behind "1-2.6M input tokens burned and nothing shipped": the run
could not converge, because every corrective attempt was seeded with the
artifact of the previous one.

### Why it survived all three re-runs

It was introduced by step 1's own fix, and all three re-runs had that fix
active. "Before any fix" in the section above meant *before the `path`
fix* — the first re-pilot already ran the first version of the squash. That
also disposes of the "first writes of brand-new files, nothing yet
squashed" reasoning: *other* files' write calls had been squashed earlier in
the same conversation, and that history is what the model was imitating.

### The actual defect, stated generally

This is the same failure mode as step 1's dropped-`path` bug, one level
deeper: **the model treats anything sitting in a tool call's arguments as a
worked example of what a valid call looks like.** Step 1 diagnosed that
correctly for `path` and then re-committed it for `contents`. Worse, the
squashed history was actively *misleading* — an assistant call with a
113-byte `contents` paired with a tool result reading "OK: wrote 4840
bytes" teaches that a short placeholder-shaped argument is how you produce
a multi-KB file.

The general rule, now encoded in `_compact_write_calls`' docstring: **the
only safe quantity of synthesized tool-call arguments to leave in history
is zero.** Rewriting them in place cannot be made safe, because every
possible replacement string is still a string in the `contents` slot.

### The fix

`_squash_write_call_arguments` is replaced by `agent._compact_write_calls`,
which **removes** each executed `write_file` call from the conversation
outright — the tool call *and* its paired tool-result message — leaving a
short plain-text note on the assistant message carrying the observation
verbatim. This keeps step 1's entire token-cost win (the file contents
still stop being resent every turn) while putting nothing fake in an
arguments slot. An assistant message whose calls were all writes keeps the
note as its `content`, an ordinary assistant turn.

Defense in depth, since a placeholder can still be echoed from elsewhere in
the conversation: every placeholder this module emits now carries a
`_PRUNE_SENTINEL` marker, and `_write_file` **rejects** any write whose
contents contain it, with a message telling the model to `read_file` and
write the real contents. A silent corruption becomes a self-correcting
error — which by itself would have broken the loop above.

Also closed while here: the same transcript shows the model calling
`write_file('src/map.js')`, which nests to `src/src/map.js` since agent
paths are already rooted at `src/`. It self-corrects (builder.py resolves
`index.html`'s refs relative to `src/` too, so a mismatch surfaces as a
retryable build failure) but the tool schemas now say so explicitly rather
than leaving it to inference.

### Regression tests

In `tests/test_agent.py`:
- `::test_no_turn_ever_carries_a_synthetic_write_file_arguments_payload` —
  the direct guard. Any `write_file` call surviving in any turn's history
  must carry the model's own real, unmodified arguments.
- `::test_write_of_a_pruning_placeholder_is_rejected_not_written_to_disk` —
  the sentinel guard, including that the stub never reaches disk.
- `::test_compaction_leaves_every_sent_conversation_structurally_valid` —
  compaction deletes tool calls *and* their result messages; get that
  pairing wrong and the real API 400s, but every test here mocks
  `ask_with_tools`, so nothing else would catch it.
- `::test_successful_write_file_is_compacted_out_of_history` and
  `::test_rejected_write_file_is_also_compacted_out_of_history` — step 1's
  two squash tests, rewritten for removal semantics.

### What this says about the earlier hypotheses

- **Thinking-mode output budget: not implicated.** The stub arguments were
  well-formed JSON that parsed cleanly. Budget exhaustion produces
  `finish_reason == "length"` and a *truncated* fragment — the failure mode
  `run_generation_attempts` already handles. Nothing here was truncated.
- **Source size / conversation length: not causal.** Any run long enough to
  execute two `write_file` calls could trigger this, at any source size.
  Large sources correlated only because they take more writes to explode.
- **Moving the source out of `explode_game`'s system prompt: not done, and
  now looks actively wrong.** It was proposed as a cost fix, not a
  correctness one. A stable system-prompt prefix is the best case for
  DeepSeek's automatic prefix caching; moving that ~150KB behind churning
  conversation would forfeit cache hits on it every turn without removing a
  single resent byte.

### Live re-run: the stub bug is gone, and what it exposed underneath

Re-ran the exact failing explode against the fix — same downloaded 159KB
Darkhold Arena source, `deepseek-v4-flash`, thinking mode on, isolated DB
and games dir. **Zero writes in the stub band**, against 12 `write_file`
calls:

```
      607 bytes  index.html          15,158 bytes  src/map.js
      613 bytes  src/style.css       37,052 bytes  src/update.js
    4,207 bytes  src/constants.js   101,293 bytes  src/render.js
    9,085 bytes  src/input.js        22,106 bytes  src/ui.js
   11,210 bytes  src/entities.js      9,660 bytes  src/game.js
   12,962 bytes  src/combat.js
```

`src/map.js` — the file that produced 113/114-byte stubs on all three prior
runs — came out at 15,158 bytes of real content. A **101,293-byte** single
tool-call argument, emitted on turn ~30 of a conversation carrying the whole
159KB source in its system prompt, independently buries the thinking-mode
output-budget hypothesis: nothing about a long tool loop prevents the model
from emitting a 100KB argument.

**The run still failed, for an unrelated reason now measured rather than
guessed.** It used exactly 40 turns — `max_steps` — and called `finish`
**zero** times, so verification never ran (`attempts: 0`) and the fork was
rolled back. The turn budget went to re-verification: 12 `write_file`
against **17 `list_files` + 21 `read_file`**. Two causes, both introduced by
the step 2 fix itself, both fixed now:

- **The compaction note's wording.** "write_file call(s) ... were dropped
  from the conversation to save context" read to the model as *the writes
  did not take effect* — three separate "it seems like my write_file calls
  are being dropped" reasoning turns, each triggering a `list_files` sweep.
  The note now leads with the call having COMPLETED SUCCESSFULLY and the
  files being on disk, with only the bulky argument trimmed. Covered by
  `::test_compaction_note_states_the_write_succeeded`.
- **`src/`-prefixed paths nesting a second level.** Agent paths are already
  rooted at `src/`, so `write_file("src/map.js")` wrote `src/src/map.js`.
  The model wrote its shell to *both* `index.html` and `src/index.html`,
  leaving two competing shells — and `builder.build_game` only ever reads
  `src/index.html`, which here was the one whose sibling refs pointed at
  files that had landed under `src/src/`. Step 2 first tried to fix this in
  the tool-schema wording; the model ignored it. `_normalize_agent_path` now
  collapses a leading `src/` structurally, so both spellings are one file,
  and observations/pruning keys report the canonical name. The explode
  prompt also pins the shell to bare-filename refs so builder resolution
  stays consistent. Covered by
  `::test_src_prefixed_paths_collapse_instead_of_nesting`.

`max_steps` also went 40 -> 60. There is no partial credit in this loop —
hitting the cap before `finish()` throws the whole run away — and 12 module
writes plus exploration plus verification retries does not fit in 40 with
any comfort.

Cost note: 2.5M input / 75.7K output tokens over 433s. The input figure is
in the same range as the stalled runs, but it bought a complete 12-module
split rather than a stub loop.

### Second re-run, with the turn-budget fixes: explode completes

Same source, same settings. **Passed verification on the first `finish`
attempt.**

| | run 1 (stub fix only) | run 2 (all fixes) |
|---|---|---|
| success | no — hit `max_steps` | **yes** |
| turns used | 40 (the cap) | **9** |
| `finish` calls | 0 | 1, passed first try |
| input tokens | 2,503,141 | **404,535** |
| output tokens | 75,697 | 57,813 |
| `list_files` / `read_file` | 17 / 21 | 2 / 1 |
| "my writes are being dropped" turns | 3 | **0** |
| writes in the stub band | 0 | 0 |

A **6.2x drop in input tokens** and a completed run, from two wording/path
fixes. That also finally lands the Sprint 5 pilot's original goal: this run
cost *less* input than the single-file baseline it was measured against,
rather than 5-12x more.

**Behavior preservation, checked properly.** Build + smoke only prove
"assembles, no console errors" — they'd wave through a dropped stylesheet or
a quietly rewritten mechanic. Comparing the built artifact against the
original directly:

- JS **byte-identical ignoring whitespace**: 122,333 non-whitespace bytes in
  both. The -6,453 byte raw delta is entirely re-indentation (the original
  was indented inside its `<script>` tags).
- All **365** top-level declarations present; none missing, none added.
- CSS identical ignoring whitespace (538 bytes both); every element `id`
  still present; the single `<canvas>` preserved.

That is much stronger evidence than this pipeline can normally produce, but
it is still not a play-test — it proves the *code* survived, not that the
game feels the same.

(One earlier worry, resolved: `src/style.css` at 613 bytes looked like a
silently dropped stylesheet and isn't. This game's *entire* original CSS is
one 650-byte `<style>` block — Darkhold Arena is canvas-rendered, and
158,352 of its 159KB is inline JS. Worth knowing before reading module sizes
as evidence of anything.)

### Explode produced one giant module, not cohesive ones — and why

Run 2 succeeded by writing a **single 151,899-byte `core.js`** holding all
the game logic, plus a 349-byte shell and the 613-byte stylesheet. That
satisfies the format and passes every gate — comfortably under
`max_module_bytes` (450,000) — but it defeats the point of the initiative:
enhancing that fork still means reading and rewriting a 151KB module, which
is the whole-file resubmit cost the multi-file path exists to avoid.

**The cause is the same imitate-the-example mechanism as the stub bug.** The
explode prompt's worked example listed exactly four `write_file` calls, with
exactly one JS module, named `core.js`:

```
write_file("index.html", ...) for the src/ shell,
write_file("style.css", ...), write_file("core.js", ...), and
write_file("game.md", ...) for the map.
```

Run 2 produced exactly that shape, with the JS module named `core.js` — and
said so in its own summary ("src/index.html (shell), src/style.css, src/core.js
(all game logic), src/game.md"). Notably run 1, which *failed* for unrelated
reasons, split into 12 cohesive modules (`map.js`/`combat.js`/`render.js`/…),
so the model is perfectly capable of splitting; nothing was asking it to.
The prompt said "cohesive ... modules" in prose while demonstrating a
one-module split, and demonstration beats description.

Fixed on three levels:

- **The example no longer anchors on one module.** It now names several
  (`entities.js`, `render.js`, `input.js`, "and so on"), and the shell's
  ref example shows one `<script>` tag per module. `core.js` appears nowhere
  in the prompt — including in the path-guidance sentence, which had quietly
  re-anchored it.
- **An explicit, size-derived target.** The prompt states the source's byte
  count and asks for roughly `source_bytes / 25,000` modules (min 3) — 6 for
  Darkhold Arena — naming typical subsystem seams, and says plainly that one
  big module "DEFEATS THE ENTIRE PURPOSE of this conversion" and why.
- **An enforced ceiling, as backstop.** `DEFAULT_EXPLODE_MAX_MODULE_BYTES`
  (60,000, config `explode_max_module_bytes`) applies to the explode pass
  only, via a cfg override so `_run_react_loop` stays unaware of which pass
  it drives. The existing rejection message ("split this module into
  smaller, cohesive files instead of shrinking it") is already exactly the
  right nudge. The prompt states the ceiling up front so the model plans
  around it rather than discovering it by rejection — a rejected 151KB write
  wastes a whole ~40K-token generation.

Covered by `tests/test_explode.py::test_explode_enforces_its_own_tighter_module_ceiling`
and `::test_explode_prompt_demands_several_modules_not_one`.

**Not yet verified live** — no explode run has been done since these three
changes, so "the split now produces ~6 cohesive modules" remains a
prediction, not a measurement.

### Live verification of the splitting fix: it splits, and that exposed a second bug

Two live runs against the same 159,312-byte Darkhold Arena source
(`deepseek-v4-flash`, thinking mode, the repo's real `config.yaml`).

**Run A — the split works, the split game does not.** The prediction above
held: the model wrote 8 JS modules (`config.js`, `map.js`, `entities.js`,
`combat.js`, `input.js`, `update.js`, `render-core.js`, `render-draw.js`)
plus `game.js`, instead of one 151KB `core.js`. Then all three
verification attempts failed and the fork was rolled back:

| attempt | failure |
| --- | --- |
| 1 | `build failed: referenced local file not found: 'render.js'` |
| 2 | `CFG is not defined`; `Identifier 'STUCK_CHECK_INTERVAL' has already been declared`; `generateMap is not defined` |
| 3 | `Identifier 'STUCK_CHECK_INTERVAL' has already been declared`; `window.mulberry32 is not a function` |

1,859,544 input / 110,978 output tokens, 608s, ~$0.29, nothing shipped.

Attempt 1 is a shell-desync: the model wrote `index.html` early, listing the
modules it *planned*, then had `render.js` (76,060 bytes) rejected by the new
60,000-byte ceiling, split it into `render-core.js` + `render-draw.js`, and
never revised the shell. The size-rejection message now says explicitly that
splitting a module obliges you to rewrite `src/index.html`'s `<script>` tags,
not just `game.md`'s table.

Attempts 2 and 3 are one root cause, and it is the more interesting one: **the
original game is a single `(function () { ... })();` wrapping its entire
program.** Nothing in it is global. Splitting that across sibling `<script>`
blocks is the hard case, and the explode prompt said nothing at all about
scope — so the model improvised, inconsistently: it kept per-module IIFEs
(hence `CFG is not defined`, `generateMap is not defined`), declared the same
constant in two modules (`STUCK_CHECK_INTERVAL` — a fatal redeclaration, not a
per-file shadow), and invented `window.mulberry32` bridges for names declared
as top-level `const`, which never become window properties.

None of this was visible before, because the one-giant-module outcome had
exactly one scope and therefore no cross-module linkage to get wrong. Fixing
the splitting is what surfaced it. The prompt now has a `## One shared scope`
section stating the four rules directly: delete the original's single outer
IIFE rather than giving each module its own; declare every identifier exactly
once across all modules; never bridge via `window.foo`; order the `<script>`
tags so load-time reads follow their declarations, entry point last.

**Run B — passes every gate, ships a broken game.** With the scope section
added, run B split into 9 JS modules (largest 49,909 bytes) and passed on the
third verification attempt: 3,015,813 input / 135,177 output tokens, 823s,
~$0.46. The scope fix clearly worked — none of run A's simultaneous
redeclaration/`window.*`/per-module-IIFE failures recurred; the two failures
it did hit were single, sequential, and self-diagnosed using the new rules.

But the behaviour-preservation check did not come back clean:

```
original JS (no ws): 122,333   built JS (no ws): 124,396   identical: False
declarations: original 365, built 365, missing: ['screenX', 'screenY']
```

`function screenX(wx) { return wx - camera.x; }` and its `screenY` twin are
**absent from the fork entirely**, while 22 call sites survive. Confirmed in
a real browser against the built artifact:

```
load-time page errors: NONE          <- the smoke test passes
typeof screenX in page: number
value of screenX      : 0
calling it            : TypeError: screenX is not a function
```

`screenX`/`screenY` are read-only `Window` built-ins. Inside the original's
single IIFE those names were safe locals; dropping the IIFE moved them into
global scope, where they collide. The model resolved the collision by
deleting the declarations — and because the name still *resolves* (to the
built-in number), nothing throws at load. It throws at call time, in world
rendering, which a bare page-load smoke test never executes. Green build,
broken game.

This is the sharpest available demonstration that **build → safety scan →
smoke test is structurally incapable of verifying this pass**: it checks that
the page loads, not that the program is intact. Prompt text can't be trusted
to hold the line either — run B had already been told to preserve behaviour.

So explode now runs a deterministic gate of its own. `_explode_declaration_check`
compares the set of names declared by the original's inline scripts against
the built result's, and fails the finish with the exact missing list plus the
rename-don't-delete remedy. It is wired in via a new optional `extra_verify`
hook on `_run_react_loop`, so the loop stays unaware of which pass it drives.
Replayed against the real run-B artifact it returns `['screenX', 'screenY']`
— it would have caught this on the first attempt.

Two supporting changes, neither sufficient alone:

- **`explode_max_module_bytes` 60,000 -> 120,000.** A ceiling the model has
  to dodge is worse than a loose one: it shrinks to fit instead of splitting,
  and drops code doing so. Run B wrote `render.js` at 49,874 bytes — just
  under 60,000 — and that is where `renderCharacterSelect` went missing; the
  complete version came to 72,660. Re-emittability doesn't object at 120,000
  (~30-40K tokens against a 150,000-token ceiling). Accepted trade-off: the
  backstop is now inert below ~250KB of source, leaving the prompt's target
  count as the only thing preventing one-giant-module there.
- **Two prompt rules**: rename a built-in collision at declaration and every
  call site rather than deleting it (noting that a deleted one won't error on
  load); and never omit, abbreviate or stub code to fit the ceiling — split
  instead, because the split is checked.

Regression tests: `test_explode_rejects_a_split_that_drops_a_declaration`
(the run-B failure in miniature, with the smoke test mocked green so only the
parity gate stands between it and a broken game),
`::test_explode_declaration_check_ignores_pure_reorganisation`, and
`::test_explode_declaration_check_names_every_missing_symbol`.

**Still not verified live:** no explode run has been done since the parity
gate, the 120,000 ceiling, and the two prompt rules landed. Run B validated
the scope fix at 60,000, nothing more.

### Runs C and D: two ways to spend everything and ship nothing

Both ran on `deepseek-v4-flash` with the 120,000 ceiling and the parity gate
in place. Neither ever called `finish`, so neither produced anything at all —
and a run that never verifies discards every module it wrote.

**Run C** wrote the cleanest split of any run to that point: 9 JS modules,
largest 52,028 bytes, and for the first time **zero ceiling rejections** (the
60,000 -> 120,000 change removing all of run B's churn). It then spent five
consecutive turns re-reading its own modules for consistency and was killed
by the no-progress guard, which counts only a successful `write_file` as
progress and so cannot distinguish a careful final review from a stall. The
irony is direct: the "your split is checked for missing declarations" prompt
rule added after run B is what encouraged the review that killed it.
2,372,551 input / 84,018 output tokens, 527s, ~$0.36.

A detail worth recording from run C's transcript: at one turn the model
emitted, as its own assistant prose, a verbatim-looking copy of a
`_compact_write_calls` note — "`[context-pruned] The write_file call(s) below
COMPLETED SUCCESSFULLY ... Results: OK: wrote 48207`" — while actually
calling only `list_files`. No 48,207-byte write exists anywhere in that job's
`agent_events`. This is the same imitate-the-transcript mechanism as the stub
bug and the `core.js` anchoring, surfacing a third time, here in narration
rather than in a tool argument. It cost one wasted turn and self-corrected
(`list_files` showed the file missing, and it rewrote it); `_write_file`'s
`_PRUNE_SENTINEL` rejection still guards the dangerous version, where that
text reaches disk.

Fix: on hitting the no-progress threshold, a run that has written something
now spends one nudge — "reading cannot verify anything, only finish() runs
the build; call it now" — and only aborts if the stall survives it.

**Run D** then exposed that the nudge was necessary but nowhere near
sufficient. It burned all 60 steps and never called `finish`: 4,478,681 input
/ 84,018 output tokens, ~$0.66, nothing shipped. It was not stuck — it wrote
`render.js` at 79,318 bytes (accepted; the same module run A had rejected at
76,060 under the old ceiling, which is the clearest single vindication of
raising it) and then spent turn after turn auditing its own split for
functions that were called but never defined, and for constants declared in
two modules. The new "never omit code" and scope rules had made it thorough
to the point of paralysis.

The no-progress guard structurally cannot catch this: **any successful write
resets it**, and a model alternating read/write indefinitely never trips it.
So the loop now also watches the step budget directly — with a quarter of the
steps left and still no `finish` attempt, it says plainly that a run ending
without a passing finish ships nothing, and that machine verification checks
the split far more strictly than re-reading the modules can. Sent once, and
never once verification has already run, so a run working through a
legitimate rejection isn't hurried into a worse one.

Covered by `test_a_review_pass_before_finish_gets_nudged_not_killed`,
`::test_the_finish_nudge_is_spent_once_and_a_real_stall_still_aborts`,
`::test_a_run_that_never_wrote_anything_is_not_nudged`,
`::test_a_run_running_out_of_steps_without_verifying_is_told_to_finish`, and
`::test_the_budget_warning_is_not_sent_once_verification_has_run`.

### Run E: explode works end-to-end, on v4-pro

Same 159,312-byte Darkhold Arena source, 120,000 ceiling, parity gate and
both nudges in place, model switched to `deepseek-v4-pro`. **Passed on the
third verification attempt**: 2,309,556 input / 88,061 output tokens, 790s,
~$0.35 — cheaper in absolute terms than run D's failed flash run ($0.66),
because it converges instead of thrashing.

The three attempts are a clean demonstration of why each gate exists:

| attempt | rejected by | detail |
| --- | --- | --- |
| 1 | smoke test | `Identifier 'lastTime' has already been declared` (scope rule 2) |
| 2 | **parity gate** | dropped 55 declarations incl. `drawPlayer`, `drawEnemy`, `drawMinion` |
| 3 | — | passed |

**Attempt 2 is the whole argument for the parity gate, observed live.** It
passed build, safety scan *and* smoke test, and would have been recorded as a
successful explode — shipping a game with no entity rendering whatsoever.
Only the declaration check stood between that and the arcade.

Final artifact: 8 real modules (`config.js` 4,937, `input.js` 2,856,
`entities.js` 8,242, `game.js` 11,174, `map.js` 11,041, `combat.js` 18,725,
`update.js` 30,301, `render.js` 74,877), against the one 151,899-byte
`core.js` this section opened with.

Behaviour checks beyond the pipeline's own:

- `missing: []` — no declaration lost. `screenX`/`screenY` are present and,
  in the built page, `typeof screenX === "function"` where the original
  (having them inside its IIFE) reports the built-in `number`. Run B's exact
  failure, inverted.
- Play-tested in Chromium against the original side by side — click-through
  character select, WASD, held-mouse attack, ~4s of game loop: **zero page
  errors and a non-blank canvas for both.**
- NOT byte-identical: +4,242 non-whitespace characters and +14 new
  declarations (`toScreenX`, `toScreenY`, `mouseDown`, `scaleX`, …). Nothing
  was lost, but this is not the pure reorganisation run 2 of the previous
  session achieved, so subtle gameplay differences are not excluded. A human
  play-test remains the only real proof, exactly as Part A always warned.

Two cosmetic issues left alone deliberately, since every prompt addition in
this sequence produced a behavioural surprise somewhere else:

- The run left `render_ui.js` and `render_world.js` as 70-byte
  "intentionally empty" comment stubs after consolidating rendering into
  `render.js`. They are not referenced by `src/index.html`, so `build_game`
  never reads them and the built artifact is unaffected — dead files, not a
  defect.
- `render.js` at 74,877 bytes is 46% of the JS. Better than one 151KB
  module, short of the ~6 even modules the prompt asks for.

**Model choice is now part of the recipe.** Every flash failure after the
scope fix was a convergence failure, not a capability one — flash split the
game sensibly every time and then failed to stop. `config.yaml.example` now
recommends `deepseek-v4-pro` for `multifile_agent` only; `newaiwebgame` and
`enhanceaiwebgame` are untouched.

One caveat on attribution: run E changed the model *and* carried the budget
warning. The warning fires at 15 steps remaining and run E reached finish at
turn ~18, so it never triggered — reaching verification is attributable to
the model, not to that fix. The fix still matters for flash and for larger
sources; it just isn't what made this run pass.

### Where the model default lives, and why it is code

`deepseek-v4-pro` for the ReAct agent started as a `config.yaml` setting,
which was a mistake worth recording: **`config.yaml` is gitignored**, so a
config-only default reaches no deployment and no fresh clone. Pushing run E's
result would have left prod resolving `cfg.get("model", "")` -> `""` ->
`ai_client.MODEL_DEFAULT` -> `deepseek-v4-flash`, i.e. running the exact
configuration there are four failed runs against, while the verified one sat
in an untracked file on one laptop.

It is now `agent.DEFAULT_AGENT_MODEL`, read as `cfg.get("model") or
DEFAULT_AGENT_MODEL` — `or`, not a `get()` default, so an explicitly blank
model still lands on the agent's own default rather than falling through to
the app-wide one. Scoped to the multi-file agent only: `newaiwebgame` and
`enhanceaiwebgame` keep resolving through `ai_client.MODEL_DEFAULT`, having
no evidence against flash.

This is the same shape as `timeout_seconds`' 120s -> 1800s fix earlier in
Sprint 6. The rule both times: **if a wrong value breaks the feature, the
default belongs in code, not in an ignored config file.**

Covered by `test_agent_defaults_to_pro_with_no_config_block`,
`::test_agent_defaults_to_pro_when_the_configured_model_is_blank`,
`::test_an_explicit_model_in_config_still_wins`, and
`::test_the_agent_default_does_not_leak_into_the_single_file_pipelines`.

## Sprint 6 step 3: the parity gate was unsatisfiable (2026-07-26)

Two production explodes of Sorcerer With A Minigun (the 159KB Darkhold Arena
source, same game as run E) failed with the identical error — job
`1f55645d…` on `deepseek-v4-flash` (650.9s, 4.47M tokens) and job
`3439369…` on `deepseek-v4-pro (high)` (935.9s, three verification
attempts). Both models, same wall; this is not a capability failure:

```
the split dropped 2 declaration(s) the original defined: screenX, screenY.
… RENAME it consistently at its declaration and at every call site — do not
delete it.
```

Job `1f55645d…`'s transcript shows the model doing precisely that, and being
failed for it:

- seq 281 (thought): "The fix is to rename them to something like `toScreenX`
  and `toScreenY` everywhere they're used."
- seq 288: "the functions are defined in `world.js` as `toScreenX` and
  `toScreenY` (already renamed)" — it then walks `render.js` and `ui.js`
  updating call sites, and finds a genuine second bug on the way
  (`renderWaveClear`/`renderGameOver` declared in both `ui.js` and
  `render.js`).
- seq 297: the same message, attempts exhausted, fork rolled back.

### The defect

`_explode_declaration_check` was `declared(source) - declared(built)`. The
remedy it prescribes — rename the declaration and every call site — *removes
the original name from the declaration set*, so a correct rename fails the
check by construction, and the failure text asks for it again. There is no
compliant output: the only way past the gate was to keep the colliding name
verbatim, which is what run E happened to do and why the gate looked sound.
The prompt's own rule 5 and the "Never drop code to fit" section were both
telling the model to do the thing the gate rejected.

This is not "the prompt needs better wording". A deterministic gate and the
instructions pointing at it must agree on what preservation means, or the
retry loop is a fixed point that spends the whole budget and ships nothing —
the same failure shape as the `_PRUNE_SENTINEL` stub loop in step 2, arrived
at from the opposite direction.

### The fix

The distinguishing evidence is the call sites, not the declaration:

| | declaration gone | still referenced | verdict |
|---|---|---|---|
| delete (run B) | yes | 22 sites | broken — those sites bind to the built-in number |
| rename (this run) | yes | none | correct |
| drop-to-fit | yes | none, callers went too | broken — code vanished |

So the gate now sorts missing names with `_declaration_parity()` into
`broken` (undeclared but still referenced — a hard failure, and the message
now names the surviving reference count per symbol, which also catches a
*half-finished* rename precisely) and `vanished` (gone entirely, which is what
a consistent rename looks like from outside). `vanished` fails only when the
built result declares no new names to stand in for them, which separates a
rename from a subsystem deleted along with its callers.

`_reference_count()` excludes member accesses (`camera.screenX`) and
object-literal keys (`{ screenX: 1 }`): neither refers to the renamed global,
and counting either would fail a legitimate rename — the exact bug being
fixed. It does not exclude `cond ? screenX : y`, which can only cost a missed
catch, never a false accusation.

Prompt rule 5 now states that renaming is expected and that verification
allows it, and "Never drop code to fit" says every original name must survive
"under its own name, or under a rename applied consistently at the
declaration and every reference" instead of demanding the original spelling.

Covered by `tests/test_explode.py::test_explode_declaration_check_accepts_a_consistent_rename`,
`::test_explode_declaration_check_still_fails_a_half_done_rename`,
`::test_explode_declaration_check_fails_code_dropped_with_its_call_sites`,
`::test_reference_count_ignores_member_access`, and the pre-existing
`::test_explode_rejects_a_split_that_drops_a_declaration` (run B's failure,
which still fails).

### Diagnosing from a copy: `vibegames.db` is WAL, and the sidecar matters

The copy of the production DB used above showed job `3439369…` as
`status='generating'` with its agent events stopping at 09:32:48 — which
reads exactly like a worker that died mid-run and left a row that
`db.claim_next_queued_request` would then let block every other job
site-wide (`AND NOT EXISTS (… WHERE status='generating')`). It hadn't. The
run's real end was 09:33:42 (09:18:06 + 935.9s), and the copy simply stops
55 seconds short of it.

`db.py:310` sets `PRAGMA journal_mode=WAL`, so recently committed
transactions live in `vibegames.db-wal` until a checkpoint folds them into
the main file. Copying `vibegames.db` alone silently truncates history to
the last checkpoint, and a job that finished looks like a job that hung. For
a faithful copy take `vibegames.db`, `-wal` and `-shm` together, or use
`sqlite3 vibegames.db ".backup out.db"` / `VACUUM INTO`, which checkpoint
first.
