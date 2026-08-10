"""Reopen a completed leaf TASK under its exact same leaf id (the ``task_reopen`` tool).

Reopen resets a leaf's task state, and it does that by REWRITING THE LEAF'S ENCLOSURE
CONTRACT — which is why it lives here rather than in ``tasks``. It reads and amends the
contract, emits a ``WorktreeCommandResult``, and renders its report through the worktree
status payload; the document reset is the smaller half, and it goes through the ``tasks``
package the same way every other worktree operation does. Ranked the other way round it
made ``tasks`` and ``worktrees`` mutually dependent (``layers.toml``): the task document
store could not be loaded without loading the whole worktree lifecycle, for one function
that is a worktree lifecycle operation wearing a task-shaped name.

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

from agents_remember.kernel.primitives.observer_paths import LANDING_FINAL_BASENAME
from agents_remember.tasks.document import TaskDocument
from agents_remember.tasks.leaf_doc import find_leaf_doc
from agents_remember.tasks.master_sync import demote_completed_master_if_unresolved
from agents_remember.tasks.store import read_task_doc, write_task_docs

from .modules.guidance import status_payload
from .modules.models import WorktreeCommandResult
from .worktree_contract import (
    ContractCells,
    WorktreeContract,
    amend_contract,
    load_contract,
    write_contract,
)


class ReopenTaskDocumentError(ValueError):
    """The leaf and its parent index could not be prevalidated for one reset."""


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
    try:
        doc_reset = _reset_leaf_doc(contract, dry_run=dry_run)
    except ReopenTaskDocumentError as exc:
        return WorktreeCommandResult(
            2,
            {
                "state": "blocked",
                **status_payload(contract),
                "blockers": [f"task-document-reset: {exc}"],
                "summary": f"Reopen refused before any reset was written: {exc}",
            },
        )
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
    master, master_state = _plan_master_index_reset(contract, json_path, doc)
    report: dict = {
        "docPath": json_path.as_posix(),
        "status": "planning",
        "lifecycleId": None,
        "reopenedAt": stamp,
        "masterIndex": f"would-{master_state}"
        if dry_run and master_state == "reset"
        else master_state,
    }
    if dry_run:
        return report
    docs = [updated]
    if master is not None:
        docs.append(master)
    write_task_docs(contract.task_root, docs)
    return report


def _plan_master_index_reset(
    contract: WorktreeContract,
    leaf_path: Path,
    doc: TaskDocument,
) -> tuple[TaskDocument | None, str]:
    """Prevalidate the parent reset so leaf and parent publish from one prepared batch."""
    master_path = _reopen_master_path(contract.task_root, doc)
    if master_path is None:
        return None, "no-master"
    if not master_path.exists():
        if doc.master:
            raise ReopenTaskDocumentError(
                f"explicit parent master task document does not exist: {master_path}"
            )
        return None, "no-master"
    try:
        master = read_task_doc(master_path)
    except (OSError, ValueError) as exc:
        raise ReopenTaskDocumentError(
            f"cannot read parent master task document {master_path}: {exc}"
        ) from exc
    if master.kind != "master":
        raise ReopenTaskDocumentError(f"parent task document is not a master: {master_path}")
    data = master.model_dump(by_alias=True)
    refs = data.get("subTasks", [])
    rows = [ref for ref in refs if ref.get("number") == doc.id]
    if not rows:
        if doc.master:
            raise ReopenTaskDocumentError(
                f"explicit parent master contains no exact row {doc.id!r}"
            )
        return None, "no-index-entry"
    if len(rows) != 1:
        raise ReopenTaskDocumentError(
            f"parent master must contain exactly one row {doc.id!r}; found {len(rows)}"
        )
    _validate_reopen_row_path(master_path, leaf_path, doc.id, rows[0])
    rows[0]["status"] = "planning"
    updated = demote_completed_master_if_unresolved(TaskDocument.model_validate(data))
    return updated, "reset"


def _validate_reopen_row_path(
    master_path: Path,
    leaf_path: Path,
    leaf_id: str,
    row: dict,
) -> None:
    file_name = str(row.get("file") or "")
    if not file_name:
        return
    row_path = (master_path.parent / Path(file_name).with_suffix(".json")).resolve(strict=False)
    if row_path != leaf_path.resolve(strict=False):
        raise ReopenTaskDocumentError(
            f"parent row {leaf_id!r} points at {row_path}, not leaf {leaf_path}"
        )


def _reopen_master_path(task_root: Path, doc: TaskDocument) -> Path | None:
    if not doc.master:
        default = task_root / "task.json"
        return default if default.exists() else None
    root = task_root.resolve(strict=False)
    ref = Path(doc.master)
    candidate = (root / ref.with_suffix(".json")).resolve(strict=False)
    if candidate.parent != root:
        raise ReopenTaskDocumentError(
            f"leaf master reference must resolve to a direct child of {root}: {doc.master!r}"
        )
    return candidate
