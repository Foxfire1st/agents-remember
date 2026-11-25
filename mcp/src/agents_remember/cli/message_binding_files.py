"""The file content of a message-binding projection: its canonical bytes and the match check."""

from __future__ import annotations

import json
import stat
from pathlib import Path
from typing import Any


def message_binding_projection_bytes(binding: dict[str, Any]) -> bytes:
    """The canonical bytes of one binding: sorted keys, compact separators, one closing newline."""

    return (
        json.dumps(binding, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def message_binding_projection_matches(path: Path, expected: bytes) -> bool:
    """Whether ``path`` already holds exactly ``expected``; a file holding anything else refuses.

    A projection is immutable once written, so a different body or a non-regular file at the path
    is an error rather than something to overwrite.
    """

    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(mode):
        raise ValueError("The message-binding projection path is not a regular file.")
    if path.read_bytes() != expected:
        raise ValueError(
            "This request ID already has a different immutable message-binding projection."
        )
    return True
