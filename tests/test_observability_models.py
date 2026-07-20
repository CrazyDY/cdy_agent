from decimal import Decimal

import pytest

from cdy_agent.observability.models import (
    EstimatedCost,
    ModelCallSpan,
    TokenUsage,
    ToolCallSpan,
    TraceRecord,
)


def sample_trace() -> TraceRecord:
    usage = TokenUsage(10, 4)
    return TraceRecord(
        schema_version=1,
        trace_id="52c809c6-6e55-4ff1-9220-e4f90a4f6774",
        started_at="2026-07-20T08:30:00.000000Z",
        duration_ms=15,
        command="chat",
        status="succeeded",
        model="test-model",
        api_mode="responses",
        session_id="f8605a17-cf86-46ce-87ad-7db57533e5dc",
        error_type=None,
        usage=usage,
        estimated_cost=EstimatedCost(Decimal("0.000010"), Decimal("0.000008")),
        model_calls=(
            ModelCallSpan(
                "0cebd5c2-7d4c-4655-a997-f31e05eb74a5",
                1,
                8,
                "succeeded",
                None,
                usage,
            ),
        ),
        tool_calls=(
            ToolCallSpan(
                "89be39ea-9485-49f1-977f-70d5e663cf3d",
                1,
                "read_file",
                3,
                "succeeded",
                None,
            ),
        ),
    )


def test_trace_round_trip_uses_stable_json_values() -> None:
    record = sample_trace()
    payload = record.to_dict()
    assert payload["usage"] == {
        "input_tokens": 10,
        "output_tokens": 4,
        "total_tokens": 14,
    }
    assert payload["estimated_cost"] == {
        "input_cost": "0.000010",
        "output_cost": "0.000008",
        "total_cost": "0.000018",
    }
    assert TraceRecord.from_dict(payload) == record


@pytest.mark.parametrize(
    "change",
    [
        {"schema_version": 2},
        {"trace_id": "short"},
        {"duration_ms": -1},
        {"command": "eval"},
        {"status": "running"},
        {"usage": {"input_tokens": 10, "output_tokens": 4, "total_tokens": 99}},
    ],
)
def test_trace_rejects_invalid_or_inconsistent_payload(
    change: dict[str, object],
) -> None:
    payload = sample_trace().to_dict()
    payload.update(change)
    with pytest.raises(ValueError):
        TraceRecord.from_dict(payload)
