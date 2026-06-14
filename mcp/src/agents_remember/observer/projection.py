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


class Analytics(BaseModel):
    """The slice-3b analytical surfaces: charts/feeds for specific cockpit panels.

    Kept apart from the structural tree (lifecycles/enclosures/providers) so the
    client-agnostic core stays small. Large raw inventories are deliberately *not*
    here -- drift rows stay in the snapshot file, the full sidecar list collapses to
    the histogram + a bounded leaderboard -- so the served projection stays lean.

    ``attentionQueue`` (slice 05) is the one *derived* surface here: the reducer
    composes it from the structural tree + these signals (not from an input file), so
    a structural-only caller can still see a non-empty queue.
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


class WorkspaceProjection(BaseModel):
    """The whole resolved tree: lifecycles + enclosures + providers + metrics + analytics."""

    model_config = ConfigDict(extra="forbid")

    version: int = 1
    generatedAt: str
    lifecycles: list[LifecycleProjection] = Field(default_factory=list)
    enclosures: list[EnclosureNode] = Field(default_factory=list)
    providers: list[ProviderNode] = Field(default_factory=list)
    metrics: Metrics = Field(default_factory=Metrics)
    analytics: Analytics = Field(default_factory=Analytics)
