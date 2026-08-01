"""Reopen a completed leaf TASK under its exact same leaf id (the ``task_reopen`` tool).

Reopen is a task-state reset, not a worktree operation — which is why it lives in the
``tasks`` package even though it also rewrites the leaf's enclosure contract.
Historically, restarting a finished leaf meant minting a suffixed leaf id (``…-r1``):
a new enclosure and lifecycle while the doc, chats, and dashboard rows stayed keyed to
the original leaf — a forked identity no join could follow. ``task_reopen`` resets the
one true leaf instead:

- contract: the three progress blockers back to their virgin state (``human_review``
  pending / unapproved, ``closeout`` and ``integration`` not-started), the stale
  ``lifecycle.id`` cleared, and ``cleanup: reopened`` as the marker ``worktree_start``
  recreates fresh from (same tombstone semantics as ``abandoned``);
- doc: status back to ``planning``, its ``lifecycleId`` cleared (the next
  ``worktree_start`` restamps it with the fresh lifecycle), the master's sub-task
  index entry flipped back, and an audit decision appended.

The agent then edits steps via ``task_doc`` and runs a NORMAL ``worktree_start`` with
the same leaf id: worktrees and branches are recreated off the current source tips, a
fresh lifecycle is promoted/minted, and every binding holds by construction because
the leaf id never changed.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from agents_remember.observer.landing_state import LANDING_FINAL_BASENAME
from agents_remember.tasks.document import TaskDocument
from agents_remember.tasks.leaf_doc import find_leaf_doc
from agents_remember.tasks.store import read_task_doc, write_task_docs
from agents_remember.worktrees.modules.guidance import status_payload
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.worktree_contract import (
    ContractCells,
    WorktreeContract,
    amend_contract,
    load_contract,
    write_contract,
)


def reopen_task(contract_path: Path, *, dry_run: bool = False) -> WorktreeCommandResult:
    contract = load_contract(contract_path)

    blockers = _reopen_blockers(contract)
    if blockers:
        return WorktreeCommandResult(
            2,
            {
                "state": "blocked",
                **status_payload(contract),
                "blockers": blockers,
                "summary": (
                    "Reopen refused: only a fully landed leaf (closeout, integration, and "
                    "cleanup completed, worktrees gone) can be reopened. " + " ".join(blockers)
                ),
            },
        )

    updated = amend_contract(
        replace(
            contract,
            approved_for_commit=False,
            commit_approval_note="",
            code_commit="",
            memory_content_commit="",
            ledger_commit="",
            integration_strategy="",
            integrated_code_commit="",
            integrated_memory_content_commit="",
            integrated_ledger_commit="",
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
    doc_reset = _reset_leaf_doc(contract, dry_run=dry_run)
    frozen_cleared = _clear_frozen_landing(contract, dry_run=dry_run)
    if not dry_run:
        write_contract(contract.contract_path, updated)
    return WorktreeCommandResult(
        0,
        {
            "state": "would-reopen" if dry_run else "reopened",
            **status_payload(updated if not dry_run else contract),
            "doc": doc_reset,
            "frozenLanding": frozen_cleared,
            "summary": (
                "Reopen preview: the contract state and leaf doc would be reset as listed."
                if dry_run
                else (
                    "Leaf task reopened under its original id: contract review/closeout/"
                    "integration reset, lifecycle binding cleared, doc back to planning. "
                    "Edit the doc's steps via task_doc, then run worktree_start with this "
                    "same leaf id to recreate the worktrees off the current source tips."
                )
            ),
            "nextOperation": "worktree_start",
        },
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
    try:
        final_path.unlink()
    except OSError:
        return "delete-failed"
    return "deleted"


def _reopen_blockers(contract: WorktreeContract) -> list[str]:
    blockers: list[str] = []
    if contract.kind != "leaf":
        blockers.append(f"contract kind is {contract.kind!r}, not a leaf enclosure.")
        return blockers
    if contract.closeout_status != "completed":
        blockers.append(f"closeout is {contract.closeout_status!r}, not completed.")
    if contract.integration_status != "completed":
        blockers.append(f"integration is {contract.integration_status!r}, not completed.")
    if contract.cleanup != "completed":
        blockers.append(f"cleanup is {contract.cleanup!r}, not completed.")
    for label, worktree in (("code", contract.code_worktree), ("memory", contract.memory_worktree)):
        if worktree is not None and worktree.exists():
            blockers.append(f"the {label} worktree still exists at {worktree.as_posix()}.")
    return blockers


def _reset_leaf_doc(contract: WorktreeContract, *, dry_run: bool) -> dict | None:
    """Doc side of the reopen: planning status, cleared lifecycle, master index, audit entry."""
    found = find_leaf_doc(contract.task_root, contract.leaf_id)
    if found is None:
        return None
    json_path, doc = found
    stamp = datetime.now(UTC).astimezone().strftime("%Y-%m-%dT%H:%M")
    data = doc.model_dump(by_alias=True)
    data["status"] = "planning"
    data["lifecycleId"] = None
    data.setdefault("decisions", []).append(
        {
            "at": stamp,
            "decision": f"Leaf {contract.leaf_id} reopened under its original id.",
            "rationale": (
                "task_reopen reset the enclosure contract (review/closeout/integration "
                "cleared, cleanup=reopened) and this document back to planning; the next "
                "worktree_start on the same leaf id recreates the worktrees and restamps "
                "the fresh lifecycle."
            ),
        }
    )
    updated = TaskDocument.model_validate(data)
    report: dict = {
        "docPath": json_path.as_posix(),
        "status": "planning",
        "lifecycleId": None,
        "reopenedAt": stamp,
    }
    if dry_run:
        return report
    write_task_docs(contract.task_root, [updated])
    report["masterIndex"] = _reset_master_index(contract, doc)
    return report


def _reset_master_index(contract: WorktreeContract, doc: TaskDocument) -> str:
    """Flip the master's sub-task index entry for this leaf back to planning."""
    master_path = contract.task_root / "task.json"
    if not master_path.exists():
        return "no-master"
    try:
        master = read_task_doc(master_path)
    except Exception:
        return "master-unreadable"
    data = master.model_dump(by_alias=True)
    hit = False
    for ref in data.get("subTasks", []):
        if str(ref.get("number", "")).strip().lower() == (doc.id or "").strip().lower():
            ref["status"] = "planning"
            hit = True
    if not hit:
        return "no-index-entry"
    write_task_docs(contract.task_root, [TaskDocument.model_validate(data)])
    return "reset"
