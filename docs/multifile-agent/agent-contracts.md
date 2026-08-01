# Multi-file games: agent contracts and failure history

The full design record for the multi-file game format, the ReAct editing
agent (`agent.py`), the live transcript UI, and the explode/dual-format
policy. Every rule below was paid for by a real failed run; the incident
that forced each one is stated inline, because the rule alone reads as
arbitrary without it.

`CLAUDE.md` keeps the format summary and the hard prohibitions resident.
This file is the reasoning behind them — read it before changing
`agent.py`, `builder.py`, the agent prompts, or any verification gate.
Sprint-by-sprint history lives in the numbered docs in this directory.

This is the "multi-file agent" initiative (`docs/multifile-agent/`,
6 sprints), built to get around a hard structural limit: one game
(Sorcerer With A Minigun) grew past what was believed to be DeepSeek's
~65,536-completion-token ceiling, past which the model could never
re-emit a whole `index.html` again in one response, by any trick. Sprint 6
found that ceiling was largely self-imposed (see `ai_client.py`'s
`MAX_OUTPUT_TOKENS` comment — the real figure is at least 150000, possibly
384K) and raised it, which lifts the single-file path's practical size
limit substantially without removing the motivation for this initiative:
even at a higher ceiling, a whole-file resubmit still costs a full-game
read+write on every enhancement, where a multi-file source only ever
touches the modules a change actually requires. The fix has two parts — a
format that never requires a whole-game read/write, and a live transcript
UI so an edit-by-edit agent run is still legible to the requester.

**Format.** A multi-file game's *source* is split on disk:
`games/<slug>/game.md` (a prose description plus a table of every `src/`
file and its purpose) and `games/<slug>/src/{index.html,style.css,*.js}`.
**3D (three.js) games build differently — see "Merged-module build" below.**

`builder.build_game(src_dir)` inlines every local `<link rel="stylesheet">`
and `<script src=...>` ref (in document order) into one HTML string —
external CDN refs (`safety.ALLOWED_CDN_HOSTS`) are left alone;
`builder.write_built_index()` writes that string to the game's real,
served `index.html`. `builder.is_multi_file(game_dir)` tells the two
formats apart (authoritative `meta.json["format"]`, falling back to
"does `src/index.html` exist" for fixtures/mid-run forks that have no
`meta.json` yet). `builder.build_and_verify(game_dir)` is the shared
build → `safety.scan()` → `smoke_test.run_smoke_test()` gate both formats
go through — a no-op passthrough (read the existing `index.html` straight
through) for single-file games.

**The ReAct agent** (`agent.py`) is what enhances a multi-file game.
Instead of resubmitting the complete file, the model drives a bounded
tool loop: `read_map()`, `list_files()`, `read_file(path)`, `search(pattern,
path=None)` to explore, then
`edit_file(path, old_string, new_string)` to replace one exact span or
`write_file(path, contents)` to replace one whole module (rejected over
`max_module_bytes` — split it instead of shrinking it), then
`finish(summary)` to trigger `builder.build_and_verify()`; a failure comes
back as `finish`'s tool result so the model keeps editing and calls
`finish` again, up to `max_verification_retries`. `agent.enhance_multifile_game()`
forks exactly like `game_enhancer.enhance_game()` (new `games/<slug>/`,
`parent_game_id`/`root_game_id`, source untouched, a failed run deletes
the half-written fork) — it's the multi-file-source counterpart
`job_runner.py` dispatches to instead of `game_enhancer.enhance_game()`.
Config lives under `multifile_agent:` in `config.yaml` (model/effort/
max_steps/max_verification_retries/max_module_bytes/snapshot_max_bytes/
edit_compact_bytes/context_soft_limit_tokens).

**The model starts with the whole source in hand, not a directory to
explore.** `_build_source_snapshot()` reads every file of the staged fork —
`game.md`, then `src/index.html` (whose `<script>` order *is* the dependency
order), then the rest of `src/` by posix path — into one block appended as the
last section of the system message, each file wrapped in
`===== BEGIN <path> (<n> bytes) =====` / `===== END <path> =====` lines (not a
fenced code block: `game.md` is prose that routinely contains fences). Paths
are bare — `render.js`, never `src/render.js` — reinforcing
`_normalize_agent_path`'s convention rather than fighting it. The reasoning is
the same 1/120 ratio as the append-only invariant: the block is byte-identical
on every turn, so after the first call it rides along at the cached rate, where
re-reading modules 4-6× per run (job 79a0abbb read 938KB across 33 `read_file`
calls for a six-file change) pays full price for each read *and* burns a step.
Nothing run-specific may ever be interpolated into that system message — no
timestamp, job id or fork slug — because per-source stability is what makes a
*second* enhance of the same game a cache hit on the whole block. A source over
`snapshot_max_bytes` (400,000) degrades to a manifest line plus the old
discovery wording rather than being truncated: a partial snapshot is worse than
none, since the model can't tell which half it's missing. Reading a snapshot
file the run hasn't rewritten still returns the full contents, prefixed with one
line saying the read told it nothing new — refusing information is what
historically triggered state-re-checking loops. Only a summary event reaches
`agent_events` (`{"tool": "snapshot", ...}`), never the body, since that table
is a permanent archive the chat pane replays from. Explode is untouched: it uses
`_build_explode_system_prompt` and starts from an empty directory.

