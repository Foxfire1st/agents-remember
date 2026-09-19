"""Response model for the ``lifecycle_finalize_task`` terminal operation."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from agents_remember.models.base import ToolResponse
from agents_remember.models.closeout.projection import TaskDocProjectionEffect
from agents_remember.models.task_document import CompletionBlocker
from agents_remember.models.worktree import (
    AtomicSeriesActivationFact,
    AtomicSeriesActivationRelease,
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
    # The atomic-series activation facts the SUCCESS path of a real series finalize carries:
    # ``worktrees/modules/finalize.py`` merges them out of
    # ``with_terminal_atomic_series_release``. Neither key was declared here for as long as both
    # were written, and because this model inherits ``extra="forbid"`` and ``tool_response.py``
    # validates with no ``except``, every atomic-series promotion returned a ValidationError AFTER
    # branch retirement, task updates and enclosure cleanup had committed -- a successful master
    # promotion reported to its caller as a failed call (D-47). Declared rather than relaxed: the
    # two facts are part of this response's contract, and a flexible envelope would have hidden the
    # next drift instead of this one. The projection is already declared on ``WorktreeSummary``
    # and ``WorktreeCommandResponse`` (``models/worktree.py``, D53), which every worktree tool
    # response inherits; this model was the only strict consumer that did not declare it, which is
    # why the transaction completed and the caller got a validation error instead of the payload.
    atomicSeriesActivation: AtomicSeriesActivationFact | None = None
    atomicSeriesActivationRelease: AtomicSeriesActivationRelease | None = None
    # Completion-seat cleanup is additive to finalization truth. Default-on auto-close reports the
    # exact retired, missing-report, and per-seat-failure sets; the explicit settings opt-out uses
    # the historical landed/archive field instead. All are empty on a dry run or disabled edge.
    autoClosedSeats: list[str] = Field(default_factory=list)
    autoCloseDeferredSeats: list[str] = Field(default_factory=list)
    autoCloseFailedSeats: list[str] = Field(default_factory=list)
    autoLandedSeats: list[str] = Field(default_factory=list)
