"""Cancellation execution for one durable lifecycle operation generation."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import NoReturn

from agents_remember.controlplane.task_publication_lock import task_publication_lock
from agents_remember.models.lifecycles.door import DoorPublicationEvidence
from agents_remember.models.lifecycles.operation import (
    LifecycleOperationKind,
    LifecycleOperationProjection,
    LifecycleOperationRecord,
    OrganizationalCompletionRepairEvidence,
)
from agents_remember.models.lifecycles.termination import (
    LifecycleCancellationEvidence,
    WorkerTerminationEvidence,
)
from agents_remember.worktrees.integration.closeout.door import (
    prepare_door_publication,
    successor_waiting_door,
)
from agents_remember.worktrees.integration.configured_contract_authority import (
    reread_configured_contract,
)
from agents_remember.worktrees.integration.integration_operation_decision import (
    raise_integration_decision,
)
from agents_remember.worktrees.integration.integration_ref_state import (
    IntegrationRefDecisionError,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_control_errors import (
    LifecycleControlError,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_control_evidence import (
    prove_cancellable_git,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_door_control import (
    complete_pending_door,
    complete_pending_door_locked,
    project_closeout_refresh,
    record_door_intent,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_identity import (
    closeout_contract_sha256,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_projection import (
    operation_projection,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_store import (
    LifecycleOperationStore,
)
from agents_remember.worktrees.integration.lifecycle.worker.state import release_worker_after_exit
from agents_remember.worktrees.integration.lifecycle.worker.termination import (
    bounded_worker_termination_outcome,
    public_worker_termination_evidence,
    signal_worker_and_prove_exit,
    worker_termination_request,
)
from agents_remember.worktrees.integration.organizational_completion_repair import (
    OrganizationalRepairPublicationError,
    prepare_organizational_completion_repair,
)
from agents_remember.worktrees.queue.closeout_queue import CloseoutQueueError
from agents_remember.worktrees.worktree_contract import WorktreeContract, load_contract


def cancel_operation(
    contract: WorktreeContract,
    store: LifecycleOperationStore,
    record: LifecycleOperationRecord,
    *,
    dry_run: bool,
) -> LifecycleOperationProjection:
    """Cancel one generation without allowing its worker authority to escape."""

    terminal = _terminal_cancel_projection(contract, store, record, dry_run=dry_run)
    if terminal is not None:
        return terminal
    record = _terminate_worker(
        store,
        record,
        dry_run=dry_run,
        cancellation_pending=True,
    )
    evidence, record = prove_cancellable_git(store, record, publish=not dry_run)
    if dry_run:
        return operation_projection(record, contract=contract)
    cancelled, contract = _publish_cancelled_outcome(
        contract,
        store,
        record,
        evidence=evidence,
        stamp=_stamp(),
    )
    contract = _complete_organizational_repair(contract, cancelled, dry_run=False)
    projection = operation_projection(cancelled, contract=contract)
    return project_closeout_refresh(projection, contract, cancelled, dry_run=False)


def _terminal_cancel_projection(
    contract: WorktreeContract,
    store: LifecycleOperationStore,
    record: LifecycleOperationRecord,
    *,
    dry_run: bool,
) -> LifecycleOperationProjection | None:
    """Finish an already-cancelled generation or reject a completed one."""

    if record.status == "cancelled":
        completed = complete_pending_door(contract, store, record, dry_run=dry_run)
        observed_contract = contract if dry_run else load_contract(contract.contract_path)
        current_contract = _complete_organizational_repair(
            observed_contract,
            completed,
            dry_run=dry_run,
        )
        projection = operation_projection(completed, contract=current_contract)
        return project_closeout_refresh(projection, current_contract, completed, dry_run=dry_run)
    if record.status == "completed":
        if record.workerPid is not None:
            terminated = _terminate_worker(
                store,
                record,
                dry_run=dry_run,
                cancellation_pending=False,
            )
            return operation_projection(terminated, contract=contract)
        raise LifecycleControlError(
            "lifecycle-cancel-completed",
            "a completed operation cannot be cancelled; choose retire or supersede",
            next_action="retire",
        )
    return None


def _publish_cancelled_outcome(
    contract: WorktreeContract,
    store: LifecycleOperationStore,
    record: LifecycleOperationRecord,
    *,
    evidence: LifecycleCancellationEvidence,
    stamp: str,
) -> tuple[LifecycleOperationRecord, WorktreeContract]:
    """Publish one cancelled journal outcome and its waiting-door successor."""

    def cancelled_record(current: LifecycleOperationRecord) -> LifecycleOperationRecord:
        return current.model_copy(
            update={
                "status": "cancelled",
                "phase": "cancelled",
                "finishedAt": stamp,
                "cancelRequested": True,
                "currentCommand": "publish cancelled journal outcome",
                "generationDisposition": "cancelled",
                "cancellationEvidence": evidence,
                "terminationReturnStatus": None,
                "terminationReturnPhase": None,
                "guidance": _cancelled_guidance(record.operationKind),
            }
        )

    if record.operationKind in {"closeout", "direct-landing"}:
        operation_input = record.input
        with task_publication_lock(contract.coordination_root, contract.repo_name):
            contract, _location = reread_configured_contract(
                contract,
                operation_input.configPath,
            )
            claimed = _require_cancelled_door_owner(contract, record)
            successor = successor_waiting_door(
                claimed.generation,
                declared_by="lifecycle-cancel",
                declared_at=stamp,
            )
            intent = prepare_door_publication(contract, successor)
            cancelled = store.update(
                lambda current: record_door_intent(
                    cancelled_record(current),
                    intent,
                    generation_disposition="cancelled",
                )
            )
            cancelled = complete_pending_door_locked(contract, store, cancelled)
            contract = load_contract(contract.contract_path)
    else:
        cancelled = store.update(cancelled_record)
    return cancelled, contract


def _require_cancelled_door_owner(
    contract: WorktreeContract,
    record: LifecycleOperationRecord,
) -> DoorPublicationEvidence:
    claimed = record.doorPublication
    if (
        claimed is not None
        and claimed.state == "proven"
        and claimed.generation.disposition == "claimed"
        and claimed.generation.operationKind == record.operationKind
        and claimed.generation.operationFingerprint == record.fingerprint
        and claimed.generation.claimedOperationKey == record.operationKey
        and contract.closeout_door == claimed.generation
    ):
        return claimed
    raise LifecycleControlError(
        "closeout-cancel-claim-mismatch",
        "closeout cancellation requires its exact claimed journal/door owner",
        expected={
            "operationFingerprint": record.fingerprint,
            "doorDisposition": "claimed",
        },
        observed={
            "doorDisposition": contract.closeout_door.disposition if contract.closeout_door else "",
            "publicationState": claimed.state if claimed is not None else "",
        },
        next_action="developer-decision",
    )


def _complete_organizational_repair(
    contract: WorktreeContract,
    record: LifecycleOperationRecord,
    *,
    dry_run: bool,
) -> WorktreeContract:
    repair = record.organizationalRepair
    applicable = (record.operationKind, repair is not None, dry_run)
    if applicable != ("integrate", True, False):
        return contract
    assert repair is not None
    return _prepare_completed_organizational_repair(contract, record, repair)


def _prepare_completed_organizational_repair(
    contract: WorktreeContract,
    record: LifecycleOperationRecord,
    repair: OrganizationalCompletionRepairEvidence,
) -> WorktreeContract:
    try:
        return prepare_organizational_completion_repair(load_contract(contract.contract_path))
    except (
        IntegrationRefDecisionError,
        OrganizationalRepairPublicationError,
        CloseoutQueueError,
    ) as exc:
        _raise_organizational_repair_failure(contract, record, repair, exc)


def _raise_organizational_repair_failure(
    contract: WorktreeContract,
    record: LifecycleOperationRecord,
    repair: OrganizationalCompletionRepairEvidence,
    error: IntegrationRefDecisionError | OrganizationalRepairPublicationError | CloseoutQueueError,
) -> NoReturn:
    if isinstance(error, IntegrationRefDecisionError):
        raise_integration_decision(error.classification.decision_payload())
    if isinstance(error, OrganizationalRepairPublicationError):
        raise LifecycleControlError(
            error.status,
            error.detail,
            expected=error.expected,
            observed=error.observed,
            next_action=error.next_action,
        ) from error
    _raise_queue_repair_failure(contract, record, repair, error)


def _raise_queue_repair_failure(
    contract: WorktreeContract,
    record: LifecycleOperationRecord,
    repair: OrganizationalCompletionRepairEvidence,
    error: CloseoutQueueError,
) -> NoReturn:
    observed = load_contract(contract.contract_path)
    raise LifecycleControlError(
        error.status,
        "organizational reset evidence contradicts the live contract",
        expected={
            "candidateState": record.candidateState,
            "acceptedContractSha256": repair.acceptedContractSha256,
            "resetContractSha256": repair.resetContractSha256,
        },
        observed={
            "contractSha256": closeout_contract_sha256(observed),
            "closeoutStatus": observed.closeout_status,
            "integrationStatus": observed.integration_status,
            "doorDisposition": (
                observed.closeout_door.disposition if observed.closeout_door else ""
            ),
        },
        next_action="developer-decision",
    ) from error


def _terminate_worker(
    store: LifecycleOperationStore,
    record: LifecycleOperationRecord,
    *,
    dry_run: bool,
    cancellation_pending: bool,
) -> LifecycleOperationRecord:
    if record.workerPid is None:
        if record.workerTermination is not None and record.workerTermination.state != "exited":
            raise LifecycleControlError(
                "worker-termination-ambiguous",
                "worker termination authority is unproven",
                observed=public_worker_termination_evidence(record.workerTermination),
                next_action="cancel",
            )
        return record
    request = record.workerTermination or worker_termination_request(record)
    if dry_run:
        return record
    needs_intent = record.workerTermination is None or (
        cancellation_pending and not record.cancelRequested
    )
    if needs_intent:
        record = store.update(
            lambda current: _request_worker_termination(
                current,
                request=request,
                cancellation_pending=cancellation_pending,
            )
        )
    outcome = bounded_worker_termination_outcome(signal_worker_and_prove_exit(request))
    if outcome.state != "exited":
        store.update(lambda current: current.model_copy(update={"workerTermination": outcome}))
        raise LifecycleControlError(
            "worker-termination-required",
            outcome.detail,
            expected={"state": "exited", "workerAuthority": "retained-until-proof"},
            observed=public_worker_termination_evidence(outcome),
            next_action="cancel",
        )
    return store.update(lambda current: release_worker_after_exit(current, outcome))


def _request_worker_termination(
    current: LifecycleOperationRecord,
    *,
    request: WorkerTerminationEvidence,
    cancellation_pending: bool,
) -> LifecycleOperationRecord:
    if current.status == "termination-required":
        return current
    return current.model_copy(
        update={
            "status": "termination-required",
            "phase": "termination-required",
            "cancelRequested": cancellation_pending or current.cancelRequested,
            "workerTermination": request,
            "terminationReturnStatus": current.status,
            "terminationReturnPhase": current.phase,
            "currentCommand": "terminate exact lifecycle worker process",
        }
    )


def _cancelled_guidance(kind: LifecycleOperationKind) -> str:
    if kind == "closeout":
        return (
            "A distinct waiting door successor is schedulable; use worktree_closeout_preview "
            "then worktree_closeout_apply for the next journal generation."
        )
    if kind == "direct-landing":
        return "Use direct_landing with freshly validated input to create one successor."
    return "Advance the task state, then use worktree_integrate for one fresh successor."


def _stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()