Because `ai_client.ask_with_tools()` is stateless, `_run_react_loop`
resends its whole `messages` list on every turn — so anything left in
there forever gets rebilled every turn for the rest of the run. Sprint 5's
real pilot (`docs/multifile-agent/05-migration-and-pilot.md`) measured
this costing 5-12x more input tokens than the single-file baseline for
comparable changes; Sprint 6 traced it mostly to a write_file call's own
arguments (the complete new file contents, JSON-escaped) never being
pruned from the assistant message that made the call. `_compact_write_calls`
fixes that by **removing** each executed write_file call from the
conversation outright — the tool call *and* its paired tool-result message
— leaving only a short plain-text note on the assistant message carrying
the observation verbatim (success or rejection alike). The model already
generated that content and the note still reports the outcome; `read_file`
is there if it needs the current bytes again.

**The conversation is append-only: no message that has already been included
in a request to the model is ever mutated or removed.** Sprint 6 also pruned
`read_file` results — replacing one with a placeholder once its path was
rewritten or it had simply gone stale — on the premise that anything retained
is rebilled every turn. Measuring three production enhances on 2026-07-27
inverted that premise: DeepSeek's prefix cache is byte-exact and prefix-only,
and bills a resent cached token at **1/120th** of a fresh one on `v4-pro`
($0.003625 vs $0.435 per 1M; flash is 1/50). Cache-*miss* input was 68-85% of
what an enhance actually cost while cache-*hit* input was under 2%. So
retention is nearly free and **mutation is the expense**: rewriting a message
at position *k* invalidates the cache from *k* onward, on that turn and every
turn after it. Each prune was observed collapsing the cached prefix from
~44,000 tokens back to ~4,500, twice in one run, each time right after a
`write_file` — the deepest possible cut. Both prune sites are gone (Sprint 6a,
`docs/multifile-agent/06a-cache-snapshot-and-edits.md`), and
`config.yaml`'s `context_prune_after_steps` is ignored with a warning rather
than honoured, because that file is gitignored and production's copy still
sets it. `_compact_write_calls` stays, and is cache-safe by construction: it
only ever touches the assistant message and tool results created in the
*current* turn, which have not been sent yet — it shortens the suffix, never
the prefix. `tests/agent_harness.py`'s `scripted_asks` asserts the invariant on
every agent test rather than in one dedicated case, because a run that mutates
its prefix still writes the right files, passes verification and ships; only
the bill changes, so nothing else would catch a regression.

**The price of append-only is that the conversation only grows, so the context
window becomes the run's real ceiling — and the loop lands the plane itself
rather than letting the API 400 mid-run.** Pruning used to hold the message
list roughly flat; now nothing does, and hitting the window would throw away a
run that may have every module already written and correct on disk. So
`_run_react_loop` checks its own billed input size at the *top* of each turn,
before the request — using the previous call's `ask_result.input_tokens`, the
only figure that counts the system prompt, the snapshot and every tool result
the way the provider actually charges for them. Past
`context_soft_limit_tokens` (700,000) it appends **one** user message telling
the model to stop exploring and finish the edits it has left; past 95% of
`ai_client.CONTEXT_WINDOW_TOKENS` it ends the run, which falls straight through
to the forced final verification and ships whatever passes build → scan →
smoke. Checking at the top of the turn is what makes both branches safe: the
conversation there always ends in a tool result or a user message, so appending
one is legal, and a stop loses nothing, since the previous turn's tool calls
have already run and written to disk. `CONTEXT_WINDOW_TOKENS` (1,048,576) is a
**documented** figure from DeepSeek's docs, not a probed one — its comment
carries the same "re-verify before trusting it" warning `MAX_OUTPUT_TOKENS`
earned the hard way, and the 95% margin means an over-estimate only makes the
guard fire early, which is the harmless direction.

**`edit_file(path, old_string, new_string)` replaces one exact span, so a
small change stops costing a whole-module rewrite.** `write_file` costs the
entire module in *output* tokens — the expensive kind, and the one capped per
response — even for a one-line change; `edit_file` costs the two strings it
names. It is exact-match only and `old_string` must occur **exactly once**:
zero or several matches are rejected outright, never guessed, never
first-or-all, because the model cannot see the result of a mis-applied edit
and a wrong-span edit can still pass build → scan → smoke. Check order in
`_edit_file`: missing file (points at `write_file`) → empty `old_string` →
the `_PRUNE_SENTINEL`/snapshot-marker guards on **both** strings → match count
→ a no-op edit where `new_string == old_string` (rejected, or it would burn a
step *and* register as progress against the stall guard) → `max_module_bytes`
enforced on the **result**, not the payload, since a tiny edit can still push
a module over. Every rejection leaves the file byte-identical on disk. No
error message ever echoes a candidate `old_string`, offers a "did you mean",
or shows a near-miss diff — all three teach precisely the fuzzy matching the
tool refuses to do, and this model imitates its own transcript. A rejected
edit is not progress, so a run looping on an `old_string` that never matches
trips the stall guard on cheap turns rather than being kept alive by them.
`write_file` remains the tool for *creating* a file or genuinely rewriting
most of one, and its module-size lint survives edit_file for a narrower
reason than before: a module that ever needs a real whole-file rewrite still
has to fit in one response.

