"""
Best-effort cost estimation for common models.
Prices are USD per 1M tokens (input, output). Override with
AgentLens(cost_table={...}) for anything not listed or out of date.
"""

from __future__ import annotations

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


def estimate_cost_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cost_table: dict[str, tuple[float, float]] | None = None,
) -> float:
    table = {**DEFAULT_COST_TABLE, **(cost_table or {})}
    match = None
    lowered = (model or "").lower()
    for key in sorted(table, key=len, reverse=True):
        if key in lowered:
            match = table[key]
            break
    if match is None:
        return 0.0
    in_price, out_price = match
    return (input_tokens * in_price + output_tokens * out_price) / 1_000_000
