from types import SimpleNamespace
from typing import Any

import pytest

from cdy_agent import openai_client
from cdy_agent.openai_client import generate_reply


class FakeResponses:
    def __init__(self, output_text: str | None) -> None:
        self.output_text = output_text
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(output_text=self.output_text)


class FakeCompletions:
    def __init__(self, output_text: object) -> None:
        self.output_text = output_text
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        message = SimpleNamespace(content=self.output_text)
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
        {"model": "gpt-5.6-terra", "input": "Hello"}
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
