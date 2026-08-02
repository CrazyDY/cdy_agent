"""Bounded task-complexity classification and planning."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from .conversation import Message
from .observability import TraceRecorder
from .openai_client import FinalResponse

MAX_SUBTASKS = 8
MAX_SUBTASK_LENGTH = 500
MAX_PLAN_RESPONSE_LENGTH = 8 * 1024

PLANNER_SYSTEM_PROMPT = """You are the planning stage of a local AI assistant.
Decide whether the user's latest request can be handled directly or requires an
ordered multi-step plan. Consider the available conversation context, but do not
answer the request and do not call tools.

A task is simple when it can be answered or completed as one coherent action.
A task is complex when it has multiple dependent actions, requires investigation
before modification, or needs separate implementation and verification steps.

Return JSON only, using exactly one of these shapes:
{"complexity":"simple","subtasks":[]}
{"complexity":"complex","subtasks":["first concrete task","second concrete task"]}

For a complex task, return 2 to 8 concise, executable subtasks in dependency
order. Do not include secrets, explanations, markdown, or fields other than
complexity and subtasks.
"""


class PlanningError(RuntimeError):
    """Raised when the planner does not return a safe, valid decision."""


@dataclass(frozen=True)
class TaskPlan:
    """One validated routing decision for the current user turn."""

    subtasks: tuple[str, ...] = ()

    @property
    def is_complex(self) -> bool:
        return bool(self.subtasks)


class Planner(Protocol):
    """Planning boundary consumed by the API-neutral Agent loop."""

    def plan(
        self,
        messages: Sequence[Message],
        recorder: TraceRecorder | None = None,
    ) -> TaskPlan: ...


class TaskPlanner:
    """Use one tool-free model call to classify and decompose a user turn."""

    def __init__(self, gateway: Any) -> None:
        self._gateway = gateway

    def plan(
        self,
        messages: Sequence[Message],
        recorder: TraceRecorder | None = None,
    ) -> TaskPlan:
        if not messages:
            raise ValueError("Conversation history must not be empty.")

        model_span = None
        active_recorder = recorder
        if active_recorder is not None:
            try:
                model_span = active_recorder.start_model_call()
            except Exception:
                _invalidate_recorder(active_recorder)
                active_recorder = None

        try:
            outcome = self._gateway.create(
                messages=_planner_messages(messages),
                tools=(),
                continuation=None,
                tool_outputs=(),
            )
        except Exception as exc:
            if active_recorder is not None and model_span is not None:
                try:
                    active_recorder.finish_model_call(model_span, None, exc)
                except Exception:
                    _invalidate_recorder(active_recorder)
            raise

        if active_recorder is not None and model_span is not None:
            try:
                active_recorder.finish_model_call(model_span, outcome.usage)
            except Exception:
                _invalidate_recorder(active_recorder)

        if not isinstance(outcome, FinalResponse):
            raise PlanningError("Planner returned an unexpected tool call.")
        return parse_task_plan(outcome.text)


def parse_task_plan(text: str) -> TaskPlan:
    """Parse and strictly validate one planner JSON response."""
    if not isinstance(text, str) or len(text) > MAX_PLAN_RESPONSE_LENGTH:
        raise PlanningError(
            f"Planner response must not exceed {MAX_PLAN_RESPONSE_LENGTH} characters."
        )
    candidate = _strip_json_fence(text)
    try:
        raw = json.loads(candidate)
    except (json.JSONDecodeError, TypeError):
        raise PlanningError("Planner returned invalid JSON.") from None

    if not isinstance(raw, dict) or set(raw) != {"complexity", "subtasks"}:
        raise PlanningError("Planner returned an invalid decision schema.")
    complexity = raw["complexity"]
    subtasks = raw["subtasks"]
    if complexity not in {"simple", "complex"} or not isinstance(subtasks, list):
        raise PlanningError("Planner returned an invalid decision schema.")

    normalized = []
    for subtask in subtasks:
        if not isinstance(subtask, str):
            raise PlanningError("Planner subtasks must be text.")
        value = subtask.strip()
        if not value or len(value) > MAX_SUBTASK_LENGTH:
            raise PlanningError(
                f"Planner subtasks must contain 1 to {MAX_SUBTASK_LENGTH} characters."
            )
        normalized.append(value)

    if complexity == "simple":
        if normalized:
            raise PlanningError("A simple planner decision cannot contain subtasks.")
        return TaskPlan()
    if not 2 <= len(normalized) <= MAX_SUBTASKS:
        raise PlanningError(
            f"A complex planner decision requires 2 to {MAX_SUBTASKS} subtasks."
        )
    return TaskPlan(tuple(normalized))


def execution_plan_message(plan: TaskPlan) -> Message:
    """Build the private execution instruction for a validated complex plan."""
    if not plan.is_complex:
        raise ValueError("A simple decision has no execution plan.")
    steps = "\n".join(
        f"{index}. {subtask}" for index, subtask in enumerate(plan.subtasks, 1)
    )
    return Message(
        "assistant",
        "I will execute this internal plan in order, completing each applicable "
        "subtask before giving one final answer. I will reassess safely if a tool "
        "fails and keep all confirmation and workspace security rules in force.\n\n"
        f"{steps}",
    )


def _planner_messages(messages: Sequence[Message]) -> tuple[Message, ...]:
    conversation = tuple(message for message in messages if message.role != "system")
    return (Message("system", PLANNER_SYSTEM_PROMPT), *conversation)


def _strip_json_fence(text: str) -> str:
    candidate = text.strip()
    if candidate.startswith("```json") and candidate.endswith("```"):
        return candidate[7:-3].strip()
    if candidate.startswith("```") and candidate.endswith("```"):
        return candidate[3:-3].strip()
    return candidate


def _invalidate_recorder(recorder: TraceRecorder) -> None:
    try:
        recorder.invalidate()
    except Exception:
        pass
