"""Application configuration for CDY Agent."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


DEFAULT_MODEL = "gpt-5.6-terra"
DEFAULT_API_MODE = "responses"
SUPPORTED_API_MODES = ("responses", "chat_completions")
CONFIG_RELATIVE_PATH = Path(".cdy-agent") / "config.yaml"


@dataclass(frozen=True)
class WorkspaceConfig:
    model: str | None = None
    api_mode: str | None = None
    log_level: str | None = None
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

    allowed_top_level = {"model", "api_mode", "log_level", "observability"}
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
        log_level=_optional_string(raw_config.get("log_level"), "log_level"),
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


def _optional_string(value: Any, name: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool) or isinstance(value, (dict, list)):
        raise ValueError(f"Workspace config {name} must be a scalar value.")
    return str(value)
