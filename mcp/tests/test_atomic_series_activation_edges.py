"""Focused malformed-authority and boundary tests for atomic-series selection."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import cast
from unittest import mock

import pytest
from agents_remember.models.structural.atomic_series_activation import (
    AtomicSeriesActivationRecord,
    AtomicSeriesActivationState,
    AtomicSeriesObservedState,
    AtomicSeriesSourcePair,
    AtomicSeriesSourceRef,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.worktrees.activation import atomic_series_activation as activation
from agents_remember.worktrees.activation import atomic_series_activation_release as release
from agents_remember.worktrees.activation import atomic_series_activation_terminal as terminal
from agents_remember.worktrees.activation import atomic_series_activation_transaction as transaction
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    RepoBranchPlan,
    WorktreeContract,
    default_series_contract,
)
from pydantic import ValidationError

SHA = "0" * 40
NOW = "2026-08-26T00:00:00+00:00"
MASTER = TaskDocumentRef(repository="repo", path="master/task.json")
OTHER_MASTER = TaskDocumentRef(repository="repo", path="other/task.json")


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
            base_commit=SHA,
        ),
        memory=(
            RepoBranchPlan(
                repo_path=root / "memory",
                source_branch="super",
                work_branch="ar/master",
                base_commit=SHA,
            )
            if external
            else None
        ),
        task_root=root / "coordination" / "tasks" / "repo" / "master",
    )


def _pair(name: str = "code") -> AtomicSeriesSourcePair:
    return AtomicSeriesSourcePair(
        code=AtomicSeriesSourceRef(repositoryIdentity=f"/{name}", sourceBranch="super")
    )


def _record(
    root: Path,
    pair: AtomicSeriesSourcePair,
    *,
    state: AtomicSeriesActivationState = "reconciling",
    master: TaskDocumentRef = MASTER,
    contract_path: Path | None = None,
) -> AtomicSeriesActivationRecord:
    return AtomicSeriesActivationRecord(
        sourcePairFingerprint=activation.source_pair_fingerprint(pair),
        sourcePair=pair,
        selectedMaster=master,
        contractPath=(contract_path or _contract(root).contract_path).as_posix(),
        state=state,
        revision=1,
        selectedAt=NOW,
    )


def _observation(
    root: Path,
    pair: AtomicSeriesSourcePair,
    *,
    state: AtomicSeriesObservedState = "reconciling",
    record: AtomicSeriesActivationRecord | None = None,
) -> activation.AtomicSeriesActivationObservation:
    return activation.AtomicSeriesActivationObservation(
        pair,
        activation.source_pair_fingerprint(pair),
        root / "activation.json",
        state,
        record,
        error_type="broken" if state == "unreadable" else None,
        detail="broken authority" if state == "unreadable" else None,
    )


def _object_dict(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return cast(dict[str, object], value)


def test_source_ref_rejects_blank_identity_cells() -> None:
    with pytest.raises(ValidationError, match="must not be blank"):
        AtomicSeriesSourceRef(repositoryIdentity="   ", sourceBranch="super")


def test_external_pair_requires_memory_repository(tmp_path: Path) -> None:
    contract = replace(_contract(tmp_path, external=True), memory_repo_path=None)
    with (
        mock.patch.object(
            activation,
            "_atomic_series_source_ref",
            return_value=AtomicSeriesSourceRef(repositoryIdentity="/code", sourceBranch="super"),
        ),
        pytest.raises(
            activation.AtomicSeriesActivationError,
            match="no memory repository",
        ),
    ):
        activation.atomic_series_source_pair(contract)


def test_source_ref_requires_repository_identity(tmp_path: Path) -> None:
    with (
        mock.patch.object(activation, "repository_identity", return_value=None),
        pytest.raises(
            activation.AtomicSeriesActivationError,
            match="identity is unavailable",
        ),
    ):
        activation._atomic_series_source_ref(tmp_path, "super", side="code")


def test_pair_observation_requires_explicit_coordination_root() -> None:
    with pytest.raises(ValueError, match="coordination_root is required"):
        activation.observe_atomic_series(_pair())


def test_terminal_contract_cannot_publish_selection(tmp_path: Path) -> None:
    contract = replace(_contract(tmp_path), integration_status="completed")
    with pytest.raises(activation.AtomicSeriesActivationError, match="cannot be selected"):
        activation.publish_atomic_series_selection(contract, "active")


def test_selected_and_cancel_owner_guards_require_exact_observation(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    pair = _pair()
    exact = _observation(tmp_path, pair, record=_record(tmp_path, pair))
    with (
        mock.patch.object(activation, "observe_atomic_series", return_value=exact),
        mock.patch.object(activation, "series_master_ref", return_value=MASTER),
    ):
        assert activation.require_selected_atomic_series(contract) is exact

    vacant = _observation(tmp_path, pair, state="vacant", record=None)
    with (
        mock.patch.object(activation, "observe_atomic_series", return_value=vacant),
        mock.patch.object(activation, "series_master_ref", return_value=MASTER),
    ):
        with pytest.raises(activation.AtomicSeriesActivationError, match="exact selected"):
            activation.require_selected_atomic_series(contract)
        with pytest.raises(activation.AtomicSeriesActivationError, match="exact selected"):
            activation.require_atomic_series_cancellation_owner(contract)


def test_observe_path_translates_lstat_failure(tmp_path: Path) -> None:
    pair = _pair()
    with mock.patch.object(Path, "lstat", side_effect=OSError("blocked")):
        observed = activation.observe_atomic_series_path(tmp_path, pair, tmp_path / "authority")
    assert observed.state == "unreadable"
    assert observed.error_type == "OSError"


def test_record_identity_and_contract_authority_mismatches_fail_closed(tmp_path: Path) -> None:
    pair = _pair()
    other_pair = _pair("other")
    path = tmp_path / "authority.json"
    with pytest.raises(activation.AtomicSeriesActivationError, match="source-pair path"):
        activation._require_record_identity(_record(tmp_path, other_pair), pair, "f" * 64, path)

    outside = _record(tmp_path, pair, contract_path=tmp_path / "outside.md")
    with pytest.raises(activation.AtomicSeriesActivationError, match="outside"):
        activation._load_selected_contract(_contract(tmp_path).coordination_root, outside)

    contract = _contract(tmp_path)
    contract.contract_path.mkdir(parents=True)
    nonregular = _record(tmp_path, pair, contract_path=contract.contract_path)
    with pytest.raises(activation.AtomicSeriesActivationError, match="not a regular file"):
        activation._load_selected_contract(contract.coordination_root, nonregular)


def test_loaded_contract_must_retain_pair_and_master_identity(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    contract.contract_path.parent.mkdir(parents=True)
    contract.contract_path.write_text("contract", encoding="utf-8")
    pair = _pair()
    record = _record(tmp_path, pair, contract_path=contract.contract_path)
    with (
        mock.patch.object(activation, "load_contract", return_value=contract),
        mock.patch.object(activation, "_require_canonical_series_contract"),
        mock.patch.object(activation, "atomic_series_source_pair", return_value=_pair("other")),
        pytest.raises(activation.AtomicSeriesActivationError, match="recorded source pair"),
    ):
        activation._load_selected_contract(contract.coordination_root, record)
    with (
        mock.patch.object(activation, "load_contract", return_value=contract),
        mock.patch.object(activation, "_require_canonical_series_contract"),
        mock.patch.object(activation, "atomic_series_source_pair", return_value=pair),
        mock.patch.object(activation, "series_master_ref", return_value=OTHER_MASTER),
        pytest.raises(activation.AtomicSeriesActivationError, match="recorded master"),
    ):
        activation._load_selected_contract(contract.coordination_root, record)


def test_canonical_contract_and_master_ref_reject_noncanonical_paths(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    with pytest.raises(activation.AtomicSeriesActivationError, match="canonical"):
        activation._require_canonical_series_contract(replace(contract, kind="leaf"))
    escaped = replace(contract, task_root=tmp_path / "outside")
    with pytest.raises(activation.AtomicSeriesActivationError, match="escapes"):
        activation.series_master_ref(escaped)


def test_archive_records_absence_and_refuses_unpreservable_entries(tmp_path: Path) -> None:
    pair = _pair()
    path = tmp_path / "authority.json"
    unreadable = _observation(tmp_path, pair, state="unreadable")
    with (
        mock.patch.object(Path, "lstat", side_effect=OSError("inspection failed")),
        pytest.raises(activation.AtomicSeriesActivationError, match="cannot inspect"),
    ):
        activation._archive_unreadable_selection(path, unreadable, MASTER, archived_at=NOW)

    activation._archive_unreadable_selection(path, unreadable, MASTER, archived_at=NOW)
    evidence = list((path.parent / "archive").rglob("*.json"))
    assert len(evidence) == 1

    path.write_text("malformed", encoding="utf-8")
    with (
        mock.patch.object(activation, "_read_regular_entry", side_effect=OSError("unreadable")),
        pytest.raises(activation.AtomicSeriesActivationError, match="retain malformed"),
    ):
        activation._archive_unreadable_selection(path, unreadable, MASTER, archived_at=NOW)

    path.unlink()
    path.mkdir()
    with (
        mock.patch.object(Path, "exists", return_value=True),
        pytest.raises(activation.AtomicSeriesActivationError, match="already exists"),
    ):
        activation._archive_unreadable_selection(path, unreadable, MASTER, archived_at=NOW)
    with (
        mock.patch.object(Path, "exists", return_value=False),
        mock.patch.object(activation, "atomic_replace", side_effect=OSError("rename failed")),
        pytest.raises(activation.AtomicSeriesActivationError, match="cannot preserve"),
    ):
        activation._archive_unreadable_selection(path, unreadable, MASTER, archived_at=NOW)


def test_release_refuses_unreadable_missing_and_recordless_internal_calls(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    pair = _pair()
    unreadable = _observation(tmp_path, pair, state="unreadable")
    missing = _observation(tmp_path, pair, state="vacant")
    with (
        mock.patch.object(release, "atomic_series_source_pair", return_value=pair),
        mock.patch.object(release, "series_master_ref", return_value=MASTER),
        mock.patch.object(release, "observe_atomic_series_path", return_value=unreadable),
        pytest.raises(activation.AtomicSeriesActivationError, match="broken authority"),
    ):
        release.release_atomic_series_selection(contract)
    with (
        mock.patch.object(release, "atomic_series_source_pair", return_value=pair),
        mock.patch.object(release, "series_master_ref", return_value=MASTER),
        mock.patch.object(release, "observe_atomic_series_path", return_value=missing),
        pytest.raises(activation.AtomicSeriesActivationError, match="existing exact"),
    ):
        release.release_atomic_series_selection(contract)
    with pytest.raises(RuntimeError, match="requires a selected record"):
        release._release_record(missing, contract, MASTER, selected_at=NOW)

    vacant_record = _record(tmp_path, pair, state="vacant")
    vacant = _observation(tmp_path, pair, state="vacant", record=vacant_record)
    assert release._release_record(vacant, contract, MASTER, selected_at=NOW) is vacant


def test_terminal_release_reports_failure_and_true_absence(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    result = WorktreeCommandResult(0, {"state": "completed"})
    with mock.patch.object(
        terminal,
        "release_terminal_atomic_series_selection_if_exact",
        side_effect=RuntimeError("release failed"),
    ):
        failed = terminal.with_terminal_atomic_series_release(contract, result, dry_run=False)
    assert failed.returncode == 2
    release_payload = _object_dict(failed.payload["atomicSeriesActivationRelease"])
    assert release_payload["state"] == "release-failed"

    pair = _pair()
    absent = _observation(tmp_path, pair, state="vacant")
    with mock.patch.object(
        terminal,
        "release_terminal_atomic_series_selection_if_exact",
        return_value=absent,
    ):
        released = terminal.with_terminal_atomic_series_release(contract, result, dry_run=False)
    assert released.payload["atomicSeriesActivationRelease"] == {"state": "already-vacant"}


def test_activation_transaction_translates_dry_run_invalid_and_fetch_failure(
    tmp_path: Path,
) -> None:
    contract = _contract(tmp_path)
    assert transaction.activate_atomic_series_contract(contract, dry_run=True) is contract
    invalid = WorktreeCommandResult(2, {"state": "invalid"})
    with mock.patch.object(
        transaction, "atomic_series_activation_input_refusal", return_value=invalid
    ):
        assert transaction.activate_atomic_series_contract(contract) is invalid
    with (
        mock.patch.object(transaction, "atomic_series_activation_input_refusal", return_value=None),
        mock.patch.object(transaction, "fetch_source_upstreams", side_effect=OSError("offline")),
    ):
        refused = transaction.activate_atomic_series_contract(contract)
    assert isinstance(refused, WorktreeCommandResult)
    assert refused.payload["state"] == "atomic-series-admission-failed"


def test_selected_sync_continue_cancel_replay_and_code_only_incomplete_pair(
    tmp_path: Path,
) -> None:
    contract = _contract(tmp_path)
    pair = _pair()
    selected = _observation(tmp_path, pair, record=_record(tmp_path, pair))
    unresolved = WorktreeCommandResult(2, {"state": "sync-resolution-required"})
    with (
        mock.patch.object(
            transaction, "require_selected_atomic_series", return_value=selected
        ) as require,
        mock.patch.object(transaction, "sync_contract_under_authority", return_value=unresolved),
    ):
        result = transaction.sync_selected_atomic_series_under_authority(
            contract,
            activation_args=WorktreeArgs(resolution_action="continue"),
            fetch={},
        )
    assert result.returncode == 2
    require.assert_called_once_with(contract)

    cancelled = WorktreeCommandResult(0, {"state": "sync-resolution-not-active"})
    vacant_record = _record(tmp_path, pair, state="vacant")
    vacant = _observation(tmp_path, pair, state="vacant", record=vacant_record)
    with (
        mock.patch.object(
            transaction, "require_atomic_series_cancellation_owner", return_value=selected
        ),
        mock.patch.object(transaction, "sync_contract_under_authority", return_value=cancelled),
        mock.patch.object(transaction, "release_atomic_series_selection", return_value=vacant),
    ):
        result = transaction.sync_selected_atomic_series_under_authority(
            contract,
            activation_args=WorktreeArgs(resolution_action="cancel"),
            fetch={},
        )
    assert result.payload["state"] == "sync-cancelled"

    external = _contract(tmp_path, external=True)
    reconciling = _observation(tmp_path, pair, record=_record(tmp_path, pair))
    with (
        mock.patch.object(transaction, "publish_atomic_series_selection", return_value=reconciling),
        mock.patch.object(
            transaction,
            "sync_contract_under_authority",
            return_value=WorktreeCommandResult(0, {"state": "synced"}),
        ),
        mock.patch.object(transaction, "load_contract", return_value=external),
        mock.patch.object(
            transaction,
            "source_pair",
            return_value=("f" * 40, external.memory_base_commit, True),
        ),
    ):
        incomplete = transaction.sync_selected_atomic_series_under_authority(
            external,
            activation_args=WorktreeArgs(),
            fetch={},
        )
    assert incomplete.payload["state"] == "atomic-series-source-pair-incomplete"
    assert "memory_sync_choice" not in _object_dict(incomplete.payload["nextArgs"])


def test_admission_refusal_preserves_typed_retry_arguments(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    refusal = transaction._admission_refusal(
        contract,
        WorktreeArgs(memory_sync_choice="skip-memory", resolution_action="cancel"),
        status="failed",
        detail="broken",
    )
    next_args = _object_dict(refusal.payload["nextArgs"])
    assert next_args["memory_sync_choice"] == "skip-memory"
    assert next_args["resolution_action"] == "cancel"
