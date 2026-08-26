"""Focused authority, routing, and response edges for resumable sync."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest import mock

import pytest
from agents_remember.kernel.memory_ledger import (
    LedgerError,
    create_initial_ledger,
    prepend_mapping,
)
from agents_remember.models.worktree import SyncPhase, SyncResolutionAction, SyncSide
from agents_remember.worktrees import sync_transaction as transaction
from agents_remember.worktrees import sync_transaction_authority as authority
from agents_remember.worktrees import sync_transaction_results as results
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.sync_transaction_git import SyncGitProofError
from agents_remember.worktrees.sync_transaction_state import (
    SyncOperationRecord,
    SyncOperationStore,
    SyncQuarantineRecord,
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
    state: SyncSideState = "pending",
    result_head: str = "",
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
        temporary=False,
        resultHead=result_head,
        conflictFiles=("conflict.txt",) if state == "resolution-required" else (),
    )


def _record(
    root: Path,
    phase: SyncPhase = "running-code",
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


def test_side_record_and_locations_refuse_missing_authority(tmp_path: Path) -> None:
    contract = replace(_contract(tmp_path), code_base_commit="")
    with (
        mock.patch.object(authority, "side_locations", return_value=(tmp_path, tmp_path, "s", "w")),
        mock.patch.object(authority, "branch_commit", return_value=SHA_PRE),
        mock.patch.object(authority, "is_ancestor", return_value=False),
        pytest.raises(SyncGitProofError, match="no recorded base"),
    ):
        authority.side_record(contract, "code", SHA_SOURCE)

    external = replace(_contract(tmp_path, external=True), memory_repo_path=None)
    with pytest.raises(SyncGitProofError, match="no memory repository"):
        authority.side_locations(external, "memory")
    leaf_without_memory = replace(
        _contract(tmp_path, external=True), kind="leaf", memory_worktree=None
    )
    with pytest.raises(SyncGitProofError, match="no memory worktree"):
        authority.side_locations(leaf_without_memory, "memory")


def test_sync_contract_kind_rejects_unknown_runtime_value(tmp_path: Path) -> None:
    contract = replace(_contract(tmp_path), kind="unknown")
    with pytest.raises(SyncGitProofError, match="kind is invalid"):
        authority.sync_contract_kind(contract)


def test_official_pair_preflight_reports_each_ledger_failure(tmp_path: Path) -> None:
    contract = _contract(tmp_path, external=True)
    with mock.patch.object(authority, "run_git", return_value=_result(1)):
        result = authority.preflight_official_pair(contract, SHA_SOURCE, SHA_SOURCE, True, {})
        assert result is not None and result.payload["state"] == "blocked"
    with (
        mock.patch.object(authority, "run_git", return_value=_result(stdout="ledger")),
        mock.patch.object(authority, "parse_ledger_text", side_effect=LedgerError("invalid")),
    ):
        result = authority.preflight_official_pair(contract, SHA_SOURCE, SHA_SOURCE, True, {})
        assert result is not None
        summary = result.payload["summary"]
        assert isinstance(summary, str) and "invalid" in summary
    history = prepend_mapping(
        create_initial_ledger("repo", SHA_SOURCE, SHA_PRE),
        SHA_SOURCE,
        SHA_RESULT,
    )
    with (
        mock.patch.object(authority, "run_git", return_value=_result(stdout="ledger")),
        mock.patch.object(authority, "parse_ledger_text", return_value=history),
    ):
        result = authority.preflight_official_pair(contract, SHA_SOURCE, SHA_SOURCE, True, {})
        assert result is None
    unmapped = create_initial_ledger("repo", SHA_BASE, SHA_PRE)
    with (
        mock.patch.object(authority, "run_git", return_value=_result(stdout="ledger")),
        mock.patch.object(authority, "parse_ledger_text", return_value=unmapped),
    ):
        result = authority.preflight_official_pair(contract, SHA_SOURCE, SHA_SOURCE, True, {})
        assert result is not None
        summary = result.payload["summary"]
        assert isinstance(summary, str) and "mid-cycle" in summary


def test_pinned_authority_allows_expected_absence_but_rejects_mismatch(tmp_path: Path) -> None:
    record = _record(tmp_path)
    with mock.patch.object(authority, "read_ref", return_value=None):
        authority.require_pinned_authority(record, allow_expected_missing=True)
    with (
        mock.patch.object(authority, "read_ref", return_value=SHA_RESULT),
        pytest.raises(SyncGitProofError, match="missing or changed"),
    ):
        authority.require_pinned_authority(record)


def test_recovery_side_refs_distinguish_absent_partial_and_complete(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    with (
        mock.patch.object(
            authority,
            "side_locations",
            return_value=(tmp_path, tmp_path / "wt", "source", "work"),
        ),
        mock.patch.object(authority, "read_ref", return_value=None),
    ):
        assert authority.complete_side_from_refs(contract, "code") is None
    with (
        mock.patch.object(
            authority,
            "side_locations",
            return_value=(tmp_path, tmp_path / "wt", "source", "work"),
        ),
        mock.patch.object(authority, "read_ref", side_effect=[SHA_PRE, None, SHA_BASE]),
        pytest.raises(SyncGitProofError, match="incomplete"),
    ):
        authority.complete_side_from_refs(contract, "code")


def test_record_contract_and_base_guards_reject_identity_drift(tmp_path: Path) -> None:
    contract = _contract(tmp_path, external=True)
    record = _record(tmp_path, memory=True).model_copy(
        update={
            "contractPath": contract.contract_path.resolve().as_posix(),
            "taskId": contract.task_id,
        }
    )
    with pytest.raises(SyncGitProofError, match="task identity"):
        authority.require_record_contract(replace(contract, task_id="OTHER"), record)

    missing_memory = record.model_copy(update={"memory": None})
    with (
        mock.patch.object(authority, "_require_side_contract"),
        pytest.raises(SyncGitProofError, match="lost its memory authority"),
    ):
        authority.require_record_contract(contract, missing_memory)

    with (
        mock.patch.object(authority, "side_locations", return_value=(tmp_path, tmp_path, "x", "y")),
        pytest.raises(SyncGitProofError, match="authority changed"),
    ):
        authority._require_side_contract(contract, record.code)

    with mock.patch.object(authority, "require_record_contract"):
        with pytest.raises(SyncGitProofError, match="code base changed"):
            authority.require_finalizable_contract(
                replace(contract, code_base_commit=SHA_RESULT),
                record,
            )
        with pytest.raises(SyncGitProofError, match="memory base changed"):
            authority.require_finalizable_contract(
                replace(
                    contract, code_base_commit=record.codeBaseFrom, memory_base_commit=SHA_RESULT
                ),
                record,
            )
    with pytest.raises(SyncGitProofError, match="base finalization"):
        authority.require_contract_bases_unchanged(
            replace(contract, code_base_commit=SHA_RESULT),
            record,
        )


def test_resolution_preview_reports_ready_and_incomplete_states(tmp_path: Path) -> None:
    side = _side(tmp_path, state="resolution-required")
    with (
        mock.patch.object(results, "unmerged_paths", return_value=()),
        mock.patch.object(results, "validate_staged_resolution"),
    ):
        ready = results.resolution_validation_preview(side, {})
    assert ready.returncode == 0
    assert ready.payload["state"] == "would-continue-sync-resolution"

    with (
        mock.patch.object(results, "unmerged_paths", return_value=("conflict.txt",)),
        mock.patch.object(
            results,
            "validate_staged_resolution",
            side_effect=SyncGitProofError("not staged"),
        ),
    ):
        incomplete = results.resolution_validation_preview(side, {})
    assert incomplete.returncode == 2
    assert incomplete.payload["state"] == "sync-resolution-incomplete"


def test_terminal_and_quarantine_replays_preserve_typed_outcomes(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    record = _record(tmp_path, "completed")
    mismatch = results.terminal_resolution_replay(
        contract,
        WorktreeArgs(memory_sync_choice="skip-memory"),
        record,
        {},
    )
    assert mismatch.payload["state"] == "sync-input-mismatch"
    completed_without_continue = results.terminal_resolution_replay(
        contract,
        WorktreeArgs(),
        record,
        {},
    )
    assert completed_without_continue.payload["state"] == "sync-already-completed"

    quarantine = SyncQuarantineRecord(
        contractPath=contract.contract_path.as_posix(),
        reason="bad",
        evidencePath=(tmp_path / "evidence").as_posix(),
        createdAt="2026-08-26T00:00:00+00:00",
    )
    assert (
        results.quarantine_replay(
            WorktreeArgs(resolution_action="cancel"), quarantine, {}
        ).returncode
        == 0
    )
    assert results.quarantine_replay(WorktreeArgs(), quarantine, {}).returncode == 2
    assert results.active_preview(record, {}).payload["state"] == "sync-active"


def test_sync_boundary_returns_input_and_internal_refusals(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    invalid = WorktreeArgs(
        resolution_action=cast(SyncResolutionAction, "invalid"),
    )
    assert (
        transaction.sync_contract_under_authority(contract, invalid, fetch={}).payload["state"]
        == "sync-input-invalid"
    )
    with mock.patch.object(transaction, "SyncOperationStore", side_effect=OSError("store failed")):
        refused = transaction.sync_contract_under_authority(contract, WorktreeArgs(), fetch={})
    assert refused.payload["state"] == "sync-operation-refused"


def test_read_and_route_sync_record_cover_quarantine_and_absent_resolution(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    quarantine = SyncQuarantineRecord(
        contractPath=contract.contract_path.as_posix(),
        reason="bad",
        evidencePath=(tmp_path / "evidence").as_posix(),
        createdAt="2026-08-26T00:00:00+00:00",
    )
    store = mock.Mock(spec=SyncOperationStore)
    store.read.return_value = quarantine
    replay = transaction._read_sync_record(
        contract,
        WorktreeArgs(resolution_action="cancel"),
        store,
        {},
    )
    assert isinstance(replay, WorktreeCommandResult)
    assert replay.payload["state"] == "sync-cancelled-no-authority"

    inactive = transaction._route_sync_record(
        contract,
        WorktreeArgs(resolution_action="continue"),
        store,
        None,
        {},
    )
    assert inactive.payload["state"] == "sync-resolution-not-active"


def test_admission_and_preflight_propagate_focused_refusals(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    code = _side(tmp_path)
    refusal = WorktreeCommandResult(2, {"state": "preflight-refused"})
    with (
        mock.patch.object(transaction, "source_pair", return_value=(SHA_SOURCE, "", False)),
        mock.patch.object(transaction, "preflight_official_pair", return_value=None),
        mock.patch.object(transaction, "side_record", return_value=code),
        mock.patch.object(transaction, "_already_current_result", return_value=None),
        mock.patch.object(transaction, "_preflight_participating_sides", return_value=refusal),
    ):
        assert (
            transaction._admit_and_run(
                contract, WorktreeArgs(), mock.Mock(spec=SyncOperationStore), None, {}
            )
            is refusal
        )

    with (
        mock.patch.object(
            transaction, "require_side_checkout", side_effect=SyncGitProofError("bad")
        ),
    ):
        result = transaction._preflight_participating_sides(code, None, {})
    assert result is not None and result.payload["state"] == "sync-side-preflight-failed"
    with (
        mock.patch.object(transaction, "require_side_checkout"),
        mock.patch.object(transaction, "git_status", return_value="dirty"),
    ):
        result = transaction._preflight_participating_sides(code, None, {})
    assert result is not None
    summary = result.payload["summary"]
    assert isinstance(summary, str) and "clean worktree" in summary


def test_already_current_memory_validation_failure_is_controlled(tmp_path: Path) -> None:
    contract = replace(
        _contract(tmp_path, external=True),
        code_base_commit=SHA_SOURCE,
        memory_base_commit=SHA_SOURCE,
    )
    code = _side(tmp_path, plan="already-current")
    memory = _side(tmp_path, side="memory", plan="already-current")
    with mock.patch.object(
        transaction,
        "validate_current_memory_side",
        side_effect=SyncGitProofError("ledger invalid"),
    ):
        result = transaction._already_current_result(contract, code, memory, {})
    assert result is not None and result.payload["state"] == "sync-work-branch-invalid"


def test_active_preview_routes_cancel_observe_resolution_and_mismatch(tmp_path: Path) -> None:
    record = _record(tmp_path)
    cancel = WorktreeCommandResult(0, {"state": "cancel"})
    active = WorktreeCommandResult(0, {"state": "active"})
    validation = WorktreeCommandResult(0, {"state": "validation"})
    mismatch = WorktreeCommandResult(2, {"state": "mismatch"})
    with (
        mock.patch.object(transaction, "cancel_preview", return_value=cancel),
        mock.patch.object(transaction, "active_preview", return_value=active),
        mock.patch.object(transaction, "resolution_validation_preview", return_value=validation),
        mock.patch.object(transaction, "manual_repair_result", return_value=mismatch),
    ):
        assert (
            transaction._active_preview(WorktreeArgs(resolution_action="cancel"), record, {})
            is cancel
        )
        assert transaction._active_preview(WorktreeArgs(), record, {}) is active
        resolving = record.model_copy(
            update={
                "phase": "code-resolution-required",
                "code": record.code.model_copy(update={"state": "resolution-required"}),
            }
        )
        assert (
            transaction._active_preview(WorktreeArgs(resolution_action="continue"), resolving, {})
            is validation
        )
        assert (
            transaction._active_preview(WorktreeArgs(resolution_action="continue"), record, {})
            is mismatch
        )


def test_resume_live_routes_each_active_transition(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    store = mock.Mock(spec=SyncOperationStore)
    record = _record(tmp_path)
    cancel = WorktreeCommandResult(0, {"state": "cancel"})
    continued = WorktreeCommandResult(0, {"state": "continue"})
    required = WorktreeCommandResult(2, {"state": "required"})
    automatic = WorktreeCommandResult(0, {"state": "automatic"})
    resolving = record.model_copy(update={"phase": "code-resolution-required"})
    with (
        mock.patch.object(transaction, "cancel_sync", return_value=cancel),
        mock.patch.object(transaction, "_continue_resolution", return_value=continued),
        mock.patch.object(transaction, "resolution_required", return_value=required),
        mock.patch.object(transaction, "_run_automatic", return_value=automatic),
    ):
        assert (
            transaction._resume_live(
                contract, WorktreeArgs(resolution_action="cancel"), store, record, {}
            )
            is cancel
        )
        assert (
            transaction._resume_live(
                contract, WorktreeArgs(resolution_action="continue"), store, record, {}
            )
            is continued
        )
        assert transaction._resume_live(contract, WorktreeArgs(), store, resolving, {}) is required
        assert transaction._resume_live(contract, WorktreeArgs(), store, record, {}) is automatic


def test_automatic_run_and_continue_resolution_report_proof_failures(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    store = mock.Mock(spec=SyncOperationStore)
    record = _record(tmp_path)
    with mock.patch.object(
        transaction,
        "_reconcile_completed_sides",
        side_effect=SyncGitProofError("proof failed"),
    ):
        failed = transaction._run_automatic(contract, store, record, {})
    assert failed.payload["state"] == "sync-git-proof-failed"

    unsupported = record.model_copy(update={"phase": "cancelling"})
    with mock.patch.object(transaction, "_reconcile_completed_sides", return_value=unsupported):
        stopped = transaction._run_automatic(contract, store, unsupported, {})
    assert stopped.payload["state"] == "sync-state-unrecognized"

    invalid = transaction._continue_resolution(contract, store, record, {})
    assert invalid.payload["state"] == "sync-resolution-not-required"
    resolving = record.model_copy(
        update={
            "phase": "code-resolution-required",
            "code": record.code.model_copy(update={"state": "resolution-required"}),
        }
    )
    with (
        mock.patch.object(
            transaction,
            "continue_side_merge",
            side_effect=SyncGitProofError("still conflicted"),
        ),
        mock.patch.object(transaction, "unmerged_paths", return_value=("conflict.txt",)),
        mock.patch.object(transaction, "update_record", return_value=resolving),
    ):
        incomplete = transaction._continue_resolution(contract, store, resolving, {})
    assert incomplete.payload["state"] == "sync-resolution-incomplete"


def test_unchanged_side_movement_and_completed_side_reconciliation(tmp_path: Path) -> None:
    store = mock.Mock(spec=SyncOperationStore)
    code = _side(tmp_path, plan="already-current")
    record = _record(tmp_path).model_copy(update={"code": code})
    with (
        mock.patch.object(transaction, "side_branch_head", return_value=SHA_RESULT),
        pytest.raises(SyncGitProofError, match="moved after sync admission"),
    ):
        transaction._run_side(store, record, "code")

    advanced = record.model_copy(update={"phase": "finalizing"})
    with (
        mock.patch.object(transaction, "_side_live_complete", return_value=True),
        mock.patch.object(transaction, "side_branch_head", return_value=SHA_RESULT),
        mock.patch.object(transaction, "_advance_after_side", return_value=advanced) as advance,
    ):
        assert transaction._reconcile_completed_sides(store, record) is advanced
    advance.assert_called_once()

    memory_record = _record(tmp_path, "running-memory", memory=True)
    assert memory_record.memory is not None
    skipped = memory_record.memory.model_copy(update={"plan": "skip"})
    memory_record = memory_record.model_copy(update={"memory": skipped})
    with mock.patch.object(transaction, "_advance_after_side", return_value=advanced) as advance:
        assert transaction._reconcile_completed_sides(store, memory_record) is advanced
    assert advance.call_args.args[3].resultHead == SHA_PRE

    assert transaction._side_live_complete(code) is True
    assert transaction._side_live_complete(skipped) is True


def test_missing_journal_and_admitted_memory_choice_route_to_typed_recovery(
    tmp_path: Path,
) -> None:
    contract = _contract(tmp_path, external=True)
    store = mock.Mock(spec=SyncOperationStore)
    store.read.return_value = None
    recovered = WorktreeCommandResult(0, {"state": "recovered"})
    with (
        mock.patch.object(transaction, "authority_refs_exist", return_value=True),
        mock.patch.object(transaction, "recover_missing_journal", return_value=recovered),
    ):
        assert transaction._read_sync_record(contract, WorktreeArgs(), store, {}) is recovered

    record = _record(tmp_path, memory=True)
    with mock.patch.object(transaction, "require_pinned_authority"):
        mismatch = transaction._resume_active(
            contract,
            WorktreeArgs(memory_sync_choice="skip-memory"),
            store,
            record,
            {},
        )
    assert mismatch.payload["state"] == "sync-input-mismatch"
