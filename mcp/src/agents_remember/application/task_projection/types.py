"""Frozen value types for the focused, branch-correct task-context projection.

A projection is a *computed* value. It reports facts that an existing AR owner
already decided -- the task document owner, the requirement packet owner, the
worktree contract and the admitted capsule binding -- and it never authors task
truth, never mutates task state and never publishes a second task database.

Layer placement. ``layers.toml`` ranks ``tasks`` (9) below ``application`` (21),
and this package's whole job is to read task truth *through* those existing
owners. Every value therefore sits beside its consumer in ``application``: hoisting
the pure types into ``models`` (rank 2) would force a second declaration of
:data:`agents_remember.tasks.document_refs.TaskAltitude`, because a rank-2 package
may not import the rank-9 package that owns that vocabulary. Two spellings of one
task vocabulary is exactly the drift the single-source rule forbids, so the types
stay at the lowest rank that can carry their real dependency.

Nothing in this module opens a file, reaches the network or calls a model.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from agents_remember.models.role_capsules.vocabulary import CapsuleOperation, CapsuleRole
from agents_remember.tasks.document_refs import TaskAltitude

#: Which of the three planes a projected line belongs to. The projection keeps
#: them apart on purpose: a historical record and an unresolved proposal must
#: never read as a current obligation, and a current obligation must never be
#: dropped because it happens to be long.
#:
#: ``current`` -- the bound task document, or a packet it declares, states it now.
#: ``historical`` -- evidence about work already recorded (completed steps, review
#: rounds, discarded slices, attempt journals). Referenced, not injected, unless
#: the bound document itself is the historical record.
#: ``proposal`` -- explicitly non-binding material: the task document's own
#: proposed implementation shape and its unresolved acceptance obligations.
ProjectionFactKind = Literal["current", "historical", "proposal"]

PROJECTION_FACT_KINDS: tuple[ProjectionFactKind, ...] = ("current", "historical", "proposal")

#: One projected section. The operation selects which channels a projection has,
#: so two operations over one binding produce genuinely different documents.
ProjectionChannel = Literal[
    "objective",
    "requirements",
    "acceptance",
    "scope",
    "decisions",
    "preservation",
    "handoff",
    "portfolio",
    "evidence",
]


@dataclass(frozen=True, slots=True)
class ExpansionReference:
    """A source link a consumer expands on demand instead of loading it now.

    Long rationale, ancestor decision logs, review history and attempt journals
    travel as references. Nothing about an obligation is ever shortened to make
    room for them: the reference is *instead of* the material, never a truncation
    of it.
    """

    label: str
    location: str
    anchor: str = ""

    def render(self) -> str:
        """``<location>#<anchor>`` when anchored, else the bare location."""

        return f"{self.location}#{self.anchor}" if self.anchor else self.location


@dataclass(frozen=True, slots=True)
class ProjectedFact:
    """One projected line, always carrying its plane and, when there is one, its source."""

    kind: ProjectionFactKind
    text: str
    source: ExpansionReference | None = None


@dataclass(frozen=True, slots=True)
class PacketSection:
    """One ``##`` section of a requirement packet, verbatim.

    The body is stored exactly as the packet carries it. A projection that
    re-flowed, summarised or clipped a required behaviour, a negative constraint
    or a failure obligation would be the "truncate to hit a size target" defect
    the requirement forbids, so no such step exists anywhere in this package.
    """

    heading: str
    body: str


@dataclass(frozen=True, slots=True)
class RequirementPacketProjection:
    """A requirement packet once the existing owners have located and identified it."""

    stable_id: str
    revision: str
    path: str
    digest: str
    sections: tuple[PacketSection, ...]

    @property
    def identity(self) -> str:
        return f"{self.stable_id}@{self.revision}"

    @property
    def source(self) -> ExpansionReference:
        return ExpansionReference(
            label=f"requirement packet {self.identity}",
            location=self.path,
        )

    @property
    def headings(self) -> tuple[str, ...]:
        return tuple(section.heading for section in self.sections)

    def section(self, heading: str) -> PacketSection | None:
        """The named section, or ``None`` when the packet does not carry it."""

        return next((item for item in self.sections if item.heading == heading), None)

    def section_body(self, heading: str) -> str | None:
        section = self.section(heading)
        return None if section is None else section.body


@dataclass(frozen=True, slots=True)
class RequirementPacketLocation:
    """An admitted, version-addressed location for one requirement packet.

    A consumer supplies this only for a requirement the bound task document
    declares as exact text rather than as a typed ``approved-requirement-packet``
    reference. ``path`` is task-root-relative, exactly like the typed reference's
    own path, so both routes address the same confined artifact.
    """

    stable_id: str
    revision: str
    path: str

    @property
    def identity(self) -> str:
        return f"{self.stable_id}@{self.revision}"


@dataclass(frozen=True, slots=True)
class RequirementProjection:
    """One requirement the bound task document declares, with its packet when it has one.

    ``owns`` is true for the requirement revisions the admitted binding makes this
    seat accountable for. An adjacent declaration is dependency/preservation
    context: this seat may not claim it closed, and its packet is not read.

    ``identity`` is ``None`` for a declaration the task document writes as exact
    text: prose does not opt itself into a version-addressed identity, so the
    projection reports the text rather than inventing one.
    """

    identity: str | None
    owns: bool
    declaration: Literal["approved-packet", "exact-text", "admitted-location"]
    declaration_text: str
    packet: RequirementPacketProjection | None


