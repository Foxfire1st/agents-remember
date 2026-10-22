"""The projection schema: the resolved state the reducer produces.

These are the *persisted and served* contract -- written atomically to
``latest-state.json`` / ``latest-metrics.json`` and (slice 04) streamed over
SSE. Like :mod:`agents_remember.observer.events` they are deliberately **not**
MCP response models: they carry no token-accounting fields, are never returned
by a tool, and are absent from ``TOOL_RESPONSE_MODELS``. Fields are
camelCase to match the package's wire convention; ``extra="forbid"`` keeps the
served contract honest.

The shapes are client-agnostic (North-Star #2): a dashboard, a future TUI, and
an orchestrating agent are equal clients. The state tree is expressed as linked
one reference-keyed task collection with child references and bound worktree
facts, plus unbound enclosures and providers. Clients read these published
relationships without reconstructing task membership or activity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from agents_remember.models.lifecycles.operation import LifecycleOperationProjection
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.models.worktree import SourceLineageProjection
from agents_remember.observer.provider_limits import ProviderLimit

from . import projection_task_documents as task_documents

CloseoutProjectionProblemNode = task_documents.CloseoutProjectionProblemNode
DiscardedSubTaskNode = task_documents.DiscardedSubTaskNode
TaskExecutionGraphView = task_documents.TaskExecutionGraphView
TaskStepDispositionNode = task_documents.TaskStepDispositionNode
TaskSubStepNode = task_documents.TaskSubStepNode
TaskStepNode = task_documents.TaskStepNode
TaskDecisionNode = task_documents.TaskDecisionNode
TaskHeaderNoteNode = task_documents.TaskHeaderNoteNode
TaskReviewFindingNode = task_documents.TaskReviewFindingNode
TaskReviewStateNode = task_documents.TaskReviewStateNode
TaskCodeExampleNode = task_documents.TaskCodeExampleNode
TaskSubTaskRefNode = task_documents.TaskSubTaskRefNode
TaskSeatNode = task_documents.TaskSeatNode
TaskSectionNode = task_documents.TaskSectionNode
TaskExecutionEndpointNode = task_documents.TaskExecutionEndpointNode
TaskExecutionNode = task_documents.TaskExecutionNode
TaskExecutionEdgeNode = task_documents.TaskExecutionEdgeNode
TaskExecutionGraphNode = task_documents.TaskExecutionGraphNode
CloseoutCandidateNode = task_documents.CloseoutCandidateNode
CloseoutQueueNode = task_documents.CloseoutQueueNode
TaskDependencyNode = task_documents.TaskDependencyNode
TaskSummary = task_documents.TaskSummary
TaskDocNode = task_documents.TaskDocNode
DirectGrantNode = task_documents.DirectGrantNode


type AttentionSeverity = Literal["alarm", "warn", "info"]
type AttentionLane = Literal["repo", "worktree"]
type ProcessFactState = Literal[
    "observed", "derived", "planned", "missing", "stale", "not-applicable"
]
type ProcessHealth = Literal[
    "nominal", "running", "blocked", "failed", "stale", "skipped", "unknown", "complete"
]


class ActionAvailability(BaseModel):
    """Whether one action is currently safe -- decided by the reducer, never the UI.

    ``disabledReason`` and ``nextSafeAction`` are load-bearing for slice 06: the
    cockpit renders the affordance and its tooltip straight from this and never
    infers safety client-side.
    """

    model_config = ConfigDict(extra="forbid")

    action: str
    enabled: bool
    disabledReason: str | None = None
    nextSafeAction: str | None = None


class EnclosureNode(BaseModel):
    """A worktree enclosure (contract + group): the persistent identity anchor.

    ``lifecycleId`` is the contract's operation-binding key. It is not a
    separately projected agent session or a source of task activity.

    ``codeWorktreeExists`` / ``memoryWorktreeExists`` are the worktree-existence
    TRUTH (L11): stat'ed at snapshot time in the I/O layer, exactly how the worktree
    tools report existence (``contract.code_worktree.exists()`` in
    ``worktree_status``). The tasks surface filters visibility on these, never on a
    cleanup-state proxy — ``cleanup: reopened`` means contract-reset-awaiting-restart
    (worktrees gone until the next ``worktree_start``), not live work.
    """

    model_config = ConfigDict(extra="forbid")

    enclosure: str
    enclosureId: str = ""
    leafId: str = ""
    taskId: str
    taskName: str
    repoName: str
    taskRoot: str = ""
    lifecycleId: str
    worktreeGroup: str
    humanReviewStatus: str
    closeoutStatus: str
    integrationStatus: str
    cleanup: str
    codeWorktreeExists: bool = False
    memoryWorktreeExists: bool = False
    lifecycleOperation: LifecycleOperationProjection | None = None
    actions: list[ActionAvailability] = Field(default_factory=list)


class ProviderNode(BaseModel):
    """One provider's current-state snapshot (data surfaces 1 and 4).

    ``snapshotStaleSeconds`` is the age of the ``current.json`` file: provider
    state is call-triggered and stale between calls, so the reducer surfaces how
    old the snapshot is rather than pretending it is live. Workspace providers
    can be aggregate nodes or repo-covered nodes when the provider snapshot
    carries per-repo evidence. Worktree providers carry ``worktreeGroup`` and
    bind to their isolated enclosure.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    state: str
    ok: bool | None = None
    watcherUp: bool = False
    indexingState: str = "unknown"
    snapshotStaleSeconds: float | None = None
    # Binding (slice 5c + task 12 S2): a provider is either workspace-scoped or
    # part of a worktree's isolated stack. Workspace providers can carry repoId
    # when current-state has repo coverage evidence (CGC watcher rows, GrepAI
    # targetRepos); worktreeGroup remains the join key back to an enclosure and
    # takes precedence over repoId client-side.
    scope: str = "workspace"  # "workspace" | "worktree"
    role: str | None = None  # "code" (CGC) | "memory" (GrepAI)
    repoId: str | None = None
    worktreeGroup: str | None = None


