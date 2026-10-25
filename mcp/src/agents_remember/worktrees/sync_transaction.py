"""Resumable, contract-addressed mid-task source synchronization."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Literal

from agents_remember.models.worktree import SyncSide
from agents_remember.worktrees.cutover_lock import (
    CUTOVER_LOCK_CODE,
    legacy_format_refusal,
)
from agents_remember.worktrees.knowledge_crossing import CrossingSyncError, merge_base
from agents_remember.worktrees.knowledge_validation import (
    LayoutProbeError,
    PairedCode,
    has_layout_marker,
)
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.git import branch_commit
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.sync_transaction_authority import (
    authority_refs_exist,
    command_result,
    pin_authority,
    record_validation_refusal,
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
    SyncFastForwardValidationError,
    SyncGitProofError,
    SyncKnowledgeValidationError,
    applied_restore_failures,
    apply_parked_wip,
    capture_retained_digests,
    clear_stale_git_locks,
    commit_check_report,
    content_conflicts,
    continue_side_merge,
    drop_parked_wip,
    ensure_temporary_worktree,
    merge_head,
    park_worktree_wip,
    recapture_resumed_merge,
    require_side_checkout,
    side_branch_head,
    side_merge_completed,
    staged_merge_repair_debt,
    start_side_merge,
    validate_memory_fast_forward,
    worktree_dirty_paths,
)
from agents_remember.worktrees.sync_transaction_parking import (
    WIP_PATH_SAMPLE_LIMIT,
    _adopt_orphaned_park,
    _orphaned_park_stashes,
    _refuse_orphaned_park,
    _wip_stash_message,
    orphaned_cancel_record,
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
    if args.resolution_action == "cancel":
        orphaned = orphaned_cancel_record(contract, args, store, record, fetch)
        if isinstance(orphaned, WorktreeCommandResult):
            return orphaned
        if orphaned is not None:
            return _resume_active(contract, args, store, orphaned, fetch)
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
            "resolution_action must be exactly 'continue' or 'cancel'. Knowledge is text: a "
            "conflicted knowledge file is resolved in the worktree and staged, then continued.",
            fetch,
            invalidField="resolution_action",
        )
    return None


def _clear_entry_git_locks(
    contract: WorktreeContract,
    code: SyncSideRecord,
    memory: SyncSideRecord | None,
) -> None:
    """Drop stale index locks before admitting or resuming a sync (MIK-R61 rule 12).

    A previous run killed inside a git operation leaves its worktree's
    ``index.lock`` behind, and every later git command fails until it is gone.
    The removal only takes locks no live process holds open, and it runs at
    both entries — fresh admission and journal resume — because the crash can
    strand either. The cleared paths go to the log; the journal's phase
    already tells the operator the run restarted.
    """

    cleared = clear_stale_git_locks(
        [Path(code.worktree), *([Path(memory.worktree)] if memory is not None else [])]
    )
    if cleared:
        logging.getLogger(__name__).info(
            "sync %s cleared %d stale git lock(s): %s",
            contract.task_id,
            len(cleared),
            ", ".join(cleared),
        )


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
    _clear_entry_git_locks(contract, code, memory)
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
    # The exact parked candidates must be durable before the first ref is pinned:
    # a partial admission can then finish its idempotent pins on cancel or resume.
    store.write(record)
    pin_authority(record)
    for side in (record.code, record.memory):
        if side is not None and side.temporary and side.plan not in {"already-current", "skip"}:
            ensure_temporary_worktree(side)
    return _run_automatic(contract, store, record, fetch)


def _sync_side_outcome(repository: Path, memory: SyncSideRecord) -> tuple[str, str]:
    """How the preflight judges the sync's exact sides, with the judged sides named.

    Returns the outcome (``admit``, ``legacy`` or ``out-of-format``) and the exact
    judged sides as ``preSyncHead <sha>, sourceCommit <sha>`` plus
    ``merge base <sha>`` for a merge, so refusals name what was judged.
    ``admit``: already current or ``skip-memory`` never reach here; a fast-forward that takes a
    converted source converts by taking it; a merge with a converted side is the crossing sync;
    all converted sides run as today. ``legacy``: a fast-forward of two unconverted sides, or a
    merge of three unconverted sides, refuses with the one ``legacy-format`` message.
    ``out-of-format``: a fast-forward from an unconverted source onto a converted target refuses,
    since the source would take the line out of the text format. Probes may raise
    :class:`LayoutProbeError` or :class:`CrossingSyncError`, which refuse by name.
    """

    if memory.plan == "merge":
        base = merge_base(repository, memory.preSyncHead, memory.sourceCommit)
        sides = (
            f"preSyncHead {memory.preSyncHead}, sourceCommit {memory.sourceCommit}, "
            f"merge base {base}"
        )
        if any(
            has_layout_marker(repository, side)
            for side in (base, memory.preSyncHead, memory.sourceCommit)
        ):
            return "admit", sides  # the crossing sync, as today (MIK-R24 rule 8), or all converted
        return "legacy", sides  # the plain Git merge of memory is gone
    sides = f"preSyncHead {memory.preSyncHead}, sourceCommit {memory.sourceCommit}"
    states = {
        side: has_layout_marker(repository, side)
        for side in (memory.preSyncHead, memory.sourceCommit)
    }
    if all(states.values()):
        return "admit", sides
    if not states[memory.preSyncHead] and states[memory.sourceCommit]:
        return "admit", sides  # the line converts by taking the converted source
    if states[memory.preSyncHead] and not states[memory.sourceCommit]:
        return "out-of-format", sides
    return "legacy", sides


def _cutover_locked(
    memory: SyncSideRecord | None, fetch: dict[str, object]
) -> WorktreeCommandResult | None:
    """MIK-R46 rule 3: judge the exact sides of the sync before anything moves.

    The preflight probes ``preSyncHead``, ``sourceCommit`` and, for a merge, their merge base
    (:func:`_sync_side_outcome`):

    * already current, or ``skip-memory``: any sides, as today (no memory side moves);
    * fast-forward, target unconverted and source converted: admitted, as today -- the line
      converts by taking the converted source;
    * fast-forward, both unconverted: refused, ``legacy-format``;
    * fast-forward, target converted and source unconverted: refused -- the source would take
      the line out of the text format;
    * merge, base/own/incoming all unconverted: refused, ``legacy-format`` -- the plain Git
      merge of memory is gone;
    * merge, at least one unconverted and one converted: the crossing sync, as today;
    * fast-forward or merge, all converted: as today.

    A sync that was already in flight is continued or cancelled, as today. A probe Git cannot
    answer refuses by name and is never read as converted or unconverted.
    """

    if memory is None or memory.plan in {"already-current", "skip"}:
        return None
    repository = Path(memory.repository)
    line = f"{memory.workBranch} ({memory.worktree})"
    try:
        outcome, sides = _sync_side_outcome(repository, memory)
    except (LayoutProbeError, CrossingSyncError) as error:
        return command_result(
            2,
            f"sync-{CUTOVER_LOCK_CODE}",
            "The memory sync is refused: it cannot tell whether a memory side holds the layout "
            f"marker ({error}), and an unanswered probe is never read as converted or unconverted "
            "memory",
            fetch,
        )
    if outcome == "admit":
        return None
    if outcome == "out-of-format":
        return command_result(
            2,
            f"sync-{CUTOVER_LOCK_CODE}",
            "The memory sync is refused: its source is unconverted and would take a "
            "converted line out of the text format.",
            fetch,
            detail=legacy_format_refusal(
                repository,
                operation="worktree_sync",
                subject=(
                    f"the memory sync of {line} ({sides}): its target holds converted memory "
                    "(knowledge/layout.json) and its source does not, so a fast-forward "
                    "would take the line out of the text format -- convert the source "
                    "line first"
                ),
            ),
        )
    detail = legacy_format_refusal(
        repository,
        operation="worktree_sync",
        subject=f"the memory sync of {line} ({sides})",
    )
    return command_result(
        2,
        f"sync-{CUTOVER_LOCK_CODE}",
        "The memory sync is refused: its memory is unconverted and must cross the boundary first.",
        fetch,
        detail=detail,
    )


def _admit_participating_sides(
    contract: WorktreeContract,
    code: SyncSideRecord,
    memory: SyncSideRecord | None,
    args: WorktreeArgs,
    fetch: dict[str, object],
) -> tuple[SyncSideRecord, SyncSideRecord | None] | WorktreeCommandResult:
    """Refuse, preview, or park: everything that precedes the journaled admission."""

    preflight = _cutover_locked(memory, fetch) or _preflight_participating_sides(
        code, memory, fetch
    )
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

    A death between a stash push and the admission journal leaves an orphan stash no
    record names; the next admission either adopts it (clean tree, exact contract
    message, stash parent is the pinned head) or refuses naming it, so a sync never
    proceeds without its owner's bytes (MIK-R61 rules 7-8).
    """

    named = _predecessor_named_stashes(contract)
    parked: dict[SyncSide, SyncSideRecord] = {}
    for side in (code, memory):
        if not _side_parks_wip(side):
            continue
        assert side is not None
        paths = worktree_dirty_paths(side)
        if not paths:
            adopted = _adopt_orphaned_park(contract, side, fetch, named)
            if isinstance(adopted, WorktreeCommandResult):
                return adopted
            if adopted is not None:
                parked[side.side] = adopted
            continue
        orphans = _orphaned_park_stashes(contract, side, named)
        if orphans:
            return _refuse_orphaned_park(side, orphans, fetch)
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


