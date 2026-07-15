import pytest

from cdy_agent.config import DEFAULT_MODEL, resolve_model


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
