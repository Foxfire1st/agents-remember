"""Typed response projections for the resumable sync transaction."""

from __future__ import annotations

from pathlib import Path

from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.guidance import contract_next_args, recovery_guidance
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.sync_transaction_authority import command_result, side_payload
from agents_remember.worktrees.sync_transaction_git import (
    SyncGitProofError,
    unmerged_paths,
    validate_staged_resolution,
)
from agents_remember.worktrees.sync_transaction_recovery import (
    completed_sync_result,
    manual_repair_result,
)
from agents_remember.worktrees.sync_transaction_state import (
    SyncOperationRecord,
    SyncQuarantineRecord,
    SyncSideRecord,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract


def memory_choice_required(
    contract: WorktreeContract,
    code: SyncSideRecord,
    memory: SyncSideRecord,
    fetch: dict[str, object],
) -> WorktreeCommandResult:
    return WorktreeCommandResult(
        2,
        {
            "state": "memory-sync-choice-required",
            "summary": "Memory has local commits and the official line moved. Choose "
            "merge-memory or skip-memory before any code mutation.",
            **recovery_guidance(
                "choose_memory_sync_recovery",
                tool="worktree_sync",
                args=contract_next_args(contract),
                required_args=["memory_sync_choice"],
            ),
            "code": side_payload(code),
            "memory": side_payload(memory),
            "fetch": fetch,
        },
    )


def sync_preview(
    code: SyncSideRecord,
    memory: SyncSideRecord | None,
    fetch: dict[str, object],
) -> WorktreeCommandResult:
    return WorktreeCommandResult(
        0,
        {
            "state": "would-sync",
            "summary": "Preview only; exact sources were read but no refs, journals, or "
            "branches moved.",
            "code": side_payload(code),
            "memory": side_payload(memory),
            "fetch": fetch,
        },
    )


def resolution_required(
    record: SyncOperationRecord,
    fetch: dict[str, object],
) -> WorktreeCommandResult:
    side = record.code if record.phase == "code-resolution-required" else record.memory
    assert side is not None
    return WorktreeCommandResult(
        2,
        {
            "state": "sync-resolution-required",
            "status": "agent-action-required",
            "resolutionOwner": "agent",
            "summary": f"Resolve and stage the retained {side.side} merge, then continue.",
            "resolution": {
                "side": side.side,
                "owner": "agent",
                "worktree": side.worktree,
                "files": list(side.conflictFiles),
            },
            "nextOperation": "continue_sync_resolution",
            "nextTool": "worktree_sync",
            "nextArgs": {
                "contract_path": record.contractPath,
                "resolution_action": "continue",
                "dry_run": False,
            },
            "cancelArgs": {
                "contract_path": record.contractPath,
                "resolution_action": "cancel",
                "dry_run": False,
            },
            "fetch": fetch,
        },
    )


def resolution_validation_preview(
    side: SyncSideRecord,
    fetch: dict[str, object],
) -> WorktreeCommandResult:
    conflicts = unmerged_paths(Path(side.worktree))
    try:
        validate_staged_resolution(side)
        ready = True
        reason = "The staged resolution is ready to validate and commit."
    except SyncGitProofError as error:
        ready = False
        reason = str(error)
    return WorktreeCommandResult(
        0 if ready else 2,
        {
            "state": "would-continue-sync-resolution" if ready else "sync-resolution-incomplete",
            "summary": reason,
            "resolution": {
                "side": side.side,
                "owner": "agent",
                "files": list(conflicts),
            },
            "fetch": fetch,
        },
    )


def active_preview(
    record: SyncOperationRecord,
    fetch: dict[str, object],
) -> WorktreeCommandResult:
    return WorktreeCommandResult(
        0,
        {
            "state": "sync-active",
            "summary": "A journaled sync is active; apply the same call to resume it.",
            "phase": record.phase,
            "nextTool": "worktree_sync",
            "nextArgs": {"contract_path": record.contractPath, "dry_run": False},
            "fetch": fetch,
        },
    )


def cancel_preview(
    record: SyncOperationRecord,
    fetch: dict[str, object],
) -> WorktreeCommandResult:
    return WorktreeCommandResult(
        0,
        {
            "state": "would-cancel-sync",
            "summary": "Preview only; cancellation would restore the pinned pre-sync heads.",
            "phase": record.phase,
            "cancelArgs": {
                "contract_path": record.contractPath,
                "resolution_action": "cancel",
                "dry_run": False,
            },
            "fetch": fetch,
        },
    )


def terminal_resolution_replay(
    contract: WorktreeContract,
    args: WorktreeArgs,
    record: SyncOperationRecord,
    fetch: dict[str, object],
) -> WorktreeCommandResult:
    if args.memory_sync_choice is not None and args.memory_sync_choice != record.memorySyncChoice:
        return manual_repair_result(
            "sync-input-mismatch",
            "memory_sync_choice cannot change for the terminal sync generation.",
            record,
            fetch,
        )
    if record.phase == "cancelled":
        return command_result(
            0 if args.resolution_action == "cancel" else 2,
            "sync-cancelled",
            "The exact sync generation was already cancelled and its heads are restored.",
            fetch,
            phase=record.phase,
        )
    if args.resolution_action == "continue":
        return completed_sync_result(contract, record, fetch)
    return command_result(
        2,
        "sync-already-completed",
        "The exact sync generation completed and cannot now be cancelled. Re-run with "
        "resolution_action='continue' to recover its terminal result.",
        fetch,
        phase=record.phase,
        nextArgs={
            "contract_path": record.contractPath,
            "resolution_action": "continue",
            "dry_run": False,
        },
    )


def quarantine_replay(
    args: WorktreeArgs,
    record: SyncQuarantineRecord,
    fetch: dict[str, object],
) -> WorktreeCommandResult:
    return command_result(
        0 if args.resolution_action == "cancel" else 2,
        "sync-cancelled-no-authority",
        "The corrupt sync journal was already quarantined. No branch heads were claimed "
        "restored because deterministic rollback authority was absent.",
        fetch,
        phase="quarantined",
        evidencePath=record.evidencePath,
    )
