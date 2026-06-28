"""Filesystem helpers for paths that may exceed Windows MAX_PATH."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def absolute_path(path: Path) -> Path:
    if path.is_absolute():
        return path
    return Path.cwd() / path


def extended_path(path: Path) -> Path:
    if sys.platform != "win32":
        return path

    text = str(absolute_path(path))
    if text.startswith("\\\\?\\"):
        return Path(text)
    if text.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + text.lstrip("\\"))
    return Path("\\\\?\\" + text)


def exists(path: Path) -> bool:
    return os.path.exists(os.fspath(extended_path(path)))


def is_file(path: Path) -> bool:
    return os.path.isfile(os.fspath(extended_path(path)))


def mkdir(path: Path, *, parents: bool = False, exist_ok: bool = False) -> None:
    extended_path(path).mkdir(parents=parents, exist_ok=exist_ok)


def read_text(path: Path, *, encoding: str = "utf-8") -> str:
    return extended_path(path).read_text(encoding=encoding)


def read_text_range(
    path: Path, start_line: int, end_line: int, *, encoding: str = "utf-8"
) -> str:
    """Return lines ``[start_line, end_line]`` (1-based, inclusive) of a file.

    Net-new ranged reader (``read_text`` is whole-file). ``end_line`` is clamped
    to EOF -- a range that runs past the file's end yields what exists, never an
    error. A ``start_line`` beyond EOF yields the empty string. ``start_line``
    below 1 is clamped to 1. The caller is responsible for honoring the
    "no silent truncation" rule: a ``"full"`` request must use :func:`read_text`,
    never this helper.
    """
    if end_line < start_line:
        return ""
    text = read_text(path, encoding=encoding)
    lines = text.splitlines(keepends=True)
    lo = max(start_line, 1)
    hi = min(end_line, len(lines))
    if lo > len(lines) or hi < lo:
        return ""
    return "".join(lines[lo - 1 : hi])


def write_text(path: Path, text: str, *, encoding: str = "utf-8") -> int:
    return extended_path(path).write_text(text, encoding=encoding)
