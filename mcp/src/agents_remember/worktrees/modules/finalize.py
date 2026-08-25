from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from agents_remember.tasks import (
    CompletionBlocker,
    SubTaskRef,
    TaskDocSourceReadError,
    TaskDocSourceSnapshot,
    TaskDocument,
    completion_blockers,
    current_task_doc_source,
    read_task_doc_with_source,
    write_task_docs,
)
from agents_remember.tasks.leaf_doc import (
    TerminalLeafResolutionError,
    resolve_terminal_leaf_doc,
)
from agents_remember.tasks.master_sync import demote_completed_master_if_unresolved
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.cleanup import cleanup_result
from agents_remember.worktrees.modules.git import is_ancestor
from agents_remember.worktrees.modules.guidance import carryover_done
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.task_fact_publication import (
    preview_contract_task_facts,
    publish_contract_task_facts,
    validate_task_fact_mutation,
)
from agents_remember.worktrees.task_resolver import archive_completed_root_task
from agents_remember.worktrees.worktree_contract import WorktreeContract, load_contract


@dataclass(frozen=True)
class FinalizeArgs:
    contract_path: Path
    task_doc_path: Path | None = None
    master_doc_path: Path | None = None
    subtask_number: str = ""
    dry_run: bool = False
    teardown_providers: bool = True


class FinalizeTaskDocumentError(ValueError):
    """The finalizer could not prove its exact task-document completion targets."""


@dataclass(frozen=True)
class FinalizeTaskTargets:
    leaf_path: Path | None = None
    leaf: TaskDocument | None = None
    completed_leaf: TaskDocument | None = None
    leaf_source: TaskDocSourceSnapshot | None = None
    parent_path: Path | None = None
    parent: TaskDocument | None = None
    parent_row: SubTaskRef | None = None
    completed_parent: TaskDocument | None = None
    parent_source: TaskDocSourceSnapshot | None = None


def finalize_result(args: FinalizeArgs) -> WorktreeCommandResult:
    contract = load_contract(args.contract_path)
    readiness = _readiness(contract)
    if readiness:
        return WorktreeCommandResult(
            0,
            {
                **_identity_payload(contract),
                "state": "not-finalizable-yet",
                "dryRun": args.dry_run,
                "contractPath": contract.contract_path.as_posix(),
                "enclosurePath": contract.contract_path.as_posix(),
                "blockers": readiness,
                "summary": "Task lifecycle is not finalizable yet.",
            },
        )

    try:
        targets = _resolve_task_targets(contract, args)
    except (FinalizeTaskDocumentError, TerminalLeafResolutionError) as exc:
        return _task_refusal(
            contract,
            args,
            state="task-document-resolution-blocked",
            blockers=[f"task-document-resolution: {exc}"],
            summary=f"Task document identity could not be proven: {exc}",
        )
    step_blockers = completion_blockers(targets.leaf) if targets.leaf is not None else []
    if step_blockers:
        return _task_refusal(
            contract,
            args,
            state="task-steps-blocked",
            blockers=step_blockers,
            summary="Task lifecycle cannot be finalized while declared work units are unresolved.",
        )

    cleanup = _run_or_verify_cleanup(contract, args)
    if cleanup.returncode != 0:
        return WorktreeCommandResult(
            0,
            {
                **_identity_payload(contract),
                "state": "cleanup-blocked",
                "dryRun": args.dry_run,
                "contractPath": contract.contract_path.as_posix(),
                "enclosurePath": contract.contract_path.as_posix(),
                "cleanup": cleanup.payload,
                "summary": "Cleanup did not complete; task documents were not changed.",
            },
        )

    updated_contract = load_contract(contract.contract_path) if not args.dry_run else contract
    try:
        updates, projection_effects = _reconcile_task_documents(
            updated_contract,
            targets,
            dry_run=args.dry_run,
        )
    except FinalizeTaskDocumentError as exc:
        return _task_refusal(
            updated_contract,
            args,
            state="task-document-publication-blocked",
            blockers=[str(exc)],
            summary=f"Task finalization did not publish task truth: {exc}",
        )
    return _finalized_result(
        updated_contract,
        args,
        cleanup=cleanup.payload,
        updates=updates,
        projection_effects=projection_effects,
    )


