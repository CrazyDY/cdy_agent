from types import SimpleNamespace
from typing import Any

import pytest

from cdy_agent import openai_client
from cdy_agent.conversation import Message
from cdy_agent.observability.models import TokenUsage
from cdy_agent.openai_client import generate_reply
from cdy_agent.tools.base import ToolCall


class FakeTool:
    name = "read_file"
    description = "Read a file."
    parameters = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    }
    requires_confirmation = False

    def confirmation_description(self, arguments: dict[str, Any]) -> str:
        return ""

    def execute(self, arguments: dict[str, Any]) -> object:
        raise NotImplementedError


TOOL_DEFINITIONS = ({
    "type": "function",
    "name": FakeTool.name,
    "description": FakeTool.description,
    "parameters": FakeTool.parameters,
},)


class FakeResponses:
    def __init__(self, output_text: str | None) -> None:
        self.output_text = output_text
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(id="response-1", output_text=self.output_text, output=[])


class FakeCompletions:
    def __init__(self, output_text: object) -> None:
        self.output_text = output_text
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        message = SimpleNamespace(content=self.output_text, tool_calls=[])
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class FakeClient:
    def __init__(
        self,
        responses_output: str | None = "unused",
        chat_output: object = "unused",
    ) -> None:
        self.responses = FakeResponses(responses_output)
        self.chat = SimpleNamespace(
            completions=FakeCompletions(chat_output),
        )


def test_gateway_normalizes_responses_usage() -> None:
    client = FakeClient()
    client.responses.create = FakeResponsesSequence(SimpleNamespace(
        id="response-1", output_text="Done", output=[],
        usage=SimpleNamespace(input_tokens=12, output_tokens=3),
    ))
    outcome = openai_client.ModelGateway(model="m", api_mode="responses", client=client).create(
        (Message("user", "secret prompt"),), ()
    )
    assert outcome == openai_client.FinalResponse("Done", TokenUsage(12, 3))


def test_responses_gateway_streams_text_deltas() -> None:
    client = FakeClient()
    client.responses.create = FakeStream(
        SimpleNamespace(type="response.output_text.delta", delta="Hel"),
        SimpleNamespace(type="response.output_text.delta", delta="lo"),
        SimpleNamespace(
            type="response.completed",
            response=SimpleNamespace(id="response-1"),
        ),
    )
    chunks: list[str] = []

    result = openai_client.ModelGateway(
        model="m", api_mode="responses", client=client
    ).stream((Message("user", "Hello"),), (), chunks.append)

    assert result == openai_client.FinalResponse("Hello")
    assert chunks == ["Hel", "lo"]
    assert client.responses.create.calls == [
        {
            "model": "m",
            "input": [{"role": "user", "content": "Hello"}],
            "stream": True,
        }
    ]


def test_chat_gateway_streams_text_deltas() -> None:
    client = FakeClient()
    client.chat.completions.create = FakeStream(
        SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(content="Hel"), finish_reason=None
                )
            ]
        ),
        SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(content="lo"), finish_reason="stop"
                )
            ]
        ),
    )
    chunks: list[str] = []

    result = openai_client.ModelGateway(
        model="m", api_mode="chat_completions", client=client
    ).stream((Message("user", "Hello"),), (), chunks.append)

    assert result == openai_client.FinalResponse("Hello")
    assert chunks == ["Hel", "lo"]
    assert client.chat.completions.create.calls == [
        {
            "model": "m",
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": True,
            "stream_options": {"include_usage": True},
        }
    ]


def test_chat_gateway_aggregates_streamed_tool_call_deltas() -> None:
    client = FakeClient()
    client.chat.completions.create = FakeStream(
        SimpleNamespace(choices=[SimpleNamespace(
            delta=SimpleNamespace(content=None, tool_calls=[SimpleNamespace(
                index=0,
                id="call-1",
                function=SimpleNamespace(name="read_", arguments='{"pa'),
            )]),
            finish_reason=None,
        )]),
        SimpleNamespace(choices=[SimpleNamespace(
            delta=SimpleNamespace(content=None, tool_calls=[SimpleNamespace(
                index=0,
                id=None,
                function=SimpleNamespace(name="file", arguments='th":"a"}'),
            )]),
            finish_reason="tool_calls",
        )]),
    )
    chunks: list[str] = []

    outcome = openai_client.ModelGateway(
        model="m", api_mode="chat_completions", client=client
    ).stream((Message("user", "Read a"),), TOOL_DEFINITIONS, chunks.append)

    calls = (ToolCall("call-1", "read_file", '{"path":"a"}'),)
    assert outcome == openai_client.ToolCallResponse(
        calls,
        openai_client.ChatContinuation(calls, None, ()),
    )
    assert chunks == []


def test_chat_gateway_orders_interleaved_streamed_tool_calls_by_index() -> None:
    client = FakeClient()
    client.chat.completions.create = FakeStream(
        SimpleNamespace(choices=[SimpleNamespace(
            delta=SimpleNamespace(tool_calls=[SimpleNamespace(
                index=1,
                id="call-2",
                function=SimpleNamespace(name="read_", arguments='{"path":"'),
            )]),
            finish_reason=None,
        )]),
        SimpleNamespace(choices=[SimpleNamespace(
            delta=SimpleNamespace(tool_calls=[SimpleNamespace(
                index=0,
                id="call-1",
                function=SimpleNamespace(name="read_", arguments='{"path":"'),
            )]),
            finish_reason=None,
        )]),
        SimpleNamespace(choices=[SimpleNamespace(
            delta=SimpleNamespace(tool_calls=[SimpleNamespace(
                index=1,
                id=None,
                function=SimpleNamespace(name="file", arguments='b"}'),
            )]),
            finish_reason=None,
        )]),
        SimpleNamespace(choices=[SimpleNamespace(
            delta=SimpleNamespace(tool_calls=[SimpleNamespace(
                index=0,
                id=None,
                function=SimpleNamespace(name="file", arguments='a"}'),
            )]),
            finish_reason="tool_calls",
        )]),
    )

    outcome = openai_client.ModelGateway(
        model="m", api_mode="chat_completions", client=client
    ).stream((Message("user", "Read files"),), TOOL_DEFINITIONS, lambda _: None)

    assert outcome == openai_client.ToolCallResponse(
        (
            ToolCall("call-1", "read_file", '{"path":"a"}'),
            ToolCall("call-2", "read_file", '{"path":"b"}'),
        ),
        openai_client.ChatContinuation(
            (
                ToolCall("call-1", "read_file", '{"path":"a"}'),
                ToolCall("call-2", "read_file", '{"path":"b"}'),
            ),
            None,
            (),
        ),
    )


