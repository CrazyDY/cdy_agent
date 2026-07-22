"""Offline evaluation case loading and execution."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import yaml

from .conversation import Message


class EvalFileError(ValueError):
    """Raised when an eval case file cannot be loaded or validated."""


class EvalAgent(Protocol):
    def run(self, messages: Sequence[Message]) -> str:
        """Run one offline eval prompt through an agent-like object."""


@dataclass(frozen=True)
class EvalCaseResult:
    name: str
    passed: bool
    message: str
    reply: str


@dataclass(frozen=True)
class EvalReport:
    results: tuple[EvalCaseResult, ...]

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed(self) -> int:
        return sum(1 for result in self.results if result.passed)

    @property
    def failed(self) -> int:
        return self.total - self.passed


@dataclass(frozen=True)
class _EvalCase:
    name: str
    prompt: str
    exact: str | None
    contains: tuple[str, ...]


def run_eval_file(path: Path, agent: EvalAgent) -> EvalReport:
    """Run every case in one YAML or JSON eval file with an injected agent."""
    cases = _load_cases(path)
    results = []
    for case in cases:
        reply = agent.run((Message("user", case.prompt),))
        passed, message = _evaluate_reply(case, reply)
        results.append(EvalCaseResult(case.name, passed, message, reply))
    return EvalReport(tuple(results))


def _load_cases(path: Path) -> tuple[_EvalCase, ...]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise EvalFileError(f"Could not read eval file: {exc}") from None

    try:
        raw = json.loads(text) if path.suffix.lower() == ".json" else yaml.safe_load(text)
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise EvalFileError(f"Invalid eval file: {exc}") from None

    if not isinstance(raw, dict) or not isinstance(raw.get("cases"), list):
        raise EvalFileError("Eval file must contain a cases list.")
    return tuple(_parse_case(index, raw_case) for index, raw_case in enumerate(raw["cases"], 1))


def _parse_case(index: int, raw_case: object) -> _EvalCase:
    if not isinstance(raw_case, dict):
        raise EvalFileError(f"Eval case {index} must be a mapping.")
    name = _required_string(raw_case, "name", index)
    prompt = _required_string(raw_case, "prompt", index)
    expect = raw_case.get("expect")
    if not isinstance(expect, dict):
        raise EvalFileError(f"Eval case {index} expect must be a mapping.")

    exact = expect.get("exact")
    if exact is not None and not isinstance(exact, str):
        raise EvalFileError(f"Eval case {index} expect.exact must be text.")
    contains = _parse_contains(expect.get("contains"), index)
    if exact is None and not contains:
        raise EvalFileError(
            f"Eval case {index} expect must include exact or contains."
        )
    return _EvalCase(name, prompt, exact, contains)


def _required_string(raw_case: dict[str, Any], field: str, index: int) -> str:
    value = raw_case.get(field)
    if not isinstance(value, str) or not value.strip():
        raise EvalFileError(f"Eval case {index} {field} must be non-empty text.")
    return value.strip()


def _parse_contains(value: object, index: int) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        if not value.strip():
            raise EvalFileError(
                f"Eval case {index} expect.contains must be non-empty text."
            )
        return (value,)
    if not isinstance(value, list):
        raise EvalFileError(
            f"Eval case {index} expect.contains must be text or a list of text."
        )
    parts = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise EvalFileError(
                f"Eval case {index} expect.contains must contain non-empty text."
            )
        parts.append(item)
    return tuple(parts)


def _evaluate_reply(case: _EvalCase, reply: str) -> tuple[bool, str]:
    if case.exact is not None and reply != case.exact:
        return False, f"Expected exact reply {case.exact!r}."
    missing = tuple(part for part in case.contains if part not in reply)
    if missing:
        return False, f"Missing expected text: {', '.join(missing)}."
    return True, "passed"
