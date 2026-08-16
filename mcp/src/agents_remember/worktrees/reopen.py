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

from agents_remember.controlplane.closeout_queue_store import CloseoutQueueStoreError
from agents_remember.kernel.atomic_write import atomic_write_bytes
from agents_remember.kernel.primitives.observer_paths import LANDING_FINAL_BASENAME
from agents_remember.tasks.document import TaskDocument
from agents_remember.tasks.leaf_doc import find_leaf_doc
from agents_remember.tasks.master_sync import demote_completed_master_if_unresolved
from agents_remember.tasks.store import (
    json_path_for,
    markdown_path_for,
    read_task_doc,
    write_task_docs,
)

from .closeout_queue_errors import CloseoutQueueError
from .closeout_queue_lifecycle import publish_queue_bound_task_facts
from .integration_branch_authority import require_parent_series_accepting_leaves
from .integration_ref_transaction import IntegratedCommits, require_integrated_ledger_mapping
from .modules.guidance import (
    RecoveryOperation,
    RecoveryTool,
    recovery_guidance,
    status_payload,
)
from .modules.models import WorktreeCommandResult
from .source_lineage import lineage_block_payload, lineage_refusal, parent_source_lineage
from .worktree_contract import (
    ContractCells,
    WorktreeContract,
    amend_contract,
    load_contract,
    write_contract,
)


class ReopenTaskDocumentError(ValueError):
    """The leaf and its parent index could not be prevalidated for one reset."""


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


def _reopen_preview_args(contract: WorktreeContract) -> dict[str, object]:
    return {"contract_path": contract.contract_path.as_posix(), "dry_run": True}


def _reopen_apply_args(contract: WorktreeContract) -> dict[str, object]:
    return {"contract_path": contract.contract_path.as_posix(), "dry_run": False}


def _start_preview_args(contract: WorktreeContract) -> dict[str, object]:
    """Reconstruct one task-addressed start from durable contract identity.

    ``worktree_name`` is the persisted code-worktree basename. ``parent_task`` is only an
    input discriminator for a genuinely nested task root; a direct master's contract happens
    to record its own series name as ``parent_task_name`` and must not feed that back as a
    nested path.
    """
    repo_task_root = contract.coordination_root / "tasks" / contract.repo_name
    relative = contract.task_root.relative_to(repo_task_root)
    args: dict[str, object] = {
        "repo_id": contract.repo_name,
        "task_name": contract.task_name,
        "worktree_name": contract.code_worktree.name,
        "leaf_id": contract.leaf_id,
        "workflow_kind": contract.workflow_kind,
        "source_branch": contract.code_source_branch,
        "work_branch": contract.code_work_branch,
        "memory_mode": contract.memory_mode,
        "dry_run": True,
    }
    if len(relative.parts) > 1:
        args["parent_task"] = relative.parts[-2]
    return args


def _response_guidance(
    operation: RecoveryOperation,
    *,
    tool: RecoveryTool,
    args: dict[str, object],
    summary: str,
) -> dict[str, object]:
    guidance = recovery_guidance(operation, tool=tool, args=args)
    return {**guidance, "nextStep": {"summary": summary, **guidance}}


def reopen_required_start_result(contract: WorktreeContract) -> WorktreeCommandResult:
    """Route ``worktree_start`` on a cleaned leaf to its explicit state reset."""
    summary = (
        "This leaf completed cleanup. Preview and apply task_reopen for this exact "
        "contract before retrying worktree_start; start will then recreate its "
        "contract-owned branches and worktrees from the current source tips."
    )
    return WorktreeCommandResult(
        2,
        {
            **_contract_reopen_facts(contract),
            "state": "reopen-required",
            "summary": summary,
            **_response_guidance(
                "reopen_completed_task",
                tool="task_reopen",
                args=_reopen_preview_args(contract),
                summary=summary,
            ),
        },
    )


def reopen_task(contract_path: Path, *, dry_run: bool = False) -> WorktreeCommandResult:
    contract = load_contract(contract_path)
    refusal = _reopen_preflight_refusal(contract)
    if refusal is not None:
        return refusal
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
        _, doc_reset = _plan_leaf_doc_reset(contract, dry_run=dry_run)
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
    try:
        frozen_cleared, published_doc_reset = _publish_reopen_transition(
            contract,
            updated,
            dry_run=dry_run,
        )
    except (
        OSError,
        CloseoutQueueError,
        CloseoutQueueStoreError,
        _ReopenTransitionRefusal,
    ) as exc:
        return WorktreeCommandResult(
            2,
            {
                "state": "blocked",
                **_contract_reopen_facts(contract),
                "blockers": [f"reopen-transition: {exc}"],
                "summary": (
                    "Reopen refused and restored every contract, task-document, and landing "
                    f"artifact to its pre-call bytes: {exc}"
                ),
            },
        )
    if published_doc_reset is not None:
        doc_reset = published_doc_reset
    summary = (
        "Reopen preview: the contract state and leaf doc would be reset as listed."
        if dry_run
        else (
            "Leaf task reopened under its original id: contract review/closeout/"
            "integration reset, lifecycle binding cleared, doc back to planning. "
            "Edit the doc's steps via task_doc, then preview worktree_start with this "
            "same leaf id to recreate the worktrees off the current source tips."
        )
    )
    if dry_run:
        operation: RecoveryOperation = "apply_task_reopen"
        tool: RecoveryTool = "task_reopen"
        next_args = _reopen_apply_args(contract)
    else:
        operation = "start_reopened_task"
        tool = "worktree_start"
        next_args = _start_preview_args(updated)
    return WorktreeCommandResult(
        0,
        {
            **_contract_reopen_facts(updated),
            "state": "would-reopen" if dry_run else "reopened",
            "doc": doc_reset,
            "frozenLanding": frozen_cleared,
            "summary": summary,
            **_response_guidance(
                operation,
                tool=tool,
                args=next_args,
                summary=summary,
            ),
        },
    )


