"""Models for worktree state included in context packets."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, field_validator

from agents_remember.kernel.coordination_context.models import MemoryMode
from agents_remember.models.base import FlexibleToolResponse, StrictResponseModel
from agents_remember.models.closeout.input import (
    CloseoutCorrectedCall,
    CloseoutInvalidField,
    ResolvedCloseoutPlan,
)
from agents_remember.models.lifecycles.memory_candidate import MemoryCandidatePairIdentity
from agents_remember.models.lifecycles.operation import LifecycleOperationProjection
from agents_remember.models.lifecycles.operation_kinds import LifecycleOperationKind
from agents_remember.models.lifecycles.operation_wait import LifecycleWaitOutcome
from agents_remember.models.quality import QualityGateResult
from agents_remember.models.structural.atomic_series_activation import (
    AtomicSeriesActivationRecord,
    AtomicSeriesObservedState,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.models.tools.public_roster import PUBLIC_TOOLS

# Worktree wire vocabulary (moved from worktrees.worktree_contract / modules.guidance).
WorkflowKind = Literal["chat-task", "light-task"]
HumanReviewStatus = Literal["pending-review", "approved"]
CloseoutStatus = Literal["not-started", "completed"]
LifecycleStatus = CloseoutStatus  # the published wire name for the closeout status
# ``checkpointed`` is the third terminal-adjacent value and the one this model was missing: the
# master's current line has landed into its super branch and the master will continue. Without it a
# partially landed master had to report ``not-started`` -- "nothing of mine has left" -- while its
# content was already upstream, which is exactly the state that made a partial master's retirement
# look safe.
IntegrationStatus = Literal["not-started", "completed", "blocked", "checkpointed"]
CleanupStatus = Literal["pending", "completed", "abandoned", "reopened"]
WorktreePhase = Literal[
    "worktree-started",
    "closeout-pending",
    "integration-pending",
    "integration-blocked",
    "carryover-pending",
    "cleanup-pending",
    "cleanup-completed",
    "abandoned",
]
NextOperation = Literal[
    "continue_work",
    "closeout",
    "request_integration_decision",
    "developer_decision",
    "request_carryover_decision",
    "finalize",
    "done",
]
NextTool = Literal[
    "worktree_status",
    "worktree_closeout_apply",
    "worktree_integrate",
    # The checkpoint's apply call. It is a registered public worktree tool and an approval-gated
    # protected-ref landing, so the same `request_integration_decision` intent that carries a
    # finished master to `worktree_integrate` carries an unfinished one here. It is a partial
    # PUBLICATION, not the pause: pausing a master moves no ref and is no integration decision at
    # all. The operation vocabulary is deliberately NOT widened for it either -- adding a member to
    # `NextOperation` would put a non-phase value into the set `WorktreeSummary` and the context
    # packet claim.
    "worktree_checkpoint_landing",
    "memory_carryover_plan",
    "worktree_cleanup",
    "lifecycle_finalize_task",
    # The published curator-coherence authority's standalone ``validate``. It is a registered public
    # tool, and it is the one step whose window closes at ``lifecycle_finalize_task``: finalize's
    # automatic cleanup collects the enclosure root, so a leaf that integrates and finalizes without
    # validating can never re-prove what it published (D-25). The hint names it before integration
    # for exactly that reason. ``NextOperation`` is deliberately NOT widened for it -- the move is
    # still the integration decision, with the validation as its precondition.
    "curator_coherence",
]
SourceLineageState = Literal["current", "blocked", "unavailable"]
SourceLineageEdgeState = Literal["current", "behind", "diverged", "unavailable"]
SourceLineageRelation = Literal["super-to-master", "master-to-leaf", "super-to-leaf"]
SourceLineageSide = Literal["code", "memory"]
SyncResolutionAction = Literal["continue", "cancel"]
MemorySyncChoice = Literal["merge-memory", "skip-memory"]
SyncSide = Literal["code", "memory"]
SyncPhase = Literal[
    "running-code",
    "code-resolution-required",
    "running-memory",
    "memory-resolution-required",
    "finalizing",
    "cancelling",
    "completed",
    "cancelled",
]
SyncOperationState = Literal[
    "running",
    "resolution-required",
    "cancelling",
    "completed",
    "cancelled",
    "journal-malformed",
    "journal-identity-invalid",
    "quarantined",
]


class SourceLineageEdge(StrictResponseModel):
    """One plane-resolved ancestry edge; agents never supply or retain commit ids."""

    relation: SourceLineageRelation
    side: SourceLineageSide
    state: SourceLineageEdgeState
    sourceBranch: str
    descendantBranch: str
    ahead: int | None = None
    behind: int | None = None
    contractPath: str
    syncContractPath: str
    detail: str | None = None


class SourceLineageRecovery(StrictResponseModel):
    """One ordered recovery derived from task identity, not model-carried Git state."""

    tool: Literal["worktree_sync"] = "worktree_sync"
    contractPath: str
    args: dict[str, object]


class SourceLineageProjection(StrictResponseModel):
    """Transitive super -> master -> leaf lineage projected into status and refusals."""

    state: SourceLineageState
    summary: str
    edges: list[SourceLineageEdge]
    recoveries: list[SourceLineageRecovery]


class SyncOperationProjection(StrictResponseModel):
    """Stable read-only view of the enclosure-root sync journal."""

    state: SyncOperationState
    phase: SyncPhase | Literal["journal-read", "quarantined"]
    contractPath: str
    journalContractPath: str | None = None
    identityMismatch: bool = False
    side: SyncSide | None = None
    conflictFiles: tuple[str, ...] = ()
    summary: str
    nextArgs: dict[str, object] | None = None
    cancelArgs: dict[str, object] | None = None
    evidencePath: str | None = None


class SyncResolutionProjection(StrictResponseModel):
    """What the agent must settle, and whether a parked candidate is part of it.

    ``wipRestore`` marks a resolution that is re-applying the work-in-progress the sync
    parked rather than a plain merge conflict. ``sync_transaction_results`` has emitted it
    since it introduced the parked-WIP path; this field is what makes that path able to
    describe itself. Without it the model refused its own projection with
    ``extra_forbidden``, so a resolution that needed agent action surfaced as a
    serialization error instead of the guidance the agent needed.
    """

    side: SyncSide
    owner: Literal["agent"] = "agent"
    worktree: str | None = None
    files: list[str] = Field(default_factory=list)
    wipRestore: bool = False


class AtomicSeriesActivationFact(StrictResponseModel):
    """Read-only per-contract activation evidence carried by status/refusals."""

    address: str | None = Field(default=None, max_length=4096)
    contractFingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    state: AtomicSeriesObservedState
    record: AtomicSeriesActivationRecord | None = None
    errorType: str | None = Field(default=None, max_length=256)
    detail: str | None = Field(default=None, max_length=8192)


class AtomicSeriesActivationRelease(StrictResponseModel):
    """What one terminal series operation did with the exact activation selection.

    ``with_terminal_atomic_series_release`` writes this on the SUCCESS path of a series closeout,
    integration, cleanup, abandonment or finalize, so it is part of those responses' contracts
    rather than an unchecked extra: it was declared nowhere in ``mcp/src`` for as long as it was
    written, and the strict response model that must carry it therefore rejected the payload of a
    *successful* terminal operation (D-47). The five states are the writer's own closed vocabulary,
    so a new one has to be added in both places rather than only where it is emitted.
    """

    state: Literal[
        "vacant",
        "already-vacant",
        "different-selection-preserved",
        "unreadable-preserved",
        "release-failed",
    ]
    errorType: str | None = Field(default=None, max_length=256)
    detail: str | None = Field(default=None, max_length=8192)


class AtomicSeriesAdmissionActivation(StrictResponseModel):
    """Activation snapshot nested in an admission refusal."""

    path: str = Field(min_length=1, max_length=4096)
    observedState: AtomicSeriesObservedState
    recordPresent: bool
    contractFingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    revision: int | None = Field(default=None, ge=1)
    selectedAt: str | None = Field(default=None, max_length=128)
    selectedMaster: TaskDocumentRef | None = None
    selectedContractPath: str | None = Field(default=None, max_length=4096)
    errorType: str | None = Field(default=None, max_length=256)
    detail: str | None = Field(default=None, max_length=8192)


class AtomicSeriesAdmissionRequested(StrictResponseModel):
    master: TaskDocumentRef | None = None
    contractPath: str | None = Field(default=None, max_length=4096)


class AtomicSeriesAdmissionStatusAction(StrictResponseModel):
    tool: Literal["worktree_status"] = "worktree_status"
    args: dict[str, object]


class AtomicSeriesAdmission(StrictResponseModel):
    """Bounded explanation of why an activation boundary admitted or refused work."""

    operation: str = Field(min_length=1, max_length=256)
    requested: AtomicSeriesAdmissionRequested
    contractFingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    activation: AtomicSeriesAdmissionActivation | None = None
    retryPrecondition: str = Field(min_length=1, max_length=8192)
    statusAction: AtomicSeriesAdmissionStatusAction | None = None
    status: str = Field(min_length=1, max_length=256)
    detail: str = Field(min_length=1, max_length=8192)
    expected: dict[str, object] | None = None
    observed: dict[str, object] | None = None


# Every vocabulary below is imported from whoever produces it, never retyped here. Retyped
# is what these were, and the copies had drifted apart in six places at once: `chat-task`
# (the kind `worktree_start`'s own docstring advertises, on 8 contracts), `reopened`,
# `carryover-pending`, `abandoned`, `request_carryover_decision` and `memory_carryover_apply`
# were all writable and none validated, which made this model reject 165 of the 213 series
# contracts on disk with a ValidationError no handler on the tool path catches.

# Produced entirely inside `application.worktree_status`, which constructs this model
# directly, so the projection there is already the single writer the checker can see.
WorktreeState = Literal["inactive", "active", "missingContract", "invalidContract"]


class WorktreeSummary(StrictResponseModel):
    state: WorktreeState
    contractPath: str | None = None
    enclosurePath: str | None = None
    taskId: str | None = None
    taskName: str | None = None
    leafId: str | None = None
    kind: str | None = None
    workflowKind: WorkflowKind | None = None
    memoryMode: MemoryMode | None = None
    worktreeGroup: str | None = None
    codeWorktree: str | None = None
    codeWorktreeExists: bool | None = None
    codeWorktreeDirty: bool | None = None
    memoryWorktree: str | None = None
    memoryWorktreeExists: bool | None = None
    memoryWorktreeDirty: bool | None = None
    ledgerPath: str | None = None
    humanReviewStatus: HumanReviewStatus | None = None
    approvedForCommit: bool | None = None
    closeoutStatus: LifecycleStatus | None = None
    integrationStatus: IntegrationStatus | None = None
    cleanup: CleanupStatus | None = None
    phase: WorktreePhase | None = None
    nextOperation: NextOperation | None = None
    nextTool: NextTool | None = None
    nextArgs: dict[str, Any] | None = None
    # Absent means the next call needs nothing beyond `nextArgs` -- the same thing the empty
    # list used to mean. `next_guidance` writes this key only when there is a required
    # argument, and the projection reports what the producer said rather than filling in a
    # value for it (`application.worktree_status._summary_from_status_payload` states the
    # measurement).
    nextRequiredArgs: list[str] | None = None
    # Present only when the contract file carried a cell outside its declared vocabulary, as
    # "<field>=<raw token> read as <fallback>". The `state` is still `active` and every other
    # field on this summary was computed from the substituted values -- this is the notice
    # that they were substituted, and the file heals the next time a lifecycle tool writes it.
    unknownContractCells: list[str] | None = None
    error: str | None = None
    errorEvidence: dict[str, object] | None = None
    status: str | None = None
    summary: str | None = Field(default=None, max_length=8192)
    detail: str | None = Field(default=None, max_length=8192)
    expected: dict[str, object] | None = None
    observed: dict[str, object] | None = None
    nextAction: Literal["developer-decision"] | None = None
    developerDecisionRequired: bool | None = None
    decisionSurface: str | None = Field(default=None, max_length=8192)
    lifecycleOperation: LifecycleOperationProjection | None = None
    sourceLineage: SourceLineageProjection | None = None
    syncOperation: SyncOperationProjection | None = None
    atomicSeriesActivation: AtomicSeriesActivationFact | None = None
    admission: AtomicSeriesAdmission | None = None
    retryPrecondition: str | None = Field(default=None, max_length=8192)
    statusAction: AtomicSeriesAdmissionStatusAction | None = None


class WorktreeCommandResponse(FlexibleToolResponse):
    repoId: str | None = None
    state: str | None = None
    dryRun: bool | None = None
    contractPath: str | None = None
    enclosurePath: str | None = None
    taskId: str | None = None
    taskName: str | None = None
    leafId: str | None = None
    kind: str | None = None
    worktreeName: str | None = None
    # The lifecycle this enclosure anchors (design §1.1): worktree_start promotes
    # it, worktree_attach resumes it. Emitted snake_case (lifecycle_id) like its
    # siblings; declared here for wire discoverability.
    lifecycleId: str | None = None
    # Background provider setup state (GitHub #53): worktree_start returns
    # 'starting' with a progressFile; worktree_status then projects the live
    # progress as running / stale (dead heartbeat) / ok /
    # ready-with-failed-phases / failed, with currentPhase and seedFallback.
    providers: dict[str, Any] | None = None
    source_lineage: SourceLineageProjection | None = None
    status: str | None = None
    detail: str | None = Field(default=None, max_length=8192)
    invalidFields: list[CloseoutInvalidField] | None = None
    resolvedPlan: ResolvedCloseoutPlan | None = None
    correctedCall: CloseoutCorrectedCall | None = None
    code_quality_gate: QualityGateResult | None = None
    quality_gate: QualityGateResult | None = None
    atomicSeriesActivation: AtomicSeriesActivationFact | None = None
    admission: AtomicSeriesAdmission | None = None
    retryPrecondition: str | None = Field(default=None, max_length=8192)
    statusAction: AtomicSeriesAdmissionStatusAction | None = None

    # The next-move triple, declared here so the worktree surface's guidance is part of
    # its own contract instead of an unchecked extra. `WorktreeStatusResponse` inherits
    # these; `WorktreeSyncResponse` and `WorktreeOperationControlResponse` narrow
    # `nextTool` further, exactly as they did before.
    nextAction: str | None = None
    nextTool: str | None = None
    nextArgs: dict[str, Any] | None = None

    # The worktree surface's rule: a next move names a *registered public* tool, because
    # these values are advertised guidance an agent is meant to act on and the roster is
    # the already-enforced authority for what the agent can actually call. The rule is
    # deliberately per surface, not global: the `task_doc` surface may name a non-public
    # tool (see the boundary note below), so this is not an inconsistency to flatten.
    #
    # Complete producer survey behind the invariant (union of 20 values, each traced to
    # its producer): NextTool and RecoveryTool literals (models/worktree.py,
    # worktrees/modules/guidance.py); SourceLineageRecovery.tool; TerminalCleanupOperation;
    # route_review.inspection_tool; the narrowed `nextTool` on WorktreeSyncResponse and
    # WorktreeOperationControlResponse; the `legal_operation_controls` row `tool` values
    # (lifecycle_operation_control_projection.py); the terminal_enclosure_archive refusals;
    # the task_unstarted_evidence `RecoveryRoute` tools; and the remaining direct literals
    # in worktrees/modules/{integrate,start}.py, worktrees/task_leaf_binding.py,
    # application/worktree_tools.py, application/next_step.py and models/base.py.
    # Every one is in PUBLIC_TOOLS except `session_retire`, which cannot reach this field.
    #
    # BOUNDARY -- `session_retire` is a registered but deliberately NON-PUBLIC tool
    # (models/tools/tool_registry.py registers it with SessionRetireResponse; it is absent
    # from mcp/tools/base.PUBLIC_TOOLS by design). It is reachable only as the `task_doc`
    # payload's top-level `nextTool` and inside its nested `discardEvidence`, and
    # `TaskDocResponse` is not a `WorktreeCommandResponse`, so this invariant does not
    # apply to it. Do not "fix" that by widening PUBLIC_TOOLS to cover a non-public tool.
    @field_validator("nextTool")
    @classmethod
    def _require_registered_public_next_tool(cls, value: str | None) -> str | None:
        """Refuse a next move that names a tool the public roster does not advertise."""

        if value is None:
            return value
        if value not in PUBLIC_TOOLS:
            raise ValueError(
                f"nextTool must name a registered public tool; {value!r} is not in PUBLIC_TOOLS"
            )
        return value


class WorktreeStartResponse(WorktreeCommandResponse):
    operation: Literal["worktree_start"] = "worktree_start"


class WorktreeAttachResponse(WorktreeCommandResponse):
    operation: Literal["worktree_attach"] = "worktree_attach"


class WorktreeStatusResponse(WorktreeCommandResponse):
    operation: Literal["worktree_status"] = "worktree_status"
    lifecycleOperations: list[LifecycleOperationProjection] = Field(default_factory=list)
    syncOperation: SyncOperationProjection | None = None


class WorktreeEnclosureAdoptResponse(WorktreeCommandResponse):
    operation: Literal["worktree_enclosure_adopt"] = "worktree_enclosure_adopt"
    publicationRequestId: str | None = None
    locatorPath: str | None = None
    manifestPath: str | None = None
    contractSha256: str | None = None
    manifestSha256: str | None = None
    artifacts: list[dict[str, object]] = Field(default_factory=list)
    removalCondition: str | None = None


class WorktreeStatusWaitResponse(WorktreeCommandResponse):
    """Read-only bounded wait on lifecycle meaningful-state changes (CCR-R15).

    Addressed by canonical contract, operation kind, expected public generation,
    and an opaque typed after_revision cursor from a prior snapshot.  On
    change it returns the compact R18-coherent status plus the next cursor; on
    timeout it returns the unchanged snapshot and cursor without claiming
    failure.  Never carries an operation key, PID, or worker/queue/gate
    authority.
    """

    operation: Literal["worktree_status_wait"] = "worktree_status_wait"
    outcome: LifecycleWaitOutcome
    operationKind: LifecycleOperationKind | None = None
    successorGeneration: int | None = None
    meaningfulRevision: int | None = None
    timeoutSeconds: float | None = None
    elapsedSeconds: float | None = None
    lifecycleOperation: LifecycleOperationProjection | None = None
    nextArgs: dict[str, object] | None = None


class WorktreeSyncResponse(WorktreeCommandResponse):
    operation: Literal["worktree_sync"] = "worktree_sync"
    phase: SyncPhase | Literal["quarantined"] | None = None
    resolution: SyncResolutionProjection | None = None
    resolutionOwner: Literal["agent"] | None = None
    nextOperation: str | None = None
    nextTool: Literal["worktree_sync"] | None = None
    nextArgs: dict[str, object] | None = None
    cancelArgs: dict[str, object] | None = None
    evidencePath: str | None = None
    invalidField: Literal["memory_sync_choice", "resolution_action"] | None = None
    manualRepair: dict[str, object] | None = None


class _WorktreeCloseoutResponse(WorktreeCommandResponse):
    pairIdentity: MemoryCandidatePairIdentity | None = None
    pairStatus: str | None = Field(default=None, max_length=256)
    pairField: str | None = Field(default=None, max_length=256)
    expected: dict[str, Any] | None = Field(default=None, max_length=32)
    observed: dict[str, Any] | None = Field(default=None, max_length=32)
    nextAction: str | None = Field(default=None, max_length=8192)
    nextArgs: dict[str, Any] | None = Field(default=None, max_length=32)


class WorktreeCloseoutPreviewResponse(_WorktreeCloseoutResponse):
    operation: Literal["worktree_closeout_preview"] = "worktree_closeout_preview"


class WorktreeCloseoutApplyResponse(_WorktreeCloseoutResponse):
    operation: Literal["worktree_closeout_apply"] = "worktree_closeout_apply"
    lifecycleOperation: LifecycleOperationProjection | None = None


class WorktreeIntegrateResponse(WorktreeCommandResponse):
    operation: Literal["worktree_integrate"] = "worktree_integrate"
    lifecycleOperation: LifecycleOperationProjection | None = None
    # Declared even though the worktree envelope is intentionally flexible: these are stable
    # completion-cleanup products, not incidental worktree-module details.
    autoClosedSeats: list[str] = Field(default_factory=list)
    autoCloseDeferredSeats: list[str] = Field(default_factory=list)
    autoCloseFailedSeats: list[str] = Field(default_factory=list)
    autoLandedSeats: list[str] = Field(default_factory=list)


class WorktreeCheckpointLandingResponse(WorktreeCommandResponse):
    operation: Literal["worktree_checkpoint_landing"] = "worktree_checkpoint_landing"
    integrationStrategy: str = ""
    integratedCodeCommit: str = ""
    integratedMemoryContentCommit: str = ""


class WorktreePauseResponse(WorktreeCommandResponse):
    """The stop-only pause: it releases the master's selection and publishes nothing.

    ``paused`` is the whole state this response claims, and it is claimed only by the route
    that releases the selection. A publication that moved refs has no business setting it --
    the two operations answer different questions and only one of them stops anything.
    """

    operation: Literal["worktree_pause"] = "worktree_pause"
    paused: bool = False


class WorktreeRecordLandingResponse(WorktreeCommandResponse):
    operation: Literal["worktree_record_landing"] = "worktree_record_landing"
    integrationStrategy: str = ""
    landedCodeCommit: str = ""
    landingTargets: list[str] = Field(default_factory=list)


class WorktreeOperationControlResponse(WorktreeCommandResponse):
    operation: Literal["worktree_operation_control"] = "worktree_operation_control"
    lifecycleOperation: LifecycleOperationProjection | None = None
    expected: dict[str, object] = Field(default_factory=dict)
    observed: dict[str, object] = Field(default_factory=dict)
    nextAction: str = ""
    nextTool: (
        Literal["worktree_operation_control", "worktree_integrate", "direct_landing"] | None
    ) = None
    nextArgs: dict[str, object] | None = None
    developerDecisionRequired: bool = False
    decisionSurface: str | None = None


class WorktreeLegacyOperationResponse(WorktreeCommandResponse):
    operation: Literal["worktree_legacy_operation"] = "worktree_legacy_operation"
    lifecycleOperation: LifecycleOperationProjection | None = None
    operationKind: str | None = None
    legacyDigest: str | None = None
    migratable: bool | None = None
    migrationReason: str | None = None
    archivable: bool | None = None
    archiveReason: str | None = None
    archivePath: str | None = None
    terminalEvidence: dict[str, object] | None = None
    removalCondition: str | None = None
    removalGuard: dict[str, object] | None = None
    expected: dict[str, object] = Field(default_factory=dict)
    observed: dict[str, object] = Field(default_factory=dict)
    nextAction: str = ""
    nextTool: str | None = None
    nextArgs: dict[str, object] | None = None
    developerDecisionRequired: bool = False
    decisionSurface: str | None = None


class WorktreeCleanupResponse(WorktreeCommandResponse):
    operation: Literal["worktree_cleanup"] = "worktree_cleanup"


class WorktreeAbandonResponse(WorktreeCommandResponse):
    operation: Literal["worktree_abandon"] = "worktree_abandon"
