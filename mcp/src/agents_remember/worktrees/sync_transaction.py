"""Resumable, contract-addressed mid-task source synchronization."""

from __future__ import annotations

from pathlib import Path

from agents_remember.models.worktree import SyncSide
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.sync_transaction_authority import (
    authority_refs_exist,
    command_result,
    pin_authority,
    preflight_official_pair,
    require_pinned_authority,
    require_record_contract,
    side_record,
    source_pair,
    sync_contract_kind,
    update_record,
)
from agents_remember.worktrees.sync_transaction_git import (
    SyncGitProofError,
    continue_side_merge,
    ensure_temporary_worktree,
    git_status,
    require_side_checkout,
    side_branch_head,
    side_merge_completed,
    start_side_merge,
    unmerged_paths,
    validate_current_memory_side,
)
from agents_remember.worktrees.sync_transaction_recovery import (
    cancel_sync,
    cleanup_terminal_residue,
    finalize_sync,
    manual_repair_result,
    recover_missing_journal,
    recover_unreadable_journal,
)
from agents_remember.worktrees.sync_transaction_results import (
    active_preview,
    cancel_preview,
    memory_choice_required,
    quarantine_replay,
    resolution_required,
    resolution_validation_preview,
    sync_preview,
    terminal_resolution_replay,
)
from agents_remember.worktrees.sync_transaction_state import (
    SyncJournalReadError,
    SyncOperationRecord,
    SyncOperationStore,
    SyncQuarantineRecord,
    SyncSideRecord,
    operation_stamp,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract

_ACTIVE_PHASES = {
    "running-code",
    "code-resolution-required",
    "running-memory",
    "memory-resolution-required",
    "finalizing",
    "cancelling",
}


def sync_contract_under_authority(
    contract: WorktreeContract,
    args: WorktreeArgs,
    *,
    fetch: dict[str, object],
) -> WorktreeCommandResult:
    """Start, observe, continue, cancel, or recover one exact sync transaction.

    The caller holds ``integration_authority_lock``. No lock survives the return:
    an agent resolves retained conflicts in the ordinary worktree between calls.
    """

    try:
        invalid = sync_input_refusal(args, fetch)
        if invalid is not None:
            return invalid
        store = SyncOperationStore(contract.worktree_group)
        observed = _read_sync_record(contract, args, store, fetch)
        if isinstance(observed, WorktreeCommandResult):
            return observed
        return _route_sync_record(contract, args, store, observed, fetch)
    except (OSError, RuntimeError, ValueError) as error:
        return command_result(
            2,
            "sync-operation-refused",
            f"Sync could not prove a safe transition ({type(error).__name__}).",
            fetch,
            detail=str(error),
        )


def _read_sync_record(
    contract: WorktreeContract,
    args: WorktreeArgs,
    store: SyncOperationStore,
    fetch: dict[str, object],
) -> SyncOperationRecord | WorktreeCommandResult | None:
    try:
        record = store.read()
    except SyncJournalReadError as error:
        return recover_unreadable_journal(
            contract,
            args,
            store=store,
            error=error,
            fetch=fetch,
        )
    if record is None and authority_refs_exist(contract):
        return recover_missing_journal(
            contract,
            cancel=args.resolution_action == "cancel",
            dry_run=args.dry_run,
            store=store,
            fetch=fetch,
        )
    if isinstance(record, SyncQuarantineRecord):
        return quarantine_replay(args, record, fetch) if args.resolution_action else None
    if record is None:
        return None
    try:
        require_record_contract(contract, record)
    except SyncGitProofError:
        return recover_unreadable_journal(
            contract,
            args,
            store=store,
            error=store.semantic_read_error("journal-identity-invalid"),
            fetch=fetch,
        )
    return record


def _route_sync_record(
    contract: WorktreeContract,
    args: WorktreeArgs,
    store: SyncOperationStore,
    record: SyncOperationRecord | None,
    fetch: dict[str, object],
) -> WorktreeCommandResult:
    if record is not None and record.phase in _ACTIVE_PHASES:
        return _resume_active(contract, args, store, record, fetch)
    if record is not None and not args.dry_run:
        cleanup_terminal_residue(store, record)
    if args.resolution_action is None:
        return _admit_and_run(contract, args, store, record, fetch)
    if record is not None:
        return terminal_resolution_replay(contract, args, record, fetch)
    return command_result(
        2,
        "sync-resolution-not-active",
        "No active sync resolution exists for this contract.",
        fetch,
    )


def sync_input_refusal(
    args: WorktreeArgs,
    fetch: dict[str, object],
) -> WorktreeCommandResult | None:
    """Reject untyped/direct-call inputs before refs, selection, or Git can move."""

    if args.memory_sync_choice not in {None, "merge-memory", "skip-memory"}:
        return command_result(
            2,
            "sync-input-invalid",
            "memory_sync_choice must be exactly 'merge-memory' or 'skip-memory'.",
            fetch,
            invalidField="memory_sync_choice",
        )
    if args.resolution_action not in {None, "continue", "cancel"}:
        return command_result(
            2,
            "sync-input-invalid",
            "resolution_action must be exactly 'continue' or 'cancel'.",
            fetch,
            invalidField="resolution_action",
        )
    return None


def _admit_and_run(
    contract: WorktreeContract,
    args: WorktreeArgs,
    store: SyncOperationStore,
    predecessor: SyncOperationRecord | None,
    fetch: dict[str, object],
) -> WorktreeCommandResult:
    code_tip, memory_tip, external = source_pair(contract)
    stop = preflight_official_pair(contract, code_tip, memory_tip, external, fetch)
    if stop is not None:
        return stop
    code = side_record(contract, "code", code_tip)
    memory = side_record(contract, "memory", memory_tip) if external else None
    current = _already_current_result(
        contract,
        code,
        memory,
        fetch,
    )
    if current is not None:
        return current
    if memory is not None and memory.plan == "merge" and args.memory_sync_choice is None:
        return memory_choice_required(contract, code, memory, fetch)
    if memory is not None and args.memory_sync_choice == "skip-memory":
        memory = memory.model_copy(update={"plan": "skip"})
    preflight = _preflight_participating_sides(code, memory, fetch)
    if preflight is not None:
        return preflight
    if args.dry_run:
        return sync_preview(code, memory, fetch)

    record = _new_sync_record(contract, args, predecessor, code, memory)
    pin_authority(record)
    store.write(record)
    for side in (record.code, record.memory):
        if side is not None and side.temporary and side.plan not in {"already-current", "skip"}:
            ensure_temporary_worktree(side)
    return _run_automatic(contract, store, record, fetch)


def _already_current_result(
    contract: WorktreeContract,
    code: SyncSideRecord,
    memory: SyncSideRecord | None,
    fetch: dict[str, object],
) -> WorktreeCommandResult | None:
    bases_current = code.sourceCommit == contract.code_base_commit and (
        memory is None or memory.sourceCommit == contract.memory_base_commit
    )
    branches_current = code.plan == "already-current" and (
        memory is None or memory.plan == "already-current"
    )
    if not (bases_current and branches_current):
        return None
    try:
        if memory is not None:
            validate_current_memory_side(memory)
    except SyncGitProofError as error:
        return command_result(2, "sync-work-branch-invalid", str(error), fetch)
    return command_result(
        0,
        "already-current",
        "The recorded base pair and participating work branches contain the official line.",
        fetch,
    )


def _new_sync_record(
    contract: WorktreeContract,
    args: WorktreeArgs,
    predecessor: SyncOperationRecord | None,
    code: SyncSideRecord,
    memory: SyncSideRecord | None,
) -> SyncOperationRecord:
    generation = predecessor.generation + 1 if predecessor is not None else 1
    stamp = operation_stamp()
    return SyncOperationRecord(
        generation=generation,
        contractPath=contract.contract_path.resolve(strict=False).as_posix(),
        taskId=contract.task_id,
        contractKind=sync_contract_kind(contract),
        codeBaseFrom=contract.code_base_commit,
        memoryBaseFrom=contract.memory_base_commit,
        phase="running-code",
        memorySyncChoice=args.memory_sync_choice,
        code=code,
        memory=memory,
        createdAt=stamp,
        updatedAt=stamp,
    )


def _preflight_participating_sides(
    code: SyncSideRecord,
    memory: SyncSideRecord | None,
    fetch: dict[str, object],
) -> WorktreeCommandResult | None:
    for side in (code, memory):
        if side is None or side.temporary or side.plan in {"already-current", "skip"}:
            continue
        try:
            require_side_checkout(side)
            dirty = git_status(Path(side.worktree))
        except SyncGitProofError as error:
            return command_result(2, "sync-side-preflight-failed", str(error), fetch)
        if dirty:
            return command_result(
                2,
                "sync-side-preflight-failed",
                f"{side.side} sync requires a clean worktree before transaction admission.",
                fetch,
            )
    return None


def _resume_active(
    contract: WorktreeContract,
    args: WorktreeArgs,
    store: SyncOperationStore,
    record: SyncOperationRecord,
    fetch: dict[str, object],
) -> WorktreeCommandResult:
    require_pinned_authority(
        record,
        allow_expected_missing=record.phase == "finalizing",
    )
    if args.memory_sync_choice is not None and args.memory_sync_choice != record.memorySyncChoice:
        return manual_repair_result(
            "sync-input-mismatch",
            "memory_sync_choice cannot change after sync admission.",
            record,
            fetch,
        )
    if args.dry_run:
        return _active_preview(args, record, fetch)
    return _resume_live(contract, args, store, record, fetch)


def _active_preview(
    args: WorktreeArgs,
    record: SyncOperationRecord,
    fetch: dict[str, object],
) -> WorktreeCommandResult:
    if args.resolution_action == "cancel":
        return cancel_preview(record, fetch)
    if args.resolution_action != "continue":
        return active_preview(record, fetch)
    if record.phase in {"code-resolution-required", "memory-resolution-required"}:
        side = record.code if record.phase == "code-resolution-required" else record.memory
        assert side is not None
        return resolution_validation_preview(side, fetch)
    return manual_repair_result(
        "sync-resolution-not-required",
        "The active transaction has no retained conflict to continue.",
        record,
        fetch,
    )


def _resume_live(
    contract: WorktreeContract,
    args: WorktreeArgs,
    store: SyncOperationStore,
    record: SyncOperationRecord,
    fetch: dict[str, object],
) -> WorktreeCommandResult:
    if args.resolution_action == "cancel" or record.phase == "cancelling":
        return cancel_sync(contract, store, record, fetch)
    if args.resolution_action == "continue":
        return _continue_resolution(contract, store, record, fetch)
    if record.phase in {"code-resolution-required", "memory-resolution-required"}:
        return resolution_required(record, fetch)
    return _run_automatic(contract, store, record, fetch)


def _run_automatic(
    contract: WorktreeContract,
    store: SyncOperationStore,
    record: SyncOperationRecord,
    fetch: dict[str, object],
) -> WorktreeCommandResult:
    try:
        record = _reconcile_completed_sides(store, record)
        if record.phase == "running-code":
            record = _run_side(store, record, "code")
            if record.phase == "code-resolution-required":
                return resolution_required(record, fetch)
        if record.phase == "running-memory":
            record = _run_side(store, record, "memory")
            if record.phase == "memory-resolution-required":
                return resolution_required(record, fetch)
        if record.phase == "finalizing":
            return finalize_sync(contract, store, record, fetch)
    except SyncGitProofError as error:
        return manual_repair_result("sync-git-proof-failed", str(error), record, fetch)
    return manual_repair_result(
        "sync-state-unrecognized",
        f"Sync stopped in unsupported phase {record.phase!r}.",
        record,
        fetch,
    )


def _run_side(
    store: SyncOperationStore,
    record: SyncOperationRecord,
    side_name: SyncSide,
) -> SyncOperationRecord:
    side = record.code if side_name == "code" else record.memory
    if side is None or side.plan in {"already-current", "skip"}:
        if side is not None and side_branch_head(side) != side.preSyncHead:
            raise SyncGitProofError(f"{side.side} work branch moved after sync admission")
        completed = (
            side.model_copy(update={"state": "completed", "resultHead": side.preSyncHead})
            if side is not None
            else None
        )
        return _advance_after_side(store, record, side_name, completed)
    ensure_temporary_worktree(side)
    state, conflicts, _ = start_side_merge(side)
    if state == "resolution-required":
        updated_side = side.model_copy(
            update={"state": "resolution-required", "conflictFiles": conflicts}
        )
        phase = "code-resolution-required" if side_name == "code" else "memory-resolution-required"
        return update_record(store, record, phase=phase, side=updated_side)
    updated_side = side.model_copy(
        update={"state": "completed", "resultHead": side_branch_head(side)}
    )
    return _advance_after_side(store, record, side_name, updated_side)


def _continue_resolution(
    contract: WorktreeContract,
    store: SyncOperationStore,
    record: SyncOperationRecord,
    fetch: dict[str, object],
) -> WorktreeCommandResult:
    if record.phase not in {"code-resolution-required", "memory-resolution-required"}:
        return manual_repair_result(
            "sync-resolution-not-required",
            "The active transaction has no retained conflict to continue.",
            record,
            fetch,
        )
    side_name: SyncSide = "code" if record.phase == "code-resolution-required" else "memory"
    side = record.code if side_name == "code" else record.memory
    assert side is not None
    try:
        result_head = continue_side_merge(side)
    except SyncGitProofError as error:
        refreshed = side.model_copy(update={"conflictFiles": unmerged_paths(Path(side.worktree))})
        record = update_record(store, record, phase=record.phase, side=refreshed)
        return manual_repair_result("sync-resolution-incomplete", str(error), record, fetch)
    completed = side.model_copy(
        update={"state": "completed", "resultHead": result_head, "conflictFiles": ()}
    )
    record = _advance_after_side(store, record, side_name, completed)
    return _run_automatic(contract, store, record, fetch)


def _reconcile_completed_sides(
    store: SyncOperationStore, record: SyncOperationRecord
) -> SyncOperationRecord:
    if record.phase == "running-code" and _side_live_complete(record.code):
        side = record.code.model_copy(
            update={"state": "completed", "resultHead": side_branch_head(record.code)}
        )
        record = _advance_after_side(store, record, "code", side)
    if (
        record.phase == "running-memory"
        and record.memory is not None
        and (
            record.memory.plan in {"already-current", "skip"} or _side_live_complete(record.memory)
        )
    ):
        side = record.memory.model_copy(
            update={
                "state": "completed",
                "resultHead": (
                    record.memory.preSyncHead
                    if record.memory.plan == "skip"
                    else side_branch_head(record.memory)
                ),
            }
        )
        record = _advance_after_side(store, record, "memory", side)
    return record


def _side_live_complete(side: SyncSideRecord) -> bool:
    if side.plan == "already-current":
        return True
    if side.plan == "skip":
        return True
    ensure_temporary_worktree(side)
    return side_merge_completed(side)


def _advance_after_side(
    store: SyncOperationStore,
    record: SyncOperationRecord,
    side_name: SyncSide,
    side: SyncSideRecord | None,
) -> SyncOperationRecord:
    phase = "running-memory" if side_name == "code" and record.memory is not None else "finalizing"
    return update_record(store, record, phase=phase, side=side, side_name=side_name)