def _finalized_result(
    contract: WorktreeContract,
    args: FinalizeArgs,
    *,
    cleanup: dict[str, object],
    updates: dict[str, Any],
    projection_effects: list[dict[str, object]],
) -> WorktreeCommandResult:
    """Build the terminal response after cleanup and task truth have converged."""

    if contract.kind == "series":
        archive = archive_completed_root_task(
            contract.coordination_root,
            contract.repo_name,
            contract.task_root,
            dry_run=args.dry_run,
        )
    else:
        archive = {
            "state": "skipped",
            "reason": "leaf-contract",
            "taskRoot": contract.task_root.as_posix(),
        }
    return WorktreeCommandResult(
        0,
        {
            **_identity_payload(contract),
            "state": "finalized" if not args.dry_run else "would-finalize",
            "dryRun": args.dry_run,
            "contractPath": contract.contract_path.as_posix(),
            "enclosurePath": contract.contract_path.as_posix(),
            "landedCommit": _landed_commit(contract),
            "targetBranch": contract.code_source_branch,
            "cleanup": cleanup,
            "taskUpdates": updates,
            "projectionEffects": projection_effects,
            "taskArchive": archive,
            "summary": (
                "Task lifecycle finalized."
                if not args.dry_run
                else "Task lifecycle would be finalized."
            ),
        },
    )


def _task_refusal(
    contract: WorktreeContract,
    args: FinalizeArgs,
    *,
    state: str,
    blockers: list[str] | list[CompletionBlocker],
    summary: str,
) -> WorktreeCommandResult:
    return WorktreeCommandResult(
        2,
        {
            **_identity_payload(contract),
            "state": state,
            "dryRun": args.dry_run,
            "contractPath": contract.contract_path.as_posix(),
            "enclosurePath": contract.contract_path.as_posix(),
            "blockers": [
                blocker.model_dump() if isinstance(blocker, CompletionBlocker) else blocker
                for blocker in blockers
            ],
            "summary": summary,
        },
    )


def _identity_payload(contract: WorktreeContract) -> dict[str, str]:
    return {
        "taskId": contract.task_id,
        "taskName": contract.task_name,
        "lifecycleId": contract.lifecycle_id,
    }


def _readiness(contract: WorktreeContract) -> list[str]:
    blockers: list[str] = []
    if contract.closeout_status != "completed":
        blockers.append("closeout-not-complete")
    if not contract.code_commit:
        blockers.append("code-commit-missing")
    if contract.integration_status != "completed":
        blockers.append("integration-not-complete")
    landed = _landed_commit(contract)
    if landed and not is_ancestor(contract.code_repo_path, landed, contract.code_source_branch):
        blockers.append("not-landed-on-target-branch")
    carried, _carried_at = carryover_done(contract)
    if not carried:
        blockers.append("memory-carryover-incomplete")
    return blockers


def _landed_commit(contract: WorktreeContract) -> str:
    return contract.integrated_code_commit or contract.code_commit


def _run_or_verify_cleanup(contract: WorktreeContract, args: FinalizeArgs) -> WorktreeCommandResult:
    if contract.cleanup == "completed":
        return WorktreeCommandResult(
            0,
            {
                "state": "already-completed",
                "summary": "Cleanup already completed.",
            },
        )
    try:
        return cleanup_result(
            WorktreeArgs(
                contract_path=contract.contract_path,
                approved=not args.dry_run,
                dry_run=args.dry_run,
                teardown_providers=args.teardown_providers,
            )
        )
    except RuntimeError as exc:
        return WorktreeCommandResult(
            2,
            {
                "state": "blocked",
                "summary": str(exc),
            },
        )


def _resolve_task_targets(
    contract: WorktreeContract,
    args: FinalizeArgs,
) -> FinalizeTaskTargets:
    if contract.kind != "leaf":
        if args.task_doc_path is not None:
            raise FinalizeTaskDocumentError(
                "a series contract cannot assert a leaf task-document completion target"
            )
        return _resolve_parent_target(contract, args, None, None)
    resolved = resolve_terminal_leaf_doc(
        contract.task_root,
        contract.leaf_id,
        asserted_path=args.task_doc_path,
    )
    if resolved is None:
        return _resolve_parent_target(contract, args, None, None)
    leaf_path, resolved_leaf = resolved
    try:
        leaf, leaf_source = read_task_doc_with_source(leaf_path)
    except (OSError, ValueError) as exc:
        raise FinalizeTaskDocumentError(
            f"cannot capture exact leaf task-document source {leaf_path}: {exc}"
        ) from exc
    if leaf != resolved_leaf:
        raise FinalizeTaskDocumentError(
            "contract-bound leaf task document changed during finalization preflight; retry"
        )
    return _resolve_parent_target(contract, args, leaf_path, leaf, leaf_source)


