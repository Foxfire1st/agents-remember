"""Revision inventory view projection helpers for explainer HTTP routes."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from agents_remember.canvas.explainer_paths import EXPLAINER_FRAME
from agents_remember.canvas.inventory import inventory_summary
from agents_remember.models.knowledge.review_frame import FrameManifest

__all__ = [
    "InventoryViewPorts",
    "manifest_inventory_lines",
    "revision_inventory_lines",
]


class _FrameDirPort(Protocol):
    @property
    def frame_dir(self) -> Path: ...


class InventoryViewPorts(Protocol):
    """Minimal application port surface needed to read revision inventory summaries."""

    @property
    def frame_scope(self) -> Callable[[Path, str, str], _FrameDirPort]: ...

    @property
    def revision_inventory(self) -> Callable[[Path, int], dict[str, Any] | None] | None: ...

    @property
    def revision_inventory_omitted_reason(self) -> Callable[[Path, int], str | None] | None: ...


def revision_inventory_lines(
    frame_dir: Path,
    revision: int,
    application: InventoryViewPorts,
) -> tuple[str | None, str | None]:
    """Read ``(inventory_line, omitted_reason)`` for one revision using an already-resolved frame."""

    inventory_reader = application.revision_inventory
    omitted_reader = application.revision_inventory_omitted_reason
    inventory_line = (
        None
        if inventory_reader is None
        else inventory_summary(inventory_reader(frame_dir, revision))
    )
    omitted_line = None if omitted_reader is None else omitted_reader(frame_dir, revision)
    return inventory_line, omitted_line


def manifest_inventory_lines(
    resolve_task_root: Callable[[], Path],
    leaf_id: str,
    application: InventoryViewPorts,
    manifest: FrameManifest | None,
) -> tuple[str | None, str | None]:
    """Resolve one frame scope and return ``(inventory_line, omitted_reason)`` for ``manifest``."""

    if manifest is None or (
        application.revision_inventory is None
        and application.revision_inventory_omitted_reason is None
    ):
        return None, None
    try:
        frame = application.frame_scope(resolve_task_root(), leaf_id, EXPLAINER_FRAME)
    except ValueError:
        return None, None
    return revision_inventory_lines(frame.frame_dir, manifest.revision, application)
