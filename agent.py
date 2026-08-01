"""
agent — ReAct (reason -> act -> observe) editing agent for multi-file games
(Sprint 2 of docs/multifile-agent/). Ships headless: verified via DB/logs,
no chat UI yet (Sprints 3-4 add the event stream and the live transcript).

Where game_generator/game_enhancer make the model resend a COMPLETE
index.html on every attempt, this agent lets the model explore a
multi-file game's `src/` tree with tools and rewrite only the modules a
change touches — read_map()/list_files()/read_file(path)/search(pattern) to
look around, write_file(path, contents) to replace one whole module,
finish(summary) to trigger verification. No single model turn ever contains (or requires
reading) the whole game, so the output-token ceiling that motivated
Sprint 1 (see docs/multifile-agent/00-overview.md) stops being a structural
problem: a per-module size ceiling (`max_module_bytes`) is enforced on every
write_file call instead.

Enhancing a multi-file game still forks exactly like game_enhancer.enhance_game
today: a brand-new games/<slug>/ is written (new game_id/slug,
parent_game_id/root_game_id linking back to the source), the source
directory is never touched, and a failed run deletes the half-written fork.
The agent's edits apply to a COPY of the source's src/ + game.md staged in
the new directory — its own meta.json and built index.html are written only
once the run passes verification.

# Exports:
#   class AgentError(Exception)
#   enhance_multifile_game(source_game_id, description, requested_by, config,
#                          db_conn=None, games_dir=None, job_id=None,
#                          new_title=None, creator_uid=None, emit=None) -> dict
#   explode_game(source_game_id, requested_by, config, db_conn=None,
#                games_dir=None, job_id=None, new_title=None, creator_uid=None,
#                emit=None, announce_completion=True) -> dict
#     (Sprint 5: AI-assisted single-file -> multi-file split, behavior-
#     preserving. Forks like enhance_multifile_game; see docstring.)
#   enhance_game_auto_format(source_game_id, description, requested_by, config,
#                            db_conn=None, games_dir=None, job_id=None,
#                            new_title=None, creator_uid=None, emit=None) -> dict
#     (Sprint 5's dual-format policy: job_runner's single dispatch point for
#     kind='enhance' — routes to enhance_multifile_game, explode_game +
#     enhance_multifile_game, or game_enhancer.enhance_game based on the
#     source's on-disk format/size.)
#   is_multi_file_source(source_game_id, games_dir, conn=None) -> bool
#   AGENT_TOOLS, DEFAULT_MAX_MODULE_BYTES
#
# Sprint 3 (docs/multifile-agent/03-agent-event-stream.md) makes the loop
# observable: every think/act/observe/verify/finish step is emitted through
# an `emit(role, content=None, data=None)` callback. The default callback
# (built here when the caller doesn't pass one) writes a durable
# db.add_agent_event row keyed by job_id, which GET /api/jobs/<job_id>/events
# polls incrementally. Emitting is best-effort everywhere it's called — a
# raising emitter is caught and logged, never allowed to fail the job.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import time
from collections import Counter
from pathlib import Path
from typing import Callable, NamedTuple

import ai_client as ai
import builder
import db
import engines
import game_enhancer as ge
import game_generator as gg
import safety

_logger = logging.getLogger(__name__)

# ai_client.MAX_OUTPUT_TOKENS (150000 as of Sprint 6 — see ai_client.py's
# comment on that constant for how the original 65536 figure turned out to
# be self-imposed, not DeepSeek's real ceiling) is the per-response
# completion-token budget — at ~4 chars/token that's ~590KB of raw HTML. A
# write_file call has to emit its contents as a JSON-escaped tool-call
# argument (quotes/backslashes/newlines all cost extra bytes over the raw
# source), and in thinking mode the same budget is shared with
# reasoning_content, so this default sits at 3x MAX_OUTPUT_TOKENS bytes
# rather than the full raw-HTML figure — comfortable headroom, not a hard
# physical limit. Configurable per-call via cfg["max_module_bytes"].
DEFAULT_MAX_MODULE_BYTES = ai.MAX_OUTPUT_TOKENS * 3

# Sprint 6 item D: a soft lint, not a gate. DEFAULT_MAX_MODULE_BYTES stops a
# write that can't be re-emitted at all; by the time a module is anywhere
# near it, the game has already grown past the point a whole-module rewrite
# stays cheap. Warning at half the applicable ceiling gives the model a
# nudge to split proactively, on the next unrelated edit to that module,
# well before it's forced to by a rejection. Never blocks the write.
# Configurable per-call via cfg["module_warn_bytes"].
#
# Sprint 6a's edit_file weakens this lint's rationale without removing it: a
# targeted edit to a 100KB module no longer costs 100KB of output, so module
# size stops being the dominant per-edit cost. What survives is the ceiling
# itself — a module that ever needs a genuine whole-file rewrite (a
# restructure, a split, a fix edit_file can't express as one exact match)
# still has to fit in one response, and a module close to the ceiling has no
# room for that fallback.
DEFAULT_MODULE_WARN_RATIO = 0.5

# The ReAct agent defaults to v4-pro where the rest of the app defaults to
# ai.MODEL_DEFAULT (v4-flash) — this is the one pipeline with direct evidence
# against flash. Exploding a 159KB single-IIFE game failed on flash four
# times running, every time by failing to CONVERGE rather than by lacking
# capability: it split the game sensibly and then audited its own modules
# until the step budget ran out, never calling finish, so the run shipped
# nothing. v4-pro reached verification in ~18 turns and passed, and cost less
# per run ($0.35 vs $0.66) despite ~3.1x the token price.
#
# This is a CODE default rather than a config-only one on purpose: config.yaml
# is gitignored, so anything set only there is invisible to every deployment
# and every fresh clone — prod would silently run the configuration we have
# four failed runs against. Same lesson as timeout_seconds' 120s -> 1800s.
# cfg["model"] still wins, so flash remains one line away.
DEFAULT_AGENT_MODEL = "deepseek-v4-pro"

# The explode pass needs a much tighter ceiling than an ordinary edit.
# DEFAULT_MAX_MODULE_BYTES exists to stop a write that physically cannot be
# re-emitted; it is not a target, and a 159KB game splits "successfully"
# into one 151KB module well under it. That passes every gate and buys
# nothing — enhancing that fork still means rewriting 151KB, which is the
# whole-file cost this initiative exists to remove (Sprint 6 step 2 pilot,
# docs/multifile-agent/05-migration-and-pilot.md).
#
# So explode enforces its own ceiling, and the prompt states both the
# ceiling and a target module count derived from the source size — the
# ceiling is the backstop, the target is what actually shapes the split.
# Overridable via cfg["explode_max_module_bytes"].
#
# 120_000, not the 60_000 this started at: a ceiling the model has to dodge
# is actively dangerous, because it shrinks to fit rather than splitting.
# The Darkhold pilot wrote render.js at 49,874 bytes — suspiciously just
# under 60,000 — and that is exactly where it dropped renderCharacterSelect;
# the complete version came to 72,660. Re-emittability doesn't object at
# 120_000 either (~30-40K tokens, against a 150_000-token output ceiling),
# and the target count below is what actually produces granularity. The
# trade-off accepted here: at 120_000 the backstop is inert for sources
# under ~250KB, so for those the prompt's target count is the only thing
# holding the line against one-giant-module.
DEFAULT_EXPLODE_MAX_MODULE_BYTES = 120_000
EXPLODE_TARGET_MODULE_BYTES = 25_000

# How many consecutive turns may go by learning nothing before the run is
# considered stuck. "Learning nothing" is the operative phrase — see
# _progress_key: a turn counts as progress if it wrote a file OR made an
# observation this run hasn't made before. It used to count only a successful
# write_file, and that killed a live enhance (job 73df2b10, 2026-07-27) on
# turn 5 of a completely healthy run: read_map, nine read_file calls across
# a 13-module game, one search — eleven productive calls, no repeats, the
# model's own reasoning showing it had finished planning and was about to
# write. Exploration is not a stall, and the finish nudge below couldn't
# save it because that nudge is gated on having already written something.
#
# The `search` tool (2026-07-27) made this much easier to hit: its whole
# point is answering narrow questions in cheap turns instead of expensive
# re-reads, so it adds turns to the pre-write phase by design. A guard that
# counts turns rather than repetition punishes exactly the behaviour the
# tool exists to encourage.
_MAX_NO_PROGRESS_STEPS = 5

# A run that never calls finish() ships NOTHING — every module written, every
# token spent, discarded. Two consecutive real explode pilots died exactly
# that way (docs/multifile-agent/05-migration-and-pilot.md): one was killed
# mid-review by the no-progress guard, the next burned all 60 steps
# self-auditing its split and never verified, at 4.5M tokens. The
# no-progress guard cannot catch the second case — any successful write
# resets it, and a model alternating read/write forever never trips it.
#
# So the loop also watches the step budget itself: with this many steps left
# and still no finish attempt, tell the model plainly to stop auditing and
# verify. Machine verification is both cheaper and stricter than the model
# re-reading its own modules, and a rejection comes back with the exact
# failure to fix.
def _finish_nudge_threshold(max_steps: int) -> int:
    return max(5, max_steps // 4)

# Running out of steps is not the same failure as stalling, and until now the
# loop could not tell the difference — both fell through to the forced final
# verification, which ships whatever passes build → scan → smoke. That is
# right for a run that finished the work and merely forgot to call finish. It
# is wrong for a run that was still mid-change: job 837b2b8c (2026-07-27) was
# executing edit_file on its 60th step, adding the last of three requested
# features, and shipped a half-applied change as a plain success. Nothing in
# the transcript said so, so the requester had to notice the game was wrong
# and enhance again.
#
# The loop cannot judge which case it is in — "am I nearly done" is exactly
# the question this model answers badly — but a human reading the transcript
# can, in seconds. So the run pauses once at the ceiling and asks.
#
# One shot, deliberately: a second prompt would mostly be asked of someone who
# has already stopped watching, and the context guard is the backstop for a
# run that genuinely needs more room than 100 turns. Both are CODE defaults
# rather than config-only ones, for the DEFAULT_AGENT_MODEL reason —
# config.yaml is gitignored, so a config-only default never reaches a fresh
# clone or a deployment. Set extra_steps_on_approval to 0 to disable the
# prompt entirely, which is what a headless deployment wants.
DEFAULT_EXTRA_STEPS_ON_APPROVAL = 40
# A paused run holds the single 'generating' slot that
# db.claim_next_queued_request allows site-wide, so it blocks every other
# queued job for as long as it waits. That is the whole reason this is bounded
# rather than indefinite. On timeout the run behaves exactly as it did before
# this feature existed.
DEFAULT_STEP_APPROVAL_TIMEOUT_SECONDS = 1800
_APPROVAL_POLL_SECONDS = 2

# Marks every placeholder this module leaves in the conversation where real
# material was pruned away (compacted write calls, dropped read results).
# Two jobs: it makes those placeholders unmistakably bookkeeping rather than
# content, and _write_file rejects any write whose contents contain it — the
# guard against the model copying one back into a real file (Sprint 6 step 2;
# see _compact_write_calls for the run that made this necessary).
_PRUNE_SENTINEL = "[context-pruned]"

# Sprint 6a: the whole game source is handed to the model up front, in the
# system message, rather than discovered a read_file at a time. Input tokens
# are cheap where output tokens and turns are not, and the block is
# byte-identical every turn, so after the first call it rides along at the
# cached rate (1/120th on v4-pro) — a run that instead re-reads its modules
# 4-6x pays full price for each read AND burns a step on it.
#
# 400_000 bytes is roughly 100K tokens, well inside the context window (see
# ai.CONTEXT_WINDOW_TOKENS) with room for the run's own turns. A source over
# it falls back to the manifest-only prompt rather than being truncated:
# a partial snapshot is worse than none, because the model cannot tell which
# half it is missing. Overridable via cfg["snapshot_max_bytes"]. A CODE
# default, like DEFAULT_AGENT_MODEL, because config.yaml is gitignored.
DEFAULT_SNAPSHOT_MAX_BYTES = 400_000

# The snapshot's file delimiters. Line-anchored and unmistakable, and
# deliberately NOT a fenced code block: game.md is prose that routinely
# contains fences, and nesting them breaks the first time it does.
#
# _SNAPSHOT_MARKER_RE is the guard, not the formatter. Every prior lesson in
# this module says the model treats anything in its transcript as a worked
# example (see _compact_write_calls), and a snapshot puts a new piece of
# scaffolding in front of it that it can copy into a file. Same defense as
# _PRUNE_SENTINEL: reject the write loudly, turning a silent corruption into
# a self-correcting error.
_SNAPSHOT_MARKER_RE = re.compile(r"^=====\s+(?:BEGIN|END)\b.*=====\s*$", re.M)

# Sprint 6a: an edit_file call is normally left in the conversation with its
# real arguments, unlike write_file (see _compact_write_calls). They are small
# by construction, and leaving them is what makes one specific sentence TRUE:
# "the current contents of a file you have edited are the snapshot's version
# with your edits applied, in order". Take the edits out of history and that
# statement becomes unsupported, which is exactly the kind of gap this agent
# answers by re-reading everything.
#
# The exception is a genuinely large edit — a near-whole-module replacement
# expressed as one exact match. Those are write_file in all but name, and get
# write_file's treatment: removed outright (never a rewritten arguments slot,
# see _compact_write_calls). Overridable via cfg["edit_compact_bytes"]; a CODE
# default, like DEFAULT_AGENT_MODEL, because config.yaml is gitignored.
DEFAULT_EDIT_COMPACT_BYTES = 8_000

# Sprint 6a made the conversation append-only, which means it only ever grows.
# That is the right trade — a resent cached token costs 1/120th of a fresh one,
# so retention is nearly free while mutation collapses the cache — but it moves
# where a long run dies. Pruning used to hold the message list roughly flat; now
# the context window itself is the ceiling, and hitting it is an API 400 in the
# middle of a run that may have every module already written on disk.
#
# So the loop watches its own input size and lands the plane. Past this figure
# it tells the model once to stop exploring and finish; past 95% of
# ai.CONTEXT_WINDOW_TOKENS it ends the run itself, which hands control to the
# forced final verification below — a run that stops at the guard still ships
# whatever passed build/scan/smoke, where a 400 ships nothing.
#
# 700_000 of a documented 1M window leaves ~350K of headroom: enough for a
# 400_000-byte snapshot (~100K tokens) plus several more turns of large tool
# results, and the nudge needs room to be acted on, not just heard.
# Overridable via cfg["context_soft_limit_tokens"]; a CODE default, like
# DEFAULT_AGENT_MODEL, because config.yaml is gitignored.
DEFAULT_CONTEXT_SOFT_LIMIT_TOKENS = 700_000

# Fraction of ai.CONTEXT_WINDOW_TOKENS at which the run stops on its own. Not
# 100%: the check reads the PREVIOUS call's billed input, and the next request
# is always bigger (this turn's assistant message and tool results are already
# appended), so the margin absorbs one turn's growth.
_CONTEXT_HARD_LIMIT_RATIO = 0.95


class SourceSnapshot(NamedTuple):
    """The full source of a multi-file game as handed to the model.

    `text` is None when the source is over the ceiling — `manifest` is still
    populated, and the prompt falls back to discovery wording. `paths` holds
    only the files whose bodies are actually in `text`, so it is the right set
    to test a read_file against (a binary file is in the manifest but not in
    `paths`)."""
    text: str | None
    manifest: str
    paths: frozenset
    total_bytes: int
    file_count: int


def _snapshot_files(game_dir: Path) -> list[tuple[str, Path]]:
    """Every source file as (bare tool-call path, real path), in a fixed
    order: game.md (the map), then src/index.html (whose <script> order IS
    the dependency order), then the rest of src/ by posix path.

    The order is fixed because the snapshot has to be byte-identical across
    runs to hit DeepSeek's prefix cache on a second enhance of the same
    source. Paths are bare — `render.js`, never `src/render.js` — matching
    what the tools accept, so the snapshot reinforces _normalize_agent_path's
    convention rather than fighting it."""
    files: list[tuple[str, Path]] = []
    game_md = game_dir / "game.md"
    if game_md.is_file():
        files.append(("game.md", game_md))
    src_dir = game_dir / "src"
    if not src_dir.is_dir():
        return files
    index = src_dir / "index.html"
    if index.is_file():
        files.append(("index.html", index))
    for p in sorted(src_dir.rglob("*"), key=lambda p: p.as_posix()):
        if p.is_file() and p != index:
            files.append((p.relative_to(src_dir).as_posix(), p))
    return files


def _build_source_snapshot(game_dir: Path, max_bytes: int) -> SourceSnapshot:
    """Read every source file into one immutable block for the system prompt.

    Never interpolate anything run-specific here — no timestamp, job id or
    fork slug. The system message is what makes a second enhance of the same
    source a cache hit, and one varying byte anywhere in it costs the whole
    block."""
    files = _snapshot_files(game_dir)
    sizes: list[tuple[str, int, bool]] = []   # (path, bytes, is_text)
    bodies: list[str] = []
    total = 0
    text_paths: list[str] = []

    for rel, path in files:
        try:
            contents = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            sizes.append((rel, path.stat().st_size if path.is_file() else 0, False))
            total += sizes[-1][1]
            continue
        size = len(contents.encode("utf-8"))
        total += size
        sizes.append((rel, size, True))
        text_paths.append(rel)
        # A file with no trailing newline would otherwise run its last line
        # into the END marker. Say that the newline was added, so the model
        # doesn't take it for part of the file and reproduce it.
        note = ""
        if contents and not contents.endswith("\n"):
            contents += "\n"
            note = ", no trailing newline"
        elif not contents:
            note = ", empty"
        bodies.append(
            f"===== BEGIN {rel} ({size} bytes{note}) =====\n"
            f"{contents}"
            f"===== END {rel} ====="
        )

    manifest = "Files ({}, {:,} bytes total): {}".format(
        len(sizes), total,
        " · ".join(
            f"{rel} {size:,}" + ("" if is_text else " (not text — use read_file)")
            for rel, size, is_text in sizes
        ) or "none",
    )
    text = "\n\n".join(bodies) if bodies and total <= max_bytes else None
    return SourceSnapshot(text, manifest, frozenset(text_paths), total, len(sizes))


class AgentError(Exception):
    """Recoverable failure inside one tool call. Never escapes the loop —
    always turned into an "ERROR: ..." observation fed back to the model."""


# ---------------------------------------------------------------------------
# Tools exposed to the model
# ---------------------------------------------------------------------------

# Paths are already rooted at src/, so a "src/" prefix nests a second level
# ("src/map.js" -> src/src/map.js). A real pilot run did exactly that. It's
# caught downstream — builder.py resolves index.html's refs relative to src/
# too, so a mismatch surfaces as a build failure the loop can retry — but
# saying so up front is free.
_PATH_ARG_DESCRIPTION = (
    "Relative to src/, e.g. 'core.js' — do NOT prefix it with 'src/'. "
    "The one exception is 'game.md', which sits alongside src/."
)

READ_MAP_TOOL = {
    "type": "function",
    "function": {
        "name": "read_map",
        "description": (
            "Read game.md: this game's prose description plus a table of "
            "every src/ file and its purpose. Call this first, before "
            "reading any individual module."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

LIST_FILES_TOOL = {
    "type": "function",
    "function": {
        "name": "list_files",
        "description": (
            "List every file under src/ with its byte size, so you can "
            "budget which modules to read."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

# A whole-file read is the agent path's single largest cost. Measured over 57
# production agent runs (2026-07-25..29): read_file results were 38% of total
# spend, and 238 of the 296 reads were of a file the run had already modified —
# i.e. reads that genuinely needed the current bytes, so no prompt wording or
# snapshot can remove them. What makes them expensive is that the tool had only
# one setting: all of it. Job b7c3215e (2026-07-29) was told by
# _locate_syntax_faults that render.js's '{' on line 19 was never closed, had no
# way to look at line 19, and re-read the whole 151KB module twice at ~57,000
# fresh tokens a call to find one brace.
#
# A range answers that question for ~5,000. The cap is on *characters returned*
# rather than lines, because the pathological case is a single line: this game
# family's render.js carries 7 lines over 1,000 chars, longest 4,729, and a
# "read line 19" that returns 100KB would have solved nothing.
_READ_MAX_CHARS = 20_000

READ_FILE_TOOL = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": (
            "Read one file: a module path relative to src/ (e.g. 'core.js') "
            "or 'game.md'. With no line arguments this returns the file's "
            "ENTIRE current contents, which on a large module is the most "
            "expensive call you can make. Prefer a range: pass 'start_line' "
            "and 'end_line' to read just the part you care about, which is "
            "usually all you need after an edit or a build failure that names "
            "a line. Line numbers are 1-based and inclusive, and are the same "
            "ones search results and build errors report. A read is capped at "
            f"{_READ_MAX_CHARS:,} characters; if your range is longer than "
            "that the result says so and tells you the character offsets it "
            "covered, and you can pass 'char_start' to continue from there — "
            "which is also how you read part of one very long line."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": _PATH_ARG_DESCRIPTION},
                "start_line": {
                    "type": "integer",
                    "description": (
                        "1-based line to start at, inclusive. Omit to start at "
                        "line 1."
                    ),
                },
                "end_line": {
                    "type": "integer",
                    "description": (
                        "1-based line to stop at, inclusive. Omit to read to "
                        "the end of the file."
                    ),
                },
                "char_start": {
                    "type": "integer",
                    "description": (
                        "1-based character offset within the selected lines to "
                        "start at. Use it to continue a range that was capped, "
                        "or to see further along a single very long line."
                    ),
                },
            },
            "required": ["path"],
        },
    },
}

# The cheap counterpart to write_file, and the reason this sprint exists: a
# one-line change to a 73KB module used to cost 73KB of *output* tokens, which
# are the expensive kind and are capped per response. An exact-match edit costs
# the two strings it names.
#
# Exact match, exactly once, no fuzz. Every softer rule — match the first
# occurrence, match all of them, normalize whitespace, take the closest match —
# is a way to silently edit the wrong span of a file the model cannot see the
# result of. A rejection costs one cheap turn; a wrong-span edit costs a
# broken game that may still pass build, scan and smoke.
EDIT_FILE_TOOL = {
    "type": "function",
    "function": {
        "name": "edit_file",
        "description": (
            "Replace one exact span of text in an existing file. This is the "
            "PREFERRED way to change a file you are not rewriting wholesale: "
            "it costs only the text you pass, where write_file costs the "
            "whole module. 'old_string' must appear EXACTLY ONCE in the "
            "file's current contents, matched byte for byte including "
            "indentation and line breaks — the call is rejected if it appears "
            "zero times or more than once, and nothing is guessed or matched "
            "approximately. Include enough surrounding lines to make it "
            "unique. 'new_string' replaces it, and may be empty to delete the "
            "span. Make several edit_file calls for several separate changes."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": _PATH_ARG_DESCRIPTION},
                "old_string": {
                    "type": "string",
                    "description": (
                        "The exact text to replace, copied verbatim from the "
                        "file's current contents. Must occur exactly once."
                    ),
                },
                "new_string": {
                    "type": "string",
                    "description": "The replacement text. May be empty to delete the span.",
                },
            },
            "required": ["path", "old_string", "new_string"],
        },
    },
}

WRITE_FILE_TOOL = {
    "type": "function",
    "function": {
        "name": "write_file",
        "description": (
            "Replace one whole file with new contents, creating it if it "
            "doesn't exist yet. Always pass the COMPLETE file, never a diff "
            "or fragment. Use this to CREATE a file or to genuinely rewrite "
            "most of one — for a change to part of an existing file, prefer "
            "edit_file, which costs a fraction as much. If you add, remove, "
            "split, or rename any src/ file, also call "
            "write_file(\"game.md\", ...) to keep the map accurate. A write "
            "over the module size ceiling is rejected — split the module into "
            "smaller, cohesive files instead of trying to shrink it."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": _PATH_ARG_DESCRIPTION},
                "contents": {"type": "string", "description": "The complete new file contents."},
            },
            "required": ["path", "contents"],
        },
    },
}

# Why a grep exists alongside read_file: without one, the only way for the
# model to answer a narrow question about code it can't currently see is to
# read a whole module back. A real enhance run (job 79a0abbb, 2026-07-26)
# died doing exactly that — its last five turns before the no-progress kill
# were spent re-reading enemies.js, render.js and config.js to settle whether
# the constant was `TILE` or `TILE_SIZE`, a question one grep answers in a
# line. That run read 938KB of file contents across 33 read_file calls for a
# change that touched six files: render.js alone was read 7 times at 73KB a
# time. Whole-file reads are the agent path's dominant input-token cost, and
# most of them are asking something a search can answer.
SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "search",
        "description": (
            "Search the game's source for a regular expression, returning "
            "matching lines as 'path:line: text'. Searches every src/ file "
            "plus game.md, or just one file if you pass 'path'. Use this "
            "INSTEAD of re-reading a module whenever your question is narrow: "
            "does this identifier exist, where is it declared, what are its "
            "call sites, is it declared twice, which files reference it. It "
            "costs a tiny fraction of a read_file and covers every file at "
            "once. Reach for read_file only when you need a module's full "
            "text because you're about to rewrite it."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": (
                        "A regular expression (Python/PCRE syntax), "
                        r"e.g. 'function\s+drawPlayer' or '\bTILE_SIZE\b'. "
                        "Case-sensitive."
                    ),
                },
                "path": {
                    "type": "string",
                    "description": (
                        "Optional. Limit the search to this one file; omit to "
                        f"search all of them. {_PATH_ARG_DESCRIPTION}"
                    ),
                },
            },
            "required": ["pattern"],
        },
    },
}

FINISH_TOOL = {
    "type": "function",
    "function": {
        "name": "finish",
        "description": (
            "Call this when the requested change is complete. Triggers a "
            "build + safety scan + smoke test of the whole game. If "
            "verification fails you'll get the concrete failure back as "
            "this call's result — fix it and call finish again."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "One or two sentences summarizing what changed.",
                },
            },
            "required": ["summary"],
        },
    },
}

AGENT_TOOLS = [READ_MAP_TOOL, LIST_FILES_TOOL, READ_FILE_TOOL, SEARCH_TOOL,
               EDIT_FILE_TOOL, WRITE_FILE_TOOL, FINISH_TOOL]


# ---------------------------------------------------------------------------
# Path resolution — same escape/absolute-path discipline as builder.py
# ---------------------------------------------------------------------------

def _normalize_agent_path(path: str) -> str:
    """Canonical form of a model-supplied path: no './' prefix, no
    redundant 'src/' prefix (see _resolve_agent_path). Every observation and
    every pruning-bookkeeping key uses this, so 'src/core.js' and 'core.js'
    can't be mistaken for two different files."""
    path = path.strip()
    if not path:
        raise AgentError("empty path")
    if path.startswith("/"):
        raise AgentError(f"absolute path not allowed: {path!r}")
    if path.startswith("./"):
        path = path[2:]
    while path.startswith("src/"):
        path = path[4:]
    if not path:
        raise AgentError("empty path")
    return path


