"""Admission request and diagnostic projection for atomic-series operations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.worktrees.activation.atomic_series_activation import (
    AtomicSeriesActivationError,
    AtomicSeriesActivationObservation,
    bounded_activation_detail,
    series_master_ref,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract


@dataclass(frozen=True)
class AtomicSeriesAdmissionRequest:
    """Inputs for the single public atomic-series admission projection."""

    operation: str
    status: str
    detail: str
    contract: WorktreeContract | None = None
    requested_master: TaskDocumentRef | None = None
    requested_contract_path: Path | None = None
    observation: AtomicSeriesActivationObservation | None = None
    expected: dict[str, object] | None = None
    observed: dict[str, object] | None = None


def atomic_series_admission_projection(
    request: AtomicSeriesAdmissionRequest,
) -> dict[str, object]:
    """Build the one public, contract-grounded admission explanation.

    This is deliberately a projection of an observation.  It never treats a
    missing snapshot as proof that a continuation is safe and never infers a
    live process from an ``active`` or ``reconciling`` selection.  Selection is
    per contract, so a foreign master is never a precondition here: every
    refusal this describes is corrective action on the addressed contract.
    """

    public_detail = bounded_activation_detail(request.detail)
    assert public_detail is not None
    requested_master, requested_contract_path = _admission_requested_identity(request)
    observation = request.observation
    admission: dict[str, object] = {
        "operation": request.operation,
        "requested": {
            "master": requested_master.model_dump(mode="json") if requested_master else None,
            "contractPath": requested_contract_path.as_posix()
            if requested_contract_path is not None
            else None,
        },
        "contractFingerprint": (
            observation.contract_fingerprint if observation is not None else None
        ),
        "activation": _admission_activation(observation),
        "retryPrecondition": _admission_retry_precondition(observation),
        "statusAction": _admission_status_action(
            request.contract,
            requested_master,
            requested_contract_path,
        ),
        "status": request.status,
        "detail": public_detail,
    }
    if request.expected is not None:
        admission["expected"] = request.expected
    if request.observed is not None:
        admission["observed"] = request.observed
    return admission


def _admission_requested_identity(
    request: AtomicSeriesAdmissionRequest,
) -> tuple[TaskDocumentRef | None, Path | None]:
    requested_master = request.requested_master
    requested_contract_path = request.requested_contract_path
    if requested_master is None and request.contract is not None:
        try:
            requested_master = series_master_ref(request.contract)
        except AtomicSeriesActivationError:
            requested_master = None
    if requested_contract_path is None and request.contract is not None:
        requested_contract_path = request.contract.contract_path
    return requested_master, requested_contract_path


def _admission_activation(
    observation: AtomicSeriesActivationObservation | None,
) -> dict[str, object] | None:
    if observation is None:
        return None
    activation: dict[str, object] = {
        "path": observation.activation_path.as_posix(),
        "observedState": observation.state,
        "recordPresent": observation.record is not None,
        "contractFingerprint": observation.contract_fingerprint,
    }
    if observation.record is not None:
        activation.update(
            {
                "revision": observation.record.revision,
                "selectedAt": observation.record.selectedAt,
                "selectedMaster": observation.record.selectedMaster.model_dump(mode="json"),
                "selectedContractPath": observation.record.contractPath,
            }
        )
    if observation.error_type is not None:
        activation["errorType"] = observation.error_type
    if observation.detail is not None:
        activation["detail"] = bounded_activation_detail(observation.detail)
    return activation


def _admission_status_action(
    contract: WorktreeContract | None,
    requested_master: TaskDocumentRef | None,
    requested_contract_path: Path | None,
) -> dict[str, object] | None:
    repository = (
        contract.repo_name
        if contract is not None
        else (requested_master.repository if requested_master is not None else None)
    )
    if repository is None or requested_contract_path is None:
        return None
    return {
        "tool": "worktree_status",
        "args": {
            "repo_id": repository,
            "contract_path": requested_contract_path.as_posix(),
        },
    }


def _admission_retry_precondition(
    observation: AtomicSeriesActivationObservation | None,
) -> str:
    if observation is not None and observation.state == "vacant":
        return (
            "No selected owner is established for this contract. A vacant snapshot is not "
            "sufficient to continue an old reconciling operation; restore/select the exact "
            "requested master and contract as required by that operation, then verify with "
            "worktree_status before retrying."
        )
    if observation is not None and observation.state == "unreadable":
        return (
            "Correct the unreadable activation authority and preserve its evidence; verify a "
            "readable contract snapshot with worktree_status before retrying."
        )
    return (
        "Apply the reported contract or selection correction, then verify the exact requested "
        "contract state with worktree_status before retrying."
    )
