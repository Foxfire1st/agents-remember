"""Request-scoped publish staging and advisory inventory helpers for explainer renders."""

from __future__ import annotations

import shutil
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
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
    begin_draft,
    read_draft,
    task_lock,
)
from agents_remember.application.review_planned_comparison import task_frames_total
from agents_remember.canvas.explainer_paths import DRAFTS_DIRECTORY, STATE_DIRECTORY
from agents_remember.canvas.inventory import build_inventory
from agents_remember.canvas.paths import PathRefused, guard_path
from agents_remember.kernel.file_lock import exclusive_file_lock
from agents_remember.models.knowledge.review_frame import (
    FrameAuthoring,
    FrameDraft,
    FrameLimits,
)

__all__ = [
    "PUBLISH_STAGING_DIRECTORY",
    "DraftStagingRequest",
    "active_staging_dirs",
    "discard_unused_staging",
    "observed_assets",
    "prune_finished_staging",
    "publish_working_directory",
    "record_published_inventory",
    "safe_build_inventory",
    "stage_and_begin_draft",
]

PUBLISH_STAGING_DIRECTORY = "publish-staging"


@dataclass(frozen=True)
class DraftStagingRequest:
    """Inputs for locked draft staging and frame-store admission."""

    drafts: Path
    request_id: str
    seat: str | None
    moment: str
    focus: str
    assets: Mapping[str, bytes]
    carried_kit: Mapping[str, bytes]
    draft_digest: str
    limits: FrameLimits


def _staging_root(workspace: Path) -> Path:
    """Guard ``<workspace>/state/publish-staging`` without resolving symlinks first."""

    return guard_path(
        workspace,
        workspace / STATE_DIRECTORY / PUBLISH_STAGING_DIRECTORY,
        label="publish staging root",
    )


def _guard_recorded_path(workspace: Path, target: Path, *, label: str) -> Path:
    """Guard a recorded working path using ``workspace.resolve()`` when recorded resolved."""

    resolved_workspace = workspace.resolve()
    root = workspace if target.is_relative_to(workspace) else resolved_workspace
    return guard_path(root, target, label=label)


def publish_working_directory(
    workspace: Path,
    drafts: Path,
    carried_kit: Mapping[str, bytes],
    *,
    draft_digest: str,
    earlier: FrameDraft | None = None,
) -> tuple[Path, bool]:
    """Allocate a request-scoped staging folder for carried kit drafts without clobbering active ones."""

    staging_root = _staging_root(workspace)
    guarded_drafts = guard_path(workspace, drafts, label="explainer drafts")
    resolved_drafts = guarded_drafts.resolve()
    if not carried_kit:
        return resolved_drafts, False
    resolved_staging_root = staging_root.resolve()
    if earlier is not None:
        reused = Path(earlier.working_directory)
        if not reused.is_absolute():
            raise PathRefused(f"reused publish staging {reused} is not an absolute path")
        guarded_reused = _guard_recorded_path(workspace, reused, label="reused publish staging")
        if (
            guarded_reused.resolve() == resolved_drafts
            or guarded_reused.parent == resolved_staging_root
        ):
            return guarded_reused, False
    staging_root.mkdir(parents=True, exist_ok=True)
    _staging_root(workspace)
    staging = staging_root / f"{draft_digest[:16]}-{uuid4().hex[:8]}"
    guard_path(workspace, staging, label="publish staging directory")
    staging.mkdir(parents=True, exist_ok=True)
    guard_path(workspace, staging, label="publish staging directory")
    return staging.resolve(), True


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
    """Reclaim completed request staging folders under the task lock without leaving ``workspace``."""

    with exclusive_file_lock(task_lock(scope), "review frame store"):
        staging_root = _staging_root(workspace)
        if not staging_root.is_dir():
            return
        entries = [
            entry for entry in staging_root.iterdir() if not entry.is_symlink() and entry.is_dir()
        ]
        if not entries:
            return
        active = active_staging_dirs(scope)
        for entry in entries:
            guard_path(workspace, entry, label="publish staging entry")
            if entry.resolve() in active:
                continue
            guard_path(workspace, entry, label="publish staging entry")
            shutil.rmtree(entry, ignore_errors=True)


def discard_unused_staging(
    workspace: Path, publish_working: Path, adopted: FrameDraft | None
) -> None:
    """Remove a freshly created staging folder inside ``workspace`` when the store did not adopt it."""

    staging_root = _staging_root(workspace)
    guarded = _guard_recorded_path(workspace, publish_working, label="publish staging directory")
    resolved_staging_root = staging_root.resolve()
    resolved_target = guarded.resolve()
    guard_path(resolved_staging_root, resolved_target, label="publish staging directory")
    if resolved_target == resolved_staging_root:
        raise PathRefused(f"refusing to discard the publish staging root {publish_working}")
    if adopted is not None and Path(adopted.working_directory).resolve() == resolved_target:
        return
    shutil.rmtree(guarded, ignore_errors=True)


def stage_and_begin_draft(
    workspace: Path,
    scope: FrameScope,
    request: DraftStagingRequest,
) -> FrameDraft:
    """Read earlier draft, stage working directory, and admit the draft under one task lock."""

    with exclusive_file_lock(task_lock(scope), "review frame store"):
        earlier = read_draft(scope, request.request_id)
        publish_working, created_staging = publish_working_directory(
            workspace,
            request.drafts,
            request.carried_kit,
            draft_digest=request.draft_digest,
            earlier=earlier,
        )
        began_draft = False
        try:
            admitted = begin_draft(
                scope,
                request_id=request.request_id,
                authoring=(
                    earlier.authoring
                    if earlier is not None
                    else FrameAuthoring(
                        request_id=request.request_id,
                        session_id=request.seat or None,
                        started_at=request.moment,
                        finished_at=request.moment,
                    )
                ),
                focus=request.focus,
                assets=request.assets,
                working_directory=publish_working,
                limits=request.limits,
                now=request.moment,
            )
            began_draft = True
            if created_staging:
                discard_unused_staging(workspace, publish_working, admitted)
            return admitted
        except BaseException:
            if created_staging and not began_draft:
                with suppress(PathRefused):
                    discard_unused_staging(
                        workspace, publish_working, read_draft(scope, request.request_id)
                    )
            raise


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
