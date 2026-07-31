"""How MCP callers point at one existing task.

Every read-side task tool -- ``worktree_attach``, ``worktree_status``,
``resolve_context`` -- takes the same bundle of identifiers and hands it to the
same resolver. :class:`TaskRef` is that bundle: the repo the task belongs to plus
whichever locator the caller happens to hold.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TaskRef:
    """The repo a task belongs to and the identifiers that locate its contract.

    A caller supplies whichever locator it holds: the task name, the on-disk
    contract path, or the leaf id (optionally with its parent task). Resolution
    order and precedence belong to the worktree resolver, not to this reference --
    it only carries what the caller knows.
    """

    repo_id: str
    task_name: str | None = None
    contract_path: str | None = None
    leaf_id: str | None = None
    parent_task: str | None = None
