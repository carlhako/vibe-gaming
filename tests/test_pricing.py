"""pricing.PRICING — single source of truth for /admin/stats cost display
and the offline `agent_cost_report.py` / `agent_cost_trends.py` scripts.
The tests here pin the public contract: defaults, env-var overrides,
malformed-input handling, and the unknown-model fallback that keeps the
admin page from blanking out on a brand-new model id."""

import json

import pytest

import pricing


def test_default_pricing_table_has_expected_entries():
    p = pricing.load_pricing()
    # The two DeepSeek families + the M3 placeholder are documented in
    # pricing.py's module docstring; anything added later should land in
    # PRICING (not just PRICING_JSON) so a fresh clone gets working rates
    # without setting env vars.
    assert p["deepseek-v4-pro"] == (0.435, 0.003625, 0.870)
    assert p["deepseek-v4-flash"] == (0.140, 0.0028, 0.280)
    assert p["MiniMax-M3"] == (0.0, 0.0, 0.0)


def test_default_pricing_key_picked_deterministically():
    assert pricing.DEFAULT_PRICING_KEY == "deepseek-v4-pro"
    assert pricing.DEFAULT_PRICING_KEY in pricing.PRICING


def test_rates_for_known_model_returns_its_own_triple():
    p = pricing.load_pricing()
    assert pricing.rates_for("deepseek-v4-pro", p) == (0.435, 0.003625, 0.870)
    assert pricing.rates_for("MiniMax-M3", p) == (0.0, 0.0, 0.0)


def test_rates_for_unknown_model_falls_back_to_default():
    """A model id we never catalogued shouldn't crash the cost display — it
    should fall back to the default-key rates (which are honest for v4-pro
    and conservative everywhere else)."""
    p = pricing.load_pricing()
    assert pricing.rates_for("gpt-9000-turbo-ultra", p) == p[pricing.DEFAULT_PRICING_KEY]


def test_rates_for_none_falls_back_to_default():
    """A row whose `model` is NULL (very old rows pre-dating this column)
    shouldn't crash either."""
    p = pricing.load_pricing()
    assert pricing.rates_for(None, p) == p[pricing.DEFAULT_PRICING_KEY]


def test_pricing_json_round_trips(monkeypatch):
    monkeypatch.setenv("PRICING_JSON", '{"MiniMax-M3":[0.30,0.005,0.60]}')
    p = pricing.load_pricing()
    assert p["MiniMax-M3"] == (0.30, 0.005, 0.60)
    # Untouched entries still come from PRICING.
    assert p["deepseek-v4-pro"] == (0.435, 0.003625, 0.870)


def test_pricing_json_partial_override_merges_shallowly(monkeypatch):
    """An override that names one model leaves the others at PRICING defaults —
    critical so a typo in one PRICING entry doesn't unset every other model's
    rates (the JSON env var is per-model, not a wholesale replacement)."""
    monkeypatch.setenv("PRICING_JSON",
                       '{"deepseek-v4-pro":[0.5,0.01,1.0]}')
    p = pricing.load_pricing()
    assert p["deepseek-v4-pro"] == (0.5, 0.01, 1.0)
    assert p["deepseek-v4-flash"] == (0.140, 0.0028, 0.280)


def test_malformed_pricing_json_is_silently_ignored(monkeypatch, caplog):
    monkeypatch.setenv("PRICING_JSON", "not json at all")
    with caplog.at_level("WARNING", logger="pricing"):
        p = pricing.load_pricing()
    assert p == pricing.PRICING
    assert "not valid JSON" in caplog.text


def test_non_object_pricing_json_is_rejected(monkeypatch, caplog):
    """A JSON array, number, or string isn't a model->rates mapping; the
    override should be dropped (with a warning) rather than partially
    applied."""
    monkeypatch.setenv("PRICING_JSON", "[0.1, 0.2, 0.3]")
    with caplog.at_level("WARNING", logger="pricing"):
        p = pricing.load_pricing()
    assert p == pricing.PRICING


def test_pricing_json_individual_bad_entry_dropped(monkeypatch, caplog):
    """One bad entry doesn't poison the whole override — the rest still apply."""
    monkeypatch.setenv(
        "PRICING_JSON",
        json.dumps({"MiniMax-M3": "oops", "deepseek-v4-pro": [0.5, 0.5, 0.5]}),
    )
    with caplog.at_level("WARNING", logger="pricing"):
        p = pricing.load_pricing()
    # The good entry applied; the bad one was dropped.
    assert p["deepseek-v4-pro"] == (0.5, 0.5, 0.5)
    # MiniMax-M3 stays at its PRICING default (placeholders) because the
    # override entry was rejected.
    assert p["MiniMax-M3"] == pricing.PRICING["MiniMax-M3"]


def test_pricing_json_non_numeric_triple_dropped(monkeypatch):
    monkeypatch.setenv("PRICING_JSON", '{"MiniMax-M3":[0.3,"half",0.6]}')
    p = pricing.load_pricing()
    # All three need to be numeric; this entry is dropped wholesale.
    assert p["MiniMax-M3"] == pricing.PRICING["MiniMax-M3"]


def test_pricing_json_bool_is_not_a_number(monkeypatch):
    """bool subclasses int in Python, but `[true, true, true]` is clearly
    not a rate. The bool guard rejects it explicitly."""
    monkeypatch.setenv("PRICING_JSON", '{"MiniMax-M3":[true, true, true]}')
    p = pricing.load_pricing()
    assert p["MiniMax-M3"] == pricing.PRICING["MiniMax-M3"]


def test_blank_pricing_json_is_no_op(monkeypatch):
    monkeypatch.setenv("PRICING_JSON", "   ")
    p = pricing.load_pricing()
    assert p == pricing.PRICING


def test_pricing_json_creates_new_key(monkeypatch):
    """An override can name a model that isn't in PRICING — useful for
    adding a brand-new model without touching code."""
    monkeypatch.setenv("PRICING_JSON", '{"brand-new-model":[1.0,0.1,2.0]}')
    p = pricing.load_pricing()
    assert p["brand-new-model"] == (1.0, 0.1, 2.0)
    # Pre-existing entries still work.
    assert p["deepseek-v4-pro"] == (0.435, 0.003625, 0.870)


def test_load_pricing_does_not_mutate_module_PRICING(monkeypatch):
    """Critical: load_pricing() must not leak overrides back into the
    module-level PRICING dict, or tests that follow in the same process
    see state from earlier tests' env vars."""
    monkeypatch.setenv("PRICING_JSON", '{"MiniMax-M3":[0.9,0.01,1.8]}')
    pricing.load_pricing()
    assert pricing.PRICING["MiniMax-M3"] == (0.0, 0.0, 0.0)
