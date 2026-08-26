"""Focused recovery and rollback-authority tests for resumable sync."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest import mock

import pytest
from agents_remember.models.worktree import SyncPhase, SyncSide
from agents_remember.worktrees import sync_transaction_recovery as recovery
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.sync_transaction_git import SyncGitProofError
from agents_remember.worktrees.sync_transaction_state import (
    SyncJournalReadError,
    SyncOperationRecord,
    SyncOperationStore,
    SyncSidePlan,
    SyncSideRecord,
    SyncSideState,
)
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    RepoBranchPlan,
    WorktreeContract,
    default_series_contract,
)

SHA_BASE = "0" * 40
SHA_PRE = "1" * 40
SHA_SOURCE = "2" * 40
SHA_RESULT = "3" * 40


def _result(returncode: int = 0, stdout: str = "", stderr: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def _object_dict(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return cast(dict[str, object], value)


def _contract(root: Path, *, external: bool = False) -> WorktreeContract:
    task = ContractTask(
        name="master",
        repo_name="repo",
        coordination_root=root / "coordination",
        workflow_kind="light-task",
        memory_mode="external" if external else "internal",
        parent_task_name="sprint",
    )
    return default_series_contract(
        task,
        code=RepoBranchPlan(
            repo_path=root / "code",
            source_branch="super",
            work_branch="ar/master",
            base_commit=SHA_BASE,
        ),
        memory=(
            RepoBranchPlan(
                repo_path=root / "memory",
                source_branch="super",
                work_branch="ar/master",
                base_commit=SHA_BASE,
            )
            if external
            else None
        ),
        task_root=root / "coordination" / "tasks" / "repo" / "master",
    )


def _side(
    root: Path,
    *,
    side: SyncSide = "code",
    plan: SyncSidePlan = "merge",
    state: SyncSideState = "completed",
    result_head: str = SHA_RESULT,
) -> SyncSideRecord:
    return SyncSideRecord(
        side=side,
        repository=(root / f"{side}-repo").as_posix(),
        worktree=(root / f"{side}-worktree").as_posix(),
        sourceBranch="source",
        workBranch="work",
        sourceCommit=SHA_SOURCE,
        preSyncHead=SHA_PRE,
        baseCommit=SHA_BASE,
        backupRef=f"refs/agents-remember/sync/test/{side}/pre-sync",
        sourceBackupRef=f"refs/agents-remember/sync/test/{side}/source",
        baseBackupRef=f"refs/agents-remember/sync/test/{side}/base",
        plan=plan,
        state=state,
        resultHead=result_head,
    )


def _record(
    root: Path,
    phase: SyncPhase = "finalizing",
    *,
    memory: bool = False,
) -> SyncOperationRecord:
    return SyncOperationRecord(
        generation=1,
        contractPath=(root / "series-contract.md").as_posix(),
        taskId="MASTER",
        contractKind="series",
        codeBaseFrom=SHA_BASE,
        memoryBaseFrom=SHA_BASE if memory else "",
        phase=phase,
        memorySyncChoice="merge-memory" if memory else None,
        code=_side(root),
        memory=_side(root, side="memory") if memory else None,
        createdAt="2026-08-26T00:00:00+00:00",
        updatedAt="2026-08-26T00:00:00+00:00",
    )


def test_finalize_is_idempotent_when_contract_bases_are_already_current(tmp_path: Path) -> None:
    contract = replace(_contract(tmp_path), code_base_commit=SHA_SOURCE)
    record = _record(tmp_path).model_copy(
        update={"contractPath": contract.contract_path.as_posix(), "taskId": contract.task_id}
    )
    completed = record.model_copy(update={"phase": "completed"})
    expected = WorktreeCommandResult(0, {"state": "synced"})
    store = mock.Mock(spec=SyncOperationStore)
    with (
        mock.patch.object(recovery, "reload_contract", return_value=contract),
        mock.patch.object(recovery, "require_finalizable_contract"),
        mock.patch.object(recovery, "_require_completed_branches"),
        mock.patch.object(recovery, "write_contract") as write,
        mock.patch.object(recovery, "update_record", return_value=completed),
        mock.patch.object(recovery, "remove_temporary_worktrees"),
        mock.patch.object(recovery, "delete_authority"),
        mock.patch.object(recovery, "completed_sync_result", return_value=expected),
    ):
        assert recovery.finalize_sync(contract, store, record, {}) is expected
    write.assert_not_called()


def test_completed_result_refuses_contract_pair_mismatch(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    record = _record(tmp_path).model_copy(
        update={"contractPath": contract.contract_path.as_posix(), "taskId": contract.task_id}
    )
    with (
        mock.patch.object(recovery, "reload_contract", return_value=contract),
        pytest.raises(SyncGitProofError, match="finalized base pair"),
    ):
        recovery.completed_sync_result(contract, record, {})


def test_cancel_refuses_movement_on_an_unchanged_side(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    record = _record(tmp_path, "running-code").model_copy(
        update={"code": _side(tmp_path, plan="already-current", result_head=SHA_PRE)}
    )
    store = mock.Mock(spec=SyncOperationStore)
    with (
        mock.patch.object(recovery, "update_record", return_value=record),
        mock.patch.object(recovery, "side_branch_head", return_value=SHA_RESULT),
        pytest.raises(SyncGitProofError, match="unchanged branch moved"),
    ):
        recovery.cancel_sync(contract, store, record, {})


def test_cancel_checks_each_unchanged_side_before_releasing_authority(tmp_path: Path) -> None:
    contract = _contract(tmp_path, external=True)
    record = _record(tmp_path, "running-code", memory=True).model_copy(
        update={
            "code": _side(tmp_path, plan="already-current", result_head=SHA_PRE),
            "memory": _side(
                tmp_path,
                side="memory",
                plan="already-current",
                result_head=SHA_PRE,
            ),
        }
    )
    store = mock.Mock(spec=SyncOperationStore)
    with (
        mock.patch.object(recovery, "update_record", return_value=record),
        mock.patch.object(recovery, "side_branch_head", return_value=SHA_PRE) as head,
        mock.patch.object(recovery, "reload_contract", return_value=contract),
        mock.patch.object(recovery, "require_contract_bases_unchanged"),
        mock.patch.object(recovery, "remove_temporary_worktrees"),
        mock.patch.object(recovery, "delete_authority"),
    ):
        result = recovery.cancel_sync(contract, store, record, {})
    assert result.payload["state"] == "sync-cancelled"
    assert head.call_count == 2


def test_unreadable_journal_requires_explicit_apply_cancel(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    store = mock.Mock(spec=SyncOperationStore)
    error = SyncJournalReadError(tmp_path / "journal", b"bad", "malformed")
    result = recovery.recover_unreadable_journal(
        contract,
        WorktreeArgs(),
        store=store,
        error=error,
        fetch={},
    )
    assert result.payload["state"] == "sync-journal-malformed"
    assert _object_dict(result.payload["nextArgs"])["resolution_action"] == "cancel"


def test_unreadable_journal_preservation_and_ref_inspection_failures_are_retryable(
    tmp_path: Path,
) -> None:
    contract = _contract(tmp_path)
    store = mock.Mock(spec=SyncOperationStore)
    error = SyncJournalReadError(tmp_path / "journal", b"bad", "malformed")
    store.archive_malformed.side_effect = OSError("archive failed")
    with mock.patch.object(recovery, "_manual_repair_evidence", return_value={"proof": "needed"}):
        result = recovery.recover_unreadable_journal(
            contract,
            WorktreeArgs(resolution_action="cancel"),
            store=store,
            error=error,
            fetch={},
        )
    assert result.payload["state"] == "sync-cancel-manual-repair-required"

    archive = tmp_path / "archive.json"
    store.archive_malformed.side_effect = None
    store.archive_malformed.return_value = archive
    with (
        mock.patch.object(
            recovery,
            "authority_refs_exist",
            side_effect=SyncGitProofError("refs unreadable"),
        ),
        mock.patch.object(recovery, "_manual_repair_evidence", return_value={"proof": "needed"}),
    ):
        result = recovery.recover_unreadable_journal(
            contract,
            WorktreeArgs(resolution_action="cancel"),
            store=store,
            error=error,
            fetch={},
        )
    assert result.payload["state"] == "sync-cancel-manual-repair-required"
    assert result.payload["evidencePath"] == archive.as_posix()


def test_missing_journal_requires_cancel_and_routes_applied_cancel(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    store = mock.Mock(spec=SyncOperationStore)
    preview = recovery.recover_missing_journal(
        contract,
        cancel=False,
        dry_run=False,
        store=store,
        fetch={},
    )
    assert preview.payload["state"] == "sync-journal-missing-after-admission"
    expected = WorktreeCommandResult(0, {"state": "recovered"})
    with mock.patch.object(recovery, "_recover_from_refs", return_value=expected):
        assert (
            recovery.recover_missing_journal(
                contract,
                cancel=True,
                dry_run=False,
                store=store,
                fetch={},
            )
            is expected
        )


def test_manual_repair_result_keeps_terminal_cancel_address(tmp_path: Path) -> None:
    record = _record(tmp_path, "cancelling")
    result = recovery.manual_repair_result("repair", "Repair it.", record, {})
    assert result.payload["state"] == "repair"
    assert _object_dict(result.payload["cancelArgs"])["contract_path"] == record.contractPath


def test_ref_recovery_routes_partial_absent_and_complete_authority(tmp_path: Path) -> None:
    external = _contract(tmp_path, external=True)
    code = _side(tmp_path)
    memory = _side(tmp_path, side="memory")
    store = mock.Mock(spec=SyncOperationStore)
    partial = WorktreeCommandResult(2, {"state": "partial"})
    with (
        mock.patch.object(
            recovery,
            "_read_recovery_side",
            side_effect=[(None, None), (memory, None)],
        ),
        mock.patch.object(
            recovery,
            "_recover_complete_sides_around_partial_authority",
            return_value=partial,
        ) as recover_partial,
    ):
        assert recovery._recover_from_refs(external, store, {}, None) is partial
    assert "code recovery refs are absent" in recover_partial.call_args.args[2][0]

    internal = _contract(tmp_path)
    with (
        mock.patch.object(recovery, "_read_recovery_side", return_value=(None, None)),
        mock.patch.object(recovery, "_manual_repair_evidence", return_value={}),
    ):
        missing = recovery._recover_from_refs(internal, store, {}, None)
    assert missing.payload["state"] == "sync-cancel-manual-repair-required"

    cancelled = WorktreeCommandResult(0, {"state": "sync-cancelled"})
    with (
        mock.patch.object(
            recovery,
            "_read_recovery_side",
            side_effect=[(code, None), (memory, None)],
        ),
        mock.patch.object(recovery, "_prove_rollback_possible"),
        mock.patch.object(recovery, "operation_stamp", return_value="now"),
        mock.patch.object(recovery, "sync_contract_kind", return_value="series"),
        mock.patch.object(recovery, "cancel_sync", return_value=cancelled),
    ):
        assert recovery._recover_from_refs(external, store, {}, tmp_path / "archive") is cancelled
    store.write.assert_called()


def test_internal_ref_recovery_skips_absent_memory_and_proves_code(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    code = _side(tmp_path)
    store = mock.Mock(spec=SyncOperationStore)
    cancelled = WorktreeCommandResult(0, {"state": "sync-cancelled"})
    with (
        mock.patch.object(recovery, "_read_recovery_side", return_value=(code, None)),
        mock.patch.object(recovery, "_prove_rollback_possible") as prove,
        mock.patch.object(recovery, "operation_stamp", return_value="now"),
        mock.patch.object(recovery, "sync_contract_kind", return_value="series"),
        mock.patch.object(recovery, "cancel_sync", return_value=cancelled),
    ):
        assert recovery._recover_from_refs(contract, store, {}, None) is cancelled
    prove.assert_called_once_with(code)


def test_partial_authority_preserves_remaining_refs_when_rollback_fails(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    code = _side(tmp_path)
    with (
        mock.patch.object(recovery, "_prove_rollback_possible"),
        mock.patch.object(
            recovery,
            "rollback_side",
            side_effect=SyncGitProofError("rollback failed"),
        ),
        mock.patch.object(recovery, "_manual_repair_evidence", return_value={}),
    ):
        result = recovery._recover_complete_sides_around_partial_authority(
            contract,
            (code,),
            ["memory refs incomplete"],
            {},
            None,
        )
    assert result.payload["state"] == "sync-cancel-manual-repair-required"
    summary = result.payload["summary"]
    assert isinstance(summary, str) and "rollback failed" in summary


def test_manual_repair_evidence_records_observation_failures_per_side(tmp_path: Path) -> None:
    contract = _contract(tmp_path, external=True)
    with mock.patch.object(recovery, "side_locations", side_effect=OSError("unavailable")):
        evidence = recovery._manual_repair_evidence(contract)
    sides = evidence["sides"]
    assert isinstance(sides, list)
    side_records = [cast(dict[str, object], side) for side in sides]
    assert [side["state"] for side in side_records] == [
        "observation-unavailable",
        "observation-unavailable",
    ]


def test_rollback_proof_accepts_only_exact_active_fast_forward_or_merge(tmp_path: Path) -> None:
    side = _side(tmp_path)
    with (
        mock.patch.object(recovery, "ensure_temporary_worktree"),
        mock.patch.object(recovery, "head_commit", return_value=SHA_RESULT),
        mock.patch.object(recovery, "merge_head", return_value=SHA_SOURCE),
        pytest.raises(SyncGitProofError, match="not the pinned sync merge"),
    ):
        recovery._prove_rollback_possible(side)
    with (
        mock.patch.object(recovery, "ensure_temporary_worktree"),
        mock.patch.object(recovery, "head_commit", return_value=SHA_PRE),
        mock.patch.object(recovery, "merge_head", return_value=SHA_SOURCE),
    ):
        recovery._prove_rollback_possible(side)
    with (
        mock.patch.object(recovery, "ensure_temporary_worktree"),
        mock.patch.object(recovery, "head_commit", return_value=SHA_PRE),
        mock.patch.object(recovery, "merge_head", return_value=None),
    ):
        recovery._prove_rollback_possible(side)
    with (
        mock.patch.object(recovery, "ensure_temporary_worktree"),
        mock.patch.object(recovery, "head_commit", return_value=SHA_RESULT),
        mock.patch.object(recovery, "merge_head", return_value=None),
        mock.patch.object(recovery, "run_git", return_value=_result(1)),
        mock.patch.object(recovery, "is_ancestor", return_value=False),
        pytest.raises(SyncGitProofError, match="cannot prove"),
    ):
        recovery._prove_rollback_possible(side)
    with (
        mock.patch.object(recovery, "ensure_temporary_worktree"),
        mock.patch.object(recovery, "head_commit", return_value=SHA_SOURCE),
        mock.patch.object(recovery, "merge_head", return_value=None),
        mock.patch.object(recovery, "run_git", return_value=_result(1)),
        mock.patch.object(recovery, "is_ancestor", return_value=True),
    ):
        recovery._prove_rollback_possible(side)
    with (
        mock.patch.object(recovery, "ensure_temporary_worktree"),
        mock.patch.object(recovery, "head_commit", return_value=SHA_RESULT),
        mock.patch.object(
            recovery,
            "run_git",
            return_value=_result(stdout=f"{SHA_RESULT} {SHA_PRE} {SHA_SOURCE}"),
        ),
        mock.patch.object(recovery, "merge_head", return_value=None),
        mock.patch.object(recovery, "is_ancestor", return_value=False),
    ):
        recovery._prove_rollback_possible(side)


def test_completed_branch_proof_rejects_each_unattributed_state(tmp_path: Path) -> None:
    pending = _record(tmp_path).model_copy(
        update={"code": _side(tmp_path, state="pending", result_head="")}
    )
    with pytest.raises(SyncGitProofError, match="not completed"):
        recovery._require_completed_branches(pending)

    unchanged = _record(tmp_path).model_copy(
        update={"code": _side(tmp_path, plan="already-current", result_head=SHA_PRE)}
    )
    with (
        mock.patch.object(recovery, "side_branch_head", return_value=SHA_RESULT),
        pytest.raises(SyncGitProofError, match="unchanged plan"),
    ):
        recovery._require_completed_branches(unchanged)

    missing = _record(tmp_path).model_copy(update={"code": _side(tmp_path, result_head="")})
    with (
        mock.patch.object(recovery, "side_branch_head", return_value=SHA_RESULT),
        pytest.raises(SyncGitProofError, match="work branch moved"),
    ):
        recovery._require_completed_branches(missing)

    record = _record(tmp_path)
    with (
        mock.patch.object(recovery, "side_branch_head", return_value=SHA_RESULT),
        mock.patch.object(recovery, "exact_created_head", return_value=False),
        pytest.raises(SyncGitProofError, match="operation-created"),
    ):
        recovery._require_completed_branches(record)
    with (
        mock.patch.object(recovery, "side_branch_head", return_value=SHA_RESULT),
        mock.patch.object(recovery, "exact_created_head", return_value=True),
        mock.patch.object(recovery, "validate_completed_side") as validate,
    ):
        recovery._require_completed_branches(record)
    validate.assert_called_once_with(record.code, SHA_RESULT)
