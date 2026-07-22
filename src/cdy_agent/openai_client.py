"""Thin OpenAI-compatible Responses and Chat Completions boundary."""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Callable

from openai import OpenAI

from .conversation import Message
from .observability.models import TokenUsage
from .tools.base import ToolCall


class MissingAPIKeyError(RuntimeError):
    """Raised when the default OpenAI client has no configured API key."""


class StreamingToolCallUnsupported(RuntimeError):
    """Raised when a streaming response requests tools."""


@dataclass(frozen=True)
class FinalResponse:
    text: str
    usage: TokenUsage | None = None


@dataclass(frozen=True)
class ResponsesContinuation:
    response_id: str


@dataclass(frozen=True)
class ChatContinuation:
    calls: tuple[ToolCall, ...]
    content: str | None = None
    history: tuple[dict[str, Any], ...] = ()


@dataclass
class _StreamedToolCall:
    call_id: str | None = None
    name_parts: list[str] = field(default_factory=list)
    argument_parts: list[str] = field(default_factory=list)
    final_arguments: str | None = None


@dataclass(frozen=True)
class ToolCallResponse:
    calls: tuple[ToolCall, ...]
    continuation: ResponsesContinuation | ChatContinuation
    usage: TokenUsage | None = None


ModelResponse = FinalResponse | ToolCallResponse
Continuation = ResponsesContinuation | ChatContinuation
ToolDefinition = Mapping[str, object]


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
        tools: Sequence[ToolDefinition],
        continuation: Continuation | None = None,
        tool_outputs: Sequence[tuple[str, str]] = (),
    ) -> ModelResponse:
        if self.api_mode == "responses":
            return self._create_response(messages, tools, continuation, tool_outputs)
        return self._create_chat_completion(messages, tools, continuation, tool_outputs)

    def stream(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolDefinition],
        on_text: Callable[[str], None],
        continuation: Continuation | None = None,
        tool_outputs: Sequence[tuple[str, str]] = (),
    ) -> ModelResponse:
        if self.api_mode == "responses":
            return self._stream_response(
                messages, tools, on_text, continuation, tool_outputs
            )
        return self._stream_chat_completion(
            messages, tools, on_text, continuation, tool_outputs
        )

    def _create_response(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolDefinition],
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
            request["tools"] = list(tools)

        response = self.client.responses.create(**request)
        usage = _response_usage(response, "input_tokens", "output_tokens")
        output_items = _sdk_sequence(getattr(response, "output", ()))
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
            return ToolCallResponse(calls, ResponsesContinuation(response_id), usage)
        return _final_response(getattr(response, "output_text", None), usage)

    def _create_chat_completion(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolDefinition],
        continuation: Continuation | None,
        tool_outputs: Sequence[tuple[str, str]],
    ) -> ModelResponse:
        request_messages: list[dict[str, Any]] = _message_dicts(messages)
        if continuation is not None:
            if not isinstance(continuation, ChatContinuation):
                raise ValueError("Continuation does not match Chat Completions API mode.")
            request_messages.extend(continuation.history)
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
                    "name": tool["name"],
                    "description": tool["description"],
                    "parameters": tool["parameters"],
                },
            } for tool in tools]

        response = self.client.chat.completions.create(**request)
        usage = _response_usage(response, "prompt_tokens", "completion_tokens")
        choices = _sdk_sequence(getattr(response, "choices", ()))
        try:
            message = choices[0].message
        except (AttributeError, IndexError, KeyError, TypeError):
            return _final_response(None, usage)
        call_items = _sdk_sequence(
            getattr(message, "tool_calls", None), allow_none=True
        )
        calls = tuple(_chat_response_tool_call(item) for item in call_items)
        if calls:
            content = getattr(message, "content", None)
            if content is not None and not isinstance(content, str):
                raise RuntimeError("OpenAI returned an unsupported response.")
            history = tuple(request_messages[len(_message_dicts(messages)):])
            return ToolCallResponse(
                calls, ChatContinuation(calls, content, history), usage
            )
        return _final_response(getattr(message, "content", None), usage)

    def _stream_response(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolDefinition],
        on_text: Callable[[str], None],
        continuation: Continuation | None,
        tool_outputs: Sequence[tuple[str, str]],
    ) -> ModelResponse:
        request: dict[str, Any] = {"model": self.model, "stream": True}
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
            request["tools"] = list(tools)

        chunks: list[str] = []
        for event in self.client.responses.create(**request):
            event_type = getattr(event, "type", None)
            if event_type == "response.output_text.delta":
                delta = getattr(event, "delta", None)
                if not isinstance(delta, str):
                    raise RuntimeError("OpenAI returned an unsupported response.")
                if delta:
                    chunks.append(delta)
                    on_text(delta)
            elif event_type == "response.output_item.added":
                item = getattr(event, "item", None)
                if getattr(item, "type", None) == "function_call":
                    raise StreamingToolCallUnsupported(
                        "Streaming tool calls are not supported."
                    )
        return _final_response("".join(chunks))

    def _stream_chat_completion(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolDefinition],
        on_text: Callable[[str], None],
        continuation: Continuation | None,
        tool_outputs: Sequence[tuple[str, str]],
    ) -> ModelResponse:
        request_messages: list[dict[str, Any]] = _message_dicts(messages)
        if continuation is not None:
            if not isinstance(continuation, ChatContinuation):
                raise ValueError("Continuation does not match Chat Completions API mode.")
            request_messages.extend(continuation.history)
            request_messages.append({
                "role": "assistant",
                "content": continuation.content,
                "tool_calls": [_chat_tool_call(call) for call in continuation.calls],
            })
            request_messages.extend(
                {"role": "tool", "tool_call_id": call_id, "content": output}
                for call_id, output in tool_outputs
            )
        request: dict[str, Any] = {
            "model": self.model,
            "messages": request_messages,
            "stream": True,
        }
        if tools:
            request["tools"] = [{
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool["description"],
                    "parameters": tool["parameters"],
                },
            } for tool in tools]

        chunks: list[str] = []
        tool_call_parts: dict[int, _StreamedToolCall] = {}
        for event in self.client.chat.completions.create(**request):
            choices = _sdk_sequence(getattr(event, "choices", ()))
            for choice in choices:
                delta = getattr(choice, "delta", None)
                tool_calls = _sdk_sequence(
                    getattr(delta, "tool_calls", None), allow_none=True
                )
                for tool_delta in tool_calls:
                    index = getattr(tool_delta, "index", None)
                    if (
                        not isinstance(index, int)
                        or isinstance(index, bool)
                        or index < 0
                    ):
                        raise _unsupported_response()
                    part = tool_call_parts.setdefault(index, _StreamedToolCall())
                    _merge_chat_tool_delta(part, tool_delta)
                content = getattr(delta, "content", None)
                if content is None:
                    continue
                if not isinstance(content, str):
                    raise RuntimeError("OpenAI returned an unsupported response.")
                if content:
                    chunks.append(content)
                    on_text(content)
        calls = tuple(
            _tool_call(
                part.call_id,
                "".join(part.name_parts),
                "".join(part.argument_parts),
            )
            for _, part in sorted(tool_call_parts.items())
        )
        if calls:
            history = tuple(request_messages[len(_message_dicts(messages)):])
            content = "".join(chunks) or None
            return ToolCallResponse(calls, ChatContinuation(calls, content, history))
        return _final_response("".join(chunks))


