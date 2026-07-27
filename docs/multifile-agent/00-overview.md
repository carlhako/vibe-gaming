# Multi-File Agent + Live Conversation — Overview

## Why

One game (Sorcerer With A Minigun, the minigun/skeletons fork chain) is
already ~230 KB of inlined HTML — right at what was then believed to be
DeepSeek's hard **output-token ceiling** of 65,536
(`ai_client.MAX_OUTPUT_TOKENS` ≈ ~260 KB of HTML at ~4 chars/token). *(That
belief turned out to be wrong — see the resolved open question below; the
real ceiling is at least 150000/~590 KB. The rest of this section is the
original, since-revised problem statement that motivated the initiative.)*
The current pipeline requires the model to re-emit the *complete*
`index.html` on every generation and every enhancement. We shipped
truncation handling (detect `finish_reason == "length"`, drop thinking mode
on retry, compactness nudge) as a stopgap, but the owner intends to keep
adding features and expects this game to grow **2–3× larger** (~460–690 KB
≈ 112K–168K output tokens). That is **1.7–2.6× past the output ceiling** —
past ~260 KB the whole file can never be emitted in a single response again,
by any trick. The whole-file-resubmit contract is structurally dead for this
game.

This initiative does two intertwined things:

1. **Multi-file game format + a ReAct editing agent** so the model never has
   to output (or read) the entire game at once — it targets only the files a
   change touches.
2. **A live, Claude-chat-style conversation UI** on the Create/Enhance
   screens — a wider, two-pane layout that streams the agent's
   think → act → observe steps as it works, with a persistent transcript.

## The core rule everything follows

**The model must never emit the entire game in a single response, and never
be forced to read all of it either.** Two mechanisms enforce this:

- **Edit multi-file, serve single-file.** Source lives split on disk
  (`game.md` + `src/index.html` + `src/style.css` + `src/*.js`); a
  deterministic, no-AI **build step** inlines it back into one served
  `index.html`. The sandbox model, `safety.scan()`, and `smoke_test.py` all
  keep operating on that one assembled artifact, unchanged. A 690 KB *served*
  file is fine — serving big files is trivial; only *generating* one in a
  single shot was the problem, and the builder concatenates mechanically.
- **Whole-module rewrites, not whole-game.** The agent reads the high-level
  `game.md` map, pulls only the modules a change touches, and rewrites those
  modules (each kept well under the ceiling). Adding a feature usually means
  touching one module — which is what modular structure is for.

## Locked-in decisions

- **Serving stays single-file** via build-and-inline. We do **not** serve
  loose sibling files — that would re-open the sandbox/opaque-origin
  reasoning for no benefit. `/play/<slug>` serves exactly one self-contained
  `index.html`, same as today.
- **Whole-module rewrites for v1**, not search/replace diffs. Fuzzy matching
  against a large file is the classic agentic-editor failure mode; a
  whole-module rewrite is unambiguous and still bounded well under the
  ceiling. Diffs can be earned later (see Sprint 6).
- **Dual-format, no forced global migration.** Legacy games stay
  `format: "single-file"` and keep using the existing
  `run_generation_attempts()` loop. Multi-file is opt-in per game; we pilot
  it on the Sorcerer With A Minigun chain only (Sprint 5).
- **Live updates via incremental polling for v1**, not SSE. It matches the
  project's existing DB-polling philosophy (`static/status.js` already polls
  `/api/status/<job_id>`), stays correct under multiple gunicorn workers, and
  needs no long-lived connections. Per-step granularity (each think/act/
  observe appears as it completes) gives the chat feel without token-level
  streaming. Token-level SSE streaming is a Sprint 6 stretch.
- **The existing admin kill switch stays authoritative.** The agent loop
  checks `db.is_ai_generation_enabled()` through `ai_client` exactly as the
  current pipeline does; a disabled switch aborts cleanly with no partial
  writes.

## Open questions (resolve during the sprints, not blocking to start)

