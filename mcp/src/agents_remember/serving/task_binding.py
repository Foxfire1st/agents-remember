"""One fail-closed task-binding preflight for every hosted-session spawn path."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.models.worktree import SourceLineageProjection
from agents_remember.tasks.document_refs import TaskDocumentRefError, TaskDocumentTopology
from agents_remember.worktrees.source_lineage import lineage_refusal, source_lineage_for_task

TaskBindingRefusalStatus = Literal[
    "task-binding-required",
    "task-binding-invalid",
    "source-lineage-stale",
    "source-lineage-unavailable",
]


@dataclass(frozen=True)
class TaskBindingRequest:
    """The complete binding claim that must be proven before settings or host effects."""

    task_document_ref: TaskDocumentRef | None
    replacement_for_task_document_ref: TaskDocumentRef | None
    seat_role: str
    structural_parent_task_document_ref: TaskDocumentRef | None = None
    structural_parent_role: str | None = None


@dataclass(frozen=True)
class TaskBindingRefusal:
    """A designed public refusal from the shared binding policy."""

    status: TaskBindingRefusalStatus
    detail: str | None = None
    source_lineage: SourceLineageProjection | None = None


@dataclass(frozen=True)
class ResolvedTaskBinding:
    """Canonical document identities plus the result of the binding preflight."""

    task_document_ref: TaskDocumentRef | None
    replacement_for_task_document_ref: TaskDocumentRef | None
    refusal: TaskBindingRefusal | None = None


class TaskDocumentResolutionFailure(ValueError):
    """Identify which caller-supplied document failed canonical resolution."""

    def __init__(self, ref: TaskDocumentRef, error: TaskDocumentRefError) -> None:
        self.ref = ref
        self.error = error
        super().__init__(str(error))


def resolve_task_binding(
    coordination_root: Path,
    request: TaskBindingRequest,
) -> ResolvedTaskBinding:
    """Resolve and validate one spawn claim through the single binding authority.

    Both document references are resolved before their mutual-exclusion rule is checked. That
    preserves the public document-not-found/invalid/repo-mismatch dialect for the exact bad input,
    while every semantic binding refusal comes from :func:`_binding_refusal`.
    """

    topology = TaskDocumentTopology(coordination_root)
    task_document_ref = _resolve_optional(topology, request.task_document_ref)
    replacement_ref = _resolve_optional(topology, request.replacement_for_task_document_ref)
    resolved_request = TaskBindingRequest(
        task_document_ref=task_document_ref,
        replacement_for_task_document_ref=replacement_ref,
        seat_role=request.seat_role,
        structural_parent_task_document_ref=request.structural_parent_task_document_ref,
        structural_parent_role=request.structural_parent_role,
    )
    return ResolvedTaskBinding(
        task_document_ref=task_document_ref,
        replacement_for_task_document_ref=replacement_ref,
        refusal=_binding_refusal(topology, resolved_request),
    )


def _resolve_optional(
    topology: TaskDocumentTopology,
    ref: TaskDocumentRef | None,
) -> TaskDocumentRef | None:
    if ref is None:
        return None
    try:
        return topology.resolve(ref).ref
    except TaskDocumentRefError as exc:
        raise TaskDocumentResolutionFailure(ref, exc) from exc


def _binding_refusal(
    topology: TaskDocumentTopology,
    request: TaskBindingRequest,
) -> TaskBindingRefusal | None:
    if (
        request.task_document_ref is not None
        and request.replacement_for_task_document_ref is not None
    ):
        return TaskBindingRefusal("task-binding-invalid")
    document = request.task_document_ref or request.replacement_for_task_document_ref
    structural_role = request.seat_role not in {"chat", "terminal"}
    if document is None:
        return TaskBindingRefusal("task-binding-required") if structural_role else None
    if not structural_role:
        return None
    try:
        topology.validate_role(document, request.seat_role)
        lineage = source_lineage_for_task(topology.coordination_root, document)
        refusal = lineage_refusal(lineage)
        if refusal is not None:
            status, detail = refusal
            return TaskBindingRefusal(status, detail, lineage)
        _validate_structural_parent(topology, request, document)
    except TaskDocumentRefError as exc:
        return TaskBindingRefusal("task-binding-invalid", str(exc))
    return None


def _validate_structural_parent(
    topology: TaskDocumentTopology,
    request: TaskBindingRequest,
    document: TaskDocumentRef,
) -> None:
    parent_document = request.structural_parent_task_document_ref
    parent_role = request.structural_parent_role
    if (parent_document is None) != (parent_role is None):
        raise TaskDocumentRefError(
            "structural-parent-incomplete",
            "structural parent document and role must be supplied together",
        )
    if request.seat_role != "reviewer":
        if parent_document is not None:
            raise TaskDocumentRefError(
                "structural-parent-unsupported",
                "only a reviewer seat carries polymorphic structural-parent provenance",
            )
        return
    if parent_document is None or parent_role is None:
        raise TaskDocumentRefError(
            "structural-parent-required",
            f"reviewer {document.key} requires an explicit structural parent",
        )
    altitude = topology.altitude(document)
    if altitude == "leaf":
        leaf_parent = topology.parent(document)
        if leaf_parent is None:
            raise TaskDocumentRefError(
                "task-document-parent-missing",
                f"leaf task document {document.key} has no parent",
            )
        allowed = {(leaf_parent, "manager")}
    elif altitude == "master":
        allowed = {(document, "manager")}
    else:
        allowed = {(document, "architect"), (document, "orchestrator")}
    if (parent_document, parent_role) not in allowed:
        raise TaskDocumentRefError(
            "structural-parent-mismatch",
            f"reviewer parent {parent_document.key} as {parent_role} does not own the "
            f"{altitude} review seam {document.key}",
        )
    topology.validate_role(parent_document, parent_role)
