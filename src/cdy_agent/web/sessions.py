"""Authenticated HTTP adapters for persisted conversations."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response
from pydantic import TypeAdapter, ValidationError

from cdy_agent.memory import (
    ConversationStoreError,
    ConversationSummary,
    StoredConversation,
)
from cdy_agent.web.errors import SafeWebError, ServerBusyError, map_web_error
from cdy_agent.web.schemas import (
    BootstrapResponse,
    CanonicalUUID,
    ConversationMessageResponse,
    ConversationSummaryResponse,
    StoredConversationResponse,
)

if TYPE_CHECKING:
    from cdy_agent.web.app import WebDependencies, WebSettings


_CANONICAL_UUID = TypeAdapter(CanonicalUUID)
_INVALID_CONVERSATION_ID = SafeWebError(
    "invalid_conversation_id",
    "Conversation ID must be a complete canonical UUID.",
    False,
    400,
)
_SERVER_BUSY = SafeWebError(
    "server_busy", "Another turn is already running.", True, 409
)


def summary_response(item: ConversationSummary) -> ConversationSummaryResponse:
    """Convert a stored summary without exposing its domain representation."""
    return ConversationSummaryResponse(
        id=item.id,
        updated_at=item.updated_at,
        message_count=item.message_count,
        preview=item.preview,
    )


def conversation_response(item: StoredConversation) -> StoredConversationResponse:
    """Convert persisted conversation fields one by one for the HTTP protocol."""
    return StoredConversationResponse(
        id=item.id,
        created_at=item.created_at,
        updated_at=item.updated_at,
        messages=tuple(
            ConversationMessageResponse(role=message.role, content=message.content)
            for message in item.messages
        ),
    )


def create_sessions_router(
    settings: WebSettings, dependencies: WebDependencies
) -> APIRouter:
    """Build the small session router around startup-resolved dependencies."""
    router = APIRouter(prefix="/api")

    @router.get("/bootstrap", response_model=BootstrapResponse)
    def bootstrap() -> BootstrapResponse | JSONResponse:
        try:
            summaries = dependencies.conversation_store.list_summaries()
        except ConversationStoreError as error:
            return _safe_error_response(map_web_error(error))
        return BootstrapResponse(
            workspace_name=settings.workspace.name,
            workspace_path=str(settings.workspace),
            model=settings.model,
            api_mode=settings.api_mode,
            busy=dependencies.turn_coordinator.busy,
            conversations=tuple(summary_response(item) for item in summaries),
        )

    @router.get("/sessions/{session_id}", response_model=StoredConversationResponse)
    def load_session(session_id: str) -> StoredConversationResponse | JSONResponse:
        canonical_id = _validated_conversation_id(session_id)
        if canonical_id is None:
            return _safe_error_response(_INVALID_CONVERSATION_ID)
        try:
            return conversation_response(dependencies.conversation_store.load(canonical_id))
        except ConversationStoreError as error:
            return _safe_error_response(map_web_error(error))

    @router.delete("/sessions/{session_id}", status_code=204)
    def delete_session(session_id: str) -> Response:
        canonical_id = _validated_conversation_id(session_id)
        if canonical_id is None:
            return _safe_error_response(_INVALID_CONVERSATION_ID)
        try:
            dependencies.turn_coordinator.delete_session(canonical_id)
        except ServerBusyError:
            return _safe_error_response(_SERVER_BUSY)
        except ConversationStoreError as error:
            return _safe_error_response(map_web_error(error))
        return Response(status_code=204)

    return router


def _validated_conversation_id(value: str) -> str | None:
    try:
        return _CANONICAL_UUID.validate_python(value)
    except ValidationError:
        return None


def _safe_error_response(error: SafeWebError) -> JSONResponse:
    return JSONResponse(
        status_code=error.status_code,
        content={
            "code": error.code,
            "message": error.message,
            "retryable": error.retryable,
        },
    )
