from copy import copy
from decimal import Decimal

import pytest

from cdy_agent.observability import Pricing, TokenUsage, TraceRecorder


def test_recorder_aggregates_known_usage_and_cost() -> None:
    ticks = iter([10.000, 10.005, 10.007, 10.010, 10.015, 10.020])
    recorder = TraceRecorder(
        "ask",
        "model",
        "responses",
        pricing=Pricing(Decimal("1"), Decimal("2")),
        clock=lambda: next(ticks),
        now=lambda: "2026-07-20T08:30:00.000000Z",
    )
    first = recorder.start_model_call()
    recorder.finish_model_call(first, TokenUsage(100, 10))
    second = recorder.start_model_call()
    recorder.finish_model_call(second, None)
    record = recorder.finish()
    assert record.status == "succeeded"
    assert record.usage == TokenUsage(100, 10)
    assert record.estimated_cost.total_cost == Decimal("0.00012")
    assert [span.sequence for span in record.model_calls] == [1, 2]


def test_recorder_marks_failures_without_exception_messages() -> None:
    recorder = TraceRecorder(
        "chat",
        "model",
        "chat_completions",
        session_id="f8605a17-cf86-46ce-87ad-7db57533e5dc",
    )
    token = recorder.start_tool_call("read_file")
    recorder.finish_tool_call(token, ok=False, error_type="invalid_arguments")
    record = recorder.finish(RuntimeError("secret response body"))
    assert record.status == "failed"
    assert record.error_type == "RuntimeError"
    assert record.tool_calls[0].error_type == "invalid_arguments"
    assert "secret response body" not in str(record.to_dict())


def test_recorder_rejects_invalid_session_semantics() -> None:
    with pytest.raises(ValueError, match="ask traces"):
        TraceRecorder(
            "ask",
            "m",
            "responses",
            session_id="f8605a17-cf86-46ce-87ad-7db57533e5dc",
        )
    with pytest.raises(ValueError, match="session UUID"):
        TraceRecorder("chat", "m", "responses", session_id=None)


def test_recorder_can_finish_once_with_unknown_usage() -> None:
    recorder = TraceRecorder("ask", "m", "responses")
    token = recorder.start_model_call()
    recorder.finish_model_call(token, None)
    record = recorder.finish()
    assert record.usage is None
    assert record.estimated_cost is None
    with pytest.raises(RuntimeError, match="already finished"):
        recorder.finish()


def test_recorder_orders_overlapping_spans_by_start_sequence() -> None:
    recorder = TraceRecorder("ask", "m", "responses")
    first_model = recorder.start_model_call()
    second_model = recorder.start_model_call()
    recorder.finish_model_call(second_model, None)
    recorder.finish_model_call(first_model, None)
    first_tool = recorder.start_tool_call("first")
    second_tool = recorder.start_tool_call("second")
    recorder.finish_tool_call(second_tool, ok=True)
    recorder.finish_tool_call(first_tool, ok=True)

    record = recorder.finish()

    assert [span.sequence for span in record.model_calls] == [1, 2]
    assert [span.sequence for span in record.tool_calls] == [1, 2]


@pytest.mark.parametrize("error_type", ["secret response body", ""])
def test_recorder_rejects_tool_error_messages(error_type: str) -> None:
    recorder = TraceRecorder("ask", "m", "responses")
    token = recorder.start_tool_call("read_file")

    with pytest.raises(ValueError, match="error type"):
        recorder.finish_tool_call(
            token,
            ok=False,
            error_type=error_type,
        )


def test_recorder_accepts_tool_error_identifiers() -> None:
    recorder = TraceRecorder("ask", "m", "responses")
    first = recorder.start_tool_call("read_file")
    second = recorder.start_tool_call("read_file")
    recorder.finish_tool_call(first, ok=False, error_type="invalid_arguments")
    recorder.finish_tool_call(second, ok=False, error_type="RuntimeError")

    record = recorder.finish()

    assert [span.error_type for span in record.tool_calls] == [
        "invalid_arguments",
        "RuntimeError",
    ]


def test_recorder_rejects_span_work_after_finalization() -> None:
    recorder = TraceRecorder("ask", "m", "responses")
    model_token = recorder.start_model_call()
    tool_token = recorder.start_tool_call("read_file")
    recorder.finish()

    with pytest.raises(RuntimeError, match="already finished"):
        recorder.start_model_call()
    with pytest.raises(RuntimeError, match="already finished"):
        recorder.start_tool_call("read_file")
    with pytest.raises(RuntimeError, match="already finished"):
        recorder.finish_model_call(model_token, None)
    with pytest.raises(RuntimeError, match="already finished"):
        recorder.finish_tool_call(tool_token, ok=True)


def test_recorder_rejects_duplicate_span_completion() -> None:
    recorder = TraceRecorder("ask", "m", "responses")
    model_token = recorder.start_model_call()
    recorder.finish_model_call(model_token, None)
    tool_token = recorder.start_tool_call("read_file")
    recorder.finish_tool_call(tool_token, ok=True)

    with pytest.raises(ValueError, match="span token"):
        recorder.finish_model_call(model_token, None)
    with pytest.raises(ValueError, match="span token"):
        recorder.finish_tool_call(tool_token, ok=True)


def test_recorder_rejects_model_and_tool_token_swapping() -> None:
    recorder = TraceRecorder("ask", "m", "responses")
    model_token = recorder.start_model_call()
    tool_token = recorder.start_tool_call("read_file")

    with pytest.raises(ValueError, match="span token"):
        recorder.finish_model_call(tool_token, None)
    with pytest.raises(ValueError, match="span token"):
        recorder.finish_tool_call(model_token, ok=True)

    recorder.finish_model_call(model_token, None)
    recorder.finish_tool_call(tool_token, ok=True)


def test_recorder_rejects_tokens_from_another_recorder() -> None:
    first = TraceRecorder("ask", "m", "responses")
    second = TraceRecorder("ask", "m", "responses")
    model_token = first.start_model_call()
    tool_token = first.start_tool_call("read_file")

    with pytest.raises(ValueError, match="span token"):
        second.finish_model_call(model_token, None)
    with pytest.raises(ValueError, match="span token"):
        second.finish_tool_call(tool_token, ok=True)


def test_recorder_rejects_copied_tokens() -> None:
    recorder = TraceRecorder("ask", "m", "responses")
    model_token = recorder.start_model_call()
    tool_token = recorder.start_tool_call("read_file")

    with pytest.raises(ValueError, match="span token"):
        recorder.finish_model_call(copy(model_token), None)
    with pytest.raises(ValueError, match="span token"):
        recorder.finish_tool_call(copy(tool_token), ok=True)

    recorder.finish_model_call(model_token, None)
    recorder.finish_tool_call(tool_token, ok=True)
