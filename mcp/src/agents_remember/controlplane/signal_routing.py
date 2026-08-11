"""Plane-owned routing over canonical task-document and role identity.

Task containment defines the hierarchy; the catalog supplies only the current occupant.
Spawn ancestry is audit provenance and never participates in address resolution.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from agents_remember.controlplane.operator_inbox_records import (
    AgentRole,
    InboxMessageKind,
    OperatorInboxEntry,
)
from agents_remember.controlplane.seats import SeatDirectory, SeatRow
from agents_remember.models.task_document_ref import TaskDocumentRef

_LEAF_ROLES = frozenset({"worker", "reviewer", "curator"})


class TaskHierarchy(Protocol):
    """The one task-containment operation routing needs from the task service."""

    def parent(self, ref: TaskDocumentRef) -> TaskDocumentRef | None: ...


class StructuralRoutingError(ValueError):
    """A structural route is absent or ambiguous; routing fails instead of guessing."""


@dataclass(frozen=True)
class RoutedOwner:
    """A stable owner seat plus its current private delivery correlations."""

    role: AgentRole | None = None
    task_document_ref: TaskDocumentRef | None = None
    agent_id: str | None = None
    lifecycle_id: str | None = None


def _current_occupant(
    catalog: SeatDirectory,
    *,
    document: TaskDocumentRef,
    role: AgentRole,
) -> RoutedOwner:
    primary = [
        row
        for row in catalog.list()
        if row.status == "running"
        and row.binding_role == role
        and row.task_document_ref == document
    ]
    if len(primary) > 1:
        raise StructuralRoutingError(f"multiple running occupants claim {document.key} as {role}")
    candidates = primary
    if not candidates:
        candidates = [
            row
            for row in catalog.list()
            if row.status == "running"
            and row.binding_role == role
            and row.replacement_for_task_document_ref == document
        ]
        if len(candidates) > 1:
            raise StructuralRoutingError(
                f"multiple running replacements claim {document.key} as {role}"
            )
    if not candidates:
        return RoutedOwner(role=role, task_document_ref=document)
    occupant = candidates[0]
    return RoutedOwner(
        role=role,
        task_document_ref=document,
        agent_id=occupant.id,
        lifecycle_id=occupant.lifecycle_id,
    )


def signal_task_document_ref(
    catalog: SeatDirectory,
    *,
    sender_agent_id: str | None,
    task_document_ref: TaskDocumentRef | None = None,
) -> TaskDocumentRef | None:
    """Resolve the sender's real task document without consulting spawn ancestry."""

    if task_document_ref is not None:
        return task_document_ref
    sender = catalog.get(sender_agent_id) if sender_agent_id is not None else None
    return sender.binding_task_document_ref if sender is not None else None


def _required_parent(hierarchy: TaskHierarchy, document: TaskDocumentRef) -> TaskDocumentRef:
    parent = hierarchy.parent(document)
    if parent is None:
        raise StructuralRoutingError(f"task document {document.key} has no structural parent")
    return parent


def _sprint_document(hierarchy: TaskHierarchy, document: TaskDocumentRef) -> TaskDocumentRef:
    current = document
    seen: set[TaskDocumentRef] = set()
    while True:
        if current in seen:
            raise StructuralRoutingError(f"task containment cycle at {current.key}")
        seen.add(current)
        parent = hierarchy.parent(current)
        if parent is None:
            return current
        current = parent


def derive_manager_owner(
    catalog: SeatDirectory,
    hierarchy: TaskHierarchy,
    *,
    task_document_ref: TaskDocumentRef,
) -> RoutedOwner:
    """The current manager of the master containing ``task_document_ref``."""

    master = _required_parent(hierarchy, task_document_ref)
    return _current_occupant(catalog, document=master, role="manager")


def derive_architect_owner(
    catalog: SeatDirectory,
    hierarchy: TaskHierarchy,
    *,
    task_document_ref: TaskDocumentRef,
) -> RoutedOwner:
    """The current architect on the sprint containing ``task_document_ref``."""

    sprint = _sprint_document(hierarchy, task_document_ref)
    return _current_occupant(catalog, document=sprint, role="architect")


