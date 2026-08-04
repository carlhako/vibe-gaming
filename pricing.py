"""pricing — per-model AI cost rates, single source of truth for /admin/stats
and the offline `agent_cost_report.py` script.

Each entry is a 3-tuple of USD per 1,000,000 tokens in the order
(input_rate, cached_input_rate, output_rate). Defaults below were checked
2026-07-27 against DeepSeek's published pricing (see
docs/multifile-agent/06a-cache-snapshot-and-edits.md for the pro cache-hit
figure's reasoning). Re-verify before trusting in new work — vendor pricing
is a vendor decision, not a constant.

Override any subset at runtime by setting the PRICING_JSON env var to a
JSON object whose keys are model names and values are [input, cached,
output] triples; e.g.

    PRICING_JSON='{"MiniMax-M3":[0.30,0.005,0.60]}'

Malformed entries (wrong shape, non-numeric) are dropped with a logged
warning rather than aborting — the rest of the table still applies, and
deployments never break because of a typo in a cost override.
"""

from __future__ import annotations

import json
import logging
import os

_logger = logging.getLogger(__name__)

# (input_rate, cached_input_rate, output_rate) USD per 1M tokens.
PRICING: dict[str, tuple[float, float, float]] = {
    "deepseek-v4-pro":   (0.435, 0.003625, 0.870),
    "deepseek-v4-flash": (0.140, 0.0028,   0.280),
    "MiniMax-M3":        (0.0,   0.0,     0.0),  # placeholder — fill in published rates
}

# Lookup key for rows whose `model` doesn't appear in PRICING (e.g. a brand
# new model id, or a typo in the recorded model field). Picked deliberately
# rather than raising, because /admin/stats' cost column degrades to a
# misleading blank cell on a KeyError — far worse than a slightly-wrong
# rate on an unrecognised row.
DEFAULT_PRICING_KEY = "deepseek-v4-pro"


def _load_pricing_overrides() -> dict[str, tuple[float, float, float]]:
    """Read PRICING_JSON (if set) and return a partial override dict.

    Empty/blank env var returns an empty dict (no override). Malformed
    JSON returns an empty dict with a warning. Entries that aren't a
    3-tuple of numbers are dropped individually with a warning so a single
    bad line doesn't poison the whole override.
    """
    raw = os.environ.get("PRICING_JSON", "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        _logger.warning(
            "PRICING_JSON is not valid JSON; falling back to PRICING defaults")
        return {}
    if not isinstance(data, dict):
        _logger.warning(
            "PRICING_JSON must be a JSON object mapping model -> [in, cached, out]; "
            "got %s — falling back to PRICING defaults",
            type(data).__name__,
        )
        return {}
    out: dict[str, tuple[float, float, float]] = {}
    for model, rates in data.items():
        if (isinstance(rates, (list, tuple)) and len(rates) == 3
                and all(isinstance(x, (int, float)) and not isinstance(x, bool)
                        for x in rates)):
            out[model] = (float(rates[0]), float(rates[1]), float(rates[2]))
        else:
            _logger.warning(
                "PRICING_JSON entry for %r is not a 3-tuple of numbers; ignored",
                model,
            )
    return out


def load_pricing() -> dict[str, tuple[float, float, float]]:
    """Return the full pricing table: PRICING overlaid with PRICING_JSON
    overrides. Cheap enough to call per request (three env reads + a JSON
    parse of a tiny blob), so no module-level cache."""
    overrides = _load_pricing_overrides()
    if not overrides:
        return dict(PRICING)
    merged = dict(PRICING)
    merged.update(overrides)
    return merged


def rates_for(model: str | None,
              pricing: dict[str, tuple[float, float, float]]
              ) -> tuple[float, float, float]:
    """Return (input_rate, cached_input_rate, output_rate) for `model`.
    Falls back to PRICING[DEFAULT_PRICING_KEY] when the model isn't in the
    table or is None — see DEFAULT_PRICING_KEY's comment for why.
    """
    return pricing.get(model or "") or pricing[DEFAULT_PRICING_KEY]
