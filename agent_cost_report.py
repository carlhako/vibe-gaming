#!/usr/bin/env python3
"""Per-step token and cost report for one agent run.

Read-only, standalone, deliberately not wired into the app: it exists so the
claims in docs/multifile-agent/06a-cache-snapshot-and-edits.md can be checked
against a real job instead of taken on faith. Sprint 6a is entirely about the
gap between input tokens that are RESENT (cached, ~free) and input tokens that
are FRESH (cache miss, the actual bill), and nothing in the UI shows that gap
per step.

    python3 agent_cost_report.py <job_id> [--db vibegames.db]

The numbers come from the `usage` events in `agent_events`, which are the only
per-call accounting that exists — the generation_requests row gets a single
total when the whole job ends, and generation_attempts only gets a row per
finish() verification, so a 48-step run's shape is invisible without these.
Legacy single-file jobs emit no agent events at all and report nothing here.

The headline number is RE-BILLED PREFIX: sum over consecutive calls of
max(0, input[n] - cached[n+1]). Turn n sent input[n] tokens; if the prefix were
perfectly stable, turn n+1 would have found all of them already cached, so
whatever it did not is material this run PAID FOR TWICE. Before Sprint 6a's
append-only change that figure ran to 42% of input on a read-heavy enhance,
because each write_file pruned the read it was based on and every prune
invalidated DeepSeek's cache from that message onward. It should now be ~0.
"""

import argparse
import json
import sqlite3
import sys

# DeepSeek's published per-1M-token pricing, verified 2026-07-27 against
# https://api-docs.deepseek.com/quick_start/pricing — (cache miss, cache hit,
# output). RE-VERIFY before trusting these in new work: they are a vendor
# pricing decision, not a constant, and this repo has already been burned once
# by a number nobody re-checked (ai_client.MAX_OUTPUT_TOKENS). The whole reason
# Sprint 6a exists is the 120:1 miss:hit ratio on pro; if that ratio moves, the
# conclusions drawn from this report move with it.
PRICING = {
    "deepseek-v4-pro": (0.435, 0.003625, 0.870),
    "deepseek-v4-flash": (0.140, 0.0028, 0.280),
}
DEFAULT_MODEL = "deepseek-v4-flash"


def load_usage(conn, job_id):
    """Every 'usage' event for job_id as (seq, data) pairs, oldest first, plus
    the tool calls that followed each one.

    A usage event is emitted immediately after its LLM call returns, before
    that turn's tool calls are executed and emitted, so every tool_call event
    between usage N and usage N+1 belongs to step N. That ordering is the only
    thing tying the two together — a tool_call event carries no step number."""
    rows = conn.execute(
        "SELECT seq, role, content, data FROM agent_events "
        "WHERE job_id=? ORDER BY seq ASC", (job_id,)
    ).fetchall()
    steps = []
    for row in rows:
        data = json.loads(row["data"]) if row["data"] else {}
        if row["role"] == "usage":
            steps.append({"data": data, "tools": [], "build": None})
        elif not steps:
            continue
        elif row["role"] == "tool_call":
            steps[-1]["tools"].append(data.get("tool") or "?")
        elif row["role"] == "build":
            steps[-1]["build"] = data.get("outcome")
    return steps


def rates(model):
    return PRICING.get(model or "", PRICING[DEFAULT_MODEL])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("job_id")
    ap.add_argument("--db", default="vibegames.db")
    args = ap.parse_args(argv)

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    steps = load_usage(conn, args.job_id)
    if not steps:
        print(f"No agent 'usage' events for job {args.job_id!r} in {args.db}.\n"
              "Single-file (non-agent) jobs never emit them.", file=sys.stderr)
        return 1

    job = conn.execute(
        "SELECT kind, status, prompt, model FROM generation_requests WHERE job_id=?",
        (args.job_id,)).fetchone()
    model = steps[-1]["data"].get("model") or (job["model"] if job else None)
    miss_rate, hit_rate, out_rate = rates(model)

    if job:
        prompt = (job["prompt"] or "").strip().replace("\n", " ")
        print(f"job {args.job_id}  kind={job['kind']}  status={job['status']}")
        print(f"  {prompt[:96]}{'…' if len(prompt) > 96 else ''}")
    print(f"  model={model}  rates: ${miss_rate}/M miss  ${hit_rate}/M hit  "
          f"${out_rate}/M out")
    print()

    print(f"{'step':>4} {'in':>9} {'cached':>9} {'fresh':>9} {'out':>8}  tools")
    total_in = total_cached = total_out = 0
    rebilled = 0
    for i, step in enumerate(steps):
        d = step["data"]
        call_in = d.get("call_input_tokens") or 0
        call_cached = d.get("call_cached_tokens") or 0
        call_out = d.get("call_output_tokens") or 0
        fresh = max(0, call_in - call_cached)
        total_in += call_in
        total_cached += call_cached
        total_out += call_out
        if i + 1 < len(steps):
            nxt_cached = steps[i + 1]["data"].get("call_cached_tokens") or 0
            rebilled += max(0, call_in - nxt_cached)
        tools = ", ".join(step["tools"]) or "—"
        if step["build"]:
            tools += f"  [verify: {step['build']}]"
        # A collapse is a cached count that FALLS relative to the previous
        # call: the conversation only grows, so a shrinking cached prefix
        # means something already sent was mutated or removed. Halving is the
        # threshold because ordinary variation is small and a real prune drops
        # cached back to the system+user prompt (~4,500 in the measured runs).
        prev_cached = steps[i - 1]["data"].get("call_cached_tokens") or 0 if i else 0
        flag = "  <-- CACHE COLLAPSE" if prev_cached > 8000 and call_cached < prev_cached / 2 else ""
        print(f"{d.get('step', i + 1):>4} {call_in:>9,} {call_cached:>9,} "
              f"{fresh:>9,} {call_out:>8,}  {tools}{flag}")

    fresh_total = max(0, total_in - total_cached)
    miss_cost = fresh_total / 1e6 * miss_rate
    hit_cost = total_cached / 1e6 * hit_rate
    out_cost = total_out / 1e6 * out_rate
    total_cost = miss_cost + hit_cost + out_cost

    print()
    print(f"steps: {len(steps)}")
    print(f"input:   {total_in:>10,}   cached {total_cached:>10,} "
          f"({_pct(total_cached, total_in)})   fresh {fresh_total:>10,}")
    print(f"output:  {total_out:>10,}")
    print()
    print(f"  cache-miss input  ${miss_cost:.4f}   ({_pct(miss_cost, total_cost)})")
    print(f"  cache-hit input   ${hit_cost:.4f}   ({_pct(hit_cost, total_cost)})")
    print(f"  output            ${out_cost:.4f}   ({_pct(out_cost, total_cost)})")
    print(f"  TOTAL             ${total_cost:.4f}")
    print()
    # The direct read-out of Sprint 6a step 1. Anything materially above zero
    # means some message already sent to the model was later mutated or
    # removed, invalidating DeepSeek's prefix cache from that point on.
    print(f"re-billed prefix: {rebilled:,} tokens "
          f"({_pct(rebilled, total_in)} of input) — "
          f"${rebilled / 1e6 * miss_rate:.4f} at the miss rate")
    return 0


def _pct(part, whole):
    return f"{part / whole * 100:.1f}%" if whole else "n/a"


if __name__ == "__main__":
    sys.exit(main())