@pytest.mark.parametrize(
    "events",
    [
        (
            SimpleNamespace(choices=[SimpleNamespace(
                delta=SimpleNamespace(tool_calls=[SimpleNamespace(
                    index=-1,
                    id="call-1",
                    function=SimpleNamespace(name="read_file", arguments="{}"),
                )]),
                finish_reason="tool_calls",
            )]),
        ),
        (
            SimpleNamespace(choices=[SimpleNamespace(
                delta=SimpleNamespace(tool_calls=[SimpleNamespace(
                    index="0",
                    id="call-1",
                    function=SimpleNamespace(name="read_file", arguments="{}"),
                )]),
                finish_reason="tool_calls",
            )]),
        ),
        (
            SimpleNamespace(choices=[SimpleNamespace(
                delta=SimpleNamespace(tool_calls=[SimpleNamespace(
                    index=0,
                    id="call-1",
                    function=SimpleNamespace(name="read_file", arguments="{"),
                )]),
                finish_reason=None,
            )]),
            SimpleNamespace(choices=[SimpleNamespace(
                delta=SimpleNamespace(tool_calls=[SimpleNamespace(
                    index=0,
                    id="call-2",
                    function=SimpleNamespace(name=None, arguments="}"),
                )]),
                finish_reason="tool_calls",
            )]),
        ),
        (
            SimpleNamespace(choices=[SimpleNamespace(
                delta=SimpleNamespace(tool_calls=[SimpleNamespace(
                    index=0,
                    id=None,
                    function=SimpleNamespace(name="read_file", arguments="{}"),
                )]),
                finish_reason="tool_calls",
            )]),
        ),
        (
            SimpleNamespace(choices=[SimpleNamespace(
                delta=SimpleNamespace(tool_calls=[SimpleNamespace(
                    index=0,
                    id="call-1",
                    function=SimpleNamespace(name=None, arguments="{}"),
                )]),
                finish_reason="tool_calls",
            )]),
        ),
        (
            SimpleNamespace(choices=[SimpleNamespace(
                delta=SimpleNamespace(tool_calls=[SimpleNamespace(
                    index=0,
                    id="call-1",
                    function=SimpleNamespace(name="read_file", arguments=42),
                )]),
                finish_reason="tool_calls",
            )]),
        ),
    ],
)
def test_chat_gateway_rejects_malformed_streamed_tool_call_completion(
    events: tuple[SimpleNamespace, ...],
) -> None:
    client = FakeClient()
    client.chat.completions.create = FakeStream(*events)

    with pytest.raises(RuntimeError, match=r"OpenAI returned an unsupported response\."):
        openai_client.ModelGateway(
            model="m", api_mode="chat_completions", client=client
        ).stream((Message("user", "Read a file"),), TOOL_DEFINITIONS, lambda _: None)


def test_chat_gateway_rejects_tool_call_finished_for_length() -> None:
    client = FakeClient()
    client.chat.completions.create = FakeStream(SimpleNamespace(
        choices=[SimpleNamespace(
            delta=SimpleNamespace(tool_calls=[SimpleNamespace(
                index=0,
                id="call-1",
                function=SimpleNamespace(name="read_file", arguments="{}"),
            )]),
            finish_reason="length",
        )],
    ))

    with pytest.raises(RuntimeError, match=r"OpenAI returned an unsupported response\."):
        openai_client.ModelGateway(
            model="m", api_mode="chat_completions", client=client
        ).stream((Message("user", "Read a file"),), TOOL_DEFINITIONS, lambda _: None)


def test_chat_gateway_rejects_tool_call_without_terminal_reason() -> None:
    client = FakeClient()
    client.chat.completions.create = FakeStream(SimpleNamespace(
        choices=[SimpleNamespace(
            delta=SimpleNamespace(tool_calls=[SimpleNamespace(
                index=0,
                id="call-1",
                function=SimpleNamespace(name="read_file", arguments="{}"),
            )]),
            finish_reason=None,
        )],
    ))

    with pytest.raises(RuntimeError, match=r"OpenAI returned an unsupported response\."):
        openai_client.ModelGateway(
            model="m", api_mode="chat_completions", client=client
        ).stream((Message("user", "Read a file"),), TOOL_DEFINITIONS, lambda _: None)


@pytest.mark.parametrize(
    "events",
    [
        (
            SimpleNamespace(choices=[SimpleNamespace(
                delta=SimpleNamespace(content="partial"),
                finish_reason="content_filter",
            )]),
        ),
        (
            SimpleNamespace(choices=[SimpleNamespace(
                delta=SimpleNamespace(content="partial"),
                finish_reason="function_call",
            )]),
        ),
        (
            SimpleNamespace(choices=[SimpleNamespace(
                delta=SimpleNamespace(content="done"),
                finish_reason="stop",
            )]),
            SimpleNamespace(choices=[SimpleNamespace(
                delta=SimpleNamespace(content=None),
                finish_reason="tool_calls",
            )]),
        ),
    ],
)
def test_chat_gateway_rejects_unsupported_or_conflicting_terminal_reasons(
    events: tuple[SimpleNamespace, ...],
) -> None:
    client = FakeClient()
    client.chat.completions.create = FakeStream(*events)

    with pytest.raises(RuntimeError, match=r"OpenAI returned an unsupported response\."):
        openai_client.ModelGateway(
            model="m", api_mode="chat_completions", client=client
        ).stream((Message("user", "Hello"),), (), lambda _: None)


def test_chat_text_stream_captures_usage_from_empty_choice_chunk() -> None:
    client = FakeClient()
    client.chat.completions.create = FakeStream(
        SimpleNamespace(
            choices=[SimpleNamespace(
                delta=SimpleNamespace(content="done"), finish_reason="stop"
            )],
            usage=None,
        ),
        SimpleNamespace(
            choices=[],
            usage=SimpleNamespace(prompt_tokens=5, completion_tokens=1),
        ),
    )

    outcome = openai_client.ModelGateway(
        model="m", api_mode="chat_completions", client=client
    ).stream((Message("user", "Hello"),), (), lambda _: None)

    assert outcome == openai_client.FinalResponse("done", TokenUsage(5, 1))
    assert client.chat.completions.create.calls[0]["stream_options"] == {
        "include_usage": True
    }


def test_chat_tool_call_stream_captures_usage_from_empty_choice_chunk() -> None:
    client = FakeClient()
    client.chat.completions.create = FakeStream(
        SimpleNamespace(
            choices=[SimpleNamespace(
                delta=SimpleNamespace(tool_calls=[SimpleNamespace(
                    index=0,
                    id="call-1",
                    function=SimpleNamespace(name="read_file", arguments="{}"),
                )]),
                finish_reason="tool_calls",
            )],
            usage=None,
        ),
        SimpleNamespace(
            choices=[],
            usage=SimpleNamespace(prompt_tokens=8, completion_tokens=2),
        ),
    )

    outcome = openai_client.ModelGateway(
        model="m", api_mode="chat_completions", client=client
    ).stream((Message("user", "Read a file"),), TOOL_DEFINITIONS, lambda _: None)

    calls = (ToolCall("call-1", "read_file", "{}"),)
    assert outcome == openai_client.ToolCallResponse(
        calls,
        openai_client.ChatContinuation(calls, None, ()),
        TokenUsage(8, 2),
    )


def test_chat_gateway_rejects_conflicting_streamed_usage() -> None:
    client = FakeClient()
    client.chat.completions.create = FakeStream(
        SimpleNamespace(
            choices=[SimpleNamespace(
                delta=SimpleNamespace(content="done"), finish_reason="stop"
            )],
            usage=SimpleNamespace(prompt_tokens=5, completion_tokens=1),
        ),
        SimpleNamespace(
            choices=[],
            usage=SimpleNamespace(prompt_tokens=6, completion_tokens=1),
        ),
    )

    with pytest.raises(RuntimeError, match=r"OpenAI returned an unsupported response\."):
        openai_client.ModelGateway(
            model="m", api_mode="chat_completions", client=client
        ).stream((Message("user", "Hello"),), (), lambda _: None)


@pytest.mark.parametrize(
    "later_choice",
    [
        SimpleNamespace(
            delta=SimpleNamespace(tool_calls=[SimpleNamespace(
                index=0,
                id=None,
                function=SimpleNamespace(name=None, arguments="late"),
            )]),
            finish_reason=None,
        ),
        SimpleNamespace(
            delta=SimpleNamespace(content="late"), finish_reason=None
        ),
    ],
)
def test_chat_gateway_rejects_deltas_after_tool_calls_terminal(
    later_choice: SimpleNamespace,
) -> None:
    client = FakeClient()
    client.chat.completions.create = FakeStream(
        SimpleNamespace(choices=[SimpleNamespace(
            delta=SimpleNamespace(tool_calls=[SimpleNamespace(
                index=0,
                id="call-1",
                function=SimpleNamespace(name="read_file", arguments="{}"),
            )]),
            finish_reason="tool_calls",
        )]),
        SimpleNamespace(choices=[later_choice]),
    )

    with pytest.raises(RuntimeError, match=r"OpenAI returned an unsupported response\."):
        openai_client.ModelGateway(
            model="m", api_mode="chat_completions", client=client
        ).stream((Message("user", "Read a file"),), TOOL_DEFINITIONS, lambda _: None)