def _resolve_agent_path(game_dir: Path, path: str) -> Path:
    """Map a model-supplied path onto a real file. A leading 'src/' is
    collapsed (see _normalize_agent_path) because agent paths are ALREADY
    rooted at src/, so 'src/core.js' would otherwise nest to src/src/core.js.

    Sprint 6 step 2's pilot showed why this can't be left to prompt wording
    (it was, and the model ignored it): the run wrote its shell to BOTH
    'index.html' and 'src/index.html', landing two competing shells at
    src/index.html and src/src/index.html with every module under src/src/.
    builder.build_game only ever reads src/index.html, so the shell it would
    have built from was the one whose sibling refs pointed nowhere — and the
    model burned 17 list_files calls trying to reconcile a tree it had no
    way to see straight. Collapsing makes the tree single-rooted: 'core.js'
    and 'src/core.js' are the same file, last write wins."""
    path = _normalize_agent_path(path)
    if path == "game.md":
        return game_dir / "game.md"
    src_dir = (game_dir / "src").resolve()
    resolved = (game_dir / "src" / path).resolve()
    try:
        resolved.relative_to(src_dir)
    except ValueError:
        raise AgentError(f"path escapes src/: {path!r}") from None
    return resolved


# ---------------------------------------------------------------------------
# Tool argument parsing
# ---------------------------------------------------------------------------

def _parse_path_arg(arguments_json: str) -> str:
    try:
        args = json.loads(arguments_json)
    except json.JSONDecodeError as exc:
        raise AgentError(f"malformed arguments: not valid JSON: {exc}") from None
    if not isinstance(args, dict):
        raise AgentError("malformed arguments: must be a JSON object")
    path = args.get("path")
    if not isinstance(path, str) or not path.strip():
        raise AgentError("malformed arguments: missing a non-empty 'path'")
    return path.strip()


def _parse_read_args(arguments_json: str) -> tuple[str, int | None, int | None, int | None]:
    """(path, start_line, end_line, char_start) for a read_file call.

    Every bound is validated here rather than clamped silently. A clamp would
    hand back a window the model did not ask for and cannot tell apart from
    the one it did — the same reason _edit_file refuses to guess at a
    near-miss old_string. Bad bounds cost one cheap turn; a silently wrong
    window costs an edit built on bytes from the wrong place."""
    path = _parse_path_arg(arguments_json)
    args = json.loads(arguments_json)

    def _bound(name: str) -> int | None:
        value = args.get(name)
        if value is None:
            return None
        # bool is an int subclass, and a JSON `true` here is a malformed call,
        # not line 1.
        if isinstance(value, bool) or not isinstance(value, int):
            raise AgentError(
                f"malformed read_file arguments: {name!r} must be a whole "
                f"number, got {value!r}"
            )
        if value < 1:
            raise AgentError(
                f"malformed read_file arguments: {name!r} is 1-based, so it "
                f"must be 1 or greater, got {value}"
            )
        return value

    start_line = _bound("start_line")
    end_line = _bound("end_line")
    char_start = _bound("char_start")
    if start_line is not None and end_line is not None and end_line < start_line:
        raise AgentError(
            f"malformed read_file arguments: end_line ({end_line}) is before "
            f"start_line ({start_line})"
        )
    return path, start_line, end_line, char_start


def _parse_search_args(arguments_json: str) -> tuple[str, str | None]:
    try:
        args = json.loads(arguments_json)
    except json.JSONDecodeError as exc:
        raise AgentError(f"malformed search arguments: not valid JSON: {exc}") from None
    if not isinstance(args, dict):
        raise AgentError("malformed search arguments: must be a JSON object")
    pattern = args.get("pattern")
    if not isinstance(pattern, str) or not pattern.strip():
        raise AgentError("malformed search arguments: missing a non-empty 'pattern'")
    path = args.get("path")
    if path is not None and (not isinstance(path, str) or not path.strip()):
        raise AgentError("malformed search arguments: 'path' must be a non-empty string")
    return pattern, (path.strip() if isinstance(path, str) else None)


def _parse_write_args(arguments_json: str) -> tuple[str, str]:
    try:
        args = json.loads(arguments_json)
    except json.JSONDecodeError as exc:
        raise AgentError(
            f"malformed write_file arguments: not valid JSON ({exc}) — if this "
            "module's contents are very large, the reply may have been cut "
            "off by the output length limit; split it into smaller files "
            "and try again."
        ) from None
    if not isinstance(args, dict):
        raise AgentError("malformed write_file arguments: must be a JSON object")
    path = args.get("path")
    contents = args.get("contents")
    if not isinstance(path, str) or not path.strip():
        raise AgentError("malformed write_file arguments: missing a non-empty 'path'")
    if not isinstance(contents, str):
        raise AgentError("malformed write_file arguments: missing 'contents'")
    return path.strip(), contents


def _parse_edit_args(arguments_json: str) -> tuple[str, str, str]:
    """(path, old_string, new_string). new_string may legitimately be empty —
    that's how a span is deleted — so it's checked for type, not truthiness."""
    try:
        args = json.loads(arguments_json)
    except json.JSONDecodeError as exc:
        raise AgentError(
            f"malformed edit_file arguments: not valid JSON ({exc}) — if the "
            "strings you passed are very large, the reply may have been cut "
            "off by the output length limit; make the edit in smaller pieces "
            "and try again."
        ) from None
    if not isinstance(args, dict):
        raise AgentError("malformed edit_file arguments: must be a JSON object")
    path = args.get("path")
    old_string = args.get("old_string")
    new_string = args.get("new_string")
    if not isinstance(path, str) or not path.strip():
        raise AgentError("malformed edit_file arguments: missing a non-empty 'path'")
    if not isinstance(old_string, str):
        raise AgentError("malformed edit_file arguments: missing 'old_string'")
    if not isinstance(new_string, str):
        raise AgentError(
            "malformed edit_file arguments: missing 'new_string' — pass an "
            "empty string to delete the matched text."
        )
    return path.strip(), old_string, new_string


def _edit_payload_bytes(arguments_json: str) -> int:
    """How much text an edit_file call actually carries, for the compaction
    fuse in _run_react_loop. Unparseable arguments count as 0: there is
    nothing worth removing from a call that never ran."""
    try:
        _, old_string, new_string = _parse_edit_args(arguments_json)
    except AgentError:
        return 0
    return len(old_string.encode("utf-8")) + len(new_string.encode("utf-8"))


def _parse_finish_summary(arguments_json: str) -> str:
    try:
        args = json.loads(arguments_json)
    except json.JSONDecodeError:
        return ""
    if not isinstance(args, dict):
        return ""
    summary = args.get("summary")
    return summary.strip() if isinstance(summary, str) else ""


# ---------------------------------------------------------------------------
# Tool execution
# ---------------------------------------------------------------------------

def _read_map(game_dir: Path) -> str:
    map_path = game_dir / "game.md"
    if not map_path.is_file():
        return "ERROR: game.md not found"
    return map_path.read_text(encoding="utf-8")


def _list_files(game_dir: Path, written: set[str] | None = None) -> str:
    """Every src/ file with its size, and — for files this run has already
    rewritten — a written_this_run flag.

    That flag is bookkeeping the model otherwise has no way to recover.
    _compact_write_calls deliberately removes each executed write_file call
    from the conversation, so a run that has drifted past its own compaction
    notes cannot tell an edited module from an untouched one without reading
    it. Job 79a0abbb rewrote config.js five times and map.js three times for
    a single feature, each rewrite preceded by a read to work out what it had
    already done. One flag on the cheapest tool there is answers that."""
    src_dir = game_dir / "src"
    if not src_dir.is_dir():
        return "ERROR: src/ not found"
    written = written or set()
    entries = []
    for p in sorted(src_dir.rglob("*"), key=lambda p: p.as_posix()):
        if not p.is_file():
            continue
        rel = p.relative_to(src_dir).as_posix()
        entry = {"path": rel, "bytes": p.stat().st_size}
        if rel in written:
            entry["written_this_run"] = True
        entries.append(entry)
    return json.dumps(entries)


def _read_file(game_dir: Path, path: str, start_line: int | None = None,
                end_line: int | None = None, char_start: int | None = None) -> str:
    """One file's contents, whole or windowed.

    An unranged read returns the file byte-identical to what this function's
    predecessor returned — the common case must not change, both because
    existing transcripts are full of it and because a stable observation is a
    cached one (same reasoning as _match_window's short-line branch).

    A ranged read gets a header stating what it covered and what it did not.
    That header is the load-bearing part: job 837b2b8c's failure mode was the
    model treating bytes it could not see as bytes that did not exist, and a
    partial read with no stated bounds is an invitation to do exactly that.
    """
    file_path = _resolve_agent_path(game_dir, path)
    if not file_path.is_file():
        return f"ERROR: {path!r} not found"
    text = file_path.read_text(encoding="utf-8")
    if start_line is None and end_line is None and char_start is None:
        return text

    lines = text.splitlines(keepends=True)
    total_lines = len(lines)
    first = start_line or 1
    last = end_line or total_lines
    if first > total_lines:
        return (
            f"ERROR: {path!r} has {total_lines} lines, so start_line "
            f"{first} is past the end of the file."
        )
    last = min(last, total_lines)
    selected = "".join(lines[first - 1:last])

    offset = (char_start or 1) - 1
    if offset and offset >= len(selected):
        return (
            f"ERROR: lines {first}-{last} of {path!r} are {len(selected)} "
            f"characters, so char_start {char_start} is past their end."
        )
    window = selected[offset:offset + _READ_MAX_CHARS]
    end_offset = offset + len(window)

    # A "NOTE:" line, not a "===== ... =====" banner. The banner form is what
    # delimits the source snapshot, and _write_file hard-rejects file contents
    # containing one precisely because the model copies scaffolding it has seen
    # (see _SNAPSHOT_MARKER_RE and _PRUNE_SENTINEL). Reusing that shape here to
    # mean something else would be teaching the confusion the guard exists to
    # prevent. "NOTE:" is already this file's vocabulary for an observation
    # prefix (_read_file_nudge, _write_file's size lint).
    span = f"line {first}" if first == last else f"lines {first}-{last}"
    header = (f"NOTE: this is a PARTIAL read of {path} — {span} of "
              f"{total_lines} lines")
    if offset or end_offset < len(selected):
        header += (
            f", characters {offset + 1}-{end_offset} of the {len(selected)} "
            "in that line range"
        )
    header += ". "
    if end_offset < len(selected):
        header += (
            f"That range continues past what is shown — pass "
            f"char_start={end_offset + 1} to read on from here. "
        )
    header += "Everything below is that file's exact current bytes.\n"
    return header + window


def _read_file_nudge(path: str, written: set[str] | None,
                      snapshot_paths: frozenset | None,
                      ranged: bool = False) -> str:
    """A one-line prefix for a read of a file the snapshot already carries
    unchanged, or "" when the read was worth making.

    The full contents still come back underneath it. Withholding information
    is what historically sent this agent into state-re-checking loops (see
    _compact_write_calls' note wording), and a read that returns less than it
    used to just gets made again. This is an *observation*, not an arguments
    slot, which is the category that has never caused an imitation bug — same
    shape as _write_file's soft size lint."""
    if ranged:
        # A ranged read of an unmodified snapshot file is not the waste this
        # nudge is about. It is the cheap navigation act the range arguments
        # exist to encourage — a few thousand characters against a whole
        # module — and nudging it would push the model back toward the
        # unranged call this is trying to discourage.
        return ""
    if not snapshot_paths or path not in snapshot_paths:
        return ""
    if written and path in written:
        return ""
    return (
        f"NOTE: {path} has not been modified during this run, so it is still "
        "byte-for-byte identical to the copy in the source snapshot in your "
        "instructions — this read told you nothing new. The snapshot has "
        "every file in full; read_file is only worth a step for a file "
        "created or rewritten during this run, and even then a start_line/"
        "end_line range costs a fraction of the whole module.\n"
    )


