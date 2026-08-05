from pathlib import Path

import pytest
import yaml

from cdy_agent.config import (
    DEFAULT_API_MODE,
    DEFAULT_MAX_MODEL_CALLS,
    DEFAULT_MODEL,
    DEFAULT_SYSTEM_PROMPT,
    SUPPORTED_API_MODES,
    WorkspaceConfig,
    load_workspace_config,
    resolve_api_mode,
    resolve_base_url,
    resolve_max_model_calls,
    resolve_model,
    resolve_rebuild_frontend,
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


def test_base_url_respects_environment_and_workspace_precedence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    config_dir = tmp_path / ".cdy-agent"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text(
        "base_url: https://workspace.example/v1\n",
        encoding="utf-8",
    )
    config = load_workspace_config(tmp_path)

    assert config.base_url == "https://workspace.example/v1"
    assert resolve_base_url(config) == "https://workspace.example/v1"

    monkeypatch.setenv("OPENAI_BASE_URL", "  https://environment.example/v1  ")
    assert resolve_base_url(config) == "https://environment.example/v1"


def test_blank_base_url_falls_back_to_workspace_or_sdk_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "   ")

    assert resolve_base_url(WorkspaceConfig(base_url=" workspace-url ")) == (
        "workspace-url"
    )
    assert resolve_base_url(WorkspaceConfig(base_url="   ")) is None


def test_streaming_defaults_to_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CDY_AGENT_STREAM", raising=False)

    assert resolve_streaming() is False


def test_max_model_calls_defaults_to_agent_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CDY_AGENT_MAX_MODEL_CALLS", raising=False)

    assert resolve_max_model_calls() == DEFAULT_MAX_MODEL_CALLS == 8


def test_max_model_calls_respects_full_precedence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CDY_AGENT_MAX_MODEL_CALLS", " 10 ")
    config = WorkspaceConfig(max_model_calls=9)

    assert resolve_max_model_calls(11, config) == 11
    assert resolve_max_model_calls(workspace_config=config) == 10
    monkeypatch.delenv("CDY_AGENT_MAX_MODEL_CALLS")
    assert resolve_max_model_calls(workspace_config=config) == 9


@pytest.mark.parametrize("configured", ["0", "-1", "abc", "", "1.5"])
def test_invalid_max_model_calls_environment_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    configured: str,
) -> None:
    monkeypatch.setenv("CDY_AGENT_MAX_MODEL_CALLS", configured)

    with pytest.raises(ValueError, match="CDY_AGENT_MAX_MODEL_CALLS"):
        resolve_max_model_calls()


@pytest.mark.parametrize("configured", [0, -1, True, "8", 1.5])
def test_workspace_config_rejects_invalid_max_model_calls(
    tmp_path: Path,
    configured: object,
) -> None:
    config_dir = tmp_path / ".cdy-agent"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text(
        yaml.safe_dump({"max_model_calls": configured}), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="max_model_calls"):
        load_workspace_config(tmp_path)


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


def test_rebuild_frontend_defaults_to_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CDY_AGENT_REBUILD_FRONTEND", raising=False)

    assert resolve_rebuild_frontend() is False


def test_workspace_config_supplies_rebuild_frontend(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("CDY_AGENT_REBUILD_FRONTEND", raising=False)
    config_dir = tmp_path / ".cdy-agent"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text(
        "rebuild_frontend: true\n", encoding="utf-8"
    )

    config = load_workspace_config(tmp_path)

    assert config.rebuild_frontend is True
    assert resolve_rebuild_frontend(workspace_config=config) is True


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        (" true ", True),
        ("YES", True),
        ("1", True),
        ("on", True),
        (" false ", False),
        ("NO", False),
        ("0", False),
        ("off", False),
    ],
)
def test_rebuild_frontend_environment_is_trimmed_and_normalized(
    monkeypatch: pytest.MonkeyPatch,
    configured: str,
    expected: bool,
) -> None:
    monkeypatch.setenv("CDY_AGENT_REBUILD_FRONTEND", configured)

    assert resolve_rebuild_frontend() is expected


def test_rebuild_frontend_override_wins_over_environment_and_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CDY_AGENT_REBUILD_FRONTEND", "true")
    config = WorkspaceConfig(rebuild_frontend=True)

    assert resolve_rebuild_frontend(False, config) is False


def test_rebuild_frontend_environment_wins_over_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CDY_AGENT_REBUILD_FRONTEND", "false")
    config = WorkspaceConfig(rebuild_frontend=True)

    assert resolve_rebuild_frontend(workspace_config=config) is False


@pytest.mark.parametrize("configured", ["sometimes", ""])
def test_invalid_rebuild_frontend_environment_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    configured: str,
) -> None:
    monkeypatch.setenv("CDY_AGENT_REBUILD_FRONTEND", configured)

    with pytest.raises(ValueError, match="CDY_AGENT_REBUILD_FRONTEND"):
        resolve_rebuild_frontend()


def test_workspace_config_rejects_non_boolean_rebuild_frontend(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / ".cdy-agent"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text(
        "rebuild_frontend: maybe\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="rebuild_frontend"):
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
