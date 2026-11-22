"""Temporary roots for tool-answer tests that measure real rendered paths."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

_ROW_CASE_ROOT_PREFIX = "rowcase-ambient-width-fixed-for-row-bounds-"


@contextmanager
def fixed_width_row_case_root(
    *, prefix: str = _ROW_CASE_ROOT_PREFIX, width: int | None = None
) -> Iterator[Path]:
    """Control the real fixture paths that contribute tokens to a row."""
    parent = "/tmp"
    if width is not None:
        # TemporaryDirectory appends an eight-character unique suffix.
        padding = width - len(os.fsencode(f"{parent}/{prefix}")) - 8
        prefix += "x" * padding
    with TemporaryDirectory(prefix=prefix, dir=parent) as temporary:
        root = Path(temporary)
        xdg = root / "xdg"
        xdg.mkdir()
        with patch.dict(
            os.environ,
            {
                "XDG_CONFIG_HOME": xdg.as_posix(),
                "XDG_DATA_HOME": xdg.as_posix(),
                "XDG_CACHE_HOME": xdg.as_posix(),
            },
        ):
            yield root
