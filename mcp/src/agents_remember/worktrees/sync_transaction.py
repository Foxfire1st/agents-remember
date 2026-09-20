"""Resumable, contract-addressed mid-task source synchronization."""

from __future__ import annotations

from pathlib import Path

from agents_remember.models.knowledge.merge import AuthoredReconciliation
from agents_remember.models.worktree import SyncKnowledgeConflict, SyncSide
from agents_remember.worktrees.knowledge_conflict import RefusedKnowledgeStage
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.sync_transaction_authority import (
    authority_refs_exist,
    command_result,
    pin_authority,
    require_pinned_authority,
    require_record_contract,
    resolution_phase,
    restore_parked_wip,
    settle_resolved_parked_wip,
    side_record,
    source_pair,
    sync_contract_kind,
    update_record,
)
from agents_remember.worktrees.sync_transaction_git import (
    SyncGitProofError,
    apply_parked_wip,
    content_conflicts,
    continue_side_merge,
    drop_parked_wip,
    ensure_temporary_worktree,
    merge_head,
    park_worktree_wip,
    reconcile_side_merge,
    require_side_checkout,
    side_branch_head,
    side_merge_completed,
    start_side_merge,
    worktree_dirty_paths,
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
    parked_wip_validation_preview,
    quarantine_replay,
    reconcile_preview,
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

# The parked candidate's path sample kept in the journal. It is the proof set the restore
# re-checks per path; the exact count is journaled alongside it.
WIP_PATH_SAMPLE_LIMIT = 128


def sync_contract_under_authority(
    contract: WorktreeContract,
    args: WorktreeArgs,
    *,
    fetch: dict[str, object],
) -> WorktreeCommandResult:
    """Start, observe, continue, cancel, or recover one exact sync transaction.

    No lock is held across the call: an agent resolves retained conflicts in the
    ordinary worktree between calls.
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
    if args.resolution_action not in {None, "continue", "cancel", "reconcile"}:
        return command_result(
            2,
            "sync-input-invalid",
            "resolution_action must be exactly 'continue', 'cancel' or 'reconcile'.",
            fetch,
            invalidField="resolution_action",
        )
    if args.resolution_action == "reconcile" and args.knowledge_resolution is None:
        return command_result(
            2,
            "sync-input-invalid",
            "resolution_action='reconcile' authors one decision, so knowledge_resolution is "
            "required. The conflict's own diagnosis names the record and the decisions it admits.",
            fetch,
            invalidField="knowledge_resolution",
        )
    if args.resolution_action != "reconcile" and args.knowledge_resolution is not None:
        return command_result(
            2,
            "sync-input-invalid",
            "knowledge_resolution is read only with resolution_action='reconcile'.",
            fetch,
            invalidField="knowledge_resolution",
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
    prepared = _admit_participating_sides(contract, code, memory, args, fetch)
    if isinstance(prepared, WorktreeCommandResult):
        return prepared
    code, memory = prepared
    record = _new_sync_record(contract, args, predecessor, code, memory)
    pin_authority(record)
    store.write(record)
    for side in (record.code, record.memory):
        if side is not None and side.temporary and side.plan not in {"already-current", "skip"}:
            ensure_temporary_worktree(side)
    return _run_automatic(contract, store, record, fetch)


def _admit_participating_sides(
    contract: WorktreeContract,
    code: SyncSideRecord,
    memory: SyncSideRecord | None,
    args: WorktreeArgs,
    fetch: dict[str, object],
) -> tuple[SyncSideRecord, SyncSideRecord | None] | WorktreeCommandResult:
    """Refuse, preview, or park: everything that precedes the journaled admission."""

    preflight = _preflight_participating_sides(code, memory, fetch)
    if preflight is not None:
        return preflight
    if args.dry_run:
        return sync_preview(code, memory, fetch)
    return _park_participating_wip(contract, code, memory, fetch)


def _side_parks_wip(side: SyncSideRecord | None) -> bool:
    """Only a live worktree this transaction will actually move parks its candidate."""

    return side is not None and not side.temporary and side.plan not in {"already-current", "skip"}


def _park_participating_wip(
    contract: WorktreeContract,
    code: SyncSideRecord,
    memory: SyncSideRecord | None,
    fetch: dict[str, object],
) -> tuple[SyncSideRecord, SyncSideRecord | None] | WorktreeCommandResult:
    """Stash each dirty moving side's candidate into the transaction that will return it.

    The parked identity is journaled with the admission record, so the candidate a closeout
    leaves in its worktree is never lost to the carry. A stash that genuinely cannot be
    taken restores whatever was already parked and refuses with the typed sync refusal.
    """

    parked: dict[SyncSide, SyncSideRecord] = {}
    for side in (code, memory):
        if not _side_parks_wip(side):
            continue
        assert side is not None
        paths = worktree_dirty_paths(side)
        if not paths:
            continue
        try:
            stash = park_worktree_wip(
                side,
                message=_wip_stash_message(contract, side),
            )
        except SyncGitProofError as error:
            detail = _restore_already_parked(parked, error)
            return command_result(2, "sync-side-preflight-failed", detail, fetch)
        parked[side.side] = side.model_copy(
            update={
                "wipState": "parked",
                "wipStash": stash,
                "wipPaths": paths[:WIP_PATH_SAMPLE_LIMIT],
                "wipPathCount": len(paths),
            }
        )
    return parked.get("code", code), parked.get("memory", memory)


def _wip_stash_message(contract: WorktreeContract, side: SyncSideRecord) -> str:
    """A stash message a human can find: the operation, the side, and the exact contract."""

    return (
        f"agents-remember worktree_sync parked {side.side} WIP for {contract.task_id} "
        f"({contract.contract_path.as_posix()})"
    )


def _restore_already_parked(
    parked: dict[SyncSide, SyncSideRecord], error: SyncGitProofError
) -> str:
    """Undo a partial park before refusing, so no candidate is left without a journal."""

    stranded: list[str] = []
    for side in parked.values():
        try:
            state, conflicts = apply_parked_wip(side)
            if state != "applied" or conflicts:
                stranded.append(f"{side.side} ({side.wipStash})")
                continue
            drop_parked_wip(side)
        except SyncGitProofError:
            stranded.append(f"{side.side} ({side.wipStash})")
    detail = str(error)
    if stranded:
        detail = (
            f"{detail} The parked candidate remains in refs/stash for "
            f"{', '.join(stranded)}; recover it with git stash apply."
        )
    return detail


def _already_current_result(
    contract: WorktreeContract,
    code: SyncSideRecord,
    memory: SyncSideRecord | None,
    fetch: dict[str, object],
) -> WorktreeCommandResult | None:
    """Report a pair whose recorded base and work branches already carry the source.

    A memory branch that already descends from its source is current whatever its
    ``memory.md`` says: the ledger is derived state, its rebuild reports the rows it
    cannot resolve, and this surface does not keep a second copy of that judgement.
    """

    bases_current = code.sourceCommit == contract.code_base_commit and (
        memory is None or memory.sourceCommit == contract.memory_base_commit
    )
    branches_current = code.plan == "already-current" and (
        memory is None or memory.plan == "already-current"
    )
    if not (bases_current and branches_current):
        return None
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
    """Refuse only the moving sides whose candidate genuinely cannot be parked."""

    for side in (code, memory):
        if side is None or side.temporary or side.plan in {"already-current", "skip"}:
            continue
        try:
            require_side_checkout(side)
            _require_parkable_worktree(side)
        except SyncGitProofError as error:
            return command_result(2, "sync-side-preflight-failed", str(error), fetch)
    return None


def _require_parkable_worktree(side: SyncSideRecord) -> None:
    """A dirty moving side is parked; only an unsettleable index or worktree is refused."""

    conflicts = content_conflicts(side)
    if conflicts:
        raise SyncGitProofError(
            f"{side.side} sync cannot park a worktree with unmerged paths: "
            f"{', '.join(conflicts[:30])}"
        )
    if merge_head(Path(side.worktree)) is not None:
        raise SyncGitProofError(f"{side.side} has an active merge outside sync admission")


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
    if args.resolution_action == "reconcile":
        return _reconcile_preview(record, fetch)
    if args.resolution_action != "continue":
        return active_preview(record, fetch)
    if record.phase in {"code-resolution-required", "memory-resolution-required"}:
        side = _retained_side(record)
        if side.wipState == "restore-conflict":
            return parked_wip_validation_preview(side, fetch)
        return resolution_validation_preview(side, fetch)
    return manual_repair_result(
        "sync-resolution-not-required",
        "The active transaction has no retained conflict to continue.",
        record,
        fetch,
    )


def _reconcile_preview(
    record: SyncOperationRecord, fetch: dict[str, object]
) -> WorktreeCommandResult:
    """Preview the authored decision against the conflict the journal actually holds."""

    side = _retained_side(record)
    if side.knowledgeConflict is None:
        return manual_repair_result(
            "sync-resolution-not-active",
            "The active transaction retains no knowledge conflict to reconcile.",
            record,
            fetch,
        )
    return reconcile_preview(side, fetch)


def _resume_live(
    contract: WorktreeContract,
    args: WorktreeArgs,
    store: SyncOperationStore,
    record: SyncOperationRecord,
    fetch: dict[str, object],
) -> WorktreeCommandResult:
    if args.resolution_action == "cancel" or record.phase == "cancelling":
        return cancel_sync(contract, store, record, fetch)
    if args.resolution_action in {"continue", "reconcile"}:
        return _continue_resolution(contract, args, store, record, fetch)
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
    outcome = start_side_merge(side)
    if outcome.state == "resolution-required":
        updated_side = side.model_copy(
            update={
                "state": "resolution-required",
                "conflictFiles": outcome.conflicts,
                "knowledgeConflict": _knowledge_conflict(outcome.refused),
            }
        )
        return update_record(store, record, phase=resolution_phase(side_name), side=updated_side)
    updated_side = side.model_copy(
        update={"state": "completed", "resultHead": side_branch_head(side)}
    )
    record, updated_side, conflicted = restore_parked_wip(store, record, side_name, updated_side)
    if conflicted:
        return record
    return _advance_after_side(store, record, side_name, updated_side)


def _continue_resolution(
    contract: WorktreeContract,
    args: WorktreeArgs,
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
    if args.resolution_action == "reconcile":
        return _reconcile_knowledge_resolution(contract, args, store, record, fetch)
    side = _retained_side(record)
    if side.wipState == "restore-conflict":
        return _continue_parked_wip_restore(contract, store, record, fetch)
    return _finish_retained_merge(contract, store, record, fetch)


def _retained_side(record: SyncOperationRecord) -> SyncSideRecord:
    """The side whose conflict the retained phase names; the phase is the whole address."""

    side = record.code if record.phase == "code-resolution-required" else record.memory
    assert side is not None
    return side


def _finish_retained_merge(
    contract: WorktreeContract,
    store: SyncOperationStore,
    record: SyncOperationRecord,
    fetch: dict[str, object],
) -> WorktreeCommandResult:
    """Validate and commit a retained merge whose conflicts are all settled.

    This is the one continuation both a hand-staged resolution and an authored reconciliation end
    in, so a reconciled sync is a normal sync with one authored input rather than a second route
    through the transaction. The journaled diagnosis is cleared with the conflict: the agent was
    told what to reconcile, and the state that carried it is gone.
    """

    side_name: SyncSide = "code" if record.phase == "code-resolution-required" else "memory"
    side = _retained_side(record)

    try:
        result_head = continue_side_merge(side)
    except SyncGitProofError as error:
        refreshed = side.model_copy(update={"conflictFiles": content_conflicts(side)})
        record = update_record(store, record, phase=record.phase, side=refreshed)
        return manual_repair_result("sync-resolution-incomplete", str(error), record, fetch)
    completed = side.model_copy(
        update={
            "state": "completed",
            "resultHead": result_head,
            "conflictFiles": (),
            "knowledgeConflict": None,
            "knowledgeReconciliations": (),
        }
    )
    record, completed, conflicted = restore_parked_wip(store, record, side_name, completed)
    if conflicted:
        return resolution_required(record, fetch)
    record = _advance_after_side(store, record, side_name, completed)
    return _run_automatic(contract, store, record, fetch)


def _reconcile_knowledge_resolution(
    contract: WorktreeContract,
    args: WorktreeArgs,
    store: SyncOperationStore,
    record: SyncOperationRecord,
    fetch: dict[str, object],
) -> WorktreeCommandResult:
    """Author the caller's decision for the exact conflict the engine reported, then continue.

    The decision is checked against the journaled diagnosis *before* the merge is entered: the record
    it names must be the record the engine refused, and the decision must be one that conflict
    admits. A decision naming another row, or an overwrite on a conflict with no row, is refused
    here with the reason instead of being carried into a merge that would ignore it and hand back the
    same conflict.

    **Accepted decisions persist.** A decision that settles this conflict may reveal the next one, and
    the attempt that answers the next one carries every decision this side has already accepted --
    the journaled ones plus the one just authored. Each attempt therefore starts from the conflict
    the previous attempt actually reached rather than from the first one again. Without that, a merge
    with two conflicts alternated between the same two rows forever: the attempt answering the second
    re-refused the first, and the caller was offered a decision it had already made and that had
    already had its effect.

    **Progress or an actionable refusal.** A conflict that survives the attempt is compared against
    the decisions already accepted for it. A row a decision has already answered and that comes back
    anyway means the retraction could not hold this conflict, so the operation stops with the reason
    and the exact row instead of journaling the same decision a second time -- see
    :func:`_reconcile_progress_refusal`.
    """

    side = _retained_side(record)
    served: SyncSide = "code" if record.phase == "code-resolution-required" else "memory"
    refused = side.knowledgeConflict
    invalid = _reconcile_refusal(served, refused, args.knowledge_resolution, fetch)
    if invalid is not None:
        return invalid
    assert refused is not None and args.knowledge_resolution is not None
    accepted = (*side.knowledgeReconciliations, args.knowledge_resolution)
    try:
        remaining = reconcile_side_merge(side, refused.path, accepted)
    except SyncGitProofError as error:
        return manual_repair_result("sync-resolution-incomplete", str(error), record, fetch)
    if remaining is not None:
        cycling = _reconcile_progress_refusal(remaining, side.knowledgeReconciliations)
        if cycling is not None:
            refreshed = side.model_copy(
                update={"knowledgeConflict": _knowledge_conflict(remaining)}
            )
            record = update_record(store, record, phase=record.phase, side=refreshed)
            return manual_repair_result("sync-resolution-cycling", cycling, record, fetch)
        refreshed = side.model_copy(
            update={
                "conflictFiles": content_conflicts(side),
                "knowledgeConflict": _knowledge_conflict(remaining),
                "knowledgeReconciliations": accepted,
            }
        )
        record = update_record(store, record, phase=record.phase, side=refreshed)
        return resolution_required(record, fetch)
    return _finish_retained_merge(contract, store, record, fetch)


def _reconcile_progress_refusal(
    remaining: RefusedKnowledgeStage, accepted: tuple[AuthoredReconciliation, ...]
) -> str | None:
    """The refusal for a conflict an already-accepted decision answered and that came back anyway.

    ``accepted`` is what this side held **before** the attempt, so a row in it is a row a decision has
    already been applied to. Finding it here means the retraction did not hold -- the arriving change
    it retracted is required by another arriving change, so removing one re-exposes the other -- and
    the honest answer is to say that rather than to offer the same decision again. A row no accepted
    decision named is ordinary progress: the merge moved on to a conflict nobody has answered yet.
    """

    conflict = remaining.conflict
    if conflict is None or conflict.table is None or conflict.record_id is None:
        return None
    answered = {
        (one.table, one.record_id)
        for one in accepted
        if one.table is not None and one.record_id is not None
    }
    if (conflict.table, conflict.record_id) not in answered:
        return None
    return (
        f"The decision this merge already accepted for {conflict.table} {conflict.record_id} does "
        f"not settle it: the engine reports {conflict.code} on that row again after the authored "
        "decision was applied and retracted the arriving change, so retracting it re-exposes another "
        "arriving change that needs the same row. No further authored decision will make this merge "
        "converge; resolve the row in the worktree, stage it, then continue, or cancel the sync. The "
        "retained dataset is unchanged."
    )


def _reconcile_refusal(
    side_name: SyncSide,
    refused: SyncKnowledgeConflict | None,
    decision: AuthoredReconciliation | None,
    fetch: dict[str, object],
) -> WorktreeCommandResult | None:
    """Refuse an authored decision that does not answer the conflict this side retained."""

    problem = _reconcile_problem(side_name, refused, decision)
    if problem is None:
        return None
    state, detail = problem
    return command_result(2, state, detail, fetch, invalidField="knowledge_resolution")


def _reconcile_problem(
    side_name: SyncSide,
    refused: SyncKnowledgeConflict | None,
    decision: AuthoredReconciliation | None,
) -> tuple[str, str] | None:
    """Why this authored decision does not answer the retained conflict, or ``None``.

    Both facts the caller could get wrong are named in the answer: which record the engine actually
    refused -- carried in the journal, never re-derived from the databases -- and which decisions
    that conflict admits.
    """

    if side_name != "memory":
        return (
            "sync-input-invalid",
            "resolution_action='reconcile' settles a retained memory knowledge conflict; the code "
            "side merges text and carries no knowledge dataset.",
        )
    if refused is None or decision is None:
        return (
            "sync-resolution-not-active",
            "No retained knowledge conflict with a journaled diagnosis exists for this contract, so "
            "there is nothing to reconcile.",
        )
    if decision.decision not in refused.decisions:
        admitted = ", ".join(refused.decisions) if refused.decisions else "no authored decision"
        return (
            "sync-input-invalid",
            f"The conflict {refused.path} reports {_conflict_code(refused)} and admits {admitted}.",
        )
    if not _decision_matches(refused, decision):
        return (
            "sync-input-invalid",
            f"The decision must name the record the engine refused: {_refused_record(refused)}.",
        )
    return None


def _conflict_code(refused: SyncKnowledgeConflict) -> str:
    """The engine's own name for the conflict, or the seam's detail when there is no conflict."""

    if refused.conflict is not None:
        return refused.conflict.code
    return refused.refusal.code if refused.refusal is not None else "no attributed conflict"


def _refused_record(refused: SyncKnowledgeConflict) -> str:
    """Render the record the engine refused, as the diagnosis rendered it."""

    conflict = refused.conflict
    if conflict is None or conflict.table is None:
        return "the engine reported this conflict without a row, so it is decided with an empty row"
    return f"table {conflict.table}, record {conflict.record_id}"


def _decision_matches(refused: SyncKnowledgeConflict, decision: AuthoredReconciliation) -> bool:
    """Whether the decision answers exactly the conflict the engine reported for this side."""

    conflict = refused.conflict
    if conflict is None:
        return False
    if decision.record_id is None:
        return conflict.record_id is None and conflict.table is None
    return decision.table == conflict.table and decision.record_id == conflict.record_id


def _knowledge_conflict(refused: RefusedKnowledgeStage | None) -> SyncKnowledgeConflict | None:
    """Project the adapter's explanation into the journal and the public response.

    Every field is copied from the engine's own values; the decisions are read from the conflict the
    engine attributed, so the response offers exactly what that conflict admits. A path that never
    reached the adapter still carries this layer's reason, because "nothing settled" is not
    something an agent can act on.
    """

    if refused is None:
        return None
    return SyncKnowledgeConflict(
        path=refused.path,
        conflict=refused.conflict,
        refusal=refused.refusal,
        detail=refused.detail,
        decisions=list(refused.decisions),
    )


def _continue_parked_wip_restore(
    contract: WorktreeContract,
    store: SyncOperationStore,
    record: SyncOperationRecord,
    fetch: dict[str, object],
) -> WorktreeCommandResult:
    """Finish a parked-candidate reapply the agent resolved; the source merge already landed."""

    side_name: SyncSide = "code" if record.phase == "code-resolution-required" else "memory"
    side = record.code if side_name == "code" else record.memory
    assert side is not None
    try:
        record = settle_resolved_parked_wip(store, record, side_name, side)
    except SyncGitProofError as error:
        refreshed = side.model_copy(update={"conflictFiles": content_conflicts(side)})
        record = update_record(store, record, phase=record.phase, side=refreshed)
        return manual_repair_result("sync-resolution-incomplete", str(error), record, fetch)
    completed = side.model_copy(
        update={"state": "completed", "wipState": "restored", "conflictFiles": ()}
    )
    record = _advance_after_side(store, record, side_name, completed)
    return _run_automatic(contract, store, record, fetch)


def _reconcile_completed_sides(
    store: SyncOperationStore, record: SyncOperationRecord
) -> SyncOperationRecord:
    """Adopt a side whose merge is already committed, returning any candidate it parks."""

    if record.phase == "running-code" and _side_live_complete(record.code):
        side = record.code.model_copy(
            update={"state": "completed", "resultHead": side_branch_head(record.code)}
        )
        record, side, conflicted = restore_parked_wip(store, record, "code", side)
        if not conflicted:
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
        record, side, conflicted = restore_parked_wip(store, record, "memory", side)
        if not conflicted:
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
