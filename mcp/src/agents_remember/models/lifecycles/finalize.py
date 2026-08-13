"""Response model for the ``lifecycle_finalize_task`` terminal operation."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from agents_remember.models.base import ToolResponse
from agents_remember.models.task_document import CompletionBlocker


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
    taskArchive: dict[str, Any] = Field(default_factory=dict)
    summary: str = ""
    # Completion-seat cleanup is additive to finalization truth. Default-on auto-close reports the
    # exact retired, missing-report, and per-seat-failure sets; the explicit settings opt-out uses
    # the historical landed/archive field instead. All are empty on a dry run or disabled edge.
    autoClosedSeats: list[str] = Field(default_factory=list)
    autoCloseDeferredSeats: list[str] = Field(default_factory=list)
    autoCloseFailedSeats: list[str] = Field(default_factory=list)
    autoLandedSeats: list[str] = Field(default_factory=list)
