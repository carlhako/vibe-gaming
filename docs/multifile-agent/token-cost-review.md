# Recurring token-cost review

A repeatable pass over the production DB to find where agent-run spend is
going and what to do about it. Run it after every batch of ~10-20 enhances.

## The prompt

Paste this into Claude Code from the repo root:

---

Review token usage across recent agent runs in the vibegames production
database and tell me what to change to reduce it. Report findings only — don't
implement anything until I pick one.

The production DB is `~/vibegames.db` (~37MB). The `vibegames.db` in the repo
is a small dev copy — don't analyse that one by mistake.

Run `python3 agent_cost_trends.py --db ~/vibegames.db` for the full picture
since the baseline, then again with `--since <the day after the last review>`
for only the new runs. Compare both against the BASELINE table in that
script's docstring, which is the literal output of the same script on
2026-07-29.

Four questions, in order:

1. **Did ranged reads get used?** Ranged `read_file(path, start_line,
   end_line, char_start)` shipped 2026-07-29 in commit `ea94952`, with a
   baseline of 0 of 296 read calls ranged and no real run having exercised it.
   If `ranged` is still near 0%, the model is ignoring the arguments and the
   answer is a deterministic reject, not more prompt wording — that is the
   lesson this repo has learned four times (CLAUDE.md on `_SNAPSHOT_MARKER_RE`,
   `_normalize_agent_path`, `_PRUNE_SENTINEL`, the explode module ceiling). If
   it is being used, check whether `read_file results` actually fell as a share
   of spend, or whether the model just made more, cheaper reads and spent the
   savings on turns.
2. **Is the unmodified-snapshot re-read still happening?** Baseline was 58
   calls / 9.9% of spend. Both existing mitigations — the emphatic wording in
   `agent._build_system_prompt` and the advisory `agent._read_file_nudge` —
   were live and being ignored 22 times out of 22 in the sample that found
   this. The queued fix is turning that nudge into a rejection pointing at the
   snapshot section. Check whether the data still justifies it before doing it.
3. **What is the biggest line item now?** Do not assume it is still
   `read_file`. Re-derive it from scratch. Output tokens (29% at baseline) and
   the turn-1 snapshot (16%) have had no work done on them at all.
4. **Read the worst individual run end to end.** Pick it from the aggregates,
   then walk it with `python3 agent_cost_report.py <job_id> --db ~/vibegames.db`
   and the raw `agent_events` rows — the `thought` events especially, which
   are where the model says in its own words what it was stuck on. Every real
   finding so far came from reading one bad run this way; the aggregates only
   tell you which run to open.

Method constraints, all of which come from mistakes made in earlier passes:

- **Use the whole population, not a sample.** The first pass reported
  redundant snapshot re-reads as the top finding at 17% of spend, from a
  20-run sample. Over all 57 runs the split inverted — reads of *modified*
  files were 34% and unmodified only 10%. A sample skewed toward short runs
  will mislead you about which fix is worth making.
- **Quote dollars, not tokens.** A resent cached token bills at 1/120th of a
  fresh one on v4-pro, so total input tokens are a near-meaningless headline.
  Cache-*miss* input was 58-98% of what each run actually cost.
- **Ground every claim in a query against that DB**, and show the query or the
  numbers. Don't carry conclusions over from CLAUDE.md — it records what was
  true when written, and re-measuring is the whole point of this exercise.

---

## Baseline being compared against

Established 2026-07-29 from 57 runs over 2026-07-25..29. The full table lives
in `agent_cost_trends.py`'s docstring — that is the authoritative copy, since
it is the literal output of the script you will re-run.

Reference points for the diff:

| | baseline |
|---|---|
| last enhance reviewed | `b7c3215e453c433c8d8b0d36c45ee67e`, 2026-07-29 08:23 |
| that run | 33 turns, 6.08M input, $0.165, 7.3 min |
| ranged reads in use | none — shipped 2026-07-29 in `ea94952`, unexercised |
| review date | 2026-07-29 — use the next day as `--since` |

Note the production server auto-pushes game commits to this repo via
`git_sync.py`, so `origin/main` will usually be ahead of a local checkout by
several `games/`-only commits. Rebase rather than merge; they have never
touched anything outside `games/`.

## What shipped from the first pass (2026-07-29)

Ranged `read_file(path, start_line, end_line, char_start)`. Motivation: 238 of
296 reads were of files the run had already modified, so no snapshot or prompt
could remove them, and each paid whole-module price for a question about a few
lines. Job `b7c3215e` spent 17 of its 33 turns and two 57,000-token re-reads of
a 151KB module locating one unbalanced brace it had already been given the line
number for.

## Queued, not yet done

- **Reject the unmodified-snapshot re-read** rather than nudging it (~10% of
  spend at baseline). Deferred deliberately: ranged reads had to land first so
  the rejection has a cheap alternative to point at. The risk to watch is the
  one in `_read_file_nudge`'s docstring — withholding content has historically
  sent this agent into state-re-checking loops — which is why the rejection
  must point at the snapshot section rather than simply refuse.
- **Output tokens** (29% of spend) and the **turn-1 snapshot** (16%) have had
  no work at all. The snapshot looks irreducible per-run, but note that every
  enhance forks, so consecutive enhances of one game never share a prefix.
