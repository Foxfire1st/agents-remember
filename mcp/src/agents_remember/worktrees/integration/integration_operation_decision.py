"""One read-only live decision observation for an integration generation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import NoReturn

from agents_remember.models.lifecycles.operation import LifecycleOperationRecord
from agents_remember.worktrees.integration.integration_publication_fence import (
    IntegrationDoorAuthorityEvidence,
    classify_integration_door_authority,
    integration_door_decision_payload,
)
from agents_remember.worktrees.integration.integration_ref_state import (
    IntegrationRefState,
    classify_integration_refs,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_control_errors import (
    LifecycleControlError,
)
from agents_remember.worktrees.integration.organizational_completion import (
    OrganizationalCompletionPublicationState,
    classify_organizational_master_completion,
)
from agents_remember.worktrees.integration.organizational_completion_repair import (
    OrganizationalRepairState,
    classify_organizational_completion_repair,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract


@dataclass(frozen=True)
class IntegrationOperationObservation:
    """One captured evidence set shared by controls, status, and handlers."""

    door: IntegrationDoorAuthorityEvidence | None
    refs: IntegrationRefState | None
    organizational: OrganizationalCompletionPublicationState | None
    repair: OrganizationalRepairState
    decision: dict[str, object] | None
    projectedResult: dict[str, object] | None


def classify_integration_operation(
    contract: WorktreeContract,
    record: LifecycleOperationRecord,
) -> IntegrationOperationObservation:
    """Classify live integration evidence once in deterministic authority order."""

    repair = classify_organizational_completion_repair(contract, record)
    if repair.state != "not-applicable":
        refs = classify_integration_refs(record)
        decision = (
            refs.decision_payload()
            if refs.state == "conflict"
            else (repair.decision_payload() if repair.state == "developer-decision" else None)
        )
        return IntegrationOperationObservation(None, refs, None, repair, decision, decision)
    door = classify_integration_door_authority(contract, record.integrationPublication)
    refs = classify_integration_refs(record)
    publication = record.integrationPublication
    organizational = (
        classify_organizational_master_completion(publication.organizationalCompletion)
        if publication is not None and publication.organizationalCompletion is not None
        else None
    )
    decision: dict[str, object] | None = None
    if not door.valid:
        decision = integration_door_decision_payload(door)
    elif refs.state == "conflict":
        decision = refs.decision_payload()
    elif organizational is not None and not organizational.mechanically_convergent:
        decision = organizational.decision_payload()
    projected_result = decision
    if (
        projected_result is None
        and isinstance(record.result, dict)
        and record.result.get("state")
        in {"integration-ref-publication-interrupted", "integration-ref-conflict"}
        and record.status not in {"completed", "cancelled"}
    ):
        projected_result = refs.public_payload()
    return IntegrationOperationObservation(
        door,
        refs,
        organizational,
        repair,
        decision,
        projected_result,
    )


def require_integration_operation_convergent(
    observation: IntegrationOperationObservation,
) -> None:
    """Translate the shared decision into the canonical public refusal."""

    if observation.decision is not None:
        raise_integration_decision(observation.decision)


def raise_integration_decision(payload: Mapping[str, object]) -> NoReturn:
    """Raise one public refusal without reconstructing classifier evidence."""

    expected = payload.get("expected")
    observed = payload.get("observed")
    raise LifecycleControlError(
        str(payload["state"]),
        str(payload["decisionSurface"]),
        expected=expected if isinstance(expected, Mapping) else {},
        observed=observed if isinstance(observed, Mapping) else {},
        next_action="developer-decision",
    )