**Small edits are deliberately left in the conversation with their real
arguments**, which is where this diverges from `write_file`. They are small
by construction, they ride at the cached rate once sent, and keeping them is
what makes one specific sentence true — *the current contents of a file you
have edited are the snapshot's version with your edits applied, in order* —
so the model can check it against the transcript instead of taking it on
faith or re-reading. The Sprint 6 lesson is narrower than "compact
everything": never leave *synthesized* arguments the model can see. A genuine
unmodified call it emitted itself is not that. The one exception is a size
fuse: an edit whose `old_string + new_string` exceeds `edit_compact_bytes`
(8,000) is a whole-module rewrite wearing another tool's name and goes
through `_compact_write_calls`' **removal** path like a `write_file`. That
note also gains a snapshot clause naming every snapshot file the run has
modified, ending with the load-bearing *"Every other file in the snapshot is
still exactly as shown there."* — without it the model generalises one stale
file into a stale snapshot and re-reads the whole game.

**Never leave synthesized arguments in a tool call the model can see.** The
first version of the above kept each write_file call and merely replaced
its `contents` argument with a short placeholder. That placeholder was
110-118 bytes, and real pilot runs then had the model *copying it back* as
the contents of modules meant to be several KB — writing the bookkeeping
text to disk, reading it back, and re-copying it, in a fixed-point loop
that burned 1-2.6M input tokens and shipped nothing. (An identical earlier
variant that dropped the `path` key taught the model to omit `path`.) The
model reads anything in an arguments slot as a worked example of a valid
call, so rewriting arguments in place cannot be made safe — only removal
can. Every placeholder the agent does emit now carries `_PRUNE_SENTINEL`,
and `_write_file` rejects any write whose contents contain it, turning a
silent corruption into a self-correcting error. Full write-up:
`docs/multifile-agent/05-migration-and-pilot.md`, "Sprint 6 step 2".

The source snapshot above adds a second piece of copyable scaffolding, and
gets the same treatment: `_write_file` hard-rejects any contents containing a
`===== BEGIN/END ... =====` line (`_SNAPSHOT_MARKER_RE`), because a module
whose first line is a marker is a syntax error the instant the build inlines
it, and the prompt states outright that markers delimit the listing and are
never part of a file. Prompt wording alone has never been sufficient against
this failure mode; the deterministic reject is what holds the line.

Two related lessons from the same pilot, both about the agent reading its
own transcript literally. The compaction note's wording is load-bearing: an
earlier draft said the write calls "were dropped from the conversation" and
the model read that as *the writes didn't happen*, re-checking state until
it exhausted its turn budget — it now leads with the call having completed
and the file being on disk. And `_normalize_agent_path` collapses a leading
`src/` on any agent path, because paths are already rooted there and
`write_file("src/map.js")` otherwise nests to `src/src/map.js`; prompt
wording alone did not stop the model doing this, and it left two competing
`index.html` shells where `builder.build_game` reads only one.

Verified live: exploding the 159KB Darkhold Arena source now passes on the
first `finish` attempt in 9 turns / 405K input tokens, where the same run
before these fixes burned 40 turns and 2.5M input tokens without ever
reaching verification. The built artifact's JS is byte-identical to the
original ignoring whitespace (122,333 non-whitespace bytes, all 365
declarations intact).

That successful run split the game into a single 151KB `core.js` rather than
cohesive modules — passing every gate while buying nothing over staying
single-file. Cause: the same imitate-the-example mechanism as the stub bug.
The explode prompt described "cohesive modules" in prose while demonstrating
a four-file split with exactly one JS module named `core.js`, and the model
reproduced the demonstration. Explode now names several modules in its
example, states a size-derived target count (`source_bytes / 25,000`, min 3)
and why one big module defeats the purpose, and enforces its own
`explode_max_module_bytes` ceiling (120,000, vs `max_module_bytes`'s
450,000) via a cfg override, so `_run_react_loop` stays unaware of which
pass it drives. Verified live on the 159KB Darkhold Arena source: the split
went from one 151KB `core.js` to 8 modules (largest 74,877), passing build,
scan, smoke, the parity check below, and a side-by-side Chromium play-test
against the original with zero runtime errors.

That ceiling started at 60,000 and was raised, because **a ceiling the model
has to dodge is worse than a loose one**: it shrinks a module to fit rather
than splitting it, and drops code doing so. The pilot wrote `render.js` at
49,874 bytes — just under 60,000 — and that is exactly where it lost
`renderCharacterSelect`; the complete version came to 72,660. The target
count, not the ceiling, is what actually produces granularity.

