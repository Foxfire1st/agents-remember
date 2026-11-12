"""Best-effort host archival at the admitted leaf retirement boundary."""

from __future__ import annotations

from agents_remember.worktrees.services import WorktreeServicesUnboundError, worktree_services
from agents_remember.worktrees.worktree_contract import WorktreeContract


def archive_terminal_agents(contract: WorktreeContract, *, dry_run: bool) -> dict[str, object]:
    if dry_run or contract.kind != "leaf":
        return {}
    try:
        port = worktree_services().leaf_agent_archive
    except WorktreeServicesUnboundError:
        port = None
    if port is None:
        return {"state": "not-bound", "reason": "No leaf agent archive service is configured."}
    try:
        return port.archive(contract)
    except Exception as error:
        # Host and receipt failures cannot refuse an already admitted retirement. The recurring
        # observer also derives debt from terminal contract truth if this attempt cannot write it.
        return {"state": "failed", "reason": f"{type(error).__name__}: {error}"}