def _resolve_parent_target(
    contract: WorktreeContract,
    args: FinalizeArgs,
    leaf_path: Path | None,
    leaf: TaskDocument | None,
    leaf_source: TaskDocSourceSnapshot | None = None,
) -> FinalizeTaskTargets:
    if leaf is None or leaf_path is None:
        if args.master_doc_path is None and not args.subtask_number:
            return FinalizeTaskTargets()
        raise FinalizeTaskDocumentError(
            "cannot complete an immediate parent row without a contract-bound leaf document"
        )
    if not leaf.master:
        if args.master_doc_path is not None or args.subtask_number:
            raise FinalizeTaskDocumentError(
                "standalone leaf has no immediate parent reference to assert"
            )
        return FinalizeTaskTargets(
            leaf_path=leaf_path,
            leaf=leaf,
            completed_leaf=_leaf_completion_candidate(leaf),
            leaf_source=leaf_source,
        )
    expected_parent = _expected_parent_path(contract.task_root, leaf)
    _assert_parent_arguments(args, expected_parent, leaf.id)
    parent, parent_source = _read_parent(expected_parent)
    row = _exact_parent_row(parent, leaf.id)
    _check_parent_row_path(expected_parent, row, leaf_path)
    completed_parent = _parent_completion_candidate(parent, row.number)
    return FinalizeTaskTargets(
        leaf_path=leaf_path,
        leaf=leaf,
        completed_leaf=_leaf_completion_candidate(leaf),
        leaf_source=leaf_source,
        parent_path=expected_parent,
        parent=parent,
        parent_row=row,
        completed_parent=completed_parent,
        parent_source=parent_source,
    )


def _assert_parent_arguments(
    args: FinalizeArgs,
    expected_parent: Path,
    leaf_id: str,
) -> None:
    if (
        args.master_doc_path is not None
        and args.master_doc_path.resolve(strict=False) != expected_parent
    ):
        raise FinalizeTaskDocumentError(
            f"master_doc_path {args.master_doc_path.resolve(strict=False)} is not the leaf's "
            f"immediate parent {expected_parent}"
        )
    if args.subtask_number and args.subtask_number != leaf_id:
        raise FinalizeTaskDocumentError(
            f"subtask_number {args.subtask_number!r} does not identify leaf {leaf_id!r}"
        )


def _read_parent(parent_path: Path) -> tuple[TaskDocument, TaskDocSourceSnapshot]:
    try:
        parent, source = read_task_doc_with_source(parent_path)
    except (OSError, ValueError) as exc:
        raise FinalizeTaskDocumentError(
            f"cannot read immediate parent task document {parent_path}: {exc}"
        ) from exc
    if parent.kind != "master":
        raise FinalizeTaskDocumentError(
            f"immediate parent path is not a master task document: {parent_path}"
        )
    return parent, source


def _exact_parent_row(parent: TaskDocument, subtask_number: str) -> SubTaskRef:
    rows = [row for row in parent.subTasks if row.number == subtask_number]
    if len(rows) != 1:
        raise FinalizeTaskDocumentError(
            f"immediate parent must contain exactly one row {subtask_number!r}; found {len(rows)}"
        )
    return rows[0]


def _check_parent_row_path(parent_path: Path, row: SubTaskRef, leaf_path: Path) -> None:
    if not row.file:
        return
    row_leaf_path = (parent_path.parent / Path(row.file).with_suffix(".json")).resolve(strict=False)
    if row_leaf_path != leaf_path.resolve(strict=False):
        raise FinalizeTaskDocumentError(
            f"parent row {row.number!r} points at {row_leaf_path}, not leaf {leaf_path}"
        )


def _expected_parent_path(task_root: Path, leaf: TaskDocument) -> Path:
    ref = Path(leaf.master) if leaf.master else Path("task.md")
    root = task_root.resolve(strict=False)
    candidate = (root / ref.with_suffix(".json")).resolve(strict=False)
    if candidate.parent != root:
        raise FinalizeTaskDocumentError(
            f"leaf master reference must resolve to a direct child of {root}: {leaf.master!r}"
        )
    return candidate