**Splitting can silently delete code, and build→scan→smoke cannot catch
it** — so explode runs one extra gate of its own,
`_explode_declaration_check`, passed to `_run_react_loop` as `extra_verify`
(an optional post-verification hook; the loop stays generic). It fails a
finish whose built HTML either still *references* a name it no longer
declares, or drops a declaration without putting any new one in its place.
The reason it has to exist: a game whose whole program
sits in one IIFE has names that are safe locally but collide with read-only
`Window` built-ins once the IIFE is dropped — `screenX`, `screenY`, `name`,
`status`, `length`, `top`. The Darkhold pilot resolved that collision by
*deleting* `function screenX(wx){...}` and `screenY`, leaving 22 call
sites, and still passed every gate: `screenX` kept resolving — to the
built-in number — so the calls raise `TypeError` at call time rather than
`ReferenceError` at load, and a page-load smoke test never reaches world
rendering. Green build, game broken the instant you play it. The prompt
also now tells the model to rename such a collision at every site rather
than delete it, and never to omit code to fit the ceiling — but the
deterministic check is what actually holds the line. It earned that live:
the passing run's second attempt cleared build, scan AND smoke while having
dropped 55 declarations including `drawPlayer`/`drawEnemy`/`drawMinion`, and
only the parity check stopped a game with no entity rendering from shipping.

**A gate must accept the fix its own message demands.** That check started as
a plain set difference over declared names, which made it unsatisfiable for
the exact case it was written for: its remedy is to RENAME the colliding
declaration and every call site, and a correct rename necessarily removes the
original name from the declaration set — so the gate failed the fix and
repeated the same demand. Two live Sorcerer With A Minigun explodes
(2026-07-26) burned every verification attempt on it — one on flash, one on
pro, so not a capability failure; the flash transcript shows the model
renaming `screenX`/`screenY` to `toScreenX`/`toScreenY`
at the declaration and walking the call sites, then being told again not to
delete them. What actually separates the delete from the rename is the call
sites — deleting `screenX` strands 22 of them, renaming it strands none — so
the check now fails on names left *referenced* without a declaration, and
separately on names that vanish with no new declaration replacing them (the
drop-to-fit case). Neither prompt wording nor a stricter gate could have
fixed this; the two simply had to agree on what "preserved" means.

**A gate that can't see scope will police names that were never its business.**
The same check then failed a Tower Maze Defense explode (2026-07-26) three
attempts running on `ct`, `it` and `target` — all three *function locals* in
the original (`var chainTargets = [t], ct = t;`, `for (const it of towers)`, a
`let target` inside `applyDamage`). Its declaration scan was a flat regex over
the whole file with no notion of scope, so a split that legitimately
re-expressed a local — a `let target` becoming an `applyDamage(target, …)`
parameter, a `for (const it of …)` becoming `.forEach(it => …)` — read as "the
original declared it, the split doesn't", with the surviving locals counted as
stranded call sites. Unsatisfiable in the strongest sense: no file the model
can write makes a local look declared at file scope, and on the two bundled
games ~45% of the names that flat regex policed were locals, most of them
generic (`b`, `c`, `col`, `dt`, `target`) — a large landmine field, since any
reorganization is free to rebind those. `_scan_scopes` now masks literals and
walks braces, classifying each one as a block, an IIFE body (top level
continues inside it, because dropping that wrapper is exactly what makes those
names global) or an ordinary function body (everything inside is local), and
the gate polices only top-level names. It also tracks every binding at any
depth, parameters included, so a name that reappears as a parameter is never
called a broken reference. Two details earned their code: a function
*declaration* is never an IIFE however invoked-looking the text after its
closing brace is (`function foo(){}` followed by `(function(){…})()` reads as
`}()` to a backwards scan), and a program written entirely inside one
`window.onload = function () {…}` has no top level of its own, so that lone
wrapper is promoted rather than leaving the gate a silent no-op — worse than a
false positive, because nothing reports it.

**The same run also shipped a function twice, and every gate was happy.** It
wrote `gameOver` into both `combat.js` and `main.js` (and `checkEnemyDeaths`
into both `enemies.js` and `combat.js`). Duplicated `const`/`let`/`class` is a
load-time SyntaxError the smoke test catches; a duplicated `function` is
silent — the later copy just replaces the earlier, so if the two bodies ever
drift the built game runs code the original never had, with every name present
and every call site resolving. The parity check now also fails a top-level
function/class name declared more than once in the built result, and the
explode prompt's "declare every identifier exactly once" rule says why copying
a function is not the harmless half of that rule.

