"""Shared pytest fixtures."""

from __future__ import annotations

import errno
import os
from collections.abc import Callable
from pathlib import Path

import pytest


@pytest.fixture
def make_symlink() -> Callable[..., None]:
    """Create a symlink or skip when the current process cannot create one."""

    def create(
        target: Path,
        link: Path,
        *,
        target_is_directory: bool = False,
    ) -> None:
        try:
            os.symlink(target, link, target_is_directory=target_is_directory)
        except (NotImplementedError, OSError) as exc:
            unsupported_errors = {
                errno.EACCES,
                errno.EPERM,
                errno.ENOSYS,
                getattr(errno, "EOPNOTSUPP", errno.ENOSYS),
            }
            if (
                isinstance(exc, NotImplementedError)
                or exc.errno in unsupported_errors
                or getattr(exc, "winerror", None) == 1314
            ):
                pytest.skip(f"symbolic link creation is unavailable: {exc}")
            raise

    return create
