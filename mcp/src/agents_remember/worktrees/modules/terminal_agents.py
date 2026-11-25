"""Archive the seats of an admitted terminal worktree before reclaiming it."""

from __future__ import annotations

from collections.abc import Callable

from agents_remember.controlplane.held_store_discovery import receipt_holder
from agents_remember.worktrees.services import WorktreeServicesUnboundError, worktree_services
from agents_remember.worktrees.worktree_contract import WorktreeContract


def archive_terminal_agents(
    contract: WorktreeContract,
    *,
    dry_run: bool,
    check_census: Callable[[], None] | None = None,
) -> dict[str, object]:
    try:
        port = worktree_services().leaf_agent_archive
    except WorktreeServicesUnboundError:
        port = None
    if port is None:
        holder = receipt_holder(contract.task_root)
        if holder.exists():
            return {
                "state": "not-bound",
                "reason": f"No archive service can check the launch records in {holder}.",
            }
        return {}
    try:
        if check_census is not None:
            return port.archive(contract, dry_run=dry_run, check_census=check_census)
        return port.archive(contract, dry_run=True) if dry_run else port.archive(contract)
    except (OSError, ValueError, RuntimeError) as error:
        return {"state": "failed", "reason": f"{type(error).__name__}: {error}"}


def terminal_agent_blockers(archive: dict[str, object]) -> list[dict[str, object]]:
    if archive.get("state") in ("not-bound", "failed"):
        return [{"terminal": "agent-archive", "reason": archive["reason"]}]
    result = []
    for kind in ("owed", "leftAlone"):
        rows = archive.get(kind)
        if not isinstance(rows, list):
            continue
        for row in rows:
            agent = row.get("agentId") or row.get("recordPath") or "unknown seat"
            result.append({"agent": agent, "reason": row["reason"]})
    return result
