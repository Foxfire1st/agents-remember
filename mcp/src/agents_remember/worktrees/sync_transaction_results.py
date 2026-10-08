"""Typed response projections for the resumable sync transaction."""

from __future__ import annotations

from agents_remember.worktrees.knowledge_crossing import crossing_summary
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.guidance import contract_next_args, recovery_guidance
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.sync_transaction_authority import command_result, side_payload
from agents_remember.worktrees.sync_transaction_git import (
    SyncGitProofError,
    content_conflicts,
    validate_staged_resolution,
)
from agents_remember.worktrees.sync_transaction_recovery import (
    completed_sync_result,
    manual_repair_result,
    with_recomputed_worklist,
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
    parked = side.wipState == "restore-conflict"
    resolution: dict[str, object] = {
        "side": side.side,
        "owner": "agent",
        "worktree": side.worktree,
        "files": list(side.conflictFiles),
    }
    if parked:
        resolution["wipRestore"] = True
    next_operation, next_args, summary = _resolution_guidance(record, side, parked)
    if side.crossingReport:
        crossing = crossing_summary(side.crossingReport)
        resolution["crossing"] = crossing
        kind = "a structural merge of knowledge files (MIK-R24 rule 8 step 3)"
        if crossing.get("converted"):
            kind = "a crossing sync (MIK-R24 rule 8)"
        summary = (
            f"{summary} This is {kind}: "
            f"{crossing.get('conflictItems', '?')} conflicted item(s) are listed in "
            f"{side.crossingReport}; every conflicted JSON item holds a 'crossing-conflict' "
            "marker the knowledge validator refuses until it is resolved."
        )
    return WorktreeCommandResult(
        2,
        {
            "state": "sync-resolution-required",
            "status": "agent-action-required",
            "resolutionOwner": "agent",
            "summary": summary,
            "resolution": resolution,
            "nextOperation": next_operation,
            "nextTool": "worktree_sync",
            "nextArgs": next_args,
            "cancelArgs": {
                "contract_path": record.contractPath,
                "resolution_action": "cancel",
                "dry_run": False,
            },
            "fetch": fetch,
        },
    )


def _resolution_guidance(
    record: SyncOperationRecord,
    side: SyncSideRecord,
    parked: bool,
) -> tuple[str, dict[str, object], str]:
    """The next move, its exact arguments, and the sentence that says what to do.

    A parked candidate reapply and a retained merge both end in the shipped continuation: the agent
    resolves the files in the worktree, stages them, and continues.
    """

    continuation: dict[str, object] = {
        "contract_path": record.contractPath,
        "resolution_action": "continue",
        "dry_run": False,
    }
    if parked:
        return (
            "continue_sync_resolution",
            continuation,
            f"Resolve the parked {side.side} candidate reapply in its worktree, then continue; the "
            "parked WIP stays in the stash until it is settled.",
        )
    return (
        "continue_sync_resolution",
        continuation,
        f"Resolve and stage the retained {side.side} merge, then continue.",
    )


def parked_wip_validation_preview(
    side: SyncSideRecord,
    fetch: dict[str, object],
) -> WorktreeCommandResult:
    """Read-only preview of settling a parked-candidate reapply the agent resolved."""

    conflicts = content_conflicts(side)
    ready = not conflicts
    return WorktreeCommandResult(
        0 if ready else 2,
        {
            "state": "would-settle-parked-wip" if ready else "sync-resolution-incomplete",
            "summary": (
                "The parked candidate reapply is resolved; continuing retires its stash entry "
                "and leaves the candidate uncommitted for closeout."
                if ready
                else f"The parked {side.side} candidate reapply still has unmerged paths."
            ),
            "resolution": {
                "side": side.side,
                "owner": "agent",
                "worktree": side.worktree,
                "files": list(conflicts),
                "wipRestore": True,
            },
            "fetch": fetch,
        },
    )


def resolution_validation_preview(
    side: SyncSideRecord,
    fetch: dict[str, object],
) -> WorktreeCommandResult:
    conflicts = content_conflicts(side)
    try:
        validate_staged_resolution(side)
        ready = True
        reason = "The staged resolution is ready to validate and commit."
    except SyncGitProofError as error:
        ready = False
        reason = str(error)
    resolution: dict[str, object] = {
        "side": side.side,
        "owner": "agent",
        "files": list(conflicts),
    }
    return WorktreeCommandResult(
        0 if ready else 2,
        {
            "state": "would-continue-sync-resolution" if ready else "sync-resolution-incomplete",
            "summary": reason,
            "resolution": resolution,
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
        return with_recomputed_worklist(completed_sync_result(contract, record, fetch), contract)
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
