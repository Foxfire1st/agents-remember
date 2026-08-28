"""Forcing for current-record reconciliation versus worker progress."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

import pytest
from agents_remember.application.lifecycle.lifecycle_operation_worker import OperationRuntime
from agents_remember.application.worktree_tools import (
    OperationControlRequest,
    worktree_operation_control_tool,
)
from agents_remember.kernel.primitives.runtime_config import load_config
from agents_remember.worktrees.integration.direct_landing.direct_landing_errors import (
    DirectLandingError,
)
from agents_remember.worktrees.integration.direct_landing.direct_landing_recovery_state import (
    DirectLandingRecoveryClassification,
)
from agents_remember.worktrees.integration.lifecycle import lifecycle_operation_recovery
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_control_errors import (
    LifecycleControlError,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_controls import (
    legal_operation_controls,
)
from agents_remember.worktrees.integration.mutation_evidence import (
    reconcile_closeout_mutations,
)
from closeout_input_test_support import with_commit_proven, with_mutation_intent
from test_lifecycle_operation_controls_l2 import _dirty_closeout, _public_control


def _value(**fields: object) -> Any:
    return cast(Any, SimpleNamespace(**fields))


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


def test_direct_recovery_state_refuses_only_developer_decisions() -> None:
    recoverable = DirectLandingRecoveryClassification(state="recoverable")
    decision = DirectLandingRecoveryClassification(
        state="developer-decision",
        status="direct-landing-ambiguous",
        detail="developer must choose",
    )
    with mock.patch.object(
        lifecycle_operation_recovery,
        "classify_direct_landing_recovery",
        return_value=recoverable,
    ):
        lifecycle_operation_recovery._require_recoverable_direct_state(_value(), _value())
    with (
        mock.patch.object(
            lifecycle_operation_recovery,
            "classify_direct_landing_recovery",
            return_value=decision,
        ),
        pytest.raises(LifecycleControlError, match="direct-landing-ambiguous"),
    ):
        lifecycle_operation_recovery._require_recoverable_direct_state(_value(), _value())


def test_direct_recovery_failure_preserves_typed_error_and_current_record() -> None:
    recoverable = DirectLandingRecoveryClassification(state="recoverable")
    durable = _value(name="durable")
    fallback = _value(name="fallback")
    store = _value(read=mock.Mock(return_value=durable))
    error = DirectLandingError(
        "direct-landing-ledger-changed",
        "ledger changed",
        expected={"state": "accepted"},
        observed={"state": "changed"},
    )
    with mock.patch.object(
        lifecycle_operation_recovery,
        "classify_direct_landing_recovery",
        return_value=recoverable,
    ):
        translated = lifecycle_operation_recovery._direct_recovery_failure(
            _value(),
            store,
            fallback,
            error,
        )
    assert translated.status == error.status
    assert translated.next_action == "recover"
    assert translated.expected == error.expected
    assert translated.observed == error.observed
    assert lifecycle_operation_recovery._current_record(store, fallback) is durable
    assert (
        lifecycle_operation_recovery._current_record(
            _value(read=mock.Mock(return_value=None)),
            fallback,
        )
        is fallback
    )


def test_direct_recovery_failure_reclassifies_typed_developer_decision() -> None:
    decision = DirectLandingRecoveryClassification(
        state="developer-decision",
        status="direct-landing-output-ambiguous",
        detail="developer decision required",
    )
    store = _value(read=mock.Mock(return_value=None))
    fallback = _value()
    with mock.patch.object(
        lifecycle_operation_recovery,
        "classify_direct_landing_recovery",
        return_value=decision,
    ):
        refused = lifecycle_operation_recovery._direct_recovery_failure(
            _value(),
            store,
            fallback,
            DirectLandingError("direct-landing-cut", "cut"),
        )
    assert refused.status == decision.status
    assert refused.next_action == "developer-decision"


def test_direct_recovery_does_not_translate_invariant_runtime_errors() -> None:
    contract = _value(contract_path=Path("contract.json"))
    requeued = _value(generation=4)
    store = _value(resume_generation=mock.Mock(return_value=(requeued, True)))
    runtime = _value(contract=contract)
    with (
        mock.patch.object(lifecycle_operation_recovery, "load_contract", return_value=contract),
        mock.patch.object(lifecycle_operation_recovery, "_require_recoverable_direct_state"),
        mock.patch.object(
            lifecycle_operation_recovery, "DirectLandingRuntime", return_value=runtime
        ),
        mock.patch.object(
            lifecycle_operation_recovery,
            "execute_or_require_direct_landing_recovery",
            side_effect=RuntimeError("journal invariant failed"),
        ),
        pytest.raises(RuntimeError, match="journal invariant failed"),
    ):
        lifecycle_operation_recovery.recover_direct_landing_under_authority(
            contract,
            store,
            _value(generation=4),
        )