def test_chat_gateway_rejects_empty_chunk_without_usage_after_terminal() -> None:
    client = FakeClient()
    client.chat.completions.create = FakeStream(
        SimpleNamespace(choices=[SimpleNamespace(
            delta=SimpleNamespace(content="done"), finish_reason="stop"
        )]),
        SimpleNamespace(choices=[], usage=None),
    )

    with pytest.raises(RuntimeError, match=r"OpenAI returned an unsupported response\."):
        openai_client.ModelGateway(
            model="m", api_mode="chat_completions", client=client
        ).stream((Message("user", "Hello"),), (), lambda _: None)


def test_chat_gateway_rejects_call_id_reused_across_indexes() -> None:
    client = FakeClient()
    client.chat.completions.create = FakeStream(SimpleNamespace(
        choices=[SimpleNamespace(
            delta=SimpleNamespace(tool_calls=[
                SimpleNamespace(
                    index=0,
                    id="call-shared",
                    function=SimpleNamespace(name="read_file", arguments="{}"),
                ),
                SimpleNamespace(
                    index=1,
                    id="call-shared",
                    function=SimpleNamespace(name="read_file", arguments="{}"),
                ),
            ]),
            finish_reason="tool_calls",
        )],
    ))

    with pytest.raises(RuntimeError, match=r"OpenAI returned an unsupported response\."):
        openai_client.ModelGateway(
            model="m", api_mode="chat_completions", client=client
        ).stream((Message("user", "Read files"),), TOOL_DEFINITIONS, lambda _: None)


def test_responses_gateway_aggregates_streamed_function_call() -> None:
    client = FakeClient()
    client.responses.create = FakeStream(
        SimpleNamespace(
            type="response.created",
            response=SimpleNamespace(id="response-1"),
        ),
        SimpleNamespace(
            type="response.output_item.added",
            output_index=0,
            item=SimpleNamespace(
                id="item-1",
                type="function_call",
                call_id="call-1",
                name="read_file",
                arguments="",
            ),
        ),
        SimpleNamespace(
            type="response.function_call_arguments.delta",
            item_id="item-1",
            output_index=0,
            delta='{"path":',
        ),
        SimpleNamespace(
            type="response.function_call_arguments.delta",
            item_id="item-1",
            output_index=0,
            delta='"delta"}',
        ),
        SimpleNamespace(
            type="response.output_item.done",
            output_index=0,
            item=SimpleNamespace(
                id="item-1",
                type="function_call",
                call_id="call-1",
                name="read_file",
                arguments='{"path":"a"}',
            ),
        ),
        SimpleNamespace(
            type="response.completed",
            response=SimpleNamespace(
                id="response-1",
                usage=SimpleNamespace(input_tokens=7, output_tokens=3),
            ),
        ),
    )

    outcome = openai_client.ModelGateway(
        model="m", api_mode="responses", client=client
    ).stream((Message("user", "Read a"),), TOOL_DEFINITIONS, lambda _: None)

    assert outcome == openai_client.ToolCallResponse(
        (ToolCall("call-1", "read_file", '{"path":"a"}'),),
        openai_client.ResponsesContinuation("response-1"),
        TokenUsage(7, 3),
    )


def test_responses_gateway_rejects_function_call_without_done_event() -> None:
    client = FakeClient()
    client.responses.create = FakeStream(
        SimpleNamespace(
            type="response.created", response=SimpleNamespace(id="response-1")
        ),
        SimpleNamespace(
            type="response.output_item.added",
            output_index=0,
            item=SimpleNamespace(
                id="item-1",
                type="function_call",
                call_id="call-1",
                name="read_file",
                arguments="",
            ),
        ),
        SimpleNamespace(
            type="response.function_call_arguments.delta",
            item_id="item-1",
            output_index=0,
            delta="{}",
        ),
        SimpleNamespace(
            type="response.completed",
            response=SimpleNamespace(id="response-1"),
        ),
    )

    with pytest.raises(RuntimeError, match=r"OpenAI returned an unsupported response\."):
        openai_client.ModelGateway(
            model="m", api_mode="responses", client=client
        ).stream((Message("user", "Read a file"),), TOOL_DEFINITIONS, lambda _: None)


def test_responses_gateway_rejects_tool_call_without_completed_event() -> None:
    client = FakeClient()
    client.responses.create = FakeStream(
        SimpleNamespace(
            type="response.created", response=SimpleNamespace(id="response-1")
        ),
        SimpleNamespace(
            type="response.output_item.done",
            output_index=0,
            item=SimpleNamespace(
                id="item-1",
                type="function_call",
                call_id="call-1",
                name="read_file",
                arguments="{}",
            ),
        ),
    )

    with pytest.raises(RuntimeError, match=r"OpenAI returned an unsupported response\."):
        openai_client.ModelGateway(
            model="m", api_mode="responses", client=client
        ).stream((Message("user", "Read a file"),), TOOL_DEFINITIONS, lambda _: None)


def test_responses_gateway_rejects_text_without_completed_event() -> None:
    client = FakeClient()
    client.responses.create = FakeStream(
        SimpleNamespace(type="response.output_text.delta", delta="partial")
    )

    with pytest.raises(RuntimeError, match=r"OpenAI returned an unsupported response\."):
        openai_client.ModelGateway(
            model="m", api_mode="responses", client=client
        ).stream((Message("user", "Hello"),), (), lambda _: None)


@pytest.mark.parametrize(
    "terminal_event",
    [
        SimpleNamespace(
            type="response.failed", response=SimpleNamespace(id="response-1")
        ),
        SimpleNamespace(
            type="response.incomplete", response=SimpleNamespace(id="response-1")
        ),
        SimpleNamespace(type="error", message="provider failure"),
    ],
)
def test_responses_gateway_rejects_non_completed_terminal_events(
    terminal_event: SimpleNamespace,
) -> None:
    client = FakeClient()
    client.responses.create = FakeStream(
        SimpleNamespace(type="response.output_text.delta", delta="partial"),
        terminal_event,
    )

    with pytest.raises(RuntimeError, match=r"OpenAI returned an unsupported response\."):
        openai_client.ModelGateway(
            model="m", api_mode="responses", client=client
        ).stream((Message("user", "Hello"),), (), lambda _: None)


def test_responses_gateway_rejects_conflicting_terminal_events() -> None:
    client = FakeClient()
    client.responses.create = FakeStream(
        SimpleNamespace(type="response.output_text.delta", delta="done"),
        SimpleNamespace(
            type="response.completed", response=SimpleNamespace(id="response-1")
        ),
        SimpleNamespace(
            type="response.incomplete", response=SimpleNamespace(id="response-1")
        ),
    )

    with pytest.raises(RuntimeError, match=r"OpenAI returned an unsupported response\."):
        openai_client.ModelGateway(
            model="m", api_mode="responses", client=client
        ).stream((Message("user", "Hello"),), (), lambda _: None)


