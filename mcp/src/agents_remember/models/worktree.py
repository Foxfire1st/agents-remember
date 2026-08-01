"""Models for worktree state included in context packets."""

from __future__ import annotations

from typing import Any, Literal

from agents_remember.models.base import FlexibleToolResponse, StrictResponseModel

# Every vocabulary below is imported from whoever produces it, never retyped here. Retyped
# is what these were, and the copies had drifted apart in six places at once: `chat-task`
# (the kind `worktree_start`'s own docstring advertises, on 8 contracts), `reopened`,
# `carryover-pending`, `abandoned`, `request_carryover_decision` and `memory_carryover_apply`
# were all writable and none validated, which made this model reject 165 of the 213 series
# contracts on disk with a ValidationError no handler on the tool path catches.
from agents_remember.worktrees.modules.guidance import (
    NextOperation,
    NextTool,
    WorktreePhase,
)
from agents_remember.worktrees.worktree_contract import (
    CleanupStatus,
    HumanReviewStatus,
    IntegrationStatus,
    MemoryMode,
    WorkflowKind,
)
from agents_remember.worktrees.worktree_contract import (
    CloseoutStatus as LifecycleStatus,  # the published wire name for the closeout status
)

# Produced entirely inside `worktrees.status`, which constructs this model directly, so the
# projection there is already the single writer the checker can see.
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
    # value for it (`worktrees.status._summary_from_status_payload` states the measurement).
    nextRequiredArgs: list[str] | None = None
    # Present only when the contract file carried a cell outside its declared vocabulary, as
    # "<field>=<raw token> read as <fallback>". The `state` is still `active` and every other
    # field on this summary was computed from the substituted values -- this is the notice
    # that they were substituted, and the file heals the next time a lifecycle tool writes it.
    unknownContractCells: list[str] | None = None
    error: str | None = None


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


class WorktreeStartResponse(WorktreeCommandResponse):
    operation: Literal["worktree_start"] = "worktree_start"


class WorktreeAttachResponse(WorktreeCommandResponse):
    operation: Literal["worktree_attach"] = "worktree_attach"


class WorktreeStatusResponse(WorktreeCommandResponse):
    operation: Literal["worktree_status"] = "worktree_status"


class WorktreeSyncResponse(WorktreeCommandResponse):
    operation: Literal["worktree_sync"] = "worktree_sync"


class WorktreeCloseoutPreviewResponse(WorktreeCommandResponse):
    operation: Literal["worktree_closeout_preview"] = "worktree_closeout_preview"


class WorktreeCloseoutApplyResponse(WorktreeCommandResponse):
    operation: Literal["worktree_closeout_apply"] = "worktree_closeout_apply"


class WorktreeIntegrateResponse(WorktreeCommandResponse):
    operation: Literal["worktree_integrate"] = "worktree_integrate"


class WorktreeCleanupResponse(WorktreeCommandResponse):
    operation: Literal["worktree_cleanup"] = "worktree_cleanup"


class WorktreeAbandonResponse(WorktreeCommandResponse):
    operation: Literal["worktree_abandon"] = "worktree_abandon"
