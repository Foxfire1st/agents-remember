"""Response model for the ``lifecycle_finalize_task`` terminal operation."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from agents_remember.models.base import ToolResponse
from agents_remember.tasks import CompletionBlocker


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
    # Session ids auto-landed at this master->super finalize edge (config-gated, default ON). Empty
    # when the gate is off, nothing matched, or this call was a dry run.
    autoLandedSeats: list[str] = Field(default_factory=list)
