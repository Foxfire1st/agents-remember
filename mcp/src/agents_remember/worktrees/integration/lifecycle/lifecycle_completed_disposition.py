"""Completed closeout disposition authority checks."""

from __future__ import annotations

from typing import Literal, cast

from agents_remember.models.lifecycles.operation import (
    IntegrationPublicationIntent,
    LifecycleOperationRecord,
)
from agents_remember.worktrees.integration.closeout.recovery_projection import (
    closeout_generation_retained,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_control_errors import (
    LifecycleControlError,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_identity import (
    closeout_contract_sha256,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    located_lifecycle_operation_store,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract


def require_completed_disposition(
    contract: WorktreeContract,
    record: LifecycleOperationRecord,
    action: Literal["retire", "supersede"],
) -> None:
    """Refuse disposition unless exact unintegrated closeout authority is idle."""
    integration = located_lifecycle_operation_store(contract, "integrate").read()
    _require_idle_integration_claim(contract, integration, action)
    exact_owner = _completed_closeout_owner(contract, record) or _completed_direct_owner(
        contract,
        record,
    )
    _require_completed_owner(contract, record, exact_owner)
    if record.workerPid is not None:
        raise LifecycleControlError(
            "lifecycle-worker-still-authoritative",
            "completed operation worker exit has not been proven",
            next_action="cancel",
        )


def _require_idle_integration_claim(
    contract: WorktreeContract,
    integration: LifecycleOperationRecord | None,
    action: Literal["retire", "supersede"],
) -> None:
    publication = getattr(integration, "integrationPublication", None)
    active = all(
        (
            integration is not None,
            publication is not None,
            getattr(integration, "status", None) not in {"cancelled", "failed"},
        )
    )
    if active:
        integration = cast(LifecycleOperationRecord, integration)
        publication = cast(IntegrationPublicationIntent, publication)
        raise LifecycleControlError(
            "lifecycle-integration-claim-active",
            "the accepted integration journal claim must finish before closeout disposition",
            expected={
                "closeoutDoorDisposition": "claimed",
                "integrationGeneration": integration.generation,
                "claimState": publication.claimState,
            },
            observed={
                "requestedDisposition": action,
                "integrationStatus": integration.status,
            },
            next_action="recover",
            next_tool="worktree_operation_control",
            next_args={
                "contract_path": contract.contract_path.as_posix(),
                "operation_kind": "integrate",
                "action": "recover",
                "expected_generation": integration.generation,
                "intent_note": "<developer intent>",
                "dry_run": False,
            },
        )


def _completed_closeout_owner(
    contract: WorktreeContract,
    record: LifecycleOperationRecord,
) -> bool:
    observed = (
        record.operationKind,
        record.generationDisposition,
        closeout_generation_retained(record),
        record.closeoutFinalizedContractSha256,
    )
    expected = (
        "closeout",
        "active",
        True,
        closeout_contract_sha256(contract),
    )
    return observed == expected


def _completed_direct_owner(
    contract: WorktreeContract,
    record: LifecycleOperationRecord,
) -> bool:
    publication = record.doorPublication
    generation = getattr(publication, "generation", None)
    observed = (
        record.operationKind,
        record.generationDisposition,
        getattr(publication, "state", None),
        getattr(generation, "disposition", None),
        getattr(generation, "operationKind", None),
        getattr(generation, "operationFingerprint", None),
        getattr(generation, "claimedOperationKey", None),
        contract.closeout_door,
    )
    expected = (
        "direct-landing",
        "active",
        "proven",
        "claimed",
        "direct-landing",
        record.fingerprint,
        record.operationKey,
        generation,
    )
    return observed == expected


def _require_completed_owner(
    contract: WorktreeContract,
    record: LifecycleOperationRecord,
    exact_owner: bool,
) -> None:
    observed = (record.status, contract.integration_status == "completed", exact_owner)
    if observed != ("completed", False, True):
        raise LifecycleControlError(
            "lifecycle-disposition-not-allowed",
            "retire/supersede requires an exact completed but unintegrated door owner",
            observed={
                "operationStatus": record.status,
                "integrationStatus": contract.integration_status,
                "exactGenerationOwner": exact_owner,
            },
            next_action="recover",
        )