@pytest.mark.parametrize(
    "later_event",
    [
        SimpleNamespace(type="response.output_text.delta", delta="late"),
        SimpleNamespace(
            type="response.output_item.done",
            output_index=0,
            item=SimpleNamespace(
                id="item-1",
                type="function_call",
                call_id="call-1",
                name="read_file",
                arguments="{}",
            ),
        ),
    ],
)
def test_responses_gateway_rejects_events_after_completed(
    later_event: SimpleNamespace,
) -> None:
    client = FakeClient()
    client.responses.create = FakeStream(
        SimpleNamespace(
            type="response.created", response=SimpleNamespace(id="response-1")
        ),
        SimpleNamespace(type="response.output_text.delta", delta="done"),
        SimpleNamespace(
            type="response.completed", response=SimpleNamespace(id="response-1")
        ),
        later_event,
    )

    with pytest.raises(RuntimeError, match=r"OpenAI returned an unsupported response\."):
        openai_client.ModelGateway(
            model="m", api_mode="responses", client=client
        ).stream((Message("user", "Hello"),), TOOL_DEFINITIONS, lambda _: None)


def test_responses_gateway_rejects_item_id_reused_at_two_indexes() -> None:
    client = FakeClient()
    client.responses.create = FakeStream(
        SimpleNamespace(
            type="response.created", response=SimpleNamespace(id="response-1")
        ),
        *(
            SimpleNamespace(
                type="response.output_item.added",
                output_index=index,
                item=SimpleNamespace(
                    id="item-shared",
                    type="function_call",
                    call_id=f"call-{index}",
                    name="read_file",
                    arguments="",
                ),
            )
            for index in (0, 1)
        ),
        *(
            SimpleNamespace(
                type="response.output_item.done",
                output_index=index,
                item=SimpleNamespace(
                    id="item-shared",
                    type="function_call",
                    call_id=f"call-{index}",
                    name="read_file",
                    arguments=f'{{"path":"{index}"}}',
                ),
            )
            for index in (0, 1)
        ),
        SimpleNamespace(
            type="response.completed", response=SimpleNamespace(id="response-1")
        ),
    )

    with pytest.raises(RuntimeError, match=r"OpenAI returned an unsupported response\."):
        openai_client.ModelGateway(
            model="m", api_mode="responses", client=client
        ).stream((Message("user", "Read files"),), TOOL_DEFINITIONS, lambda _: None)


def test_responses_gateway_rejects_call_id_reused_across_indexes() -> None:
    client = FakeClient()
    client.responses.create = FakeStream(
        SimpleNamespace(
            type="response.created", response=SimpleNamespace(id="response-1")
        ),
        *(
            SimpleNamespace(
                type="response.output_item.added",
                output_index=index,
                item=SimpleNamespace(
                    id=f"item-{index}",
                    type="function_call",
                    call_id="call-shared",
                    name="read_file",
                    arguments="",
                ),
            )
            for index in (0, 1)
        ),
        *(
            SimpleNamespace(
                type="response.output_item.done",
                output_index=index,
                item=SimpleNamespace(
                    id=f"item-{index}",
                    type="function_call",
                    call_id="call-shared",
                    name="read_file",
                    arguments="{}",
                ),
            )
            for index in (0, 1)
        ),
        SimpleNamespace(
            type="response.completed", response=SimpleNamespace(id="response-1")
        ),
    )

    with pytest.raises(RuntimeError, match=r"OpenAI returned an unsupported response\."):
        openai_client.ModelGateway(
            model="m", api_mode="responses", client=client
        ).stream((Message("user", "Read files"),), TOOL_DEFINITIONS, lambda _: None)


@pytest.mark.parametrize("second_arguments", ["{}", '{"path":"other"}'])
def test_responses_gateway_rejects_duplicate_done_events(
    second_arguments: str,
) -> None:
    client = FakeClient()
    client.responses.create = FakeStream(
        SimpleNamespace(
            type="response.created", response=SimpleNamespace(id="response-1")
        ),
        SimpleNamespace(
            type="response.output_item.added",
            output_index=0,
            item=SimpleNamespace(
                id="item-1",
                type="function_call",
                call_id="call-1",
                name="read_file",
                arguments="",
            ),
        ),
        *(
            SimpleNamespace(
                type="response.output_item.done",
                output_index=0,
                item=SimpleNamespace(
                    id="item-1",
                    type="function_call",
                    call_id="call-1",
                    name="read_file",
                    arguments=arguments,
                ),
            )
            for arguments in ("{}", second_arguments)
        ),
        SimpleNamespace(
            type="response.completed", response=SimpleNamespace(id="response-1")
        ),
    )

    with pytest.raises(RuntimeError, match=r"OpenAI returned an unsupported response\."):
        openai_client.ModelGateway(
            model="m", api_mode="responses", client=client
        ).stream((Message("user", "Read a file"),), TOOL_DEFINITIONS, lambda _: None)


def test_responses_gateway_orders_parallel_calls_by_output_index() -> None:
    client = FakeClient()
    client.responses.create = FakeStream(
        SimpleNamespace(
            type="response.created", response=SimpleNamespace(id="response-1")
        ),
        *(
            SimpleNamespace(
                type="response.output_item.added",
                output_index=index,
                item=SimpleNamespace(
                    id=f"item-{index}",
                    type="function_call",
                    call_id=f"call-{index}",
                    name="read_file",
                    arguments="",
                ),
            )
            for index in (1, 0)
        ),
        *(
            SimpleNamespace(
                type="response.output_item.done",
                output_index=index,
                item=SimpleNamespace(
                    id=f"item-{index}",
                    type="function_call",
                    call_id=f"call-{index}",
                    name="read_file",
                    arguments=f'{{"path":"{index}"}}',
                ),
            )
            for index in (1, 0)
        ),
        SimpleNamespace(
            type="response.completed", response=SimpleNamespace(id="response-1")
        ),
    )

    outcome = openai_client.ModelGateway(
        model="m", api_mode="responses", client=client
    ).stream((Message("user", "Read files"),), TOOL_DEFINITIONS, lambda _: None)

    assert isinstance(outcome, openai_client.ToolCallResponse)
    assert outcome.calls == (
        ToolCall("call-0", "read_file", '{"path":"0"}'),
        ToolCall("call-1", "read_file", '{"path":"1"}'),
    )


@pytest.mark.parametrize("missing_field", ["call_id", "name"])
def test_responses_gateway_rejects_completed_item_missing_metadata(
    missing_field: str,
) -> None:
    client = FakeClient()
    client.responses.create = FakeStream(
        SimpleNamespace(
            type="response.created", response=SimpleNamespace(id="response-1")
        ),
        SimpleNamespace(
            type="response.output_item.added",
            output_index=0,
            item=SimpleNamespace(
                id="item-1",
                type="function_call",
                call_id="call-1",
                name="read_file",
                arguments="",
            ),
        ),
        SimpleNamespace(
            type="response.output_item.done",
            output_index=0,
            item=SimpleNamespace(
                id="item-1",
                type="function_call",
                **{
                    field: value
                    for field, value in {
                        "call_id": "call-1",
                        "name": "read_file",
                        "arguments": "{}",
                    }.items()
                    if field != missing_field
                },
            ),
        ),
    )

    with pytest.raises(RuntimeError, match=r"OpenAI returned an unsupported response\."):
        openai_client.ModelGateway(
            model="m", api_mode="responses", client=client
        ).stream((Message("user", "Read a file"),), TOOL_DEFINITIONS, lambda _: None)


def test_responses_gateway_rejects_delta_before_function_call_item() -> None:
    client = FakeClient()
    client.responses.create = FakeStream(
        SimpleNamespace(
            type="response.created", response=SimpleNamespace(id="response-1")
        ),
        SimpleNamespace(
            type="response.function_call_arguments.delta",
            output_index=0,
            delta='{"path":"ignored"}',
        ),
        SimpleNamespace(
            type="response.output_item.added",
            output_index=0,
            item=SimpleNamespace(
                id="item-1",
                type="function_call",
                call_id="call-1",
                name="read_file",
                arguments="",
            ),
        ),
        SimpleNamespace(
            type="response.output_item.done",
            output_index=0,
            item=SimpleNamespace(
                id="item-1",
                type="function_call",
                call_id="call-1",
                name="read_file",
                arguments="{}",
            ),
        ),
    )

    with pytest.raises(RuntimeError, match=r"OpenAI returned an unsupported response\."):
        openai_client.ModelGateway(
            model="m", api_mode="responses", client=client
        ).stream((Message("user", "Read a file"),), TOOL_DEFINITIONS, lambda _: None)


