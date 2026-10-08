"""The contract reset that reopening a leaf and reopening a series share.

``worktrees/reopen.py`` (the ``task_reopen`` entry and the leaf half) and
``worktrees/reopen_series.py`` (the series half) both reset a contract to its restartable
tombstone, report the same facts about it, clear the same frozen landing record and refuse a
transition the same way. Those four definitions are here so that the two halves do not import each
other. Their text was moved from ``reopen.py`` unchanged when that module was split to stay under
the file-size rail.
"""

from __future__ import annotations

from dataclasses import replace

from agents_remember.kernel.primitives.observer_paths import LANDING_FINAL_BASENAME

from .worktree_contract import (
    ContractCells,
    WorktreeContract,
    amend_contract,
)


class _ReopenTransitionRefusal(RuntimeError):
    """The locked reopen authority no longer matches its reviewed terminal leaf."""


def _contract_reopen_facts(contract: WorktreeContract) -> dict[str, object]:
    """Pure contract facts for reopen responses.

    This deliberately does not call ``status_payload``: that interactive projection reads
    providers, landing state, Git freshness, and descendant source lineage. A cleaned leaf has
    no branches left for those probes to inspect, and the only actionable fact at this boundary
    is its stable task identity plus the explicit recovery call.
    """
    return {
        "task_id": contract.task_id,
        "task_name": contract.task_name,
        "code_repository_name": contract.repo_name,
        "workflow_kind": contract.workflow_kind,
        "memory_mode": contract.memory_mode,
        "kind": contract.kind,
        "leaf_id": contract.leaf_id,
        "enclosure_path": contract.contract_path.as_posix(),
        "contract_path": contract.contract_path.as_posix(),
        "parent_contract_path": (
            contract.parent_contract_path.as_posix() if contract.parent_contract_path else ""
        ),
        "worktree_group": contract.worktree_group.as_posix(),
        "human_review_status": contract.human_review_status,
        "approved_for_commit": contract.approved_for_commit,
        "closeout_status": contract.closeout_status,
        "integration_status": contract.integration_status,
        "cleanup": contract.cleanup,
        "lifecycle_id": contract.lifecycle_id,
    }


def _reopened_contract(contract: WorktreeContract) -> WorktreeContract:
    """The contract with every review/closeout/integration cell reset (task reopen)."""

    return amend_contract(
        replace(
            contract,
            approved_for_commit=False,
            commit_approval_note="",
            code_commit="",
            memory_content_commit="",
            integration_strategy="",
            integrated_code_commit="",
            integrated_memory_content_commit="",
            lifecycle_id="",
            memory_state="",
        ),
        # The vocabulary cells go through the typed record, which is what puts them in front
        # of pyright: `dataclasses.replace` is `**changes: Any` in typeshed, so the `reopened`
        # marker below crossed the boundary unchecked for as long as it was spelled as a
        # `replace` keyword -- and it was one of the six values the packet then rejected.
        ContractCells(
            human_review_status="pending-review",
            closeout_status="not-started",
            integration_status="not-started",
            cleanup="reopened",
        ),
    )


def _clear_frozen_landing(contract: WorktreeContract, *, dry_run: bool) -> str:
    """Delete the frozen landing-final.json so the reopened arc starts clean.

    The landing freeze persists a finished leaf's landing facts beside its
    contract and pulls it out of the landing sweep permanently. A reopen makes those facts a
    lie: the leaf re-enters the sweep, but until this file is gone its second finish cannot
    re-freeze (a stale-but-loadable file would keep the leaf out of the sweep and serve the
    first-finish facts forever). Removing it here is what lets the re-finished leaf freeze with
    fresh facts.
    """
    final_path = contract.contract_path.parent / LANDING_FINAL_BASENAME
    if not final_path.exists():
        return "absent"
    if dry_run:
        return "would-delete"
    final_path.unlink()
    return "deleted"