class Metrics(BaseModel):
    """Workspace host-observation diagnostics; task counts belong to task entries."""

    model_config = ConfigDict(extra="forbid")

    ignoredHostAgents: int = 0


class DriftSnapshotNode(BaseModel):
    """A repo's onboarding-drift result, read from a persisted JSON snapshot (3b, b1).

    The reducer never classifies drift (that is git-per-sidecar -- far too costly for
    a poll-cadence write); it reads the snapshot the memory_quality drift run
    persisted and surfaces ``snapshotStaleSeconds`` (age of ``checkedAt``), exactly
    like a provider tile. The full per-sidecar rows stay in the snapshot file; the
    projection carries only the classification counts a gauge needs.
    """

    model_config = ConfigDict(extra="forbid")

    repository: str
    branch: str
    counts: dict[str, int] = Field(default_factory=dict)
    actionableCount: int = 0
    actionableFingerprint: str | None = None
    checkedAt: str | None = None
    sourceRoot: str | None = None
    memoryRoot: str | None = None
    reportPath: str | None = None
    snapshotStaleSeconds: float | None = None


class SetupSummaryNode(BaseModel):
    """The latest provider-setup outcome for one action (slice 3b, surface 2)."""

    model_config = ConfigDict(extra="forbid")

    action: str
    ok: bool | None = None
    ready: bool | None = None
    state: str | None = None
    generatedAt: str | None = None
    snapshotStaleSeconds: float | None = None
    resultCounts: dict[str, int] = Field(default_factory=dict)


class SetupProgressNode(BaseModel):
    """A worktree group's live provider-setup progress (slice 3b, surface 3).

    Projected through ``setup_progress.progress_status``, so a ``running`` group
    whose heartbeat went stale reads ``stale`` -- the boot-sequence widget data.
    """

    model_config = ConfigDict(extra="forbid")

    group: str
    state: str
    currentPhase: str | None = None
    heartbeatAgeSeconds: float | None = None
    completedCount: int = 0
    failedPhases: list[str] = Field(default_factory=list)


class RouteCoverageNode(BaseModel):
    """One onboarding route's coverage, from its ``overview.index.json`` (3b, surface 10)."""

    model_config = ConfigDict(extra="forbid")

    repository: str | None = None
    route: str
    sourceFilesInScope: int = 0
    fileSidecars: int = 0
    childRoutes: int = 0


