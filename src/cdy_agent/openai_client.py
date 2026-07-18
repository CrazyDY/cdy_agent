"""Thin OpenAI-compatible Responses and Chat Completions boundary."""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from openai import OpenAI

from .conversation import Message
from .tools.base import Tool, ToolCall


class MissingAPIKeyError(RuntimeError):
    """Raised when the default OpenAI client has no configured API key."""


@dataclass(frozen=True)
class FinalResponse:
    text: str


@dataclass(frozen=True)
class ResponsesContinuation:
    response_id: str


@dataclass(frozen=True)
class ChatContinuation:
    calls: tuple[ToolCall, ...]
    content: str | None = None


@dataclass(frozen=True)
class ToolCallResponse:
    calls: tuple[ToolCall, ...]
    continuation: ResponsesContinuation | ChatContinuation


ModelResponse = FinalResponse | ToolCallResponse
Continuation = ResponsesContinuation | ChatContinuation


class ModelGateway:
    """Normalize text and tool-call responses from both supported SDK APIs."""

    def __init__(
        self,
        *,
        model: str,
        api_mode: str,
        client: OpenAI | None = None,
    ) -> None:
        if api_mode not in {"responses", "chat_completions"}:
            raise ValueError(f"Unsupported API mode: {api_mode!r}.")
        if client is None:
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key or not api_key.strip():
                raise MissingAPIKeyError("OPENAI_API_KEY is required.")
            client = OpenAI()
        self.model = model
        self.api_mode = api_mode
        self.client = client

    def create(
        self,
        messages: Sequence[Message],
        tools: Sequence[Tool],
        continuation: Continuation | None = None,
        tool_outputs: Sequence[tuple[str, str]] = (),
    ) -> ModelResponse:
        if self.api_mode == "responses":
            return self._create_response(messages, tools, continuation, tool_outputs)
        return self._create_chat_completion(messages, tools, continuation, tool_outputs)

    def _create_response(
        self,
        messages: Sequence[Message],
        tools: Sequence[Tool],
        continuation: Continuation | None,
        tool_outputs: Sequence[tuple[str, str]],
    ) -> ModelResponse:
        request: dict[str, Any] = {"model": self.model}
        if continuation is None:
            request["input"] = _message_dicts(messages)
        else:
            if not isinstance(continuation, ResponsesContinuation):
                raise ValueError("Continuation does not match Responses API mode.")
            request["input"] = [
                {"type": "function_call_output", "call_id": call_id, "output": output}
                for call_id, output in tool_outputs
            ]
            request["previous_response_id"] = continuation.response_id
        if tools:
            request["tools"] = [
                {
                    "type": "function",
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                }
                for tool in tools
            ]

        response = self.client.responses.create(**request)
        try:
            output_items = tuple(getattr(response, "output", ()))
        except TypeError:
            raise RuntimeError("OpenAI returned an unsupported response.") from None
        calls = tuple(
            _tool_call(
                getattr(item, "call_id", None),
                getattr(item, "name", None),
                getattr(item, "arguments", None),
            )
            for item in output_items
            if getattr(item, "type", None) == "function_call"
        )
        if calls:
            response_id = getattr(response, "id", None)
            if not isinstance(response_id, str) or not response_id.strip():
                raise RuntimeError("OpenAI returned an unsupported response.")
            return ToolCallResponse(calls, ResponsesContinuation(response_id))
        return _final_response(getattr(response, "output_text", None))

    def _create_chat_completion(
        self,
        messages: Sequence[Message],
        tools: Sequence[Tool],
        continuation: Continuation | None,
        tool_outputs: Sequence[tuple[str, str]],
    ) -> ModelResponse:
        request_messages: list[dict[str, Any]] = _message_dicts(messages)
        if continuation is not None:
            if not isinstance(continuation, ChatContinuation):
                raise ValueError("Continuation does not match Chat Completions API mode.")
            request_messages.append({
                "role": "assistant",
                "content": continuation.content,
                "tool_calls": [_chat_tool_call(call) for call in continuation.calls],
            })
            request_messages.extend(
                {"role": "tool", "tool_call_id": call_id, "content": output}
                for call_id, output in tool_outputs
            )
        request: dict[str, Any] = {"model": self.model, "messages": request_messages}
        if tools:
            request["tools"] = [{
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            } for tool in tools]

        response = self.client.chat.completions.create(**request)
        try:
            message = response.choices[0].message
        except (AttributeError, IndexError, TypeError):
            return _final_response(None)
        raw_calls = getattr(message, "tool_calls", None) or ()
        try:
            call_items = tuple(raw_calls)
        except TypeError:
            raise RuntimeError("OpenAI returned an unsupported response.") from None
        calls = tuple(_chat_response_tool_call(item) for item in call_items)
        if calls:
            content = getattr(message, "content", None)
            if content is not None and not isinstance(content, str):
                raise RuntimeError("OpenAI returned an unsupported response.")
            return ToolCallResponse(calls, ChatContinuation(calls, content))
        return _final_response(getattr(message, "content", None))


def _message_dicts(messages: Sequence[Message]) -> list[dict[str, str]]:
    return [{"role": message.role, "content": message.content} for message in messages]


def _tool_call(call_id: object, name: object, arguments: object) -> ToolCall:
    if (
        not isinstance(call_id, str) or not call_id.strip()
        or not isinstance(name, str) or not name.strip()
        or not isinstance(arguments, str)
    ):
        raise RuntimeError("OpenAI returned an unsupported response.")
    return ToolCall(call_id, name, arguments)


def _chat_tool_call(call: ToolCall) -> dict[str, Any]:
    return {
        "id": call.call_id,
        "type": "function",
        "function": {"name": call.name, "arguments": call.arguments_json},
    }


def _chat_response_tool_call(item: object) -> ToolCall:
    function = getattr(item, "function", None)
    return _tool_call(
        getattr(item, "id", None),
        getattr(function, "name", None),
        getattr(function, "arguments", None),
    )


def _final_response(text: object) -> FinalResponse:
    if not isinstance(text, str) or not text.strip():
        raise RuntimeError("OpenAI returned an unsupported response.")
    return FinalResponse(text)


def generate_reply(
    prompt: str,
    *,
    model: str,
    api_mode: str,
    client: OpenAI | None = None,
) -> str:
    """Generate one non-empty text reply for a user prompt."""
    normalized_prompt = prompt.strip()
    if not normalized_prompt:
        raise ValueError("Prompt must not be empty.")

    return generate_reply_for_messages(
        (Message(role="user", content=normalized_prompt),),
        model=model,
        api_mode=api_mode,
        client=client,
    )


def generate_reply_for_messages(
    messages: Sequence[Message],
    *,
    model: str,
    api_mode: str,
    client: OpenAI | None = None,
) -> str:
    """Generate one reply from a complete, ordered message history."""
    if not messages:
        raise ValueError("Conversation history must not be empty.")
    gateway = ModelGateway(model=model, api_mode=api_mode, client=client)
    try:
        response = gateway.create(messages, ())
    except RuntimeError as error:
        if str(error) == "OpenAI returned an unsupported response.":
            raise RuntimeError("OpenAI returned an empty response.") from None
        raise
    if not isinstance(response, FinalResponse):
        raise RuntimeError("OpenAI returned an empty response.")
    return response.text