# A search result rides along in the conversation for the rest of the run —
# as, since Sprint 6a removed both prune sites, does every read_file result.
# That's the point: an answer the model can still see is an answer it won't
# ask for twice. Which only holds if the answer stays small, hence both caps. A
# pattern broad enough to blow through them is a pattern that should have
# been narrower, and the observation says so. Worst case is 60 × 400 ≈ 24KB
# of conversation, once, at the cached rate thereafter.
_SEARCH_MAX_MATCHES = 60
# 400, up from 200, and — the part that actually mattered — the window is
# centred on the MATCH rather than taken from the start of the line (see
# _match_window). Job 837b2b8c (2026-07-27) spent 20 of its 60 turns on one
# line of a render.js with 137 lines over 200 chars, the longest 4,729: every
# search reported `1 match(es)` and then showed the first 200 characters,
# which did not contain the match. The model re-read the whole 96KB file three
# times (~36,700 fresh tokens each, 22% of that run's total cost), never saw
# the bytes it needed, started guessing that the file held literal `\ud83c`
# escapes where it actually holds the emoji, and ran out of steps mid-change.
# Its own reasoning at step 45: "the entire line is a single very long line
# and search only reports the beginning."
#
# This is the failure class this file has already been bitten by twice (the
# declaration-parity gate that rejected its own remedy; the module ceiling the
# model dodged by deleting code): _edit_file's zero-match rejection tells the
# model to use search to find the text "as it is actually written", and search
# structurally could not do that for a long line.
_SEARCH_MAX_LINE_CHARS = 400


