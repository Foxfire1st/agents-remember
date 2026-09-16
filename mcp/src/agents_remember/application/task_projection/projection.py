"""Assemble one focused task projection from the documents the read plan selected.

The projection is a pure computation over documents an existing owner already
resolved. It has no writer: it cannot set a status, raise a gate, create an
approval, write memory or publish a second task database -- nothing in this
package imports a write surface, and a structural test asserts exactly that.

Three rules shape the assembly:

1. **Only the planned documents are read.** :func:`read_documents` hands the
   builder exactly the altitudes the plan names, and the builder never widens it.
2. **Only the bound document's own decisions are injected.** An ancestor's
   decision log travels as an expansion reference carrying its entry count, so a
   leaf projection cannot grow into the series' history.
3. **Nothing is truncated.** Every obligation, negative constraint and failure
   obligation is carried verbatim from the packet that declares it; material that
   is deliberately not injected is named as referenced instead.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from agents_remember.errors import TaskProjectionSourceError
from agents_remember.models.role_capsules.types import (
    CapsuleBinding,
    CapsuleRequirementBinding,
    CapsuleRoleSeat,
)
from agents_remember.models.role_capsules.vocabulary import CapsuleRole
from agents_remember.models.task_intent import AcceptanceObligationQuestion
from agents_remember.tasks.document import SubTaskRef, TaskDocument
from agents_remember.tasks.document_refs import ResolvedTaskDocument
from agents_remember.tasks.task_intent import (
    TaskIntentRequirementPacket,
)

from . import packets
from .declarations import (
    RequirementDeclaration,
    adjacent,
    declaration_identity,
    missing_section_gap,
    read_declarations,
)
from .packets import PacketSectionRole, role_section
from .rendering import render_markdown
from .revision import projection_revision
from .scope import ResolvedProjectionScope, read_documents, referenced_documents
from .selection import read_plan
from .statuses import (
    STATUS_REQUIREMENT_PACKET_UNRESOLVED,
)
from .types import (
    BoundTask,
    ExpansionReference,
    KnowledgeExpansion,
    KnowledgeExpansionRequest,
    ProjectedFact,
    ProjectionReadPlan,
    ProjectionSelection,
    RequirementProjection,
    TaskProjection,
    TaskProjectionRequest,
    WritableScope,
)

#: The packet section roles whose verbatim body the projection injects as a
#: preservation constraint or failure obligation.
_PRESERVATION_ROLES: tuple[PacketSectionRole, ...] = (
    "preservation-boundaries",
    "exclusions",
    "failure-and-recovery",
)


def project_task_context(
    scope: ResolvedProjectionScope,
    binding: CapsuleBinding,
    request: TaskProjectionRequest | None = None,
) -> TaskProjection:
    """Project the admitted task context for ``binding``.

    Raises :class:`~agents_remember.errors.TaskProjectionSourceError` when a
    required input is missing or contradictory. The bound task document, the
    requirement packets and the memory surface are only read.
    """

    return _Builder(scope, binding, request or TaskProjectionRequest()).build()


class _Builder:
    """One projection's assembly, from the planned documents to the frozen value."""

    def __init__(
        self,
        scope: ResolvedProjectionScope,
        binding: CapsuleBinding,
        request: TaskProjectionRequest,
    ) -> None:
        self.scope = scope
        self.binding = binding
        self.request = request
        seat = binding.admitted.seat
        # The launcher seat is a seat kind, not a tenth role: it has no role to bind.
        self.role: CapsuleRole | None = seat.role if isinstance(seat, CapsuleRoleSeat) else None
        self.plan: ProjectionReadPlan = read_plan(
            role=self.role,
            altitude=scope.bound_task.altitude,
            parent_altitude=scope.parent_altitude,
            operation=binding.operation,
        )
        self.read: tuple[ResolvedTaskDocument, ...] = read_documents(scope, self.plan)
        self.pointed_at: tuple[ResolvedTaskDocument, ...] = referenced_documents(scope, self.plan)
        self.document: TaskDocument = scope.bound_document.document
        self.gaps: list[str] = []
        self.expansion: list[ExpansionReference] = []
        self.declarations: tuple[RequirementDeclaration, ...] = read_declarations(
            Path(scope.bound_task.task_root), scope.bound_document
        )

    # -- assembly ------------------------------------------------------------------

    def build(self) -> TaskProjection:
        selection = self._selection()
        requirements, owned_packets = self._requirements()
        draft = TaskProjection(
            task=self.scope.bound_task,
            worktree=self.scope.worktree,
            selection=selection,
            objective=self.document.objective,
            requirements=requirements,
            acceptance=self._acceptance(owned_packets),
            scope=self._writable_scope(),
            decisions=self._decisions(),
            preservation=self._preservation(owned_packets),
            handoff=self._handoff(requirements),
            portfolio=self._portfolio(),
            series=self._series(),
            evidence=self._evidence(),
            knowledge=self._knowledge(),
            expansion=tuple(self.expansion),
            gaps=tuple(self._gaps()),
            markdown="",
            projection_revision="",
        )
        # The revision is computed from the selected inputs, not from the rendered
        # bytes, so the rendered document can carry its own revision and the digest
        # pair stays meaningful in that order.
        identified = replace(draft, projection_revision=projection_revision(draft))
        return replace(identified, markdown=render_markdown(identified))

    def _selection(self) -> ProjectionSelection:
        for document in self.read:
            self.expansion.append(_document_reference(document, "task document"))
        for document in (*self.read[1:], *self.pointed_at):
            # An ancestor's decision log is never injected: it is a reference carrying
            # its entry count, so "smallest complete projection" cannot quietly become
            # "the whole series history".
            self.expansion.append(
                ExpansionReference(
                    label=(
                        f"{document.document.id} decision log "
                        f"({len(document.document.decisions)} entries, not injected)"
                    ),
                    location=document.path.as_posix(),
                    anchor="Decisions",
                )
            )
        for document in self.pointed_at:
            self.expansion.append(_document_reference(document, "referenced task document"))
        for declared in self.declarations:
            if isinstance(declared, TaskIntentRequirementPacket):
                self.expansion.append(
                    ExpansionReference(
                        label=(
                            f"adjacent requirement packet {declared.stableId}@{declared.version}"
                        ),
                        location=declared.path,
                    )
                )
        return ProjectionSelection(
            role=self.role,
            operation=self.binding.operation,
            altitude=self.scope.bound_task.altitude,
            parent_altitude=self.scope.parent_altitude,
            plan=self.plan,
            read_documents=tuple(document.ref.key for document in self.read),
            referenced_documents=tuple(document.ref.key for document in self.pointed_at),
        )

    # -- requirement plane ---------------------------------------------------------

    def _requirements(
        self,
    ) -> tuple[tuple[RequirementProjection, ...], tuple[packets.RequirementPacketProjection, ...]]:
        owned = tuple(self._owned(required) for required in self.binding.admitted.requirements)
        claimed = {item.identity for item in owned}
        adjacent_declarations = tuple(
            adjacent(declared)
            for declared in self.declarations
            if declaration_identity(declared) not in claimed
        )
        packets_read = tuple(item.packet for item in owned if item.packet is not None)
        return (*owned, *adjacent_declarations), packets_read

    def _owned(self, required: CapsuleRequirementBinding) -> RequirementProjection:
        identity = required.identity
        declared = next(
            (item for item in self.declarations if declaration_identity(item) == identity),
            None,
        )
        if isinstance(declared, TaskIntentRequirementPacket):
            return self._from_declaration(identity, declared)
        return self._from_location(identity, required.stable_id, required.revision)

    def _from_declaration(
        self,
        identity: str,
        declared: TaskIntentRequirementPacket,
    ) -> RequirementProjection:
        packet = self._read_packet(declared.stableId, declared.version, declared.path)
        return RequirementProjection(
            identity=identity,
            owns=True,
            declaration="approved-packet",
            declaration_text=declared.path,
            packet=packet,
        )

    def _from_location(
        self,
        identity: str,
        stable_id: str,
        revision: str,
    ) -> RequirementProjection:
        location = next(
            (item for item in self.request.requirement_packets if item.identity == identity),
            None,
        )
        if location is None:
            raise TaskProjectionSourceError(
                STATUS_REQUIREMENT_PACKET_UNRESOLVED,
                f"the admitted binding makes this seat accountable for {identity}, but the bound "
                f"task document {self.scope.bound_task.reference} declares no approved packet for "
                "it and no packet location was admitted",
                next_action=(
                    "declare the packet on the task document as an approved-requirement-packet "
                    "reference (path/stableId/version), or admit a RequirementPacketLocation for "
                    f"{identity}; a projection never drops an owned obligation"
                ),
            )
        packet = self._read_packet(stable_id, revision, location.path)
        return RequirementProjection(
            identity=identity,
            owns=True,
            declaration="admitted-location",
            declaration_text=location.path,
            packet=packet,
        )

    def _read_packet(
        self,
        stable_id: str,
        revision: str,
        path: str,
    ) -> packets.RequirementPacketProjection:
        packet = packets.read_packet(
            task_root=Path(self.scope.bound_task.task_root),
            stable_id=stable_id,
            revision=revision,
            path=path,
        )
        self.expansion.append(packet.source)
        return packet

    # -- remaining planes ----------------------------------------------------------

    def _acceptance(
        self,
        owned_packets: tuple[packets.RequirementPacketProjection, ...],
    ) -> tuple[ProjectedFact, ...]:
        facts: list[ProjectedFact] = []
        for packet in owned_packets:
            section = role_section(packet, "expected-evidence")
            if section is None:
                self.gaps.append(missing_section_gap(packet, "expected-evidence"))
                continue
            facts.append(ProjectedFact(kind="current", text=section.body, source=packet.source))
        for obligation in self.document.openQuestions:
            if isinstance(obligation, AcceptanceObligationQuestion):
                facts.append(
                    ProjectedFact(
                        kind="proposal",
                        text=f"{obligation.id}: {obligation.question}",
                        source=_document_reference(self.scope.bound_document, "open questions"),
                    )
                )
        return tuple(facts)

    def _preservation(
        self,
        owned_packets: tuple[packets.RequirementPacketProjection, ...],
    ) -> tuple[ProjectedFact, ...]:
        facts: list[ProjectedFact] = []
        for packet in owned_packets:
            for role in _PRESERVATION_ROLES:
                section = role_section(packet, role)
                if section is None:
                    self.gaps.append(missing_section_gap(packet, role))
                    continue
                facts.append(
                    ProjectedFact(
                        kind="current",
                        text=f"[{packet.identity} § {section.heading}]\n{section.body}",
                        source=packet.source,
                    )
                )
        if not owned_packets:
            self.gaps.append(
                "no owned requirement packet was admitted, so no preservation boundary or "
                "failure obligation is projected; this seat's obligations are incomplete"
            )
        return tuple(facts)

    def _decisions(self) -> tuple[ProjectedFact, ...]:
        source = _document_reference(self.scope.bound_document, "decision log")
        anchored = replace(source, anchor="Shared decisions")
        return tuple(
            ProjectedFact(
                kind="current",
                text=f"{decision.at} — {decision.decision}\n  rationale: {decision.rationale}",
                source=anchored,
            )
            for decision in self.document.decisions
        )

    def _writable_scope(self) -> WritableScope:
        return WritableScope(
            task_document=self.scope.bound_document.path.as_posix(),
            admitted_actions=tuple(sorted(self.binding.admitted.tool_policy.granted)),
            worktree=self.scope.worktree,
        )

    def _handoff(
        self, requirements: tuple[RequirementProjection, ...]
    ) -> tuple[ProjectedFact, ...]:
        facts = [
            ProjectedFact(
                kind="current",
                text=f"task document (JSON authority): {self.scope.bound_document.path.as_posix()}",
            ),
            ProjectedFact(
                kind="current",
                text=f"rendered task document: {self.scope.bound_task.markdown_path}",
                source=ExpansionReference(
                    label="rendered task document",
                    location=self.scope.bound_task.markdown_path,
                ),
            ),
            ProjectedFact(
                kind="current",
                text=f"enclosure contract: {self.scope.worktree.contract_path}",
            ),
        ]
        facts.extend(
            ProjectedFact(
                kind="current",
                text=f"declared reference: {reference}",
                source=ExpansionReference(label="declared reference", location=reference),
            )
            for reference in self.document.references
        )
        facts.extend(
            ProjectedFact(
                kind="current",
                text=(
                    f"{requirement.identity}: deliver against {requirement.packet.path} "
                    f"(content {requirement.packet.digest})"
                ),
                source=requirement.packet.source,
            )
            for requirement in requirements
            if requirement.packet is not None
        )
        return tuple(facts)

    def _portfolio(self) -> tuple[ProjectedFact, ...]:
        if not self.plan.portfolio_facts:
            return ()
        facts = [
            ProjectedFact(kind="current", text=_subtask_line(row)) for row in self.document.subTasks
        ]
        facts.extend(
            ProjectedFact(kind="current", text=f"commands master: {commanded}")
            for commanded in self.document.orchestrates
        )
        facts.extend(
            ProjectedFact(
                kind="current",
                text=f"seat {seat.role} [{seat.state}] {seat.label}".rstrip(),
            )
            for seat in self.document.seats
        )
        facts.extend(self._graph_facts())
        if self.document.integrationBranch:
            facts.append(
                ProjectedFact(
                    kind="current",
                    text=f"integration branch: {self.document.integrationBranch}",
                )
            )
        return tuple(facts)

    def _graph_facts(self) -> list[ProjectedFact]:
        graph = self.document.executionGraph
        if graph is None:
            return []
        waves = [[node.ref.path for node in wave] for wave in graph.derived_waves()]
        return [
            ProjectedFact(
                kind="current",
                text=(
                    f"execution graph: {len(graph.nodes)} node(s), {len(graph.edges)} edge(s); "
                    f"waves: {waves}"
                ),
            )
        ]

    def _series(self) -> tuple[ProjectedFact, ...]:
        parent = next(
            (item for item in self.read if item.ref != self.scope.bound_document.ref), None
        )
        if parent is None:
            return ()
        row = _series_row(parent.document, self.scope.bound_task)
        if row is None:
            return ()
        return (
            ProjectedFact(
                kind="current",
                text=f"series row {_subtask_line(row)}",
                source=_document_reference(parent, "series index"),
            ),
        )

    def _evidence(self) -> tuple[ProjectedFact, ...]:
        document = self.document
        units = [*document.steps, *(sub for step in document.steps for sub in step.substeps)]
        done = sum(1 for unit in units if unit.status == "done")
        facts = [
            ProjectedFact(
                kind="historical",
                text=(
                    f"progress: {done} of {len(units)} declared step/substep unit(s) done; "
                    f"task status {document.status!r}"
                ),
                source=_document_reference(self.scope.bound_document, "steps"),
            )
        ]
        review = document.reviewState
        if review is not None:
            facts.append(
                ProjectedFact(
                    kind="historical",
                    text=(
                        f"review: round {review.round}, pending {review.pending}, "
                        f"{len(review.baselineFindings)} baseline finding(s), "
                        f"{len(review.remainingFindingIds)} remaining"
                    ),
                    source=_document_reference(self.scope.bound_document, "review"),
                )
            )
        if document.discardedSubTasks:
            facts.append(
                ProjectedFact(
                    kind="historical",
                    text=f"{len(document.discardedSubTasks)} discarded slice(s) recorded",
                    source=_document_reference(self.scope.bound_document, "discarded"),
                )
            )
        if document.executionRegistrations:
            facts.append(
                ProjectedFact(
                    kind="historical",
                    text=(
                        f"{len(document.executionRegistrations)} execution registration(s) recorded"
                    ),
                    source=_document_reference(self.scope.bound_document, "registrations"),
                )
            )
        return tuple(facts)

    def _knowledge(self) -> tuple[KnowledgeExpansion, ...]:
        source = self.request.knowledge
        if source is None:
            self.gaps.append(
                "knowledge expansion is not admitted: no knowledge source is supplied, so only "
                "the expansion references below are available for later retrieval"
            )
            return ()
        request = KnowledgeExpansionRequest(
            task_reference=self.scope.bound_task.reference,
            altitude=self.scope.bound_task.altitude,
            references=tuple(self.expansion),
        )
        return tuple(source.expand(request))

    def _gaps(self) -> list[str]:
        gaps = list(self.gaps)
        if self.scope.memory_gap is not None:
            gaps.append(self.scope.memory_gap)
        if not self.document.objective.strip():
            gaps.append(
                f"the bound task document {self.scope.bound_task.reference} declares no objective"
            )
        return gaps


def _subtask_line(row: SubTaskRef) -> str:
    scope = f" (scope: {row.scope})" if row.scope else ""
    return f"[{row.status}] {row.number} — {row.name}{scope}"


def _series_row(document: TaskDocument, bound: BoundTask) -> SubTaskRef | None:
    """The bound task's row in its parent's series index, matched the owner's own way."""

    names = {Path(bound.markdown_path).name, Path(bound.markdown_path).stem, bound.task_id}
    for row in document.subTasks:
        if row.number in names:
            return row
        if row.file and (Path(row.file).name in names or Path(row.file).stem in names):
            return row
    return None


def _document_reference(document: ResolvedTaskDocument, anchor: str) -> ExpansionReference:
    return ExpansionReference(
        label=f"{document.document.id} {anchor}",
        location=document.path.as_posix(),
        anchor=anchor,
    )


__all__ = ["project_task_context"]
