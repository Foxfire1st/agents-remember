"""Readiness and refusal policy for pull-request-landed standalone masters.

A standalone master (a series contract with no commanding sprint) can land on its
target branch through a pull request recorded by ``worktree_record_landing``. That
route records ``integration_strategy="pr"``, ``integration_status="completed"``,
and the landed commits while leaving ``closeout_status`` at ``not-started`` and
``code_commit`` empty.

Keeping the eligibility predicate, finalize/cleanup readiness blockers, and
abandoned-contract guard in one module ensures ``finalize``, ``worktree_cleanup``,
and ``worktree_record_landing`` enforce the same contract boundary.
"""

from __future__ import annotations

from pathlib import Path

from agents_remember.tasks.leaf_doc import find_leaf_doc
from agents_remember.tasks.store import read_task_doc
from agents_remember.worktrees.modules.git import is_ancestor
from agents_remember.worktrees.modules.guidance import carryover_done
from agents_remember.worktrees.worktree_contract import WorktreeContract

# The route label recorded in the contract's ``integration.strategy`` cell when
# the landing arrived via a merged pull request rather than local ref movement.
PR_STRATEGY = "pr"

# Refusal code returned by ``worktree_record_landing`` when either the contract's
# cleanup state or its own task document reads abandoned.
CONTRACT_ABANDONED_CODE = "contract-abandoned"


def is_pr_landed_standalone_candidate(contract: WorktreeContract) -> bool:
    """Whether a contract matches the contract-side shape of a PR-landed series.

    Callers still verify ``sprint_ref is None`` through integration branch
    authority so a sprint-commanded master keeps the closed-out route.
    """
    return (
        contract.kind == "series"
        and contract.integration_strategy == PR_STRATEGY
        and contract.integration_status == "completed"
        and contract.closeout_status != "completed"
    )


def pr_landed_standalone_blockers(
    contract: WorktreeContract,
    *,
    master_status: str,
) -> list[str]:
    """Readiness blockers for a PR-landed standalone master before finalize or cleanup."""
    blockers: list[str] = []
    if master_status == "abandoned" or contract.cleanup == "abandoned":
        blockers.append("master-abandoned")
    if not contract.integrated_code_commit:
        blockers.append("landed-commit-missing")
    elif not is_ancestor(
        contract.code_repo_path,
        contract.integrated_code_commit,
        contract.code_source_branch,
    ):
        blockers.append("not-landed-on-target-branch")
    if (
        contract.memory_mode == "external"
        and not _external_memory_landed(contract)
        and "landed-commit-missing" not in blockers
    ):
        blockers.append("landed-commit-missing")
    carried, _carried_at = carryover_done(contract)
    if not carried:
        blockers.append("memory-carryover-incomplete")
    return blockers


def _external_memory_landed(contract: WorktreeContract) -> bool:
    """Whether an external-memory contract's landed memory commit is reachable on its source branch."""
    memory_commit = contract.integrated_memory_content_commit
    if not memory_commit or contract.memory_repo_path is None or not contract.memory_source_branch:
        return False
    return is_ancestor(contract.memory_repo_path, memory_commit, contract.memory_source_branch)


def abandoned_contract_reason(contract: WorktreeContract) -> str | None:
    """Refusal sentence when recording a landing on an abandoned contract or task document.

    A missing or unreadable task document is not treated as abandoned here so the
    caller's existing checks handle it unchanged.
    """
    if contract.cleanup == "abandoned":
        return (
            "record_landing refused: this contract is abandoned (cleanup is 'abandoned'), "
            "so a pull-request landing cannot be recorded on it; reopen the task with "
            "task_reopen first if its work is taken again."
        )
    status = _contract_task_status(contract)
    if status == "abandoned":
        return (
            "record_landing refused: the task document reads 'abandoned', so a pull-request "
            "landing cannot be recorded on this contract; reopen or restore the task before "
            "recording a landing."
        )
    return None


def _read_task_status(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        return read_task_doc(path).status
    except (OSError, ValueError):
        return None


def _contract_task_status(contract: WorktreeContract) -> str | None:
    """Status of the contract's own task document, or ``None`` when absent or unreadable."""
    if contract.kind == "series":
        return _read_task_status(contract.task_root / "task.json")
    found = find_leaf_doc(contract.task_root, contract.leaf_id)
    if found is not None:
        return found[1].status
    if not contract.leaf_id:
        return _read_task_status(contract.task_artifact.with_suffix(".json"))
    return None