def _structural_parent_owner(
    catalog: SeatDirectory,
    hierarchy: TaskHierarchy,
    *,
    document: TaskDocumentRef,
    role: str,
) -> RoutedOwner | None:
    """Resolve the structural parent of one role/document binding, if it has one."""

    if role in _LEAF_ROLES:
        return derive_manager_owner(catalog, hierarchy, task_document_ref=document)
    if role == "manager":
        return _current_occupant(
            catalog,
            document=_required_parent(hierarchy, document),
            role="orchestrator",
        )
    if role in {"orchestrator", "strategist", "designer"}:
        return _current_occupant(catalog, document=document, role="architect")
    if role == "system-specialist":
        return _current_occupant(catalog, document=document, role="orchestrator")
    return None


def derive_signal_owner(
    catalog: SeatDirectory,
    hierarchy: TaskHierarchy,
    *,
    sender_agent_id: str | None,
    message_kind: InboxMessageKind,
    task_document_ref: TaskDocumentRef | None = None,
) -> RoutedOwner:
    """Resolve one hop up the task hierarchy from the sender's document+role seat."""

    document = signal_task_document_ref(
        catalog,
        sender_agent_id=sender_agent_id,
        task_document_ref=task_document_ref,
    )
    if document is None:
        return RoutedOwner()
    if message_kind == "decision-item":
        return derive_architect_owner(catalog, hierarchy, task_document_ref=document)
    sender = catalog.get(sender_agent_id) if sender_agent_id is not None else None
    if sender is None:
        return RoutedOwner()
    return (
        _structural_parent_owner(
            catalog,
            hierarchy,
            document=document,
            role=sender.binding_role,
        )
        or RoutedOwner()
    )


def is_seat_dead(catalog: SeatDirectory, agent_id: str | None) -> bool:
    """Whether an exact private occupant correlation is no longer live."""

    if agent_id is None:
        return True
    entry = catalog.get(agent_id)
    return entry is None or entry.status != "running"


def _parsed_at(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _entry_progressed_after(entry: SeatRow, since: datetime) -> bool:
    if entry.status == "running" and entry.turn_state == "working":
        return True
    timestamps = (
        entry.created_at,
        entry.last_attached_at,
        entry.turn_state_changed_at,
        entry.landed_at,
    )
    return any(at is not None and at > since for at in map(_parsed_at, timestamps))


def task_chain_has_progress(
    catalog: SeatDirectory,
    hierarchy: TaskHierarchy,
    *,
    task_document_ref: TaskDocumentRef,
    subject_agent_id: str | None,
    since: str,
) -> bool:
    """Whether another current seat on the leaf or its manager progressed after ``since``."""

    since_at = _parsed_at(since)
    if since_at is None:
        return False
    manager_document = _required_parent(hierarchy, task_document_ref)
    return any(
        entry.id != subject_agent_id
        and entry.status in {"running", "landed"}
        and _entry_tracks_task_chain(entry, task_document_ref, manager_document)
        and _entry_progressed_after(entry, since_at)
        for entry in catalog.list()
    )


def _entry_tracks_task_chain(
    entry: SeatRow,
    task_document_ref: TaskDocumentRef,
    manager_document: TaskDocumentRef,
) -> bool:
    candidate_documents = (
        (task_document_ref, manager_document)
        if entry.binding_role == "manager"
        else (task_document_ref,)
    )
    return (
        entry.task_document_ref in candidate_documents
        or entry.replacement_for_task_document_ref in candidate_documents
    )


def _owner_for_stamped_seat(
    catalog: SeatDirectory,
    entry: OperatorInboxEntry,
) -> RoutedOwner:
    if entry.ownerRole is None or entry.ownerTaskDocumentRef is None:
        return RoutedOwner()
    return _current_occupant(
        catalog,
        document=entry.ownerTaskDocumentRef,
        role=entry.ownerRole,
    )


def derive_row_owner(
    catalog: SeatDirectory,
    hierarchy: TaskHierarchy,
    entry: OperatorInboxEntry,
) -> RoutedOwner:
    """Re-resolve a pending row from its durable structural subject after replacement."""

    if entry.messageKind == "dispatch-brief":
        return RoutedOwner()
    document = entry.subjectTaskDocumentRef
    role = entry.seatRole or entry.senderRole
    if document is None or role is None:
        return _owner_for_stamped_seat(catalog, entry)
    return _structural_parent_owner(
        catalog,
        hierarchy,
        document=document,
        role=role,
    ) or _owner_for_stamped_seat(catalog, entry)
