"""Structural-parent policy for identity-free reviewer dispatch."""

from __future__ import annotations

from dataclasses import dataclass

from agents_remember.application.terminal_tools import SpawnedBy
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.models.terminal_catalog import TerminalCatalogEntry
from agents_remember.tasks.document_refs import TaskDocumentTopology


class AmbientReviewerParentError(ValueError):
    """Ambient reviewer ownership cannot be derived without guessing."""

    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status


ReviewerParent = tuple[TaskDocumentRef, str]


@dataclass(frozen=True)
class DispatchProvenance:
    """Runtime author plus the reviewer ownership expected after dispatch."""

    spawned_by: SpawnedBy
    reviewer_parent: ReviewerParent | None


def ambient_reviewer_parent(
    topology: TaskDocumentTopology,
    document: TaskDocumentRef,
) -> ReviewerParent:
    """Derive the only unambiguous reviewer owner at leaf and master altitude."""

    altitude = topology.altitude(document)
    if altitude == "leaf":
        parent = topology.parent(document)
        if parent is None:
            raise AmbientReviewerParentError(
                "structural-parent-missing",
                f"leaf reviewer {document.key} has no canonical owning master",
            )
        topology.validate_role(parent, "manager")
        return parent, "manager"
    if altitude == "master":
        topology.validate_role(document, "manager")
        return document, "manager"
    raise AmbientReviewerParentError(
        "structural-parent-ambiguous",
        f"sprint reviewer {document.key} could be owned by an architect or orchestrator; "
        "dispatch it from the selected plane seat instead of guessing",
    )


def resolve_dispatch_provenance(
    topology: TaskDocumentTopology,
    document: TaskDocumentRef,
    role: str,
    caller: TerminalCatalogEntry | None,
) -> DispatchProvenance:
    """Resolve one reviewer parent and stamp it into the dispatch author provenance."""

    reviewer_parent = _reviewer_parent(topology, document, role, caller)
    return DispatchProvenance(
        spawned_by=SpawnedBy(
            session_id=caller.id if caller is not None else None,
            lifecycle_id=caller.lifecycle_id if caller is not None else None,
            caller_kind="plane" if caller is not None else "ambient",
            structural_parent_task_document_ref=(
                reviewer_parent[0] if reviewer_parent is not None else None
            ),
            structural_parent_role=(reviewer_parent[1] if reviewer_parent is not None else None),
        ),
        reviewer_parent=reviewer_parent,
    )


def _reviewer_parent(
    topology: TaskDocumentTopology,
    document: TaskDocumentRef,
    role: str,
    caller: TerminalCatalogEntry | None,
) -> ReviewerParent | None:
    if role != "reviewer":
        return None
    if caller is not None and caller.binding_task_document_ref is not None:
        return caller.binding_task_document_ref, caller.binding_role
    return ambient_reviewer_parent(topology, document)