@dataclass(frozen=True, slots=True)
class BoundTask:
    """The one admitted task document the projection was bound to, as the owners read it."""

    reference: str
    task_id: str
    title: str
    kind: str
    altitude: TaskAltitude
    objective: str
    document_digest: str
    task_root: str
    markdown_path: str


@dataclass(frozen=True, slots=True)
class WorktreeBinding:
    """The admitted worktree, branch and memory surface, from the contract that owns them."""

    repository_id: str
    contract_path: str
    code_worktree: str
    work_branch: str
    source_branch: str
    base_commit: str
    memory_mode: str
    memory_worktree: str | None
    memory_work_branch: str
    ledger_path: str | None


@dataclass(frozen=True, slots=True)
class WritableScope:
    """Where the seat may write, and which actions were admitted for it.

    Both halves come from an existing owner -- the worktree contract and the
    admitted ``CapsuleToolPolicy`` snapshot -- and the projection only reports
    them. It grants nothing.
    """

    task_document: str
    admitted_actions: tuple[str, ...]
    worktree: WorktreeBinding


@dataclass(frozen=True, slots=True)
class ProjectionReadPlan:
    """The explicit, inspectable answer to "what may this seat read, and how much".

    There is no heuristic here and no "read everything, filter later" step: the
    plan names the altitudes the projection reads, whether an ancestor's decision
    log is injected or only referenced, whether portfolio facts are read, and the
    exact channel set. A plan is a value, so a consumer can print it and a test can
    pin it.
    """

    altitude: TaskAltitude
    read_altitudes: tuple[TaskAltitude, ...]
    portfolio_facts: bool
    channels: tuple[ProjectionChannel, ...]


@dataclass(frozen=True, slots=True)
class ProjectionSelection:
    """What the plan actually did: the documents read, and the ones only pointed at."""

    role: CapsuleRole | None
    operation: CapsuleOperation
    altitude: TaskAltitude
    parent_altitude: TaskAltitude | None
    plan: ProjectionReadPlan
    read_documents: tuple[str, ...]
    referenced_documents: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class KnowledgeExpansionRequest:
    """What a later knowledge view is asked for, once such a view exists.

    This is the recorded seam. It carries no invariant-database identity and
    presupposes no schema beyond the expansion references the projection already
    produced, so a future implementation can be supplied without changing this
    package or its consumers.
    """

    task_reference: str
    altitude: TaskAltitude
    references: tuple[ExpansionReference, ...]


@dataclass(frozen=True, slots=True)
class KnowledgeExpansion:
    """One extra line a later knowledge view returns for an expansion reference."""

    kind: ProjectionFactKind
    text: str
    source: ExpansionReference | None = None


@runtime_checkable
class TaskKnowledgeExpansionSource(Protocol):
    """The typed input seam for later knowledge retrieval.

    Nothing in this package depends on the Knowledge Substrate master, and no
    implementation of this protocol ships here. A projection without a source
    reports the expansion channel as unadmitted rather than silently omitting it.
    """

    def expand(self, request: KnowledgeExpansionRequest) -> tuple[KnowledgeExpansion, ...]:
        """Return the extra material for ``request``, or ``()`` when there is none."""
        ...  # pragma: no cover


@dataclass(frozen=True, slots=True)
class TaskProjectionRequest:
    """The declared inputs a consumer supplies beyond the admitted capsule binding.

    ``requirement_packets`` is the version-addressed fallback route for a task
    document that declares a requirement as exact text only. ``knowledge`` is the
    recorded seam for a later knowledge view; with no source, the projection
    reports that channel as unadmitted.
    """

    requirement_packets: tuple[RequirementPacketLocation, ...] = ()
    knowledge: TaskKnowledgeExpansionSource | None = None


@dataclass(frozen=True, slots=True)
class TaskProjection:
    """The complete, read-only projection: typed facts plus their rendered Markdown.

    ``markdown`` is the model-visible channel. Everything else is the inspectable
    half -- the binding, the selection, and the per-fact plane -- which keeps
    identity and provenance diagnostics out of the prose that reaches a model
    while leaving them available to a reviewer or an operator.
    """

    task: BoundTask
    worktree: WorktreeBinding
    selection: ProjectionSelection
    objective: str
    requirements: tuple[RequirementProjection, ...]
    acceptance: tuple[ProjectedFact, ...]
    scope: WritableScope
    decisions: tuple[ProjectedFact, ...]
    preservation: tuple[ProjectedFact, ...]
    handoff: tuple[ProjectedFact, ...]
    portfolio: tuple[ProjectedFact, ...]
    series: tuple[ProjectedFact, ...]
    evidence: tuple[ProjectedFact, ...]
    knowledge: tuple[KnowledgeExpansion, ...]
    expansion: tuple[ExpansionReference, ...]
    gaps: tuple[str, ...]
    markdown: str
    projection_revision: str


__all__ = [
    "PROJECTION_FACT_KINDS",
    "BoundTask",
    "ExpansionReference",
    "KnowledgeExpansion",
    "KnowledgeExpansionRequest",
    "PacketSection",
    "ProjectedFact",
    "ProjectionChannel",
    "ProjectionFactKind",
    "ProjectionReadPlan",
    "ProjectionSelection",
    "RequirementPacketLocation",
    "RequirementPacketProjection",
    "RequirementProjection",
    "TaskKnowledgeExpansionSource",
    "TaskProjection",
    "TaskProjectionRequest",
    "WorktreeBinding",
    "WritableScope",
]
