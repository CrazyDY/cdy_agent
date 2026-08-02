"""Application configuration for CDY Agent."""

from __future__ import annotations

import os
import platform
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

DEFAULT_MODEL = "gpt-5.6-terra"
DEFAULT_API_MODE = "responses"
DEFAULT_MAX_MODEL_CALLS = 8
DEFAULT_SYSTEM_PROMPT = (
    "You are CDY Agent, a local personal AI assistant. Follow the user's "
    "instructions, use local tools only when useful, and avoid exposing secrets."
    f"\n**Current OS**: {platform.system()} {platform.release()}"
)
SUPPORTED_API_MODES = ("responses", "chat_completions")
CONFIG_RELATIVE_PATH = Path(".cdy-agent") / "config.yaml"


@dataclass(frozen=True)
class WorkspaceConfig:
    model: str | None = None
    api_mode: str | None = None
    system_prompt: str | None = None
    stream: bool | None = None
    max_model_calls: int | None = None
    log_level: str | None = None
    rebuild_frontend: bool | None = None
    input_cost_per_million: str | None = None
    output_cost_per_million: str | None = None


def load_workspace_config(workspace: Path) -> WorkspaceConfig:
    """Load optional non-secret workspace configuration without creating files."""
    config_path = workspace / CONFIG_RELATIVE_PATH
    if not config_path.exists():
        return WorkspaceConfig()
    try:
        raw_config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid workspace config YAML: {exc}") from None
    if raw_config is None:
        return WorkspaceConfig()
    if not isinstance(raw_config, dict):
        raise ValueError("Workspace config must be a mapping.")

    allowed_top_level = {
        "model",
        "api_mode",
        "system_prompt",
        "stream",
        "max_model_calls",
        "log_level",
        "rebuild_frontend",
        "observability",
    }
    unknown = set(raw_config) - allowed_top_level
    if unknown:
        keys = ", ".join(sorted(str(key) for key in unknown))
        raise ValueError(f"Unsupported config key: {keys}.")

    observability = raw_config.get("observability", {})
    if observability is None:
        observability = {}
    if not isinstance(observability, dict):
        raise ValueError("Workspace config observability must be a mapping.")
    allowed_observability = {
        "input_cost_per_million",
        "output_cost_per_million",
    }
    unknown_observability = set(observability) - allowed_observability
    if unknown_observability:
        keys = ", ".join(sorted(str(key) for key in unknown_observability))
        raise ValueError(f"Unsupported observability config key: {keys}.")

    return WorkspaceConfig(
        model=_optional_string(raw_config.get("model"), "model"),
        api_mode=_optional_string(raw_config.get("api_mode"), "api_mode"),
        system_prompt=_optional_string(
            raw_config.get("system_prompt"), "system_prompt"
        ),
        stream=_optional_bool(raw_config.get("stream"), "stream"),
        max_model_calls=_optional_positive_int(
            raw_config.get("max_model_calls"), "max_model_calls"
        ),
        log_level=_optional_string(raw_config.get("log_level"), "log_level"),
        rebuild_frontend=_optional_bool(
            raw_config.get("rebuild_frontend"), "rebuild_frontend"
        ),
        input_cost_per_million=_optional_string(
            observability.get("input_cost_per_million"),
            "observability.input_cost_per_million",
        ),
        output_cost_per_million=_optional_string(
            observability.get("output_cost_per_million"),
            "observability.output_cost_per_million",
        ),
    )


def resolve_model(
    model_override: str | None = None,
    workspace_config: WorkspaceConfig | None = None,
) -> str:
    """Resolve the model from a CLI override, environment, or default."""
    if model_override and model_override.strip():
        return model_override.strip()

    environment_model = os.getenv("CDY_AGENT_MODEL")
    if environment_model and environment_model.strip():
        return environment_model.strip()

    if workspace_config and workspace_config.model and workspace_config.model.strip():
        return workspace_config.model.strip()

    return DEFAULT_MODEL


