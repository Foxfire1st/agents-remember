"""Response model for the ``task_doc`` authoring tool.

An AR-owned, operation-bearing response (a strict ``ToolResponse``): it echoes
the document's identity and progress after the operation. The task document
itself (``tasks.TaskDocument``) is the persisted contract and is deliberately
**not** returned here.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from agents_remember.models.base import ToolResponse
from agents_remember.models.worktree import WorktreeCommandResponse


class TaskDocMasterSync(BaseModel):
    """Optional master-row sync result for a leaf task_doc write."""

    model_config = ConfigDict(extra="forbid")

    status: str
    masterDocPath: str
    renderedPath: str
    subtaskNumber: str | None = None
    rendered: str | None = None
    diff: str | None = None
    wouldLose: bool = False


class TaskDocResponse(ToolResponse):
    """``task_doc``: the document's identity, status, and progress after the op."""

    taskId: str
    slug: str
    kind: str
    status: str
    lifecycleId: str | None = None
    docPath: str
    renderedPath: str
    stepsDone: int = 0
    stepsTotal: int = 0
    # dry-run / preview (R5): set only when dry_run=True; a real op leaves these at their defaults.
    dryRun: bool = False
    rendered: str | None = None
    diff: str | None = None
    wouldLose: bool = False
    masterSync: TaskDocMasterSync | None = None
    # remove_subtask outcome (260703-L18 finding 1, closes friction F-N): the op removes the master
    # row and (unless keep_file) deletes the leaf doc, then echoes what it did. Without these fields
    # the extra=forbid envelope REJECTED the real payload, surfacing a tool error after a destructive
    # success -- a caller who believes the error could retry the (already-done) removal. Present only
    # on remove_subtask (real op: removedSubtask + deletedFiles; dry-run: removedSubtask +
    # wouldDeleteFiles); every other operation leaves them None (excluded by exclude_none).
    removedSubtask: str | None = None
    deletedFiles: list[str] | None = None
    wouldDeleteFiles: list[str] | None = None


class TaskReopenResponse(WorktreeCommandResponse):
    """task_reopen resets a leaf's contract + doc; the payload keeps the contract-state shape."""

    operation: Literal["task_reopen"] = "task_reopen"
