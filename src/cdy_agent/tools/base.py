from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol


@dataclass(frozen=True)
class ToolCall:
    call_id: str
    name: str
    arguments_json: str


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    data: Any = None
    code: str | None = None
    message: str | None = None

    @classmethod
    def success(cls, data: Any) -> ToolResult:
        return cls(ok=True, data=data)

    @classmethod
    def failure(cls, code: str, message: str, data: Any = None) -> ToolResult:
        return cls(ok=False, data=data, code=code, message=message)

    def to_json(self) -> str:
        if self.ok:
            value = {"ok": True, "data": self.data}
        else:
            error = {"code": self.code, "message": self.message}
            if self.data is not None:
                error["data"] = self.data
            value = {"ok": False, "error": error}
        return json.dumps(value, ensure_ascii=False)


@dataclass(frozen=True)
class ConfirmationRequest:
    tool_name: str
    arguments: dict[str, Any]
    description: str
    allow_always: bool = False


class ConfirmationDecision(str, Enum):
    DENY = "deny"
    ALLOW_ONCE = "allow_once"
    ALLOW_ALWAYS = "allow_always"


@dataclass(frozen=True)
class PreparedToolExecution:
    """Bind confirmation, persistence, and execution to one preparation."""

    requires_confirmation: bool
    confirmation_description: str
    execute: Callable[[], ToolResult]
    remember_approval: Callable[[], ToolResult] | None = None


ConfirmationCallback = Callable[
    [ConfirmationRequest],
    bool | ConfirmationDecision,
]


class Tool(Protocol):
    name: str
    description: str
    parameters: dict[str, Any]
    requires_confirmation: bool

    def preflight(self, arguments: dict[str, Any]) -> ToolResult | None: ...
    def confirmation_description(self, arguments: dict[str, Any]) -> str: ...
    def execute(self, arguments: dict[str, Any]) -> ToolResult: ...
