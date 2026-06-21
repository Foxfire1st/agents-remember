"""The projection schema: the resolved state the reducer produces.

These are the *persisted and served* contract -- written atomically to
``latest-state.json`` / ``latest-metrics.json`` and (slice 04) streamed over
SSE. Like :mod:`agents_remember.observer.events` they are deliberately **not**
MCP response models: they carry no token-accounting fields, are never returned
by a tool, and are absent from ``PUBLIC_TOOL_RESPONSE_MODELS``. Fields are
camelCase to match the package's wire convention; ``extra="forbid"`` keeps the
served contract honest.

The shapes are client-agnostic (North-Star #2): a dashboard, a future TUI, and
an orchestrating agent are equal clients. The state tree is expressed as linked
flat collections (lifecycles, enclosures, providers) cross-referenced by id
(``enclosure.lifecycleId``, ``provider.repoId``) rather than deep nesting --
friendlier to every client than one nested document, and it keeps multi-repo
enclosures (North-Star #4) from being keyed to a single repo.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agents_remember.observer.lifecycle_state import Phase, State


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


class TokenSample(BaseModel):
    """One point on a lifecycle's cumulative-token fuel gauge (slice 3b, §2.4).

    Derived from the event log: every ``tool.completed`` event carries the tokens
    that call cost, so the running total over event timestamps is the time series
    note 03 gap #2 ("no token-spend persistence") wanted -- now derivable because
    the event substrate (slice 02) persists it.
    """

    model_config = ConfigDict(extra="forbid")

    ts: str
    cumulative: int


class GateNode(BaseModel):
    """A durable gate materialized into the projection (slice 6c): the dashboard's
    review surface for a decision point on a lifecycle.

    Distinct from the event-derived ``ask`` proto-gate -- this is the persisted
    ``GateRecord`` (``controlplane``) the operator acts on. ``decisions`` is the set
    of verbs the cockpit may POST for an open gate (empty once decided), so the UI
    renders the affordances straight from the reducer, never inferring them.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: str
    state: str
    decidedBy: str | None = None
    decidedVia: str | None = None
    decisions: list[str] = Field(default_factory=list)
    packet: dict[str, Any] = Field(default_factory=dict)
    ts: str


class LifecycleProjection(BaseModel):
    """One lifecycle's resolved state, folded from its event log.

    Fleeting lifecycles project as bare-bones entries (no ``enclosure``);
    persistent ones carry the full enclosure detail -- the slice-05 visual
    distinction falls out of this shape, not UI inference.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    state: State
    phase: Phase
    fleeting: bool
    enclosure: str | None = None
    repoId: str | None = None
    scope: str | None = None
    tokens: int = 0
    startedAt: str
    lastEventTs: str
    staleSeconds: float | None = None
    # True when ``state`` was *derived* by the reducer (stale heartbeat -> paused;
    # dormant fleeting -> abandoned) rather than read from a written transition.
    # Keeps "never pretend declared is observed" enforceable from the data alone.
    inferred: bool = False
    # The latest open block ask (the proto-gate); slice 06 materializes the record.
    ask: dict[str, Any] | None = None
    # The durable gate materialized from the GateStore (slice 6c): the dashboard's
    # review surface. None when the lifecycle has no open gate.
    gate: GateNode | None = None
    actions: list[ActionAvailability] = Field(default_factory=list)
    # The cumulative-token fuel gauge, folded from the log's tool.completed events.
    tokenSeries: list[TokenSample] = Field(default_factory=list)


class EnclosureNode(BaseModel):
    """A worktree enclosure (contract + group): the persistent identity anchor.

    ``lifecycleId`` cross-references the lifecycle this enclosure anchors (it is
    ``""`` for a legacy contract written before the lifecycle field existed).
    """

    model_config = ConfigDict(extra="forbid")

    enclosure: str
    taskId: str
    taskName: str
    repoName: str
    lifecycleId: str
    worktreeGroup: str
    humanReviewStatus: str
    closeoutStatus: str
    integrationStatus: str
    cleanup: str
    actions: list[ActionAvailability] = Field(default_factory=list)


class ProviderNode(BaseModel):
    """One provider's current-state snapshot (data surface 1).

    ``snapshotStaleSeconds`` is the age of the ``current.json`` file: provider
    state is call-triggered and stale between calls, so the reducer surfaces how
    old the snapshot is rather than pretending it is live.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    state: str
    ok: bool | None = None
    watcherUp: bool = False
    indexingState: str = "unknown"
    snapshotStaleSeconds: float | None = None
    # Binding (slice 5c): a provider is either workspace-scoped or part of a worktree's *isolated*
    # stack. The engine room shows each worktree's CGC (its code repo) + GrepAI (its memory repo),
    # so a worktree-scoped node carries its worktree group + repo + role rather than being lumped
    # in with main's. ``worktreeGroup`` is the join key back to the enclosure (group name).
    scope: str = "workspace"  # "workspace" | "worktree"
    role: str | None = None  # "code" (CGC) | "memory" (GrepAI)
    repoId: str | None = None
    worktreeGroup: str | None = None


