"""Decide whether a closeout reopens an already completed integration."""

from __future__ import annotations

from agents_remember.worktrees.modules.git import (
    branch_commit,
    head_commit,
    is_ancestor,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract


def preview_integration_reopen(
    contract: WorktreeContract,
    *,
    code_dirty: bool,
    memory_would_commit: bool,
) -> dict[str, object]:
    """Project whether prospective closeout output would need reintegration."""

    if contract.integration_status != "completed":
        return {"would_reopen": False, "reason": "integration is not completed"}
    code_head = (
        branch_commit(contract.code_repo_path, contract.code_work_branch)
        if contract.kind == "series"
        else head_commit(contract.code_worktree)
    )
    code_unlanded = code_dirty or (
        code_head != contract.code_commit
        and _commit_missing_from_source(
            contract.code_repo_path,
            code_head,
            contract.code_source_branch,
        )
    )
    would_reopen = code_unlanded or memory_would_commit
    return {
        "would_reopen": would_reopen,
        "code_would_reopen": code_unlanded,
        "memory_would_reopen": memory_would_commit,
        "reason": (
            "completed integration would be reopened after closeout"
            if would_reopen
            else "no new unlanded code or memory content is expected"
        ),
    }


def completed_integration_reopen(
    contract: WorktreeContract,
    *,
    code_commit: str,
    memory_content_commit: str,
    ledger_commit: str,
) -> dict[str, object]:
    """Describe which newly committed legs require plane-owned reintegration."""

    if contract.integration_status != "completed":
        return {"reopened": False, "reason": "integration is not completed"}
    code_unlanded = _completed_code_is_unlanded(contract, code_commit)
    memory_unlanded = _completed_memory_is_unlanded(
        contract,
        memory_content_commit=memory_content_commit,
        ledger_commit=ledger_commit,
    )
    reopened = code_unlanded or memory_unlanded
    return {
        "reopened": reopened,
        "code_unlanded": code_unlanded,
        "memory_unlanded": memory_unlanded,
        "previous_code_commit": contract.code_commit,
        "previous_memory_content_commit": contract.memory_content_commit,
        "previous_ledger_commit": contract.ledger_commit,
        "reason": (
            "new closeout commit is not on the recorded source branch"
            if reopened
            else "no new unlanded code or memory content commit"
        ),
    }


def _commit_missing_from_source(repo, commit: str, source_branch: str) -> bool:
    return bool(commit) and not is_ancestor(repo, commit, source_branch)


def _completed_code_is_unlanded(
    contract: WorktreeContract,
    code_commit: str,
) -> bool:
    return code_commit != contract.code_commit and _commit_missing_from_source(
        contract.code_repo_path,
        code_commit,
        contract.code_source_branch,
    )


def _completed_memory_is_unlanded(
    contract: WorktreeContract,
    *,
    memory_content_commit: str,
    ledger_commit: str,
) -> bool:
    content_changed = (
        contract.memory_mode == "external"
        and bool(memory_content_commit)
        and memory_content_commit != contract.memory_content_commit
    )
    return bool(
        content_changed
        and contract.memory_repo_path is not None
        and _commit_missing_from_source(
            contract.memory_repo_path,
            ledger_commit,
            contract.memory_source_branch,
        )
    )
