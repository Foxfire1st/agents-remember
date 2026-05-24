"""Read-only worktree status projection for context packets."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import git_worktree_manager
from .worktree_contract import ContractError, load_contract


def worktree_status_packet(contract_path: Path | None) -> dict[str, Any]:
    if contract_path is None:
        return {"state": "inactive"}
    resolved = contract_path.resolve()
    if not resolved.exists():
        return {"state": "missingContract", "contractPath": resolved.as_posix()}
    try:
        contract = load_contract(resolved)
    except ContractError as error:
        return {
            "state": "invalidContract",
            "contractPath": resolved.as_posix(),
            "error": str(error),
        }
    return {
        "state": "active",
        **_packet_from_status_payload(git_worktree_manager.status_payload(contract)),
    }


def _packet_from_status_payload(payload: dict[str, object]) -> dict[str, Any]:
    return {
        "taskId": payload["task_id"],
        "taskName": payload["task_name"],
        "workflowKind": payload["workflow_kind"],
        "memoryMode": payload["memory_mode"],
        "contractPath": payload["contract_path"],
        "worktreeGroup": payload["worktree_group"],
        "codeWorktree": payload["code_worktree"],
        "codeWorktreeExists": payload["code_worktree_exists"],
        "codeWorktreeDirty": payload["code_worktree_dirty"],
        "memoryWorktree": payload["memory_worktree"],
        "memoryWorktreeExists": payload["memory_worktree_exists"],
        "memoryWorktreeDirty": payload["memory_worktree_dirty"],
        "ledgerPath": payload["ledger_path"],
        "humanReviewStatus": payload["human_review_status"],
        "approvedForCommit": payload["approved_for_commit"],
        "closeoutStatus": payload["closeout_status"],
        "integrationStatus": payload["integration_status"],
        "cleanup": payload["cleanup"],
        "phase": payload["phase"],
        "nextOperation": payload["nextOperation"],
        "nextTool": payload.get("nextTool", ""),
        "nextArgs": payload.get("nextArgs", {}),
        "nextRequiredArgs": payload.get("nextRequiredArgs", []),
        "rawStatus": payload,
    }