**Explode runs on `deepseek-v4-pro`, not flash** — `agent.DEFAULT_AGENT_MODEL`,
a code default rather than a config one, because `config.yaml` is gitignored
and so a config-only default never reaches a deployment or a fresh clone.
`multifile_agent.model` still overrides it; `newaiwebgame`/`enhanceaiwebgame`
keep resolving through `ai_client.MODEL_DEFAULT` (flash), having no evidence
against it. Four flash runs failed on the hard
case and every one was a *convergence* failure rather than a capability one —
flash split the game sensibly each time, then audited its own modules until
the step budget ran out without ever calling `finish`, shipping nothing.
Pro reached verification in ~18 turns and passed, and was cheaper per run
($0.35 vs $0.66) despite ~3.1x the token price. Two loop-level nudges back
this up for flash and for bigger sources: one when a run goes
`_MAX_NO_PROGRESS_STEPS` turns without progress (it's reviewing, not stalled —
don't kill it; tell it to verify if it has written files, or to start writing
if it hasn't), and one when a quarter of the step budget remains with no
`finish` attempt yet (worded for whichever of those two states the run is in).
The stall nudge is **re-armed by any successful write** — it answers one
specific pause, and a run that has since written real files has earned
another.

**The stall guard counts repetition, not turns, because exploration is not a
stall.** It originally counted consecutive turns without a successful
`write_file`, and that killed a live enhance on turn 5 (job 73df2b10,
2026-07-27) in the middle of a completely healthy run: `read_map`, nine
`read_file` calls across a 13-module game, one `search` — eleven productive
calls, no repeats, and the model's own reasoning showing it had finished
planning and was about to write. Nothing had been written yet, so the nudge
above (gated on `wrote_anything`) couldn't save it either; it aborted with no
warning at all. `search` made this far easier to hit, since its whole purpose
is answering narrow questions in extra cheap turns rather than one expensive
re-read — a guard counting turns punishes exactly the behavior the tool
exists to encourage. A turn is now progress if it wrote a file **or** made an
observation the run hasn't made before (`_progress_key`: `read_map`,
`list_files`, `read_file(path)`, `search(pattern, path)`; an `ERROR:`
observation is never progress and is never recorded, so retrying a bad path
stays a repeat). A re-read of a file the run itself wrote is deliberately
*not* counted as new — the model just emitted those bytes, so that is the
review pass the finish nudge answers. What this gives up is the guard's old
role as a cap on exploration; `max_steps` and the budget warning cover that,
and a run genuinely going in circles still trips it, since circling means
re-asking questions it has already answered.

**A stalled run is verified before it's discarded, because `finish` is not
the model's to bestow.** Every exit from `_run_react_loop` other than a
passing `finish` used to throw the whole run away — the caller deletes the
fork — even with every module the change needed already correct on disk. But
`finish` only triggers a deterministic build → scan → smoke gate the loop can
run itself, so it now does: any run that wrote at least one file and hasn't
spent `max_verification_retries` gets one forced verification (the same gate,
`extra_verify` parity check included) after the loop ends, whatever ended it
— stall, step budget, or an `ai_client` error on what would have been the
finishing turn. It ships if it passes; if it fails, the reported error is the
real defect (`smoke test failed: ...`) with the stall appended as context,
which is actionable where "agent made no progress" was not. The case that
forced this: a live enhance (job 79a0abbb, 2026-07-26) wrote all six modules
a feature needed, spent its last five turns re-reading them to settle a
`TILE` vs `TILE_SIZE` question, tripped the no-progress guard a second time,
and was killed — 1.58M tokens and ~18 minutes for nothing, one unmade call
away from either shipping or a concrete error. Verifying costs one build; not
verifying guarantees a total loss.

**`search(pattern, path=None)` exists so the agent doesn't have to re-read a
module to answer a narrow question.** It greps every `src/` file (plus
`game.md`), returning `path:line: text` for up to `_SEARCH_MAX_MATCHES`
matches. Without it the only way to check whether an identifier exists, where
it's declared, whether it's declared twice, or what its call sites are was a
whole-file `read_file` — and whole-file reads are the agent path's dominant
input-token cost. The same job 79a0abbb read 938KB of file contents across 33
`read_file` calls for a six-file change, `render.js` seven times at 73KB
each, and its fatal last five turns were a `TILE_SIZE` question one grep
answers in a line. Unlike a `read_file` result, a search result is never
pruned — an answer the model can still see is one it won't ask for twice —
which is why both the match count and the per-line length are capped.
`list_files()` also now flags each file the run has already rewritten
(`written_this_run`), bookkeeping the model otherwise cannot recover, since
`_compact_write_calls` deliberately removes its own write calls from the
conversation: that same job rewrote `config.js` five times for one feature.

**`read_file(path, start_line, end_line, char_start)` takes a range, because
the reads that actually cost money are the ones no snapshot can remove.**
Measured across 57 production agent runs (2026-07-25..29, $6.23 of spend):
`read_file` results were **38% of total spend** — the single largest line item,
ahead of the turn-1 snapshot (21%) and output (18%), with cached input at 5%
confirming Sprint 6a's finding that retention is nearly free. Of 296 reads,
only 58 were of an unmodified snapshot file (the waste `_read_file_nudge`
scolds); **238 were of a file the run had already modified**, which genuinely
need the current bytes. Those cannot be prompted away — but they were paying
whole-module price for a question about a few lines. Line numbers are 1-based
and inclusive; the cap (`_READ_MAX_CHARS`, 20,000) is on **characters
returned**, not lines, because the pathological case is a single line: this
game family's `render.js` carries 7 lines over 1,000 chars, longest 4,729, so
"read line 19" could still mean 100KB. A capped read reports the offsets it
covered and hands back a `char_start` to continue from, reusing the
`[chars X-Y of N]` vocabulary `_match_window` already taught the model. Bad
bounds are rejected, never clamped — a clamp returns a window the model cannot
tell apart from the one it asked for, the same reason `_edit_file` refuses a
near-miss `old_string`. An unranged read still returns the whole file
byte-identically, so existing transcripts and the cached prefix are untouched.
Three call sites had to agree for this to pay off: `_progress_key` keys on the
range as well as the path (keying on the path alone would score a second read
of a different part of the same module as a repeat, the miscount that killed
healthy job 73df2b10), `_read_file_nudge` stays silent on a ranged read (it
exists to discourage a redundant *whole-file* read, and nudging the cheap
alternative pushes the model back to the expensive one), and
`_locate_syntax_faults` now names the ranged read in its own message. That
last one is the case that forced this: job b7c3215e (2026-07-29) was told
`render.js '{' opened at line 19 — never closed`, had no way to look at line
19, and spent **17 of its 33 turns** on 12 blind regex searches and two
57,000-token whole-file re-reads to find one brace. Same rule as `_edit_file`'s
zero-match rejection pointing at `search`: a gate that reports a location has
to name the tool that can go there.

