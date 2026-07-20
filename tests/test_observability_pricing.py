from decimal import Decimal

import pytest

from cdy_agent.observability.models import TokenUsage
from cdy_agent.observability.pricing import Pricing, estimate_cost, resolve_pricing


def test_resolve_pricing_and_estimate_exact_cost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CDY_AGENT_INPUT_COST_PER_MILLION", "1.25")
    monkeypatch.setenv("CDY_AGENT_OUTPUT_COST_PER_MILLION", "2.5")
    pricing = resolve_pricing()
    assert pricing == Pricing(Decimal("1.25"), Decimal("2.5"))
    cost = estimate_cost(TokenUsage(800, 200), pricing)
    assert cost is not None
    assert cost.input_cost == Decimal("0.00100")
    assert cost.output_cost == Decimal("0.0005")


def test_absent_pricing_keeps_cost_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CDY_AGENT_INPUT_COST_PER_MILLION", raising=False)
    monkeypatch.delenv("CDY_AGENT_OUTPUT_COST_PER_MILLION", raising=False)
    assert resolve_pricing() is None
    assert estimate_cost(TokenUsage(1, 1), None) is None


@pytest.mark.parametrize(
    ("input_price", "output_price"),
    [("1", None), (None, "2"), ("bad", "2"), ("-1", "2"), ("NaN", "2")],
)
def test_resolve_pricing_rejects_partial_or_invalid_values(
    monkeypatch: pytest.MonkeyPatch,
    input_price: str | None,
    output_price: str | None,
) -> None:
    for name, value in (
        ("CDY_AGENT_INPUT_COST_PER_MILLION", input_price),
        ("CDY_AGENT_OUTPUT_COST_PER_MILLION", output_price),
    ):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match="cost per million"):
        resolve_pricing()