@pytest.mark.parametrize(
    "events",
    [
        (
            SimpleNamespace(
                type="response.output_item.added",
                output_index=0,
                item=SimpleNamespace(
                    id="item-1",
                    type="function_call",
                    call_id="call-1",
                    name="read_file",
                    arguments="",
                ),
            ),
            SimpleNamespace(
                type="response.output_item.done",
                output_index=0,
                item=SimpleNamespace(
                    id="item-1",
                    type="function_call",
                    call_id="call-1",
                    name="read_file",
                    arguments="{}",
                ),
            ),
        ),
        (
            SimpleNamespace(
                type="response.created", response=SimpleNamespace(id="response-1")
            ),
            SimpleNamespace(
                type="response.completed", response=SimpleNamespace(id="response-2")
            ),
        ),
        (
            SimpleNamespace(
                type="response.output_item.added",
                output_index=0,
                item=SimpleNamespace(
                    id="item-1",
                    type="function_call",
                    call_id="call-1",
                    name="read_file",
                    arguments="",
                ),
            ),
            SimpleNamespace(
                type="response.function_call_arguments.delta",
                output_index=0,
                item_id="item-2",
                delta="{}",
            ),
        ),
        (
            SimpleNamespace(
                type="response.output_item.added",
                output_index=-1,
                item=SimpleNamespace(type="function_call"),
            ),
        ),
        (
            SimpleNamespace(
                type="response.output_item.added",
                output_index="0",
                item=SimpleNamespace(type="function_call"),
            ),
        ),
        *[
            (
                SimpleNamespace(
                    type="response.output_item.done",
                    output_index=0,
                    item=SimpleNamespace(
                        id="item-1",
                        type="function_call",
                        **{
                            field: value
                            for field, value in {
                                "call_id": "call-1",
                                "name": "read_file",
                                "arguments": "{}",
                            }.items()
                            if field != missing_field
                        },
                    ),
                ),
            )
            for missing_field in ("call_id", "name", "arguments")
        ],
    ],
)
def test_responses_gateway_rejects_malformed_streamed_function_call(
    events: tuple[SimpleNamespace, ...],
) -> None:
    client = FakeClient()
    client.responses.create = FakeStream(*events)

    with pytest.raises(RuntimeError, match=r"OpenAI returned an unsupported response\."):
        openai_client.ModelGateway(
            model="m", api_mode="responses", client=client
        ).stream((Message("user", "Read a file"),), TOOL_DEFINITIONS, lambda _: None)


def test_responses_text_stream_captures_completed_usage() -> None:
    client = FakeClient()
    client.responses.create = FakeStream(
        SimpleNamespace(type="response.output_text.delta", delta="done"),
        SimpleNamespace(
            type="response.completed",
            response=SimpleNamespace(
                id="response-1",
                usage=SimpleNamespace(input_tokens=5, output_tokens=1),
            ),
        ),
    )

    outcome = openai_client.ModelGateway(
        model="m", api_mode="responses", client=client
    ).stream((Message("user", "Hello"),), (), lambda _: None)

    assert outcome == openai_client.FinalResponse("done", TokenUsage(5, 1))


def test_responses_stream_continuation_sends_only_tool_outputs_and_response_id(
) -> None:
    client = FakeClient()
    client.responses.create = FakeStreamSequence(
        (
            SimpleNamespace(
                type="response.created", response=SimpleNamespace(id="response-1")
            ),
            SimpleNamespace(
                type="response.output_item.added",
                output_index=0,
                item=SimpleNamespace(
                    id="item-1",
                    type="function_call",
                    call_id="call-1",
                    name="read_file",
                    arguments="",
                ),
            ),
            SimpleNamespace(
                type="response.output_item.done",
                output_index=0,
                item=SimpleNamespace(
                    id="item-1",
                    type="function_call",
                    call_id="call-1",
                    name="read_file",
                    arguments='{"path":"README.md"}',
                ),
            ),
            SimpleNamespace(
                type="response.completed", response=SimpleNamespace(id="response-1")
            ),
        ),
        (
            SimpleNamespace(type="response.output_text.delta", delta="Done"),
            SimpleNamespace(
                type="response.completed", response=SimpleNamespace(id="response-2")
            ),
        ),
    )
    gateway = openai_client.ModelGateway(
        model="m", api_mode="responses", client=client
    )
    messages = (Message("user", "Inspect"),)

    first = gateway.stream(messages, TOOL_DEFINITIONS, lambda _: None)
    assert isinstance(first, openai_client.ToolCallResponse)
    second = gateway.stream(
        messages,
        TOOL_DEFINITIONS,
        lambda _: None,
        continuation=first.continuation,
        tool_outputs=(("call-1", '{"ok":true}'),),
    )

    assert second == openai_client.FinalResponse("Done")
    assert client.responses.create.calls == [
        {
            "model": "m",
            "stream": True,
            "input": [{"role": "user", "content": "Inspect"}],
            "tools": list(TOOL_DEFINITIONS),
        },
        {
            "model": "m",
            "stream": True,
            "input": [{
                "type": "function_call_output",
                "call_id": "call-1",
                "output": '{"ok":true}',
            }],
            "previous_response_id": "response-1",
            "tools": list(TOOL_DEFINITIONS),
        },
    ]


def test_chat_stream_continuation_adds_tool_messages_exactly_once() -> None:
    client = FakeClient()
    client.chat.completions.create = FakeStreamSequence(
        (
            SimpleNamespace(choices=[SimpleNamespace(
                delta=SimpleNamespace(tool_calls=[SimpleNamespace(
                    index=0,
                    id="call-1",
                    function=SimpleNamespace(
                        name="read_file", arguments='{"path":"README.md"}'
                    ),
                )]),
                finish_reason="tool_calls",
            )]),
        ),
        (
            SimpleNamespace(choices=[SimpleNamespace(
                delta=SimpleNamespace(content="Done"), finish_reason="stop"
            )]),
        ),
    )
    gateway = openai_client.ModelGateway(
        model="m", api_mode="chat_completions", client=client
    )
    messages = (Message("user", "Inspect"),)

    first = gateway.stream(messages, TOOL_DEFINITIONS, lambda _: None)
    assert isinstance(first, openai_client.ToolCallResponse)
    second = gateway.stream(
        messages,
        TOOL_DEFINITIONS,
        lambda _: None,
        continuation=first.continuation,
        tool_outputs=(("call-1", '{"ok":true}'),),
    )

    assert second == openai_client.FinalResponse("Done")
    tool_definition = {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file.",
            "parameters": FakeTool.parameters,
        },
    }
    assert client.chat.completions.create.calls == [
        {
            "model": "m",
            "messages": [{"role": "user", "content": "Inspect"}],
            "stream": True,
            "stream_options": {"include_usage": True},
            "tools": [tool_definition],
        },
        {
            "model": "m",
            "messages": [
                {"role": "user", "content": "Inspect"},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": "call-1",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": '{"path":"README.md"}',
                        },
                    }],
                },
                {
                    "role": "tool",
                    "tool_call_id": "call-1",
                    "content": '{"ok":true}',
                },
            ],
            "stream": True,
            "stream_options": {"include_usage": True},
            "tools": [tool_definition],
        },
    ]


