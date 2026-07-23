from __future__ import annotations

import os


MAX_OUTPUT_BYTES = 64 * 1024


def sanitized_environment() -> dict[str, str]:
    environment = dict(os.environ)
    environment.pop("GIT_EXTERNAL_DIFF", None)
    environment.pop("RIPGREP_CONFIG_PATH", None)
    environment.update({"GIT_PAGER": "cat", "PAGER": "cat"})
    return environment


def limited_output(
    output: str, limit: int = MAX_OUTPUT_BYTES
) -> tuple[str, bool]:
    encoded = output.encode("utf-8")
    if len(encoded) <= limit:
        return output, False
    limited = encoded[:limit]
    return limited.decode("utf-8", errors="ignore"), True
