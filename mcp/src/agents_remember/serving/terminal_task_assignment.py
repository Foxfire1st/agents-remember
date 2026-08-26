"""Shared hosted-session assignment to a canonical task-document role seat."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.models.worktree import SourceLineageProjection
from agents_remember.serving.ports import TerminalCatalogPort
from agents_remember.serving.seat_binding import attach_seat_role
from agents_remember.tasks.document_refs import TaskDocumentRefError, TaskDocumentTopology
from agents_remember.worktrees.source_lineage import lineage_refusal, source_lineage_for_task

TaskAssignmentStatus = Literal[
    "attached",
    "seat-taken",
    "unknown-session",
    "role-required",
    "task-binding-invalid",
    "source-lineage-stale",
    "source-lineage-unavailable",
]


class TaskAssignmentHost(Protocol):
    def has_session(self, tmux_name: str) -> bool: ...


@dataclass(frozen=True)
class TaskAssignmentRuntime:
    """Plane-owned collaborators used to arbitrate one task-seat assignment."""

    catalog: TerminalCatalogPort
    host: TaskAssignmentHost
    topology: TaskDocumentTopology


@dataclass(frozen=True)
class TaskAssignmentResult:
    """Result of moving one hosted session to a document-owned role seat."""

    status: TaskAssignmentStatus
    session_id: str
    task_document_ref: TaskDocumentRef
    previous_task_document_ref: TaskDocumentRef | None = None
    owner_session_id: str | None = None
    role: str | None = None
    seat_role: str | None = None
    previous_seat_role: str | None = None
    detail: str | None = None
    source_lineage: SourceLineageProjection | None = None


def task_binding_conflict_owner(
    catalog: TerminalCatalogPort,
    *,
    task_document_ref: TaskDocumentRef | None,
    session_id: str,
    seat_role: str,
    host: TaskAssignmentHost,
) -> str | None:
    """Return the different live occupant after re-evaluating any dead preferred generation."""

    if task_document_ref is None:
        return None
    exited: set[str] = set()
    while True:
        owner = catalog.active_for_task(task_document_ref, seat_role=seat_role)
        if owner is None or owner.id == session_id:
            return None
        if owner.id in exited:
            # A catalog that could not publish the exit is not proof of vacancy.
            return owner.id
        if host.has_session(owner.tmux_name):
            return owner.id
        exited.add(owner.id)
        catalog.mark_exited(owner.id)


def replacement_binding_conflict_owner(
    catalog: TerminalCatalogPort,
    *,
    task_document_ref: TaskDocumentRef | None,
    session_id: str,
    seat_role: str,
    host: TaskAssignmentHost,
) -> str | None:
    """Return another live staged replacement for a structural seat, if one exists."""

    if task_document_ref is None:
        return None
    for candidate in catalog.list():
        if (
            candidate.id == session_id
            or candidate.status != "running"
            or candidate.binding_role != seat_role
            or candidate.replacement_for_task_document_ref != task_document_ref
        ):
            continue
        if host.has_session(candidate.tmux_name):
            return candidate.id
        catalog.mark_exited(candidate.id)
    return None


def assign_terminal_session_to_task(
    runtime: TaskAssignmentRuntime,
    *,
    session_id: str,
    task_document_ref: TaskDocumentRef,
    role: str | None = None,
) -> TaskAssignmentResult:
    """Move a session under the same catalog transaction as conflict re-evaluation."""

    with runtime.catalog.batch():
        return _assign_terminal_session_to_task(
            runtime,
            session_id=session_id,
            task_document_ref=task_document_ref,
            role=role,
        )


def _assign_terminal_session_to_task(
    runtime: TaskAssignmentRuntime,
    *,
    session_id: str,
    task_document_ref: TaskDocumentRef,
    role: str | None,
) -> TaskAssignmentResult:
    """Validate and publish one task binding while the caller holds the catalog batch."""

    entry = runtime.catalog.get(session_id)
    if entry is None or entry.status != "running":
        return TaskAssignmentResult(
            status="unknown-session",
            session_id=session_id,
            task_document_ref=task_document_ref,
        )
    seat_role = attach_seat_role(
        requested=role,
        spawn_role=entry.spawn_role,
        current=entry.seat_role,
        kind=entry.kind,
    )
    if seat_role is None:
        return TaskAssignmentResult(
            status="role-required",
            session_id=session_id,
            task_document_ref=task_document_ref,
            previous_task_document_ref=entry.task_document_ref,
            previous_seat_role=entry.binding_role,
            role=entry.role,
        )
    try:
        runtime.topology.resolve(task_document_ref)
        if seat_role not in {"chat", "terminal"}:
            runtime.topology.validate_role(task_document_ref, seat_role)
    except TaskDocumentRefError:
        return TaskAssignmentResult(
            status="task-binding-invalid",
            session_id=session_id,
            task_document_ref=task_document_ref,
            previous_task_document_ref=entry.task_document_ref,
            role=entry.role,
            seat_role=seat_role,
            previous_seat_role=entry.binding_role,
        )
    lineage = source_lineage_for_task(runtime.topology.coordination_root, task_document_ref)
    refusal = lineage_refusal(lineage)
    if refusal is not None:
        status, detail = refusal
        return TaskAssignmentResult(
            status=status,
            session_id=session_id,
            task_document_ref=task_document_ref,
            previous_task_document_ref=entry.task_document_ref,
            role=entry.role,
            seat_role=seat_role,
            previous_seat_role=entry.binding_role,
            detail=detail,
            source_lineage=lineage,
        )
    owner = task_binding_conflict_owner(
        runtime.catalog,
        task_document_ref=task_document_ref,
        session_id=session_id,
        seat_role=seat_role,
        host=runtime.host,
    )
    if owner is not None:
        return TaskAssignmentResult(
            status="seat-taken",
            session_id=session_id,
            task_document_ref=task_document_ref,
            previous_task_document_ref=entry.task_document_ref,
            owner_session_id=owner,
            role=entry.role,
            seat_role=seat_role,
            previous_seat_role=entry.binding_role,
        )
    previous_task_document_ref = entry.task_document_ref
    previous_seat_role = entry.binding_role
    runtime.catalog.upsert(entry.with_task_binding(task_document_ref, seat_role))
    return TaskAssignmentResult(
        status="attached",
        session_id=session_id,
        task_document_ref=task_document_ref,
        previous_task_document_ref=previous_task_document_ref,
        role=entry.role,
        seat_role=seat_role,
        previous_seat_role=previous_seat_role,
    )