def _predecessor_named_stashes(contract: WorktreeContract) -> set[str]:
    """Stash identities the live journal still names (never orphans).

    Entries a stranded earlier generation disclosed in its own answer stay the
    operator's; only stashes no record names can be orphans of a dead admission.
    A missing or unreadable journal names nothing.
    """

    try:
        predecessor = SyncOperationStore(contract.worktree_group).read()
    except (SyncJournalReadError, OSError, ValueError):
        return set()
    if not isinstance(predecessor, SyncOperationRecord):
        return set()
    return {
        side.wipStash
        for side in (predecessor.code, predecessor.memory)
        if side is not None and side.wipStash
    }


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
            # The stash is dropped only after every parked path is proven restored
            # (MIK-R61 rule 8): the full stash inventory, never the journal's sample.
            failures = applied_restore_failures(side, side_branch_head(side))
            if failures:
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
    initial_admission = record.phase == "running-code" and all(
        side.state == "pending" and not side.resultHead
        for side in (record.code, record.memory)
        if side is not None
    )
    require_pinned_authority(
        record,
        allow_expected_missing=record.phase == "finalizing" or initial_admission,
    )
    _clear_entry_git_locks(contract, record.code, record.memory)
    retired = _retired_knowledge_blocker(record)
    if retired is not None and args.resolution_action != "cancel" and not args.dry_run:
        return manual_repair_result(
            "sync-retired-knowledge-in-flight",
            retired,
            record,
            fetch,
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
    if initial_admission:
        pin_authority(record)
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


def _retired_knowledge_blocker(record: SyncOperationRecord) -> str | None:
    """Refuse a continued sync whose journal still holds a database conflict (MIK-R61 rule 6).

    The sync was started by a build that merged the knowledge database; cancelling it with
    ``worktree_sync`` works and a fresh sync starts afterwards. Empty values were already
    dropped on read, so only a non-empty survivor refuses.
    """

    holders = sorted(
        side.side
        for side in (record.code, record.memory)
        if side is not None and (side.knowledgeConflict or side.knowledgeReconciliations)
    )
    if not holders:
        return None
    return (
        "This sync was started by a build that merged the knowledge database: its journal "
        f"still holds an unresolved database conflict on {', '.join(holders)}. Cancel it "
        "with worktree_sync (resolution_action='cancel') and start it again."
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
        if record.phase == "running-memory":
            record = _run_side(store, record, "memory")
        return _finish_automatic(contract, store, record, fetch)
    except SyncFastForwardValidationError as error:
        return _fast_forward_refused(store, record, error, fetch)
    except SyncKnowledgeValidationError as error:
        return _knowledge_validation_refused(store, error, record, fetch)
    except SyncGitProofError as error:
        return manual_repair_result("sync-git-proof-failed", str(error), record, fetch)


def _finish_automatic(
    contract: WorktreeContract,
    store: SyncOperationStore,
    record: SyncOperationRecord,
    fetch: dict[str, object],
) -> WorktreeCommandResult:
    """Answer a side run that stopped: a retained conflict, the finalization, or a refusal."""

    if record.phase in {"code-resolution-required", "memory-resolution-required"}:
        return resolution_required(record, fetch)
    if record.phase == "finalizing":
        return finalize_sync(contract, store, record, fetch)
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
    if side_name == "memory" and side.plan == "fast-forward":
        # A fast-forward adopts its incoming tree as it is (MIK-R61 rule 4): a converted
        # incoming tree is judged before anything moves, and a refusal moves no ref.
        debt = validate_memory_fast_forward(side, _paired_code(record))
        if debt:
            side = side.model_copy(update={"repairDebt": debt})
    ensure_temporary_worktree(side)
    resumed_merge = merge_head(Path(side.worktree)) is not None
    paired = _paired_code(record)
    owner: tuple[Literal["leaf", "master"], str] = (
        "leaf" if record.contractKind == "leaf" else "master",
        record.taskId,
    )
    try:
        outcome = start_side_merge(
            side,
            paired_code=paired,
            crossing_owner=owner,
        )
    except SyncKnowledgeValidationError:
        if not resumed_merge and side_name == "memory":
            record = _persist_fresh_validator_stop(store, record)
        raise
    if outcome.state == "resolution-required":
        if resumed_merge:
            retained = recapture_resumed_merge(side, paired_code=paired, owner=owner)
        else:
            retained = capture_retained_digests(side, include_untracked=False)
        updated_side = side.model_copy(
            update={
                "state": "resolution-required",
                "conflictFiles": outcome.conflicts,
                "crossingReport": outcome.crossing_report,
                "retainedTreeDigests": retained,
            }
        )
        return update_record(store, record, phase=resolution_phase(side_name), side=updated_side)
    updated_side = side.model_copy(
        update={
            "state": "completed",
            "resultHead": side_branch_head(side),
            "crossingReport": outcome.crossing_report,
            "repairDebt": outcome.repair_debt or side.repairDebt,
        }
    )
    record, updated_side, conflicted = restore_parked_wip(store, record, side_name, updated_side)
    if conflicted:
        return record
    return _advance_after_side(store, record, side_name, updated_side)


def _persist_fresh_validator_stop(
    store: SyncOperationStore, record: SyncOperationRecord
) -> SyncOperationRecord:
    """Journal the staged memory tree the knowledge validator refused.

    Only a merge this call performed is fingerprinted: it crossed no owner
    window, so the staged output is operation-created and a later cancel can
    tell it from owner edits. A resumed merge or an agent-resolved tree is never
    persisted here; their cancel refuses safely instead. A capture failure
    journals nothing rather than masking the validator refusal.
    """

    if record.memory is None:
        return record
    try:
        digests = capture_retained_digests(record.memory, include_untracked=False)
    except SyncGitProofError:
        return record
    updated = record.memory.model_copy(update={"retainedTreeDigests": digests})
    return update_record(store, record, phase=record.phase, side=updated, side_name="memory")


def _knowledge_validation_refused(
    store: SyncOperationStore,
    error: SyncKnowledgeValidationError,
    record: SyncOperationRecord,
    fetch: dict[str, object],
) -> WorktreeCommandResult:
    """The validator refused the staged memory merge (MIK-R22): name every violation and the way on.

    The merge stays staged and the phase is unchanged, so the same call that reached the refusal
    resumes it: ``continue`` for a retained conflict, a plain rerun for the automatic merge.
    The refusal is journaled with its violations (MIK-R61 rule 5); a continued sync validates
    again and clears it when the tree passes.
    """

    refreshed = record_validation_refusal(
        store,
        record,
        kind="staged-merge",
        path="staged memory merge tree",
        message=str(error),
    )
    worktree = record.memory.worktree if record.memory is not None else "the memory worktree"
    retained = record.phase == "memory-resolution-required"
    rerun = "worktree_sync with resolution_action='continue'" if retained else "worktree_sync"
    summary = (
        f"{error}\nRecovery: repair these files in {worktree}, stage them (git add), then rerun "
        f"{rerun}; or cancel the sync with resolution_action='cancel'."
    )
    return manual_repair_result("sync-knowledge-validation-refused", summary, refreshed, fetch)


def _fast_forward_refused(
    store: SyncOperationStore,
    record: SyncOperationRecord,
    error: SyncFastForwardValidationError,
    fetch: dict[str, object],
) -> WorktreeCommandResult:
    """The validator refused a fast-forward's incoming tree (MIK-R61 rule 4).

    Nothing moved: the violation is on the source line. A rerun would judge the same
    pinned tip again, so the way on is repairing that line, ending this sync with
    cancel, and starting a new sync onto the repaired line. The refusal is journaled
    with its violations (MIK-R61 rule 5).
    """

    source = record.memory.sourceCommit if record.memory is not None else "the source commit"
    branch = record.memory.sourceBranch if record.memory is not None else "the source branch"
    refreshed = record_validation_refusal(
        store,
        record,
        kind="fast-forward",
        path=f"incoming memory tree {source}",
        message=str(error),
    )
    summary = (
        f"{error}\nRecovery: repair the source line ({branch} at {source}), then end this "
        "sync with worktree_sync and resolution_action='cancel' and start a new sync onto "
        "the repaired line."
    )
    return manual_repair_result("sync-knowledge-validation-refused", summary, refreshed, fetch)


def _paired_code(record: SyncOperationRecord) -> PairedCode | None:
    """The code commit a memory merge is validated against: the code side's settled result."""

    code = record.code
    if code.state != "completed" or not code.resultHead:
        return None
    return PairedCode(repository=Path(code.repository), commit=code.resultHead)


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
    moved = _moved_source_sides(record)
    if moved is not None:
        return manual_repair_result(
            "sync-source-moved",
            moved,
            record,
            fetch,
        )
    side = _retained_side(record)
    if side.wipState == "restore-conflict":
        return _continue_parked_wip_restore(contract, store, record, fetch)
    return _finish_retained_merge(contract, store, record, fetch)


def _moved_source_sides(record: SyncOperationRecord) -> str | None:
    """Refuse a continue onto a tip the official line has left behind (MIK-R61 rule 12).

    A sync journaled onto a tip the line left behind is ended with cancel, not continued
    onto it: continuing would carry the stale tip, and a stale merge stops at the commit
    check on findings later landings already repaired. A new sync onto the line's current
    tip then starts as every sync does.
    """

    for side in (record.code, record.memory):
        if side is None or side.plan in {"already-current", "skip"}:
            continue
        repository = Path(side.repository)
        try:
            tip = branch_commit(repository, side.sourceBranch)
        except (RuntimeError, subprocess.SubprocessError, OSError) as error:
            return (
                f"The {side.side} source tip cannot be read ({error}); the retained "
                "resolution cannot continue onto an unreadable line. Run worktree_sync "
                "again once the line reads, or cancel it with resolution_action='cancel'."
            )
        if tip != side.sourceCommit:
            return (
                f"The {side.side} source line moved since this sync was admitted "
                f"({side.sourceBranch} is at {tip}, the sync carries {side.sourceCommit}). "
                "End this sync with worktree_sync and resolution_action='cancel', then "
                "start a new sync onto the line's current tip; it is not continued onto "
                "the tip the line left behind."
            )
    return None


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
    """Validate and commit a retained merge whose conflicts are all resolved and staged."""

    side_name: SyncSide = "code" if record.phase == "code-resolution-required" else "memory"
    side = _retained_side(record)

    paired = _paired_code(record)
    try:
        result_head = continue_side_merge(side, paired_code=paired)
    except SyncKnowledgeValidationError as error:
        return _knowledge_validation_refused(store, error, record, fetch)
    except SyncGitProofError as error:
        stopped = commit_check_report(side)
        if stopped is not None:
            return _commit_check_stopped(store, stopped, str(error), record, fetch)
        refreshed = side.model_copy(update={"conflictFiles": content_conflicts(side)})
        record = update_record(store, record, phase=record.phase, side=refreshed)
        return manual_repair_result(
            "sync-resolution-incomplete",
            _incomplete_resolution_summary(side, str(error)),
            record,
            fetch,
        )
    # A continued merge is a merge: the debt it carries is knowable only once the resolved tree is
    # committed, so it is computed here exactly as the automatic path computes it. Without this the
    # retained side keeps the empty tuple it was parked with, and the answer names no debt while the
    # resulting pair still reports it (MIK-R198 rule 7).
    debt = (
        staged_merge_repair_debt(side, paired)
        if side_name == "memory" and side.plan == "merge"
        else ()
    )
    completed = side.model_copy(
        update={
            "state": "completed",
            "resultHead": result_head,
            "conflictFiles": (),
            "repairDebt": debt or side.repairDebt,
        }
    )
    record, completed, conflicted = restore_parked_wip(store, record, side_name, completed)
    if conflicted:
        return resolution_required(record, fetch)
    record = _advance_after_side(store, record, side_name, completed)
    return _run_automatic(contract, store, record, fetch)


def _incomplete_resolution_summary(side: SyncSideRecord, detail: str) -> str:
    """Name the ending action on a continue that resolved nothing (MIK-R61 rule 12).

    No answer of continue leaves the sync open without naming the worktree_sync action
    that ends it: the journal keeps its phase, so the way on is resolving and continuing,
    or cancelling.
    """

    return (
        f"{detail} The journal keeps its phase; resolve the named paths in {side.worktree}, "
        "stage them (git add), then run worktree_sync with resolution_action='continue'; "
        "or end the sync with resolution_action='cancel'."
    )


def _commit_check_stopped(
    store: SyncOperationStore,
    report: dict[str, object],
    detail: str,
    record: SyncOperationRecord,
    fetch: dict[str, object],
) -> WorktreeCommandResult:
    """Answer a continue stopped at the repository's commit check (MIK-R61 rule 12).

    The content merged but the commit -- and the pre-commit check it runs -- did not. The
    merge stays staged and the phase is unchanged, so repairing the named files, staging
    them and continuing resumes the same merge. For each file the check judged, the answer
    says whether the task changed it; cancel is named as the way back.
    """

    staged = report.get("staged")
    assert isinstance(staged, list)
    task_changed = report.get("taskChanged")
    assert isinstance(task_changed, dict)
    lines = [
        f"- {path}: {'changed by the task' if task_changed.get(path) else 'from the carried line'}"
        for path in staged
    ]
    side = report.get("side", "the side")
    worktree = ""
    retained = _retained_side(record)
    if retained.side == side:
        worktree = retained.worktree
    summary = (
        f"The {side} merge is complete in content but its commit did not pass the repository's "
        f"commit check: {detail}\nThe check judged the merged tree:\n"
        + "\n".join(lines)
        + f"\nRecovery: repair the named files in {worktree}, stage them (git add), then continue "
        "with worktree_sync and resolution_action='continue'; or end the sync with "
        "resolution_action='cancel'."
    )
    record = update_record(store, record, phase=record.phase)
    return manual_repair_result("sync-commit-check-failed", summary, record, fetch)


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
        return manual_repair_result(
            "sync-resolution-incomplete",
            _incomplete_resolution_summary(side, str(error)),
            record,
            fetch,
        )
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
        # An adopted merge is adopted with the debt its committed tree carries: a crash between the
        # memory merge commit and the journal update must not be the one path that names no debt
        # while the resulting pair still reports it (MIK-R198 rule 7).
        # A fast-forward adopts a tree as a merge commits one: with debt still a local copy, an
        # adoption reading the durable record after the advance must recompute it for both plans
        # (L191-A3-03).
        debt = (
            staged_merge_repair_debt(record.memory, _paired_code(record))
            if record.memory.plan in {"merge", "fast-forward"} and not record.memory.repairDebt
            else record.memory.repairDebt
        )
        side = record.memory.model_copy(
            update={
                "state": "completed",
                "resultHead": (
                    record.memory.preSyncHead
                    if record.memory.plan == "skip"
                    else side_branch_head(record.memory)
                ),
                "repairDebt": debt,
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
    if side_name == "memory" and record.validationRefusal is not None:
        # A continued sync validates again and clears the journaled refusal when the tree
        # passes (MIK-R61 rule 5): completing the memory side proves its tree judged clean.
        record = record.model_copy(update={"validationRefusal": None})
    return update_record(store, record, phase=phase, side=side, side_name=side_name)
