"""The single writer of a contract's terminal integration cell.

Both landing routes converge here. The local route (:mod:`...modules.integrate`) moves the code and
memory refs itself and then records what it moved. The PR route has no local ref movement at all --
``gh pr merge`` already moved them on the remote -- so its entry point records the commit it was
given, after proving it really is reachable from a landing target.

Keeping the cell and its commit triple behind one function is not tidiness. That cell is what
``worktree_cleanup`` requires before it will retire a branch, and what the series abandon guard
refuses to retire past, so two writers would mean two definitions of "landed" -- and the one that
was never called is the one that matters when someone later decides a half-finished master's work
was discarded.
"""

from __future__ import annotations

from dataclasses import replace

from agents_remember.worktrees.worktree_contract import (
    ContractCells,
    WorktreeContract,
    amend_contract,
    write_contract,
)


def record_landed_integration(
    contract: WorktreeContract,
    *,
    strategy: str,
    code_commit: str,
    memory_content_commit: str = "",
    ledger_commit: str = "",
) -> WorktreeContract:
    """Publish that this contract's code landed, and return the updated contract."""

    updated = amend_contract(
        replace(
            contract,
            integration_strategy=strategy,
            integrated_code_commit=code_commit,
            integrated_memory_content_commit=memory_content_commit,
            integrated_ledger_commit=ledger_commit,
        ),
        ContractCells(integration_status="completed", cleanup="pending"),
    )
    write_contract(contract.contract_path, updated)
    return updated
