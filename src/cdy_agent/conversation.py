"""In-memory conversation state for CDY Agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


MessageRole = Literal["user", "assistant"]
SUPPORTED_MESSAGE_ROLES = ("user", "assistant")


@dataclass(frozen=True)
class Message:
    """One normalized conversation message."""

    role: MessageRole
    content: str


@dataclass
class Conversation:
    """Store one ordered conversation in memory."""

    _messages: list[Message] = field(default_factory=list, init=False)

    @property
    def history(self) -> tuple[Message, ...]:
        """Return an immutable snapshot of the ordered messages."""
        return tuple(self._messages)

    def append(self, role: MessageRole, content: str) -> Message:
        """Normalize and append one supported, non-empty message."""
        if role not in SUPPORTED_MESSAGE_ROLES:
            raise ValueError(f"Unsupported message role: {role!r}.")

        normalized_content = content.strip()
        if not normalized_content:
            raise ValueError("Message must not be empty.")

        message = Message(role=role, content=normalized_content)
        self._messages.append(message)
        return message
