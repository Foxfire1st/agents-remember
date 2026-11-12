"""Finalization, rollback, and damaged-journal escape for worktree sync."""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path

from agents_remember.kernel.git_command import run_git
from agents_remember.models.worktree import SyncSide
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.git import head_commit, is_ancestor
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.services import WorktreeServicesUnboundError, worktree_services
from agents_remember.worktrees.sync_source_strategy import (
    source_strategy,
    sync_log_entry,
    target_code_base,
    target_memory_base,
)
from agents_remember.worktrees.sync_transaction_authority import (
    authority_refs_exist,
    command_result,
    complete_side_from_refs,
    delete_authority,
    reload_contract,
    remove_temporary_worktrees,
    require_contract_bases_unchanged,
    require_finalizable_contract,
    require_parked_wip_settled,
    restore_cancelled_wip,
    side_locations,
    sync_contract_kind,
    update_record,
)
from agents_remember.worktrees.sync_transaction_completion import completed_sync_result
from agents_remember.worktrees.sync_transaction_git import (
    CancelSaveError,
    CancelTreeAssessment,
    SavedUnprovenState,
    SyncGitProofError,
    assess_retained_tree,
    assess_stash_tree,
    delete_pinned_ref,
    discard_owned_reapply,
    discard_saved_unproven,
    drop_parked_wip,
    ensure_temporary_worktree,
    exact_created_head,
    merge_head,
    read_ref,
    read_saved_unproven_state,
    remove_temporary_worktree,
    rollback_side,
    save_unproven_state,
    side_branch_head,
)
from agents_remember.worktrees.sync_transaction_parking import _adopt_orphaned_park
from agents_remember.worktrees.sync_transaction_sources import (
    delete_default_source_refs,
    recovered_source_strategy,
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

logger = logging.getLogger(__name__)


def finalize_sync(
    contract: WorktreeContract,
    store: SyncOperationStore,
    record: SyncOperationRecord,
    fetch: dict[str, object],
) -> WorktreeCommandResult:
    current = reload_contract(contract)
    require_finalizable_contract(current, record)
    _require_completed_branches(record)
    require_parked_wip_settled(record)
    if not source_strategy(record.syncSource).finalized(current, record):
        current = replace(
            current,
            code_base_commit=target_code_base(record),
            memory_base_commit=target_memory_base(record),
            sync_log=(*current.sync_log, {"at": operation_stamp(), **sync_log_entry(record)}),
        )
        write_contract(current.contract_path, current)
    # Publish the terminal transaction before deleting its recovery authority. A crash at
    # any later cut leaves a terminal record whose residue cleanup is strictly idempotent.
    record = update_record(store, record, phase="completed")
    _retire_restored_stashes(record)
    remove_temporary_worktrees(record)
    delete_authority(record)
    return with_recomputed_worklist(completed_sync_result(current, record, fetch), current)


def with_recomputed_worklist(
    result: WorktreeCommandResult, contract: WorktreeContract
) -> WorktreeCommandResult:
    """Attach the recomputed worklist's summary to a completed sync's result, where one applies.

    Used by the finalization and by the ``continue`` replay of a completed generation, so both
    report the worklist of the base pair the sync wrote. The result is otherwise untouched.
    """

    worklist = recompute_knowledge_worklist(contract)
    if worklist is not None:
        result.payload["knowledgeWorklist"] = worklist
    return result


def recompute_knowledge_worklist(contract: WorktreeContract) -> dict[str, object] | None:
    """MIK-R08 rule 8: a completed managed sync recomputes the leaf's worklist.

    The sync moved the base pair, so B and K_B moved with it. The bound port computes and persists
    the worklist and returns its summary; with no port bound (or no worklist applicable, every
    unconverted leaf) the sync result is exactly what it was. It runs after the completed journal is
    published, so nothing it does may fail the sync: any failure here -- reading the contract, the
    port itself -- leaves the summary out and the completed result as it was.
    """

    try:
        port = worktree_services().knowledge_worklist
        return None if port is None else port.recompute(reload_contract(contract))
    except WorktreeServicesUnboundError:
        return None
    except Exception:  # a worklist failure never fails a completed sync (MIK-R08 rule 8)
        return None


def cancel_sync(
    contract: WorktreeContract,
    store: SyncOperationStore,
    record: SyncOperationRecord,
    fetch: dict[str, object],
) -> WorktreeCommandResult:
    _cancelled_contract(reload_contract(contract), record)
    assessments = _precheck_cancel_sides(record)
    # MIK-R61 rule 12: no way out is manual. Later work the transaction does not
    # own is saved first exactly like unproven state then the cancel completes,
    # naming the saved files and digests; the cancel never refuses over bytes
    # no stash and no commit holds.
    assessments = _assessments_saving_later_work(assessments)
    saved, stopped = _recover_and_save_cancel_state(contract, record, assessments, fetch)
    if stopped is not None:
        return stopped
    record = update_record(store, record, phase="cancelling")
    for side in (record.memory, record.code):
        if side is not None:
            assessment = assessments.get(side.side)
            if assessment is not None and (
                assessment.needs_discard or side.wipState == "restore-conflict"
            ):
                discard_owned_reapply(side, assessment.owned_untracked)
            if side.plan in {"already-current", "skip"}:
                if side_branch_head(side) != side.preSyncHead:
                    raise SyncGitProofError(
                        f"{side.side} unchanged branch moved after sync admission"
                    )
            else:
                rollback_side(side)
    try:
        record = restore_cancelled_wip(store, record)
    except SyncGitProofError as error:
        return manual_repair_result("sync-cancel-wip-restore-failed", str(error), record, fetch)
    current = reload_contract(contract)
    restored = _cancelled_contract(current, record)
    if restored != current:
        write_contract(current.contract_path, restored)
    require_contract_bases_unchanged(restored, record)
    remove_temporary_worktrees(record)
    record = update_record(store, record, phase="cancelled")
    _retire_restored_stashes(record)
    delete_authority(record)
    return command_result(
        0,
        "sync-cancelled",
        _cancelled_summary(saved, assessments),
        fetch,
    )


def _recover_and_save_cancel_state(
    contract: WorktreeContract,
    record: SyncOperationRecord,
    assessments: dict[str, CancelTreeAssessment],
    fetch: dict[str, object],
) -> tuple[dict[str, SavedUnprovenState], WorktreeCommandResult | None]:
    try:
        saved = read_saved_unproven_state(
            contract.worktree_group / "scratch",
            record.generation,
            [side for side in (record.code, record.memory) if side is not None],
        )
    except CancelSaveError as error:
        return {}, _refuse_cancel_for_unsaved_state(contract, record, fetch, error)
    if not any(assessment.unproven_paths for assessment in assessments.values()):
        return saved, None
    newly_saved, stopped = _save_and_discard_unproven(contract, record, assessments, fetch)
    if stopped is not None:
        return saved, stopped
    for name, state in newly_saved.items():
        prior = saved.get(name)
        saved[name] = replace(state, files=(*prior.files, *state.files)) if prior else state
    return saved, None


def _cancelled_contract(
    contract: WorktreeContract, record: SyncOperationRecord
) -> WorktreeContract:
    """Undo only this unfinished generation's own atomic contract finalization.

    A kill after write_contract but before the completed journal leaves this
    exact tail. Prove its pair and journaled heads before any cancel mutation;
    removing it is repeatable even if the cancel dies before its terminal write.
    The source strategy says what the finalization moved and how its ownership is proven.
    """
    strategy = source_strategy(record.syncSource)
    if strategy.finalization_absent(contract, record):
        return contract
    require_finalizable_contract(contract, record)
    return strategy.without_finalization(contract, record)


def _assessments_saving_later_work(
    assessments: dict[str, CancelTreeAssessment],
) -> dict[str, CancelTreeAssessment]:
    """Fold later work into the save set: the cancel saves it first, never refuses it.

    The saved whole-tree patches already capture every byte the worktree holds,
    so a later path joins the named save list and the discard that follows may
    clear it; the cancelled summary names what was saved with its digests.
    """

    merged: dict[str, CancelTreeAssessment] = {}
    for name, assessment in assessments.items():
        if not assessment.later_work:
            merged[name] = assessment
            continue
        if assessment.merge_fault:
            logger.error("sync cancel saves first over merge fault: %s", assessment.merge_fault)
        merged[name] = replace(
            assessment,
            unproven_paths=tuple(
                sorted(set(assessment.unproven_paths) | set(assessment.later_work))
            ),
        )
    return merged


def _save_and_discard_unproven(
    contract: WorktreeContract,
    record: SyncOperationRecord,
    assessments: dict[str, CancelTreeAssessment],
    fetch: dict[str, object],
) -> tuple[dict[str, SavedUnprovenState], WorktreeCommandResult | None]:
    """Save unattributable state before discarding it (MIK-R61 rule 12).

    Returns the saved states and no stop on success; a refused save or a failed discard
    returns no states and the answer that leaves the sync open.
    """

    # A refused save changes nothing and leaves the sync open.
    try:
        saved = save_unproven_state(
            contract.worktree_group / "scratch",
            record.generation,
            [side for side in (record.code, record.memory) if side is not None],
            assessments,
        )
    except CancelSaveError as error:
        return {}, _refuse_cancel_for_unsaved_state(contract, record, fetch, error)
    try:
        for side in (record.code, record.memory):
            if side is not None and side.side in saved:
                discard_saved_unproven(side, saved[side.side].untracked)
    except SyncGitProofError as error:
        return (
            {},
            manual_repair_result(
                "sync-cancel-saved-discard-failed",
                f"{error} The unattributable state was saved first and nothing of it was "
                "lost; resolve the named failure and run worktree_sync with "
                "resolution_action='cancel' again.",
                record,
                fetch,
            ),
        )
    return saved, None


def _cancelled_summary(
    saved: dict[str, SavedUnprovenState],
    assessments: dict[str, CancelTreeAssessment],
) -> str:
    """The cancelled summary, naming the saved files where the cancel saved first."""

    base = "The exact sync transaction was cancelled and every participating branch was restored."
    if not saved:
        return base
    named = "; ".join(
        f"{state.side}: " + ", ".join(f"{path} (sha256:{digest})" for path, digest in state.files)
        for state in saved.values()
    )
    summary = (
        f"{base} The saved files hold what the worktrees contained beyond the sync's own "
        f"record, which the cancel could not attribute to the sync: {named}."
    )
    faults = sorted(
        {assessment.merge_fault for assessment in assessments.values() if assessment.merge_fault}
    )
    if faults:
        summary += " The cancel could not own these paths because " + "; ".join(faults) + "."
    return summary


def _refuse_cancel_for_unsaved_state(
    contract: WorktreeContract,
    record: SyncOperationRecord,
    fetch: dict[str, object],
    error: CancelSaveError,
) -> WorktreeCommandResult:
    """Refuse a cancel whose unattributable state cannot be saved (MIK-R61 rule 12).

    The one refusal of a cancel that leaves the sync open: nothing was changed, and the
    answer names the path it could not write and the folder to free.
    """

    folder = (contract.worktree_group / "scratch").as_posix()
    return command_result(
        2,
        "sync-cancel-save-failed",
        f"The sync cannot be cancelled: {error} Nothing was changed; the sync record "
        f"keeps its phase. Free space in {folder} and run worktree_sync with "
        "resolution_action='cancel' again.",
        fetch,
        phase=record.phase,
        cancelArgs=_cancel_args(contract),
    )


def _retire_restored_stashes(record: SyncOperationRecord) -> None:
    """Drop the kept stash entries of restored sides at terminal finalization.

    Restore and settle keep every stash so a later cancel can still return the
    parked bytes; only a terminal transaction retires them, entry by entry, and
    only the entry its own record names. A missing entry is already gone.
    """

    for side in (record.code, record.memory):
        if side is not None and side.wipState == "restored" and side.wipStash:
            drop_parked_wip(side)


def _precheck_cancel_sides(record: SyncOperationRecord) -> dict[str, CancelTreeAssessment]:
    """Prove a cancel can run before the record moves: a refused cancel changes nothing."""

    assessments: dict[str, CancelTreeAssessment] = {}
    for side in (record.code, record.memory):
        if side is None:
            continue
        if side.plan in {"already-current", "skip"}:
            if side_branch_head(side) != side.preSyncHead:
                raise SyncGitProofError(f"{side.side} unchanged branch moved after sync admission")
            continue
        if merge_head(Path(side.worktree)) is not None or side.wipState == "restore-conflict":
            carried = _cancel_carried_head(side)
            assessments[side.side] = assess_retained_tree(side, carried)
        else:
            assessments[side.side] = assess_stash_tree(side, _cancel_carried_head(side))
    return assessments


def _cancel_carried_head(side: SyncSideRecord) -> str | None:
    """The head the parked reapply ran onto, or ``None`` where it never moved."""

    worktree = Path(side.worktree)
    current = head_commit(worktree)
    active = merge_head(worktree)
    if active is not None:
        if current != side.preSyncHead or active != side.sourceCommit:
            raise SyncGitProofError(f"{side.side} active merge is outside sync authority")
        return side.sourceCommit
    if current == side.preSyncHead:
        return None
    if exact_created_head(side, current):
        return current
    raise SyncGitProofError(
        f"{side.side} has later or unrelated commits; automatic rollback is unsafe"
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
    """Finish only idempotent temp/ref/stash cleanup left after a terminal publication."""

    remove_temporary_worktrees(record)
    _retire_restored_stashes(record)
    delete_authority(record)
    # Re-publish the same strict bytes after cleanup so a truncated prior write cannot
    # masquerade as successful residue collection.
    store.write(record)


def manual_repair_result(
    state: str,
    summary: str,
    record: SyncOperationRecord,
    fetch: dict[str, object],
    *,
    omissions: dict[str, object] | None = None,
) -> WorktreeCommandResult:
    return command_result(
        2,
        state,
        summary,
        fetch,
        phase=record.phase,
        cancelArgs=_cancel_args_for_path(record.contractPath),
        **({"omissions": omissions} if omissions is not None else {}),
        **(
            {
                "validationRefusal": record.validationRefusal.model_dump(mode="json"),
            }
            if record.validationRefusal is not None
            else {}
        ),
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
        recovered: dict[str, SyncSideRecord] = {}
        for side in (memory, code):
            if side is not None:
                adopted = _adopt_orphaned_park(contract, side, fetch, set())
                if isinstance(adopted, WorktreeCommandResult):
                    return adopted
                recovered_side = adopted if adopted is not None else side
                _prove_rollback_possible(recovered_side)
                recovered[side.side] = recovered_side
        code = recovered["code"]
        memory = recovered.get("memory")
        stamp = operation_stamp()
        record = SyncOperationRecord(
            generation=1,
            contractPath=contract.contract_path.resolve(strict=False).as_posix(),
            taskId=contract.task_id,
            contractKind=sync_contract_kind(contract),
            codeBaseFrom=code.baseCommit,
            memoryBaseFrom=memory.baseCommit if memory is not None else contract.memory_base_commit,
            **recovered_source_strategy(contract, code).record_fields(contract),
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
            delete_default_source_refs(side, contract.contract_path)
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


def _cancel_args(contract: WorktreeContract) -> dict[str, object]:
    return _cancel_args_for_path(contract.contract_path.as_posix())


def _cancel_args_for_path(contract_path: str) -> dict[str, object]:
    return {
        "contract_path": contract_path,
        "resolution_action": "cancel",
        "dry_run": False,
    }