**A match the observation doesn't show is worse than no match at all.** That
per-line cap used to take the line's **first** 200 characters, and on a
minified-ish line that is nowhere near the match: job 837b2b8c (2026-07-27)
spent 20 of its 60 turns — a third of the run — trying to edit one line of a
`render.js` with 137 lines over 200 chars, longest 4,729. Every `search`
answered `1 match(es)` and then showed text not containing the match, so the
model could not build an `edit_file` `old_string`; it re-read the whole 96KB
module three times (~36,700 fresh tokens each, 22% of the run's cost), began
guessing the file held literal `\ud83c` escapes where it actually holds the
emoji, and ran out of steps mid-change. Its own words at step 45: *"the entire
line is a single very long line and search only reports the beginning."* This
is the same shape as the two gates documented above that rejected their own
remedies — `_edit_file`'s zero-match rejection tells the model to use `search`
to find the text "as it is actually written", and `search` structurally could
not do that. `_match_window` now centres the window on the match (cap raised
to 400, worst case still 60 × 400 ≈ 24KB) and states its position,
`…foo…  [chars 2818-3217 of 4729]`, so a model needing a different part of the
line knows one exists and can slide the window with another pattern instead of
re-reading. A line that already fits is returned byte-identical to before. The
`…` is the one piece of scaffolding here the model could copy into an
`old_string`, so the header says once that it isn't file content — in an
observation, which is the category that has never caused an imitation bug,
unlike an arguments slot (see `_compact_write_calls`).

**Running out of steps is not the same failure as stalling, and the run now
asks before it ships.** Both used to fall through to that same forced
verification, which is right for a run that finished and forgot to call
`finish`, and wrong for one still mid-change. Job 837b2b8c was executing
`edit_file` on its 60th step, adding the last of three requested features; it
shipped a half-applied change as a plain success with a 🎉, so the requester
only found out by playing the game. The loop cannot judge which case it is in
— "am I nearly done" is exactly the question this model answers badly — but a
human reading the transcript can, in seconds. So at the ceiling the run emits
an `approval_request` event and **blocks**, polling its own row the way the
cancel checkpoint does, at the top of a turn where the conversation ends in a
tool result and nothing is in flight. `POST /api/jobs/<job_id>/approve-steps`
answers it (no auth — the 32-hex job id is the capability, same as cancel);
the status page's transcript pane renders the buttons, and clamps the granted
number server-side because that button is public. Asked **once** per run
(`extra_steps_on_approval`, 40): a second prompt would mostly be asked of
someone who has stopped watching, and the context guard is the backstop past
that. A grant appends one user message — appended, never inserted, so the
cached prefix survives — and re-arms `finish_nudge_at` and the budget nudges
against the new ceiling, the same "a fresh budget earns a fresh warning"
principle as the stall nudge being re-armed by a successful write.

The wait is bounded (`step_approval_timeout_seconds`, 1800) for a reason worth
knowing before changing it: `db.claim_next_queued_request` allows exactly one
`generating` job site-wide, so **a paused run blocks the whole queue**. On
timeout it does exactly what it did before this existed. It stays `generating`
throughout rather than taking a sixth `status` value, which would ripple into
`_already_cancelled`, `sweep_orphaned_requests`, the claim guard and the
`TERMINAL_STATUSES`/`LABELS` tables in three JS files for no gain; the state
lives in two nullable columns instead, written by direct `UPDATE`s that skip
`update_generation_request` because it bumps `updated_at`, which
`/api/status` serves as `generating_started_at` — routing an approval through
it would restart the elapsed timer on the user's screen. `extra_steps_granted`
is tri-state: NULL unanswered, 0 declined ("ship what you have"), >0 granted.
A `job_id` with no row, or `extra_steps_on_approval: 0`, skips the prompt
entirely rather than waiting out a timeout for an answer that cannot come —
and "no row" has to come from `db.request_step_approval`'s own UPDATE
rowcount, never from re-reading `awaiting_approval_at`. Answering *clears*
that column, so a click landing between the write and the re-read reads back
NULL, which is exactly what an unknown job looks like: the grant was dropped
and the run shipped early. That was a ~50% flake in the one-shot test before
it was ever seen in production.

**A forced-verification ship now says so.** `_run_react_loop` returns
`complete`, false whenever the last-ditch gate is what shipped the run, and
the summary leads with *"⚠️ The agent ran out of turns before confirming it
was done… the requested change may be only partly applied."* The `final`
event carries `incomplete: true` and both chat panes drop the 🎉. Passing
build → scan → smoke means the code *builds*, which was never the same claim
as the request having been carried out.

**A parse error names no file, so the loop names it.** The smoke test loads the
*built* `index.html` and reports whatever Chromium says, which for a syntax
error is `pageerror: Unexpected end of input` — no module, no line, across
every inlined script at once. That string is unactionable on a 238KB
thirteen-module source, and job 0cf766d0 (2026-07-28) proved it: it made 30
good edits across 7 files, hit a parse error, and spent **all three**
verification attempts and ~4.5M tokens guessing at it, re-reading a 118KB
`render.js` three times and reasoning its way through candidates that were all
fine, before the retry ceiling discarded the entire fork. It also never noticed
the clue it was given — the message appeared *twice*, and since
`builder.build_game` inlines each module as its own `<script>`, scripts parse
independently, so two errors meant two separate broken modules. So
`run_verification` now appends `_locate_syntax_faults(game_dir)` to every
failure detail: `_delimiter_fault` walks `_mask_js_literals`' output (offsets
and newlines preserved, so a `}` in a string or a regex can't shift the walk or
the line number) per `src/*.js` file and per inline `<script>` of
`src/index.html`, and reports `render.js '{' opened at line 386 — never
closed`, one fault per file, all files. It runs on every failure rather than on
a message match, because it is silent — `""` — whenever everything balances, so
a ReferenceError or a blocked-host failure is unaffected; verified against all
20 real multi-file games with zero false positives, at 0.06s for a whole game.
Classification stays on the browser's own words: `_classify_failure` runs
*before* the note is appended, or the file and line text would skew its keyword
match.

