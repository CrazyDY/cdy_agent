from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from .models import EstimatedCost, TokenUsage

MILLION = Decimal(1_000_000)


@dataclass(frozen=True)
class Pricing:
    input_per_million: Decimal
    output_per_million: Decimal


def resolve_pricing() -> Pricing | None:
    raw_input = os.getenv("CDY_AGENT_INPUT_COST_PER_MILLION")
    raw_output = os.getenv("CDY_AGENT_OUTPUT_COST_PER_MILLION")
    if raw_input is None and raw_output is None:
        return None
    if raw_input is None or raw_output is None:
        raise ValueError(
            "Input and output cost per million must be configured together."
        )
    try:
        values = (Decimal(raw_input.strip()), Decimal(raw_output.strip()))
    except (InvalidOperation, ValueError):
        raise ValueError(
            "Token cost per million must be a non-negative decimal."
        ) from None
    if any(not value.is_finite() or value < 0 for value in values):
        raise ValueError("Token cost per million must be a non-negative decimal.")
    return Pricing(*values)


def estimate_cost(
    usage: TokenUsage, pricing: Pricing | None
) -> EstimatedCost | None:
    if pricing is None:
        return None
    return EstimatedCost(
        Decimal(usage.input_tokens) * pricing.input_per_million / MILLION,
        Decimal(usage.output_tokens) * pricing.output_per_million / MILLION,
    )
