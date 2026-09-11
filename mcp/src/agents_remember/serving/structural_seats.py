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
        if role == "reviewer":
            return self._reviewer_parent_address(caller, document)
        if role in {"worker", "curator"}:
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
                "reviewer",
            }:
                raise StructuralSeatError(
                    "structural-child-refused",
                    "architect children are its sprint orchestrator, strategist, designer, "
                    "and plan-review reviewer",
                )
        elif caller.binding_role == "orchestrator":
            same_sprint_child = document == caller_document and role in {
                "system-specialist",
                "reviewer",
            }
            master_manager = (
                role == "manager" and self._parent_document(document) == caller_document
            )
            if not same_sprint_child and not master_manager:
                raise StructuralSeatError(
                    "structural-child-refused",
                    "orchestrator children are its sprint specialists, its super-exit reviewer, "
                    "and managers on direct masters",
                )
        elif caller.binding_role == "manager":
            self._authorize_manager_child(caller_document, document, role)
        else:
            raise StructuralSeatError(
                "structural-child-refused",
                f"role {caller.binding_role!r} does not own subordinate seats",
            )

    def _authorize_manager_child(
        self,
        caller_document: TaskDocumentRef,
        document: TaskDocumentRef,
        role: str,
    ) -> None:
        if document == caller_document and role == "reviewer":
            return
        if document == caller_document or role not in {"worker", "reviewer", "curator"}:
            raise StructuralSeatError(
                "structural-child-refused",
                "manager children are worker/reviewer/curator seats on its leaves and its "
                "master-exit reviewer on the master",
            )
        if self._parent_document(document) != caller_document:
            raise StructuralSeatError(
                "structural-child-refused",
                "requested leaf is outside the manager's master",
            )

    def _reviewer_parent_address(
        self,
        caller: TerminalCatalogEntry,
        document: TaskDocumentRef,
    ) -> tuple[TaskDocumentRef, str]:
        """Validate the plane-stamped owner of a polymorphic reviewer seat."""

        parent_document = caller.structural_parent_task_document_ref
        parent_role = caller.structural_parent_role
        altitude = self.topology.altitude(document)
        if parent_document is None and parent_role is None:
            if altitude == "leaf":
                # Reviewer rows that predate polymorphic reviewer seats were leaf-only. Preserve
                # that one deterministic migration meaning without guessing a higher-level owner.
                return self._parent_document(document), "manager"
            raise StructuralSeatError(
                "structural-parent-unproven",
                f"{altitude} reviewer {document.key} has no plane-stamped structural parent",
            )
        if parent_document is None or parent_role is None:
            raise StructuralSeatError(
                "structural-parent-incomplete",
                f"reviewer {document.key} has an incomplete structural parent address",
            )
        allowed = (
            {(self._parent_document(document), "manager")}
            if altitude == "leaf"
            else {(document, "manager")}
            if altitude == "master"
            else {(document, "architect"), (document, "orchestrator")}
        )
        if (parent_document, parent_role) not in allowed:
            raise StructuralSeatError(
                "structural-parent-mismatch",
                f"reviewer {document.key} at {altitude} has invalid structural parent "
                f"{parent_document.key} as {parent_role}",
            )
        return parent_document, parent_role

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
