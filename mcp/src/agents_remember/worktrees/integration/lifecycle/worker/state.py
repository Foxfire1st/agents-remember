"""Reconcile durable worker bindings with exact live process identity."""

from __future__ import annotations

from agents_remember.models.lifecycles.operation import LifecycleOperationRecord
from agents_remember.models.lifecycles.termination import WorkerTerminationEvidence
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_store import (
    LifecycleOperationStore,
)
from agents_remember.worktrees.integration.lifecycle.worker.termination import (
    observe_worker_termination,
    worker_termination_request,
)


def reconcile_worker_exit(store: LifecycleOperationStore) -> LifecycleOperationRecord | None:
    """Clear a worker binding only after the exact process instance is no longer live."""

    current = store.observe_current()
    for _attempt in range(3):
        if current is None or current.workerPid is None:
            return current
        projected = project_worker_exit(current)
        if projected == current:
            return current
        updated, matched = store.update_if_current(
            current,
            lambda _record, projected=projected: projected,
        )
        if matched:
            return updated
        current = updated
    return store.observe_current() or current


def project_worker_exit(record: LifecycleOperationRecord) -> LifecycleOperationRecord:
    """Observe exact worker exit without publishing it, for dry-run projections."""
    if record.workerPid is None:
        return record
    request = record.workerTermination or worker_termination_request(record).model_copy(
        update={"signal": "none"}
    )
    outcome = observe_worker_termination(request)
    if outcome is None or outcome.state != "exited":
        return record
    if record.workerLease is None or record.workerProcessFingerprint is None:
        raise RuntimeError("worker binding is incomplete and exit cannot be reconciled")
    exit_proof = outcome.model_copy(update={"signal": request.signal})
    return release_worker_after_exit(record, exit_proof)


def release_worker_after_exit(
    record: LifecycleOperationRecord,
    exit_proof: WorkerTerminationEvidence,
) -> LifecycleOperationRecord:
    """Release one exact worker binding and restore or retain its requested disposition."""

    if exit_proof.state != "exited":
        raise RuntimeError("worker authority can be released only with exact exit proof")
    return_status = record.terminationReturnStatus
    return_phase = record.terminationReturnPhase
    if (return_status is None) != (return_phase is None):
        raise RuntimeError("worker exit proof has an incomplete durable return state")
    if return_status is None:
        if record.status == "termination-required":
            raise RuntimeError("worker exit proof has no durable return status and phase")
        return_status = record.status
        return_phase = record.phase
    cancellation_pending = record.cancelRequested
    return record.model_copy(
        update={
            "status": "termination-required" if cancellation_pending else return_status,
            "phase": "termination-required" if cancellation_pending else return_phase,
            "workerPid": None,
            "workerLease": None,
            "workerProcessFingerprint": None,
            "workerTermination": exit_proof,
            "terminationReturnStatus": return_status if cancellation_pending else None,
            "terminationReturnPhase": return_phase if cancellation_pending else None,
            "cancelRequested": cancellation_pending,
        }
    )