**A re-verification that changed nothing must not cost an attempt.** There are
only three, and the same job spent its second on a finish it called after pure
reads — its own reasoning was *"maybe the error was transient"* — and its third
on an edit it recanted in the next breath (*"wait, actually that was already
valid code"*). A finish with nothing written since the last failed one runs a
byte-identical build and can only fail identically. `edited_since_finish` was
already tracked for the re-finish nudge, so the loop now bounces that call with
a tool result instead of building — same reasoning as `_edit_file` rejecting a
no-op edit: a turn that changes nothing must not register as progress against a
budget. It is **one-shot per failure window** (re-armed by the next real
failure), so a run that genuinely believes the build is wrong is never locked
out — it costs a turn to insist, not one of three attempts.

**The agent event stream + live chat UI.** Every think/act/observe/verify
step is emitted through an `emit(role, content, data)` callback
(`agent.py`'s default writes a `db.add_agent_event` row keyed by
`job_id`). `GET /api/jobs/<job_id>/events?since=<seq>` returns everything
newer than `seq` — the incremental slice `static/agent_chat.js` polls
every second on the job status page. `templates/status.html` is a
two-pane `.job-shell`: the left pane is the original `status.js`-driven
queue/ETA/timer panel (unchanged), the right pane (`#chat-pane`) is
`agent_chat.js`'s independent, Claude-chat-style transcript — thoughts
collapse past 240 chars, tool calls/results get an icon and a short
summary (never the full file contents), a `build` event renders ✅/❌ with
the attempt number, and a terminal `final` event renders the notes plus a
Play link. Legacy single-file jobs have no agent events at all — the pane
just shows "No live transcript for this job" and the left pane alone
carries the whole experience, exactly as before this feature existed.

A `usage` event is also emitted once per LLM call, carrying that call's own
token counts plus the run's running totals. It's the only per-call
accounting there is: the `generation_requests` row only gets a total when
the whole job ends, and `generation_attempts` only gets a row per `finish()`
verification, so without it a 60-turn agent run's spend is invisible until
it's over. `agent_chat.js` renders it two ways: an always-on running-total
bar (`#chat-usage-bar` — step, in/out/cached/total tokens) that updates on
every `usage` event, and a terminal summary line appended after the final
`final`/`error` card. Tokens only, no USD — the dollar readout lives in the
admin explode dialog (`admin_explode.js`), which is the page that has the
per-million rates in hand (they're admin-page data attributes, behind
`ADMIN_TOKEN`); putting spend in front of a public requester is a separate
decision, not an oversight.

**The transcript is the archive.** `agent_events` rows are never pruned or
deleted, and the chat pane renders from nothing else, so `/status/<job_id>`
replays a months-old enhance exactly as its requester watched it — the admin
History tab links to it per row ("Status ↗"). The fidelity comes from
`emit()` truncating *before* it stores: what was displayed and what was kept
are the same bytes by construction. The flip side is that anything the caps
trim is gone from both, which is why `_THOUGHT_MAX_CHARS` is 24000 and not
its original 4000 — measured against a real explode run, 9 of 10 reasoning
blocks were under 900 chars while the one that hit the ceiling was the
opening "here's how I'll split this game" plan, i.e. exactly the block worth
reviewing. Raw per-turn API payloads are still not stored for agent runs
(unlike single-file jobs, where `generation_attempts.raw_response` holds
them); that costs byte-exact model output and `finish_reason` for forensics,
but nothing that any user ever saw on screen.

**Exploding a game from the admin page.** `/admin/stats`' Games tab shows a
per-game **Fmt** badge (`S` single-file / `E` exploded) resolved through
`builder.is_multi_file()`, and an Explode button on every single-file game
with a `game_id`. It posts to `POST /admin/games/<game_id>/explode` (behind
`require_admin_token`), which queues a `kind='explode'` `generation_requests`
row and returns the `job_id` as JSON instead of redirecting; `job_runner.py`
dispatches that kind to `agent.explode_game()`. Going through the normal job
queue is the point — the AI kill switch, the worker's crash sweep, and the
History tab's model/effort/token/cost accounting all apply to it with no
special-casing. The button's dialog (`static/admin_explode.js`) then follows
the run on the same `/api/jobs/<job_id>/events` feed, rendering the
transcript plus a live token/cost readout off the `usage` events; the
endpoint also returns the job-level totals for the terminal summary. A 409
from an already-in-flight job comes back with that job's `job_id`, so the
dialog attaches to the running job rather than erroring. Unlike
`enhance_game_auto_format`'s internal explode, this fork is **not** hidden —
the admin asked for the format change itself, so the multi-file version is a
visible arcade entry (the same row's Hide toggle is right there). The
single-file original is untouched either way.

History rows for a job that has agent events get a **Transcript** button
that replays it into the same dialog — otherwise a finished agent run's
transcript is only reachable from `/status/<job_id>` while it's still live.
`db.get_generation_history()` returns a `has_agent_events` flag so the
button isn't offered on single-file jobs, which would open an empty dialog.

**Explode + the dual-format enhance policy** (`agent.explode_game()`,
`agent.enhance_game_auto_format()`) convert an *existing single-file* game
into this format. `explode_game()` hands the model the whole original
`index.html` as input context (input tokens aren't subject to the output
ceiling) and has it re-emit the same game split across `write_file` calls
— behavior-preserving by contract and gated by the same
`build_and_verify()`, but only a manual play-test actually confirms the
game still *plays* identically, not just "console-error-free." It forks
like any other enhance (`parent_game_id`/`root_game_id` back to the
single-file source) except the title defaults to the source's own title
verbatim, since this is a format change, not a content change.
`enhance_game_auto_format()` is `job_runner.py`'s single dispatch point
for every `kind='enhance'` job: a multi-file source goes straight to
`enhance_multifile_game()`; a single-file source at or over
`game_enhancer.LARGE_SOURCE_BYTES` gets auto-exploded first (the resulting
intermediate fork is hidden via `db.set_game_hidden` — it's an
implementation detail, not something the requester asked to see as its
own arcade entry, though its lineage still chains through it to the
original single-file source) and then enhanced on that fork for the
actual requested change; everything else keeps using the legacy
`game_enhancer.enhance_game()` path untouched. If the explode step itself
fails, the whole request falls back to the legacy single-file path rather
than failing over an internal step the user never directly asked for.
`builder.build_and_verify()` treats a not-yet-written `index.html` (either
format, mid-explode) as a normal failed-verification result, not a crash.

---

## Merged-module build (3D / three.js games)

Added when the three.js engine option landed. A 3D game's `meta.json` carries
`"engine": "three"`; `engines.py` owns everything about what such a game looks
like, and `builder.read_engine(game_dir)` is how the rest of the code asks.

**The rule.** For a 3D game, `builder.build_game(src_dir, engine)` does not emit
one `<script>` block per module. It merges every local script ref into a single
`<script type="module">`, placed where the first ref was, under a fixed header:

```js
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { PointerLockControls } from 'three/addons/controls/PointerLockControls.js';
```

**Why it has to exist.** three.js has been ESM-only since r161 — the UMD builds
were removed — so a 3D game must `import`. `import` is a syntax error in a
classic script, so the ordinary sibling-`<script>` build cannot produce a
working 3D game at all. This is not optional or deferrable:
`enhance_game_auto_format` already explodes *any* single-file game that crosses
`ge.LARGE_SOURCE_BYTES` on its first enhance, so a 3D game reaches the
multi-file path on its own whether or not anyone asks it to. Without the merged
module, that produces a silently broken split.

**What it preserves.** The merged module is one shared scope, so the four
load-bearing explode rules hold verbatim — no per-module IIFE, declare every
identifier exactly once, no `window.foo` bridges, dependency order. One module
scope is shared exactly the way the global scope was.

**What changes, and only this.**

- **`THREE`, `OrbitControls` and `PointerLockControls` are ambient.** The build
  header supplies them.
  A src module carrying its own `import`/`export` is rejected by
  `_reject_esm_statements` with the file and line, because after concatenation
  it would land mid-module where imports are illegal. `import(` (dynamic) and
  the word "import" in prose or a string deliberately do not trip it.
- **The Window-collision rename rule (explode rule 5) is inapplicable.** Module
  scope never touches `window`, so a top-level `const name` cannot shadow
  `window.name`. Asking for a rename there would be busywork, so the 3D variant
  of the prompt says to keep the original spelling.
- **The declaration parity gate needs no change.** `_declared_names` scans
  `const`/`let`/`var`/`function`/`class`, so an `import * as THREE` binding
  never enters the declared set on either side and cannot false-positive. This
  was checked rather than assumed.

**The engine must be passed to `build_and_verify` explicitly.** A fork in
progress has no `meta.json`: `_stage_fork` deliberately doesn't copy the
source's, and `explode_game` writes one only once the run succeeds. Reading the
engine from disk would therefore see nothing and verify a 3D game as if it were
2D — building classic scripts and failing every attempt on syntax errors that
point nowhere near the real cause. Both orchestrators read the engine from the
*source* game and pass it down through `_run_react_loop`.
