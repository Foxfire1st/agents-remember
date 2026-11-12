"""The ``task_reopen`` operation: reset a completed leaf to planning (L11).

Task-domain sibling of ``task_doc`` (see ``task_doc_tools``); extracted so each tool's
application logic stays a focused module. ``task_doc_tools`` re-exports
``task_reopen_tool`` unchanged.
"""

from __future__ import annotations

from typing import Any

from agents_remember.kernel.authority import require_within_coordination
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.worktrees.reopen import reopen_task


def task_reopen_tool(
    config: McpRuntimeConfig,
    *,
    contract_path: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Reopen a completed leaf task under its exact same leaf id (L11).

    Task-domain sibling of ``task_doc``: it resets the leaf's enclosure contract and
    task document back to planning; recreating the worktrees stays ``worktree_start``'s
    job. The response keeps the worktree-command shape (contract state fields), so it
    validates against a ``WorktreeCommandResponse`` subclass in the registry.
    """
    confined_contract_path = require_within_coordination(config, contract_path, "contract_path")
    result = reopen_task(confined_contract_path, dry_run=dry_run)
    return {**result.payload, "ok": result.returncode == 0, "operation": "task_reopen"}
