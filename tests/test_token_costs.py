"""Sprint 6a step 0 of docs/multifile-agent/: input tokens bill at two rates.

DeepSeek's prefix cache charges a byte-identical resent prefix at a small
fraction of a fresh token (1/120th on v4-pro), and `cached_tokens` is a SLICE
of `input_tokens` rather than an addition to it. Before this, /admin/stats
charged every input token at the miss rate, reporting a cache-heavy agent
enhance at ~4x its real cost — which is what made the sprint's cost work
unmeasurable in the first place."""

import pytest

import app as app_module
import db

_attach = app_module._attach_token_costs

# The measured job d5279657 (docs/multifile-agent/06a-cache-snapshot-and-edits.md)
# and DeepSeek's v4-pro pricing as verified 2026-07-27.
MISS, HIT, OUT = 0.435, 0.003625, 0.870
JOB = {"input_tokens": 1_772_525, "cached_tokens": 1_255_296,
       "output_tokens": 118_106}


def test_cached_input_is_billed_at_the_cached_rate_not_the_miss_rate():
    row = dict(JOB)
    _attach([row], MISS, OUT, HIT)

    fresh = 1_772_525 - 1_255_296
    assert row["fresh_input_tokens"] == fresh
    assert row["cached_input_cost"] == 1_255_296 / 1e6 * HIT
    assert row["input_cost"] == fresh / 1e6 * MISS + 1_255_296 / 1e6 * HIT
    assert row["output_cost"] == 118_106 / 1e6 * OUT
    # The doc's table: $0.225 miss + $0.0046 hit + $0.1028 output = $0.332.
    assert round(row["total_cost"], 3) == 0.332


def test_an_unset_cached_rate_reproduces_todays_arithmetic_exactly():
    """The env var defaults to the input rate, so nothing on the page moves
    until it is deliberately set — that is what makes this a safe deploy."""
    row = dict(JOB)
    _attach([row], MISS, OUT)  # cached rate omitted

    # approx, not ==: the split adds the two halves separately, so the same
    # arithmetic regroups the floating-point rounding. The billed amount is
    # identical to the cent, which is all this claim is about.
    assert row["input_cost"] == pytest.approx(1_772_525 / 1e6 * MISS)
    assert round(row["total_cost"], 3) == 0.874


def test_a_row_with_no_cached_tokens_is_unaffected_by_the_split():
    """Ask-AI rows have no cached_tokens column at all; single-file
    generation jobs predating cache accounting have it NULL."""
    for cached in (None, 0):
        row = {"input_tokens": 10_000, "cached_tokens": cached, "output_tokens": 500}
        _attach([row], MISS, OUT, HIT)
        assert row["fresh_input_tokens"] == 10_000
        assert row["input_cost"] == 10_000 / 1e6 * MISS

    missing_column = {"input_tokens": 10_000, "output_tokens": 500}
    _attach([missing_column], MISS, OUT, HIT)
    assert missing_column["input_cost"] == 10_000 / 1e6 * MISS


def test_a_job_with_no_token_counts_still_costs_nothing_rather_than_zero():
    """Pre-existing promise of _token_cost: a job with no recorded tokens
    renders as '—', not as a misleading $0.00."""
    row = {"input_tokens": None, "cached_tokens": None, "output_tokens": None}
    _attach([row], MISS, OUT, HIT)
    assert row["fresh_input_tokens"] is None
    assert row["input_cost"] is None
    assert row["cached_input_cost"] is None
    assert row["total_cost"] is None


def test_admin_stats_prices_a_cache_heavy_job_at_the_split_rate(
        isolated_db, games_dir, monkeypatch):
    """End to end through the env var, the route and the template — the
    History row for a 71%-cached job must not report it at the miss rate."""
    monkeypatch.setenv("ADMIN_TOKEN", "secret-token")
    monkeypatch.setenv("DEEPSEEK_INPUT_COST_PER_MILLION", str(MISS))
    monkeypatch.setenv("DEEPSEEK_OUTPUT_COST_PER_MILLION", str(OUT))
    monkeypatch.setenv("DEEPSEEK_CACHED_INPUT_COST_PER_MILLION", str(HIT))
    db.create_generation_request("d5279657", "enhance", "add the amulet equip feature",
                                 "web:t")
    db.update_generation_request(
        "d5279657", status="success", model="deepseek-v4-pro",
        input_tokens=JOB["input_tokens"], cached_tokens=JOB["cached_tokens"],
        output_tokens=JOB["output_tokens"],
        tokens_used=JOB["input_tokens"] + JOB["output_tokens"])

    flask_app = app_module.create_app(games_dir=games_dir)
    flask_app.config["TESTING"] = True
    body = flask_app.test_client().get("/admin/stats?token=secret-token").data.decode()

    assert "DEEPSEEK_CACHED_INPUT_COST_PER_MILLION" in body
    assert "$0.3323" in body       # the real cost; at the miss rate it read $0.8738
    assert "$0.8738" not in body


def test_cached_tokens_can_never_bill_more_than_the_input_they_are_a_slice_of():
    """Defensive: cached_tokens is a slice of input_tokens, so a row claiming
    more cached than input is nonsense — clamp rather than bill negative fresh
    tokens or double-count."""
    row = {"input_tokens": 1_000, "cached_tokens": 5_000, "output_tokens": 0}
    _attach([row], MISS, OUT, HIT)
    assert row["fresh_input_tokens"] == 0
    assert row["input_cost"] == 1_000 / 1e6 * HIT
