"""Attach-side resume of a live atomic series, or the refusal that names its real blocker."""

from __future__ import annotations

from agents_remember.worktrees.activation.atomic_series_activation import (
    atomic_series_status_projection,
)
from agents_remember.worktrees.modules.git import branch_exists
from agents_remember.worktrees.modules.guidance import status_payload
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.scheduling_mode import TERMINAL_SERIES_CLEANUP
from agents_remember.worktrees.source_lineage import (
    lineage_block_payload,
    lineage_refusal,
    source_lineage_for_contract,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract


def series_attach_result(contract: WorktreeContract) -> WorktreeCommandResult:
    """Resume a live atomic series, or refuse naming the state that actually blocks it.

    A series has no workbench of its own -- its work runs in child leaves -- so attaching to one
    means proving its integration branch is still live and addressable, not handing back a
    checkout. The refusal this replaces named ``kind == "series"``, which is a property the
    codebase fully supports (``atomic_series_activation`` observes it and defines its terminal
    predicate), so it blamed a non-cause and hid the real one. In practice the unstated cause was
    a branch that had been merged and deleted, and the message sent readers after contract
    staleness and operation generations instead.
    """

    payload = dict(status_payload(contract))
    payload["atomicSeriesActivation"] = atomic_series_status_projection(contract)
    if contract.cleanup in TERMINAL_SERIES_CLEANUP:
        return WorktreeCommandResult(
            2,
            {
                **payload,
                "state": "series-terminal",
                "summary": (
                    "worktree_attach refused: this series is already terminal "
                    f"(cleanup is {contract.cleanup!r}), so no live integration branch remains to "
                    "resume. Reopen the task with task_reopen, or start a successor task."
                ),
            },
        )
    if not branch_exists(contract.code_repo_path, contract.code_work_branch):
        return WorktreeCommandResult(
            2,
            {
                **payload,
                "state": "series-branch-missing",
                "summary": (
                    "worktree_attach refused: the series integration branch "
                    f"{contract.code_work_branch!r} does not exist locally, so there is nothing to "
                    f"resume. Re-cut it from {contract.code_source_branch!r} at the recorded base "
                    "commit, or start a successor task."
                ),
            },
        )
    lineage = source_lineage_for_contract(contract)
    if lineage_refusal(lineage) is not None:
        assert lineage is not None
        return WorktreeCommandResult(
            2,
            {
                **payload,
                **lineage_block_payload(lineage),
                "summary": "Attach refused before this series was resumed: " + lineage.summary,
            },
        )
    return WorktreeCommandResult(0, {"state": "attached", "attached": True, **payload})