def resolve_api_mode(workspace_config: WorkspaceConfig | None = None) -> str:
    """Resolve and validate the configured OpenAI-compatible API mode."""
    configured_mode = os.getenv("CDY_AGENT_API_MODE")
    if (not configured_mode or not configured_mode.strip()) and workspace_config:
        configured_mode = workspace_config.api_mode
    if not configured_mode or not configured_mode.strip():
        return DEFAULT_API_MODE

    normalized_mode = configured_mode.strip().lower()
    if normalized_mode not in SUPPORTED_API_MODES:
        supported = ", ".join(SUPPORTED_API_MODES)
        raise ValueError(
            f"Unsupported CDY_AGENT_API_MODE {normalized_mode!r}. "
            f"Choose one of: {supported}."
        )
    return normalized_mode


def resolve_system_prompt(workspace_config: WorkspaceConfig | None = None) -> str:
    """Resolve the initialized system prompt from workspace config or default."""
    if (
        workspace_config
        and workspace_config.system_prompt
        and workspace_config.system_prompt.strip()
    ):
        return workspace_config.system_prompt.strip()
    return DEFAULT_SYSTEM_PROMPT


def resolve_streaming(
    stream_override: bool | None = None,
    workspace_config: WorkspaceConfig | None = None,
) -> bool:
    """Resolve streaming output from CLI, environment, workspace config, or default."""
    if stream_override is not None:
        return stream_override

    environment_stream = os.getenv("CDY_AGENT_STREAM")
    if environment_stream is not None:
        return _parse_bool(environment_stream, "CDY_AGENT_STREAM")

    if workspace_config and workspace_config.stream is not None:
        return workspace_config.stream

    return False


def resolve_max_model_calls(
    max_model_calls_override: int | None = None,
    workspace_config: WorkspaceConfig | None = None,
) -> int:
    """Resolve the model-call limit from CLI, environment, config, or default."""
    if max_model_calls_override is not None:
        return _positive_int(max_model_calls_override, "--max-model-calls")

    environment_value = os.getenv("CDY_AGENT_MAX_MODEL_CALLS")
    if environment_value is not None:
        try:
            parsed_environment = int(environment_value.strip())
        except ValueError:
            raise ValueError(
                "CDY_AGENT_MAX_MODEL_CALLS must be a positive integer."
            ) from None
        return _positive_int(parsed_environment, "CDY_AGENT_MAX_MODEL_CALLS")

    if workspace_config and workspace_config.max_model_calls is not None:
        return _positive_int(workspace_config.max_model_calls, "max_model_calls")

    return DEFAULT_MAX_MODEL_CALLS


def resolve_rebuild_frontend(
    rebuild_override: bool | None = None,
    workspace_config: WorkspaceConfig | None = None,
) -> bool:
    """Resolve whether to rebuild the Web frontend from CLI, environment, config, or default.

    Defaults to False: when built assets already exist the build is skipped, and it
    only runs once when assets are missing. A True value forces a fresh build.
    """
    if rebuild_override is not None:
        return rebuild_override

    environment_rebuild = os.getenv("CDY_AGENT_REBUILD_FRONTEND")
    if environment_rebuild is not None:
        return _parse_bool(environment_rebuild, "CDY_AGENT_REBUILD_FRONTEND")

    if workspace_config and workspace_config.rebuild_frontend is not None:
        return workspace_config.rebuild_frontend

    return False


def _optional_string(value: Any, name: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool) or isinstance(value, (dict, list)):
        raise ValueError(f"Workspace config {name} must be a scalar value.")
    return str(value)


def _optional_bool(value: Any, name: str) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ValueError(f"Workspace config {name} must be true or false.")
    return value


def _optional_positive_int(value: Any, name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"Workspace config {name} must be a positive integer.")
    return value


def _positive_int(value: int, name: str) -> int:
    if isinstance(value, bool) or value < 1:
        raise ValueError(f"{name} must be a positive integer.")
    return value


def _parse_bool(value: str, name: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Unsupported {name} value {value!r}. Choose true or false.")
