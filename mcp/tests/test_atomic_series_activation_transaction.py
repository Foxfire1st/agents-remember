"""Focused transition tests for activation-aware atomic-series synchronization."""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest import mock

from agents_remember.models.worktree import MemorySyncChoice
from agents_remember.worktrees.activation import (
    atomic_series_activation_transaction as transaction,
)
from agents_remember.worktrees.activation.atomic_series_activation import (
    AtomicSeriesActivationError,
)
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    RepoBranchPlan,
    WorktreeContract,
    default_series_contract,
)


def _contract(root: Path, *, external: bool = False) -> WorktreeContract:
    code_base = "a" * 40
    memory_base = "b" * 40
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
            base_commit=code_base,
        ),
        memory=(
            RepoBranchPlan(
                repo_path=root / "memory",
                source_branch="super",
                work_branch="ar/master",
                base_commit=memory_base,
            )
            if external
            else None
        ),
        task_root=root / "coordination" / "tasks" / "repo" / "master",
    )


def _observation(state: str) -> SimpleNamespace:
    return SimpleNamespace(source_fact=lambda: {"state": state})


def test_invalid_input_refuses_before_selection_or_sync(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    args = WorktreeArgs(memory_sync_choice=cast(MemorySyncChoice, "invented"))

    with (
        mock.patch.object(transaction, "publish_atomic_series_selection") as publish,
        mock.patch.object(transaction, "sync_contract_under_authority") as sync,
    ):
        result = transaction.sync_selected_atomic_series_under_authority(
            contract,
            activation_args=args,
            fetch={},
        )

    assert result.payload["state"] == "sync-input-invalid"
    publish.assert_not_called()
    sync.assert_not_called()


def test_completed_pass_with_source_moved_again_remains_reconciling(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    with (
        mock.patch.object(
            transaction,
            "publish_atomic_series_selection",
            return_value=_observation("reconciling"),
        ) as publish,
        mock.patch.object(
            transaction,
            "sync_contract_under_authority",
            return_value=WorktreeCommandResult(
                0,
                {"state": "sync-pass-completed-source-moved-again", "summary": "Moved."},
            ),
        ),
    ):
        result = transaction.sync_selected_atomic_series_under_authority(
            contract,
            activation_args=WorktreeArgs(),
            fetch={},
        )

    assert result.returncode == 2
    assert result.payload["atomicSeriesActivation"] == {"state": "reconciling"}
    publish.assert_called_once_with(contract, "reconciling")


def test_explicit_cancel_releases_exact_selection_to_vacant(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    with (
        mock.patch.object(
            transaction,
            "require_atomic_series_cancellation_owner",
            return_value=_observation("reconciling"),
        ) as selected,
        mock.patch.object(
            transaction,
            "sync_contract_under_authority",
            return_value=WorktreeCommandResult(
                0,
                {"state": "sync-cancelled", "summary": "Cancelled."},
            ),
        ),
        mock.patch.object(
            transaction,
            "release_atomic_series_selection",
            return_value=_observation("vacant"),
        ) as release,
    ):
        result = transaction.sync_selected_atomic_series_under_authority(
            contract,
            activation_args=WorktreeArgs(resolution_action="cancel"),
            fetch={},
        )

    assert result.returncode == 0
    assert result.payload["state"] == "sync-cancelled"
    assert result.payload["atomicSeriesActivation"] == {"state": "vacant"}
    selected.assert_called_once_with(contract)
    release.assert_called_once_with(contract)


def test_no_authority_cancel_replay_also_releases_selection(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    with (
        mock.patch.object(
            transaction,
            "require_atomic_series_cancellation_owner",
            return_value=_observation("vacant"),
        ),
        mock.patch.object(
            transaction,
            "sync_contract_under_authority",
            return_value=WorktreeCommandResult(
                0,
                {"state": "sync-cancelled-no-authority", "summary": "Archived."},
            ),
        ),
        mock.patch.object(
            transaction,
            "release_atomic_series_selection",
            return_value=_observation("vacant"),
        ),
    ):
        result = transaction.sync_selected_atomic_series_under_authority(
            contract,
            activation_args=WorktreeArgs(resolution_action="cancel"),
            fetch={},
        )

    assert result.returncode == 0
    assert result.payload["state"] == "sync-cancelled-no-authority"
    assert result.payload["atomicSeriesActivation"] == {"state": "vacant"}


def test_only_exact_current_pair_publishes_active(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    reconciling = _observation("reconciling")
    active = _observation("active")
    with (
        mock.patch.object(
            transaction,
            "publish_atomic_series_selection",
            side_effect=[reconciling, active],
        ) as publish,
        mock.patch.object(
            transaction,
            "sync_contract_under_authority",
            return_value=WorktreeCommandResult(0, {"state": "synced"}),
        ),
        mock.patch.object(transaction, "load_contract", return_value=contract),
        mock.patch.object(
            transaction,
            "source_pair",
            return_value=(contract.code_base_commit, "", False),
        ),
    ):
        result = transaction.sync_selected_atomic_series_under_authority(
            contract,
            activation_args=WorktreeArgs(),
            fetch={},
        )

    assert result.returncode == 0
    assert result.payload["state"] == "synced"
    assert result.payload["atomicSeriesActivation"] == {"state": "active"}
    assert publish.call_args_list == [
        mock.call(contract, "reconciling"),
        mock.call(contract, "active"),
    ]


def test_skip_memory_completion_stays_reconciling_until_pair_is_current(
    tmp_path: Path,
) -> None:
    contract = _contract(tmp_path, external=True)
    reconciling = _observation("reconciling")
    with (
        mock.patch.object(
            transaction,
            "publish_atomic_series_selection",
            return_value=reconciling,
        ) as publish,
        mock.patch.object(
            transaction,
            "sync_contract_under_authority",
            return_value=WorktreeCommandResult(0, {"state": "synced"}),
        ),
        mock.patch.object(transaction, "load_contract", return_value=contract),
        mock.patch.object(
            transaction,
            "source_pair",
            return_value=(contract.code_base_commit, "c" * 40, True),
        ),
    ):
        result = transaction.sync_selected_atomic_series_under_authority(
            contract,
            activation_args=WorktreeArgs(memory_sync_choice="skip-memory"),
            fetch={},
        )

    assert result.returncode == 2
    assert result.payload["state"] == "atomic-series-source-pair-incomplete"
    assert result.payload["nextArgs"] == {
        "contract_path": contract.contract_path.as_posix(),
        "dry_run": False,
        "memory_sync_choice": "merge-memory",
    }
    publish.assert_called_once_with(contract, "reconciling")


def test_activation_contract_race_is_a_contract_addressed_retry(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    changed = replace(contract, closeout_status="completed")
    with (
        mock.patch.object(transaction, "fetch_source_upstreams", return_value={}),
        mock.patch.object(transaction, "integration_authority_lock", return_value=nullcontext()),
        mock.patch.object(transaction, "load_contract", return_value=changed),
    ):
        result = transaction.activate_atomic_series_contract(contract)

    assert isinstance(result, WorktreeCommandResult)
    assert result.payload["state"] == "atomic-series-contract-changed"
    assert result.payload["nextTool"] == "worktree_sync"
    assert result.payload["nextArgs"] == {
        "contract_path": contract.contract_path.as_posix(),
        "dry_run": False,
    }


def test_activation_store_failure_is_translated_once_at_boundary(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    with mock.patch.object(
        transaction,
        "publish_atomic_series_selection",
        side_effect=AtomicSeriesActivationError("activation-unreadable", "broken"),
    ):
        result = transaction.sync_selected_atomic_series_under_authority(
            contract,
            activation_args=WorktreeArgs(),
            fetch={},
        )

    assert result.returncode == 2
    assert result.payload["state"] == "activation-unreadable"
    assert result.payload["nextTool"] == "worktree_sync"


def test_dry_run_never_publishes_activation(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    preview = WorktreeCommandResult(0, {"state": "would-sync"})
    with (
        mock.patch.object(transaction, "publish_atomic_series_selection") as publish,
        mock.patch.object(
            transaction,
            "sync_contract_under_authority",
            return_value=preview,
        ) as sync,
    ):
        result = transaction.sync_selected_atomic_series_under_authority(
            contract,
            activation_args=WorktreeArgs(dry_run=True),
            fetch={},
        )

    assert result is preview
    publish.assert_not_called()
    sync.assert_called_once()
