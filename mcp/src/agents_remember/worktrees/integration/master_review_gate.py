"""Integration-boundary payloads for blocked integration decisions."""

from __future__ import annotations

from agents_remember.worktrees.modules.guidance import status_payload
from agents_remember.worktrees.worktree_contract import (
    ContractCells,
    WorktreeContract,
    amend_contract,
    write_contract,
)


def blocked_integration_payload(
    contract: WorktreeContract,
    state: str,
    reason: str,
    persist: bool = True,
    developer_decision_required: bool = True,
    **extra: object,
) -> dict[str, object]:
    """Persist and project one blocked integration decision."""

    blocked = amend_contract(contract, ContractCells(integration_status="blocked"))
    if persist:
        write_contract(blocked.contract_path, blocked)
    next_step: dict[str, object] = {"summary": reason}
    for key in ("nextOperation", "nextTool", "nextArgs", "nextRequiredArgs"):
        if key in extra:
            next_step[key] = extra[key]
    return {
        "state": state,
        **status_payload(blocked),
        "reason": reason,
        "summary": reason,
        "developerDecisionRequired": developer_decision_required,
        "nextStep": next_step,
        **extra,
    }


__all__ = [
    "blocked_integration_payload",
]
