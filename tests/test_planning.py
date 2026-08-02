from collections.abc import Sequence

import pytest

from cdy_agent.conversation import Message
from cdy_agent.observability import TokenUsage
from cdy_agent.openai_client import (
    FinalResponse,
    ResponsesContinuation,
    ToolCallResponse,
)
from cdy_agent.planning import (
    MAX_PLAN_RESPONSE_LENGTH,
    MAX_SUBTASK_LENGTH,
    PlanningError,
    TaskPlanner,
    parse_task_plan,
)
from cdy_agent.tools.base import ToolCall


class FakeGateway:
    def __init__(self, outcomes: Sequence[object]) -> None:
        self.outcomes = iter(outcomes)
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        outcome = next(self.outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class SpyRecorder:
    def __init__(self, *, fail_start: bool = False) -> None:
        self.fail_start = fail_start
        self.events: list[tuple[object, ...]] = []
        self.invalidations = 0

    def start_model_call(self) -> int:
        if self.fail_start:
            raise RuntimeError("trace secret")
        self.events.append(("start",))
        return 1

    def finish_model_call(
        self,
        token: int,
        usage: TokenUsage | None,
        error: Exception | None = None,
    ) -> None:
        self.events.append(("finish", token, usage, error))

    def invalidate(self) -> None:
        self.invalidations += 1


def test_planner_classifies_simple_task_without_tools() -> None:
    gateway = FakeGateway(
        [FinalResponse('{"complexity":"simple","subtasks":[]}', TokenUsage(4, 2))]
    )
    recorder = SpyRecorder()

    plan = TaskPlanner(gateway).plan(
        [Message("system", "workspace secret"), Message("user", "Say hello")],
        recorder,
    )

    assert not plan.is_complex
    assert plan.subtasks == ()
    assert gateway.calls[0]["tools"] == ()
    assert gateway.calls[0]["continuation"] is None
    assert gateway.calls[0]["tool_outputs"] == ()
    sent_messages = gateway.calls[0]["messages"]
    assert isinstance(sent_messages, tuple)
    assert sent_messages[0].role == "system"
    assert "planning stage" in sent_messages[0].content
    assert Message("system", "workspace secret") not in sent_messages
    assert recorder.events == [
        ("start",),
        ("finish", 1, TokenUsage(4, 2), None),
    ]


def test_planner_accepts_fenced_complex_plan() -> None:
    gateway = FakeGateway(
        [
            FinalResponse(
                """```json
{"complexity":"complex","subtasks":["Inspect code","Implement change"]}
```"""
            )
        ]
    )

    plan = TaskPlanner(gateway).plan([Message("user", "Change the project")])

    assert plan.is_complex
    assert plan.subtasks == ("Inspect code", "Implement change")


@pytest.mark.parametrize(
    "reply, message",
    [
        ("not json", "invalid JSON"),
        (
            '{"complexity":"simple","subtasks":[],"reason":"short"}',
            "invalid decision schema",
        ),
        (
            '{"complexity":"simple","subtasks":["extra"]}',
            "cannot contain subtasks",
        ),
        (
            '{"complexity":"complex","subtasks":["only one"]}',
            "requires 2 to 8 subtasks",
        ),
        (
            '{"complexity":"complex","subtasks":["one",2]}',
            "must be text",
        ),
    ],
)
def test_parse_task_plan_rejects_invalid_decisions(reply: str, message: str) -> None:
    with pytest.raises(PlanningError, match=message):
        parse_task_plan(reply)


def test_parse_task_plan_bounds_subtask_length() -> None:
    reply = (
        '{"complexity":"complex","subtasks":["'
        + "x" * (MAX_SUBTASK_LENGTH + 1)
        + '","valid"]}'
    )

    with pytest.raises(PlanningError, match="1 to 500 characters"):
        parse_task_plan(reply)


def test_parse_task_plan_bounds_total_response_length() -> None:
    with pytest.raises(PlanningError, match="must not exceed 8192 characters"):
        parse_task_plan("x" * (MAX_PLAN_RESPONSE_LENGTH + 1))


def test_planner_rejects_unexpected_tool_call() -> None:
    gateway = FakeGateway(
        [
            ToolCallResponse(
                (ToolCall("1", "unexpected", "{}"),),
                ResponsesContinuation("next"),
            )
        ]
    )

    with pytest.raises(PlanningError, match="unexpected tool call"):
        TaskPlanner(gateway).plan([Message("user", "hello")])


def test_planner_records_provider_failure_and_reraises() -> None:
    error = RuntimeError("provider secret")
    recorder = SpyRecorder()

    with pytest.raises(RuntimeError) as raised:
        TaskPlanner(FakeGateway([error])).plan([Message("user", "hello")], recorder)

    assert raised.value is error
    assert recorder.events[0] == ("start",)
    assert recorder.events[1][0:3] == ("finish", 1, None)
    assert recorder.events[1][3] is error


def test_planner_trace_failure_does_not_change_result() -> None:
    gateway = FakeGateway(
        [FinalResponse('{"complexity":"simple","subtasks":[]}')]
    )
    recorder = SpyRecorder(fail_start=True)

    plan = TaskPlanner(gateway).plan([Message("user", "hello")], recorder)

    assert not plan.is_complex
    assert recorder.invalidations == 1