def test_gateway_normalizes_chat_usage() -> None:
    client = FakeClient()
    client.chat.completions.create = FakeChatSequence(SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="Done", tool_calls=[]))],
        usage=SimpleNamespace(prompt_tokens=9, completion_tokens=2),
    ))
    outcome = openai_client.ModelGateway(model="m", api_mode="chat_completions", client=client).create(
        (Message("user", "secret prompt"),), ()
    )
    assert outcome == openai_client.FinalResponse("Done", TokenUsage(9, 2))


def test_responses_gateway_normalizes_tool_call_usage() -> None:
    client = FakeClient()
    client.responses.create = FakeResponsesSequence(SimpleNamespace(
        id="response-1",
        output_text="",
        output=[SimpleNamespace(
            type="function_call", call_id="call-1", name="read_file", arguments="{}"
        )],
        usage=SimpleNamespace(input_tokens=12, output_tokens=3),
    ))

    outcome = openai_client.ModelGateway(
        model="m", api_mode="responses", client=client
    ).create((Message("user", "Hello"),), TOOL_DEFINITIONS)

    assert isinstance(outcome, openai_client.ToolCallResponse)
    assert outcome.usage == TokenUsage(12, 3)


def test_chat_gateway_normalizes_tool_call_usage() -> None:
    client = FakeClient()
    client.chat.completions.create = FakeChatSequence(SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(
            content=None,
            tool_calls=[SimpleNamespace(
                id="call-1",
                function=SimpleNamespace(name="read_file", arguments="{}"),
            )],
        ))],
        usage=SimpleNamespace(prompt_tokens=9, completion_tokens=2),
    ))

    outcome = openai_client.ModelGateway(
        model="m", api_mode="chat_completions", client=client
    ).create((Message("user", "Hello"),), TOOL_DEFINITIONS)

    assert isinstance(outcome, openai_client.ToolCallResponse)
    assert outcome.usage == TokenUsage(9, 2)


@pytest.mark.parametrize(
    ("api_mode", "usage"),
    [
        ("responses", SimpleNamespace(output_tokens=2)),
        ("responses", SimpleNamespace(input_tokens="12", output_tokens=2)),
        ("chat_completions", SimpleNamespace(completion_tokens=2)),
        ("chat_completions", SimpleNamespace(prompt_tokens=9, completion_tokens=None)),
    ],
)
def test_gateway_maps_malformed_usage_to_unsupported(
    api_mode: str, usage: SimpleNamespace
) -> None:
    client = FakeClient()
    client.responses.create = FakeResponsesSequence(SimpleNamespace(
        id="response-1", output_text="Done", output=[], usage=usage
    ))
    client.chat.completions.create = FakeChatSequence(SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(
            content="Done", tool_calls=[]
        ))],
        usage=usage,
    ))

    with pytest.raises(
        RuntimeError, match=r"OpenAI returned an unsupported response\."
    ):
        openai_client.ModelGateway(
            model="m", api_mode=api_mode, client=client
        ).create((Message("user", "Hello"),), ())


@pytest.mark.parametrize("api_mode", ["responses", "chat_completions"])
def test_gateway_allows_missing_usage(api_mode: str) -> None:
    client = FakeClient(responses_output="Done", chat_output="Done")
    outcome = openai_client.ModelGateway(model="m", api_mode=api_mode, client=client).create(
        (Message("user", "Hello"),), ()
    )
    assert outcome.usage is None


def test_generate_reply_sends_normalized_prompt_and_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client = FakeClient(responses_output="Hello from the model.")

    result = generate_reply(
        "  Hello  ",
        model="gpt-5.6-terra",
        api_mode="responses",
        client=client,
    )

    assert result == "Hello from the model."
    assert client.responses.calls == [
        {
            "model": "gpt-5.6-terra",
            "input": [{"role": "user", "content": "Hello"}],
        }
    ]


def test_generate_reply_rejects_blank_prompt_before_api_call() -> None:
    client = FakeClient(responses_output="unused")

    with pytest.raises(ValueError, match="Prompt must not be empty"):
        generate_reply(
            "   ",
            model="gpt-5.6-terra",
            api_mode="responses",
            client=client,
        )

    assert client.responses.calls == []


def test_generate_reply_rejects_blank_output() -> None:
    client = FakeClient(responses_output="   ")

    with pytest.raises(RuntimeError, match="empty response"):
        generate_reply(
            "Hello",
            model="gpt-5.6-terra",
            api_mode="responses",
            client=client,
        )


def test_generate_reply_creates_default_sdk_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-api-key")
    client = FakeClient(responses_output="Created through the SDK factory.")
    factory_calls: list[bool] = []

    def fake_openai_factory() -> FakeClient:
        factory_calls.append(True)
        return client

    monkeypatch.setattr(openai_client, "OpenAI", fake_openai_factory)

    result = generate_reply(
        "Hello",
        model="gpt-5.6-terra",
        api_mode="responses",
    )

    assert result == "Created through the SDK factory."
    assert factory_calls == [True]


@pytest.mark.parametrize("api_key", [None, "   "])
def test_generate_reply_rejects_missing_api_key_before_sdk_factory(
    monkeypatch: pytest.MonkeyPatch,
    api_key: str | None,
) -> None:
    factory_calls: list[bool] = []

    if api_key is None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    else:
        monkeypatch.setenv("OPENAI_API_KEY", api_key)

    def fake_openai_factory() -> FakeClient:
        factory_calls.append(True)
        return FakeClient(responses_output="unused")

    monkeypatch.setattr(openai_client, "OpenAI", fake_openai_factory)

    with pytest.raises(
        openai_client.MissingAPIKeyError,
        match="OPENAI_API_KEY",
    ):
        generate_reply(
            "Hello",
            model="gpt-5.6-terra",
            api_mode="responses",
        )

    assert factory_calls == []


def test_generate_reply_rejects_invalid_api_mode_before_sdk_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-api-key")
    factory_calls: list[bool] = []

    def fake_openai_factory() -> FakeClient:
        factory_calls.append(True)
        return FakeClient(responses_output="unused")

    monkeypatch.setattr(openai_client, "OpenAI", fake_openai_factory)

    with pytest.raises(ValueError, match="Unsupported API mode"):
        generate_reply(
            "Hello",
            model="test-model",
            api_mode="legacy",
        )

    assert factory_calls == []


def test_generate_reply_uses_chat_completions_mode() -> None:
    client = FakeClient(chat_output="Hello from DeepSeek.")

    result = generate_reply(
        "  Hello  ",
        model="deepseek-v4-flash",
        api_mode="chat_completions",
        client=client,
    )

    assert result == "Hello from DeepSeek."
    assert client.chat.completions.calls == [
        {
            "model": "deepseek-v4-flash",
            "messages": [{"role": "user", "content": "Hello"}],
        }
    ]
    assert client.responses.calls == []


def test_generate_reply_uses_only_responses_mode() -> None:
    client = FakeClient(responses_output="Hello from OpenAI.")

    result = generate_reply(
        "Hello",
        model="gpt-5.6-terra",
        api_mode="responses",
        client=client,
    )

    assert result == "Hello from OpenAI."
    assert client.chat.completions.calls == []


def test_generate_reply_rejects_invalid_api_mode_before_api_call() -> None:
    client = FakeClient()

    with pytest.raises(ValueError, match="Unsupported API mode"):
        generate_reply(
            "Hello",
            model="test-model",
            api_mode="legacy",
            client=client,
        )

    assert client.responses.calls == []
    assert client.chat.completions.calls == []


@pytest.mark.parametrize("chat_output", [None, "   ", ["not", "text"]])
def test_generate_reply_rejects_empty_or_non_text_chat_output(
    chat_output: object,
) -> None:
    client = FakeClient(chat_output=chat_output)

    with pytest.raises(RuntimeError, match="empty response"):
        generate_reply(
            "Hello",
            model="deepseek-v4-flash",
            api_mode="chat_completions",
            client=client,
        )


