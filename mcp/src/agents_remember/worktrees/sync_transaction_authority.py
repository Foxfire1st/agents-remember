"""Admission and identity authority for resumable source synchronization."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal, cast

from agents_remember.models.worktree import SyncPhase, SyncSide
from agents_remember.worktrees.modules.git import branch_commit, head_commit, is_ancestor
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.sync_transaction_git import (
    MAX_NAMED_SYNC_PATHS,
    SyncGitProofError,
    apply_parked_wip,
    cancelled_wip_restored,
    capture_retained_digests,
    content_conflicts,
    create_pinned_ref,
    delete_pinned_ref,
    discard_memory_cache_changes,
    git_status,
    named_merge_rules,
    parked_wip_failures,
    read_ref,
    reapply_merge_fault,
    remove_temporary_worktree,
    sample_paths,
    side_branch_head,
    stash_entry_live,
    verify_crashed_reapply,
)
from agents_remember.worktrees.sync_transaction_state import (
    SyncOperationRecord,
    SyncOperationStore,
    SyncSidePlan,
    SyncSideRecord,
    operation_stamp,
    sync_side_base_ref,
    sync_side_refs,
)
from agents_remember.worktrees.worktree_contract import (
    WorktreeContract,
    load_contract,
)

logger = logging.getLogger(__name__)


def side_record(
    contract: WorktreeContract,
    side_name: SyncSide,
    source_commit: str,
) -> SyncSideRecord:
    repository, worktree, source_branch, work_branch = side_locations(contract, side_name)
    pre_head = (
        branch_commit(repository, work_branch)
        if contract.kind == "series"
        else head_commit(worktree)
    )
    if source_commit == pre_head or is_ancestor(repository, source_commit, pre_head):
        plan: SyncSidePlan = "already-current"
    elif is_ancestor(repository, pre_head, source_commit):
        plan = "fast-forward"
    else:
        plan = "merge"
    backup_ref, source_ref = sync_side_refs(contract.contract_path, side_name)
    base_commit = contract.code_base_commit if side_name == "code" else contract.memory_base_commit
    if not base_commit:
        raise SyncGitProofError(f"{side_name} sync has no recorded base commit")
    return SyncSideRecord(
        side=side_name,
        repository=repository.resolve().as_posix(),
        worktree=worktree.resolve(strict=False).as_posix(),
        sourceBranch=source_branch,
        workBranch=work_branch,
        sourceCommit=source_commit,
        preSyncHead=pre_head,
        baseCommit=base_commit,
        backupRef=backup_ref,
        sourceBackupRef=source_ref,
        baseBackupRef=sync_side_base_ref(contract.contract_path, side_name),
        plan=plan,
        temporary=contract.kind == "series",
    )


def side_locations(contract: WorktreeContract, side_name: SyncSide) -> tuple[Path, Path, str, str]:
    temporary_root = contract.worktree_group / ".sync"
    if side_name == "code":
        return (
            contract.code_repo_path,
            temporary_root / "code" if contract.kind == "series" else contract.code_worktree,
            contract.code_source_branch,
            contract.code_work_branch,
        )
    if contract.memory_repo_path is None:
        raise SyncGitProofError("external-memory sync has no memory repository")
    if contract.kind != "series" and contract.memory_worktree is None:
        raise SyncGitProofError("external-memory leaf sync has no memory worktree")
    memory_worktree = (
        temporary_root / "memory" if contract.kind == "series" else contract.memory_worktree
    )
    assert memory_worktree is not None
    return (
        contract.memory_repo_path,
        memory_worktree,
        contract.memory_source_branch,
        contract.memory_work_branch,
    )


def sync_contract_kind(contract: WorktreeContract) -> Literal["leaf", "series"]:
    """Close the historical string cell at the durable sync-journal boundary."""

    if contract.kind not in {"leaf", "series"}:
        raise SyncGitProofError(f"sync contract kind is invalid: {contract.kind!r}")
    return cast(Literal["leaf", "series"], contract.kind)


def source_pair(contract: WorktreeContract) -> tuple[str, str, bool]:
    code_tip = branch_commit(contract.code_repo_path, contract.code_source_branch)
    external = contract.memory_mode == "external" and contract.memory_repo_path is not None
    memory_tip = (
        branch_commit(contract.memory_repo_path, contract.memory_source_branch)
        if external and contract.memory_repo_path is not None
        else ""
    )
    return code_tip, memory_tip, external


def pin_authority(record: SyncOperationRecord) -> None:
    for side in participating_sides(record):
        repository = Path(side.repository)
        create_pinned_ref(repository, side.baseBackupRef, side.baseCommit)
        create_pinned_ref(repository, side.backupRef, side.preSyncHead)
        create_pinned_ref(repository, side.sourceBackupRef, side.sourceCommit)


def require_pinned_authority(
    record: SyncOperationRecord, *, allow_expected_missing: bool = False
) -> None:
    for side in participating_sides(record):
        repository = Path(side.repository)
        for ref, expected, label in (
            (side.baseBackupRef, side.baseCommit, "recorded-base"),
            (side.backupRef, side.preSyncHead, "pre-sync"),
            (side.sourceBackupRef, side.sourceCommit, "source"),
        ):
            current = read_ref(repository, ref)
            if current is None and allow_expected_missing:
                continue
            if current != expected:
                raise SyncGitProofError(f"{side.side} {label} authority ref is missing or changed")


def delete_authority(record: SyncOperationRecord) -> None:
    for side in reversed(participating_sides(record)):
        repository = Path(side.repository)
        delete_pinned_ref(repository, side.sourceBackupRef, side.sourceCommit)
        delete_pinned_ref(repository, side.backupRef, side.preSyncHead)
        delete_pinned_ref(repository, side.baseBackupRef, side.baseCommit)


def authority_refs_exist(contract: WorktreeContract) -> bool:
    repositories = {"code": contract.code_repo_path, "memory": contract.memory_repo_path}
    side_names: tuple[SyncSide, ...] = ("code", "memory")
    for side_name in side_names:
        repository = repositories[side_name]
        if repository is None:
            continue
        backup, source = sync_side_refs(contract.contract_path, side_name)
        base = sync_side_base_ref(contract.contract_path, side_name)
        if any(read_ref(repository, ref) is not None for ref in (base, backup, source)):
            return True
    return False


def complete_side_from_refs(
    contract: WorktreeContract, side_name: SyncSide
) -> SyncSideRecord | None:
    repository, worktree, source_branch, work_branch = side_locations(contract, side_name)
    backup, source = sync_side_refs(contract.contract_path, side_name)
    base = sync_side_base_ref(contract.contract_path, side_name)
    pre_head = read_ref(repository, backup)
    source_head = read_ref(repository, source)
    base_commit = read_ref(repository, base)
    if pre_head is None and source_head is None and base_commit is None:
        return None
    if pre_head is None or source_head is None or base_commit is None:
        raise SyncGitProofError(f"{side_name} sync recovery refs are incomplete")
    return SyncSideRecord(
        side=side_name,
        repository=repository.resolve().as_posix(),
        worktree=worktree.resolve(strict=False).as_posix(),
        sourceBranch=source_branch,
        workBranch=work_branch,
        sourceCommit=source_head,
        preSyncHead=pre_head,
        baseCommit=base_commit,
        backupRef=backup,
        sourceBackupRef=source,
        baseBackupRef=base,
        plan="merge",
        temporary=contract.kind == "series",
    )


def remove_temporary_worktrees(record: SyncOperationRecord) -> None:
    for side in reversed(participating_sides(record)):
        remove_temporary_worktree(side)
    root = Path(record.code.worktree).parent
    if record.code.temporary and root.exists() and not any(root.iterdir()):
        root.rmdir()


def require_record_contract(contract: WorktreeContract, record: SyncOperationRecord) -> None:
    if contract.contract_path.resolve() != Path(record.contractPath).resolve():
        raise SyncGitProofError("sync journal contract path changed")
    if contract.task_id != record.taskId or contract.kind != record.contractKind:
        raise SyncGitProofError("sync journal task identity changed")
    _require_side_contract(contract, record.code)
    if record.memory is not None:
        _require_side_contract(contract, record.memory)
    # A partial-admission recovery may legitimately have only the code ref pair.
    elif (
        contract.memory_mode == "external"
        and contract.memory_repo_path is not None
        and record.phase not in {"cancelling", "cancelled"}
    ):
        raise SyncGitProofError("active external-memory sync lost its memory authority")


def _require_side_contract(contract: WorktreeContract, side: SyncSideRecord) -> None:
    repository, worktree, source_branch, work_branch = side_locations(contract, side.side)
    expected = (
        repository.resolve().as_posix(),
        worktree.resolve(strict=False).as_posix(),
        source_branch,
        work_branch,
    )
    observed = (side.repository, side.worktree, side.sourceBranch, side.workBranch)
    if observed != expected:
        raise SyncGitProofError(f"sync journal {side.side} authority changed")


def require_finalizable_contract(contract: WorktreeContract, record: SyncOperationRecord) -> None:
    require_record_contract(contract, record)
    if contract.code_base_commit not in {record.codeBaseFrom, record.code.sourceCommit}:
        raise SyncGitProofError("contract code base changed outside the sync transaction")
    memory_to = target_memory_base(record)
    if contract.memory_base_commit not in {record.memoryBaseFrom, memory_to}:
        raise SyncGitProofError("contract memory base changed outside the sync transaction")


def require_contract_bases_unchanged(
    contract: WorktreeContract, record: SyncOperationRecord
) -> None:
    if (
        contract.code_base_commit != record.codeBaseFrom
        or contract.memory_base_commit != record.memoryBaseFrom
    ):
        raise SyncGitProofError("sync cancellation refuses after contract base finalization")


def target_memory_base(record: SyncOperationRecord) -> str:
    if record.memory is not None and record.memory.plan != "skip":
        return record.memory.sourceCommit
    return record.memoryBaseFrom


def reload_contract(contract: WorktreeContract) -> WorktreeContract:
    return load_contract(contract.contract_path)


def update_record(
    store: SyncOperationStore,
    record: SyncOperationRecord,
    *,
    phase: SyncPhase,
    side: SyncSideRecord | None = None,
    side_name: SyncSide | None = None,
) -> SyncOperationRecord:
    if side_name is None and phase.startswith("code-"):
        side_name = "code"
    elif side_name is None and phase.startswith("memory-"):
        side_name = "memory"
    changes: dict[str, object] = {"phase": phase, "updatedAt": operation_stamp()}
    if side is not None and side_name is not None:
        changes[side_name] = side
    updated = SyncOperationRecord.model_validate(record.model_copy(update=changes).model_dump())
    store.write(updated)
    return updated


def participating_sides(record: SyncOperationRecord) -> tuple[SyncSideRecord, ...]:
    return (record.code, record.memory) if record.memory is not None else (record.code,)


def side_payload(side: SyncSideRecord | None) -> dict[str, object]:
    if side is None:
        return {"state": "no-external-memory"}
    return {
        "state": side.state,
        "plan": side.plan,
        "worktree": side.worktree,
        "temporary": side.temporary,
        **({"files": list(side.conflictFiles)} if side.conflictFiles else {}),
        **(
            {
                "wip": {
                    "state": side.wipState,
                    "paths": list(side.wipPaths),
                    "pathCount": side.wipPathCount,
                }
            }
            if side.wipState
            else {}
        ),
    }


def resolution_phase(side_name: SyncSide) -> SyncPhase:
    return "code-resolution-required" if side_name == "code" else "memory-resolution-required"


def _restoration_failures(side: SyncSideRecord) -> tuple[tuple[str, ...], bool]:
    """The unproven parked paths and whether the stash inventory is complete.

    An unreadable stash inventory never passes: it returns the journaled sample
    as the named failures with the inventory marked incomplete.
    """

    try:
        return parked_wip_failures(side, side_branch_head(side)), True
    except SyncGitProofError:
        return sample_paths(side), False


def _skip_apply_proven(side: SyncSideRecord) -> bool:
    """Whether the tree already holds the parked reapply over a live stash."""

    if not side.wipStash or not stash_entry_live(side):
        return False
    failures, complete = _restoration_failures(side)
    return complete and not failures


def _mark_restored(
    store: SyncOperationStore,
    record: SyncOperationRecord,
    side_name: SyncSide,
    side: SyncSideRecord,
) -> tuple[SyncOperationRecord, SyncSideRecord, bool]:
    """Journal a proven reapply without touching the tree or the kept stash."""

    restored = side.model_copy(update={"wipState": "restored", "conflictFiles": ()})
    record = update_record(store, record, phase=record.phase, side=restored, side_name=side_name)
    return record, restored, False


def _retain_crashed_reapply(
    store: SyncOperationStore,
    record: SyncOperationRecord,
    side_name: SyncSide,
    side: SyncSideRecord,
) -> tuple[SyncOperationRecord, SyncSideRecord, bool]:
    """Retain pre-existing unmerged paths after a crash window.

    A side already carrying fingerprints is returned as-is: recapturing would
    bless whatever the window holds. Otherwise only a scratch-verified tree
    earns a reference; an unverified tree is retained with an empty map so a
    later cancel refuses rather than discard owner bytes.
    """

    if side.retainedTreeDigests:
        return record, side, True
    if verify_crashed_reapply(side):
        return _retain_parked_conflict(store, record, side_name, side)
    return _retain_parked_conflict(store, record, side_name, side, digests={})


def _apply_and_prove(
    store: SyncOperationStore,
    record: SyncOperationRecord,
    side_name: SyncSide,
    side: SyncSideRecord,
) -> tuple[SyncOperationRecord, SyncSideRecord, bool]:
    """Reapply the parked candidate and prove it, or retain its conflict."""

    try:
        state, _ = apply_parked_wip(side)
    except SyncGitProofError:
        if _skip_apply_proven(side):
            return _mark_restored(store, record, side_name, side)
        failures, complete = _restoration_failures(side)
        raise _unproven_restore_error(side, failures, uninventoried=not complete) from None
    if state == "conflict":
        return _retain_parked_conflict(store, record, side_name, side)
    failures, complete = _restoration_failures(side)
    if not complete or failures:
        raise _unproven_restore_error(side, failures, uninventoried=not complete)
    return _mark_restored(store, record, side_name, side)


def restore_parked_wip(
    store: SyncOperationStore,
    record: SyncOperationRecord,
    side_name: SyncSide,
    side: SyncSideRecord,
) -> tuple[SyncOperationRecord, SyncSideRecord, bool]:
    """Return one side's parked candidate to its worktree and journal the outcome.

    A clean reapply is proven restored before the side is marked so; a conflicted one
    becomes the retained ``*-resolution-required`` state with the stash kept. The third
    value reports that retained conflict, and the caller must not advance that side.
    A tree that already holds the reapply (a crash after it, or an edit the carried
    line holds) is marked without a second reapply. The stash entry itself is never
    retired here: it stays until the transaction is terminal, so a later cancel can
    still return every parked byte.
    """

    if side.wipState not in {"parked", "restore-conflict"} or not side.wipStash:
        return record, side, False
    if content_conflicts(side):
        return _retain_crashed_reapply(store, record, side_name, side)
    if _skip_apply_proven(side):
        return _mark_restored(store, record, side_name, side)
    if git_status(side):
        failures, complete = _restoration_failures(side)
        raise _unproven_restore_error(side, failures, uninventoried=not complete)
    return _apply_and_prove(store, record, side_name, side)


def _retain_parked_conflict(
    store: SyncOperationStore,
    record: SyncOperationRecord,
    side_name: SyncSide,
    side: SyncSideRecord,
    digests: dict[str, str] | None = None,
) -> tuple[SyncOperationRecord, SyncSideRecord, bool]:
    """Journal a parked-candidate reapply the agent must resolve, keeping the stash.

    A passed ``digests`` map is journaled as-is: an empty map where a crash window
    leaves the tree unverified, so a later cancel refuses rather than bless owner
    bytes. Otherwise every dirty path is operation output of this call — a fresh
    reapply or a scratch-verified crash reapply — and is fingerprinted whole.
    """

    conflicts = content_conflicts(side)
    if digests is None:
        digests = capture_retained_digests(side, include_untracked=True)
    updated = side.model_copy(
        update={
            "state": "resolution-required",
            "wipState": "restore-conflict",
            "conflictFiles": conflicts,
            "retainedTreeDigests": digests,
        }
    )
    record = update_record(
        store,
        record,
        phase=resolution_phase(side_name),
        side=updated,
        side_name=side_name,
    )
    return record, updated, True


def _unproven_restore_error(
    side: SyncSideRecord, failures: tuple[str, ...], *, uninventoried: bool = False
) -> SyncGitProofError:
    """The failed proof: its paths, the kept stash, and the one next action.

    A path the carried head's attributes give a merge of its own is named with it,
    and a merge git could not make again is named with git's words.
    """

    logger.error(
        "sync %s parked WIP unproven on every failed path: %s (stash %s)",
        side.side,
        ", ".join(failures),
        side.wipStash,
    )
    named = failures[:MAX_NAMED_SYNC_PATHS]
    rules = named_merge_rules(side, named)
    shown = ", ".join(f"{path} (merge={rules[path]})" if path in rules else path for path in named)
    if len(failures) > MAX_NAMED_SYNC_PATHS:
        shown += f" and {len(failures) - MAX_NAMED_SYNC_PATHS} more"
    count = "1 path holds" if len(failures) == 1 else f"{len(failures)} paths hold"
    fault = reapply_merge_fault(side, side_branch_head(side), named)
    merge_note = (
        f" Git could not make the reapply's merge again ({fault}), so no path both sides "
        "changed could be proven."
        if fault
        else ""
    )
    inventory_note = (
        " The recorded stash could not be fully inventoried, so no path beyond the "
        "journal sample was proven."
        if uninventoried
        else ""
    )
    return SyncGitProofError(
        f"{side.side} parked worktree WIP could not be proven restored: {count} none of the "
        f"parked bytes: {shown}.{merge_note} The parked candidate stays in stash "
        f"{side.wipStash}; run "
        f"worktree_sync with resolution_action='cancel' to restore the pre-sync heads and the "
        f"parked work. Do not edit the worktree until the transaction is closed.{inventory_note}"
    )


def restore_cancelled_wip(
    store: SyncOperationStore, record: SyncOperationRecord
) -> SyncOperationRecord:
    """Return every parked candidate after a rollback restored the pinned pre-sync heads.

    Sides the sync already restored are returned again from their kept stash: the
    cancel discards the tree copy first, so the only surviving copy is reapplied
    here. No stash is retired here: every entry stays until the cancellation is
    terminal, so a crash mid-cancel leaves a retry its full restoration authority.
    """

    for side in (record.memory, record.code):
        if side is None or not side.wipStash:
            continue
        if side.wipState not in {"parked", "restore-conflict", "restored"}:
            continue
        if cancelled_wip_restored(side):
            updated = side.model_copy(update={"wipState": "restored", "conflictFiles": ()})
            record = update_record(
                store, record, phase=record.phase, side=updated, side_name=side.side
            )
            continue
        state, conflicts = apply_parked_wip(side)
        if state != "applied" or conflicts:
            raise SyncGitProofError(
                f"{side.side} parked worktree WIP could not be restored after cancellation: "
                f"{', '.join(conflicts[:30]) or 'the reapply did not apply'}"
            )
        updated = side.model_copy(update={"wipState": "restored", "conflictFiles": ()})
        record = update_record(store, record, phase=record.phase, side=updated, side_name=side.side)
    return record


def settle_resolved_parked_wip(
    store: SyncOperationStore,
    record: SyncOperationRecord,
    side_name: SyncSide,
    side: SyncSideRecord,
) -> SyncOperationRecord:
    """Close a parked-candidate conflict the agent resolved in the worktree.

    The merge itself already committed, so the resolved candidate stays uncommitted for the
    owner of that worktree. The stash entry is kept: the resolved bytes differ from the
    parked ones, so a later cancel reads them as owner state and refuses rather than
    discarding them.
    """

    conflicts = content_conflicts(side)
    if conflicts:
        raise SyncGitProofError(
            f"{side.side} parked candidate reapply still has unmerged paths: "
            f"{', '.join(conflicts[:30])}"
        )
    discard_memory_cache_changes(side)
    updated = side.model_copy(update={"wipState": "restored", "conflictFiles": ()})
    return update_record(store, record, phase=record.phase, side=updated, side_name=side_name)


def require_parked_wip_settled(record: SyncOperationRecord) -> None:
    """Refuse finalization while any side still parks the candidate it carries."""

    for side in participating_sides(record):
        if side.wipState in {"parked", "restore-conflict"}:
            raise SyncGitProofError(
                f"{side.side} sync still parks its worktree WIP; restore it before finalizing"
            )


def command_result(
    returncode: int,
    state: str,
    summary: str,
    fetch: dict[str, object],
    **evidence: object,
) -> WorktreeCommandResult:
    return WorktreeCommandResult(
        returncode,
        {"state": state, "summary": summary, "fetch": fetch, **evidence},
    )
