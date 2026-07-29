# Recurring token-cost review

A repeatable pass over the production DB to find where agent-run spend is
going and what to do about it. Run it after every batch of ~10-20 enhances.

## The prompt

Paste this into Claude Code from the repo root:

---

Review token usage across recent agent runs in the production database at
`~/vibegames.db` and tell me what to change to reduce it.

Start by running `python3 agent_cost_trends.py --db ~/vibegames.db` for the
all-time-since-baseline view, then again with `--since 2026-07-30` for only
the runs made after ranged reads shipped. Compare both against the BASELINE
table in that script's docstring.

The specific things I want to know:

1. **Did ranged reads get used?** The baseline was 0 of 296 read calls ranged.
   If ranged is still near 0%, the model is ignoring the range arguments and
   the answer is a deterministic reject, not more prompt wording. If it is
   being used, did `read_file results` actually fall as a share of spend, or
   did the model just make more reads?
2. **Is the unmodified-snapshot re-read still happening?** Baseline was 58
   calls / 9.9% of spend, and both existing mitigations (the emphatic system
   prompt at `agent._build_system_prompt`, and `agent._read_file_nudge`) were
   being ignored. The queued fix is turning the nudge into a rejection that
   points at the snapshot section — check whether the data still justifies it.
3. **What is the new biggest line item?** Do not assume it is still
   `read_file`. Re-derive it. Output tokens and the turn-1 snapshot were 29%
   and 16% at baseline and neither has had any work done on it.
4. **Pick the worst individual run** and walk its transcript
   (`python3 agent_cost_report.py <job_id> --db ~/vibegames.db`, plus the
   `agent_events` rows) to find what it actually spent its turns on. Every
   real finding so far has come from reading one bad run end to end, not from
   the aggregates — the aggregates only say which run to read.

Ground every claim in a query against that DB. Cache-MISS input is what a run
pays for (cached tokens bill at 1/120th on v4-pro), so quote dollars, not
total input tokens. Report findings ranked by savings with the evidence for
each; do not implement anything until I pick one.

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
| ranged reads in use | none — shipped 2026-07-29, unexercised |

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