class ToolReportNode(BaseModel):
    """A recent verbose tool-report file (slice 3b, surface 12; bounded keep-last-5)."""

    model_config = ConfigDict(extra="forbid")

    tool: str
    path: str
    label: str
    ageSeconds: float | None = None


LEDGER_WINDOW = 25
"""Newest-N memory.md ledger rows served per coupler popover (5h). Bounded at 25 = the performance guard:
the popover defaults to the first 8 and expands in place to these 25; older rows stay in the file
(the "+N more in memory.md" footer). The full-history browser is the post-ship viewer (agents-remember#88)."""


class LedgerRefNode(BaseModel):
    """One memory.md ledger row -- a code-commit -> memory-commit mapping (5h coupler popover).

    The warp couplers stand for the memory.md ledger link; the popover renders a window of these
    rows with this enclosure's row highlighted. Full SHAs (the popover shortens them for display).

    The optional ``*Subject`` / ``*Date`` fields are the per-side commit message + committer ISO
    date, probed best-effort from the local repos in the snapshots I/O layer (5h Tier 2: "hashes
    alone don't tell a story"). They are ``None`` -- and, since the projection dumps
    ``exclude_none=True``, omitted from the wire -- when the commit is not in the local repo or the
    git probe could not run, so the popover row honestly falls back to the hash alone. Never faked.
    """

    model_config = ConfigDict(extra="forbid")

    codeCommit: str
    memoryCommit: str
    codeSubject: str | None = None
    codeDate: str | None = None
    memorySubject: str | None = None
    memoryDate: str | None = None


class LedgerNode(BaseModel):
    """A repo's memory ledger currency (slice 3b, surface 8).

    ``closeoutCount`` is the ledger row count (one row per closeout); the rows carry
    no timestamps, so "closeouts over time" is deliberately not projected -- only the
    count + the last-verified-code-commit currency.

    ``rows`` is the newest ``LEDGER_WINDOW`` code<->memory mappings for the OFFICIAL coupler
    popover (5h); ``closeoutCount`` stays the full total so the popover's "+N more" footer is right.
    """

    model_config = ConfigDict(extra="forbid")

    repository: str
    closeoutCount: int = 0
    lastVerifiedCodeCommit: str
    baseCodeCommit: str
    rows: list[LedgerRefNode] = Field(default_factory=list)


class AttentionItem(BaseModel):
    """One thing that needs the human, decided by the reducer (note 06, slice 05).

    A ranked cross-section of the structural tree + analytics signals -- the
    home-screen attention queue. Computed server-side so every client (dashboard,
    TUI, agent) shares one queue, and ``waitSeconds`` is a server-computed age, never
    a client's render time. The ``*Id`` / ``enclosure`` cross-refs point back into the
    structural tree so the UI can couple a queue item to its operation-tree node.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: str
    severity: AttentionSeverity
    lane: AttentionLane
    title: str
    detail: str | None = None
    waitSeconds: float | None = None
    enclosure: str | None = None
    repoId: str | None = None
    providerId: str | None = None
    # Complete actionable findings from the native drift producer, excluding its clock.
    actionableFingerprint: str | None = None


class CommitRefNode(BaseModel):
    """One side of the official-line/worktree pair: a branch @ commit at a path (slice 5e).

    ``factState`` is the honesty axis: ``observed`` means the worktree checkout exists on
    disk; ``derived`` means the value is a recorded contract field whose checkout is not
    present; ``planned`` is an expected-but-not-yet step; ``missing`` is unobservable; and
    ``not-applicable`` is a lane that does not exist (e.g. memory on a ``disabled`` contract).
    The cockpit colours/animates from this so it never renders a planned path as a live one.
    """

    model_config = ConfigDict(extra="forbid")

    branch: str | None = None
    commit: str | None = None
    path: str | None = None
    exists: bool | None = None
    dirty: bool | None = None
    # How far the recorded base is behind the *local* source-branch tip (fetch-free; a
    # parallel cycle that landed). 0/None when current or unknown.
    behindSource: int | None = None
    factState: ProcessFactState = "missing"


class ProviderBootNode(BaseModel):
    """One isolated engine in a worktree's provider runtime: CGC (code) or GrepAI (memory).

    The boot *sequence* (seed/clone/watcher phases) lives on the owning
    :class:`EngineProcessNode` because provider setup is one progress file per worktree
    group; this node is the engine's identity + its current runtime health.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    role: str  # "code" (CGC) | "memory" (GrepAI)
    runtimeState: str  # nominal | indexing | down | configured | missing | unknown
    factState: ProcessFactState = "observed"


