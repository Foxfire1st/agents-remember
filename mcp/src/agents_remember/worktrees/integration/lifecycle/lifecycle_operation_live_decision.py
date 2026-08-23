"""Bounded live-evidence refusals shared by lifecycle controls."""

from __future__ import annotations

from agents_remember.models.lifecycles.operation import LifecycleOperationRecord
from agents_remember.worktrees.integration.closeout_ledger_recovery import (
    classify_closeout_ledger_recovery,
)
from agents_remember.worktrees.integration.direct_landing.direct_landing_recovery_state import (
    classify_direct_landing_recovery,
)
from agents_remember.worktrees.integration.initial_closeout_door_recovery import (
    classify_initial_closeout_door_recovery,
)
from agents_remember.worktrees.integration.integration_operation_decision import (
    IntegrationOperationObservation,
    classify_integration_operation,
    require_integration_operation_convergent,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_control_errors import (
    LifecycleControlError,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_control_projection import (
    generation_requires_recovery,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_recovery import (
    direct_recovery_refusal,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_worker_termination import (
    worker_exit_unproven,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract


def raise_live_evidence_decision(
    contract: WorktreeContract,
    record: LifecycleOperationRecord,
    *,
    integration_observation: IntegrationOperationObservation | None = None,
) -> None:
    """Raise the sole typed refusal selected from current durable/live evidence."""

    initial_door = classify_initial_closeout_door_recovery(contract, record)
    if initial_door.state == "developer-decision":
        raise LifecycleControlError(
            "closeout-initial-door-intent-missing",
            "the closeout record cannot prove the sole pre-intent publication cut",
            expected=initial_door.expected,
            observed=initial_door.observed,
            next_action="developer-decision",
        )
    ledger_recovery = classify_closeout_ledger_recovery(contract, record)
    if ledger_recovery.state == "developer-decision":
        raise LifecycleControlError(
            ledger_recovery.status,
            ledger_recovery.detail,
            expected=ledger_recovery.expected,
            observed=ledger_recovery.observed,
            next_action="developer-decision",
        )
    direct_recovery = classify_direct_landing_recovery(contract, record)
    if direct_recovery.state == "developer-decision":
        raise direct_recovery_refusal(direct_recovery)
    if record.operationKind != "integrate":
        if worker_exit_unproven(record):
            return
        if generation_requires_recovery(record) and record.status not in {
            "completed",
            "cancelled",
        }:
            raise immutable_recovery_refusal(record)
        return
    observed_integration = integration_observation or classify_integration_operation(
        contract,
        record,
    )
    require_integration_operation_convergent(observed_integration)
    if worker_exit_unproven(record):
        return
    if generation_requires_recovery(record) and record.status not in {
        "completed",
        "cancelled",
    }:
        raise immutable_recovery_refusal(record, integration=observed_integration)


def immutable_recovery_refusal(
    record: LifecycleOperationRecord,
    *,
    integration: IntegrationOperationObservation | None = None,
) -> LifecycleControlError:
    """Return bounded immutable output evidence and the sole executable recovery."""

    observed: dict[str, object] = {
        "generation": record.generation,
        "status": record.status,
        "phase": record.phase,
        "irreversibleBoundaryEntered": record.irreversibleBoundaryEntered,
        "mutationEvidence": {
            leg: evidence.model_dump(mode="json")
            for leg, evidence in sorted(record.mutationEvidence.items())
        },
        "recoveryCommits": (
            record.recoveryCommits.model_dump(mode="json")
            if record.recoveryCommits is not None
            else None
        ),
    }
    if integration is not None:
        observed["integrationRefs"] = (
            integration.refs.observed_payload() if integration.refs is not None else None
        )
        observed["integrationPublication"] = (
            record.integrationPublication.model_dump(mode="json")
            if record.integrationPublication is not None
            else None
        )
    return LifecycleControlError(
        "lifecycle-immutable-output-recovery-required",
        "this generation has immutable output intent or proof and can only recover",
        expected={
            "operationKind": record.operationKind,
            "generation": record.generation,
            "nextAction": "recover",
        },
        observed=observed,
        next_action="recover",
    )
