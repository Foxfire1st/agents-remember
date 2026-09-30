"""Record the files a computation read outside any Git tree, with their exact identities.

A result computed from Git trees is a function of their content-addressed IDs, so a caller may
reuse it for the same IDs. A computation that also reads plain files -- a task's requirement
manifest, coordination settings -- can only be reused while those files are unchanged. Inside
:func:`recorded_reads` every reader that calls :func:`record_read` adds ``{path: identity}``:

* ``sha256:<hex>`` of the exact bytes read;
* ``absent`` when the file does not exist;
* ``unreadable (<error>)`` when it exists but cannot be read.

A path recorded twice with different identities in one computation (the file changed while it was
being read) is marked :data:`CONFLICTING`, so the caller knows the result cannot be reused.
:func:`file_identity` answers the same question now, for comparison. Outside a recording block,
:func:`record_read` does nothing.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Final

__all__ = [
    "ABSENT",
    "CONFLICTING",
    "bytes_identity",
    "file_identity",
    "record_read",
    "recorded_reads",
]

ABSENT: Final = "absent"
CONFLICTING: Final = "conflicting reads"
_READS: ContextVar[dict[str, str] | None] = ContextVar("recorded_reads", default=None)


def bytes_identity(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def file_identity(path: Path) -> str:
    """The identity of a file now: its bytes' SHA-256, ``absent``, or ``unreadable (...)``."""

    try:
        return bytes_identity(path.read_bytes())
    except FileNotFoundError:
        return ABSENT
    except OSError as error:
        return f"unreadable ({type(error).__name__})"


@contextmanager
def recorded_reads() -> Iterator[dict[str, str]]:
    """Record every read inside the block as ``{path: identity}``."""

    reads: dict[str, str] = {}
    token = _READS.set(reads)
    try:
        yield reads
    finally:
        _READS.reset(token)


def record_read(path: Path, identity: str | None = None) -> None:
    """Record one read of ``path`` (its identity now, unless the reader passes what it read)."""

    reads = _READS.get()
    if reads is None:
        return
    key = path.as_posix()
    seen = file_identity(path) if identity is None else identity
    previous = reads.get(key)
    reads[key] = seen if previous is None or previous == seen else CONFLICTING
