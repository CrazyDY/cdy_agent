"""Application configuration for CDY Agent."""

from __future__ import annotations

import os


DEFAULT_MODEL = "gpt-5.6-terra"


def resolve_model(model_override: str | None = None) -> str:
    """Resolve the model from a CLI override, environment, or default."""
    if model_override and model_override.strip():
        return model_override.strip()

    environment_model = os.getenv("CDY_AGENT_MODEL")
    if environment_model and environment_model.strip():
        return environment_model.strip()

    return DEFAULT_MODEL
