from pathlib import Path

import pytest

from cdy_agent.config import (
    DEFAULT_API_MODE,
    DEFAULT_MODEL,
    DEFAULT_SYSTEM_PROMPT,
    SUPPORTED_API_MODES,
    WorkspaceConfig,
    load_workspace_config,
    resolve_api_mode,
    resolve_model,
    resolve_streaming,
    resolve_system_prompt,
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


def test_missing_workspace_config_is_empty_and_does_not_create_files(
    tmp_path: Path,
) -> None:
    config = load_workspace_config(tmp_path)

    assert config == WorkspaceConfig()
    assert not (tmp_path / ".cdy-agent").exists()


def test_workspace_config_supplies_model_and_api_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("CDY_AGENT_MODEL", raising=False)
    monkeypatch.delenv("CDY_AGENT_API_MODE", raising=False)
    config_dir = tmp_path / ".cdy-agent"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text(
        "model: workspace-model\napi_mode: chat_completions\n",
        encoding="utf-8",
    )
    config = load_workspace_config(tmp_path)

    assert resolve_model(workspace_config=config) == "workspace-model"
    assert resolve_api_mode(workspace_config=config) == "chat_completions"


def test_streaming_defaults_to_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CDY_AGENT_STREAM", raising=False)

    assert resolve_streaming() is False


def test_workspace_config_supplies_streaming(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("CDY_AGENT_STREAM", raising=False)
    config_dir = tmp_path / ".cdy-agent"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text("stream: true\n", encoding="utf-8")

    config = load_workspace_config(tmp_path)

    assert config.stream is True
    assert resolve_streaming(workspace_config=config) is True


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        (" true ", True),
        ("YES", True),
        ("1", True),
        (" false ", False),
        ("NO", False),
        ("0", False),
    ],
)
def test_streaming_environment_is_trimmed_and_normalized(
    monkeypatch: pytest.MonkeyPatch,
    configured: str,
    expected: bool,
) -> None:
    monkeypatch.setenv("CDY_AGENT_STREAM", configured)

    assert resolve_streaming() is expected


def test_streaming_override_wins_over_environment_and_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CDY_AGENT_STREAM", "true")
    config = WorkspaceConfig(stream=True)

    assert resolve_streaming(False, config) is False


def test_streaming_environment_wins_over_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CDY_AGENT_STREAM", "false")
    config = WorkspaceConfig(stream=True)

    assert resolve_streaming(workspace_config=config) is False


@pytest.mark.parametrize("configured", ["sometimes", ""])
def test_invalid_streaming_environment_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    configured: str,
) -> None:
    monkeypatch.setenv("CDY_AGENT_STREAM", configured)

    with pytest.raises(ValueError, match="CDY_AGENT_STREAM"):
        resolve_streaming()


def test_workspace_config_rejects_non_boolean_stream(tmp_path: Path) -> None:
    config_dir = tmp_path / ".cdy-agent"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text("stream: maybe\n", encoding="utf-8")

    with pytest.raises(ValueError, match="stream"):
        load_workspace_config(tmp_path)


def test_workspace_config_supplies_system_prompt(tmp_path: Path) -> None:
    config_dir = tmp_path / ".cdy-agent"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text(
        "system_prompt: |\n  You are a local coding assistant.\n",
        encoding="utf-8",
    )
    config = load_workspace_config(tmp_path)

    assert resolve_system_prompt(config) == "You are a local coding assistant."


def test_blank_system_prompt_falls_back_to_default() -> None:
    config = WorkspaceConfig(system_prompt="   ")

    assert resolve_system_prompt(config) == DEFAULT_SYSTEM_PROMPT


def test_environment_wins_over_workspace_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = WorkspaceConfig(model="workspace-model", api_mode="responses")
    monkeypatch.setenv("CDY_AGENT_MODEL", "env-model")
    monkeypatch.setenv("CDY_AGENT_API_MODE", "chat_completions")

    assert resolve_model(workspace_config=config) == "env-model"
    assert resolve_api_mode(workspace_config=config) == "chat_completions"


def test_cli_model_override_wins_over_environment_and_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CDY_AGENT_MODEL", "env-model")
    config = WorkspaceConfig(model="workspace-model")

    assert resolve_model("cli-model", workspace_config=config) == "cli-model"


def test_workspace_config_rejects_unknown_keys(tmp_path: Path) -> None:
    config_dir = tmp_path / ".cdy-agent"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text(
        "model: test-model\nOPENAI_API_KEY: secret\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Unsupported config key"):
        load_workspace_config(tmp_path)


def test_workspace_config_rejects_invalid_shape(tmp_path: Path) -> None:
    config_dir = tmp_path / ".cdy-agent"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text("- not\n- a\n- mapping\n", encoding="utf-8")

    with pytest.raises(ValueError, match="mapping"):
        load_workspace_config(tmp_path)
