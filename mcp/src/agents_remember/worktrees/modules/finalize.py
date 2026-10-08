from __future__ import annotations

from pathlib import Path
from typing import Any

from agents_remember.tasks import CompletionBlocker, completion_blockers
from agents_remember.tasks.leaf_doc import TerminalLeafResolutionError
from agents_remember.worktrees.activation.atomic_series_activation_terminal import (
    with_terminal_atomic_series_release,
)
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.cleanup import cleanup_result
from agents_remember.worktrees.modules.cleanup_report import cleanup_report
from agents_remember.worktrees.modules.git import is_ancestor
from agents_remember.worktrees.modules.guidance import carryover_done
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.modules.terminal_agents import archive_terminal_agents
from agents_remember.worktrees.services import (
    ReviewArtifactCleanupRequest,
    WorktreeServicesUnboundError,
    worktree_services,
)
from agents_remember.worktrees.task_resolver import archive_completed_root_task
from agents_remember.worktrees.worktree_contract import WorktreeContract, load_contract

from .finalize_task_documents import (
    FinalizeArgs,
    FinalizeTaskDocumentError,
    _reconcile_task_documents,
    _resolve_task_targets,
)


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

    activation_release = with_terminal_atomic_series_release(
        contract,
        WorktreeCommandResult(
            0,
            {
                "state": "terminal-finalization-release",
                "summary": "Terminal task truth is ready for archival.",
            },
        ),
        dry_run=args.dry_run,
    )
    if activation_release.returncode != 0:
        return WorktreeCommandResult(
            2,
            {
                **_identity_payload(contract),
                **activation_release.payload,
                "state": "activation-release-blocked",
                "dryRun": args.dry_run,
                "contractPath": contract.contract_path.as_posix(),
                "enclosurePath": contract.contract_path.as_posix(),
                "cleanup": cleanup,
                "taskUpdates": updates,
            },
        )
    if contract.kind == "series":
        archive = archive_completed_root_task(
            contract.coordination_root,
            contract.repo_name,
            contract.task_root,
            dry_run=args.dry_run,
        )
        archive = _with_review_artifact_cleanup(contract, archive, dry_run=args.dry_run)
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
            **({"agentArchive": cleanup["agentArchive"]} if "agentArchive" in cleanup else {}),
            "taskUpdates": updates,
            "projectionEffects": projection_effects,
            "taskArchive": archive,
            **{
                key: activation_release.payload[key]
                for key in (
                    "atomicSeriesActivation",
                    "atomicSeriesActivationRelease",
                )
                if key in activation_release.payload
            },
            "summary": (
                "Task lifecycle finalized."
                if not args.dry_run
                else "Task lifecycle would be finalized."
            ),
        },
    )


def _with_review_artifact_cleanup(
    contract: WorktreeContract, archive: dict[str, object], *, dry_run: bool
) -> dict[str, object]:
    """Carry the archive hook's report (MIK-R25 rule 5): the task's review refs and copies go with it.

    It runs only for a task that is archived now (or would be, on a dry run). A process composed
    without the hook says so rather than reporting that nothing was there to delete.
    """

    state = archive.get("state")
    if state not in {"archived", "would-archive"}:
        return archive
    try:
        port = worktree_services().review_artifact_cleanup
    except WorktreeServicesUnboundError:
        port = None
    if port is None:
        return {
            **archive,
            "reviewArtifacts": {
                "state": "not-bound",
                "detail": "no review-artifact cleanup is bound into this process",
            },
        }
    task_root = Path(str(archive.get("archivePath"))) if state == "archived" else contract.task_root
    request = ReviewArtifactCleanupRequest(
        task_root=task_root,
        task_name=contract.task_root.name,
        code_repository=contract.code_repo_path,
        memory_repository=contract.memory_repo_path,
        dry_run=dry_run,
    )
    try:
        report: dict[str, object] = port.cleanup(request)
    except (
        Exception
    ) as error:  # the task is already archived: a failed hook is reported, not raised
        report = {"state": "failed", "detail": f"{type(error).__name__}: {error}"}
    return {**archive, "reviewArtifacts": report}


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
        agents = archive_terminal_agents(contract, dry_run=args.dry_run)
        return WorktreeCommandResult(
            0,
            {
                "state": "already-completed",
                "summary": "Cleanup already completed.",
                **({"agentArchive": agents} if agents else {}),
            },
        )
    try:
        result = cleanup_result(
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
    if args.dry_run or result.returncode != 0:
        # A dry run reports the plan in cleanup's own words, and a failed cleanup is a refusal
        # the caller must see whole, ``blockers`` and partial inventory included. Only a real,
        # completed reclamation is shaped into the operator report this module restores.
        return result
    return WorktreeCommandResult(
        result.returncode,
        {
            **cleanup_report(contract, result.payload),
            **(
                {"agentArchive": result.payload["agentArchive"]}
                if "agentArchive" in result.payload
                else {}
            ),
        },
    )