def _reopen_preflight_refusal(contract: WorktreeContract) -> WorktreeCommandResult | None:
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

    try:
        require_parent_series_accepting_leaves(contract, operation="task_reopen")
    except RuntimeError as exc:
        return WorktreeCommandResult(
            2,
            {
                "state": "blocked",
                **status_payload(contract),
                "blockers": [str(exc)],
                "summary": f"Reopen refused before resetting task state: {exc}",
            },
        )

    if contract.memory_mode == "external":
        try:
            require_integrated_ledger_mapping(
                contract,
                IntegratedCommits(
                    code=contract.integrated_code_commit,
                    memory_content=contract.integrated_memory_content_commit,
                    ledger=contract.integrated_ledger_commit,
                ),
            )
        except RuntimeError as exc:
            return WorktreeCommandResult(
                2,
                {
                    "state": "blocked",
                    **status_payload(contract),
                    "blockers": [f"integrated-memory-landing: {exc}"],
                    "summary": f"Reopen refused before resetting task state: {exc}",
                },
            )

    # A terminal leaf's source already contains that leaf's exact landed commits.
    # Pre-start lineage compares against the recorded base, which is intentionally
    # older after a successful integration. Reopen instead proves that the current
    # source tips are the exact durable landing recorded by this completed leaf.
    landed = replace(
        contract,
        code_base_commit=contract.integrated_code_commit,
        memory_base_commit=(
            contract.integrated_ledger_commit if contract.memory_mode == "external" else ""
        ),
    )
    lineage = parent_source_lineage(landed)
    if lineage_refusal(lineage) is not None:
        assert lineage is not None
        return WorktreeCommandResult(
            2,
            {
                **status_payload(contract),
                **lineage_block_payload(lineage),
                "summary": ("Reopen refused before resetting task state: " + lineage.summary),
            },
        )
    return None


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


def _plan_leaf_doc_reset(
    contract: WorktreeContract, *, dry_run: bool
) -> tuple[list[TaskDocument], dict | None]:
    """Prepare the doc side without publishing any part of the reopen transition."""
    found = find_leaf_doc(contract.task_root, contract.leaf_id)
    if found is None:
        raise ReopenTaskDocumentError(
            f"leaf {contract.leaf_id!r} has no canonical task document; "
            "task_reopen cannot produce an actionable worktree_start identity"
        )
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
        return ([], report)
    docs = [updated]
    if master is not None:
        docs.append(master)
    return (docs, report)


def _publish_reopen_transition(
    contract: WorktreeContract,
    updated: WorktreeContract,
    *,
    dry_run: bool,
) -> tuple[str, dict | None]:
    """Publish contract, task docs, and landing deletion as one rollback-capable unit."""
    if dry_run:
        return _clear_frozen_landing(contract, dry_run=True), None

    def publication() -> tuple[str, dict | None]:
        current = load_contract(contract.contract_path)
        if current != contract:
            raise _ReopenTransitionRefusal(
                "the completed leaf contract changed after reopen preflight"
            )
        refusal = _reopen_preflight_refusal(current)
        if refusal is not None:
            raise _ReopenTransitionRefusal(str(refusal.payload["summary"]))
        try:
            docs, doc_reset = _plan_leaf_doc_reset(current, dry_run=False)
        except ReopenTaskDocumentError as exc:
            raise _ReopenTransitionRefusal(f"task-document-reset: {exc}") from exc
        final_path = contract.contract_path.parent / LANDING_FINAL_BASENAME
        paths = {contract.contract_path, final_path}
        for doc in docs:
            paths.add(json_path_for(contract.task_root, doc))
            paths.add(markdown_path_for(contract.task_root, doc))
        originals = {path: path.read_bytes() if path.exists() else None for path in paths}
        try:
            frozen_cleared = _clear_frozen_landing(contract, dry_run=False)
            write_task_docs(contract.task_root, docs)
            write_contract(contract.contract_path, updated)
        except BaseException as publish_error:
            try:
                _restore_reopen_artifacts(originals)
            except BaseException as rollback_error:
                raise RuntimeError(
                    f"reopen publication and rollback both failed: {rollback_error}"
                ) from publish_error
            raise
        return frozen_cleared, doc_reset

    return publish_queue_bound_task_facts(
        contract,
        publication,
        topology_stable=True,
    )


def _restore_reopen_artifacts(originals: dict[Path, bytes | None]) -> None:
    for path, payload in originals.items():
        if payload is None:
            path.unlink(missing_ok=True)
        else:
            atomic_write_bytes(path, payload)


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
