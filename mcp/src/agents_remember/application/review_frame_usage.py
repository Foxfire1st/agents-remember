"""Disk-usage accounting and physical confinement helpers for the review frame store.

Keeps byte metering separate from publication logic and counts every physical byte in the task
frame area, including revision metadata and any persisted ``revisions/<n>/inventory.json`` sidecar,
while validating physical containment on every entry.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

from agents_remember.application.review_frame_paths import (
    READY_POINTER,
    RECLAMATION_NAME,
    SEAT_COUNTER_NAME,
)
from agents_remember.models.knowledge.review_frame import FrameDraft

__all__ = [
    "area_usage",
    "directory_size",
    "draft_usage_bytes",
    "escaped",
    "lineage_usage",
    "revision_usage_bytes",
]


def escaped(root: Path, target: Path) -> bool:
    """Whether ``target`` leaves ``root`` physically, through ``..`` or a symlinked component."""

    resolved = target.resolve()
    if resolved != root and root not in resolved.parents:
        return True
    probe = root
    try:
        relative = target.relative_to(root)
    except ValueError:
        return True
    for part in relative.parts:
        probe = probe / part
        if probe.is_symlink():
            return True
    return False


def directory_size(
    path: Path,
    *,
    refuse: type[ValueError] = ValueError,
) -> int:
    """Return total file bytes under ``path``, checking physical containment on every entry."""

    if escaped(Path(path.anchor), path):
        raise refuse(f"the store path {path} is not physically inside its admitted area")
    if path.is_file():
        return path.stat().st_size
    size = 0
    for item in path.rglob("*"):
        if escaped(path, item):
            raise refuse(f"the store child {item} is not physically inside its admitted area")
        if item.is_file():
            size += item.stat().st_size
    return size


def revision_usage_bytes(revision_dir: Path, *, refuse: type[ValueError] = ValueError) -> int:
    """Return all metered physical bytes for one revision directory."""

    return directory_size(revision_dir, refuse=refuse)


def draft_usage_bytes(
    directory: Path,
    *,
    read_draft: Callable[[Path], FrameDraft | None],
    refuse: type[ValueError] = ValueError,
) -> int:
    """Return on-disk bytes plus reserved asset bytes for one draft directory."""

    draft = read_draft(directory)
    reserved = (
        sum(asset.byte_size for asset in draft.assets)
        if draft and draft.state != "published"
        else 0
    )
    return directory_size(directory, refuse=refuse) + reserved


def lineage_usage(
    frame_dir: Path,
    *,
    revisions: Sequence[tuple[int, Path]],
    draft_directories: Sequence[Path],
    read_draft: Callable[[Path], FrameDraft | None],
    refuse: type[ValueError] = ValueError,
) -> dict[str, int]:
    """Count one lineage's metered revision, draft, and metadata bytes."""

    rev_bytes = sum(
        revision_usage_bytes(directory, refuse=refuse) for _number, directory in revisions
    )
    draft_bytes = sum(
        draft_usage_bytes(directory, read_draft=read_draft, refuse=refuse)
        for directory in draft_directories
    )
    pointer_path = frame_dir / READY_POINTER
    pointer = directory_size(pointer_path, refuse=refuse) if pointer_path.is_file() else 0
    reclamation_path = frame_dir / RECLAMATION_NAME
    reclamation = (
        directory_size(reclamation_path, refuse=refuse) if reclamation_path.is_file() else 0
    )
    seat_path = frame_dir / SEAT_COUNTER_NAME
    seat_counter = directory_size(seat_path, refuse=refuse) if seat_path.is_file() else 0
    metadata = pointer + reclamation + seat_counter
    return {
        "revisions": rev_bytes,
        "drafts": draft_bytes,
        "metadata": metadata,
        "total": rev_bytes + draft_bytes + metadata,
    }


def area_usage(
    area: Path,
    *,
    lineage_bytes: Callable[[Path], dict[str, int]],
    refuse: type[ValueError] = ValueError,
) -> int:
    """Count metered bytes across every leaf and frame in a task area."""

    if not area.is_dir():
        return 0
    total = 0
    for leaf in area.iterdir():
        if escaped(area, leaf):
            raise refuse(f"the task store path {leaf} is not physically inside its area")
        if leaf.is_dir():
            for frame in leaf.iterdir():
                if escaped(area, frame):
                    raise refuse(f"the frame {frame} is not physically inside its area")
                if frame.is_dir():
                    total += lineage_bytes(frame)["total"]
    return total
