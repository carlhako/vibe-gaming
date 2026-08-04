#!/usr/bin/env python3
"""Where token spend goes across MANY agent runs, and how that shifts over time.

Read-only, standalone, the fleet counterpart to agent_cost_report.py (which
explains one job step by step). This one answers the question you ask before
picking what to optimise: of everything spent across the last N runs, which
category was it?

    python3 agent_cost_trends.py --db ~/vibegames.db
    python3 agent_cost_trends.py --db ~/vibegames.db --since 2026-07-29
    python3 agent_cost_trends.py --db ~/vibegames.db --job b7c3215e   # one run

Cache-MISS input is what a run actually pays for: DeepSeek bills a resent
cached token at 1/120th of a fresh one on v4-pro, so total input tokens are a
near-meaningless headline and every figure here is dollars, not tokens.

The read_file split is the load-bearing part. A read of a file the run has NOT
modified is redundant with the source snapshot already in the system message
(see agent._read_file_nudge) and can in principle be prompted or rejected
away. A read of a file the run HAS modified genuinely needs the current bytes
and cannot — the lever there is reading a RANGE instead of the whole module
(agent._READ_MAX_CHARS). The two need separate numbers because they need
separate fixes.

BASELINE — exactly what this script printed on 2026-07-29, over the 57 runs
since 2026-07-25, with ranged reads written but not yet exercised by any run:

    57 runs since 2026-07-25 (38 with agent events)   total $6.23
      read_file results        $  1.845   29.6%
      output                   $  1.834   29.4%
      other fresh input        $  1.310   21.0%
      turn-1 snapshot          $  0.993   15.9%
      cached input             $  0.251    4.0%
      read_file calls: 296
        unmodified    58 calls  $ 0.619   9.9% of spend
        modified     238 calls  $ 2.133  34.2% of spend
        ranged         0 of 296 (0%)

Re-run with the same --since to reproduce that table, and with a later --since
to see the new runs alone. The change ranged reads were meant to buy is
`ranged` climbing as a share of read calls and `read_file results` falling as
a share of spend. If `ranged` stays near 0%, the model is ignoring the range
arguments and the fix is a deterministic reject, not more prompt wording —
that is the lesson this repo has learned four times over (see CLAUDE.md on
_SNAPSHOT_MARKER_RE, _normalize_agent_path, _PRUNE_SENTINEL).
"""

import argparse
import json
import sqlite3
from collections import Counter

import pricing


def _classify_reads(events):
    """Every read_file call in one run as (redundant, ranged, fresh_tokens).

    `redundant` means the run had not written that path yet and the snapshot
    was included, so the bytes were already verbatim in the system message.
    The fresh-token figure is the cache miss of the NEXT llm call, which is
    the turn that actually carried this read's result into the prompt.
    """
    written, snapshotted, reads = set(), False, []
    for i, (role, data) in enumerate(events):
        tool = data.get("tool")
        if role == "tool_result" and tool == "snapshot" and data.get("included"):
            snapshotted = True
        if role == "tool_call" and tool in ("write_file", "edit_file"):
            written.add(data.get("path"))
        if role == "tool_call" and tool == "read_file":
            fresh = 0
            for later_role, later in events[i:]:
                if later_role == "usage":
                    fresh = (later.get("call_input_tokens", 0)
                             - later.get("call_cached_tokens", 0))
                    break
            ranged = any(data.get(k) is not None
                         for k in ("start_line", "end_line", "char_start"))
            reads.append((snapshotted and data.get("path") not in written,
                          ranged, fresh))
    return reads


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="vibegames.db")
    ap.add_argument("--since", default="2026-07-25",
                    help="ISO date; only runs created on or after it")
    ap.add_argument("--job", help="restrict to one job id (or unique prefix)")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    sql = ("SELECT job_id, model, input_tokens, cached_tokens, output_tokens, "
           "duration_seconds FROM generation_requests WHERE kind IN "
           "('enhance','explode') AND status='success' AND created_at >= ?")
    params = [args.since]
    if args.job:
        sql += " AND job_id LIKE ?"
        params.append(args.job + "%")
    jobs = conn.execute(sql + " ORDER BY created_at", params).fetchall()
    if not jobs:
        print("no matching runs")
        return

    buckets = Counter()
    read_stats = Counter()
    agent_runs = 0
    for job in jobs:
        miss_rate, hit_rate, out_rate = pricing.rates_for(
            job["model"], pricing.load_pricing())
        events = [
            (r["role"], json.loads(r["data"]) if r["data"] else {})
            for r in conn.execute(
                "SELECT role, data FROM agent_events WHERE job_id=? "
                "ORDER BY seq", (job["job_id"],))
        ]
        usage = [d for role, d in events if role == "usage"]
        buckets["cached input"] += (job["cached_tokens"] or 0) / 1e6 * hit_rate
        buckets["output"] += (job["output_tokens"] or 0) / 1e6 * out_rate
        if not usage:
            # A legacy single-file job: no agent events, so no per-call shape.
            buckets["other fresh input"] += (
                ((job["input_tokens"] or 0) - (job["cached_tokens"] or 0))
                / 1e6 * miss_rate)
            continue
        agent_runs += 1

        # Which step each read landed on, so its cost lands in the read bucket
        # rather than in "other". Same ordering rule as agent_cost_report:
        # a usage event precedes the tool calls of its own step.
        read_steps = set()
        step = 0
        for role, data in events:
            if role == "usage":
                step = data.get("step", step + 1)
            if role == "tool_call" and data.get("tool") == "read_file":
                read_steps.add(step + 1)   # the read is carried by the NEXT call
        for d in usage:
            fresh = (d.get("call_input_tokens", 0)
                     - d.get("call_cached_tokens", 0)) / 1e6 * miss_rate
            if d.get("step") == 1:
                buckets["turn-1 snapshot"] += fresh
            elif d.get("step") in read_steps:
                buckets["read_file results"] += fresh
            else:
                buckets["other fresh input"] += fresh

        for redundant, ranged, tokens in _classify_reads(events):
            kind = "unmodified" if redundant else "modified"
            read_stats[f"{kind} calls"] += 1
            read_stats[f"{kind} $"] += tokens / 1e6 * miss_rate
            read_stats["ranged calls" if ranged else "whole-file calls"] += 1

    total = sum(buckets.values())
    print(f"{len(jobs)} runs since {args.since} "
          f"({agent_runs} with agent events)   total ${total:.2f}\n")
    for label, value in sorted(buckets.items(), key=lambda kv: -kv[1]):
        print(f"  {label:24} ${value:7.3f}  {value / total * 100:5.1f}%")

    calls = read_stats["unmodified calls"] + read_stats["modified calls"]
    if not calls:
        print("\n  no read_file calls")
        return
    print(f"\n  read_file calls: {calls}")
    for kind in ("unmodified", "modified"):
        n, cost = read_stats[f"{kind} calls"], read_stats[f"{kind} $"]
        note = ("redundant with the snapshot"
                if kind == "unmodified" else "needs the current bytes")
        print(f"    {kind:11} {n:4} calls  ${cost:6.3f}  "
              f"{cost / total * 100:4.1f}% of spend   ({note})")
    ranged = read_stats["ranged calls"]
    print(f"    ranged      {ranged:4} of {calls} "
          f"({ranged / calls * 100:.0f}%) — was 0% before 2026-07-29")


if __name__ == "__main__":
    main()
