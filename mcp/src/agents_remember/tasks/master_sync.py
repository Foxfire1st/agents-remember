"""Leaf-to-master row synchronization for JSON-primary task documents."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from .document import DocStatus, SubTaskRef, TaskDocument
from .readiness import completion_blockers
from .store import TaskDocSourceSnapshot, capture_task_doc_source, markdown_path_for

MasterSyncStatus = Literal["none", "created", "updated", "unchanged"]


class MasterSyncError(ValueError):
    """Raised when a resolvable parent master cannot be read or updated."""


@dataclass(frozen=True)
class MasterSyncPlan:
    status: MasterSyncStatus
    master: TaskDocument | None = None
    master_json_path: Path | None = None
    subtask_number: str | None = None
    source_snapshot: TaskDocSourceSnapshot | None = None

    @property
    def changed(self) -> bool:
        return self.status in {"created", "updated"}


def plan_master_sync(task_root: Path, leaf: TaskDocument) -> MasterSyncPlan:
    """Return the master document update implied by a leaf document, if any."""
    if leaf.kind != "subTask":
        return MasterSyncPlan(status="none")
    master_json_path = _master_json_path(task_root, leaf)
    if master_json_path is None or not master_json_path.exists():
        return MasterSyncPlan(status="none")
    try:
        source_snapshot = capture_task_doc_source(master_json_path)
        if source_snapshot.json_bytes is None:
            raise FileNotFoundError(master_json_path)
        master = TaskDocument.model_validate_json(source_snapshot.json_bytes)
    except (OSError, ValidationError, ValueError) as exc:
        raise MasterSyncError(
            f"cannot read parent master task document: {master_json_path}"
        ) from exc
    if master.kind != "master":
        raise MasterSyncError(f"parent task document is not a master: {master_json_path}")

    matches = [ref for ref in master.subTasks if ref.number == leaf.id]
    if len(matches) > 1:
        raise MasterSyncError(
            f"parent master must contain at most one row {leaf.id!r}; found {len(matches)}"
        )
    existing = matches[0] if matches else None
    _validate_existing_row_path(master_json_path, task_root, leaf, existing)
    ref = subtask_ref_from_leaf(task_root, leaf, existing=existing)
    if existing == ref:
        return MasterSyncPlan(
            status="unchanged",
            master=master,
            master_json_path=master_json_path,
            subtask_number=leaf.id,
            source_snapshot=source_snapshot,
        )

    data = master.model_dump(by_alias=True)
    refs = list(data.get("subTasks", []))
    ref_payload = ref.model_dump()
    if existing is None:
        refs.append(ref_payload)
        status: MasterSyncStatus = "created"
    else:
        index = next(index for index, item in enumerate(refs) if item.get("number") == leaf.id)
        refs[index] = ref_payload
        status = "updated"
    data["subTasks"] = refs
    updated = demote_completed_master_if_unresolved(TaskDocument.model_validate(data))
    return MasterSyncPlan(
        status=status,
        master=updated,
        master_json_path=master_json_path,
        subtask_number=leaf.id,
        source_snapshot=source_snapshot,
    )


def subtask_ref_from_leaf(
    task_root: Path, leaf: TaskDocument, *, existing: SubTaskRef | None = None
) -> SubTaskRef:
    """Map deterministic leaf facts into the parent master row."""
    return SubTaskRef(
        number=leaf.id,
        name=leaf.title,
        file=markdown_path_for(task_root, leaf).name,
        status=derived_master_status(leaf),
        scope=existing.scope if existing else "",
    )


def derived_master_status(leaf: TaskDocument) -> DocStatus:
    """Collapse leaf step state to the master's strict status vocabulary."""
    statuses = [step.status for step in leaf.steps]
    statuses.extend(sub.status for step in leaf.steps for sub in step.substeps)
    if statuses and not completion_blockers(leaf):
        return "Completed"
    if any(status in {"done", "inProgress", "blocked"} for status in statuses):
        return "inProgress"
    if statuses and leaf.status == "Completed":
        # An old inconsistent leaf remains readable, but cannot project a terminal parent row.
        return "inProgress"
    return leaf.status


def demote_completed_master_if_unresolved(master: TaskDocument) -> TaskDocument:
    """Keep a master terminal only while every declared row remains terminal."""
    if master.kind != "master" or master.status != "Completed" or not completion_blockers(master):
        return master
    data = master.model_dump(by_alias=True)
    data["status"] = "inProgress"
    return TaskDocument.model_validate(data)


def _validate_existing_row_path(
    master_json_path: Path,
    task_root: Path,
    leaf: TaskDocument,
    existing: SubTaskRef | None,
) -> None:
    if existing is None or not existing.file:
        return
    existing_path = (master_json_path.parent / existing.file).resolve(strict=False)
    leaf_path = markdown_path_for(task_root, leaf).resolve(strict=False)
    if existing_path != leaf_path:
        raise MasterSyncError(
            f"parent row {leaf.id!r} points at {existing_path}, not leaf {leaf_path}"
        )


def _master_json_path(task_root: Path, leaf: TaskDocument) -> Path | None:
    if leaf.master:
        return _json_path_from_master_ref(task_root, leaf.master)
    default = task_root / "task.json"
    return default if default.exists() else None


def _json_path_from_master_ref(task_root: Path, master_ref: str | None) -> Path | None:
    if not master_ref:
        return None
    ref = Path(master_ref)
    json_ref = ref.with_suffix(".json") if ref.suffix else ref
    candidate = (task_root / json_ref).resolve(strict=False)
    root = task_root.resolve(strict=False)
    # Cross-series master refs are explicit navigation metadata; automatic row sync is same-root only.
    if not candidate.is_relative_to(root) or candidate.parent != root:
        return None
    return candidate
