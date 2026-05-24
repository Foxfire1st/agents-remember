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


def write_text(path: Path, text: str, *, encoding: str = "utf-8") -> int:
    return extended_path(path).write_text(text, encoding=encoding)