class EngineProcessEdge(BaseModel):
    """One conduit in the process map, with a state-backed visual treatment (slice 5e).

    Edges are derived from facts, never decoration: a ``cgc-seed`` edge reads ``running``
    only while the seed phase is live, ``failed`` on a failed phase, ``planned`` before it
    starts -- so an animated conduit always means an observed transition.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    fromNode: str
    toNode: str
    # git-base|worktree-add|ledger-map|contract-anchor|cgc-seed|grepai-clone|
    # watcher-start|sync|closeout|integration|cleanup
    kind: str
    # nominal|running|blocked|failed|stale|skipped|complete|planned|unknown
    state: str
    label: str
    detail: str | None = None


class LandingRefNode(BaseModel):
    """One remote/PR participant in the successful-landing arc (slice 5h).

    The landing tail references refs the *worktree* projection has no node for -- ``origin/main``,
    ``origin/<feat>``, the PR, ``origin/mem-main``. Like :class:`CommitRefNode`, ``factState`` is the
    honesty axis so the cockpit never animates a *planned* PR as an observed one: ``observed`` means a
    live probe (``git ls-remote`` / ``gh``) confirmed it, ``planned`` is an expected-but-not-yet step,
    and ``missing`` is a probe that could not run (e.g. ``gh`` absent/unauthed).
    """

    model_config = ConfigDict(extra="forbid")

    kind: str  # origin-main | origin-feat | origin-mem-main | pr
    label: str  # display: "origin/main", "PR #128"
    state: str  # behind | tip | open | merged | pushed | planned | unknown
    factState: ProcessFactState = "planned"
    detail: str | None = None
    # gh's own milestone timestamp for the PR ref -- mergedAt once merged, else createdAt (slice 5l
    # P2). ISO-8601 string; None for branch refs and PRs gh could not time. Display-only (05k).
    at: str | None = None
    observedAt: str | None = None
    lastAttemptAt: str | None = None
    staleSeconds: float | None = None


class EngineProcessNode(BaseModel):
    """One worktree enclosure as a state-backed process -- the Engine Room's unit (slice 5e).

    Composed (like ``attentionQueue``) from the contract + status guidance + provider boot +
    lifecycle, **not** read from a new file. The center of the Engine Room is this enclosure,
    not a provider card: the official source line, the code/memory worktrees, the contract
    coupling them, and the two isolated engines bound to that worktree group. ``health`` and
    the per-side ``factState``s carry the observed/derived/planned/missing honesty 5e demands.
    """

    model_config = ConfigDict(extra="forbid")

    id: str  # the contract path -- the stable enclosure id (== EnclosureNode.enclosure)
    enclosure: str
    worktreeGroup: str
    taskId: str
    leafId: str = ""
    taskName: str
    repoName: str
    lifecycleId: str | None = None
    # preflight|code-worktree|memory-compatibility|contract-written|worktree-started|
    # provider-setup|sync-needed|commit-approval-pending|closeout-pending|integration-pending|
    # integration-blocked|carryover-pending|cleanup-pending|completed|abandoned|unknown
    phase: str
    health: ProcessHealth

    codeSource: CommitRefNode
    codeWorktree: CommitRefNode
    memoryMode: str  # "external" | "disabled"
    memorySource: CommitRefNode | None = None
    memoryWorktree: CommitRefNode | None = None
    ledgerPath: str | None = None
    sourceLineage: SourceLineageProjection | None = None
    # The memory.md ledger window for the WORKTREE coupler popover (5h): newest ``LEDGER_WINDOW`` rows
    # mapping this worktree's code<->memory commits, plus the total count for the "+N more" footer.
    ledgerRows: list[LedgerRefNode] = Field(default_factory=list)
    ledgerRowCount: int = 0

    humanReviewStatus: str
    closeoutStatus: str
    integrationStatus: str
    integrationStrategy: str | None = (
        None  # ff-only | replay; None until the integration decision is recorded (5h)
    )
    cleanup: str
    # The carryover milestone (05m): ISO time the parked memory was carried into official memory
    # (read from the official ledger); None until carried. Display-only -- 5k renders the seam.
    carryoverDoneAt: str | None = None

    # Provider boot: one setup-progress sequence per worktree group (slice 3b reused).
    setupState: str | None = None  # running|stale|failed|failed-unchecked|ok|complete|prepared
    currentPhase: str | None = None
    completedPhases: list[str] = Field(default_factory=list)
    failedPhases: list[str] = Field(default_factory=list)
    heartbeatAgeSeconds: float | None = None
    seedFallback: bool = False
    retryArgs: dict[str, Any] | None = None

    providers: list[ProviderBootNode] = Field(default_factory=list)
    edges: list[EngineProcessEdge] = Field(default_factory=list)
    # The successful-landing arc -- remote/PR refs observed during closeout/integration (5h). Empty
    # until the lifecycle reaches a landing phase or when no probe could run.
    landing: list[LandingRefNode] = Field(default_factory=list)
    actions: list[ActionAvailability] = Field(default_factory=list)
    # The lifecycle-guidance next operation + human one-liner (display/copy only until slice 06).
    nextAction: str | None = None
    summary: str = ""

    missingFacts: list[str] = Field(default_factory=list)
    sourceFiles: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class EngineProcessFacts:
    """Raw per-enclosure facts the reader gathers for the process map (slice 5e).

    An *input* carrier, not a served node: ``contract`` is the pure ``contract_payload``
    dump (branches, commits, paths, gate states); ``guidance`` is the pure
    ``lifecycle_guidance`` (phase + next operation); ``status`` is the best-effort
    ``status_payload`` (worktree existence, dirty flags, base freshness, provider boot) or
    ``None`` when its git probes could not run. Defined here -- the module both the I/O reader
    (:mod:`agents_remember.observer.snapshots`) and the pure
    :func:`agents_remember.observer.reducer.build_engine_processes` already import -- so the
    reducer stays free of any I/O-layer dependency.
    """

    contract: dict[str, Any]
    guidance: dict[str, Any]
    status: dict[str, Any] | None
    # The worktree memory.md ledger window for the coupler popover (5h) -- read in the I/O layer
    # (snapshots) so the reducer stays a pure fold; newest ``LEDGER_WINDOW`` rows + the total count.
    ledger_rows: list[LedgerRefNode] = field(default_factory=list)
    ledger_row_count: int = 0


# 260707-HFX2-L13 F6/CS-6 D2+D1: ``TaskDocNode`` in the always-on projection is a summary -- the
# reader body is omitted there and fetched through the on-demand task-document endpoint. No bound
# limits how many documents are projected (260916-TDPU removed the document-count cap); "summary"
# describes the omitted fields only. This budget remains as a regression guardrail: the write path
# should now measure 0 body bytes for broadcast task documents.
TASK_DOCUMENTS_PAYLOAD_BUDGET_BYTES = 256 * 1024


def task_documents_body_bytes(docs: list[TaskSummary]) -> int:
    """Approximate byte cost of task-doc reader bodies still present in the broadcast (F6 guardrail).

    For summary nodes this returns zero; tests seed full nodes to prove the guard still catches any
    regression that reintroduces body fields to the always-on path.
    """
    total = 0
    for doc in docs:
        total += len(doc.objective) + len(doc.design or "") + len(doc.currentStep or "")
        total += sum(len(item) for item in doc.requirements)
        total += sum(len(item) for item in doc.openQuestions)
        total += sum(len(item) for item in doc.references)
        total += sum(len(section.heading) + len(section.body) for section in doc.sections)
        total += sum(
            len(example.distinctChange)
            + len(example.why)
            + len(example.snippet)
            + len(example.title)
            for example in doc.codeExamples
        )
        total += sum(len(decision.decision) + len(decision.rationale) for decision in doc.decisions)
    return total


class TaskState(BaseModel):
    """The task facts, computed together once by the task-entry producer."""

    model_config = ConfigDict(extra="forbid")

    stage: Literal["planned", "started", "aborted", "completed"]
    activity: Literal["works", "paused", "unknown"] | None = None
    eventAt: str | None = None
    # MIK-R190 rule 13: present only when the task is started, nothing of it works
    # and one of its seats is limited; the dashboard shows it instead of `paused`.
    providerLimit: ProviderLimit | None = None


class TaskCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    planned: int = 0
    started: int = 0
    aborted: int = 0
    completed: int = 0
    works: int = 0
    paused: int = 0
    unknown: int = 0


class TaskListDisagreement(BaseModel):
    """One task record that a master's sub-task list and the record's own master place apart.

    ``names-another-master``: this master lists the record, the record names ``recordMaster``.
    ``not-listed``: the record names this master (or, naming none, lies in its folder) and no
    master lists it. ``listed-twice``: this master lists a record that ``parent`` holds already.
    ``unresolvable-master``: the record's ``master`` text, served as ``masterLiteral``, names no
    task document; this master lists the record (``row``) or holds its folder.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["names-another-master", "not-listed", "listed-twice", "unresolvable-master"]
    record: TaskDocumentRef
    row: str | None = None
    recordMaster: TaskDocumentRef | None = None
    masterLiteral: str | None = None
    parent: str | None = None