def _reconcile_task_documents(
    contract: WorktreeContract,
    targets: FinalizeTaskTargets,
    *,
    dry_run: bool,
) -> tuple[dict[str, Any], list[dict[str, object]]]:
    updates: dict[str, Any] = {}
    documents: list[TaskDocument] = []
    if targets.leaf_path is None or targets.leaf is None:
        updates["leaf"] = {
            "state": "skipped",
            "reason": "no contract-bound leaf task document authored",
        }
    else:
        if targets.completed_leaf is None:
            raise FinalizeTaskDocumentError("preflighted leaf completion candidate is missing")
        documents.append(targets.completed_leaf)
        updates["leaf"] = _task_update_payload(
            targets.leaf_path,
            targets.completed_leaf,
            dry_run=dry_run,
        )

    if targets.parent_path is None or targets.parent is None or targets.parent_row is None:
        updates["parent"] = {"state": "skipped", "reason": "leaf has no immediate parent"}
    else:
        if targets.completed_parent is None:
            raise FinalizeTaskDocumentError("preflighted parent completion candidate is missing")
        documents.append(targets.completed_parent)
        updates["parent"] = _task_update_payload(
            targets.parent_path,
            targets.completed_parent,
            dry_run=dry_run,
        )
        updates["parent"]["subtaskNumber"] = targets.parent_row.number
    projection_effects: list[dict[str, object]] = []
    if dry_run and documents:
        validate_task_fact_mutation(
            contract.coordination_root,
            contract.repo_name,
            lambda: _require_finalize_sources_current(targets),
        )
        projection_effects = [
            effect.model_dump(by_alias=True)
            for effect in preview_contract_task_facts(contract, tuple(documents))
        ]
    elif documents:
        if targets.leaf_path is None:
            raise FinalizeTaskDocumentError("completion candidates have no task-document root")
        task_root = targets.leaf_path.parent
        published = publish_contract_task_facts(
            contract,
            lambda: write_task_docs(task_root, documents),
            documents=tuple(documents),
            validate=lambda: _require_finalize_sources_current(targets),
        )
        projection_effects = [
            effect.model_dump(by_alias=True) for effect in published.projection_effects
        ]
    return updates, projection_effects


def _require_finalize_sources_current(targets: FinalizeTaskTargets) -> None:
    for source in (targets.leaf_source, targets.parent_source):
        if source is None:
            continue
        try:
            current = current_task_doc_source(source)
        except TaskDocSourceReadError as exc:
            raise FinalizeTaskDocumentError(
                f"task document became unreadable before finalization: {exc.evidence()}"
            ) from exc
        if current != source:
            raise FinalizeTaskDocumentError(
                "task document source changed after finalization preflight; re-read and retry"
            )


def _leaf_completion_candidate(doc: TaskDocument) -> TaskDocument:
    data = doc.model_dump(by_alias=True)
    data["status"] = "Completed"
    data["decisions"] = _finalized_decisions(data)
    return TaskDocument.model_validate(data)


def _parent_completion_candidate(
    doc: TaskDocument,
    subtask_number: str,
) -> TaskDocument:
    data = doc.model_dump(by_alias=True)
    refs = data["subTasks"]
    index = next((idx for idx, ref in enumerate(refs) if ref["number"] == subtask_number), None)
    if index is None:
        raise FinalizeTaskDocumentError(
            f"preflighted parent row disappeared before reconciliation: {subtask_number!r}"
        )
    refs[index]["status"] = "Completed"
    data["decisions"] = _finalized_decisions(data)
    updated = TaskDocument.model_validate(data)
    return demote_completed_master_if_unresolved(updated)


def _task_update_payload(
    path: Path,
    doc: TaskDocument,
    *,
    dry_run: bool,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "state": "would-update" if dry_run else "updated",
        "docPath": path.as_posix(),
        "status": doc.status,
    }
    if not dry_run:
        payload["renderedPath"] = path.with_suffix(".md").as_posix()
    return payload


def _finalized_decisions(data: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "at": datetime.now().astimezone().isoformat(timespec="minutes"),
            "decision": "Finalize task lifecycle.",
            "rationale": "The finalizer proved the task commit landed on the parent target branch, cleanup completed, and task documents could be reconciled.",
        },
        *data.get("decisions", []),
    ]
