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


class FakeClient:
    def __init__(self, output_text: str | None) -> None:
        self.responses = FakeResponses(output_text)


def test_generate_reply_sends_normalized_prompt_and_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client = FakeClient("Hello from the model.")

    result = generate_reply(
        "  Hello  ",
        model="gpt-5.6-terra",
        client=client,
    )

    assert result == "Hello from the model."
    assert client.responses.calls == [
        {"model": "gpt-5.6-terra", "input": "Hello"}
    ]


def test_generate_reply_rejects_blank_prompt_before_api_call() -> None:
    client = FakeClient("unused")

    with pytest.raises(ValueError, match="Prompt must not be empty"):
        generate_reply("   ", model="gpt-5.6-terra", client=client)

    assert client.responses.calls == []


def test_generate_reply_rejects_blank_output() -> None:
    client = FakeClient("   ")

    with pytest.raises(RuntimeError, match="empty response"):
        generate_reply("Hello", model="gpt-5.6-terra", client=client)


def test_generate_reply_creates_default_sdk_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-api-key")
    client = FakeClient("Created through the SDK factory.")
    factory_calls: list[bool] = []

    def fake_openai_factory() -> FakeClient:
        factory_calls.append(True)
        return client

    monkeypatch.setattr(openai_client, "OpenAI", fake_openai_factory)

    result = generate_reply("Hello", model="gpt-5.6-terra")

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
        return FakeClient("unused")

    monkeypatch.setattr(openai_client, "OpenAI", fake_openai_factory)

    with pytest.raises(
        openai_client.MissingAPIKeyError,
        match="OPENAI_API_KEY",
    ):
        generate_reply("Hello", model="gpt-5.6-terra")

    assert factory_calls == []
