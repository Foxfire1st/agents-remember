"""Resolve current occupants and parent/child relations from task containment."""

from __future__ import annotations

from dataclasses import dataclass

from agents_remember.controlplane.seats import current_seat_occupant
from agents_remember.errors import SeatOccupancyError
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.models.terminal_catalog import TerminalCatalogEntry
from agents_remember.serving.ports import TerminalCatalogPort
from agents_remember.tasks.document_refs import TaskDocumentRefError, TaskDocumentTopology


@dataclass(frozen=True)
class StructuralSeatError(ValueError):
    status: str
    detail: str

    def __str__(self) -> str:
        return self.detail


class StructuralSeatResolver:
    """One fail-closed policy boundary for every document+role lookup."""

    def __init__(self, catalog: TerminalCatalogPort, topology: TaskDocumentTopology) -> None:
        self.catalog = catalog
        self.topology = topology

    def current(self, document: TaskDocumentRef, role: str) -> TerminalCatalogEntry:
        """Resolve exactly one current occupant, preferring the bound incumbent over a staged heir."""

        try:
            self.topology.validate_role(document, role)
        except TaskDocumentRefError as exc:
            raise StructuralSeatError(exc.status, str(exc)) from exc
        try:
            occupant = current_seat_occupant(self.catalog.list(), document=document, role=role)
        except SeatOccupancyError as exc:
            raise StructuralSeatError("structural-seat-ambiguous", str(exc)) from exc
        if occupant is not None:
            return occupant
        raise StructuralSeatError(
            "structural-seat-missing", f"no running occupant for {document.key} as {role}"
        )

    def parent_address(self, caller: TerminalCatalogEntry) -> tuple[TaskDocumentRef, str]:
        """Return the caller's canonical parent address without requiring a live occupant."""

        document = caller.binding_task_document_ref
        if document is None:
            raise StructuralSeatError("ambient-seat-unbound", "caller has no task document")
        role = caller.binding_role
        if role in {"worker", "reviewer", "curator"}:
            return self._parent_document(document), "manager"
        if role == "manager":
            return self._parent_document(document), "orchestrator"
        if role == "system-specialist":
            return document, "orchestrator"
        if role in {"orchestrator", "strategist", "designer"}:
            return document, "architect"
        raise StructuralSeatError(
            "structural-parent-unsupported", f"role {role!r} has no messageable parent"
        )

    def child_address(
        self,
        caller: TerminalCatalogEntry,
        *,
        document: TaskDocumentRef,
        role: str,
    ) -> tuple[TaskDocumentRef, str]:
        """Return an authorized canonical child address even while its seat is vacant."""

        self.authorize_child(caller, document=document, role=role)
        return document, role

    def parent(self, caller: TerminalCatalogEntry) -> TerminalCatalogEntry:
        """Resolve the caller's current structural parent without spawn ancestry."""

        document, role = self.parent_address(caller)
        return self.current(document, role)

    def child(
        self,
        caller: TerminalCatalogEntry,
        *,
        document: TaskDocumentRef,
        role: str,
    ) -> TerminalCatalogEntry:
        """Resolve one authorized direct child inside the caller's document scope."""

        child_document, child_role = self.child_address(caller, document=document, role=role)
        return self.current(child_document, child_role)

    def authorize_child(
        self,
        caller: TerminalCatalogEntry,
        *,
        document: TaskDocumentRef,
        role: str,
    ) -> None:
        """Prove a direct-child binding without requiring that the child exists yet."""

        caller_document = caller.binding_task_document_ref
        if caller_document is None:
            raise StructuralSeatError("ambient-seat-unbound", "caller has no task document")
        try:
            self.topology.validate_role(document, role)
        except TaskDocumentRefError as exc:
            raise StructuralSeatError(exc.status, str(exc)) from exc
        if caller.binding_role == "architect":
            if document != caller_document or role not in {
                "orchestrator",
                "strategist",
                "designer",
            }:
                raise StructuralSeatError(
                    "structural-child-refused",
                    "architect children are its sprint orchestrator, strategist, and designer",
                )
        elif caller.binding_role == "orchestrator":
            same_sprint_specialist = document == caller_document and role == "system-specialist"
            master_manager = (
                role == "manager" and self._parent_document(document) == caller_document
            )
            if not same_sprint_specialist and not master_manager:
                raise StructuralSeatError(
                    "structural-child-refused",
                    "orchestrator children are its sprint specialists and managers on direct masters",
                )
        elif caller.binding_role == "manager":
            if role not in {"worker", "reviewer", "curator"}:
                raise StructuralSeatError(
                    "structural-child-refused", "manager children are worker/reviewer/curator seats"
                )
            if self._parent_document(document) != caller_document:
                raise StructuralSeatError(
                    "structural-child-refused", "requested leaf is outside the manager's master"
                )
        else:
            raise StructuralSeatError(
                "structural-child-refused",
                f"role {caller.binding_role!r} does not own subordinate seats",
            )

    def _parent_document(self, document: TaskDocumentRef) -> TaskDocumentRef:
        try:
            parent = self.topology.parent(document)
        except TaskDocumentRefError as exc:
            raise StructuralSeatError(exc.status, str(exc)) from exc
        if parent is None:
            raise StructuralSeatError(
                "task-document-parent-missing", f"task document {document.key} has no parent"
            )
        return parent
