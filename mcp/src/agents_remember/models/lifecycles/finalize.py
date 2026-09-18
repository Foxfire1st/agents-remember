"""Response model for the ``lifecycle_finalize_task`` terminal operation."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from agents_remember.models.base import ToolResponse
from agents_remember.models.closeout.projection import TaskDocProjectionEffect
from agents_remember.models.task_document import CompletionBlocker
from agents_remember.models.worktree import (
    AtomicSeriesActivationFact,
    AtomicSeriesActivationReleaseFact,
)


class LifecycleFinalizeTaskResponse(ToolResponse):
    """``lifecycle_finalize_task``: terminal task lifecycle reconciliation."""

    operation: Literal["lifecycle_finalize_task"] = "lifecycle_finalize_task"
    taskId: str = ""
    taskName: str = ""
    lifecycleId: str = ""
    state: str
    dryRun: bool = False
    contractPath: str
    enclosurePath: str | None = None
    landedCommit: str | None = None
    targetBranch: str | None = None
    blockers: list[str | CompletionBlocker] = Field(default_factory=list)
    cleanup: dict[str, Any] = Field(default_factory=dict)
    taskUpdates: dict[str, Any] = Field(default_factory=dict)
    projectionEffects: list[TaskDocProjectionEffect] = Field(default_factory=list, max_length=8)
    taskArchive: dict[str, Any] = Field(default_factory=dict)
    summary: str = ""
    # Completion-seat cleanup is additive to finalization truth. Default-on auto-close reports the
    # exact retired, missing-report, and per-seat-failure sets; the explicit settings opt-out uses
    # the historical landed/archive field instead. All are empty on a dry run or disabled edge.
    autoClosedSeats: list[str] = Field(default_factory=list)
    autoCloseDeferredSeats: list[str] = Field(default_factory=list)
    autoCloseFailedSeats: list[str] = Field(default_factory=list)
    autoLandedSeats: list[str] = Field(default_factory=list)
    # The atomic-series terminal release projection (D53). ``_finalized_result`` copies both
    # keys straight out of ``with_terminal_atomic_series_release``'s payload
    # (``worktrees/modules/finalize.py:209-210``), and the ``activation-release-blocked`` arm
    # (``:161-177``) spreads that payload whole, so both keys arrive on either terminal arm.
    # The projection is already declared on ``WorktreeSummary`` and on
    # ``WorktreeCommandResponse`` (``models/worktree.py``), which every worktree tool response
    # inherits; this model was the ONLY strict consumer of it and the only one that did not
    # declare it -- which is why the transaction completed and the caller got a validation
    # error instead of the payload that would have told it so.
    atomicSeriesActivation: AtomicSeriesActivationFact | None = None
    atomicSeriesActivationRelease: AtomicSeriesActivationReleaseFact | None = None
