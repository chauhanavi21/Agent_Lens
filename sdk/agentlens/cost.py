"""
Cost estimation.

The important behaviour here isn't the prices — those go stale the moment
they're written. It's the distinction between **zero** and **unknown**.

An unrecognized model used to estimate at $0.00, which reads as "this step
was free" when it means "nobody priced this." A dashboard summing those
zeros reports a total that's confidently wrong, and the error is invisible
because $0.00 is a perfectly plausible number. So every estimate carries
where it came from, and anything unpriced is surfaced rather than absorbed.

Prices are USD per 1M tokens (input, output). Override per-client with
`AgentLens(cost_table={...})`, or globally with `AGENTLENS_COST_TABLE`
pointing at a JSON file — because a table baked into a release is a table
that's wrong by the next repricing.
"""

from __future__ import annotations

import json
import os
from typing import Optional

CostTable = "dict[str, tuple[float, float]]"

DEFAULT_COST_TABLE: dict[str, tuple[float, float]] = {
    # OpenAI
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "o3-mini": (1.10, 4.40),
    # Anthropic
    "claude-3-5-haiku": (0.80, 4.00),
    "claude-sonnet-4": (3.00, 15.00),
    "claude-opus-4": (15.00, 75.00),
    # Meta / open weights (typical hosted pricing)
    "llama-3.1-70b": (0.60, 0.80),
    "llama-3.1-8b": (0.10, 0.20),
    "mixtral-8x7b": (0.50, 0.50),
}

# Where a cost figure came from. Stored on the span so a total can say how
# much of itself it actually knows.
COST_REPORTED = "reported"  # the provider told us; trust it over any table
COST_TABLE = "table"  # matched a price entry
COST_UNPRICED = "unpriced"  # no entry — the 0.0 here is a gap, not a fact
COST_FREE = "free"  # priced at zero on purpose (local models)


def load_cost_table_from_env(env: Optional[dict] = None) -> dict:
    """
    Read extra prices from `AGENTLENS_COST_TABLE`: a path to a JSON file, or
    inline JSON. Shaped `{"model-substring": [input_per_1m, output_per_1m]}`.

    A malformed table is ignored rather than raised: prices are an
    enhancement, and failing an agent's startup over a typo in a pricing
    file would be a bad trade.
    """
    raw = (env if env is not None else os.environ).get("AGENTLENS_COST_TABLE", "").strip()
    if not raw:
        return {}

    try:
        if raw.startswith("{"):
            data = json.loads(raw)
        else:
            with open(raw, encoding="utf-8") as f:
                data = json.load(f)
        table = {}
        for model, prices in data.items():
            if isinstance(prices, (list, tuple)) and len(prices) == 2:
                table[str(model).lower()] = (float(prices[0]), float(prices[1]))
        return table
    except Exception:
        return {}


def lookup_price(model: str, cost_table: Optional[dict] = None) -> Optional[tuple]:
    """
    Find the price entry for a model, or None if nothing matches.

    Longest key first, so `gpt-4o-mini` doesn't get billed at `gpt-4o` rates
    — a 16x error that would look entirely plausible on a dashboard.
    """
    table = {**DEFAULT_COST_TABLE, **load_cost_table_from_env(), **(cost_table or {})}
    lowered = (model or "").lower()
    if not lowered:
        return None
    for key in sorted(table, key=len, reverse=True):
        if key in lowered:
            return table[key]
    return None


def estimate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cost_table: Optional[dict] = None,
    reported_cost: Optional[float] = None,
) -> tuple[float, str]:
    """
    Return `(cost_usd, source)`.

    A provider-reported cost always wins: it's authoritative, and a local
    table is a guess about someone else's billing.
    """
    if reported_cost is not None:
        try:
            return round(float(reported_cost), 6), COST_REPORTED
        except (TypeError, ValueError):
            pass

    price = lookup_price(model, cost_table)
    if price is None:
        # 0.0 with an "unpriced" marker, so a caller can tell this apart
        # from a step that genuinely cost nothing
        return 0.0, COST_UNPRICED

    input_price, output_price = price
    if input_price == 0 and output_price == 0:
        return 0.0, COST_FREE
    return (input_tokens * input_price + output_tokens * output_price) / 1_000_000, COST_TABLE


def estimate_cost_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cost_table: Optional[dict] = None,
) -> float:
    """Cost only. Kept for callers that don't need the provenance."""
    return estimate_cost(model, input_tokens, output_tokens, cost_table)[0]


def extract_reported_cost(result: object) -> Optional[float]:
    """
    Pull a cost the provider already calculated, if the response carries one.

    Several gateways (OpenRouter, LiteLLM, Helicone) return the real charge,
    which beats any local estimate.
    """
    if not isinstance(result, dict):
        usage = getattr(result, "usage", None)
        for holder in (result, usage):
            for attr in ("cost", "total_cost", "cost_usd"):
                value = getattr(holder, attr, None)
                if isinstance(value, (int, float)):
                    return float(value)
        return None

    for key in ("cost", "total_cost", "cost_usd"):
        if isinstance(result.get(key), (int, float)):
            return float(result[key])
    usage = result.get("usage")
    if isinstance(usage, dict):
        for key in ("cost", "total_cost", "cost_usd"):
            if isinstance(usage.get(key), (int, float)):
                return float(usage[key])
    return None
