"""Admission and identity authority for resumable source synchronization."""

from __future__ import annotations

from pathlib import Path
from typing import Literal, cast

from agents_remember.kernel.git_command import run_git
from agents_remember.kernel.memory_ledger import (
    LedgerError,
    find_unique_mapping,
    parse_ledger_text,
)
from agents_remember.models.worktree import SyncPhase, SyncSide
from agents_remember.worktrees.modules.git import branch_commit, head_commit, is_ancestor
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.sync_transaction_git import (
    SyncGitProofError,
    create_pinned_ref,
    delete_pinned_ref,
    read_ref,
    remove_temporary_worktree,
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


def preflight_official_pair(
    contract: WorktreeContract,
    code_tip: str,
    memory_tip: str,
    external: bool,
    fetch: dict[str, object],
) -> WorktreeCommandResult | None:
    if not external:
        return None
    assert contract.memory_repo_path is not None
    ledger_blob = run_git(contract.memory_repo_path, ["show", f"{memory_tip}:memory.md"])
    if ledger_blob.returncode != 0:
        return command_result(
            2,
            "blocked",
            "The admitted official memory source has no readable memory.md ledger.",
            fetch,
        )
    try:
        ledger = parse_ledger_text(ledger_blob.stdout)
    except LedgerError as error:
        return command_result(
            2, "blocked", f"The official memory ledger is invalid: {error}", fetch
        )
    try:
        mapping = find_unique_mapping(ledger, code_tip)
    except LedgerError as error:
        return command_result(
            2,
            "blocked",
            f"The official memory ledger does not uniquely map the admitted code tip: {error}",
            fetch,
        )
    if mapping is None:
        return command_result(
            2,
            "blocked",
            "The official line is mid-cycle: its memory ledger does not map the admitted code tip.",
            fetch,
        )
    return None


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
    }


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