def test_generate_reply_rejects_missing_chat_choice() -> None:
    client = FakeClient()
    client.chat.completions.create = lambda **kwargs: SimpleNamespace(choices=[])

    with pytest.raises(RuntimeError, match="empty response"):
        generate_reply(
            "Hello",
            model="deepseek-v4-flash",
            api_mode="chat_completions",
            client=client,
        )


def test_generate_reply_rejects_missing_chat_content() -> None:
    client = FakeClient()
    client.chat.completions.create = lambda **kwargs: SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace())]
    )

    with pytest.raises(RuntimeError, match="empty response"):
        generate_reply(
            "Hello",
            model="deepseek-v4-flash",
            api_mode="chat_completions",
            client=client,
        )


def test_generate_reply_for_messages_sends_responses_history() -> None:
    client = FakeClient(responses_output="Second reply")
    messages = (
        Message(role="user", content="First question"),
        Message(role="assistant", content="First reply"),
        Message(role="user", content="Follow-up"),
    )

    result = openai_client.generate_reply_for_messages(
        messages,
        model="gpt-5.6-terra",
        api_mode="responses",
        client=client,
    )

    assert result == "Second reply"
    assert client.responses.calls == [
        {
            "model": "gpt-5.6-terra",
            "input": [
                {"role": "user", "content": "First question"},
                {"role": "assistant", "content": "First reply"},
                {"role": "user", "content": "Follow-up"},
            ],
        }
    ]


def test_generate_reply_for_messages_sends_chat_history() -> None:
    client = FakeClient(chat_output="Second reply")
    messages = (
        Message(role="user", content="First question"),
        Message(role="assistant", content="First reply"),
        Message(role="user", content="Follow-up"),
    )

    result = openai_client.generate_reply_for_messages(
        messages,
        model="deepseek-v4-flash",
        api_mode="chat_completions",
        client=client,
    )

    assert result == "Second reply"
    assert client.chat.completions.calls == [
        {
            "model": "deepseek-v4-flash",
            "messages": [
                {"role": "user", "content": "First question"},
                {"role": "assistant", "content": "First reply"},
                {"role": "user", "content": "Follow-up"},
            ],
        }
    ]
    assert client.responses.calls == []


def test_generate_reply_for_messages_rejects_empty_history() -> None:
    client = FakeClient()

    with pytest.raises(ValueError, match="history must not be empty"):
        openai_client.generate_reply_for_messages(
            (),
            model="test-model",
            api_mode="responses",
            client=client,
        )

    assert client.responses.calls == []
    assert client.chat.completions.calls == []


def test_gateway_adapts_responses_tool_calls_and_continuation() -> None:
    client = FakeClient()
    client.responses.create = FakeResponsesSequence(
        SimpleNamespace(
            id="response-1",
            output_text="",
            output=[
                SimpleNamespace(
                    type="function_call",
                    call_id="call-1",
                    name="read_file",
                    arguments='{"path":"README.md"}',
                ),
                SimpleNamespace(
                    type="function_call",
                    call_id="call-2",
                    name="read_file",
                    arguments='{"path":"pyproject.toml"}',
                ),
            ],
        ),
        SimpleNamespace(id="response-2", output_text="Done", output=[]),
    )
    gateway = openai_client.ModelGateway(
        model="test-model", api_mode="responses", client=client
    )

    first = gateway.create(
        (Message(role="user", content="Inspect files"),), TOOL_DEFINITIONS
    )

    assert first == openai_client.ToolCallResponse(
        calls=(
            ToolCall("call-1", "read_file", '{"path":"README.md"}'),
            ToolCall("call-2", "read_file", '{"path":"pyproject.toml"}'),
        ),
        continuation=openai_client.ResponsesContinuation("response-1"),
    )
    second = gateway.create(
        (Message(role="user", content="Inspect files"),),
        TOOL_DEFINITIONS,
        continuation=first.continuation,
        tool_outputs=(("call-1", '{"ok":true}'), ("call-2", '{"ok":true}')),
    )
    assert second == openai_client.FinalResponse("Done")
    assert client.responses.create.calls == [
        {
            "model": "test-model",
            "input": [{"role": "user", "content": "Inspect files"}],
            "tools": [{
                "type": "function",
                "name": "read_file",
                "description": "Read a file.",
                "parameters": FakeTool.parameters,
            }],
        },
        {
            "model": "test-model",
            "input": [
                {"type": "function_call_output", "call_id": "call-1", "output": '{"ok":true}'},
                {"type": "function_call_output", "call_id": "call-2", "output": '{"ok":true}'},
            ],
            "tools": [{
                "type": "function",
                "name": "read_file",
                "description": "Read a file.",
                "parameters": FakeTool.parameters,
            }],
            "previous_response_id": "response-1",
        },
    ]


def test_gateway_adapts_chat_tool_calls_and_continuation() -> None:
    assistant = SimpleNamespace(
        content=None,
        tool_calls=[SimpleNamespace(
            id="call-1",
            type="function",
            function=SimpleNamespace(name="read_file", arguments='{"path":"README.md"}'),
        )],
    )
    client = FakeClient()
    client.chat.completions.create = FakeChatSequence(
        SimpleNamespace(choices=[SimpleNamespace(message=assistant)]),
        SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="Done", tool_calls=[]))]),
    )
    gateway = openai_client.ModelGateway(
        model="test-model", api_mode="chat_completions", client=client
    )

    first = gateway.create((Message(role="user", content="Inspect"),), TOOL_DEFINITIONS)
    assert first.calls == (ToolCall("call-1", "read_file", '{"path":"README.md"}'),)
    second = gateway.create(
        (Message(role="user", content="Inspect"),),
        TOOL_DEFINITIONS,
        continuation=first.continuation,
        tool_outputs=(("call-1", '{"ok":true}'),),
    )

    assert second == openai_client.FinalResponse("Done")
    tool_definition = {
        "type": "function",
        "function": {
            "name": "read_file", "description": "Read a file.",
            "parameters": FakeTool.parameters,
        },
    }
    assert client.chat.completions.create.calls == [
        {
            "model": "test-model",
            "messages": [{"role": "user", "content": "Inspect"}],
            "tools": [tool_definition],
        },
        {
            "model": "test-model",
            "messages": [
                {"role": "user", "content": "Inspect"},
                {"role": "assistant", "content": None, "tool_calls": [{
                    "id": "call-1", "type": "function",
                    "function": {"name": "read_file", "arguments": '{"path":"README.md"}'},
                }]},
                {"role": "tool", "tool_call_id": "call-1", "content": '{"ok":true}'},
            ],
            "tools": [tool_definition],
        },
    ]


def test_chat_continuation_accumulates_consecutive_tool_rounds() -> None:
    def assistant(call_id: str) -> object:
        return SimpleNamespace(
            content=None,
            tool_calls=[
                SimpleNamespace(
                    id=call_id,
                    type="function",
                    function=SimpleNamespace(
                        name="read_file", arguments=f'{{"path":"{call_id}"}}'
                    ),
                )
            ],
        )
    client = FakeClient()
    client.chat.completions.create = FakeChatSequence(
        SimpleNamespace(choices=[SimpleNamespace(message=assistant("call-1"))]),
        SimpleNamespace(choices=[SimpleNamespace(message=assistant("call-2"))]),
        SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="Done", tool_calls=[])
                )
            ]
        ),
    )
    gateway = openai_client.ModelGateway(model="m", api_mode="chat_completions", client=client)
    first = gateway.create((Message(role="user", content="go"),), TOOL_DEFINITIONS)
    second = gateway.create(
        (Message(role="user", content="go"),),
        TOOL_DEFINITIONS,
        first.continuation,
        (("call-1", "one"),),
    )
    gateway.create(
        (Message(role="user", content="go"),),
        TOOL_DEFINITIONS,
        second.continuation,
        (("call-2", "two"),),
    )
    messages = client.chat.completions.create.calls[2]["messages"]
    assert [(m["role"], m.get("tool_call_id")) for m in messages] == [
        ("user", None), ("assistant", None), ("tool", "call-1"),
        ("assistant", None), ("tool", "call-2"),
    ]