def _message_dicts(messages: Sequence[Message]) -> list[dict[str, str]]:
    return [{"role": message.role, "content": message.content} for message in messages]


def _sdk_sequence(value: object, *, allow_none: bool = False) -> Sequence[Any]:
    if allow_none and value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise RuntimeError("OpenAI returned an unsupported response.")
    return value


def _unsupported_response() -> RuntimeError:
    return RuntimeError("OpenAI returned an unsupported response.")


def _merge_chat_tool_delta(
    part: _StreamedToolCall, tool_delta: object
) -> None:
    call_id = getattr(tool_delta, "id", None)
    if call_id is not None:
        if not isinstance(call_id, str) or not call_id.strip():
            raise _unsupported_response()
        if part.call_id is not None and part.call_id != call_id:
            raise _unsupported_response()
        part.call_id = call_id

    function = getattr(tool_delta, "function", None)
    if function is None:
        return
    name = getattr(function, "name", None)
    arguments = getattr(function, "arguments", None)
    if name is not None:
        if not isinstance(name, str):
            raise _unsupported_response()
        part.name_parts.append(name)
    if arguments is not None:
        if not isinstance(arguments, str):
            raise _unsupported_response()
        part.argument_parts.append(arguments)


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


def _response_usage(
    response: object, input_name: str, output_name: str
) -> TokenUsage | None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    input_tokens = getattr(usage, input_name, None)
    output_tokens = getattr(usage, output_name, None)
    try:
        return TokenUsage(input_tokens, output_tokens)
    except (TypeError, ValueError):
        raise RuntimeError("OpenAI returned an unsupported response.") from None


def _final_response(
    text: object, usage: TokenUsage | None = None
) -> FinalResponse:
    if not isinstance(text, str) or not text.strip():
        raise RuntimeError("OpenAI returned an unsupported response.")
    return FinalResponse(text, usage)


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
