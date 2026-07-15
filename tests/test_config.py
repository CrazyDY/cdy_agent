import pytest

from cdy_agent.config import (
    DEFAULT_API_MODE,
    DEFAULT_MODEL,
    SUPPORTED_API_MODES,
    resolve_api_mode,
    resolve_model,
)


def test_model_override_takes_priority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CDY_AGENT_MODEL", "env-model")

    assert resolve_model("  cli-model  ") == "cli-model"


def test_environment_model_takes_priority_over_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CDY_AGENT_MODEL", "  env-model  ")

    assert resolve_model() == "env-model"


def test_blank_override_falls_back_to_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CDY_AGENT_MODEL", "env-model")

    assert resolve_model("   ") == "env-model"


def test_blank_environment_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CDY_AGENT_MODEL", "   ")

    assert resolve_model() == DEFAULT_MODEL


def test_missing_environment_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CDY_AGENT_MODEL", raising=False)

    assert resolve_model() == "gpt-5.6-terra"


@pytest.mark.parametrize("configured_mode", [None, "   "])
def test_missing_or_blank_api_mode_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
    configured_mode: str | None,
) -> None:
    if configured_mode is None:
        monkeypatch.delenv("CDY_AGENT_API_MODE", raising=False)
    else:
        monkeypatch.setenv("CDY_AGENT_API_MODE", configured_mode)

    assert resolve_api_mode() == DEFAULT_API_MODE == "responses"


@pytest.mark.parametrize(
    ("configured_mode", "expected"),
    [
        (" responses ", "responses"),
        (" CHAT_COMPLETIONS ", "chat_completions"),
    ],
)
def test_api_mode_is_trimmed_and_normalized(
    monkeypatch: pytest.MonkeyPatch,
    configured_mode: str,
    expected: str,
) -> None:
    monkeypatch.setenv("CDY_AGENT_API_MODE", configured_mode)

    assert resolve_api_mode() == expected


def test_invalid_api_mode_lists_value_and_supported_modes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CDY_AGENT_API_MODE", "legacy")

    with pytest.raises(ValueError) as error:
        resolve_api_mode()

    message = str(error.value)
    assert "legacy" in message
    assert all(mode in message for mode in SUPPORTED_API_MODES)
