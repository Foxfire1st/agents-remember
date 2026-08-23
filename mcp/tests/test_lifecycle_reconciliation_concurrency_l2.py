"""Forcing for current-record reconciliation versus worker progress."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from unittest import mock

from agents_remember.application.lifecycle.lifecycle_operation_worker import OperationRuntime
from agents_remember.application.worktree_tools import (
    OperationControlRequest,
    worktree_operation_control_tool,
)
from agents_remember.kernel.primitives.runtime_config import load_config
from agents_remember.worktrees.integration.lifecycle import lifecycle_operation_recovery
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_controls import (
    legal_operation_controls,
)
from agents_remember.worktrees.integration.mutation_evidence import (
    reconcile_closeout_mutations,
)
from closeout_input_test_support import with_commit_proven, with_mutation_intent
from test_lifecycle_operation_controls_l2 import _dirty_closeout, _public_control


def test_public_control_reconciliation_cannot_overwrite_concurrent_worker_progress(
    tmp_path: Path,
) -> None:
    contract, _operation_input, store, record = _dirty_closeout(tmp_path)
    code = record.mutationEvidence["code"]
    assert code.acceptedBefore is not None
    intent = code.model_copy(
        update={
            "state": "mutation-intent",
            "before": code.acceptedBefore,
            "expectedOutputTree": code.acceptedBefore.candidateTree,
        }
    )
    record = store.update(
        lambda current: current.model_copy(
            update={"mutationEvidence": {**current.mutationEvidence, "code": intent}}
        )
    )
    row = next(
        item for item in legal_operation_controls(contract, record) if item["action"] == "recover"
    )
    row["arguments"]["dry_run"] = True
    config = load_config(Path(record.input.configPath))
    entered = Event()
    release = Event()
    first = True

    def blocked_reconciliation(current):
        nonlocal first
        if first:
            first = False
            entered.set()
            assert release.wait(timeout=10)
        return reconcile_closeout_mutations(current)

    with (
        mock.patch.object(
            lifecycle_operation_recovery,
            "reconcile_closeout_mutations",
            side_effect=blocked_reconciliation,
        ),
        ThreadPoolExecutor(max_workers=1) as pool,
    ):
        future = pool.submit(_public_control, config, row)
        assert entered.wait(timeout=10)
        with mock.patch(
            "agents_remember.application.lifecycle.lifecycle_operation_worker._stamp",
            return_value="2026-08-23T12:34:56+00:00",
        ):
            OperationRuntime(store).progress("memory-preflight", {})
        progressed = store.read()
        assert progressed is not None
        progressed_bytes = store.path.read_bytes()
        release.set()
        response = future.result(timeout=10)

    assert response["ok"] is True
    projection = response["lifecycleOperation"]
    assert projection["phase"] == "memory-preflight"
    assert projection["heartbeatAt"] == "2026-08-23T12:34:56+00:00"
    assert projection["currentCommand"] == "lifecycle stage: memory-preflight"
    assert store.path.read_bytes() == progressed_bytes
    durable = store.read()
    assert durable is not None
    assert durable.phase == progressed.phase
    assert durable.heartbeatAt == progressed.heartbeatAt
    assert durable.currentCommand == progressed.currentCommand


def test_stale_cancel_at_commit_boundary_returns_and_executes_exact_recovery(
    tmp_path: Path,
) -> None:
    contract, _operation_input, store, record = _dirty_closeout(tmp_path)
    stale_cancel = next(
        item for item in legal_operation_controls(contract, record) if item["action"] == "cancel"
    )
    store.update(with_mutation_intent)
    proven = store.update(with_commit_proven)
    config = load_config(Path(proven.input.configPath))

    refused = _public_control(config, stale_cancel)
    assert refused["ok"] is False
    assert refused["status"] == "lifecycle-immutable-output-recovery-required"
    assert refused["nextAction"] == "recover"
    assert refused["nextTool"] == "worktree_operation_control"
    assert refused["nextArgs"]["expected_generation"] == proven.generation
    assert refused["observed"]["irreversibleBoundaryEntered"] is True
    assert refused["observed"]["mutationEvidence"]["code"]["state"] == "commit-proven"
    with mock.patch(
        "agents_remember.worktrees.integration.lifecycle.lifecycle_operation_controls.launch_detached_worker"
    ) as launch:
        recovered = worktree_operation_control_tool(
            config,
            OperationControlRequest(**refused["nextArgs"]),
        )
    assert recovered["ok"] is True
    assert recovered["lifecycleOperation"]["generation"] == proven.generation
    launch.assert_called_once()