class TaskEntry(TaskState):
    """One qualified task, its authored summary, runtime facts and ordered children."""

    key: str
    reference: TaskDocumentRef | None = None
    id: str
    scope: str | None = None
    title: str
    stepsDone: int = 0
    stepsTotal: int = 0
    summary: TaskSummary | None = None
    parent: str | None = None
    worktrees: list[EnclosureNode] = Field(default_factory=list)
    children: list[str] = Field(default_factory=list)
    counts: TaskCounts = Field(default_factory=TaskCounts)
    # Served only on a master where a record and a list disagree; at most one per listed row
    # and one per record that no list holds, so the corpus bounds it.
    disagreements: list[TaskListDisagreement] | None = None


class Analytics(BaseModel):
    """The slice-3b analytical surfaces: charts/feeds for specific cockpit panels.

    Kept apart from the structural task/enclosure/provider tree so the
    client-agnostic core stays small. Converted drift counts, provider setup/progress,
    route coverage, tool reports and memory-ledger currency describe the retained analytics.

    ``attentionQueue`` (slice 05) and ``engineProcesses`` (slice 5e) are the *derived*
    surfaces here: the reducer composes them from the structural tree + these signals (not
    from an input file), so a structural-only caller can still see them populated.
    """

    model_config = ConfigDict(extra="forbid")

    driftSnapshots: list[DriftSnapshotNode] = Field(default_factory=list)
    setupSummaries: list[SetupSummaryNode] = Field(default_factory=list)
    setupProgress: list[SetupProgressNode] = Field(default_factory=list)
    routeCoverage: list[RouteCoverageNode] = Field(default_factory=list)
    toolReports: list[ToolReportNode] = Field(default_factory=list)
    ledgers: list[LedgerNode] = Field(default_factory=list)
    attentionQueue: list[AttentionItem] = Field(default_factory=list)
    # The enclosure-centered Engine Room process map (slice 5e): one node per worktree
    # enclosure, derived from contract, status guidance and provider boot.
    engineProcesses: list[EngineProcessNode] = Field(default_factory=list)


class WorkspaceProjection(BaseModel):
    """The resolved tasks, unbound worktrees, providers, counts and analytics."""

    model_config = ConfigDict(extra="forbid")

    version: int = 2
    generatedAt: str
    tasks: list[TaskEntry] = Field(default_factory=list)
    taskCounts: TaskCounts = Field(default_factory=TaskCounts)
    enclosures: list[EnclosureNode] = Field(default_factory=list)
    providers: list[ProviderNode] = Field(default_factory=list)
    # Worktree-group basenames admitted by their nonterminal enclosure contracts.
    # Engine Room and Topology use the same active_enclosure_worktree_groups
    # membership. This is operational admission, never a task-stage source.
    activeWorktreeGroups: list[str] = Field(default_factory=list)
    # Projected closeout queues (one per sprint master with an execution graph), carrying candidate
    # states, grades, waiting reasons, and the active atomic blocker (L8-R1/R2/R5).
    closeoutQueues: list[CloseoutQueueNode] | None = None
    metrics: Metrics = Field(default_factory=Metrics)
    analytics: Analytics = Field(default_factory=Analytics)
