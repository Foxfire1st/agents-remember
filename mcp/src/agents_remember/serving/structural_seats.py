"""Resolve current occupants and parent/child relations from task containment."""

from __future__ import annotations

from dataclasses import dataclass

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
        rows = [
            row
            for row in self.catalog.list()
            if row.status == "running"
            and row.binding_role == role
            and row.task_document_ref == document
        ]
        if len(rows) > 1:
            raise StructuralSeatError(
                "structural-seat-ambiguous",
                f"multiple running occupants claim {document.key} as {role}",
            )
        if rows:
            return rows[0]
        replacements = [
            row
            for row in self.catalog.list()
            if row.status == "running"
            and row.binding_role == role
            and row.replacement_for_task_document_ref == document
        ]
        if len(replacements) > 1:
            raise StructuralSeatError(
                "structural-seat-ambiguous",
                f"multiple running replacements claim {document.key} as {role}",
            )
        if replacements:
            return replacements[0]
        raise StructuralSeatError(
            "structural-seat-missing", f"no running occupant for {document.key} as {role}"
        )

    def parent(self, caller: TerminalCatalogEntry) -> TerminalCatalogEntry:
        """Resolve the caller's current structural parent without spawn ancestry."""

        document = caller.binding_task_document_ref
        if document is None:
            raise StructuralSeatError("ambient-seat-unbound", "caller has no task document")
        role = caller.binding_role
        if role in {"worker", "reviewer", "curator"}:
            parent_document = self._parent_document(document)
            return self.current(parent_document, "manager")
        if role == "manager":
            parent_document = self._parent_document(document)
            return self.current(parent_document, "orchestrator")
        if role == "system-specialist":
            return self.current(document, "orchestrator")
        if role in {"orchestrator", "strategist", "designer"}:
            return self.current(document, "architect")
        raise StructuralSeatError(
            "structural-parent-unsupported", f"role {role!r} has no messageable parent"
        )

    def child(
        self,
        caller: TerminalCatalogEntry,
        *,
        document: TaskDocumentRef,
        role: str,
    ) -> TerminalCatalogEntry:
        """Resolve one authorized direct child inside the caller's document scope."""

        self.authorize_child(caller, document=document, role=role)
        return self.current(document, role)

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