@pytest.mark.parametrize("api_mode", ["responses", "chat_completions"])
def test_gateway_rejects_unsupported_sdk_response(api_mode: str) -> None:
    client = FakeClient(responses_output=None, chat_output=None)
    client.responses.create = FakeResponsesSequence(
        SimpleNamespace(id="response-1", output_text=None, output=[])
    )
    client.chat.completions.create = FakeChatSequence(
        SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=None, tool_calls=[]))])
    )
    gateway = openai_client.ModelGateway(model="test-model", api_mode=api_mode, client=client)

    with pytest.raises(RuntimeError, match=r"OpenAI returned an unsupported response\."):
        gateway.create((Message(role="user", content="Hello"),), ())


@pytest.mark.parametrize(
    ("call_id", "name", "arguments"),
    [("", "read_file", "{}"), ("call-1", "", "{}"), ("call-1", "read_file", {})],
)
def test_gateway_rejects_invalid_tool_call_fields(
    call_id: object, name: object, arguments: object
) -> None:
    client = FakeClient()
    client.responses.create = FakeResponsesSequence(SimpleNamespace(
        id="response-1",
        output_text="",
        output=[SimpleNamespace(
            type="function_call", call_id=call_id, name=name, arguments=arguments
        )],
    ))
    gateway = openai_client.ModelGateway(
        model="test-model", api_mode="responses", client=client
    )

    with pytest.raises(RuntimeError, match=r"OpenAI returned an unsupported response\."):
        gateway.create((Message(role="user", content="Hello"),), TOOL_DEFINITIONS)


@pytest.mark.parametrize("output", [None, 42])
def test_responses_gateway_maps_non_iterable_output_to_unsupported(
    output: object,
) -> None:
    client = FakeClient()
    client.responses.create = FakeResponsesSequence(SimpleNamespace(
        id="response-1", output_text=None, output=output
    ))
    gateway = openai_client.ModelGateway(
        model="test-model", api_mode="responses", client=client
    )

    with pytest.raises(RuntimeError, match=r"OpenAI returned an unsupported response\."):
        gateway.create((Message(role="user", content="Hello"),), TOOL_DEFINITIONS)


@pytest.mark.parametrize("output", [{}, "bad"])
def test_responses_gateway_rejects_invalid_iterable_output_container(
    output: object,
) -> None:
    client = FakeClient()
    client.responses.create = FakeResponsesSequence(SimpleNamespace(
        id="response-1", output_text="valid text must not mask malformed output", output=output
    ))
    gateway = openai_client.ModelGateway(
        model="test-model", api_mode="responses", client=client
    )

    with pytest.raises(RuntimeError, match=r"OpenAI returned an unsupported response\."):
        gateway.create((Message(role="user", content="Hello"),), TOOL_DEFINITIONS)


@pytest.mark.parametrize("missing_field", ["call_id", "name", "arguments"])
def test_responses_gateway_maps_missing_function_call_fields_to_unsupported(
    missing_field: str,
) -> None:
    fields = {
        "type": "function_call",
        "call_id": "call-1",
        "name": "read_file",
        "arguments": "{}",
    }
    del fields[missing_field]
    client = FakeClient()
    client.responses.create = FakeResponsesSequence(SimpleNamespace(
        id="response-1", output_text="", output=[SimpleNamespace(**fields)]
    ))
    gateway = openai_client.ModelGateway(
        model="test-model", api_mode="responses", client=client
    )

    with pytest.raises(RuntimeError, match=r"OpenAI returned an unsupported response\."):
        gateway.create((Message(role="user", content="Hello"),), TOOL_DEFINITIONS)


@pytest.mark.parametrize("choices", [None, 42])
def test_chat_gateway_maps_non_indexable_choices_to_unsupported(
    choices: object,
) -> None:
    client = FakeClient()
    client.chat.completions.create = FakeChatSequence(SimpleNamespace(choices=choices))
    gateway = openai_client.ModelGateway(
        model="test-model", api_mode="chat_completions", client=client
    )

    with pytest.raises(RuntimeError, match=r"OpenAI returned an unsupported response\."):
        gateway.create((Message(role="user", content="Hello"),), TOOL_DEFINITIONS)


def test_chat_gateway_rejects_mapping_choices() -> None:
    client = FakeClient()
    client.chat.completions.create = FakeChatSequence(SimpleNamespace(choices={}))
    gateway = openai_client.ModelGateway(
        model="test-model", api_mode="chat_completions", client=client
    )

    with pytest.raises(RuntimeError, match=r"OpenAI returned an unsupported response\."):
        gateway.create((Message(role="user", content="Hello"),), TOOL_DEFINITIONS)


def test_chat_gateway_rejects_mapping_tool_calls_even_with_valid_text() -> None:
    client = FakeClient()
    client.chat.completions.create = FakeChatSequence(SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(
            content="valid text must not mask malformed calls", tool_calls={}
        ))]
    ))
    gateway = openai_client.ModelGateway(
        model="test-model", api_mode="chat_completions", client=client
    )

    with pytest.raises(RuntimeError, match=r"OpenAI returned an unsupported response\."):
        gateway.create((Message(role="user", content="Hello"),), TOOL_DEFINITIONS)


@pytest.mark.parametrize(
    "tool_call",
    [
        SimpleNamespace(function=SimpleNamespace(name="read_file", arguments="{}")),
        SimpleNamespace(id="call-1"),
        SimpleNamespace(id="call-1", function=SimpleNamespace(arguments="{}")),
        SimpleNamespace(id="call-1", function=SimpleNamespace(name="read_file")),
        SimpleNamespace(
            id="", function=SimpleNamespace(name="read_file", arguments="{}")
        ),
        SimpleNamespace(
            id="call-1", function=SimpleNamespace(name="", arguments="{}")
        ),
        SimpleNamespace(
            id="call-1", function=SimpleNamespace(name="read_file", arguments={})
        ),
    ],
)
def test_chat_gateway_maps_invalid_tool_call_shapes_to_unsupported(
    tool_call: SimpleNamespace,
) -> None:
    client = FakeClient()
    client.chat.completions.create = FakeChatSequence(SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(
            content=None, tool_calls=[tool_call]
        ))]
    ))
    gateway = openai_client.ModelGateway(
        model="test-model", api_mode="chat_completions", client=client
    )

    with pytest.raises(RuntimeError, match=r"OpenAI returned an unsupported response\."):
        gateway.create((Message(role="user", content="Hello"),), TOOL_DEFINITIONS)


class FakeResponsesSequence:
    def __init__(self, *responses: SimpleNamespace) -> None:
        self.responses = iter(responses)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        return next(self.responses)


class FakeChatSequence(FakeResponsesSequence):
    pass


class FakeStream:
    def __init__(self, *events: SimpleNamespace) -> None:
        self.events = events
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> tuple[SimpleNamespace, ...]:
        self.calls.append(kwargs)
        return self.events


class FakeStreamSequence:
    def __init__(self, *streams: tuple[SimpleNamespace, ...]) -> None:
        self.streams = iter(streams)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> tuple[SimpleNamespace, ...]:
        self.calls.append(kwargs)
        return next(self.streams)
