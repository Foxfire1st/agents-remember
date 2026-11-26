"""Sidecar read/write helpers for per-revision explainer inventory metadata.

The inventory JSON lives at ``revisions/<n>/inventory.json`` beside ``manifest.json``,
keeping the revision manifest schema unchanged and ensuring the inventory file is neither
an admitted asset nor served to the explanation iframe.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from agents_remember.application.review_frame_paths import (
    REVISIONS_DIRECTORY,
    task_lock_for_frame_dir,
)
from agents_remember.application.review_frame_read import read_frame_bytes
from agents_remember.kernel.atomic_write import atomic_write_bytes
from agents_remember.kernel.canonical_json import canonical_json_bytes
from agents_remember.kernel.file_lock import LockAcquisitionTimeout, exclusive_file_lock

INVENTORY_NAME = "inventory.json"
INVENTORY_OMITTED_STORAGE_LIMIT_NAME = "inventory.omitted-storage-limit"
INVENTORY_OMITTED_STORAGE_LIMIT_REASON = (
    "inventory left out because the task's storage limit was reached"
)


def encode_revision_inventory(inventory: Mapping[str, Any]) -> bytes:
    """Serialize one revision inventory mapping as canonical UTF-8 JSON bytes."""

    return canonical_json_bytes(dict(inventory))


def write_revision_inventory(revision_dir: Path, encoded: bytes) -> None:
    """Write ``inventory.json`` atomically inside one revision directory."""

    atomic_write_bytes(revision_dir / INVENTORY_NAME, encoded)


def persist_revision_inventory(
    frame_dir: Path,
    revision: int,
    inventory: Mapping[str, Any],
) -> bool:
    """Persist ``revisions/<revision>/inventory.json`` after publication without failing it."""

    revision_dir = frame_dir / REVISIONS_DIRECTORY / str(revision)
    if revision_dir.is_symlink() or not revision_dir.is_dir():
        return False
    try:
        write_revision_inventory(revision_dir, encode_revision_inventory(inventory))
        marker = revision_dir / INVENTORY_OMITTED_STORAGE_LIMIT_NAME
        if marker.exists() and not marker.is_symlink():
            marker.unlink()
    except (OSError, TypeError, ValueError):
        return False
    return True


def _write_omitted_marker(revision_dir: Path) -> None:
    with contextlib.suppress(OSError):
        atomic_write_bytes(revision_dir / INVENTORY_OMITTED_STORAGE_LIMIT_NAME, b"")


def prepare_revision_inventory_staging(
    inventory: Mapping[str, Any] | None,
    *,
    used_after_publish: int,
    limit_bytes: int,
) -> tuple[str, bytes] | None:
    """Choose ``inventory.json`` or its zero-byte omission marker before revision promotion."""

    if inventory is None:
        return None
    try:
        encoded = encode_revision_inventory(inventory)
    except (TypeError, ValueError):
        return None
    if used_after_publish + len(encoded) > limit_bytes:
        return (INVENTORY_OMITTED_STORAGE_LIMIT_NAME, b"")
    return (INVENTORY_NAME, encoded)


def write_staged_revision_inventory(
    staging_dir: Path,
    staged: tuple[str, bytes] | None,
) -> None:
    """Write a prepared advisory sidecar or omission marker into staging without failing publish."""

    if staged is None:
        return
    name, payload = staged
    with contextlib.suppress(OSError):
        if name == INVENTORY_NAME:
            write_revision_inventory(staging_dir, payload)
        else:
            atomic_write_bytes(staging_dir / name, payload)


def _persist_inventory_locked(
    frame_dir: Path,
    revision: int,
    inventory: Mapping[str, Any],
    limit_bytes: int,
    measure_used_bytes: Callable[[], int],
) -> tuple[bool, str | None]:
    revision_dir = frame_dir / REVISIONS_DIRECTORY / str(revision)
    if revision_dir.is_symlink() or not revision_dir.is_dir():
        return False, None
    sidecar = revision_dir / INVENTORY_NAME
    if sidecar.is_file() and not sidecar.is_symlink():
        return True, None
    marker = revision_dir / INVENTORY_OMITTED_STORAGE_LIMIT_NAME
    if marker.is_file() and not marker.is_symlink():
        return False, INVENTORY_OMITTED_STORAGE_LIMIT_REASON
    encoded = encode_revision_inventory(inventory)
    try:
        used_bytes = measure_used_bytes()
    except (OSError, ValueError):
        used_bytes = limit_bytes
    if used_bytes + len(encoded) > limit_bytes:
        _write_omitted_marker(revision_dir)
        return False, INVENTORY_OMITTED_STORAGE_LIMIT_REASON
    return (
        (True, None)
        if persist_revision_inventory(frame_dir, revision, inventory)
        else (False, None)
    )


def persist_revision_inventory_within_limit(
    frame_dir: Path,
    revision: int,
    inventory: Mapping[str, Any],
    *,
    limit_bytes: int,
    measure_used_bytes: Callable[[], int],
) -> tuple[bool, str | None]:
    """Persist ``inventory.json`` under the task frame lock only if live usage fits ``limit_bytes``.

    Takes the store's shared task lock so measurement and sidecar persistence are atomic with
    respect to every leaf publication in the same task pool. When no room remains, nothing is
    evicted and ``inventory.json`` is omitted; a zero-byte marker records the reason so the
    dashboard can explain the absence without adding a single byte to the task store.
    """

    revision_dir = frame_dir / REVISIONS_DIRECTORY / str(revision)
    if revision_dir.is_symlink() or not revision_dir.is_dir():
        return False, None
    resource = task_lock_for_frame_dir(frame_dir)
    try:
        with exclusive_file_lock(resource, "review frame store"):
            return _persist_inventory_locked(
                frame_dir, revision, inventory, limit_bytes, measure_used_bytes
            )
    except (OSError, TypeError, ValueError, LockAcquisitionTimeout):
        return False, None


def revision_inventory(frame_dir: Path, revision: int) -> dict[str, Any] | None:
    """Read ``revisions/<revision>/inventory.json``, returning ``None`` when absent or unreadable."""

    path = frame_dir / REVISIONS_DIRECTORY / str(revision) / INVENTORY_NAME
    try:
        payload = read_frame_bytes(path)
    except OSError:
        return None
    try:
        parsed = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def revision_inventory_omitted_reason(frame_dir: Path, revision: int) -> str | None:
    """Return the plain omission reason when ``inventory.json`` was left out at the storage limit."""

    revision_dir = frame_dir / REVISIONS_DIRECTORY / str(revision)
    if (revision_dir / INVENTORY_NAME).exists():
        return None
    marker = revision_dir / INVENTORY_OMITTED_STORAGE_LIMIT_NAME
    try:
        read_frame_bytes(marker)
    except OSError:
        return None
    return INVENTORY_OMITTED_STORAGE_LIMIT_REASON
