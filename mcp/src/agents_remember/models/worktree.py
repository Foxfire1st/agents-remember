"""Models for worktree state included in context packets."""

from __future__ import annotations

from typing import Any, Literal

from agents_remember.models.base import FlexibleToolResponse, StrictResponseModel

WorktreeState = Literal["inactive", "active", "missingContract", "invalidContract"]
WorkflowKind = Literal["chat", "light", "light-task"]
MemoryMode = Literal["internal", "external", "disabled"]
HumanReviewStatus = Literal["pending-review", "approved"]
LifecycleStatus = Literal["not-started", "completed"]
IntegrationStatus = Literal["not-started", "completed", "blocked"]
CleanupStatus = Literal["pending", "completed", "abandoned"]
WorktreePhase = Literal[
    "cleanup-completed",
    "integration-blocked",
    "cleanup-pending",
    "commit-approval-pending",
    "integration-pending",
    "closeout-pending",
    "worktree-started",
]
NextOperation = Literal[
    "done",
    "developer_decision",
    "request_cleanup_decision",
    "request_commit_approval",
    "request_integration_decision",
    "closeout",
    "continue_work",
]
NextTool = Literal[
    "worktree_integrate",
    "worktree_cleanup",
    "worktree_closeout_preview",
    "worktree_closeout_apply",
    "worktree_status",
]


class WorktreeSummary(StrictResponseModel):
    state: WorktreeState
    contractPath: str | None = None
    taskId: str | None = None
    taskName: str | None = None
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
    nextRequiredArgs: list[str] | None = None
    error: str | None = None


class WorktreeCommandResponse(FlexibleToolResponse):
    repoId: str | None = None
    state: str | None = None
    dryRun: bool | None = None
    contractPath: str | None = None
    taskId: str | None = None
    taskName: str | None = None
    worktreeName: str | None = None
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


class DirectCloseoutPreviewResponse(WorktreeCommandResponse):
    operation: Literal["direct_closeout_preview"] = "direct_closeout_preview"


class DirectCloseoutApplyResponse(WorktreeCommandResponse):
    operation: Literal["direct_closeout_apply"] = "direct_closeout_apply"


class WorktreeIntegrateResponse(WorktreeCommandResponse):
    operation: Literal["worktree_integrate"] = "worktree_integrate"


class WorktreeCleanupResponse(WorktreeCommandResponse):
    operation: Literal["worktree_cleanup"] = "worktree_cleanup"


class WorktreeAbandonResponse(WorktreeCommandResponse):
    operation: Literal["worktree_abandon"] = "worktree_abandon"
