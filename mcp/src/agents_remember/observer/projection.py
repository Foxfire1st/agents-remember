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

from dataclasses import dataclass, field
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
    evidenceRefs: list[dict[str, Any]] = Field(default_factory=list)
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
    # When the lifecycle last *entered its current written state* (seeded at
    # ``startedAt``, advanced on every state-changing event in the fold). Unlike
    # ``lastEventTs`` it is immune to heartbeats, so it is the stable
    # triggering-signal anchor the attention queue uses to honor a lifecycle
    # acknowledgement of an awaiting-developer / blocked item and to re-surface it on a *new*
    # occurrence. Inferred transitions (stale -> paused) intentionally do not move it.
    stateEnteredAt: str = ""
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
    checkedAt: str | None = None
    sourceRoot: str | None = None
    memoryRoot: str | None = None
    reportPath: str | None = None
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


class AgentPickupNode(BaseModel):
    """A pending dashboard response waiting for an agent to consume it."""

    model_config = ConfigDict(extra="forbid")

    id: str
    entryId: str
    lifecycleId: str | None = None
    agentId: str | None = None
    senderAgentId: str | None = None
    senderRole: str | None = None
    recipientRole: str | None = None
    gateId: str | None = None
    messageKind: str = "message"
    artifactPath: str | None = None
    deliveryState: str = "queued"
    deliveredToSession: str | None = None
    state: str
    ageSeconds: float | None = None
    ttlSeconds: float


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
    stepsDone: int = 0
    stepsTotal: int = 0
    currentStep: str | None = None
    docPath: str
    createdAt: str = ""
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
    sections: list[TaskSectionNode] = Field(default_factory=list)
    # The lifecycle of the parent master this doc declares via its `master` ref, when that ref points to
    # a master in another series (a different lifecycle) -- drives a "↑ parent series" breadcrumb (6g).
    masterLifecycleId: str | None = None


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
    checklist where each subtask is one checkbox and ``doneCount`` counts the *declared*
    ``Completed`` subtasks, authoritative over a slice's own internal steps. Carries the
    full master render (subTasks + sections + decisions) so older clients can still render
    the series reader.
    """

    model_config = ConfigDict(extra="forbid")

    seriesId: str
    repository: str
    title: str
    status: str
    createdAt: str = ""
    objective: str = ""
    subTasks: list[SeriesSubTaskNode] = Field(default_factory=list)
    doneCount: int = 0
    totalCount: int = 0
    seriesTokenTotal: int = 0
    sections: list[SeriesSectionNode] = Field(default_factory=list)
    decisions: list[TaskDecisionNode] = Field(default_factory=list)
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
    gateId: str | None = None
    enclosure: str | None = None
    repoId: str | None = None
    providerId: str | None = None
    # The triggering signal's timestamp (state-entry / gate / snapshot time), used to
    # honor a current lifecycle acknowledgement: an item is suppressed while an
    # acknowledgement at-or-after this exists, and re-surfaces when a *newer* signal arrives.
    # ``None`` means no freshness anchor, so an acknowledgement holds until the underlying
    # condition clears.
    signalTs: str | None = None


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
    runtimeState: str  # nominal | indexing | down | configured | missing | unknown
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
    factState: str = "planned"  # observed | derived | planned | missing
    detail: str | None = None
    # gh's own milestone timestamp for the PR ref -- mergedAt once merged, else createdAt (slice 5l
    # P2). ISO-8601 string; None for branch refs and PRs gh could not time. Display-only (05k).
    at: str | None = None


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
    health: str  # nominal|running|blocked|failed|stale|skipped|unknown|complete

    codeSource: CommitRefNode
    codeWorktree: CommitRefNode
    memoryMode: str  # "external" | "internal" | "disabled"
    memorySource: CommitRefNode | None = None
    memoryWorktree: CommitRefNode | None = None
    ledgerPath: str | None = None
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
    agentPickups: list[AgentPickupNode] = Field(default_factory=list)
    ledgers: list[LedgerNode] = Field(default_factory=list)
    taskDocuments: list[TaskDocNode] = Field(default_factory=list)
    attentionQueue: list[AttentionItem] = Field(default_factory=list)
    # The enclosure-centered Engine Room process map (slice 5e): one node per worktree
    # enclosure, derived from contract + status guidance + provider boot + lifecycle.
    engineProcesses: list[EngineProcessNode] = Field(default_factory=list)
    # The series/master surface (R1): one node per series master, keyed by its task FOLDER
    # (masters carry no lifecycleId, so the lifecycle-keyed taskDocuments never include them).
    series: list[SeriesNode] = Field(default_factory=list)


class WorkspaceProjection(BaseModel):
    """The whole resolved tree: lifecycles + enclosures + providers + metrics + analytics."""

    model_config = ConfigDict(extra="forbid")

    version: int = 2
    generatedAt: str
    lifecycles: list[LifecycleProjection] = Field(default_factory=list)
    enclosures: list[EnclosureNode] = Field(default_factory=list)
    providers: list[ProviderNode] = Field(default_factory=list)
    # Worktree-group basenames whose enclosure lifecycle is still active (non-terminal,
    # cleanup not completed/abandoned). The shared `enclosures`/`lifecycles` collections keep
    # every historical entry for the Operations/Lifecycle history surfaces; this is the bounded
    # active set the Topology constellation filters on so it shows live work, not all-time work.
    # Sourced from the same `active_enclosure_worktree_groups` admission the Engine Room uses,
    # so the two views share one definition of "active". The join key matches the worktree-scoped
    # `ProviderNode.worktreeGroup` (a basename) and `Path(EnclosureNode.worktreeGroup).name`.
    activeWorktreeGroups: list[str] = Field(default_factory=list)
    metrics: Metrics = Field(default_factory=Metrics)
    analytics: Analytics = Field(default_factory=Analytics)