def _match_window(line: str, match: re.Match) -> tuple[str, bool]:
    """One line of search output, and whether it had to be windowed.

    A line that fits is returned stripped, byte-identical to what this
    function's predecessor produced — the common case must not change, both
    because it is the overwhelming majority of matches and because a stable
    observation is a cached one.

    A line that does not fit is windowed around `match` instead of truncated
    from the left, because the whole point of the observation is to show the
    model the bytes it is about to copy into an edit_file `old_string`, and on
    a 4,729-char line those bytes are nowhere near character zero. The window
    carries its own position, so a model that needs a different part of the
    line knows one exists and can slide the window by searching for something
    further along it rather than re-reading the file.
    """
    stripped = line.strip()
    if len(stripped) <= _SEARCH_MAX_LINE_CHARS:
        return stripped, False

    start, end = match.span()
    span = end - start
    if span >= _SEARCH_MAX_LINE_CHARS:
        # A match wider than the window: anchor at its start rather than
        # centring, so the window at least begins where the model asked.
        win_start = start
    else:
        win_start = max(0, start - (_SEARCH_MAX_LINE_CHARS - span) // 2)
    win_end = min(len(line), win_start + _SEARCH_MAX_LINE_CHARS)
    # Pull back off the right edge so a match near the end of the line still
    # gets a full window of context rather than a stub.
    win_start = max(0, win_end - _SEARCH_MAX_LINE_CHARS)

    window = line[win_start:win_end]
    if win_start == 0:
        # Match the stripped short-line branch, and keep the reported offsets
        # honest about what was dropped.
        lstripped = window.lstrip()
        win_start += len(window) - len(lstripped)
        window = lstripped
    if win_end == len(line):
        window = window.rstrip()

    text = ("…" if win_start > 0 else "") + window + ("…" if win_end < len(line) else "")
    return f"{text}  [chars {win_start + 1}-{win_end} of {len(line)}]", True


def _search(game_dir: Path, pattern: str, path: str | None = None) -> str:
    try:
        rx = re.compile(pattern)
    except re.error as exc:
        return f"ERROR: not a valid regular expression: {exc}"

    if path is not None:
        target = _resolve_agent_path(game_dir, path)
        if not target.is_file():
            return f"ERROR: {path!r} not found"
        targets = [(path, target)]
    else:
        src_dir = game_dir / "src"
        if not src_dir.is_dir():
            return "ERROR: src/ not found"
        targets = [
            (p.relative_to(src_dir).as_posix(), p)
            for p in sorted(src_dir.rglob("*"), key=lambda p: p.as_posix())
            if p.is_file()
        ]
        game_md = game_dir / "game.md"
        if game_md.is_file():
            targets.append(("game.md", game_md))

    lines: list[str] = []
    total = 0
    any_windowed = False
    for rel, file_path in targets:
        try:
            text = file_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            match = rx.search(line)
            if match is None:
                continue
            total += 1
            if len(lines) >= _SEARCH_MAX_MATCHES:
                continue
            shown, windowed = _match_window(line, match)
            any_windowed = any_windowed or windowed
            lines.append(f"{rel}:{lineno}: {shown}")

    # The pattern is echoed back VERBATIM, not through !r. repr() would turn
    # `function\s+\w+` into `'function\\s+\\w+'`, and this model reliably
    # imitates whatever its own transcript shows it (see _compact_write_calls) —
    # here that would teach it to double-escape its next pattern into a literal
    # backslash.
    scope = f"'{path}'" if path is not None else f"{len(targets)} file(s)"
    if not total:
        return f"No matches for `{pattern}` in {scope}."
    header = f"{total} match(es) for `{pattern}` in {scope}"
    if total > len(lines):
        header += (
            f" — showing the first {len(lines)}. Narrow the pattern if you "
            "need the rest"
        )
    out = header + ":\n" + "\n".join(lines)
    if any_windowed:
        # Said once, in the header, rather than on every windowed line: the
        # '…' is the one piece of scaffolding in this observation that could
        # end up copied into an edit_file 'old_string', and one clear sentence
        # is both cheaper and more legible than 60 repetitions of a caveat.
        # An observation is also the safe place to put it — the imitation bugs
        # this file documents all came from text left in an ARGUMENTS slot
        # (see _compact_write_calls), never from a tool result.
        out += (
            "\n\nSome matches are on lines too long to show whole. Those are "
            "windowed around the match: '…' marks where the line was trimmed "
            "and '[chars A-B of N]' gives the window's position. Neither is "
            "part of the file — do not include them in an edit_file "
            "'old_string'. To see a different part of a long line, search for "
            "something written further along it; the window follows the match."
        )
    return out


def _write_file(game_dir: Path, path: str, contents: str, max_module_bytes: int,
                 warn_bytes: int) -> str:
    file_path = _resolve_agent_path(game_dir, path)
    size = len(contents.encode("utf-8"))
    if _PRUNE_SENTINEL in contents:
        # Defense in depth against the Sprint 6 step 2 stub-write bug (see
        # _compact_write_calls): every placeholder this module puts into the
        # conversation carries _PRUNE_SENTINEL, so contents echoing one back
        # means the model has copied bookkeeping text into a real file
        # instead of authoring it. Reject loudly and tell it what to do —
        # silently writing it is what turned that bug into a multi-hundred-
        # thousand-token stall with a corrupt module on disk.
        return (
            f"REJECTED: the contents you passed for {path!r} are a "
            "context-pruning placeholder from this conversation's history, "
            "not real file contents. Those placeholders mark where earlier "
            "material was dropped to save context — never copy one into a "
            "file. Call read_file to see what's currently on disk, then "
            "write the complete, real contents."
        )
    if _SNAPSHOT_MARKER_RE.search(contents):
        # Same class of bug as the sentinel above, and the same defense. The
        # source snapshot in the system prompt wraps each file in
        # '===== BEGIN <path> ... =====' lines, and this model reliably
        # imitates the scaffolding it is shown — a module whose first line is
        # a BEGIN marker is a syntax error the moment the build inlines it.
        return (
            f"REJECTED: the contents you passed for {path!r} contain a "
            "'===== BEGIN/END ... =====' line. Those markers are scaffolding "
            "that delimits files in the source snapshot in your instructions; "
            "they are never part of a file's contents. Pass only the file's "
            "own text, with no marker lines around or inside it."
        )
    if size > max_module_bytes:
        return (
            f"REJECTED: {path!r} is {size} bytes, over the {max_module_bytes}-byte "
            "module size ceiling. Split this module into smaller, cohesive "
            "files instead of shrinking it. If you do, you MUST also rewrite "
            "src/index.html so its <script> tags list the new files in place "
            "of this one — a shell still pointing at a file you never wrote "
            "fails the build — and update game.md's file table to match."
        )
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(contents, encoding="utf-8")
    ok = f"OK: wrote {size} bytes to {path}"
    if size > warn_bytes:
        # Soft lint only — the write already happened above. Folded into the
        # same observation string (not a separate event) so it survives
        # _compact_write_calls' note verbatim, same as the OK line itself.
        ok += (
            f". Note: {path!r} is getting large ({size} of a {max_module_bytes}-byte "
            "ceiling) — consider splitting it into smaller cohesive modules on "
            "your next pass through this file, before it forces a rejection."
        )
    return ok


def _edit_file(game_dir: Path, path: str, old_string: str, new_string: str,
                max_module_bytes: int, warn_bytes: int) -> str:
    """Replace one exactly-once occurrence of old_string in `path`.

    Every rejection below leaves the file byte-identical on disk, and none of
    them ever echoes a candidate `old_string` back, offers a "did you mean",
    or shows a near-miss diff. All three would teach precisely the fuzzy
    matching this tool refuses to do — the model imitates what its transcript
    shows it (see _compact_write_calls), and a tool that demonstrates
    approximate matching in its error messages will get approximate matches
    passed back. Counts and a directive; nothing else."""
    file_path = _resolve_agent_path(game_dir, path)
    if not file_path.is_file():
        return (
            f"ERROR: {path!r} not found — edit_file only changes an existing "
            "file. Use write_file(path, contents) to create it."
        )
    if not old_string:
        return (
            f"REJECTED: 'old_string' was empty for {path!r}. edit_file replaces "
            "one exact span of existing text; to create a file or replace all "
            "of it, use write_file(path, contents)."
        )
    # Same two guards as _write_file, applied to BOTH strings. old_string is
    # guarded too because a match attempt against snapshot scaffolding is the
    # model treating the listing as file content — the same misreading that
    # would corrupt a write, one step earlier.
    for label, text in (("old_string", old_string), ("new_string", new_string)):
        if _PRUNE_SENTINEL in text:
            return (
                f"REJECTED: the {label} you passed for {path!r} contains a "
                "context-pruning placeholder from this conversation's history, "
                "not real file text. Those placeholders mark where earlier "
                "material was dropped to save context — they are never part of "
                "any file. Call read_file to see what is currently on disk."
            )
        if _SNAPSHOT_MARKER_RE.search(text):
            return (
                f"REJECTED: the {label} you passed for {path!r} contains a "
                "'===== BEGIN/END ... =====' line. Those markers are scaffolding "
                "that delimits files in the source snapshot in your instructions; "
                "they are never part of a file's contents. Pass only text from "
                "inside the file itself."
            )
    text = file_path.read_text(encoding="utf-8")
    count = text.count(old_string)
    if count == 0:
        return (
            f"REJECTED: that 'old_string' does not appear in {path!r}. The "
            "match is exact — byte for byte, including indentation and line "
            "breaks — and nothing is matched approximately. Use "
            "search(pattern, path=" + repr(path) + ") to find the text as it "
            "is actually written in the file's CURRENT contents (which include "
            "every edit you have already made this run), and pass that exactly."
        )
    if count > 1:
        return (
            f"REJECTED: that 'old_string' appears {count} times in {path!r}, so "
            "it is ambiguous — edit_file only replaces a span that occurs "
            "exactly once. Extend 'old_string' with the surrounding lines that "
            "make the occurrence you mean unique, and call edit_file again. If "
            "you meant to change all of them, make one call per occurrence, "
            "each with its own unique surrounding context."
        )
    if new_string == old_string:
        # Would consume a step and, worse, register as progress against the
        # stall guard while changing nothing.
        return (
            f"REJECTED: 'new_string' is identical to 'old_string' for {path!r}, "
            "so this edit would change nothing. Pass the text you actually want "
            "in its place."
        )
    updated = text.replace(old_string, new_string, 1)
    size = len(updated.encode("utf-8"))
    if size > max_module_bytes:
        # Checked against the RESULT, not the payload: a small edit can still
        # push a module over. The file is left untouched.
        return (
            f"REJECTED: that edit would make {path!r} {size} bytes, over the "
            f"{max_module_bytes}-byte module size ceiling. {path!r} is unchanged. "
            "Split this module into smaller, cohesive files instead of shrinking "
            "it. If you do, you MUST also rewrite src/index.html so its <script> "
            "tags list the new files in place of this one, and update game.md's "
            "file table to match."
        )
    file_path.write_text(updated, encoding="utf-8")
    removed = len(old_string.encode("utf-8"))
    added = len(new_string.encode("utf-8"))
    ok = f"OK: edited {path} (-{removed} +{added} bytes, now {size} bytes)"
    if size > warn_bytes:
        ok += (
            f". Note: {path!r} is getting large ({size} of a {max_module_bytes}-byte "
            "ceiling) — consider splitting it into smaller cohesive modules on "
            "your next pass through this file, before it forces a rejection."
        )
    return ok


def _execute_tool(tc: ai.ToolCall, game_dir: Path, max_module_bytes: int,
                   warn_bytes: int, written: set[str] | None = None,
                   snapshot_paths: frozenset | None = None) -> tuple[str, str | None]:
    """Run one non-finish tool call. Returns (observation, touched_path) —
    touched_path is the path read/written, or None for tools that don't
    target a single file. Never raises: an AgentError becomes an
    "ERROR: ..." observation, same as a rejected submit_game becomes
    "REJECTED: ..." in game_generator's loop.

    `written` is the set of paths this run has already rewritten, surfaced by
    list_files (see _list_files). `snapshot_paths` is the set of files the
    system prompt already carries in full, which turns a redundant read into
    a nudge (see _read_file_nudge)."""
    try:
        if tc.name == "read_map":
            return _read_map(game_dir), None
        if tc.name == "list_files":
            return _list_files(game_dir, written), None
        if tc.name == "read_file":
            path, start_line, end_line, char_start = _parse_read_args(tc.arguments)
            path = _normalize_agent_path(path)
            observation = _read_file(game_dir, path, start_line, end_line, char_start)
            if not observation.startswith("ERROR:"):
                ranged = any(v is not None for v in (start_line, end_line, char_start))
                observation = _read_file_nudge(
                    path, written, snapshot_paths, ranged) + observation
            return observation, path
        if tc.name == "search":
            pattern, path = _parse_search_args(tc.arguments)
            if path is not None:
                path = _normalize_agent_path(path)
            return _search(game_dir, pattern, path), path
        if tc.name == "edit_file":
            path, old_string, new_string = _parse_edit_args(tc.arguments)
            path = _normalize_agent_path(path)
            return _edit_file(game_dir, path, old_string, new_string,
                              max_module_bytes, warn_bytes), path
        if tc.name == "write_file":
            path, contents = _parse_write_args(tc.arguments)
            path = _normalize_agent_path(path)
            return _write_file(game_dir, path, contents, max_module_bytes, warn_bytes), path
        return f"ERROR: unknown tool {tc.name!r}", None
    except AgentError as exc:
        return f"ERROR: {exc}", None


def _progress_key(tc: ai.ToolCall, touched_path: str | None):
    """A hashable identity for the *observation* a non-write tool call
    produced, or None if the call has no reusable identity. Two calls with
    the same key tell the run the same thing, so the second one taught it
    nothing (see _MAX_NO_PROGRESS_STEPS).

    Deliberately not invalidated by a later write to the same path: after
    write_file(p) the model already knows p's contents — it just emitted
    them — so re-reading p is exactly the "careful final review" pass the
    finish nudge exists to answer, not new information."""
    if tc.name in ("read_map", "list_files"):
        return (tc.name,)
    if tc.name == "read_file":
        # The range is part of the identity. Two reads of different parts of
        # the same module are two different observations, and keying on the
        # path alone would score the second as a repeat — pushing a run that
        # is navigating a large file exactly as intended toward the stall
        # guard. That guard already killed a healthy run once (job 73df2b10)
        # by miscounting productive turns as idle ones.
        try:
            _, start_line, end_line, char_start = _parse_read_args(tc.arguments)
        except AgentError:
            return None
        return ("read_file", touched_path, start_line, end_line, char_start)
    if tc.name == "search":
        try:
            pattern, _ = _parse_search_args(tc.arguments)
        except AgentError:
            return None
        return ("search", pattern, touched_path)
    # write_file and edit_file fall through: a mutation is not an observation.
    # Their progress is tracked separately, and only when they SUCCEED (see
    # _run_react_loop), so a run looping on an old_string that never matches
    # still trips the stall guard.
    return None


def _classify_failure(detail: str) -> str:
    if detail.startswith("build failed"):
        return "build_error"
    if detail.startswith("safety violation"):
        return "safety_violation"
    return "smoke_test_failed"


# ---------------------------------------------------------------------------
# Event emission (Sprint 3) — durable, ordered think/act/observe transcript
# ---------------------------------------------------------------------------

# These caps apply *before* the event is stored, so the transcript a
# requester watched and the transcript replayed months later are the same
# bytes by construction — but anything trimmed here is gone from both. 4000
# was too tight to review a run by: measured over a real explode pilot, 9 of
# 10 reasoning blocks came in under 900 chars while the one that mattered
# most — the up-front "here's how I'll split this game" plan — was the
# single block that hit the ceiling and lost its tail. The chat panes
# collapse anything past 240 chars into a <details>, so a longer thought
# costs display space only when someone expands it.
_THOUGHT_MAX_CHARS = 24000
_ASSISTANT_MAX_CHARS = 2000


def _make_emitter(job_id: str | None, db_conn) -> Callable:
    """Default emit callback: writes a db.add_agent_event row. A no-op when
    there's no job_id to key events on (e.g. headless/direct calls in
    tests) — mirrors the rest of this module's "job_id is optional,
    everything DB-shaped becomes a no-op without it" convention."""
    if job_id is None:
        return lambda role, content=None, data=None: None
    return lambda role, content=None, data=None: db.add_agent_event(
        job_id, role, content=content, data=data, conn=db_conn
    )


def _safe_emit(emit: Callable, role: str, content: str | None = None, data: dict | None = None) -> None:
    """Emitting must never fail the job — same swallow-and-log discipline as
    game_generator.run_moderation_pass. A raising emitter (or one that hits
    a transient DB error) just means this one event is lost, not the run."""
    try:
        emit(role, content, data)
    except Exception:
        _logger.exception("agent event emit failed (role=%s)", role)


def _reasoning_content(ask_result: ai.ToolAskResult) -> str | None:
    """Thinking-mode chain-of-thought, when present, pulled from the raw
    response — ai_client.ask_with_tools() strips reasoning_content from the
    returned .message (DeepSeek rejects it echoed back), so the raw payload
    is the only place left carrying it."""
    try:
        choices = ask_result.raw_response.get("choices") or []
        message = choices[0].get("message") or {}
    except (AttributeError, IndexError, TypeError):
        return None
    text = message.get("reasoning_content")
    return text.strip() if isinstance(text, str) and text.strip() else None


def _summarize_tool_call(tc: ai.ToolCall) -> tuple[str, dict]:
    """A tool_call event's (content, data) — never the full write_file
    contents, just the path + byte count, so nothing here duplicates the
    file bytes that already live in src/."""
    if tc.name == "read_file":
        try:
            path, start_line, end_line, char_start = _parse_read_args(tc.arguments)
            data = {"tool": tc.name, "path": path}
            bits = []
            if start_line is not None or end_line is not None:
                data["start_line"] = start_line
                data["end_line"] = end_line
                bits.append(f"lines {start_line or 1}-{end_line or 'end'}")
            if char_start is not None:
                data["char_start"] = char_start
                bits.append(f"from char {char_start}")
            if bits:
                return f"read_file({path!r}, {', '.join(bits)})", data
            return f"read_file({path!r})", data
        except AgentError:
            return "read_file(...)", {"tool": tc.name}
    if tc.name == "write_file":
        try:
            path, contents = _parse_write_args(tc.arguments)
            size = len(contents.encode("utf-8"))
            return f"write_file({path!r}, {size} bytes)", {"tool": tc.name, "path": path, "bytes": size}
        except AgentError:
            return "write_file(...)", {"tool": tc.name}
    if tc.name == "edit_file":
        # Byte counts only. agent_events is a permanent, publicly replayable
        # archive (the chat pane renders from nothing else), and old_string/
        # new_string are file contents — the same reason write_file's contents
        # never reach an event.
        try:
            path, old_string, new_string = _parse_edit_args(tc.arguments)
            removed = len(old_string.encode("utf-8"))
            added = len(new_string.encode("utf-8"))
            return (f"edit_file({path!r}, -{removed} +{added} bytes)",
                    {"tool": tc.name, "path": path, "removed": removed, "added": added})
        except AgentError:
            return "edit_file(...)", {"tool": tc.name}
    if tc.name == "search":
        try:
            pattern, path = _parse_search_args(tc.arguments)
            data = {"tool": tc.name, "pattern": pattern}
            if path:
                data["path"] = path
                return f"search({pattern!r} in {path!r})", data
            return f"search({pattern!r})", data
        except AgentError:
            return "search(...)", {"tool": tc.name}
    if tc.name == "finish":
        summary = _parse_finish_summary(tc.arguments)
        data = {"tool": tc.name}
        if summary:
            data["summary"] = summary
        return "finish(...)", data
    return f"{tc.name}()", {"tool": tc.name}


def _summarize_observation(tc_name: str, path: str | None, observation: str) -> tuple[str, dict]:
    """A tool_result event's (content, data) for a non-finish tool call.
    read_map/read_file observations ARE the file's full contents, so those
    get replaced with a short "read N bytes" summary; write_file's and
    edit_file's observations are already just an OK/REJECTED line (safe to
    keep verbatim); list_files' observation is a small path+size listing, not
    file bytes, so it's also kept verbatim."""
    data: dict = {"tool": tc_name}
    if path:
        data["path"] = path
    if tc_name in ("read_file", "read_map"):
        if observation.startswith("ERROR:"):
            return observation, data
        size = len(observation.encode("utf-8"))
        data["bytes"] = size
        label = path if tc_name == "read_file" else "game.md"
        return f"Read {label} ({size} bytes)", data
    if tc_name in ("write_file", "edit_file"):
        # Both observations are already just an OK/REJECTED line — no file
        # bytes in either — so they're kept verbatim.
        data["outcome"] = "ok" if observation.startswith("OK:") else "rejected"
        return observation, data
    if tc_name == "list_files":
        try:
            data["file_count"] = len(json.loads(observation))
        except (json.JSONDecodeError, TypeError):
            pass
        return observation, data
    if tc_name == "search":
        # Capped to _SEARCH_MAX_MATCHES lines by construction, so the whole
        # observation is safe to keep verbatim — but only its first line
        # (the "N match(es) for ..." header) is worth a transcript row.
        head, _, _ = observation.partition("\n")
        return head, data
    return observation, data


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------

def _build_system_prompt(source_title: str,
                          snapshot: SourceSnapshot | None = None,
                          engine: str | None = None) -> str:
    """The enhance pass's system message.

    Everything in it must be stable per source game — no timestamp, job id or
    fork slug — because that stability is what lets a second enhance of the
    same source hit DeepSeek's prefix cache on the whole block, snapshot
    included (Sprint 6a). `engine` is a property of the source game, not of
    the run, so it does not break that stability."""
    allowed_hosts = ", ".join(sorted(safety.ALLOWED_CDN_HOSTS))
    three_section = (
        engines.three_contract(multi_file=True)
        if engine == engines.ENGINE_THREE else ""
    )
    has_snapshot = snapshot is not None and snapshot.text is not None
    if has_snapshot:
        # Say what is still true, then name the exceptions. Never a bare
        # hedge like "this may be out of date": the documented failure mode
        # is the model reading a hedge as "nothing here is trustworthy" and
        # launching a re-verification sweep, which is precisely what
        # _compact_write_calls' first note wording caused.
        structure = (
            "The complete current source of this game is included below, "
            "under '## Current source'. It is every file — game.md plus "
            "every src/ file — exactly as it is on disk right now, at the "
            "start of this run. You are not exploring a codebase you cannot "
            "see; you already have all of it.\n"
            "The snapshot is authoritative as of the start of this run, and "
            "stays authoritative for every file you do not change. Do NOT "
            "call read_map, list_files or read_file for anything it already "
            "contains — that spends a step and tells you nothing. There are "
            "exactly two reasons to read: a file created later in this run, "
            "and re-reading a file after you have rewritten it with "
            "write_file. In both cases read the LINE RANGE you care about "
            "rather than the whole module — a whole-file read of a large "
            "module is the most expensive thing you can do, and it is almost "
            "never what the question actually needs.\n"
            "You never need to re-read a file after an edit_file call you can "
            "still see in this conversation: that file's current contents are "
            "the snapshot's version with those edits applied, in order.\n"
            "Your tools:\n"
        )
    else:
        structure = (
            "The game's source is split across multiple files instead of one "
            "big index.html, so you never have to read or rewrite the whole "
            "game at once. Explore before you edit, using these tools:\n"
        )
    prompt = (
        f"You are enhancing an existing multi-file browser game in the "
        f"arcade, currently titled '{source_title}'. This produces a NEW "
        "game entry — the original is left completely untouched; you are "
        "editing a fresh copy that becomes a new forked entry once your "
        "changes pass verification.\n\n"
        "## How this game is structured\n"
        + structure +
        "- read_map() — game.md: a prose description plus a table of every "
        "src/ file and its purpose.\n"
        "- list_files() — every src/ file with its byte size.\n"
        "- read_file(path, start_line=None, end_line=None) — one src/ file "
        "(or game.md). With no range this returns the WHOLE file and is the "
        "most expensive call available to you; with a range it costs a "
        "fraction of that. When you already know roughly where you need to "
        "look — a line number from a search result or a build error, the "
        "area around an edit you just made — read that range, not the "
        "module. Line numbers are 1-based and inclusive. If a range is too "
        "long to return at once the result says so and gives you a "
        "char_start to continue from, which is also how you read part of a "
        "single very long line.\n"
        "- search(pattern, path=None) — regex search across every file, "
        "returning matching lines as 'path:line: text'. This is how you "
        "answer a narrow question: whether an identifier exists, where it's "
        "declared, every call site of a function, which module owns a "
        "constant. It costs a fraction of a read_file, so use it instead of "
        "re-reading a module you've already seen. A search result gives you "
        "the line number, which is what a ranged read_file wants next if you "
        "need more context around it; reach for an UNRANGED read_file only "
        "when you genuinely need a file's whole text because you're about to "
        "rewrite it.\n"
        "- edit_file(path, old_string, new_string) — replace one exact span "
        "of text in an existing file. THIS IS THE TOOL TO REACH FOR when you "
        "are changing part of a file: it costs only the text you pass, where "
        "write_file costs the entire module. old_string must match the file's "
        "current contents byte for byte and appear EXACTLY ONCE — include "
        "enough surrounding lines to make it unique — and nothing is matched "
        "approximately, so a mismatch is rejected rather than guessed at. Use "
        "one call per change; several small edits are far cheaper than one "
        "whole-file rewrite.\n"
        "- write_file(path, contents) — replace ONE WHOLE file (or create a "
        "new one). Always the complete file, never a diff. Use it to create a "
        "file, or when you are genuinely rewriting most of one; otherwise "
        "prefer edit_file. Rejected if over the module size ceiling — split "
        "it instead of shrinking it.\n"
        "- finish(summary) — call once you're done. Triggers a build + "
        "safety scan + smoke test; a failure comes back as this call's "
        "result so you can keep editing and call finish again.\n\n"
        + three_section
        + "## Contract\n"
        "All HTML/CSS/JS stays inline within the src/ files (no separate "
        "asset files). You may load external JavaScript modules or "
        "stylesheets via <script>/<link> tags ONLY from these CDN hosts: "
        f"{allowed_hosts}. Do not reference any other external host, and do "
        "not attempt any network calls back to this site or anywhere else "
        "at runtime.\n\n"
        "## Sandbox constraints\n"
        "The game is played inside a sandboxed <iframe> with no "
        "same-origin access: document.cookie, localStorage, sessionStorage, "
        "indexedDB, and window.parent/window.top are all unavailable — keep "
        "all game state in ordinary JavaScript variables. Do not use "
        "eval() or `new Function(...)`.\n\n"
        "## Structural changes\n"
        "If you add, remove, split, or rename any src/ file, you MUST "
        "update game.md via write_file(\"game.md\", ...) so the map stays "
        "accurate for the next enhancement.\n\n"
        "## Quality bar\n"
        "Apply the requested change while preserving everything else about "
        "the game the request doesn't ask you to touch — its feel, "
        "controls, and existing polish are working and should survive "
        "unrelated edits. Only touch the module(s) the change actually "
        "requires; leave everything else byte-for-byte as it was.\n"
    )
    if snapshot is None:
        return prompt
    if not has_snapshot:
        # Over the ceiling: no bodies, but the manifest still costs a few
        # hundred stable bytes and saves the list_files turn outright.
        return prompt + "\n## Current source\n" + snapshot.manifest + "\n"
    return (
        prompt
        + "\n## Current source\n"
        + snapshot.manifest + "\n\n"
        + "Each file below is delimited by '===== BEGIN <path> (<n> bytes) "
          "=====' and '===== END <path> =====' lines. Those marker lines are "
          "scaffolding for this listing ONLY — they are not part of any "
          "file's contents, and a write must never include one. Paths are "
          "exactly what the tools take: 'render.js', not 'src/render.js'.\n\n"
        + snapshot.text + "\n"
    )


def _build_user_prompt(description: str) -> str:
    return f"Enhance/fix this game per this request: {description}"


_INLINE_SCRIPT_RE = re.compile(
    r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.S | re.I)
_DECL_RE = re.compile(
    r"\b(?:function|const|let|var|class)\s+([A-Za-z_$][\w$]*)")
_IDENT_RE = re.compile(r"[A-Za-z_$][\w$]*")
# A `(` … `)` immediately before a `{` makes that brace a function body only
# if what precedes the parens isn't one of these — `if (x) {` and
# `catch (e) {` look identical to `f(x) {` from behind.
_CONTROL_HEADS = frozenset({
    "if", "for", "while", "switch", "catch", "with", "do", "else", "try",
    "finally", "return",
})
# Where a `/` can only start a regex literal, never a division: masking those
# matters because an unmasked `/[{'"]/` would unbalance the brace walk below.
_REGEX_PRECEDERS = frozenset("(,=:[!&|?{};+-*%^~<>\n")
_REGEX_KEYWORDS = frozenset({
    "return", "typeof", "instanceof", "in", "of", "new", "delete", "void",
    "case", "do", "else", "yield", "await",
})


def _inline_script(html: str) -> str:
    return "".join(_INLINE_SCRIPT_RE.findall(html))


def _mask_js_literals(js: str) -> str:
    """Blank the *contents* of every string, template literal, regex literal
    and comment, keeping the text's length and newlines so offsets still line
    up. Everything below walks braces to decide what scope a declaration sits
    in, and a `}` inside a string or a `/*` comment would silently shift that
    walk by a whole scope."""
    out = list(js)
    n = len(js)
    i = 0
    prev = ""          # last significant char seen outside a literal
    prev_word = ""     # last identifier/keyword seen outside a literal

    def blank(start: int, end: int) -> None:
        for k in range(start, min(end, n)):
            if js[k] != "\n":
                out[k] = " "

    while i < n:
        ch = js[i]
        if ch in "\"'":
            j = i + 1
            while j < n:
                if js[j] == "\\":
                    j += 2
                    continue
                if js[j] == ch or js[j] == "\n":
                    break
                j += 1
            blank(i + 1, j)
            i = j + 1
            prev, prev_word = ch, ""
            continue
        if ch == "`":
            # Template literals nest: `${ `${x}` }`. Blank the whole thing
            # including substitutions — a declaration never lives in one, and
            # blanking both the `${` and its `}` keeps the walk balanced.
            j = i + 1
            depth = 0
            while j < n:
                if js[j] == "\\":
                    j += 2
                    continue
                if js[j] == "$" and js[j + 1:j + 2] == "{":
                    depth += 1
                    j += 2
                    continue
                if depth and js[j] == "}":
                    depth -= 1
                elif not depth and js[j] == "`":
                    break
                j += 1
            blank(i + 1, j)
            i = j + 1
            prev, prev_word = "`", ""
            continue
        if ch == "/" and js[i + 1:i + 2] == "/":
            j = js.find("\n", i)
            j = n if j == -1 else j
            blank(i, j)
            i = j
            continue
        if ch == "/" and js[i + 1:i + 2] == "*":
            j = js.find("*/", i + 2)
            j = n if j == -1 else j + 2
            blank(i, j)
            i = j
            continue
        if ch == "/" and (prev in _REGEX_PRECEDERS or prev == ""
                          or prev_word in _REGEX_KEYWORDS):
            j = i + 1
            in_class = False
            while j < n:
                c = js[j]
                if c == "\\":
                    j += 2
                    continue
                if c == "\n":
                    break
                if c == "[":
                    in_class = True
                elif c == "]":
                    in_class = False
                elif c == "/" and not in_class:
                    break
                j += 1
            blank(i + 1, j)
            i = j + 1
            prev, prev_word = "/", ""
            continue
        if not ch.isspace():
            if ch.isalnum() or ch in "_$":
                m = _IDENT_RE.match(js, i)
                if m:
                    prev_word, prev = m.group(0), js[m.end() - 1]
                    i = m.end()
                    continue
            prev, prev_word = ch, ""
        i += 1
    return "".join(out)


_DELIM_CLOSERS = {"}": "{", ")": "(", "]": "["}


def _delimiter_fault(js: str) -> str | None:
    """The first structural `{}`/`()`/`[]` fault in `js`, as a line
    reference, or None if every delimiter balances.

    Runs on `_mask_js_literals`' output, which keeps offsets and newlines, so
    a brace inside a string or a comment can't shift the walk and a reported
    line number is the real one. Not a parser: it answers exactly the
    question V8's "Unexpected end of input" refuses to — *which* opener never
    closed, and where."""
    stack: list[tuple[str, int]] = []
    for i, ch in enumerate(js):
        if ch in "{([":
            stack.append((ch, i))
        elif ch in _DELIM_CLOSERS:
            want = _DELIM_CLOSERS[ch]
            if not stack:
                return (f"line {js.count(chr(10), 0, i) + 1}: '{ch}' closes "
                        "nothing — every opener before it is already closed")
            opener, at = stack.pop()
            if opener != want:
                return (f"line {js.count(chr(10), 0, i) + 1}: '{ch}' does not "
                        f"match the '{opener}' opened at line "
                        f"{js.count(chr(10), 0, at) + 1}")
    if stack:
        # Report the whole remaining stack, outermost first. One missing
        # closer leaves exactly one entry; several entries usually means one
        # opener swallowed the rest of the file.
        parts = [f"'{ch}' opened at line {js.count(chr(10), 0, at) + 1}"
                 for ch, at in stack[:5]]
        more = "" if len(stack) <= 5 else f" (+{len(stack) - 5} more)"
        return (f"{', '.join(parts)}{more} — never closed; the file ends "
                "inside it")
    return None


def _locate_syntax_faults(game_dir) -> str:
    """Point at the unbalanced delimiter behind a failed verification.

    The smoke test loads the *built* index.html and reports whatever Chromium
    says, which for a parse error is "pageerror: Unexpected end of input" —
    no file, no line, across every inlined module at once. Job 0cf766d0
    (2026-07-28) spent all three of its verification retries and ~4.5M tokens
    guessing at that string, re-reading a 118KB render.js three times, and
    died with 30 good edits discarded; it never noticed that the message
    appearing *twice* meant two separate modules were broken, since each
    module is inlined as its own <script> and so parses independently.

    Returns a note naming each faulty file and line, or "" when everything
    balances (a ReferenceError, a blocked host, a genuine gameplay crash) —
    so the caller can append it unconditionally."""
    src_dir = Path(game_dir) / "src"
    if not src_dir.is_dir():
        return ""                       # single-file game: nothing to attribute
    findings: list[str] = []
    for path in sorted(src_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in (".js", ".html"):
            continue
        rel = path.relative_to(src_dir).as_posix()
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if path.suffix.lower() == ".js":
            blocks = [(text, 0)]
        else:
            # Only the inline <script> bodies of a shell file are JS. Carry
            # each block's line offset so the number quoted is the one the
            # model will see when it reads the file.
            blocks = [(m.group(1), text.count("\n", 0, m.start(1)))
                      for m in _INLINE_SCRIPT_RE.finditer(text)]
        for body, line_offset in blocks:
            fault = _delimiter_fault(_mask_js_literals(body))
            if fault is None:
                continue
            if line_offset:
                fault = re.sub(r"line (\d+)",
                               lambda m: f"line {int(m.group(1)) + line_offset}",
                               fault)
            findings.append(f"  {rel} {fault}")
            break                        # one fault per file is enough to act on
    if not findings:
        return ""
    head = ("Unbalanced delimiters found in the source (this is almost "
            "certainly the parse error above — fix these, then call finish "
            "again):" if len(findings) > 1 else
            "Unbalanced delimiter found in the source (this is almost "
            "certainly the parse error above — fix it, then call finish "
            "again):")
    # Name the tool that acts on a line number. This message's whole value is
    # the line it reports, and until read_file took a range there was no way
    # to look at that line: job b7c3215e (2026-07-29) was handed "render.js
    # '{' opened at line 19 — never closed", had only whole-file reads and
    # regex search, and spent 17 of its 33 turns and two 57,000-token re-reads
    # of a 151KB module finding one brace. Same rule as _edit_file's
    # zero-match rejection pointing at search: a gate that demands a fix has
    # to name the tool that delivers it.
    tail = ("\nread_file(path, start_line=..., end_line=...) around the "
            "reported line is the cheap way to see it — a whole-file read is "
            "not needed to find a brace.")
    return head + "\n" + "\n".join(findings) + tail


def _skip_back(js: str, j: int) -> int:
    while j >= 0 and js[j].isspace():
        j -= 1
    return j


def _word_before(js: str, j: int) -> tuple[str, int]:
    """The identifier ending at `js[j]` and the index it starts at."""
    start = j
    while start >= 0 and (js[start].isalnum() or js[start] in "_$"):
        start -= 1
    return js[start + 1:j + 1], start + 1


def _matching_open_paren(js: str, close: int) -> int:
    depth = 0
    j = close
    while j >= 0:
        if js[j] == ")":
            depth += 1
        elif js[j] == "(":
            depth -= 1
            if depth == 0:
                return j
        j -= 1
    return -1


# Anything a function head can legally follow and still be an *expression* —
# `(function () {`, `!function () {`, `cb = function () {`. A head at
# statement position (after `;`, `}`, `{` or the start of the script) is a
# function declaration, which cannot be immediately invoked no matter what
# follows its closing brace.
_EXPRESSION_PRECEDERS = frozenset("(!=,:[&|?+-*/%^~<>")


def _function_body_head(js: str, brace: int) -> tuple[str, bool] | None:
    """If the `{` at `brace` opens a function body, return
    (parameter-list text, is-a-function-expression); otherwise None. `js` must
    already be literal-masked. Read backwards, because that is the only
    direction where `function f(a) {`, `(a, b) => {`, `x => {` and the
    object-method shorthand `draw(ctx) {` all look the same — and where
    `if (x) {` reliably doesn't."""
    j = _skip_back(js, brace - 1)
    if j < 0:
        return None
    if j >= 1 and js[j] == ">" and js[j - 1] == "=":            # ... => {
        j = _skip_back(js, j - 2)
        if j >= 0 and js[j] == ")":
            open_paren = _matching_open_paren(js, j)
            if open_paren < 0:
                return None
            params, head = js[open_paren + 1:j], open_paren
        else:
            params, head = _word_before(js, j)
            head = head if params else j
    elif js[j] == ")":
        open_paren = _matching_open_paren(js, j)
        if open_paren < 0:
            return None
        params = js[open_paren + 1:j]
        k = _skip_back(js, open_paren - 1)
        word, word_start = _word_before(js, k)
        if word in _CONTROL_HEADS:
            return None
        head = word_start if word else k + 1
        if word and word != "function":                          # function f(
            k = _skip_back(js, word_start - 1)
            kw, kw_start = _word_before(js, k)
            if kw == "function":
                head = kw_start
    else:
        return None
    prev = _skip_back(js, head - 1)
    is_expression = prev >= 0 and js[prev] in _EXPRESSION_PRECEDERS
    return params, is_expression


def _is_invoked_at(js: str, close_brace: int) -> bool:
    """Does the function body closing at `close_brace` get called right
    there — `(function () { … })()`, `(() => { … })()`, `!function () { … }()`?
    Such a body holds the whole program's names, which is what dropping the
    wrapper hoists into global scope, so it counts as top level. An ordinary
    `function fire() { … }` body, or a `setTimeout(function () { … }, 5)`
    callback, holds locals nobody outside can see."""
    n = len(js)
    j = close_brace + 1
    while j < n and js[j].isspace():
        j += 1
    if j < n and js[j] == ")":
        j += 1
        while j < n and js[j].isspace():
            j += 1
    return j < n and js[j] == "("


class _Scopes:
    """What a program declares, split by scope, so the parity gate can tell a
    global apart from a local.

    `top_level` — names visible to the whole program: declared at a script's
    top level, or inside an IIFE body (the original's names all live there,
    and dropping that wrapper is what makes them real globals).
    `bound` — every name bound anywhere at any depth, parameters included.
    `redeclared_functions` — top-level function/class names declared more than
    once, i.e. one module's copy silently overriding another's.
    """

    __slots__ = ("top_level", "bound", "redeclared_functions")

    def __init__(self, top_level: set[str], bound: set[str],
                 redeclared_functions: list[str] | None = None):
        self.top_level = top_level
        self.bound = bound
        self.redeclared_functions = redeclared_functions or []


def _scan_scopes(html: str) -> _Scopes:
    """Scope-aware inventory of the inline <script> blocks of `html`.

    Still not a JS parser — it masks literals, then walks braces classifying
    each one as a block, an IIFE body (top level continues inside it) or an
    ordinary function body (everything inside is local). That is the whole
    distinction the gate needs, and the one a bare regex over the file does
    not make: the flat version of this counted `for (const it of inRange)` and
    a `let target = …` inside a function as declarations, so a split that
    legitimately re-expressed one of those locals — a `let target` becoming an
    `applyDamage(target, …)` parameter, a `for (const it of …)` becoming a
    `.forEach(it => …)` — read as "the original declared it, the split
    doesn't", with the surviving locals counted as broken references. That
    unsatisfiable failure burned all three attempts of a real Tower Maze
    Defense explode (2026-07-26, `ct`/`it`/`target`); there is nothing the
    model can write to make a local look declared at file scope.
    """
    js = _mask_js_literals(_inline_script(html))
    frames: list[tuple[int, bool]] = []   # (open brace idx, is function body)
    # open idx -> (params, is a function expression, enclosing function frames)
    functions: dict[int, tuple[str, bool, tuple[int, ...]]] = {}
    closes: dict[int, int] = {}
    decls: list[tuple[str, str, tuple[int, ...]]] = []   # name, kw, fn frames

    for m in re.finditer(
            r"[{}]|\b(?:function|const|let|var|class)\s+[A-Za-z_$][\w$]*", js):
        tok = m.group(0)
        if tok == "{":
            head = _function_body_head(js, m.start())
            if head is not None:
                functions[m.start()] = (
                    *head, tuple(idx for idx, is_fn in frames if is_fn))
            frames.append((m.start(), head is not None))
        elif tok == "}":
            if frames:
                open_idx, _ = frames.pop()
                closes[open_idx] = m.start()
        else:
            decls.append((_DECL_RE.match(tok).group(1), tok.split()[0],
                          tuple(idx for idx, is_fn in frames if is_fn)))

    def transparent(open_idx: int) -> bool:
        """Is this function body one whose names the split will hoist into
        global scope — i.e. an IIFE wrapper rather than an ordinary function?"""
        _params, is_expression, _enclosing = functions[open_idx]
        close = closes.get(open_idx)
        return (is_expression and close is not None
                and _is_invoked_at(js, close))

    bound = {name for name, _kw, _stack in decls}
    for params, _is_expression, _enclosing in functions.values():
        bound.update(_IDENT_RE.findall(params))

    def at_top_level(promoted: int | None = None) -> list[tuple[str, str]]:
        return [(name, kw) for name, kw, stack in decls
                if all(idx == promoted or transparent(idx) for idx in stack)]

    scoped = at_top_level()
    outermost = [idx for idx, (_p, _e, enclosing) in functions.items()
                 if not enclosing]
    if not scoped and len(outermost) == 1:
        # A program written as one `window.onload = function () { … }` or a
        # single DOMContentLoaded callback has no top level of its own.
        # Promoting that lone wrapper keeps the gate from quietly degrading
        # into a no-op on such a game — worse than a false positive, because
        # nothing reports it.
        scoped = at_top_level(promoted=outermost[0])

    counts = Counter(name for name, kw in scoped if kw in ("function", "class"))
    return _Scopes({name for name, _kw in scoped}, bound,
                   sorted(n for n, c in counts.items() if c > 1))


def _declared_names(html: str) -> set[str]:
    """The names of `html`'s program that a split turns into real globals —
    see _scan_scopes for why this is scope-aware and not a flat regex."""
    return _scan_scopes(html).top_level


def _missing_declarations(source_html: str, built_html: str) -> list[str]:
    """Top-level names of the original that the split binds nowhere at all.
    Measured against every binding in the built result, at any depth: a name
    that reappears as a parameter or a local is a name whose references this
    check cannot honestly call broken."""
    return sorted(_declared_names(source_html) - _scan_scopes(built_html).bound)


def _reference_count(name: str, html: str) -> int:
    """How many times `name` appears as a bare identifier in the inline
    scripts of `html`. Member accesses (`camera.screenX`) and object-literal
    keys (`{ screenX: 1 }`) don't count — neither one refers to the global
    that a rename moved away, and counting either would fail a legitimate
    rename, which is the failure this whole check exists to stop doing. The
    same exclusion does drop the rare `cond ? screenX : y`; that direction
    only ever costs a missed catch, not a false accusation. Occurrences in
    strings and comments are masked out, so a name that only survives in a
    log message isn't reported as a live call site."""
    script = _mask_js_literals(_inline_script(html))
    return len(re.findall(
        rf"(?<![.\w$]){re.escape(name)}(?![\w$])(?!\s*:)", script))


def _declaration_parity(source_html: str,
                        built_html: str) -> tuple[list[str], list[str]]:
    """Sort the names the built result no longer declares into the only two
    cases that differ in consequence:

    `broken` — the name is undeclared but the program still references it.
    That is the real defect: those call sites now bind to a browser built-in
    or to nothing at all.

    `vanished` — the name is gone from the program entirely, declaration and
    references alike. That is exactly what a correct, consistent RENAME looks
    like from the outside, and it is what the explode prompt demands for a
    name colliding with a Window built-in, so it cannot be treated as a
    failure on its own.
    """
    missing = _missing_declarations(source_html, built_html)
    broken = [n for n in missing if _reference_count(n, built_html)]
    vanished = [n for n in missing if n not in broken]
    return broken, vanished


def _fmt_name_list(names: list[str], limit: int = 20) -> str:
    return ", ".join(names[:limit]) + ("…" if len(names) > limit else "")


def _explode_declaration_check(source_html: str):
    """An extra explode-only verification gate: no name the single-file
    original declared may end up referenced-but-undeclared in the split, and
    no name may disappear without a replacement taking its place.

    This exists because build->scan->smoke cannot catch the worst outcome
    this pass has. Splitting a game whose whole program sat in one IIFE
    moves its names into global scope, where some of them collide with
    read-only Window built-ins — `screenX`, `screenY`, `name`, `status`,
    `length`, `top`, `closed`, `origin`, and friends. The Darkhold pilot hit
    exactly that, resolved the collision by DELETING
    `function screenX(wx) {...}` and `function screenY(wy) {...}`, and still
    passed every gate: `screenX` kept resolving — to the built-in number —
    so the 22 surviving call sites raise TypeError at call time rather than
    ReferenceError at load, and a page-load smoke test never runs world
    rendering. The result was a green build and a game that breaks the
    instant you start playing.

    Why this is a reference check and not a set difference over declared
    names, which is what it was first: the prompt's own remedy for such a
    collision is to RENAME the declaration and every call site, and a
    successful rename removes the original name from the declaration set.
    A plain set difference therefore failed the fix it had just asked for,
    with a message repeating the demand — an unsatisfiable loop that burned
    every verification attempt on two real Sorcerer With A Minigun explodes
    (2026-07-26) without the model ever being able to comply. What actually
    distinguishes the delete from the rename is the call sites: deleting
    `screenX` leaves 22 of them behind, renaming it leaves none. So the gate
    fails on names that are still referenced, and separately on names that
    vanish with nothing new declared to stand in for them (which is the
    other real defect — code dropped to fit the module ceiling).

    Every name here is a TOP-LEVEL name — see _scan_scopes. Policing locals
    too is what made the gate unsatisfiable a second time, on the Tower Maze
    Defense explode.

    The third thing it fails is the mirror image of a dropped declaration: the
    same top-level function declared in two modules at once, which is what a
    split does when it copies a function instead of moving it. The later copy
    silently wins, so if the two bodies differ the built game runs code the
    original never did — and nothing else notices, since the name is present
    and every call site resolves. That happened in the same Tower Maze run
    (`gameOver` in both combat.js and main.js, `checkEnemyDeaths` in both
    enemies.js and combat.js).
    """
    if not _declared_names(source_html):
        # Every scope in this source read as local (several top-level callbacks,
        # say, with nothing promotable). The gate still runs, but it has nothing
        # to compare — say so, because a check that quietly passes everything
        # is indistinguishable from a check that works.
        _logger.warning(
            "explode parity check found no top-level declarations in the "
            "source game: it cannot detect dropped or stranded names for this "
            "split")

    def check(game_dir, built_html):
        broken, vanished = _declaration_parity(source_html, built_html)
        problems = []
        duplicated = _scan_scopes(built_html).redeclared_functions
        if duplicated:
            problems.append(
                f"{len(duplicated)} function/class name(s) are declared at the "
                "top level of more than one module, so the last copy loaded "
                f"silently replaces the earlier one: {_fmt_name_list(duplicated)}. "
                "Each one must be declared exactly once across src/ — keep it "
                "in the module it belongs to and delete the other copy, "
                "leaving the call sites alone (they resolve across modules "
                "either way)."
            )
        if broken:
            sites = ", ".join(
                f"{n} ({_reference_count(n, built_html)} reference(s) left)"
                for n in broken[:20]
            ) + ("…" if len(broken) > 20 else "")
            problems.append(
                f"{len(broken)} name(s) the original declared are no longer "
                "declared anywhere in src/, while the built game still "
                f"references them: {sites}. Restore each one as a real "
                "declaration. If you renamed it to dodge a browser-global "
                "collision (screenX, screenY, name, status, length, top, …), "
                "that was right — but you missed those references, so rename "
                "them to match. If you deleted it, put it back under a "
                "non-colliding name and update every reference. Do not leave "
                "the call sites bound to the built-in: `screenX` still "
                "resolves, to a number, so nothing fails on load and it "
                "throws only once that code runs."
            )
        if vanished:
            # A rename replaces one name with another, so the built result
            # declares something the original didn't. Nothing new at all means
            # the code went away rather than moved — the drop-to-fit failure.
            replacements = _declared_names(built_html) - _declared_names(source_html)
            if len(replacements) < len(vanished):
                problems.append(
                    f"{len(vanished)} name(s) the original declared are gone "
                    "from the split entirely, with no new declaration to "
                    f"replace them: {_fmt_name_list(vanished)}. Every "
                    "function/const/let/class in the original must still be "
                    "declared exactly once across src/ — under its own name, "
                    "or under a new one applied consistently at the "
                    "declaration and every reference. Never omit code to fit "
                    "the module size ceiling; split the module instead."
                )
        return " ".join(problems) or None
    return check


def _explode_target_module_count(source_bytes: int) -> int:
    """Roughly how many JS modules a source of this size should split into.
    Deliberately a range-anchor for the prompt, not a rule the loop
    enforces — the enforced number is the byte ceiling."""
    return max(3, round(source_bytes / EXPLODE_TARGET_MODULE_BYTES))


def _build_explode_system_prompt(source_title: str, source_html: str,
                                  max_module_bytes: int = DEFAULT_EXPLODE_MAX_MODULE_BYTES,
                                  engine: str | None = None) -> str:
    """Sprint 5's explode pass (docs/multifile-agent/05-migration-and-pilot.md
    Part A): the model is handed the ENTIRE original single-file game as
    plain input context (reading it costs input tokens only, never subject
    to the output-token ceiling that motivates this whole initiative) and
    re-emits it split across several bounded write_file calls instead of
    one whole-game submission — the same trick that lets explode work even
    on a game already too large to ever be resubmitted whole."""
    allowed_hosts = ", ".join(sorted(safety.ALLOWED_CDN_HOSTS))
    is_three = engine == engines.ENGINE_THREE
    three_section = engines.three_contract(multi_file=True) if is_three else ""
    return (
        f"You are converting an existing single-file browser game, "
        f"currently titled '{source_title}', into this arcade's multi-file "
        "format. This produces a NEW game entry — the original single-file "
        "game is left completely untouched.\n\n"
        + three_section
        + "## Task\n"
        "Split the single index.html below into a shell src/index.html "
        "plus src/style.css and SEVERAL cohesive src/*.js modules, and "
        "author game.md (a prose description of the game plus a table of "
        "every src/ file and its purpose). This MUST be behavior-preserving: "
        "every mechanic, visual, and control must work identically to the "
        "original — you are re-organizing the code, not rewriting the "
        "game, adding features, or fixing bugs you happen to notice.\n\n"
        "## How many modules\n"
        f"This game's source is {len(source_html):,} bytes. Split its "
        f"JavaScript across roughly {_explode_target_module_count(len(source_html))} "
        f"modules, none larger than {max_module_bytes:,} bytes — that ceiling "
        "is enforced, and an oversized write_file is rejected. Divide along "
        "the seams the code already has, one module per subsystem: "
        "constants/config, entity definitions, world or level generation, "
        "combat, input handling, the update loop, rendering, UI/HUD, and a "
        "small entry point that wires them together are typical. Name each "
        "file for what it holds.\n\n"
        "Putting all the logic into one big module DEFEATS THE ENTIRE "
        "PURPOSE of this conversion. The point is that a later edit only "
        "has to read and rewrite the one module it touches; a single "
        "large module leaves that edit exactly as expensive as it was "
        "before the split.\n\n"
        "Use write_file(path, contents) for every file you create — the "
        "src/ shell as write_file(\"index.html\", ...), the stylesheet as "
        "write_file(\"style.css\", ...), each logic module under its own "
        "descriptive name (write_file(\"entities.js\", ...), "
        "write_file(\"render.js\", ...), write_file(\"input.js\", ...), and "
        "so on), and the map as write_file(\"game.md\", ...). Paths are already rooted "
        "at src/, so write bare filenames and never a \"src/\" prefix; "
        "src/index.html must likewise reference its siblings by bare "
        "filename, one <script> tag per module in dependency order "
        "(<link rel=\"stylesheet\" href=\"style.css\">, then "
        "<script src=\"entities.js\"></script>"
        "<script src=\"render.js\"></script> and so on), never "
        "\"src/style.css\". You may use "
        "list_files()/read_file(path) to check back on files you've "
        "already written, and search(pattern) to grep every module you've "
        "written so far — that is much the cheaper way to check whether a "
        "name is declared, declared twice, or referenced with no "
        "declaration, which is exactly what verification checks. To fix a "
        "small mistake in a module you have already written — a duplicated "
        "declaration, a name that needs renaming, a missing <script> tag — "
        "use edit_file(path, old_string, new_string) rather than rewriting "
        "the whole module: it replaces one exactly-once occurrence and costs "
        "only the text you pass. Call "
        "finish(summary) only once EVERY file "
        "(including src/index.html and game.md) has been written — it "
        "triggers a build + safety scan + smoke test of the assembled "
        "result; a failure comes back as this call's result so you can "
        "keep editing and call finish again.\n\n"
        "## One shared scope — read this before you split anything\n"
        + (
            "The build concatenates your modules into ONE "
            "<script type=\"module\"> in the order src/index.html lists them, "
            "under the import header shown above. There is NO per-file scope: "
            "every module's top-level declarations land in one shared module "
            "scope. That makes these rules non-negotiable.\n"
            if is_three else
            "The build concatenates your modules into sibling <script> blocks in "
            "the order src/index.html lists them. There is no module system, no "
            "import/export, and NO per-file scope: every module's top-level "
            "declarations land in one shared global scope. That makes four rules "
            "non-negotiable.\n"
        )
        + "1. If the original wraps its whole program in one IIFE — "
        "`(function () { ... })();` around everything — DELETE that single "
        "outer wrapper and distribute its body across your modules "
        "unchanged. Do not give each module its own IIFE: names declared "
        "inside one are invisible to every other module, which is the single "
        "most common way this conversion fails.\n"
        "2. Declare every identifier EXACTLY ONCE across all modules "
        "combined. The same const/let/class name in two files is a fatal "
        "redeclaration error that kills the entire game on load — it is not "
        "a per-file shadow. If two subsystems both need a constant, it "
        "belongs in exactly one module. A duplicated `function` is the "
        "quieter version of the same mistake: two copies don't error, the "
        "later one just silently replaces the earlier, so if they ever drift "
        "the game runs code the original never had. MOVE each function into "
        "the one module it belongs to; never copy it into a second. Calls "
        "reach it from any module either way. Verification checks this.\n"
        "3. Never invent `window.foo = foo` bridges to pass things between "
        "modules. Top-level const/let/class bindings never become window "
        "properties, so `window.foo` reads undefined even where bare `foo` "
        "works fine. Modules share a scope already — just use the bare name.\n"
        "4. Order the <script> tags so anything executed at load time comes "
        "after what it reads. Functions may call across modules freely once "
        "everything has loaded, but a top-level const/let must be declared "
        "before the first module that reads it while loading. Put the entry "
        "point — the module that actually starts the game — last.\n"
        + (
            # Module scope is not global scope, so the Window-collision rule
            # that rule 5 exists for cannot arise here: a top-level `const
            # name` in a module never touches window.name.
            "5. The merged module's scope is NOT the global scope, so a "
            "top-level declaration never collides with a Window built-in "
            "(screenX, name, status, length, top, …). Keep the original "
            "spelling; there is nothing to rename.\n\n"
            if is_three else
            "5. Dropping the outer IIFE puts every name into global scope, where "
            "a few collide with read-only Window built-ins: screenX, screenY, "
            "name, status, length, top, self, closed, origin, history, location, "
            "focus, close, open, event. If the original declares one of those, "
            "RENAME it (e.g. screenX -> toScreenX) at its declaration AND at "
            "every call site. Never resolve such a collision by deleting the "
            "declaration: the name still resolves — to the built-in — so nothing "
            "fails on load and the game breaks only once that code actually "
            "runs. Renaming is fully expected here and verification allows it — "
            "it checks that no name is left referenced without a declaration, "
            "not that the original spelling survived.\n\n"
        )
        + "## Never drop code to fit\n"
        "This conversion is behavior-preserving, and the module ceiling is "
        "never a reason to omit anything. If a subsystem's code exceeds the "
        "ceiling, SPLIT it across two modules (render_world.js + "
        "render_ui.js) — never abbreviate it, summarize it, stub it, or "
        "leave a function out to make the file fit. Every function and "
        "constant in the original must appear exactly once across your "
        "modules — under its own name, or under a rename applied "
        "consistently at the declaration and every reference. The split is "
        "checked for this: code that simply disappears fails "
        "verification.\n\n"
        "## Contract\n"
        "All HTML/CSS/JS stays inline within the src/ files (no separate "
        "asset files). You may load external JavaScript modules or "
        "stylesheets via <script>/<link> tags ONLY from these CDN hosts: "
        f"{allowed_hosts}. Do not reference any other external host, and do "
        "not attempt any network calls back to this site or anywhere else "
        "at runtime.\n\n"
        "## Sandbox constraints\n"
        "The game is played inside a sandboxed <iframe> with no "
        "same-origin access: document.cookie, localStorage, sessionStorage, "
        "indexedDB, and window.parent/window.top are all unavailable — keep "
        "all game state in ordinary JavaScript variables. Do not use "
        "eval() or `new Function(...)`.\n\n"
        "## Original single-file game\n"
        f"```html\n{source_html}\n```\n"
    )


def _build_explode_user_prompt() -> str:
    return (
        "Split this game into the multi-file format described above, across "
        "several cohesive modules rather than one large one. Do "
        "not add, remove, or change any feature, visual, or control — the "
        "played game must behave exactly as it does now."
    )


# ---------------------------------------------------------------------------
# The ReAct loop
# ---------------------------------------------------------------------------

def _compact_write_calls(messages: list[dict], assistant_msg: dict,
                          records: list[tuple[str, str, str | None, str]],
                          snapshot_paths: frozenset | None = None,
                          modified_paths: set[str] | None = None) -> None:
    """Drop executed write_file calls out of the conversation entirely,
    replacing them with a short plain-text note on the assistant message
    that made them.

    `records` are (call_id, tool_name, path, observation) for the calls to
    compact — every write_file this turn, plus any edit_file whose strings
    were large enough to be a whole-module rewrite in disguise (see
    DEFAULT_EDIT_COMPACT_BYTES). Ordinary small edits are deliberately NOT
    passed in: they stay in history with their real arguments, which is what
    keeps "the file is the snapshot's version with my edits applied" a
    statement the model can verify from the transcript rather than take on
    faith.

    Why they must go: a write_file call's own arguments carry the COMPLETE
    new file contents (potentially tens of KB) inside the assistant message
    that requested it. `ask_with_tools()` is stateless, so anything left in
    `messages` is resent on every subsequent turn for the rest of the run —
    the Sprint 5 pilot measured this dominating the agent path's
    5-12x-larger-than-baseline input token cost, well ahead of stale reads.
    The model already generated that content, and the observation (byte
    count, success/rejection) is preserved in the note; read_file is there
    if it ever needs the current bytes again.

    Why they're REMOVED rather than squashed in place: Sprint 6's first
    attempt kept the tool call and only replaced its "contents" argument
    with a short placeholder string. That placeholder was ~113 bytes, and
    it sat in history paired with a tool result reading "OK: wrote 4840
    bytes" — which taught the model, by example from its own prior turns,
    that a ~113-byte placeholder-shaped "contents" is a legitimate call
    that produces a multi-KB file. Real pilot runs then had it emitting
    exactly that: ~113-120 byte stub writes for modules meant to be several
    KB, whose contents were the placeholder text itself, in a
    self-reinforcing loop that burned 1-2.6M input tokens and shipped
    nothing. (The same run's "confabulated" reasoning — the model insisting
    a tool result said "wrote 4840 bytes" when it plainly said 114 — was
    not confabulation at all: it had read the stub file back, and the
    stub's contents literally contained that sentence.) See
    docs/multifile-agent/05-migration-and-pilot.md. An earlier variant of
    the same placeholder, which also dropped the "path" key, taught the
    model to omit "path" the same way. Both are the identical failure mode:
    anything left in the arguments slot is read as an example of what a
    valid call looks like. The only safe amount of fake tool-call arguments
    to leave in history is none.

    Removing a tool call means also removing its tool-result message, or
    the API's assistant-tool_calls/tool-result pairing breaks. Both happen
    here. An assistant message left with no tool calls at all keeps the
    note as its `content`, which is a perfectly ordinary assistant turn.
    Mutates `messages` and `assistant_msg` in place; a no-op when there are
    no write calls to compact."""
    if not records:
        return
    ids = {call_id for call_id, _, _, _ in records}
    remaining = [e for e in (assistant_msg.get("tool_calls") or []) if e.get("id") not in ids]
    if remaining:
        assistant_msg["tool_calls"] = remaining
    else:
        assistant_msg.pop("tool_calls", None)

    # Wording matters more than it looks. The first version said the calls
    # "were dropped from the conversation to save context", and the Sprint 6
    # step 2 pilot showed the model reading that as "my writes did not take
    # effect" — three separate "it seems like my write_file calls are being
    # dropped" reasoning turns, each kicking off a list_files/read_file sweep
    # to re-check state. That run spent 38 of its 40 turns on exploration and
    # ran out of budget before ever calling finish. So: lead with the write
    # having SUCCEEDED, and be explicit that only the bulky argument was
    # trimmed and that the file is on disk.
    tool_names = sorted({name for _, name, _, _ in records})
    # "write_file" alone keeps the original wording byte for byte — explode
    # never emits an edit large enough to be compacted, so its note is
    # unchanged and stays a cache hit against every run before this one.
    label = " / ".join(tool_names)
    trimmed = ("Only the bulky 'contents' argument has been trimmed"
               if tool_names == ["write_file"]
               else "Only the bulky arguments have been trimmed")
    note = (
        f"{_PRUNE_SENTINEL} The {label} call(s) below COMPLETED SUCCESSFULLY and "
        f"the files are on disk. {trimmed} "
        "from this transcript, to save context. Results: "
        + "; ".join(observation for _, _, _, observation in records)
        + ". There is no need to re-write or re-check these files; call read_file "
          "only if you need to see their current contents again."
    )
    # Say which snapshot files this run has superseded, and — the load-bearing
    # half — that every other one is still exactly as the snapshot shows it.
    # Without that second sentence the model generalises one stale file into a
    # stale snapshot and re-reads the whole game, which is the cost this
    # sprint exists to remove. The list is every file modified so far, not
    # just this turn's, so "every other file" is literally true.
    superseded = sorted(
        (modified_paths or set()) & (snapshot_paths or frozenset())
    )
    if superseded:
        note += (
            " Note on the source snapshot in your instructions: it shows "
            + ", ".join(superseded)
            + " as "
            + ("it was" if len(superseded) == 1 else "they were")
            + " before your changes, so for "
            + ("that file" if len(superseded) == 1 else "those files")
            + " your edits above are what is on disk now. Every other file in "
              "the snapshot is still exactly as shown there."
        )
    existing = (assistant_msg.get("content") or "").strip()
    assistant_msg["content"] = f"{existing}\n{note}" if existing else note

    messages[:] = [
        m for m in messages
        if not (m.get("role") == "tool" and m.get("tool_call_id") in ids)
    ]


def _await_step_approval(*, job_id: str, db_conn, emit: Callable | None,
                          extra_steps: int, timeout_seconds: float,
                          step_budget: int, wrote_anything: bool,
                          ) -> tuple[int, bool]:
    """Pause the run at its step ceiling and ask the requester for more turns.

    Returns (steps_granted, cancelled). A grant of 0 means the user said stop,
    the wait timed out, or the job was cancelled — the caller ends the loop
    either way, and only `cancelled` changes what happens next (a cancel skips
    the forced final verification, exactly as the mid-run cancel check does).

    Blocking a worker thread is not something this codebase does lightly: the
    paused job holds the one 'generating' slot claim_next_queued_request
    allows site-wide, so the whole queue waits with it. That is why the wait is
    bounded, why cancel is checked on every poll rather than only at the end,
    and why the timeout path is byte-for-byte the behaviour that existed before
    this function did.

    The waiting flag is cleared on every exit, including the exceptional one —
    a job that finished while still advertising a live prompt would leave the
    UI offering buttons that can never be answered.
    """
    minutes = max(1, int(round(timeout_seconds / 60)))
    written = ("Everything it has written so far is on disk and will be "
               "verified either way." if wrote_anything
               else "It has not written any files yet.")
    if not db.request_step_approval(job_id, conn=db_conn):
        # The UPDATE matched no row, so there is no job to poll and no status
        # page attached to one — blocking would burn the whole timeout waiting
        # for an answer that cannot arrive. Emit nothing and behave exactly as
        # a run with job_id=None does. This has to come from the UPDATE's own
        # rowcount: re-reading awaiting_approval_at instead loses a grant that
        # lands in between, because answering clears that column too.
        return 0, False
    _safe_emit(
        emit, "approval_request",
        f"The agent has used all {step_budget} of its turns without calling "
        f"finish. {written} Read the transcript above: if it still looks like "
        f"it is making progress, you can give it {extra_steps} more turns. "
        f"Waiting up to {minutes} minute(s) — with no answer, it verifies and "
        "ships whatever is written now.",
        {"step": step_budget, "extra_steps": extra_steps,
         "timeout_seconds": timeout_seconds, "wrote_anything": wrote_anything},
    )

    deadline = time.monotonic() + timeout_seconds
    granted, cancelled, outcome = 0, False, "timeout"
    try:
        while True:
            if db.is_job_cancelled(job_id, conn=db_conn):
                cancelled, outcome = True, "cancelled"
                break
            _, decision = db.get_step_approval(job_id, conn=db_conn)
            if decision is not None:
                granted = max(0, int(decision))
                outcome = "granted" if granted else "declined"
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(_APPROVAL_POLL_SECONDS, remaining))
    finally:
        db.clear_step_approval(job_id, conn=db_conn)

    message = {
        "granted": f"You granted {granted} more turns.",
        "declined": "You chose to stop here and ship what is already written.",
        "timeout": (f"No answer within {minutes} minute(s) — verifying and "
                    "shipping whatever is written now."),
        "cancelled": "Job cancelled while waiting.",
    }[outcome]
    _safe_emit(emit, "approval_result", message,
               {"outcome": outcome, "extra_steps": granted})
    return granted, cancelled


def _run_react_loop(*, game_dir: Path, system_prompt: str, user_prompt: str,
                     cfg: dict, job_id: str | None, db_conn, emit: Callable | None = None,
                     extra_verify: Callable | None = None,
                     snapshot_paths: frozenset | None = None,
                     engine: str | None = None,
                     engine_version: str | None = None) -> dict:
    """Drive the read_map/list_files/read_file/search/write_file/finish loop
    against game_dir until finish() passes build->scan->smoke, the
    verification-retry budget is exhausted, or the step budget runs out.

    A run that ends any other way than a passing finish() still gets one
    forced verification of whatever it wrote (see the end of this function),
    so a stalled-but-correct run ships instead of being discarded.
    game_dir is either an already-staged copy of a multi-file source's
    src/+game.md (enhance_multifile_game) or an empty directory the model
    populates from scratch via write_file (explode_game, Sprint 5) —
    `_write_file`/`builder.is_multi_file`'s src/index.html check both cope
    with the latter starting out empty.

    Returns a dict: success/summary/attempts/input_tokens/output_tokens/
    cached_tokens/tokens_used/model/effort/error. Does not touch games_dir bookkeeping,
    the DB registry, or rollback — the caller (enhance_multifile_game,
    explode_game) owns all of that, same division of labor as
    game_generator.run_generation_attempts() vs. its callers.

    `emit(role, content=None, data=None)` (Sprint 3) is called at every
    think/act/observe/verify step for the durable agent_events transcript,
    plus a 'usage' event per LLM call carrying that call's own token counts
    and the run's running totals (the job row only gets a total at the very
    end, and generation_attempts only gets a row per finish() verification,
    so 'usage' is the only per-call accounting there is). Defaults to a
    DB-writing emitter keyed on job_id (a no-op if job_id is None). Every
    call is wrapped in _safe_emit so a raising emitter can't fail the run.

    Cache discipline (Sprint 6a — see
    docs/multifile-agent/06a-cache-snapshot-and-edits.md). The conversation
    is APPEND-ONLY: no message that has already been included in a request
    to the model is ever mutated or removed. DeepSeek's prefix cache is
    byte-exact and prefix-only, and it bills a resent cached token at
    1/120th of a fresh one (v4-pro), so retention is nearly free while
    MUTATION is what costs — editing a message at position k re-bills
    everything from k onward at full price, on that turn and every turn
    after it. Sprint 6 pruned stale read_file results for exactly the
    reason this inverts, and measurement showed each prune collapsing the
    cached prefix from ~44,000 tokens back to ~4,500. That pruning is gone.
    _compact_write_calls survives because it only ever touches the
    assistant message and tool results created in the CURRENT turn, which
    have not been sent yet — it shortens the suffix, never the prefix.

    The price of append-only is that the conversation only grows, so the
    context window becomes the run's real ceiling. The loop watches its own
    billed input size and lands the plane rather than letting the API 400
    mid-run: one nudge to stop exploring and finish past
    cfg["context_soft_limit_tokens"], then an orderly stop past 95% of
    ai.CONTEXT_WINDOW_TOKENS, which falls through to the forced final
    verification and ships whatever is already correct on disk.

    `extra_verify(game_dir, built_html) -> str | None` is an optional gate
    run only after build->scan->smoke has already passed; returning a string
    turns that finish() into a failure carrying it as the detail. explode
    uses it for the declaration-parity check (see
    _explode_declaration_check), which catches a whole class of breakage the
    standard gate structurally cannot. The loop itself stays unaware of
    which pass it drives.

    `snapshot_paths` is the set of files the caller already put into the
    system prompt in full (see _build_source_snapshot). The loop uses it for
    one thing only — telling the model that a read of an untouched file was
    redundant — and is otherwise unaware of the snapshot; explode passes
    nothing, since it starts from an empty directory.
    """
    # 40 was too tight for a real explode: Sprint 6 step 2's pilot split a
    # 159KB game into 12 modules and hit the cap having never once called
    # finish, so it verified nothing and shipped nothing despite every write
    # succeeding. There's no partial credit here — running out of steps
    # before finish() throws away the whole run — and the dominant fix is
    # cutting wasted exploration turns (see _compact_write_calls' note
    # wording and _normalize_agent_path), but the cap needs real headroom
    # above "one write per module" too.
    max_steps = cfg.get("max_steps", 60)
    extra_steps_on_approval = cfg.get(
        "extra_steps_on_approval", DEFAULT_EXTRA_STEPS_ON_APPROVAL
    )
    step_approval_timeout = cfg.get(
        "step_approval_timeout_seconds", DEFAULT_STEP_APPROVAL_TIMEOUT_SECONDS
    )
    max_verification_retries = cfg.get("max_verification_retries", 3)
    max_module_bytes = cfg.get("max_module_bytes", DEFAULT_MAX_MODULE_BYTES)
    module_warn_bytes = cfg.get(
        "module_warn_bytes", int(max_module_bytes * DEFAULT_MODULE_WARN_RATIO)
    )
    edit_compact_bytes = cfg.get("edit_compact_bytes", DEFAULT_EDIT_COMPACT_BYTES)
    context_soft_limit = cfg.get(
        "context_soft_limit_tokens", DEFAULT_CONTEXT_SOFT_LIMIT_TOKENS
    )
    context_hard_limit = int(ai.CONTEXT_WINDOW_TOKENS * _CONTEXT_HARD_LIMIT_RATIO)
    # Sprint 6a removed read-result pruning outright; a config still carrying
    # the key is stale, and saying so beats silently ignoring it. Warn rather
    # than fail: an out-of-date config.yaml must not stop a production run.
    if "context_prune_after_steps" in cfg:
        _logger.warning(
            "multifile_agent.context_prune_after_steps is obsolete and ignored — "
            "read results are no longer pruned, because mutating an already-sent "
            "message invalidates DeepSeek's prefix cache from that point on "
            "(see docs/multifile-agent/06a-cache-snapshot-and-edits.md). "
            "Remove the key from config.yaml."
        )
    # `or` rather than a get() default, so an explicitly blank model in a
    # config still lands on the agent's own default instead of falling
    # through to ai_client's app-wide one (see DEFAULT_AGENT_MODEL).
    model = cfg.get("model") or DEFAULT_AGENT_MODEL
    effort = cfg.get("effort", "high")
    # 1800s, matching config.yaml.example and the two single-file pipelines —
    # NOT ai_client's own 120s default. A config.yaml predating the
    # multifile_agent block (this repo's own did) silently got 120s here,
    # which is short enough to time out a thinking-mode turn emitting a large
    # write_file argument; real explode runs emit 100KB+ in a single call.
    ai_timeout = cfg.get("timeout_seconds", 1800)
    if emit is None:
        emit = _make_emitter(job_id, db_conn)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    total_input_tokens = 0
    total_output_tokens = 0
    total_cached_tokens = 0
    last_model = model or "default"
    last_effort = effort
    verification_attempts = 0
    tokens_at_last_attempt = (0, 0)
    consecutive_no_progress = 0
    wrote_anything = False
    written_paths: set[str] = set()
    seen_observations: set = set()
    nudged_to_finish = False
    nudged_to_write = False
    nudged_low_budget = False
    # The re-finish nudge (below) answers a different budget failure from
    # nudged_low_budget: not "you never verified" but "you called finish, it
    # failed, you fixed it, now re-verify instead of re-reading to check by
    # hand". edited_since_finish tracks whether any write has landed since the
    # last finish attempt; nudged_refinish is re-armed on every new finish so
    # each fix-cycle earns one nudge. Job d6ca3a88 (2026-07-27) fixed a TDZ
    # failure at ~step 54 of 60 and then spent its last six turns re-reading to
    # be sure rather than calling finish — the forced verification shipped it,
    # but a nudge would have converged it cleanly with steps to spare.
    edited_since_finish = False
    nudged_refinish = False
    # One-shot per failure window, so a "maybe it was transient" re-finish
    # costs a turn instead of one of only three verification attempts. See
    # the rejection below.
    rejected_noop_finish = False
    # The context guard reads the PREVIOUS call's billed input rather than
    # estimating the next one: ask_result.input_tokens is what the provider
    # actually charged for the whole message list, which is the only figure
    # that accounts for the system prompt, the snapshot, and every tool result
    # exactly as the provider counts them. Starts at 0 so the first turn is
    # never blocked.
    last_input_tokens = 0
    nudged_context = False
    # Mutable, because the user can extend it once at the ceiling (see
    # _await_step_approval). Everything inside the loop measures against
    # step_budget; max_steps is only the starting value and the one-shot
    # prompt's trigger point.
    step_budget = max_steps
    asked_for_more_steps = False
    finish_nudge_at = _finish_nudge_threshold(step_budget)
    summary = ""
    last_candidate_summary = ""
    error = None
    success = False
    cancelled = False

    def record_verification(outcome: str, detail: str | None) -> None:
        nonlocal tokens_at_last_attempt
        if job_id is not None:
            in_delta = total_input_tokens - tokens_at_last_attempt[0]
            out_delta = total_output_tokens - tokens_at_last_attempt[1]
            db.add_generation_attempt(
                job_id, verification_attempts, outcome, detail=detail,
                input_tokens=in_delta, tokens_used=out_delta, conn=db_conn,
            )
        tokens_at_last_attempt = (total_input_tokens, total_output_tokens)

    def run_verification(forced: bool = False) -> tuple[bool, str]:
        """Build -> safety scan -> smoke test (-> extra_verify), recorded as
        one verification attempt and emitted as one 'build' event. Called
        both for a finish() the model asked for and for the forced last-ditch
        attempt below; `forced` only changes the wording."""
        nonlocal verification_attempts
        passed, detail, built_html = builder.build_and_verify(
            game_dir, engine=engine, engine_version=engine_version)
        if passed and extra_verify is not None:
            extra_detail = extra_verify(game_dir, built_html)
            if extra_detail:
                passed, detail = False, extra_detail
        verification_attempts += 1
        prefix = "Forced final verification" if forced else "Verification"
        if passed:
            record_verification("success", None)
            _safe_emit(emit, "build", f"{prefix} passed: build, safety scan, and "
                                      "smoke test all succeeded.",
                       {"outcome": "success", "attempt": verification_attempts,
                        "forced": forced})
            return True, detail
        outcome = _classify_failure(detail)
        # Classify on the browser's own words, then append our attribution —
        # the note names files and lines, which would otherwise skew the
        # keyword match _classify_failure does.
        located = _locate_syntax_faults(game_dir)
        if located:
            detail = f"{detail}\n\n{located}"
        record_verification(outcome, detail)
        _safe_emit(emit, "build", f"{prefix} failed: {detail}",
                   {"outcome": outcome, "attempt": verification_attempts,
                    "forced": forced})
        return False, detail

    step_num = 0
    while True:
        step_num += 1
        # Step-budget guard, checked HERE for the same reason the two guards
        # below are: the conversation ends in a tool result or a user message
        # at this point, so appending one is legal and stopping loses nothing
        # — every tool call from the previous turn has already run and hit
        # disk. Which is also what makes it safe to block here for half an
        # hour: nothing is in flight.
        if step_num > step_budget:
            # job_id is None means there is nobody to ask — a direct call or a
            # test, with no generation_requests row to poll and no status page
            # watching it. Those runs keep the pre-existing behaviour exactly.
            if (job_id is None or asked_for_more_steps
                    or extra_steps_on_approval <= 0):
                break
            asked_for_more_steps = True
            extra, cancelled = _await_step_approval(
                job_id=job_id, db_conn=db_conn, emit=emit,
                extra_steps=extra_steps_on_approval,
                timeout_seconds=step_approval_timeout,
                step_budget=step_budget, wrote_anything=wrote_anything,
            )
            if cancelled:
                error = "cancelled by user"
                break
            if not extra:
                break
            step_budget += extra
            # A fresh budget earns a fresh warning — same principle as the
            # stall nudge being re-armed by any successful write. Without
            # this, finish_nudge_at still points at the old ceiling, which is
            # now in the past, so the model would coast to the new one with
            # no warning at all.
            finish_nudge_at = _finish_nudge_threshold(step_budget)
            nudged_low_budget = False
            nudged_refinish = False
            # Appended, never inserted or edited, so the cached prefix is
            # untouched and this costs one turn's worth of fresh tokens once.
            # Worded the way every other nudge here is: lead with what to do.
            # "You ran out of turns" invites the state-re-checking sweep that
            # _compact_write_calls' note wording documents.
            messages.append({
                "role": "user",
                "content": (
                    f"The user reviewed your progress and granted you {extra} "
                    f"more turns ({step_budget} in total). Carry on from where "
                    "you are: make the edits this request still needs, then "
                    "call finish(summary). Do not start over, re-read files "
                    "you have already seen, or re-check work you have already "
                    "done — everything you have written is on disk."
                ),
            })
        # Cancellation check, checked HERE for the same reason the context
        # guard below is: the conversation always ends in a tool result or a
        # user message at this point, so breaking loses nothing this turn —
        # every tool call from the previous turn already ran and wrote to
        # disk. Unlike the context guard, a user-initiated cancel skips the
        # forced final verification too (see below): the user explicitly
        # asked this to stop, so it should stop, not spend one more
        # build/scan/smoke cycle first.
        if job_id is not None and db.is_job_cancelled(job_id, conn=db_conn):
            cancelled = True
            error = "cancelled by user"
            break
        # Context guard, checked HERE — before the request, not after the one
        # that revealed the size. Both branches then act on a conversation
        # that ends in a tool result or a user message, so appending a user
        # message is valid and a break loses nothing this turn: every tool
        # call from the previous turn has already been executed and written to
        # disk, which is what the forced final verification below picks up.
        if last_input_tokens >= context_hard_limit:
            error = (
                f"context window nearly full: the last request billed "
                f"{last_input_tokens:,} input tokens, at or past "
                f"{context_hard_limit:,} ("
                f"{int(_CONTEXT_HARD_LIMIT_RATIO * 100)}% of "
                f"{ai.CONTEXT_WINDOW_TOKENS:,}). Stopping before the API "
                "rejects the next request"
            )
            break
        if last_input_tokens >= context_soft_limit and not nudged_context:
            # One nudge, once. Worded the way every other nudge here is (see
            # _compact_write_calls' note): lead with what to do, never with
            # "you have a problem" — the transcript-imitation lesson is that
            # alarm wording makes this model re-check state, which is the one
            # thing that cannot help when the conversation is already too big.
            nudged_context = True
            messages.append({
                "role": "user",
                "content": (
                    f"CONTEXT WARNING: this conversation has grown to "
                    f"{last_input_tokens:,} input tokens and cannot grow "
                    "indefinitely — past roughly "
                    f"{context_hard_limit:,} the run has to stop wherever it "
                    "is. Stop exploring: do not call read_file or search "
                    "again unless a change you are about to make genuinely "
                    "depends on it. Make the edits you have left with "
                    "edit_file (or write_file where a whole module really "
                    "does change) and call finish(summary) as soon as they "
                    "are done."
                ),
            })
        try:
            ask_result = ai.ask_with_tools(
                messages, tools=AGENT_TOOLS, tool_choice="auto",
                model=model, effort=effort, timeout=ai_timeout,
            )
        except ai.AIError as exc:
            error = f"AI error: {exc}"
            break

        total_input_tokens += ask_result.input_tokens
        total_output_tokens += ask_result.output_tokens
        total_cached_tokens += ask_result.cached_tokens
        last_input_tokens = ask_result.input_tokens
        last_model = ask_result.model or "default"
        last_effort = ask_result.effort
        messages.append(ask_result.message)
        assistant_msg = ask_result.message

        # One 'usage' event per LLM call, carrying that call's own token
        # counts and the run's running totals. The generation_requests row
        # only gets a total once the whole job is over, and
        # generation_attempts only records a row per finish() verification,
        # so without this a long agent run's token spend is invisible until
        # it ends — and invisible per-turn even then.
        _safe_emit(
            emit, "usage",
            f"LLM call {step_num}: {ask_result.input_tokens:,} in / "
            f"{ask_result.output_tokens:,} out "
            f"(running total {total_input_tokens + total_output_tokens:,})",
            {
                "step": step_num,
                "call_input_tokens": ask_result.input_tokens,
                "call_output_tokens": ask_result.output_tokens,
                "call_cached_tokens": ask_result.cached_tokens,
                "input_tokens": total_input_tokens,
                "output_tokens": total_output_tokens,
                "cached_tokens": total_cached_tokens,
                "tokens_used": total_input_tokens + total_output_tokens,
                "model": last_model,
                "effort": last_effort,
            },
        )

        thought = _reasoning_content(ask_result)
        if thought:
            _safe_emit(emit, "thought", thought[:_THOUGHT_MAX_CHARS])
        if ask_result.text:
            _safe_emit(emit, "assistant", ask_result.text[:_ASSISTANT_MAX_CHARS])

        if not ask_result.tool_calls:
            messages.append({
                "role": "user",
                "content": "You must call one of your tools (read_map/list_files/"
                           "read_file/write_file/finish) every turn.",
            })
            consecutive_no_progress += 1
            if consecutive_no_progress >= _MAX_NO_PROGRESS_STEPS:
                error = (
                    f"agent made no progress: {_MAX_NO_PROGRESS_STEPS} "
                    "consecutive turns with no tool call"
                )
                break
            continue

        finish_calls = [tc for tc in ask_result.tool_calls if tc.name == "finish"]
        other_calls = [tc for tc in ask_result.tool_calls if tc.name != "finish"]
        made_progress = False

        compact_records: list[tuple[str, str, str | None, str]] = []
        for tc in other_calls:
            call_content, call_data = _summarize_tool_call(tc)
            _safe_emit(emit, "tool_call", call_content, call_data)
            content, touched_path = _execute_tool(
                tc, game_dir, max_module_bytes, module_warn_bytes, written_paths,
                snapshot_paths)
            obs_content, obs_data = _summarize_observation(tc.name, touched_path, content)
            _safe_emit(emit, "tool_result", obs_content, obs_data)
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": content})
            if not content.startswith("ERROR:"):
                # A failed call is never progress, and its key is not
                # recorded either — retrying the same bad path stays a
                # repeat rather than looking like a fresh observation.
                key = _progress_key(tc, touched_path)
                if key is not None and key not in seen_observations:
                    seen_observations.add(key)
                    made_progress = True
            if tc.name in ("write_file", "edit_file"):
                # Every write_file is compacted; an edit_file only when its
                # strings are big enough to be a whole-module rewrite wearing
                # a different tool's name. A small edit stays in history with
                # its real arguments on purpose (see DEFAULT_EDIT_COMPACT_BYTES).
                if (tc.name == "write_file"
                        or _edit_payload_bytes(tc.arguments) > edit_compact_bytes):
                    compact_records.append((tc.id, tc.name, touched_path, content))
                if touched_path and content.startswith("OK:"):
                    made_progress = True
                    wrote_anything = True
                    edited_since_finish = True
                    written_paths.add(touched_path)
                    # Re-arm the stall nudge below: it answers a specific
                    # stall, and a real write means the run moved on. Job
                    # 79a0abbb spent its one nudge on an early review pause,
                    # then wrote six more files and was killed outright at
                    # the next pause with everything unverified.
                    nudged_to_finish = False
        _compact_write_calls(messages, assistant_msg, compact_records,
                             snapshot_paths, written_paths)

        for extra in finish_calls[1:]:
            messages.append({
                "role": "tool", "tool_call_id": extra.id,
                "content": "Ignored: only one finish call per turn.",
            })

        if finish_calls:
            finish_call = finish_calls[0]
            call_content, call_data = _summarize_tool_call(finish_call)
            _safe_emit(emit, "tool_call", call_content, call_data)
            candidate_summary = _parse_finish_summary(finish_call.arguments)
            last_candidate_summary = candidate_summary or last_candidate_summary
            # A finish with nothing written since the last failed one runs a
            # byte-identical build and can only fail identically, yet it
            # spends one of the three verification attempts doing so. Job
            # 0cf766d0 (2026-07-28) burned its second attempt on exactly this
            # — its own reasoning was "maybe the error was transient" — and
            # then ran out on the third. Bounce it once per failure window
            # (the latch is re-armed by the next failure, so the model can
            # still insist and get its build) for the same reason _edit_file
            # rejects a no-op edit: don't let a turn that changes nothing
            # register as progress against a budget.
            if (verification_attempts > 0 and not edited_since_finish
                    and not rejected_noop_finish):
                rejected_noop_finish = True
                note = (
                    "REJECTED: nothing on disk has changed since the last "
                    "verification, so this build would be byte-identical and "
                    "fail the same way. The failure is not transient — it is "
                    "in the source. Edit the file that is wrong with "
                    "edit_file or write_file, then call finish again."
                )
                messages.append({"role": "tool", "tool_call_id": finish_call.id,
                                 "content": note})
                _safe_emit(emit, "tool_result", note, {"tool": "finish"})
                continue
            passed, detail = run_verification()
            if passed:
                messages.append({
                    "role": "tool", "tool_call_id": finish_call.id,
                    "content": "Verification passed: build, safety scan, and smoke test all succeeded.",
                })
                summary = candidate_summary
                success = True
                break
            error = detail
            # A finish just failed. Re-arm the re-finish nudge and require a
            # fresh write before it can fire, so it points the model back at
            # finish only once it has actually edited something to fix this
            # failure — not on the very next read.
            edited_since_finish = False
            nudged_refinish = False
            rejected_noop_finish = False
            if verification_attempts >= max_verification_retries:
                messages.append({
                    "role": "tool", "tool_call_id": finish_call.id,
                    "content": f"REJECTED: {detail}",
                })
                break
            messages.append({
                "role": "tool", "tool_call_id": finish_call.id,
                "content": f"REJECTED: {detail}\n\nFix the problem and call finish again.",
            })
            continue

        # Two budget nudges, both independent of write cadence (which is why
        # they sit apart from the no-progress guard below, see
        # _finish_nudge_threshold): one for a run that has never verified and
        # is running low, and one for a run that failed a finish, fixed it, and
        # is now re-reading instead of re-verifying. Only one fires per turn.
        steps_left = step_budget - step_num
        if (verification_attempts > 0 and edited_since_finish
                and not nudged_refinish and steps_left <= finish_nudge_at):
            # A finish already failed and the model has since edited files to
            # fix it, but the budget is running low and it hasn't re-verified.
            # Job d6ca3a88's wasted tail (see edited_since_finish's comment):
            # it re-read its own modules to convince itself the fix was right
            # instead of just calling finish, which checks it far more strictly
            # and hands back the exact failure if not. Lead with "you fixed it,
            # verify" — the transcript-imitation lesson in _compact_write_calls
            # is that "you stalled" wording makes the model re-check state
            # rather than move on.
            nudged_refinish = True
            messages.append({
                "role": "user",
                "content": (
                    f"BUDGET WARNING: {steps_left} turn(s) remain. You called "
                    "finish, it failed, and you have since edited files to fix "
                    "it. Call finish(summary) again NOW to re-verify — the "
                    "build, safety scan and smoke test check your fix far more "
                    "reliably than re-reading the modules by hand, and if "
                    "anything is still wrong you get the exact failure back and "
                    "can keep editing. Do not spend your remaining turns "
                    "re-reading to check manually."
                ),
            })
        elif (verification_attempts == 0 and not nudged_low_budget
                and steps_left <= finish_nudge_at):
            nudged_low_budget = True
            if wrote_anything:
                warning = (
                    f"BUDGET WARNING: {steps_left} turn(s) remain, and you have "
                    "not called finish yet. A run that ends without a passing "
                    "finish ships NOTHING — every file you have written is "
                    "discarded. Stop reviewing and call finish(summary) on your "
                    "next turn. It runs the build, safety scan and smoke test, "
                    "which check your split far more strictly than re-reading "
                    "the modules can; if anything is wrong you get the exact "
                    "failure back and can keep editing from there."
                )
            else:
                # Exploring is no longer capped at _MAX_NO_PROGRESS_STEPS
                # turns (that guard counts repetition now), so a run can in
                # principle read and search its way through the whole budget
                # without ever making the change. This is the backstop.
                warning = (
                    f"BUDGET WARNING: {steps_left} turn(s) remain and you have "
                    "not written a single file. Reading and searching ship "
                    "NOTHING on their own — only write_file changes the game, "
                    "and only finish(summary) verifies it. Stop exploring and "
                    "start writing the change now with what you already know."
                )
            messages.append({"role": "user", "content": warning})

        consecutive_no_progress = 0 if made_progress else consecutive_no_progress + 1
        if consecutive_no_progress >= _MAX_NO_PROGRESS_STEPS:
            # Nothing written yet, so the finish nudge below would be
            # nonsense (there is nothing to verify) — but this run is
            # re-reading and re-searching what it has already seen, which is
            # its own kind of stuck. Point it at the actual next step once
            # before giving up.
            if not wrote_anything and not nudged_to_write:
                nudged_to_write = True
                consecutive_no_progress = 0
                messages.append({
                    "role": "user",
                    "content": (
                        f"You have gone {_MAX_NO_PROGRESS_STEPS} turns without "
                        "reading or finding anything you had not already seen, "
                        "and you have not written a file yet. Re-reading what "
                        "is already in this conversation cannot move the task "
                        "forward. Make the change now with write_file, using "
                        "what you have already read; if a file's current "
                        "contents are genuinely missing from this conversation, "
                        "read that one file and then write it."
                    ),
                })
                continue
            # Reading isn't writing, but it isn't necessarily stalling
            # either: a run that has written files and is now re-reading
            # them is doing a final consistency pass before finish. Killing
            # that throws away a complete split for being careful — a real
            # explode pilot wrote all 10 modules, spent its last turns
            # reviewing them, and was aborted having never called finish
            # (which the "your split is checked for missing declarations"
            # prompt rule actively encourages). So spend one nudge before
            # giving up; only a stall that survives the nudge is a stall.
            if wrote_anything and not nudged_to_finish:
                nudged_to_finish = True
                consecutive_no_progress = 0
                messages.append({
                    "role": "user",
                    "content": (
                        f"You have gone {_MAX_NO_PROGRESS_STEPS} turns without "
                        "writing a file. Reading cannot verify anything — only "
                        "finish(summary) runs the build, safety scan and smoke "
                        "test. If every file is written, call finish(summary) "
                        "NOW. If something is still missing or wrong, write it "
                        "with write_file first, then call finish."
                    ),
                })
                continue
            error = (
                f"agent made no progress: {_MAX_NO_PROGRESS_STEPS} consecutive "
                "turns that neither wrote a file nor observed anything new"
            )
            break

    # Last-ditch verification. Every way this loop can end other than a
    # passing finish() used to discard the run outright — the caller deletes
    # the fork — even when every module the change needed was already written
    # and correct on disk. The only thing missing was the finish() call, and
    # finish() is not the model's to bestow: it just runs a deterministic
    # build -> scan -> smoke gate the loop can run itself.
    #
    # Job 79a0abbb (2026-07-26) is the case. It wrote all six modules for the
    # requested feature, spent its last five turns re-reading them to settle a
    # `TILE` vs `TILE_SIZE` question, tripped the no-progress guard a second
    # time, and was killed — 1.58M tokens and ~18 minutes for nothing, one
    # unmade call away from either shipping or a concrete error to show.
    # Verifying costs one build; not verifying guarantees a total loss. Skip it
    # only when the verification-retry budget is already spent (that ceiling
    # exists to stop exactly this kind of repetition) or when nothing was ever
    # written (nothing to verify).
    complete = success
    if (not success and not cancelled and wrote_anything
            and verification_attempts < max_verification_retries):
        stalled_error = error or f"ran out of steps after {step_budget} turns"
        passed, detail = run_verification(forced=True)
        if passed:
            success = True
            # Shipped, but say so plainly. This path passing means the code
            # BUILDS, not that the request was carried out: job 837b2b8c
            # (2026-07-27) was mid-edit on the last of three requested
            # features when its budget ran out, passed build/scan/smoke on the
            # two-and-a-bit it had done, and reported as an unqualified
            # success with a 🎉 — so the requester only found out by playing
            # the game. The forced verification is worth keeping (the
            # alternative is discarding real work), but it must not be
            # reported as a clean finish.
            warning = (
                "⚠️ The agent ran out of turns before confirming it was done. "
                "Everything it had written passed the build, safety scan and "
                "smoke test and has shipped, but the requested change may be "
                "only partly applied — check the game and enhance again for "
                "anything still missing."
            )
            summary = (f"{warning}\n\nIts last summary of the work: "
                       f"{last_candidate_summary}"
                       if last_candidate_summary else warning)
            error = None
        else:
            # Report the real defect, not the stall that exposed it — "smoke
            # test failed: ReferenceError foo is not defined" is actionable
            # where "agent made no progress" is not. Keep the stall as
            # context, since it's why the run ended here.
            error = f"{detail} (agent never called finish: {stalled_error})"

    return {
        "success": success,
        # True only for a run the model itself declared done with a passing
        # finish(). A forced-verification ship is a success that shipped
        # without that confirmation, and the callers surface the difference.
        "complete": complete,
        "summary": summary,
        "attempts": verification_attempts,
        "input_tokens": total_input_tokens,
        "output_tokens": total_output_tokens,
        "cached_tokens": total_cached_tokens,
        "tokens_used": total_input_tokens + total_output_tokens,
        "model": last_model,
        "effort": last_effort,
        "error": None if success else (error or "agent gave up without a passing finish"),
    }


# ---------------------------------------------------------------------------
# Fork staging
# ---------------------------------------------------------------------------

def _stage_fork(source_dir: Path, dest_dir: Path) -> None:
    """Copy only the editable multi-file source (src/ + game.md) into a
    fresh fork directory — never the source's meta.json (the fork mints its
    own identity only once the agent's edits pass verification) or its
    built index.html (build_and_verify regenerates that from the agent's
    own edits)."""
    dest_dir.mkdir(parents=True, exist_ok=False)
    shutil.copytree(source_dir / "src", dest_dir / "src")
    game_md = source_dir / "game.md"
    if game_md.is_file():
        shutil.copy2(game_md, dest_dir / "game.md")


# ---------------------------------------------------------------------------
# job_runner dispatch check
# ---------------------------------------------------------------------------

def is_multi_file_source(source_game_id: str, games_dir, conn=None) -> bool:
    """Whether source_game_id's on-disk game is multi-file, for job_runner's
    enhance dispatch (route here vs. game_enhancer.enhance_game()). Returns
    False — routing to the legacy path — if the game can't be resolved at
    all; that path's own resolve_target() raises the real, user-facing
    error, so this never needs to duplicate it."""
    row = db.get_web_game(source_game_id, conn=conn)
    if row is None:
        return False
    return builder.is_multi_file(Path(games_dir) / row["slug"])


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def _failure_result(exc: Exception, cfg: dict, t0: float) -> dict:
    return {
        "success": False, "complete": False, "game_id": None, "slug": None, "title": None,
        "description": None, "attempts": 0,
        "input_tokens": 0, "output_tokens": 0, "cached_tokens": 0, "tokens_used": 0, "model": "default",
        "effort": cfg.get("effort", "high"), "duration_seconds": time.monotonic() - t0,
        "error": str(exc), "notes": "", "url": None,
        "parent_game_id": None, "root_game_id": None,
    }


def enhance_multifile_game(source_game_id: str, description: str, requested_by: str,
                            config: dict, db_conn=None, games_dir: Path | None = None,
                            job_id: str | None = None, new_title: str | None = None,
                            creator_uid: str | None = None, emit: Callable | None = None) -> dict:
    """Drive the fork -> ReAct-edit -> verify loop for a multi-file source
    game and return a result dict in the same shape as
    game_enhancer.enhance_game() (result["message"] is ready to display; DB
    registration already performed on success before returning).

    On success this writes a brand-new games/<slug>/ directory and
    web_games row (parent_game_id=source_game_id,
    root_game_id=source's root_game_id) — the source game is never
    modified. On failure the half-written fork directory is deleted.

    `emit` (Sprint 3) is passed straight through to _run_react_loop and is
    also used here for the terminal 'final'/'error' event, once the fork's
    slug/title/url are known — see _make_emitter/_safe_emit."""
    games_dir = Path(games_dir) if games_dir is not None else gg.GAMES_DIR
    cfg = config.get("multifile_agent", {})
    t0 = time.monotonic()
    if emit is None:
        emit = _make_emitter(job_id, db_conn)

    try:
        source_row = ge.resolve_target(source_game_id, games_dir, conn=db_conn)
    except ge.GameEnhancementError as exc:
        result = _failure_result(exc, cfg, t0)
        result["message"] = ge.format_report(result)
        _safe_emit(emit, "error", result["error"])
        return result

    source_dir = games_dir / source_row["slug"]
    if not builder.is_multi_file(source_dir):
        # job_runner only dispatches here for multi-file sources — but fail
        # loudly rather than silently mis-editing a single-file game if it
        # ever does.
        exc = ge.GameEnhancementError(f"'{source_game_id}' is not a multi-file game")
        result = _failure_result(exc, cfg, t0)
        result["message"] = ge.format_report(result)
        _safe_emit(emit, "error", result["error"])
        return result

    title_override = (new_title or "").strip() or None
    if title_override is None:
        n = db.count_by_root(source_row["root_game_id"], conn=db_conn) + 1
        base_title = re.sub(r"\s*\(v\d+\)$", "", source_row["title"]).strip()
        title_override = f"{base_title} (v{n})"

    dest_game_id = db.mint_game_id()
    dest_slug = db.make_slug(title_override, dest_game_id)
    collision = gg.check_slug_collision(dest_slug, games_dir)
    if collision:
        exc = ge.GameEnhancementError(f"slug collision: {collision}")
        result = _failure_result(exc, cfg, t0)
        result["message"] = ge.format_report(result)
        _safe_emit(emit, "error", result["error"])
        return result

    dest_dir = games_dir / dest_slug
    _stage_fork(source_dir, dest_dir)

    # Read from dest_dir, not source_dir: it's the staged copy the agent will
    # actually edit, so "identical to what you were shown" stays true even if
    # the two ever diverge.
    snapshot = _build_source_snapshot(
        dest_dir, cfg.get("snapshot_max_bytes", DEFAULT_SNAPSHOT_MAX_BYTES))
    # One summary event, never the body. agent_events is a permanent archive
    # and the chat pane replays from it; a few hundred KB of source per job
    # would bloat both for nothing anyone would read there.
    _safe_emit(
        emit, "tool_result",
        "Loaded source snapshot: {} files, {:,} bytes".format(
            snapshot.file_count, snapshot.total_bytes)
        if snapshot.text is not None else
        "Source too large to snapshot ({} files, {:,} bytes) — the agent will "
        "read files as it needs them".format(snapshot.file_count, snapshot.total_bytes),
        {"tool": "snapshot", "file_count": snapshot.file_count,
         "bytes": snapshot.total_bytes, "included": snapshot.text is not None},
    )

    # From the source game: the staged fork has no meta.json of its own yet,
    # and the engine is inherited, never chosen per enhancement.
    engine, engine_version = builder.read_engine(source_dir)

    outcome = _run_react_loop(
        game_dir=dest_dir,
        system_prompt=_build_system_prompt(source_row["title"], snapshot, engine),
        user_prompt=_build_user_prompt(description),
        cfg=cfg, job_id=job_id, db_conn=db_conn, emit=emit,
        snapshot_paths=snapshot.paths if snapshot.text is not None else None,
        engine=engine, engine_version=engine_version,
    )
    duration = time.monotonic() - t0

    if not outcome["success"]:
        gg.rollback_game_files(dest_dir)
        result = {
            "success": False, "complete": False, "game_id": None, "slug": None, "title": None,
            "description": None, "attempts": outcome["attempts"],
            "input_tokens": outcome["input_tokens"], "output_tokens": outcome["output_tokens"],
            "tokens_used": outcome["tokens_used"], "cached_tokens": outcome["cached_tokens"],
            "model": outcome["model"],
            "effort": outcome["effort"], "duration_seconds": duration,
            "error": outcome["error"], "notes": "", "url": None,
            "parent_game_id": None, "root_game_id": None,
        }
        result["message"] = ge.format_report(result)
        _safe_emit(emit, "error", result["error"])
        return result

    dest_version = (source_row["version"] or 1) + 1
    meta = {
        "game_id": dest_game_id,
        "parent_game_id": source_row["game_id"],
        "root_game_id": source_row["root_game_id"],
        "title": title_override,
        "description": source_row["description"],
        "requested_by": requested_by,
        "created_at": db.now_iso(),
        "version": dest_version,
        "prompt": description,
        "format": "multi-file",
    }
    if engine:
        meta["engine"] = engine
        meta["engine_version"] = engine_version
    (dest_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    result = {
        "success": True, "game_id": dest_game_id, "slug": dest_slug,
        "title": title_override, "description": source_row["description"],
        "attempts": outcome["attempts"],
        "input_tokens": outcome["input_tokens"], "output_tokens": outcome["output_tokens"],
        "tokens_used": outcome["tokens_used"], "cached_tokens": outcome["cached_tokens"],
        "model": outcome["model"], "effort": outcome["effort"],
        "duration_seconds": duration, "error": None, "notes": outcome["summary"],
        "complete": outcome["complete"],
        "url": gg.build_play_url(dest_slug, config),
        "parent_game_id": source_row["game_id"], "root_game_id": source_row["root_game_id"],
    }
    db.register_web_game(
        game_id=result["game_id"], slug=result["slug"], title=result["title"],
        description=result["description"], requested_by=requested_by, status="success",
        attempts=result["attempts"], version=dest_version, model=result["model"],
        effort=result["effort"], duration_seconds=duration,
        input_tokens=result["input_tokens"], output_tokens=result["output_tokens"],
        tokens_used=result["tokens_used"], error=None,
        parent_game_id=result["parent_game_id"], root_game_id=result["root_game_id"],
        creator_uid=creator_uid, conn=db_conn,
    )
    gg.run_moderation_pass(
        result["game_id"], result["slug"], result["description"], result["notes"],
        games_dir, db_conn=db_conn,
    )
    result["message"] = ge.format_report(result)
    _safe_emit(emit, "final", result["notes"] or "Enhancement complete.",
               {"slug": result["slug"], "title": result["title"],
                "url": f"/play/{result['slug']}",
                "incomplete": not result["complete"]})
    return result


# ---------------------------------------------------------------------------
# Sprint 5 — the explode pass (docs/multifile-agent/05-migration-and-pilot.md
# Part A) and the dual-format enhance policy (Part B)
# ---------------------------------------------------------------------------

def explode_game(source_game_id: str, requested_by: str, config: dict, db_conn=None,
                  games_dir: Path | None = None, job_id: str | None = None,
                  new_title: str | None = None, creator_uid: str | None = None,
                  emit: Callable | None = None, announce_completion: bool = True) -> dict:
    """Convert an existing single-file game into a multi-file fork,
    behavior-preserving: the model is handed the whole original index.html
    as input context and re-emits it split across src/index.html +
    src/style.css + src/*.js + game.md via the same write_file/finish tools
    _run_react_loop already drives, so this works even on a game already at
    the output-token ceiling (the whole point — see
    docs/multifile-agent/00-overview.md). Build -> safety scan -> smoke
    test must all pass, same gate as any other change; a manual play-test
    is still the only check that the game *plays* identically, not just
    that it's console-error-free (see the Sprint 5 doc's Part A).

    Forks exactly like enhance_multifile_game: a brand-new games/<slug>/ is
    written (parent_game_id=source_game_id, root_game_id=source's
    root_game_id), the source is never touched, and a failed run deletes
    the half-written fork. Unlike an enhance, the default title is the
    source's own title verbatim (this is a format change, not a content
    change) unless new_title overrides it.

    `announce_completion=False` (used by enhance_game_auto_format's
    dual-format policy, Part B) suppresses the terminal 'final' event and
    its Play link — that fork is an internal implementation detail the
    requester didn't directly ask to see — emitting a short 'assistant'
    note instead so the transcript still explains why an extra step ran
    before the caller continues on with the actual requested enhancement.
    """
    games_dir = Path(games_dir) if games_dir is not None else gg.GAMES_DIR
    cfg = config.get("multifile_agent", {})
    t0 = time.monotonic()
    if emit is None:
        emit = _make_emitter(job_id, db_conn)

    try:
        source_row = ge.resolve_target(source_game_id, games_dir, conn=db_conn)
    except ge.GameEnhancementError as exc:
        result = _failure_result(exc, cfg, t0)
        result["message"] = ge.format_report(result)
        _safe_emit(emit, "error", result["error"])
        return result

    source_dir = games_dir / source_row["slug"]
    if builder.is_multi_file(source_dir):
        exc = ge.GameEnhancementError(f"'{source_game_id}' is already multi-file")
        result = _failure_result(exc, cfg, t0)
        result["message"] = ge.format_report(result)
        _safe_emit(emit, "error", result["error"])
        return result

    source_html = (source_dir / "index.html").read_text(encoding="utf-8")
    title_override = (new_title or "").strip() or source_row["title"]

    dest_game_id = db.mint_game_id()
    dest_slug = db.make_slug(title_override, dest_game_id)
    collision = gg.check_slug_collision(dest_slug, games_dir)
    if collision:
        exc = ge.GameEnhancementError(f"slug collision: {collision}")
        result = _failure_result(exc, cfg, t0)
        result["message"] = ge.format_report(result)
        _safe_emit(emit, "error", result["error"])
        return result

    dest_dir = games_dir / dest_slug
    dest_dir.mkdir(parents=True, exist_ok=False)

    # Explode runs under its own, much tighter module ceiling than an
    # ordinary edit — see DEFAULT_EXPLODE_MAX_MODULE_BYTES. Overriding the
    # key the loop already reads keeps _run_react_loop unaware of which pass
    # it's driving.
    explode_max_module_bytes = cfg.get(
        "explode_max_module_bytes", DEFAULT_EXPLODE_MAX_MODULE_BYTES
    )
    explode_cfg = dict(cfg, max_module_bytes=explode_max_module_bytes)

    # Inherited from the game being split — the fork's own meta.json is only
    # written once the run succeeds, so the loop has to be told directly.
    engine, engine_version = builder.read_engine(source_dir)

    outcome = _run_react_loop(
        game_dir=dest_dir,
        system_prompt=_build_explode_system_prompt(
            source_row["title"], source_html, explode_max_module_bytes, engine
        ),
        user_prompt=_build_explode_user_prompt(),
        cfg=explode_cfg, job_id=job_id, db_conn=db_conn, emit=emit,
        extra_verify=_explode_declaration_check(source_html),
        engine=engine, engine_version=engine_version,
    )
    duration = time.monotonic() - t0

    if not outcome["success"]:
        gg.rollback_game_files(dest_dir)
        result = {
            "success": False, "complete": False, "game_id": None, "slug": None, "title": None,
            "description": None, "attempts": outcome["attempts"],
            "input_tokens": outcome["input_tokens"], "output_tokens": outcome["output_tokens"],
            "tokens_used": outcome["tokens_used"], "cached_tokens": outcome["cached_tokens"],
            "model": outcome["model"],
            "effort": outcome["effort"], "duration_seconds": duration,
            "error": outcome["error"], "notes": "", "url": None,
            "parent_game_id": None, "root_game_id": None,
        }
        result["message"] = ge.format_report(result)
        _safe_emit(emit, "error", result["error"])
        return result

    dest_version = (source_row["version"] or 1) + 1
    meta = {
        "game_id": dest_game_id,
        "parent_game_id": source_row["game_id"],
        "root_game_id": source_row["root_game_id"],
        "title": title_override,
        "description": source_row["description"],
        "requested_by": requested_by,
        "created_at": db.now_iso(),
        "version": dest_version,
        "prompt": "explode: split single-file game into multi-file modules (behavior-preserving)",
        "format": "multi-file",
    }
    if engine:
        meta["engine"] = engine
        meta["engine_version"] = engine_version
    (dest_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    result = {
        "success": True, "game_id": dest_game_id, "slug": dest_slug,
        "title": title_override, "description": source_row["description"],
        "attempts": outcome["attempts"],
        "input_tokens": outcome["input_tokens"], "output_tokens": outcome["output_tokens"],
        "tokens_used": outcome["tokens_used"], "cached_tokens": outcome["cached_tokens"],
        "model": outcome["model"], "effort": outcome["effort"],
        "duration_seconds": duration, "error": None,
        "notes": outcome["summary"] or "Converted to the multi-file format.",
        "complete": outcome["complete"],
        "url": gg.build_play_url(dest_slug, config),
        "parent_game_id": source_row["game_id"], "root_game_id": source_row["root_game_id"],
    }
    db.register_web_game(
        game_id=result["game_id"], slug=result["slug"], title=result["title"],
        description=result["description"], requested_by=requested_by, status="success",
        attempts=result["attempts"], version=dest_version, model=result["model"],
        effort=result["effort"], duration_seconds=duration,
        input_tokens=result["input_tokens"], output_tokens=result["output_tokens"],
        tokens_used=result["tokens_used"], error=None,
        parent_game_id=result["parent_game_id"], root_game_id=result["root_game_id"],
        creator_uid=creator_uid, conn=db_conn,
    )
    gg.run_moderation_pass(
        result["game_id"], result["slug"], result["description"], result["notes"],
        games_dir, db_conn=db_conn,
    )
    result["message"] = ge.format_report(result)
    if announce_completion:
        _safe_emit(emit, "final", result["notes"],
                   {"slug": result["slug"], "title": result["title"],
                    "url": f"/play/{result['slug']}",
                    "incomplete": not result["complete"]})
    else:
        _safe_emit(
            emit, "assistant",
            "Converted this game to a multi-file format so future edits can "
            "target individual files — continuing with your requested change…",
        )
    return result


def enhance_game_auto_format(source_game_id: str, description: str, requested_by: str,
                              config: dict, db_conn=None, games_dir: Path | None = None,
                              job_id: str | None = None, new_title: str | None = None,
                              creator_uid: str | None = None, emit: Callable | None = None) -> dict:
    """job_runner's single dispatch point for kind='enhance' (Sprint 5's
    dual-format policy, docs/multifile-agent/05-migration-and-pilot.md Part
    B): decides among three enhance paths based only on the source game's
    on-disk format and size, never a per-request choice.

    - Multi-file source -> enhance_multifile_game() directly (unchanged
      from Sprint 2).
    - Single-file source at/over ge.LARGE_SOURCE_BYTES (the same size
      ceiling game_enhancer already uses to trigger its own compactness
      nudge) -> explode_game() first, then enhance_multifile_game() on the
      resulting fork for the actual requested change. The intermediate
      exploded fork is hidden (db.set_game_hidden) — it's an internal
      formatting step, not something the requester asked to see as its own
      arcade entry — but its parent_game_id/root_game_id still chain back
      through it to the original single-file source, so the info modal's
      ancestor chain and sidebar lineage stay correct across the
      single-to-multi boundary. If the explode step itself fails, falls
      back to the legacy single-file path below rather than failing the
      whole job over an internal step the user never directly asked for.
    - Otherwise -> the legacy game_enhancer.enhance_game() whole-file path,
      unchanged.
    """
    games_dir = Path(games_dir) if games_dir is not None else gg.GAMES_DIR

    if is_multi_file_source(source_game_id, games_dir, conn=db_conn):
        return enhance_multifile_game(
            source_game_id, description, requested_by, config,
            db_conn=db_conn, games_dir=games_dir, job_id=job_id,
            new_title=new_title, creator_uid=creator_uid, emit=emit,
        )

    row = db.get_web_game(source_game_id, conn=db_conn)
    if row is not None:
        index_path = games_dir / row["slug"] / "index.html"
        if index_path.is_file() and index_path.stat().st_size >= ge.LARGE_SOURCE_BYTES:
            exploded = explode_game(
                source_game_id, requested_by, config, db_conn=db_conn,
                games_dir=games_dir, job_id=job_id, emit=emit,
                announce_completion=False,
            )
            if exploded["success"]:
                db.set_game_hidden(exploded["game_id"], True, conn=db_conn)
                return enhance_multifile_game(
                    exploded["game_id"], description, requested_by, config,
                    db_conn=db_conn, games_dir=games_dir, job_id=job_id,
                    new_title=new_title, creator_uid=creator_uid, emit=emit,
                )
            # Explode failed -- fall through to the legacy single-file path.

    return ge.enhance_game(
        source_game_id, description, requested_by, config,
        db_conn=db_conn, games_dir=games_dir, job_id=job_id,
        new_title=new_title, creator_uid=creator_uid,
    )
