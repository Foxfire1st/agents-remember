"""Leaf-to-master row synchronization for JSON-primary task documents."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from .document import DocStatus, SubTaskRef, TaskDocument
from .store import markdown_path_for, read_task_doc

MasterSyncStatus = Literal["none", "created", "updated", "unchanged"]


class MasterSyncError(ValueError):
    """Raised when a resolvable parent master cannot be read or updated."""


@dataclass(frozen=True)
class MasterSyncPlan:
    status: MasterSyncStatus
    master: TaskDocument | None = None
    master_json_path: Path | None = None
    subtask_number: str | None = None

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
        master = read_task_doc(master_json_path)
    except (OSError, ValidationError, ValueError) as exc:
        raise MasterSyncError(
            f"cannot read parent master task document: {master_json_path}"
        ) from exc
    if master.kind != "master":
        raise MasterSyncError(f"parent task document is not a master: {master_json_path}")

    existing = next((ref for ref in master.subTasks if ref.number == leaf.id), None)
    ref = subtask_ref_from_leaf(task_root, leaf, existing=existing)
    if existing == ref:
        return MasterSyncPlan(
            status="unchanged",
            master=master,
            master_json_path=master_json_path,
            subtask_number=leaf.id,
        )

    data = master.model_dump(by_alias=True)
    refs = list(data.get("subTasks", []))
    ref_payload = ref.model_dump()
    if existing is None:
        refs.append(ref_payload)
        status: MasterSyncStatus = "created"
    else:
        refs = [ref_payload if item.get("number") == leaf.id else item for item in refs]
        status = "updated"
    data["subTasks"] = refs
    return MasterSyncPlan(
        status=status,
        master=TaskDocument.model_validate(data),
        master_json_path=master_json_path,
        subtask_number=leaf.id,
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
    statuses = [
        sub.status if step.substeps else step.status
        for step in leaf.steps
        for sub in (step.substeps or [step])
    ]
    if statuses and all(status == "done" for status in statuses):
        return "Completed"
    if any(status in {"done", "inProgress", "blocked"} for status in statuses):
        return "inProgress"
    return leaf.status


def _master_json_path(task_root: Path, leaf: TaskDocument) -> Path | None:
    candidate = _json_path_from_master_ref(task_root, leaf.master) if leaf.master else None
    if candidate is not None:
        return candidate
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
