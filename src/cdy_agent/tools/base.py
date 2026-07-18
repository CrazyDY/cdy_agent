from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Protocol


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
    def success(cls, data: Any) -> "ToolResult":
        return cls(ok=True, data=data)

    @classmethod
    def failure(cls, code: str, message: str) -> "ToolResult":
        return cls(ok=False, code=code, message=message)

    def to_json(self) -> str:
        value = {"ok": True, "data": self.data} if self.ok else {
            "ok": False,
            "error": {"code": self.code, "message": self.message},
        }
        return json.dumps(value, ensure_ascii=False)


@dataclass(frozen=True)
class ConfirmationRequest:
    tool_name: str
    arguments: dict[str, Any]
    description: str


ConfirmationCallback = Callable[[ConfirmationRequest], bool]


class Tool(Protocol):
    name: str
    description: str
    parameters: dict[str, Any]
    requires_confirmation: bool

    def preflight(self, arguments: dict[str, Any]) -> ToolResult | None: ...
    def confirmation_description(self, arguments: dict[str, Any]) -> str: ...
    def execute(self, arguments: dict[str, Any]) -> ToolResult: ...
