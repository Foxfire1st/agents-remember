"""Task-document reader DTOs, kept independent of task authoring and lifecycle state."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agents_remember.models.task_document import MasterExecutionNature
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.observer.projection_closeout import (
    CloseoutProjectionProblemNode,
    DiscardedSubTaskNode,
)
from agents_remember.observer.projection_graph import TaskExecutionGraphView


class TaskStepDispositionNode(BaseModel):
    """Why and when one exact task work unit was intentionally skipped."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["intentionalSkip"]
    reason: str
    recordedAt: str
    recordedVia: Literal["task_doc.skip_step"]
    lifecycleId: str | None = None


class TaskSubStepNode(BaseModel):
    """One substep of a task step (the JSON's finer-grained progress unit)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    status: str
    disposition: TaskStepDispositionNode | None = None
    # Free prose about this unit (MIK-R127 rule 9): the Steps block shows it as one
    # muted line under the step and opens it in place. Summaries carry a bounded
    # preview (rule 9); the on-demand body carries the whole note.
    note: str | None = None


class TaskStepNode(BaseModel):
    """One step of a task document, with its substeps -- the actual task *content* (slice 5c).

    The 3c projection carried only step counts; the cockpit detail needs the step list itself to
    show what a lifecycle is actually doing, so the served node carries the titles + statuses.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    status: str
    disposition: TaskStepDispositionNode | None = None
    substeps: list[TaskSubStepNode] = Field(default_factory=list)
    # The checkbox line (the deliverable) and free prose about this unit (MIK-R127
    # rule 9): the Steps block shows one of them as a muted line under the step.
    # Summaries carry bounded previews (rule 9); the on-demand body carries whole
    # strings. The flags tell the page a preview is shown; older readers ignore
    # them and render the preview as the value.
    outcome: str | None = None
    note: str | None = None
    outcomeTruncated: bool = False
    noteTruncated: bool = False


class TaskDecisionNode(BaseModel):
    """One decision-log row of a task document."""

    model_config = ConfigDict(extra="forbid")

    at: str
    decision: str
    rationale: str
    # True when the product wrote the entry itself (a record of an act, not a
    # decision — MIK-R126 rule 6 under the lifecycle-records ruling). The server marks
    # it from the writers' own constants; the stored entry keeps its shape.
    product_record: bool = False


class TaskHeaderNoteNode(BaseModel):
    """One extra header metadata line of a task document (MIK-R127 rule 9).

    Rendered as ``label: value`` in the head strip; at most six on the page, with a
    link to all where there are more.
    """

    model_config = ConfigDict(extra="forbid")

    label: str
    value: str = ""


class TaskReviewFindingNode(BaseModel):
    """One review finding of a task document (MIK-R127 rule 9).

    Every baseline finding is projected with its open flag: the Review block
    draws the open ones, and the findings view draws the whole round.
    """

    model_config = ConfigDict(extra="forbid")

    findingId: str
    description: str
    open: bool = True
    # True when a summary carries only a preview of the description (rule 9);
    # the on-demand body carries the whole string.
    descriptionTruncated: bool = False


class TaskReviewStateNode(BaseModel):
    """The review round of a task document (MIK-R127 rule 9), bounded as shown.

    ``findings`` carries the round's baseline findings with their descriptions
    (what the Review block and the findings view draw); ``totalFindings`` names
    the whole round for the link, and ``openFindings`` counts the open ones of
    the whole round: a summary carries only the first findings, so a reader
    must not count their open flags for the round.
    """

    model_config = ConfigDict(extra="forbid")

    round: int = 0
    pending: bool = False
    additionalRounds: int = 0
    findings: list[TaskReviewFindingNode] = Field(default_factory=list)
    totalFindings: int = 0
    openFindings: int = 0
    developerApproval: str | None = None