class Metrics(BaseModel):
    """Workspace rollups: 3a point counts + the 3b derived aggregates.

    ``stalenessHistogram`` buckets every onboarding sidecar by the age of its
    ``lastVerifiedCommitDate`` (slice 3b, surface 11) -- the git-free
    verification-age distribution note 03 wanted, computed without classifying
    drift. Per-lifecycle token *series* live on each ``LifecycleProjection``; these
    are the workspace-wide rollups.
    """

    model_config = ConfigDict(extra="forbid")

    lifecycleCount: int = 0
    runningCount: int = 0
    blockedCount: int = 0
    pausedCount: int = 0
    totalTokens: int = 0
    stalenessHistogram: dict[str, int] = Field(default_factory=dict)


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
    snapshotStaleSeconds: float | None = None


class SidecarStaleNode(BaseModel):
    """One onboarding sidecar's verification age (slice 3b, surface 11; git-free).

    Read from the sidecar's table metadata (``lastVerifiedCommitDate``) with no git
    -- the always-on complement to the drift snapshot. The projection carries only
    the *stalest* bounded sample (a leaderboard); the full distribution is the
    ``Metrics.stalenessHistogram`` rollup.
    """

    model_config = ConfigDict(extra="forbid")

    onboardingFile: str
    repository: str
    lastVerifiedDate: str
    ageSeconds: float | None = None


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


class LedgerNode(BaseModel):
    """A repo's memory ledger currency (slice 3b, surface 8).

    ``closeoutCount`` is the ledger row count (one row per closeout); the rows carry
    no timestamps, so "closeouts over time" is deliberately not projected -- only the
    count + the last-verified-code-commit currency.
    """

    model_config = ConfigDict(extra="forbid")

    repository: str
    closeoutCount: int = 0
    lastVerifiedCodeCommit: str
    baseCodeCommit: str


class TaskSubStepNode(BaseModel):
    """One substep of a task step (the JSON's finer-grained progress unit)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    status: str


class TaskStepNode(BaseModel):
    """One step of a task document, with its substeps -- the actual task *content* (slice 5c).

    The 3c projection carried only step counts; the cockpit detail needs the step list itself to
    show what a lifecycle is actually doing, so the served node carries the titles + statuses.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    status: str
    substeps: list[TaskSubStepNode] = Field(default_factory=list)


class TaskDecisionNode(BaseModel):
    """One decision-log row of a task document."""

    model_config = ConfigDict(extra="forbid")

    at: str
    decision: str
    rationale: str


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


class TaskSectionNode(BaseModel):
    """One ordered section of a master's render plan: freeform prose or a generated block (6g).

    Mirrors ``tasks.document.Section`` so the master overview renders its bespoke sections in order.
    """

    model_config = ConfigDict(extra="forbid")

    kind: str
    heading: str
    body: str = ""


