from __future__ import annotations

import subprocess
from uuid import uuid4

import pytest
from pydantic import ValidationError

from cdy_agent.agent import AgentLoopLimitError
from cdy_agent.memory import ConversationNotFoundError
from cdy_agent.openai_client import MissingAPIKeyError
from cdy_agent.run_control import AgentRunCancelled
from cdy_agent.web.errors import map_web_error
from cdy_agent.web.schemas import (
    ApprovalResolve,
    AssistantDelta,
    BootstrapResponse,
    ConversationSummaryResponse,
    StoredConversationResponse,
    TurnAccepted,
    TurnStart,
    parse_client_event,
)


def test_turn_start_rejects_unknown_fields() -> None:
    """Adding an unrecognized client field must not silently alter server behavior."""
    with pytest.raises(ValidationError):
        TurnStart.model_validate(
            {"type": "turn.start", "prompt": "hello", "workspace": "C:\\"}
        )


def test_turn_start_rejects_whitespace_only_prompt() -> None:
    """Removing prompt validation would permit an unusable empty Agent turn."""
    with pytest.raises(ValidationError):
        TurnStart.model_validate({"type": "turn.start", "prompt": " \t\n"})


def test_turn_start_limits_prompt_by_utf8_bytes() -> None:
    """Replacing byte measurement with character measurement would bypass the limit."""
    with pytest.raises(ValidationError):
        TurnStart.model_validate({"type": "turn.start", "prompt": "é" * 32769})


def test_turn_start_accepts_exactly_64_kib_prompt() -> None:
    event = TurnStart.model_validate(
        {"type": "turn.start", "prompt": "é" * 32768}
    )

    assert len(event.prompt.encode("utf-8")) == 64 * 1024


def test_turn_start_rejects_noncanonical_session_id() -> None:
    """Weak UUID parsing would allow alternate IDs past the protocol boundary."""
    session_id = str(uuid4()).upper()

    with pytest.raises(ValidationError):
        TurnStart.model_validate(
            {"type": "turn.start", "prompt": "hello", "session_id": session_id}
        )


def test_approval_resolve_accepts_exact_decisions() -> None:
    event = ApprovalResolve.model_validate(
        {
            "type": "approval.resolve",
            "turn_id": str(uuid4()),
            "approval_id": str(uuid4()),
            "decision": "allow_always",
        }
    )

    assert event.decision.value == "allow_always"


def test_approval_resolve_rejects_unknown_decision() -> None:
    """A decision outside the existing confirmation enum must fail closed."""
    with pytest.raises(ValidationError):
        ApprovalResolve.model_validate(
            {
                "type": "approval.resolve",
                "turn_id": str(uuid4()),
                "approval_id": str(uuid4()),
                "decision": "approve_everything",
            }
        )


def test_assistant_delta_rejects_empty_text() -> None:
    """An empty delta would create a meaningless streaming protocol event."""
    with pytest.raises(ValidationError):
        AssistantDelta.model_validate(
            {"type": "assistant.delta", "turn_id": str(uuid4()), "delta": ""}
        )


def test_parse_client_event_selects_discriminated_type() -> None:
    event = parse_client_event({"type": "turn.start", "prompt": "hello"})

    assert isinstance(event, TurnStart)


def test_parse_client_event_rejects_unknown_discriminator() -> None:
    """Dropping the discriminator would make malformed protocol messages ambiguous."""
    with pytest.raises(ValidationError):
        parse_client_event({"type": "turn.restart", "prompt": "hello"})


def test_http_records_validate_nested_canonical_ids_and_forbid_unknown_fields() -> None:
    session_id = str(uuid4())
    summary = ConversationSummaryResponse.model_validate(
        {
            "id": session_id,
            "updated_at": "2026-07-30T12:00:00.000000Z",
            "message_count": 2,
            "preview": "Hello",
        }
    )
    response = BootstrapResponse.model_validate(
        {
            "workspace_name": "project",
            "workspace_path": "C:/project",
            "model": "test-model",
            "api_mode": "responses",
            "busy": False,
            "conversations": [summary.model_dump()],
        }
    )
    stored = StoredConversationResponse.model_validate(
        {
            "id": session_id,
            "created_at": "2026-07-30T12:00:00.000000Z",
            "updated_at": "2026-07-30T12:00:00.000000Z",
            "messages": [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi"},
            ],
        }
    )

    assert response.conversations == (summary,)
    assert stored.id == session_id
    with pytest.raises(ValidationError):
        TurnAccepted.model_validate(
            {
                "type": "turn.accepted",
                "turn_id": str(uuid4()),
                "session_id": session_id,
                "debug": True,
            }
        )


@pytest.mark.parametrize(
    ("error", "code", "status_code", "retryable"),
    [
        (ConversationNotFoundError("private detail"), "conversation_not_found", 404, False),
        (AgentRunCancelled("private detail"), "turn_cancelled", 409, False),
        (AgentLoopLimitError("private detail"), "model_call_limit", 503, True),
        (MissingAPIKeyError("private detail"), "provider_unavailable", 503, True),
        (subprocess.TimeoutExpired(["private"], 1), "process_timeout", 504, True),
    ],
)
def test_map_web_error_maps_known_exceptions_to_safe_fixed_records(
    error: BaseException, code: str, status_code: int, retryable: bool
) -> None:
    mapped = map_web_error(error)

    assert mapped.code == code
    assert mapped.status_code == status_code
    assert mapped.retryable is retryable
    assert "private detail" not in mapped.message


def test_map_web_error_hides_unexpected_exception_text() -> None:
    mapped = map_web_error(RuntimeError("credential=secret"))

    assert mapped.code == "internal_error"
    assert mapped.message == "The turn failed safely."
    assert mapped.retryable is False
    assert mapped.status_code == 500
