"""Publish file content atomically through the package's single owner.

``atomic_write_text`` and ``atomic_write_bytes`` create a per-call temporary file in the
destination directory, flush and fsync its content, replace the destination, then fsync
the directory. ``atomic_replace`` applies the same replace-and-directory-fsync contract to
an existing temporary path. Failed publishes remove their temporary file.

An empty payload replaces the destination with an empty file; it never unlinks the
destination. These functions do not lock. Callers that require serialization must hold
their store's lock before publishing.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from uuid import uuid4


def _temp_path_for(path: Path) -> Path:
    """The private temp this module writes before it replaces ``path``.

    Hidden (leading dot) so a directory listing or a ``*.json`` glob never picks up a
    half-written file, and unique per call: the pid separates processes and the uuid
    separates threads inside one. A fixed ``<name>.tmp`` -- what four of the thirteen
    call sites used -- is shared by every concurrent writer of the same destination.
    """
    return path.with_name(f".{path.name}.{os.getpid()}.{uuid4().hex}.tmp")


def _fsync_directory(directory: Path) -> None:
    """Flush ``directory``'s own entries so a completed rename survives a host loss.

    Skipped on Windows, which has no directory handle to flush: ``os.open`` on a
    directory raises ``PermissionError`` there, so the unguarded form that
    ``durable_store`` carried would have turned every durable write on that platform
    into a crash. The file's own fsync and the atomic replace still hold; only the
    durability of the *directory entry* is unavailable, and it is unavailable because
    the platform does not offer it rather than because this module skipped it.
    """
    if sys.platform == "win32":
        return
    handle = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(handle)
    finally:
        os.close(handle)


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    """Publish ``payload`` at ``path``: readers see the old file or the new one, never both.

    Creates ``path``'s parent, writes and fsyncs a private temp, replaces the
    destination, then fsyncs the directory. The temp is removed on any failure --
    ``BaseException``, so a ``KeyboardInterrupt`` or a cancellation between the write and
    the replace does not leave an orphan either.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _temp_path_for(path)
    try:
        with tmp.open("wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    _fsync_directory(path.parent)


def atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    """:func:`atomic_write_bytes` for text. The encoding is explicit, never the locale's."""
    atomic_write_bytes(path, text.encode(encoding))


def atomic_replace(source: Path, destination: Path) -> None:
    """Move an already-written ``source`` onto ``destination`` atomically and durably.

    The other half of the primitive: two call sites do not write new bytes at all --
    ``serving/daemon`` rotates a log aside and ``serving/conversation/control/asset_spool``
    promotes spooled bytes into a fresh one-use identity -- but they reach the same
    :func:`os.replace`, so they reach it through here rather than around it.

    Both directories are fsynced because a cross-directory rename changes two of them;
    when they are the same directory it is flushed once.
    """
    os.replace(source, destination)
    _fsync_directory(destination.parent)
    if source.parent != destination.parent:
        _fsync_directory(source.parent)