class TaskCodeExampleNode(BaseModel):
    """One proposed-code-example block of a task document (the plan's distinct change shapes)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    distinctChange: str
    why: str
    language: str = ""
    snippet: str = ""


class TaskSubTaskRefNode(BaseModel):
    """One slice in a master's series index -- the drill-in row (slice 6g).

    Mirrors ``tasks.document.SubTaskRef``: ``status`` drives the index marker and ``file`` is the
    drill-in match key (its stem resolves to the slice document's slug).
    """

    model_config = ConfigDict(extra="forbid")

    number: str
    name: str
    file: str = ""
    status: str
    scope: str = ""
    # When `file` points at another master (a parallel/external series), the contract-paired lifecycle
    # it links to — the dashboard renders such a row as a "→" cross-series jump (slice 6g). Null for an
    # in-series slice row.
    linkedLifecycleId: str | None = None
    # The typed master link (L14-R1): on an orchestration sprint, the exact commanded master document
    # this row tracks — the dashboard opens that document directly (sprint → master drill-down). Null
    # for an ordinary master's leaf rows and for legacy slug-only membership rows.
    masterRef: TaskDocumentRef | None = None


class TaskSeatNode(BaseModel):
    """One first-class seat of an orchestration sprint (L14-R3).

    Mirrors ``tasks.document.SprintSeat``: the sprint document owns the seat record, so seat task
    documents leave the sprint's task index (existing ones stay on disk as historical records).
    ``identity`` is a correlatable session or catalog id — provenance for correlation, never an
    authority source.
    """

    model_config = ConfigDict(extra="forbid")

    role: str
    label: str = ""
    identity: str | None = None
    state: str = "planned"  # planned | active | retired


class TaskSectionNode(BaseModel):
    """One ordered section of a master's render plan: freeform prose or a generated block (6g).

    Mirrors ``tasks.document.Section`` so the master overview renders its bespoke sections in order.
    """

    model_config = ConfigDict(extra="forbid")

    kind: str
    heading: str
    body: str = ""


class TaskExecutionEndpointNode(BaseModel):
    """One resolved-form edge endpoint: the master ref plus the segment-sampling leaf id.

    The persisted grammar also allows a bare ref (lump/legacy); the before-validator
    lifts that form so the served shape is uniform.
    """

    model_config = ConfigDict(extra="forbid")

    ref: TaskDocumentRef
    leafId: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _lift_bare_ref(cls, value: Any) -> Any:
        if isinstance(value, TaskDocumentRef):
            return {"ref": value}
        if isinstance(value, dict) and "ref" not in value and "repository" in value:
            return {"ref": value}
        return value


class TaskExecutionNode(BaseModel):
    """One graph node: a whole-master lump or a leaf-segment of one master (L11-R1)."""

    model_config = ConfigDict(extra="forbid")

    kind: str = "master"
    ref: TaskDocumentRef
    leafIds: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _lift_bare_ref(cls, value: Any) -> Any:
        if isinstance(value, TaskDocumentRef):
            return {"ref": value}
        if isinstance(value, dict) and "ref" not in value and "repository" in value:
            return {"ref": value}
        return value


class TaskExecutionEdgeNode(BaseModel):
    """One reasoned dependency edge in a sprint task document."""

    model_config = ConfigDict(extra="forbid")

    predecessor: TaskExecutionEndpointNode
    successor: TaskExecutionEndpointNode
    reason: str
    judgmentId: str | None = None


class TaskExecutionGraphNode(BaseModel):
    """Persisted AON graph projected without adding scheduler judgment."""

    model_config = ConfigDict(extra="forbid")

    nodes: list[TaskExecutionNode] = Field(default_factory=list)
    edges: list[TaskExecutionEdgeNode] = Field(default_factory=list)


class CloseoutCandidateNode(BaseModel):
    """One exact-current member of the disposable scheduling projection."""

    model_config = ConfigDict(extra="forbid")

    generationId: str
    taskDocumentRef: TaskDocumentRef
    owningMaster: TaskDocumentRef
    classification: str
    priority: str
    order: int = 0
    reasons: list[str] = Field(default_factory=list, max_length=256)


class CloseoutQueueNode(BaseModel):
    """Exact effective state for one disposable sprint scheduling projection."""

    model_config = ConfigDict(extra="forbid")

    sprintRef: TaskDocumentRef
    revision: int = 0
    serviceCondition: str
    sourceClassification: str | None = None
    sourceFingerprint: str | None = None
    sourceProblems: list[CloseoutProjectionProblemNode] = Field(
        default_factory=list,
        max_length=256,
    )
    members: list[CloseoutCandidateNode] = Field(default_factory=list)


class TaskDependencyNode(BaseModel):
    """A direct same-master dependency, including a no-longer-listed task."""

    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    status: str
    docPath: str | None = None


class TaskDocNode(BaseModel):
    """A task document's progress and reader content (slice 3c, surface 7).

    Read from the JSON-primary ``ar-task-document/v1`` document, so the dashboard
    can show what a task is doing without opening files on disk. ``lifecycleId``
    is runtime attachment, not the admission ticket: planning documents can be
    projected before an enclosure or lifecycle exists.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    lifecycleId: str | None = None
    repository: str
    title: str
    status: str
    kind: str
    # The document's form by the model's own rule (MIK-R127 rule 1): a master that
    # commands masters is a sprint, any other master is a master, anything else a
    # task. The page shows that word and chooses the skeleton by it.
    form: Literal["sprint", "master", "task"] = "task"
    # The document's declared type (e.g. "SubTask (role instructions)") for the
    # head strip (MIK-R127 rule 2).
    type: str = ""
    # A sprint's declared branch identity (MIK-R127 rule 2); master-only like the
    # document field it mirrors.
    integrationBranch: str | None = None
    # Descriptive status suffix beside the strict enum (MIK-R127 rule 2): one line
    # that opens in place. Summaries carry a bounded preview (rule 9); the
    # on-demand body carries the whole note.
    statusNote: str | None = None
    statusNoteTruncated: bool = False
    # Extra header metadata lines (MIK-R127 rule 2): label/value lines, at most six
    # on the page with a link to all. Summaries carry the first
    # _SUMMARY_HEADER_NOTES with the whole count (rule 9); the on-demand body
    # carries every note. None names an older cache, which the page reads as the
    # shown length.
    headerNotes: list[TaskHeaderNoteNode] = Field(default_factory=list)
    headerNotesTotal: int | None = None
    # The recorded review round (MIK-R127 rule 9): the Review block and the master
    # Now block read it from the always-on summary, never from a body fetch.
    reviewState: TaskReviewStateNode | None = None
    stepsDone: int = 0
    stepsTotal: int = 0
    currentStep: str | None = None
    docPath: str
    # Hash of the reader body fields omitted from the always-on summary. Dashboard readers use it
    # to refetch an already-open task document only when the on-demand body changed.
    bodyRevision: str = ""
    createdAt: str = ""
    completedAt: str | None = None
    waitsFor: list[str] | None = None
    waitsForTasks: list[TaskDependencyNode] | None = None
    neededByTasks: list[TaskDependencyNode] | None = None
    nonGoals: str | None = None
    acceptance: str | None = None
    ageSeconds: float | None = None
    steps: list[TaskStepNode] = Field(default_factory=list)
    # The readable task content (slice 5c): so the dashboard *is* the task reader -- you never
    # have to open the JSON on disk. The doc is the JSON-primary source (slice 3c).
    objective: str = ""
    requirements: list[str] = Field(default_factory=list)
    design: str | None = None
    codeExamples: list[TaskCodeExampleNode] = Field(default_factory=list)
    decisions: list[TaskDecisionNode] = Field(default_factory=list)
    openQuestions: list[str] = Field(default_factory=list)
    references: list[str] = Field(default_factory=list)
    # Master docs use these for the series index + ordered render plan; non-master task docs may
    # carry freeform sections. Empty when the document has no authored sections.
    subTasks: list[TaskSubTaskRefNode] = Field(default_factory=list)
    # Only master documents own discard-unstarted audit history. ``None`` distinguishes a
    # non-master task from a master whose typed history is present and empty.
    discardedCount: int | None = None
    discardedSubTasks: list[DiscardedSubTaskNode] | None = Field(
        default=None,
        max_length=256,
    )
    sections: list[TaskSectionNode] = Field(default_factory=list)
    # The lifecycle of the parent master this doc declares via its `master` ref, when that ref points to
    # a master in another series (a different lifecycle) -- drives a "↑ parent series" breadcrumb (6g).
    masterLifecycleId: str | None = None
    # The orchestration-command relation (260703-L14): non-empty only on a ``master`` doc that IS an
    # orchestration task -- the master task names it commands. The dashboard derives the
    # orchestration > master > leaf hierarchy from it; docs without the field render as before.
    orchestrates: list[str] = Field(default_factory=list)
    # The sprint's first-class seats (L14-R3): non-empty only on an orchestration sprint master;
    # the dashboard renders them as structure, never as seat task documents.
    seats: list[TaskSeatNode] = Field(default_factory=list)
    # A commanded master's declared Git execution nature. None is an explicit legacy/migration
    # signal, never an organizational default.
    executionNature: MasterExecutionNature | None = None
    # Sprint-only persisted graph plus mechanically derived topological waves.
    executionGraph: TaskExecutionGraphNode | None = None
    executionWaves: list[list[TaskExecutionNode]] = Field(default_factory=list)
    # Render-ready per-node sprint graph (L12-R4): the dashboard renders this view directly and
    # never joins raw refs to titles or re-derives waves/frontier state from the persisted graph.
    executionGraphView: TaskExecutionGraphView | None = None
    # The leaf's direct-execution grant with who gave it (MIK-R94 rule 4); None
    # when the leaf has no grant. Read from the one record at projection time.
    directGrant: DirectGrantNode | None = None


class DirectGrantNode(BaseModel):
    """A leaf's direct-execution grant as the dashboard shows it (MIK-R94 rule 4).

    Read from the one grant record at projection time, never stored twice.
    ``None`` on the parent node distinguishes a leaf with no grant.
    """

    model_config = ConfigDict(extra="forbid")

    grantedBy: str
    grantedAt: str


class SeriesSubTaskNode(BaseModel):
    """One subtask of a series master -- a single checkbox.

    ``status`` is the lever: ``planning`` -> ⬜, ``inProgress`` -> 🔨, ``Completed`` -> ✅.
    ``createdAt`` is resolved from the referenced leaf task document when present; the dashboard
    uses it for creation-order display without parsing file-name prefixes.
    """

    model_config = ConfigDict(extra="forbid")

    number: str
    name: str
    file: str = ""
    status: str
    scope: str = ""
    createdAt: str | None = None
    # The leaf's direct-execution grant with who gave it (MIK-R94 rule 4); None
    # when the leaf has no grant. Read from the one record at projection time.
    directGrant: DirectGrantNode | None = None


class SeriesSectionNode(BaseModel):
    """One ordered render section of a series master (freeform prose or a structured block)."""

    model_config = ConfigDict(extra="forbid")

    kind: str
    heading: str
    body: str = ""


class SeriesNode(BaseModel):
    """A series master's progress, keyed by its task FOLDER (R1 -- never a lifecycle).

    The master is also projected as a :class:`TaskDocNode` for direct document selection.
    ``SeriesNode`` is the folder-keyed aggregation/compatibility surface: the master
    checklist where each subtask that will run is one checkbox and ``doneCount`` counts the
    *declared* ``Completed`` subtasks, authoritative over a slice's own internal steps.
    Abandoned rows are neither done nor part of ``totalCount``; ``abandonedCount`` names them
    beside the figure ("5/5, 1 abandoned"). Carries the full master render (subTasks +
    sections + decisions) so older clients can still render the series reader.
    """

    model_config = ConfigDict(extra="forbid")

    seriesId: str
    repository: str
    title: str
    status: str
    createdAt: str = ""
    objective: str = ""
    subTasks: list[SeriesSubTaskNode] = Field(default_factory=list)
    discardedCount: int = 0
    discardedSubTasks: list[DiscardedSubTaskNode] = Field(default_factory=list, max_length=256)
    doneCount: int = 0
    totalCount: int = 0
    abandonedCount: int = 0
    sections: list[SeriesSectionNode] = Field(default_factory=list)
    decisions: list[TaskDecisionNode] = Field(default_factory=list)
    docPath: str
    ageSeconds: float | None = None
