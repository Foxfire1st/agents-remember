from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from agents_remember.tasks import TaskDocument, read_task_doc, write_task_doc
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.cleanup import cleanup_result
from agents_remember.worktrees.modules.git import is_ancestor
from agents_remember.worktrees.modules.guidance import carryover_done
from agents_remember.worktrees.modules.models import WorktreeCommandResult
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
    updates = _reconcile_task_documents(args)
    if updated_contract.kind == "series":
        archive = archive_completed_root_task(
            updated_contract.coordination_root,
            updated_contract.repo_name,
            updated_contract.task_root,
            dry_run=args.dry_run,
        )
    else:
        archive = {
            "state": "skipped",
            "reason": "leaf-contract",
            "taskRoot": updated_contract.task_root.as_posix(),
        }
    return WorktreeCommandResult(
        0,
        {
            **_identity_payload(updated_contract),
            "state": "finalized" if not args.dry_run else "would-finalize",
            "dryRun": args.dry_run,
            "contractPath": updated_contract.contract_path.as_posix(),
            "enclosurePath": updated_contract.contract_path.as_posix(),
            "landedCommit": _landed_commit(updated_contract),
            "targetBranch": updated_contract.code_source_branch,
            "cleanup": cleanup.payload,
            "taskUpdates": updates,
            "taskArchive": archive,
            "summary": (
                "Task lifecycle finalized."
                if not args.dry_run
                else "Task lifecycle would be finalized."
            ),
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


def _reconcile_task_documents(args: FinalizeArgs) -> dict[str, Any]:
    updates: dict[str, Any] = {}
    if args.task_doc_path is None:
        updates["leaf"] = {"state": "skipped", "reason": "task_doc_path not supplied"}
    else:
        updates["leaf"] = _complete_leaf(args.task_doc_path, dry_run=args.dry_run)

    if args.master_doc_path is None:
        updates["parent"] = {"state": "skipped", "reason": "master_doc_path not supplied"}
    elif not args.subtask_number:
        updates["parent"] = {"state": "skipped", "reason": "subtask_number not supplied"}
    else:
        updates["parent"] = _complete_parent_row(
            args.master_doc_path,
            args.subtask_number,
            dry_run=args.dry_run,
        )
    return updates


def _complete_leaf(path: Path, *, dry_run: bool) -> dict[str, Any]:
    if not path.exists():
        return {"state": "skipped", "reason": "task document not found", "path": path.as_posix()}
    doc = read_task_doc(path)
    if doc.kind == "master":
        return {
            "state": "skipped",
            "reason": "leaf path points at a master",
            "path": path.as_posix(),
        }
    data = doc.model_dump(by_alias=True)
    data["status"] = "Completed"
    data["decisions"] = _finalized_decisions(data)
    updated = TaskDocument.model_validate(data)
    return _write_or_preview(path.parent, updated, dry_run=dry_run)


def _complete_parent_row(path: Path, subtask_number: str, *, dry_run: bool) -> dict[str, Any]:
    if not path.exists():
        return {"state": "skipped", "reason": "master document not found", "path": path.as_posix()}
    doc = read_task_doc(path)
    if doc.kind != "master":
        return {
            "state": "skipped",
            "reason": "master path is not a master",
            "path": path.as_posix(),
        }
    data = doc.model_dump(by_alias=True)
    refs = data["subTasks"]
    index = next((idx for idx, ref in enumerate(refs) if ref["number"] == subtask_number), None)
    if index is None:
        return {
            "state": "skipped",
            "reason": "subtask row not found",
            "path": path.as_posix(),
            "subtaskNumber": subtask_number,
        }
    refs[index]["status"] = "Completed"
    data["decisions"] = _finalized_decisions(data)
    updated = TaskDocument.model_validate(data)
    result = _write_or_preview(path.parent, updated, dry_run=dry_run)
    result["subtaskNumber"] = subtask_number
    return result


def _write_or_preview(task_root: Path, doc: TaskDocument, *, dry_run: bool) -> dict[str, Any]:
    if dry_run:
        return {
            "state": "would-update",
            "docPath": (
                task_root / f"{doc.slug if doc.kind == 'subTask' else 'task'}.json"
            ).as_posix(),
            "status": doc.status,
        }
    json_path, markdown_path = write_task_doc(task_root, doc)
    return {
        "state": "updated",
        "docPath": json_path.as_posix(),
        "renderedPath": markdown_path.as_posix(),
        "status": doc.status,
    }


def _finalized_decisions(data: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "at": datetime.now().astimezone().isoformat(timespec="minutes"),
            "decision": "Finalize task lifecycle.",
            "rationale": "The finalizer proved the task commit landed on the parent target branch, cleanup completed, and task documents could be reconciled.",
        },
        *data.get("decisions", []),
    ]
