"""Admission request and diagnostic projection for atomic-series operations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from agents_remember.models.structural.atomic_series_activation import AtomicSeriesSourcePair
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.worktrees.activation.atomic_series_activation import (
    AtomicSeriesActivationError,
    AtomicSeriesActivationObservation,
    atomic_series_source_pair,
    bounded_activation_detail,
    series_master_ref,
    source_pair_fingerprint,
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
    classification: Literal["wait", "corrective-action"] | None = None
    expected: dict[str, object] | None = None
    observed: dict[str, object] | None = None


def atomic_series_admission_projection(
    request: AtomicSeriesAdmissionRequest,
) -> dict[str, object]:
    """Build the one public, source-grounded admission explanation.

    This is deliberately a projection of an observation.  It never treats a
    missing snapshot as proof that a continuation is safe and never infers a
    live process from an ``active`` or ``reconciling`` selection.
    """

    public_detail = bounded_activation_detail(request.detail)
    assert public_detail is not None
    requested_master, requested_contract_path = _admission_requested_identity(request)
    source_pair = _admission_source_pair(request.contract, request.observation)
    blocking = _admission_blocking(request.observation, requested_master)
    classification = request.classification or (
        "wait" if blocking is not None else "corrective-action"
    )
    activation = _admission_activation(request.observation)
    status_action = _admission_status_action(
        request.contract,
        requested_master,
        requested_contract_path,
    )
    retry_precondition = _admission_retry_precondition(request.observation, blocking)

    admission: dict[str, object] = {
        "classification": classification,
        "operation": request.operation,
        "requested": {
            "master": requested_master.model_dump(mode="json") if requested_master else None,
            "contractPath": requested_contract_path.as_posix()
            if requested_contract_path is not None
            else None,
        },
        "sourcePair": source_pair.model_dump(mode="json") if source_pair is not None else None,
        "sourcePairFingerprint": (
            request.observation.source_pair_fingerprint
            if request.observation is not None
            else source_pair_fingerprint(source_pair)
            if source_pair is not None
            else None
        ),
        "activation": activation,
        "blocking": blocking,
        "retryPrecondition": retry_precondition,
        "statusAction": status_action,
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


def _admission_source_pair(
    contract: WorktreeContract | None,
    observation: AtomicSeriesActivationObservation | None,
) -> AtomicSeriesSourcePair | None:
    if observation is not None:
        return observation.source_pair
    if contract is None:
        return None
    try:
        return atomic_series_source_pair(contract)
    except AtomicSeriesActivationError:
        return None


def _admission_blocking(
    observation: AtomicSeriesActivationObservation | None,
    requested_master: TaskDocumentRef | None,
) -> dict[str, object] | None:
    if observation is None or requested_master is None:
        return None
    selected_master = observation.selected_master
    selected_record = observation.record
    if (
        selected_master is None
        or selected_master == requested_master
        or selected_record is None
        or observation.state not in {"active", "reconciling"}
    ):
        return None
    return {
        "master": selected_master.model_dump(mode="json"),
        "contractPath": selected_record.contractPath,
        "state": observation.state,
        "revision": selected_record.revision,
        "selectedAt": selected_record.selectedAt,
    }


def _admission_activation(
    observation: AtomicSeriesActivationObservation | None,
) -> dict[str, object] | None:
    if observation is None:
        return None
    activation: dict[str, object] = {
        "path": observation.activation_path.as_posix(),
        "observedState": observation.state,
        "recordPresent": observation.record is not None,
        "sourcePairFingerprint": observation.source_pair_fingerprint,
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
    blocking: dict[str, object] | None,
) -> str:
    if blocking is not None:
        return (
            "Wait for the named selected master to release or for this requested master and "
            "contract to become selected; verify the source-pair snapshot with worktree_status "
            "using the supplied repo_id and contract_path before retrying. The selected state "
            "does not prove that a live process exists."
        )
    if observation is not None and observation.state == "vacant":
        return (
            "No selected owner is established. A vacant snapshot is not sufficient to continue "
            "an old reconciling operation; restore/select the exact requested master and "
            "contract as required by that operation, then verify with worktree_status before "
            "retrying."
        )
    if observation is not None and observation.state == "unreadable":
        return (
            "Correct the unreadable activation authority and preserve its evidence; verify a "
            "readable source-pair snapshot with worktree_status before retrying."
        )
    return (
        "Apply the reported contract or selection correction, then verify the exact requested "
        "source-pair state with worktree_status before retrying."
    )
