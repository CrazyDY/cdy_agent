"""Validated HTTP and WebSocket records for the local Web interface."""

from __future__ import annotations

from typing import Annotated, Literal, TypeAlias
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StrictStr, TypeAdapter

from cdy_agent.tools.base import ConfirmationDecision

MAX_PROMPT_BYTES = 64 * 1024


def _canonical_uuid(value: str) -> str:
    try:
        parsed = UUID(value)
    except (ValueError, TypeError, AttributeError) as error:
        raise ValueError("Must be a complete canonical UUID.") from error
    if str(parsed) != value:
        raise ValueError("Must be a complete canonical UUID.")
    return value


def _valid_prompt(value: str) -> str:
    if not value.strip():
        raise ValueError("Prompt must not be blank.")
    if len(value.encode("utf-8")) > MAX_PROMPT_BYTES:
        raise ValueError("Prompt must not exceed 64 KiB of UTF-8 text.")
    return value


CanonicalUUID: TypeAlias = Annotated[StrictStr, AfterValidator(_canonical_uuid)]
Prompt: TypeAlias = Annotated[StrictStr, AfterValidator(_valid_prompt)]
NonEmptyText: TypeAlias = Annotated[StrictStr, Field(min_length=1)]


class WebSchema(BaseModel):
    """Base model that refuses fields outside the public Web protocol."""

    model_config = ConfigDict(extra="forbid")


class TurnStart(WebSchema):
    type: Literal["turn.start"]
    prompt: Prompt
    session_id: CanonicalUUID | None = None


class TurnCancel(WebSchema):
    type: Literal["turn.cancel"]
    turn_id: CanonicalUUID


class ApprovalResolve(WebSchema):
    type: Literal["approval.resolve"]
    turn_id: CanonicalUUID
    approval_id: CanonicalUUID
    decision: ConfirmationDecision


ClientEvent: TypeAlias = Annotated[
    TurnStart | TurnCancel | ApprovalResolve,
    Field(discriminator="type"),
]
CLIENT_EVENT_ADAPTER = TypeAdapter(ClientEvent)


def parse_client_event(payload: object) -> ClientEvent:
    """Parse exactly one client WebSocket event."""
    return CLIENT_EVENT_ADAPTER.validate_python(payload)


class TurnAccepted(WebSchema):
    type: Literal["turn.accepted"]
    turn_id: CanonicalUUID
    session_id: CanonicalUUID


class AssistantDelta(WebSchema):
    type: Literal["assistant.delta"]
    turn_id: CanonicalUUID
    delta: NonEmptyText


class ToolStatus(WebSchema):
    type: Literal["tool.status"]
    turn_id: CanonicalUUID
    name: NonEmptyText
    phase: Literal["started", "finished"]
    label: NonEmptyText


class ApprovalRequired(WebSchema):
    type: Literal["approval.required"]
    turn_id: CanonicalUUID
    approval_id: CanonicalUUID
    description: NonEmptyText
    allow_always: bool


class ConversationSummaryResponse(WebSchema):
    id: CanonicalUUID
    updated_at: NonEmptyText
    message_count: int = Field(ge=0)
    preview: NonEmptyText


class ConversationMessageResponse(WebSchema):
    role: Literal["user", "assistant"]
    content: NonEmptyText


class StoredConversationResponse(WebSchema):
    id: CanonicalUUID
    created_at: NonEmptyText
    updated_at: NonEmptyText
    messages: tuple[ConversationMessageResponse, ...]


class BootstrapResponse(WebSchema):
    workspace_name: NonEmptyText
    workspace_path: NonEmptyText
    model: NonEmptyText
    api_mode: Literal["responses", "chat_completions"]
    busy: bool
    conversations: tuple[ConversationSummaryResponse, ...]


class TurnCompleted(WebSchema):
    type: Literal["turn.completed"]
    turn_id: CanonicalUUID
    assistant_message: NonEmptyText
    conversation: ConversationSummaryResponse


class TurnFailed(WebSchema):
    type: Literal["turn.failed"]
    turn_id: CanonicalUUID
    code: NonEmptyText
    message: NonEmptyText
    retryable: bool


class TurnCancelled(WebSchema):
    type: Literal["turn.cancelled"]
    turn_id: CanonicalUUID


class ServerBusy(WebSchema):
    type: Literal["server.busy"]
    code: Literal["server_busy"] = "server_busy"
    message: NonEmptyText = "Another turn is already running."
    retryable: Literal[True] = True


class ProtocolError(WebSchema):
    type: Literal["protocol.error"]
    code: Literal["protocol_error"] = "protocol_error"
    message: NonEmptyText


ServerEvent: TypeAlias = Annotated[
    TurnAccepted
    | AssistantDelta
    | ToolStatus
    | ApprovalRequired
    | TurnCompleted
    | TurnFailed
    | TurnCancelled
    | ServerBusy
    | ProtocolError,
    Field(discriminator="type"),
]
