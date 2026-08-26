"""Finalization, rollback, and damaged-journal escape for worktree sync."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from agents_remember.kernel.git_command import run_git
from agents_remember.models.worktree import SyncSide
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.git import branch_commit, head_commit, is_ancestor
from agents_remember.worktrees.modules.guidance import contract_next_args, recovery_guidance
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.sync_transaction_authority import (
    authority_refs_exist,
    command_result,
    complete_side_from_refs,
    delete_authority,
    reload_contract,
    remove_temporary_worktrees,
    require_contract_bases_unchanged,
    require_finalizable_contract,
    side_locations,
    side_payload,
    sync_contract_kind,
    target_memory_base,
    update_record,
)
from agents_remember.worktrees.sync_transaction_git import (
    SyncGitProofError,
    delete_pinned_ref,
    ensure_temporary_worktree,
    exact_created_head,
    merge_head,
    read_ref,
    remove_temporary_worktree,
    rollback_side,
    side_branch_head,
    validate_completed_side,
)
from agents_remember.worktrees.sync_transaction_state import (
    SyncJournalReadError,
    SyncOperationRecord,
    SyncOperationStore,
    SyncQuarantineRecord,
    SyncSideRecord,
    operation_stamp,
    sync_side_base_ref,
    sync_side_refs,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract, write_contract


def finalize_sync(
    contract: WorktreeContract,
    store: SyncOperationStore,
    record: SyncOperationRecord,
    fetch: dict[str, object],
) -> WorktreeCommandResult:
    current = reload_contract(contract)
    require_finalizable_contract(current, record)
    _require_completed_branches(record)
    memory_to = target_memory_base(record)
    if (
        current.code_base_commit != record.code.sourceCommit
        or current.memory_base_commit != memory_to
    ):
        entry = {
            "at": operation_stamp(),
            "codeBaseFrom": record.codeBaseFrom,
            "codeBaseTo": record.code.sourceCommit,
            "memoryBaseFrom": record.memoryBaseFrom,
            "memoryBaseTo": memory_to,
            "code": record.code.plan,
            "memory": record.memory.plan if record.memory is not None else "no-external-memory",
        }
        current = replace(
            current,
            code_base_commit=record.code.sourceCommit,
            memory_base_commit=memory_to,
            sync_log=(*current.sync_log, entry),
        )
        write_contract(current.contract_path, current)
    # Publish the terminal transaction before deleting its recovery authority. A crash at
    # any later cut leaves a terminal record whose residue cleanup is strictly idempotent.
    record = update_record(store, record, phase="completed")
    remove_temporary_worktrees(record)
    delete_authority(record)
    return completed_sync_result(current, record, fetch)


def completed_sync_result(
    contract: WorktreeContract,
    record: SyncOperationRecord,
    fetch: dict[str, object],
) -> WorktreeCommandResult:
    """Reconstruct the exact successful-pass result, including moved-again state."""

    current = reload_contract(contract)
    memory_to = target_memory_base(record)
    if (
        current.code_base_commit != record.code.sourceCommit
        or current.memory_base_commit != memory_to
    ):
        raise SyncGitProofError(
            "completed sync journal does not match the contract's finalized base pair"
        )
    latest_code = branch_commit(current.code_repo_path, current.code_source_branch)
    latest_memory = (
        branch_commit(current.memory_repo_path, current.memory_source_branch)
        if current.memory_mode == "external" and current.memory_repo_path is not None
        else ""
    )
    moved_again = latest_code != record.code.sourceCommit or (
        record.memory is not None and latest_memory != record.memory.sourceCommit
    )
    memory_skipped = record.memory is not None and record.memory.plan == "skip"
    state = (
        "sync-pass-completed-memory-skipped"
        if memory_skipped
        else "sync-pass-completed-source-moved-again"
        if moved_again
        else "synced"
    )
    return WorktreeCommandResult(
        0,
        {
            "state": state,
            "summary": (
                "The code sync pass completed, but memory was explicitly skipped and the "
                "recorded pair is not current. Run merge-memory before paired activation."
                if memory_skipped
                else "The admitted sync pass completed, but the source moved again; run the "
                "next contract-addressed sync before resuming work."
                if moved_again
                else "The worktree base pair now matches the admitted official line."
            ),
            "code": side_payload(record.code),
            "memory": side_payload(record.memory),
            "codeBaseCommit": record.code.sourceCommit,
            "memoryBaseCommit": memory_to,
            "fetch": fetch,
            **(
                recovery_guidance(
                    "sync_source_lineage",
                    tool="worktree_sync",
                    args=contract_next_args(current, dry_run=True),
                )
                if moved_again or memory_skipped
                else {}
            ),
        },
    )


def cancel_sync(
    contract: WorktreeContract,
    store: SyncOperationStore,
    record: SyncOperationRecord,
    fetch: dict[str, object],
) -> WorktreeCommandResult:
    record = update_record(store, record, phase="cancelling")
    for side in (record.memory, record.code):
        if side is not None:
            if side.plan in {"already-current", "skip"}:
                if side_branch_head(side) != side.preSyncHead:
                    raise SyncGitProofError(
                        f"{side.side} unchanged branch moved after sync admission"
                    )
            else:
                rollback_side(side)
    require_contract_bases_unchanged(reload_contract(contract), record)
    remove_temporary_worktrees(record)
    record = update_record(store, record, phase="cancelled")
    delete_authority(record)
    return command_result(
        0,
        "sync-cancelled",
        "The exact sync transaction was cancelled and every participating branch was restored.",
        fetch,
    )


def recover_unreadable_journal(
    contract: WorktreeContract,
    args: WorktreeArgs,
    *,
    store: SyncOperationStore,
    error: SyncJournalReadError,
    fetch: dict[str, object],
) -> WorktreeCommandResult:
    identity_invalid = error.reason == "journal-identity-invalid"
    if args.resolution_action != "cancel" or args.dry_run:
        return command_result(
            2,
            "sync-journal-identity-invalid" if identity_invalid else "sync-journal-malformed",
            (
                "The stable sync journal contradicts the configured contract authority. "
                if identity_invalid
                else "The stable sync journal is malformed. "
            )
            + "Normal sync fails closed; explicit cancellation may recover through the "
            "deterministic Git authority refs.",
            fetch,
            evidencePath=error.path.as_posix(),
            nextTool="worktree_sync",
            nextArgs=_cancel_args(contract),
        )
    try:
        archive = store.archive_malformed(error)
    except (OSError, RuntimeError, ValueError) as archive_error:
        return command_result(
            2,
            "sync-cancel-manual-repair-required",
            "Explicit cancellation could not preserve the unreadable journal entry; "
            f"automatic rollback is unsafe ({type(archive_error).__name__}).",
            fetch,
            evidencePath=error.path.as_posix(),
            manualRepair=_manual_repair_evidence(contract),
            nextTool="worktree_sync",
            nextArgs=_cancel_args(contract),
        )
    try:
        refs_exist = authority_refs_exist(contract)
    except SyncGitProofError as ref_error:
        return command_result(
            2,
            "sync-cancel-manual-repair-required",
            f"The corrupt journal was archived, but Git ref absence is unproven: {ref_error}",
            fetch,
            evidencePath=archive.as_posix() if archive else error.path.as_posix(),
            manualRepair=_manual_repair_evidence(contract),
            nextTool="worktree_sync",
            nextArgs=_cancel_args(contract),
        )
    if archive is not None and not refs_exist:
        quarantined = SyncQuarantineRecord(
            contractPath=contract.contract_path.resolve(strict=False).as_posix(),
            reason=error.reason,
            evidencePath=archive.as_posix(),
            createdAt=operation_stamp(),
        )
        store.write_quarantine(quarantined)
        return command_result(
            0,
            "sync-cancelled-no-authority",
            "The corrupt journal bytes were archived and replaced by strict terminal "
            "quarantine evidence. No branch heads were claimed restored because all "
            "deterministic rollback refs were absent.",
            fetch,
            phase="quarantined",
            evidencePath=archive.as_posix(),
        )
    return _recover_from_refs(contract, store, fetch, archive)


def recover_missing_journal(
    contract: WorktreeContract,
    *,
    cancel: bool,
    dry_run: bool,
    store: SyncOperationStore,
    fetch: dict[str, object],
) -> WorktreeCommandResult:
    if not cancel or dry_run:
        return command_result(
            2,
            "sync-journal-missing-after-admission",
            "Sync authority refs exist without their journal. Normal sync fails closed; use "
            "explicit cancellation to prove and restore each complete side.",
            fetch,
            nextArgs=_cancel_args(contract),
        )
    return _recover_from_refs(contract, store, fetch, None)


def cleanup_terminal_residue(
    store: SyncOperationStore,
    record: SyncOperationRecord,
) -> None:
    """Finish only idempotent temp/ref cleanup left after a terminal publication."""

    remove_temporary_worktrees(record)
    delete_authority(record)
    # Re-publish the same strict bytes after cleanup so a truncated prior write cannot
    # masquerade as successful residue collection.
    store.write(record)


def manual_repair_result(
    state: str,
    summary: str,
    record: SyncOperationRecord,
    fetch: dict[str, object],
) -> WorktreeCommandResult:
    return command_result(
        2,
        state,
        summary,
        fetch,
        phase=record.phase,
        cancelArgs=_cancel_args_for_path(record.contractPath),
    )


def _recover_from_refs(
    contract: WorktreeContract,
    store: SyncOperationStore,
    fetch: dict[str, object],
    archive: Path | None,
) -> WorktreeCommandResult:
    try:
        code, code_error = _read_recovery_side(contract, "code")
        memory, memory_error = (
            _read_recovery_side(contract, "memory")
            if contract.memory_mode == "external" and contract.memory_repo_path is not None
            else (None, None)
        )
        errors = [error for error in (code_error, memory_error) if error is not None]
        if code is None and memory is not None:
            errors.append("code recovery refs are absent while memory refs are complete")
        if errors:
            return _recover_complete_sides_around_partial_authority(
                contract,
                (memory, code),
                errors,
                fetch,
                archive,
            )
        if code is None:
            raise SyncGitProofError(
                "code sync recovery refs are absent or incomplete; automatic rollback is unsafe"
            )
        for side in (memory, code):
            if side is not None:
                _prove_rollback_possible(side)
        stamp = operation_stamp()
        record = SyncOperationRecord(
            generation=1,
            contractPath=contract.contract_path.resolve(strict=False).as_posix(),
            taskId=contract.task_id,
            contractKind=sync_contract_kind(contract),
            codeBaseFrom=code.baseCommit,
            memoryBaseFrom=memory.baseCommit if memory is not None else contract.memory_base_commit,
            phase="cancelling",
            memorySyncChoice="merge-memory" if memory is not None else None,
            code=code,
            memory=memory,
            createdAt=stamp,
            updatedAt=stamp,
        )
        store.write(record)
        return cancel_sync(contract, store, record, fetch)
    except SyncGitProofError as error:
        return command_result(
            2,
            "sync-cancel-manual-repair-required",
            str(error),
            fetch,
            evidencePath=archive.as_posix() if archive else None,
            manualRepair=_manual_repair_evidence(contract),
            nextTool="worktree_sync",
            nextArgs=_cancel_args(contract),
        )


def _read_recovery_side(
    contract: WorktreeContract,
    side_name: SyncSide,
) -> tuple[SyncSideRecord | None, str | None]:
    try:
        return complete_side_from_refs(contract, side_name), None
    except SyncGitProofError as error:
        return None, str(error)


def _recover_complete_sides_around_partial_authority(
    contract: WorktreeContract,
    sides: tuple[SyncSideRecord | None, ...],
    errors: list[str],
    fetch: dict[str, object],
    archive: Path | None,
) -> WorktreeCommandResult:
    """Restore every complete side while preserving incomplete authority for manual repair."""

    complete = tuple(side for side in sides if side is not None)
    restored: list[str] = []
    try:
        for side in complete:
            _prove_rollback_possible(side)
        for side in complete:
            rollback_side(side)
            remove_temporary_worktree(side)
            delete_pinned_ref(Path(side.repository), side.sourceBackupRef, side.sourceCommit)
            delete_pinned_ref(Path(side.repository), side.backupRef, side.preSyncHead)
            delete_pinned_ref(Path(side.repository), side.baseBackupRef, side.baseCommit)
            restored.append(side.side)
    except SyncGitProofError as error:
        errors.append(str(error))
    restored_summary = ", ".join(restored) or "none"
    return command_result(
        2,
        "sync-cancel-manual-repair-required",
        "Explicit cancellation restored every complete recoverable side "
        f"({restored_summary}), but incomplete authority remains: {'; '.join(errors)}",
        fetch,
        evidencePath=archive.as_posix() if archive else None,
        manualRepair=_manual_repair_evidence(contract),
        nextTool="worktree_sync",
        nextArgs=_cancel_args(contract),
    )


def _manual_repair_evidence(contract: WorktreeContract) -> dict[str, object]:
    sides: list[dict[str, object]] = []
    side_names = ["code"] + (["memory"] if contract.memory_mode == "external" else [])
    for side_name in side_names:
        try:
            if side_name == "code":
                repository, worktree, _, work_branch = side_locations(contract, "code")
                backup, source = sync_side_refs(contract.contract_path, "code")
                base = sync_side_base_ref(contract.contract_path, "code")
            else:
                repository, worktree, _, work_branch = side_locations(contract, "memory")
                backup, source = sync_side_refs(contract.contract_path, "memory")
                base = sync_side_base_ref(contract.contract_path, "memory")
            branch = run_git(repository, ["rev-parse", "--verify", f"{work_branch}^{{commit}}"])
            worktree_head = (
                run_git(worktree, ["rev-parse", "--verify", "HEAD"]) if worktree.exists() else None
            )
            active_merge = merge_head(worktree) if worktree.exists() else None
            sides.append(
                {
                    "side": side_name,
                    "repository": repository.as_posix(),
                    "worktree": worktree.as_posix(),
                    "worktreeExists": worktree.exists(),
                    "workBranch": work_branch,
                    "branchHead": branch.stdout.strip() if branch.returncode == 0 else None,
                    "worktreeHead": (
                        worktree_head.stdout.strip()
                        if worktree_head is not None and worktree_head.returncode == 0
                        else None
                    ),
                    "mergeHead": active_merge,
                    "refs": {
                        "base": {"name": base, "observed": read_ref(repository, base)},
                        "preSync": {
                            "name": backup,
                            "observed": read_ref(repository, backup),
                        },
                        "source": {
                            "name": source,
                            "observed": read_ref(repository, source),
                        },
                    },
                }
            )
        except (OSError, RuntimeError, ValueError) as error:
            sides.append(
                {
                    "side": side_name,
                    "state": "observation-unavailable",
                    "errorType": type(error).__name__,
                }
            )
    return {
        "contractPath": contract.contract_path.as_posix(),
        "contractBases": {
            "code": contract.code_base_commit,
            "memory": contract.memory_base_commit,
        },
        "sides": sides,
        "requiredChecks": [
            "preserve every incomplete authority ref until its meaning is established",
            "prove each work branch is at its pre-sync head or the exact pinned merge",
            "prove any MERGE_HEAD equals the pinned source ref before aborting it",
            "make contract bases equal the pinned base refs before retrying cancellation",
        ],
        "retry": {"tool": "worktree_sync", "args": _cancel_args(contract)},
    }


def _prove_rollback_possible(side: SyncSideRecord) -> None:
    ensure_temporary_worktree(side)
    worktree = Path(side.worktree)
    current = head_commit(worktree)
    active = merge_head(worktree)
    if active is not None:
        if current != side.preSyncHead or active != side.sourceCommit:
            raise SyncGitProofError(f"{side.side} active merge is not the pinned sync merge")
        return
    if current == side.preSyncHead:
        return
    parents = run_git(Path(side.repository), ["rev-list", "--parents", "-n", "1", current])
    cells = parents.stdout.split()
    exact_ff = current == side.sourceCommit and is_ancestor(
        Path(side.repository), side.preSyncHead, side.sourceCommit
    )
    exact_merge = parents.returncode == 0 and cells[1:] == [side.preSyncHead, side.sourceCommit]
    if not exact_ff and not exact_merge:
        raise SyncGitProofError(
            f"{side.side} history cannot prove an operation-owned merge with no later commits"
        )


def _require_completed_branches(record: SyncOperationRecord) -> None:
    """Prove finalization still addresses only the admitted operation-owned heads."""

    for side in (record.code, record.memory):
        if side is None:
            continue
        if side.state != "completed":
            raise SyncGitProofError(f"{side.side} is not completed at sync finalization")
        current = side_branch_head(side)
        if side.plan in {"already-current", "skip"}:
            if current != side.preSyncHead or side.resultHead != side.preSyncHead:
                raise SyncGitProofError(
                    f"{side.side} unchanged plan no longer has its admitted head"
                )
            continue
        if not side.resultHead or current != side.resultHead:
            raise SyncGitProofError(f"{side.side} work branch moved after its admitted sync merge")
        if not exact_created_head(side, current):
            raise SyncGitProofError(
                f"{side.side} final head is not the exact operation-created head"
            )
        validate_completed_side(side, current)


def _cancel_args(contract: WorktreeContract) -> dict[str, object]:
    return _cancel_args_for_path(contract.contract_path.as_posix())


def _cancel_args_for_path(contract_path: str) -> dict[str, object]:
    return {
        "contract_path": contract_path,
        "resolution_action": "cancel",
        "dry_run": False,
    }