class TaskDocNode(BaseModel):
    """A task document's progress, keyed by lifecycle (slice 3c, surface 7).

    Read from the JSON-primary ``ar-task-document/v1`` document (the source of
    truth -- never the rendered markdown), so the dashboard can show what a
    lifecycle is doing at step granularity. Bounded to the dashboard's needs: the
    per-step detail stays in the document file, and documents not yet bound to a
    lifecycle are omitted by the reader.
    """

    model_config = ConfigDict(extra="forbid")

    lifecycleId: str
    repository: str
    title: str
    status: str
    kind: str
    stepsDone: int = 0
    stepsTotal: int = 0
    currentStep: str | None = None
    docPath: str
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
    # Master-only (kind == "master"): the series index + the ordered render plan. A master carries
    # no lifecycleId of its own (schema) -- it is contract-paired to the series lifecycle by the
    # reader (slice 6g). Empty for light/subTask docs.
    subTasks: list[TaskSubTaskRefNode] = Field(default_factory=list)
    sections: list[TaskSectionNode] = Field(default_factory=list)
    # The lifecycle of the parent master this doc declares via its `master` ref, when that ref points to
    # a master in another series (a different lifecycle) -- drives a "↑ parent series" breadcrumb (6g).
    masterLifecycleId: str | None = None


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
    severity: str
    lane: str
    title: str
    detail: str | None = None
    waitSeconds: float | None = None
    lifecycleId: str | None = None
    enclosure: str | None = None
    repoId: str | None = None
    providerId: str | None = None


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
    factState: str = "missing"  # observed | derived | planned | missing | not-applicable


class ProviderBootNode(BaseModel):
    """One isolated engine in a worktree's provider runtime: CGC (code) or GrepAI (memory).

    The boot *sequence* (seed/clone/watcher phases) lives on the owning
    :class:`EngineProcessNode` because provider setup is one progress file per worktree
    group; this node is the engine's identity + its current runtime health.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    role: str  # "code" (CGC) | "memory" (GrepAI)
    runtimeState: str  # nominal | indexing | down | configured | unknown
    factState: str = "observed"


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
    taskName: str
    repoName: str
    lifecycleId: str | None = None
    # preflight|code-worktree|memory-compatibility|contract-written|worktree-started|
    # provider-setup|sync-needed|commit-approval-pending|closeout-pending|integration-pending|
    # integration-blocked|cleanup-pending|completed|abandoned|unknown
    phase: str
    health: str  # nominal|running|blocked|failed|stale|skipped|unknown|complete

    codeSource: CommitRefNode
    codeWorktree: CommitRefNode
    memoryMode: str  # "external" | "internal" | "disabled"
    memorySource: CommitRefNode | None = None
    memoryWorktree: CommitRefNode | None = None
    ledgerPath: str | None = None

    humanReviewStatus: str
    closeoutStatus: str
    integrationStatus: str
    cleanup: str

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


class Analytics(BaseModel):
    """The slice-3b analytical surfaces: charts/feeds for specific cockpit panels.

    Kept apart from the structural tree (lifecycles/enclosures/providers) so the
    client-agnostic core stays small. Large raw inventories are deliberately *not*
    here -- drift rows stay in the snapshot file, the full sidecar list collapses to
    the histogram + a bounded leaderboard -- so the served projection stays lean.

    ``attentionQueue`` (slice 05) and ``engineProcesses`` (slice 5e) are the *derived*
    surfaces here: the reducer composes them from the structural tree + these signals (not
    from an input file), so a structural-only caller can still see them populated.
    """

    model_config = ConfigDict(extra="forbid")

    driftSnapshots: list[DriftSnapshotNode] = Field(default_factory=list)
    stalestSidecars: list[SidecarStaleNode] = Field(default_factory=list)
    setupSummaries: list[SetupSummaryNode] = Field(default_factory=list)
    setupProgress: list[SetupProgressNode] = Field(default_factory=list)
    routeCoverage: list[RouteCoverageNode] = Field(default_factory=list)
    toolReports: list[ToolReportNode] = Field(default_factory=list)
    ledgers: list[LedgerNode] = Field(default_factory=list)
    taskDocuments: list[TaskDocNode] = Field(default_factory=list)
    attentionQueue: list[AttentionItem] = Field(default_factory=list)
    # The enclosure-centered Engine Room process map (slice 5e): one node per worktree
    # enclosure, derived from contract + status guidance + provider boot + lifecycle.
    engineProcesses: list[EngineProcessNode] = Field(default_factory=list)


class WorkspaceProjection(BaseModel):
    """The whole resolved tree: lifecycles + enclosures + providers + metrics + analytics."""

    model_config = ConfigDict(extra="forbid")

    version: int = 2
    generatedAt: str
    lifecycles: list[LifecycleProjection] = Field(default_factory=list)
    enclosures: list[EnclosureNode] = Field(default_factory=list)
    providers: list[ProviderNode] = Field(default_factory=list)
    metrics: Metrics = Field(default_factory=Metrics)
    analytics: Analytics = Field(default_factory=Analytics)