- ~~**Is 65,536 a hard model max or a raisable default?**~~ **Resolved,
  Sprint 6 (2026-07-26): raisable, and it was never really 65,536 to begin
  with.** Every prior "verification" of that number passed
  `max_tokens=65536` explicitly and then observed truncation at exactly
  65536 — self-confirming, since nobody had tried asking for more. A real
  probe requesting up to `max_tokens=384001` was never rejected, and a
  forced long deterministic generation with `max_tokens=150000` produced
  exactly 150000 output tokens without stopping early
  (`finish_reason == "length"`, still generating) — i.e. the real ceiling
  is at least 150000, confirmed live; DeepSeek's own docs claim 384K.
  `ai_client.MAX_OUTPUT_TOKENS` is now 150000 (see that constant's comment).
  This doesn't remove the motivation for this initiative — a multi-file
  source still avoids a whole-game read+write on every enhancement even at
  the higher ceiling — but it does mean single-file games have much more
  headroom before hitting the wall this initiative exists to solve.
- **Context-pruning strategy for long agent runs.** Superseded file contents
  should be dropped/summarized from the running message list so a 10-step
  edit doesn't balloon input. Sprint 2 picks the concrete policy.
- **How aggressively to explode legacy games.** Sprint 5 decides whether an
  enhance of a single-file game auto-explodes it or leaves it legacy.

## Sprint sequence and dependency rationale

1. **[Sprint 1](01-multifile-build.md) — Multi-file format + build-and-inline
   (no AI).** The foundation everything sits on, and the biggest de-risk:
   proves the sandbox/serving/scan/smoke chain is unaffected by a split
   source, with zero AI involved. Independently shippable.
2. **[Sprint 2](02-react-agent-core.md) — ReAct agent core (headless).** The
   tool-driven edit loop (`read_map`/`read_file`/`write_file`/`finish`) with
   build→scan→smoke as its verification feedback and a per-write ceiling
   guard. Wired into `job_runner` for multi-file enhances; no UI yet.
3. **[Sprint 3](03-agent-event-stream.md) — Agent event stream +
   persistence.** The backend the chat UI consumes: an `agent_events` table,
   structured events emitted by the agent, and an incremental
   `GET /api/jobs/<job_id>/events?since=<seq>` endpoint.
4. **[Sprint 4](04-live-chat-ui.md) — Live chat UI + wider layout.** The
   Claude-chat-style transcript on Create/Enhance: widened two-pane layout,
   polling the events endpoint, rendering think/act/observe/build/smoke as
   chat messages. The headline user-facing feature.
5. **[Sprint 5](05-migration-and-pilot.md) — Explode + pilot on Sorcerer With A Minigun.**
   The AI-assisted single-file → multi-file split, dual-format enhance, and a
   measured token-delta comparison on the real problem game.
6. **[Sprint 6](06-streaming-and-polish.md) — Streaming + job controls +
   module-size hygiene (optional).** Token-level SSE streaming, cancel-job
   and live per-job cost; the module-size soft lint is done.
7. **[Sprint 6a](06a-cache-snapshot-and-edits.md) — Cache discipline,
   source snapshot, targeted edits.** Runs next, ahead of the rest of
   Sprint 6. Sprint 6's context pruning mutates already-sent messages,
   which collapses DeepSeek's prefix cache — and a cache hit costs
   **1/120th** of a miss, so cache-miss input turned out to be 68-85% of
   what an enhance actually costs while cached input was under 2%.
   Measured across three production enhances 2026-07-27. Make the
   conversation append-only, hand the model the whole source up front in an
   immutable block instead of making it read the same 80KB module four
   times, add an exact-match `edit_file` so a small change stops costing a
   32K-token whole-module rewrite, and price cached tokens at the cached
   rate so any of it can be verified.

   *(This absorbed the former Sprint 7 — "context pruning vs. prompt
   caching", whose measurement it carried out — and the former Sprint 8's
   `replace_in_file` item. Both files are deleted; Sprint 8's token-level
   streaming item went back to Sprint 6, where it started.)*

## What this initiative does NOT change

- The sandbox boundary (`sandbox="allow-scripts allow-forms
  allow-pointer-lock"`, no `allow-same-origin`) and single served
  `index.html` per game.
- `safety.py` and `smoke_test.py` — they run against the built artifact,
  same as today.
- The DB-polling `job_runner` model, the `generation_requests` job/status
  machinery, `vg_uid` identity, ratings, moderation, or admin pages.
- Single-file games: they keep working through the existing pipeline
  untouched.

## Verification approach across all sprints

Same pattern as the original roadmap: run `python3 app.py` locally, exercise
new routes in the browser preview or via `curl`, inspect `vibegames.db` with
the `sqlite3` CLI, and run `pytest` for new/updated tests before a sprint is
done. Agent sprints additionally mock DeepSeek tool calls (as the existing
`tests/test_generation_loop.py` mocks `ai.ask_with_tools`) so no network or
API key is needed in tests.
