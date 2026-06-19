"""Response model for the ``task_doc`` authoring tool.

An AR-owned, operation-bearing response (a strict ``ToolResponse``): it echoes
the document's identity and progress after the operation. The task document
itself (``tasks.TaskDocument``) is the persisted contract and is deliberately
**not** returned here.
"""

from __future__ import annotations

from agents_remember.models.base import ToolResponse


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
