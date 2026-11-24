"""Request-scoped publish staging and advisory inventory helpers for explainer renders."""

from __future__ import annotations

import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from uuid import uuid4

from agents_remember.application.review_frame_inventory import (
    persist_revision_inventory_within_limit,
    revision_inventory,
    revision_inventory_omitted_reason,
)
from agents_remember.application.review_frame_store import (
    FramePublication,
    FrameScope,
    read_draft,
    task_lock,
)
from agents_remember.application.review_planned_comparison import task_frames_total
from agents_remember.canvas.explainer_paths import DRAFTS_DIRECTORY, STATE_DIRECTORY
from agents_remember.canvas.inventory import build_inventory
from agents_remember.models.knowledge.review_frame import FrameDraft

__all__ = [
    "PUBLISH_STAGING_DIRECTORY",
    "active_staging_dirs",
    "discard_unused_staging",
    "observed_assets",
    "prune_finished_staging",
    "publish_working_directory",
    "record_published_inventory",
    "safe_build_inventory",
]

PUBLISH_STAGING_DIRECTORY = "publish-staging"


def publish_working_directory(
    workspace: Path,
    drafts: Path,
    carried_kit: Mapping[str, bytes],
    *,
    draft_digest: str,
    earlier: FrameDraft | None = None,
) -> tuple[Path, bool]:
    """Allocate a request-scoped staging folder for carried kit drafts without clobbering active ones."""

    if not carried_kit:
        return drafts.resolve(), False
    staging_root = (workspace / STATE_DIRECTORY / PUBLISH_STAGING_DIRECTORY).resolve()
    if earlier is not None:
        reused = Path(earlier.working_directory)
        if reused.is_absolute() and (reused == drafts.resolve() or reused.parent == staging_root):
            return reused, False
    staging_root.mkdir(parents=True, exist_ok=True)
    staging = staging_root / f"{draft_digest[:16]}-{uuid4().hex[:8]}"
    staging.mkdir(parents=True, exist_ok=True)
    return staging, True


def active_staging_dirs(scope: FrameScope) -> set[Path]:
    """Return resolved working directories currently held by active drafts in ``scope``."""

    drafts_root = scope.frame_dir / DRAFTS_DIRECTORY
    if not drafts_root.is_dir() or drafts_root.is_symlink():
        return set()
    active: set[Path] = set()
    for entry in drafts_root.iterdir():
        if entry.is_symlink() or not entry.is_dir():
            continue
        draft = read_draft(scope, entry.name)
        if draft is not None and draft.state == "active":
            active.add(Path(draft.working_directory).resolve())
    return active


def prune_finished_staging(workspace: Path, scope: FrameScope) -> None:
    """Reclaim completed request staging folders not referenced by any active draft."""

    staging_root = (workspace / STATE_DIRECTORY / PUBLISH_STAGING_DIRECTORY).resolve()
    if not staging_root.is_dir() or staging_root.is_symlink():
        return
    entries = [
        entry for entry in staging_root.iterdir() if not entry.is_symlink() and entry.is_dir()
    ]
    if not entries:
        return
    active = active_staging_dirs(scope)
    for entry in entries:
        if entry.resolve() in active:
            continue
        shutil.rmtree(entry, ignore_errors=True)


def discard_unused_staging(publish_working: Path, adopted: FrameDraft | None) -> None:
    """Remove a freshly created staging folder when the store did not adopt it."""

    if (
        adopted is not None
        and Path(adopted.working_directory).resolve() == publish_working.resolve()
    ):
        return
    shutil.rmtree(publish_working, ignore_errors=True)


def observed_assets(report: Mapping[str, Any]) -> tuple[str, ...]:
    """Read runtime-loaded asset paths from the prospective browser load report."""

    raw = report.get("loaded_assets")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return ()
    return tuple(item for item in raw if isinstance(item, str) and item)


def safe_build_inventory(
    draft_assets: Mapping[str, bytes],
    *,
    report: Mapping[str, Any],
    carried_paths: Sequence[str],
) -> dict[str, Any] | None:
    """Compute revision inventory without letting an inventory failure block publication."""

    dom_inventory = report.get("dom_inventory")
    try:
        return build_inventory(
            draft_assets,
            entrypoint="index.html",
            dom_inventory=dom_inventory if isinstance(dom_inventory, Mapping) else None,
            carried_kit_paths=carried_paths,
            observed_assets=observed_assets(report),
        )
    except (TypeError, ValueError, KeyError, AttributeError, OSError):
        return None


def _task_used_bytes(task_root: Path, leaf_id: str, fallback: int) -> int:
    usage_info = task_frames_total(task_root, leaf_id)
    if usage_info.get("state") == "ok" and isinstance(usage_info.get("bytes"), int):
        return int(usage_info["bytes"])
    return fallback


def record_published_inventory(
    scope: FrameScope,
    publication: FramePublication,
    inventory: Mapping[str, Any] | None,
    limit_bytes: int,
    report: dict[str, Any],
) -> None:
    """Persist ``inventory.json`` atomically under the task frame lock, or mark it omitted."""

    manifest = publication.manifest
    if manifest is None:
        return
    omitted_reason: str | None = None
    if publication.published:
        if inventory is None:
            return
        omitted_reason = revision_inventory_omitted_reason(scope.frame_dir, manifest.revision)
        if omitted_reason is None:
            if revision_inventory(scope.frame_dir, manifest.revision) is not None:
                return
            task_lock(scope)
            persisted, omitted_reason = persist_revision_inventory_within_limit(
                scope.frame_dir,
                manifest.revision,
                inventory,
                limit_bytes=limit_bytes,
                measure_used_bytes=lambda: _task_used_bytes(
                    scope.task_root, manifest.binding.leaf_id, limit_bytes
                ),
            )
            if persisted:
                return
    else:
        omitted_reason = revision_inventory_omitted_reason(scope.frame_dir, manifest.revision)
    if omitted_reason is not None:
        report.pop("inventory", None)
        report["inventoryOmitted"] = omitted_reason
        report["publication"]["inventoryOmitted"] = omitted_reason
