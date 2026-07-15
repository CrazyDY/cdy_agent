"""Application configuration for CDY Agent."""

from __future__ import annotations

import os


DEFAULT_MODEL = "gpt-5.6-terra"
DEFAULT_API_MODE = "responses"
SUPPORTED_API_MODES = ("responses", "chat_completions")


def resolve_model(model_override: str | None = None) -> str:
    """Resolve the model from a CLI override, environment, or default."""
    if model_override and model_override.strip():
        return model_override.strip()

    environment_model = os.getenv("CDY_AGENT_MODEL")
    if environment_model and environment_model.strip():
        return environment_model.strip()

    return DEFAULT_MODEL


def resolve_api_mode() -> str:
    """Resolve and validate the configured OpenAI-compatible API mode."""
    configured_mode = os.getenv("CDY_AGENT_API_MODE")
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
