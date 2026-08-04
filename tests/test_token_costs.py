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
import pricing

_attach = app_module._attach_token_costs

# The measured job d5279657 (docs/multifile-agent/06a-cache-snapshot-and-edits.md)
# and DeepSeek's v4-pro pricing as verified 2026-07-27. Same numbers that
# live in pricing.PRICING["deepseek-v4-pro"] by default; pinned here so
# this file documents the math without coupling to pricing.py's defaults.
MISS, HIT, OUT = 0.435, 0.003625, 0.870
PRO_PRICING = {"deepseek-v4-pro": (MISS, HIT, OUT)}
JOB = {"input_tokens": 1_772_525, "cached_tokens": 1_255_296,
       "output_tokens": 118_106}


def test_cached_input_is_billed_at_the_cached_rate_not_the_miss_rate():
    row = dict(JOB)
    row["model"] = "deepseek-v4-pro"
    _attach([row], PRO_PRICING)

    fresh = 1_772_525 - 1_255_296
    assert row["fresh_input_tokens"] == fresh
    assert row["cached_input_cost"] == 1_255_296 / 1e6 * HIT
    assert row["input_cost"] == fresh / 1e6 * MISS + 1_255_296 / 1e6 * HIT
    assert row["output_cost"] == 118_106 / 1e6 * OUT
    # The doc's table: $0.225 miss + $0.0046 hit + $0.1028 output = $0.332.
    assert round(row["total_cost"], 3) == 0.332


def test_a_row_with_no_cached_tokens_is_unaffected_by_the_split():
    """Ask-AI rows have no cached_tokens column at all; single-file
    generation jobs predating cache accounting have it NULL."""
    for cached in (None, 0):
        row = {"input_tokens": 10_000, "cached_tokens": cached, "output_tokens": 500,
               "model": "deepseek-v4-pro"}
        _attach([row], PRO_PRICING)
        assert row["fresh_input_tokens"] == 10_000
        assert row["input_cost"] == 10_000 / 1e6 * MISS

    missing_column = {"input_tokens": 10_000, "output_tokens": 500,
                      "model": "deepseek-v4-pro"}
    _attach([missing_column], PRO_PRICING)
    assert missing_column["input_cost"] == 10_000 / 1e6 * MISS


def test_a_job_with_no_token_counts_still_costs_nothing_rather_than_zero():
    """Pre-existing promise of _token_cost: a job with no recorded tokens
    renders as '—', not as a misleading $0.00."""
    row = {"input_tokens": None, "cached_tokens": None, "output_tokens": None,
           "model": "deepseek-v4-pro"}
    _attach([row], PRO_PRICING)
    assert row["fresh_input_tokens"] is None
    assert row["input_cost"] is None
    assert row["cached_input_cost"] is None
    assert row["total_cost"] is None


def test_admin_stats_prices_a_cache_heavy_job_at_the_split_rate(
        isolated_db, games_dir, monkeypatch):
    """End to end through the per-model pricing table, the route and the
    template — the History row for a 71%-cached job must not report it at
    the miss rate."""
    monkeypatch.setenv("ADMIN_TOKEN", "secret-token")
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

    # The new caveat paragraph names PRICING_JSON (the override mechanism),
    # not the historical DEEPSEEK_*_COST_PER_MILLION env vars. The math is
    # unchanged so the same dollar values render.
    assert "PRICING_JSON" in body
    assert "$0.3323" in body       # the real cost; at the miss rate it read $0.8738
    assert "$0.8738" not in body


def test_cached_tokens_can_never_bill_more_than_the_input_they_are_a_slice_of():
    """Defensive: cached_tokens is a slice of input_tokens, so a row claiming
    more cached than input is nonsense — clamp rather than bill negative fresh
    tokens or double-count."""
    row = {"input_tokens": 1_000, "cached_tokens": 5_000, "output_tokens": 0,
           "model": "deepseek-v4-pro"}
    _attach([row], PRO_PRICING)
    assert row["fresh_input_tokens"] == 0
    assert row["input_cost"] == 1_000 / 1e6 * HIT


def test_per_model_routing_picks_each_rows_own_rates():
    """Two rows, two different models — the cost column tracks each model's
    rates. Pre-step-1 code applied one scalar set to every row regardless."""
    deepseek_rates = {"deepseek-v4-pro": (0.435, 0.003625, 0.870),
                      "MiniMax-M3":     (0.5,   0.005,    1.0)}
    rows = [
        {"model": "deepseek-v4-pro", "input_tokens": 1_000_000,
         "cached_tokens": 0, "output_tokens": 0},
        {"model": "MiniMax-M3",     "input_tokens": 1_000_000,
         "cached_tokens": 0, "output_tokens": 0},
    ]
    _attach(rows, deepseek_rates)
    assert round(rows[0]["input_cost"], 4) == 0.435
    assert round(rows[1]["input_cost"], 4) == 0.5


def test_unknown_model_falls_back_to_default_pricing():
    """A row whose `model` isn't in the pricing table falls back to
    pricing.DEFAULT_PRICING_KEY's rates rather than blanking the cell —
    blank cells hide the cost problem this whole function exists to surface."""
    pricing_only_pro = {"deepseek-v4-pro": (0.435, 0.003625, 0.870)}
    row = {"model": "never-seen-this-model", "input_tokens": 1_000_000,
           "cached_tokens": 0, "output_tokens": 0}
    _attach([row], pricing_only_pro)
    assert round(row["input_cost"], 4) == 0.435


def test_load_pricing_returns_defaults():
    assert "deepseek-v4-pro" in pricing.load_pricing()
    assert pricing.load_pricing()["deepseek-v4-pro"] == (0.435, 0.003625, 0.870)
    assert pricing.load_pricing()["MiniMax-M3"] == (0.0, 0.0, 0.0)
