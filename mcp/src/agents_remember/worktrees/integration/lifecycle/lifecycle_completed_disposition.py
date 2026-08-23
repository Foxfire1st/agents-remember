"""Completed closeout disposition authority checks."""

from __future__ import annotations

from typing import Literal

from agents_remember.models.lifecycles.operation import LifecycleOperationRecord
from agents_remember.worktrees.integration.closeout_recovery_projection import (
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
    if (
        integration is not None
        and integration.integrationPublication is not None
        and integration.status not in {"cancelled", "failed"}
    ):
        raise LifecycleControlError(
            "lifecycle-integration-claim-active",
            "the accepted integration journal claim must finish before closeout disposition",
            expected={
                "closeoutDoorDisposition": "claimed",
                "integrationGeneration": integration.generation,
                "claimState": integration.integrationPublication.claimState,
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
    exact_owner = (
        record.operationKind == "closeout"
        and record.generationDisposition == "active"
        and closeout_generation_retained(record)
        and record.closeoutFinalizedContractSha256 == closeout_contract_sha256(contract)
    )
    if (
        record.status != "completed"
        or contract.integration_status == "completed"
        or not exact_owner
    ):
        raise LifecycleControlError(
            "lifecycle-disposition-not-allowed",
            "retire/supersede requires closeout-complete but unintegrated work",
            observed={
                "operationStatus": record.status,
                "integrationStatus": contract.integration_status,
                "exactGenerationOwner": exact_owner,
            },
            next_action="recover",
        )
    if record.workerPid is not None:
        raise LifecycleControlError(
            "lifecycle-worker-still-authoritative",
            "completed operation worker exit has not been proven",
            next_action="cancel",
        )
