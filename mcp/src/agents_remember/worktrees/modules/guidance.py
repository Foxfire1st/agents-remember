from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from agents_remember.worktrees.modules.git import (
    contract_has_worktree_changes,
    worktree_dirty,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract


def next_guidance(
    operation: str,
    *,
    tool: str | None = None,
    args: dict[str, object] | None = None,
    required_args: list[str] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {"nextOperation": operation}
    if tool:
        payload["nextTool"] = tool
    if args is not None:
        payload["nextArgs"] = args
    if required_args:
        payload["nextRequiredArgs"] = required_args
    return payload


def contract_next_args(contract: WorktreeContract, **extra: object) -> dict[str, object]:
    return {"contract_path": contract.contract_path.as_posix(), **extra}


def contract_payload(contract: WorktreeContract) -> dict[str, object]:
    data = asdict(contract)
    for key, value in list(data.items()):
        if isinstance(value, Path):
            data[key] = value.as_posix()
        elif value is None:
            data[key] = ""
    return data


def lifecycle_guidance(contract: WorktreeContract) -> dict[str, object]:
    if contract.cleanup == "completed":
        return {
            "phase": "cleanup-completed",
            "summary": "Worktree task lifecycle is complete and cleanup has already run.",
            **next_guidance("done"),
        }
    if contract.integration_status == "blocked":
        return {
            "phase": "integration-blocked",
            "summary": "Integration is blocked; review the conflict or non-fast-forward state with the developer before retrying.",
            **next_guidance(
                "developer_decision",
                tool="worktree_integrate",
                args=contract_next_args(contract, strategy="replay", dry_run=True),
            ),
        }
    if contract.integration_status == "completed":
        return {
            "phase": "cleanup-pending",
            "summary": "Integration completed; cleanup is still pending.",
            **next_guidance(
                "request_cleanup_decision",
                tool="worktree_cleanup",
                args=contract_next_args(contract, dry_run=True),
            ),
        }
    if contract_has_worktree_changes(contract):
        return {
            "phase": "commit-approval-pending",
            "summary": "Worktree changes are present; prepare a closeout preview and ask for explicit commit approval before creating commits.",
            **next_guidance(
                "request_commit_approval",
                tool="worktree_closeout_preview",
                args=contract_next_args(contract),
                required_args=["code_commit_message"],
            ),
        }
    if contract.closeout_status == "completed":
        return {
            "phase": "integration-pending",
            "summary": "Closeout completed; integrate the task branches back into their source branches.",
            **next_guidance(
                "request_integration_decision",
                tool="worktree_integrate",
                args=contract_next_args(contract, strategy="ff-only", dry_run=True),
            ),
        }
    if contract.approved_for_commit:
        return {
            "phase": "closeout-pending",
            "summary": "Closeout approval is recorded, but closeout has not completed.",
            **next_guidance(
                "closeout",
                tool="worktree_closeout_apply",
                args=contract_next_args(contract),
                required_args=["intent_note", "code_commit_message"],
            ),
        }
    return {
        "phase": "worktree-started",
        "summary": "Worktrees are ready; continue the wrapped workflow and close out after review.",
        **next_guidance(
            "continue_work",
            tool="worktree_status",
            args=contract_next_args(contract),
        ),
    }


def status_payload(contract: WorktreeContract) -> dict[str, object]:
    guidance = lifecycle_guidance(contract)
    payload = {
        "task_id": contract.task_id,
        "task_name": contract.task_name,
        "code_repository_name": contract.repo_name,
        "workflow_kind": contract.workflow_kind,
        "memory_mode": contract.memory_mode,
        "contract_path": contract.contract_path.as_posix(),
        "worktree_group": contract.worktree_group.as_posix(),
        "code_worktree": contract.code_worktree.as_posix(),
        "code_worktree_exists": contract.code_worktree.exists(),
        "code_worktree_dirty": worktree_dirty(contract.code_worktree),
        "memory_worktree": contract.memory_worktree.as_posix() if contract.memory_worktree else "",
        "memory_worktree_exists": contract.memory_worktree.exists()
        if contract.memory_worktree
        else False,
        "memory_worktree_dirty": worktree_dirty(contract.memory_worktree),
        "ledger_path": contract.ledger_path.as_posix() if contract.ledger_path else "",
        "human_review_status": contract.human_review_status,
        "approved_for_commit": contract.approved_for_commit,
        "closeout_status": contract.closeout_status,
        "integration_status": contract.integration_status,
        "cleanup": contract.cleanup,
    }
    payload.update(guidance)
    return payload
