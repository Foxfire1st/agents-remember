"""Pure exact closeout-door classifier for integration authority."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from agents_remember.models.lifecycles.operation import (
    IntegrationPublicationIntent,
    LifecycleOperationRecord,
)
from agents_remember.worktrees.integration.closeout_door import door_generation_for_operation
from agents_remember.worktrees.integration.closeout_recovery_projection import (
    closeout_generation_retained,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_identity import (
    closeout_contract_sha256,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    located_lifecycle_operation_store,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_public_evidence import (
    public_lifecycle_evidence_pair,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract

IntegrationDoorAuthorityState = Literal[
    "not-applicable",
    "claimed",
    "preclaim-refused",
    "residual-conflict",
]


@dataclass(frozen=True)
class IntegrationDoorAuthorityEvidence:
    """Exact expected and observed door identity at one integration boundary."""

    state: IntegrationDoorAuthorityState
    expected: dict[str, object]
    observed: dict[str, object]

    @property
    def valid(self) -> bool:
        return self.state in {"not-applicable", "claimed"}

    @property
    def status(self) -> str:
        if self.state == "preclaim-refused":
            return "integration-closeout-door-not-claimed"
        return "integration-closeout-door-conflict"

    @property
    def detail(self) -> str:
        if self.state == "preclaim-refused":
            return "the live closeout door no longer permits a new integration claim"
        return "the closeout door contradicts this proven journaled integration claim"


class IntegrationDoorAuthorityConflict(RuntimeError):
    """Typed refusal carrying the shared pure classifier result."""

    def __init__(self, evidence: IntegrationDoorAuthorityEvidence) -> None:
        self.evidence = evidence
        super().__init__(evidence.detail)


def classify_integration_door_authority(
    contract: WorktreeContract,
    publication: IntegrationPublicationIntent | None,
) -> IntegrationDoorAuthorityEvidence:
    """Classify preclaim or journal-owned door authority without mutating any plane."""

    if publication is not None and not publication.closeoutDoorGenerationId:
        return IntegrationDoorAuthorityEvidence("not-applicable", {}, {})
    if publication is not None:
        expected = _publication_expected(contract, publication)
        observed = _observed_door(
            contract,
            closeout=None,
            integration_generation=publication.generation,
        )
        return IntegrationDoorAuthorityEvidence(
            (
                "claimed"
                if _door_cells(observed) == _door_cells(expected)
                else _conflict_state(publication)
            ),
            expected,
            observed,
        )
    closeout = _closeout_record(contract)
    if closeout is None and contract.closeout_door is None:
        return IntegrationDoorAuthorityEvidence("not-applicable", {}, {})
    expected = _preclaim_expected(contract, closeout)
    observed = _observed_door(contract, closeout=closeout, integration_generation=None)
    valid = bool(
        closeout is not None
        and closeout.status == "completed"
        and closeout_generation_retained(closeout)
        and closeout.closeoutFinalizedContractSha256 == closeout_contract_sha256(contract)
        and _door_cells(observed) == _door_cells(expected)
    )
    return IntegrationDoorAuthorityEvidence(
        "claimed" if valid else "preclaim-refused",
        expected,
        observed,
    )


def integration_door_decision_payload(
    evidence: IntegrationDoorAuthorityEvidence,
) -> dict[str, object]:
    """Project one contradiction identically at status and protected handlers."""

    public = public_lifecycle_evidence_pair(evidence.expected, evidence.observed)
    return {
        "state": evidence.status,
        "reason": evidence.detail,
        "summary": evidence.detail,
        "developerDecisionRequired": True,
        "decisionSurface": evidence.detail,
        "nextAction": "developer-decision",
        "expected": public.expected,
        "observed": public.observed,
    }


def _conflict_state(publication: IntegrationPublicationIntent) -> IntegrationDoorAuthorityState:
    return "residual-conflict" if publication.claimState == "proven" else "preclaim-refused"


def _publication_expected(
    contract: WorktreeContract,
    publication: IntegrationPublicationIntent,
) -> dict[str, object]:
    return {
        "contractPath": contract.contract_path.as_posix(),
        "operationKind": "integrate",
        "generation": publication.generation,
        "doorOperationKind": "closeout",
        "disposition": "claimed",
        "generationId": publication.closeoutDoorGenerationId,
        "operationFingerprint": publication.closeoutOperationFingerprint,
        "claimedOperationKey": publication.closeoutOperationKey,
    }


def _preclaim_expected(
    contract: WorktreeContract,
    closeout: LifecycleOperationRecord | None,
) -> dict[str, object]:
    if closeout is None:
        return {
            "contractPath": contract.contract_path.as_posix(),
            "operationKind": "closeout",
            "generation": 0,
            "disposition": "claimed",
            "generationId": "",
            "operationFingerprint": "",
            "claimedOperationKey": "",
            "closeoutOperationStatus": "completed",
            "generationDisposition": "active",
            "closeoutFinalizedContractSha256": "",
        }
    generation = door_generation_for_operation(contract, closeout, "claimed")
    return {
        "contractPath": contract.contract_path.as_posix(),
        "operationKind": "closeout",
        "generation": closeout.generation,
        "disposition": "claimed",
        "generationId": generation.generationId,
        "operationFingerprint": closeout.fingerprint,
        "claimedOperationKey": closeout.operationKey,
        "closeoutOperationStatus": "completed",
        "generationDisposition": "active",
        "closeoutFinalizedContractSha256": closeout.closeoutFinalizedContractSha256,
    }


def _observed_door(
    contract: WorktreeContract,
    *,
    closeout: LifecycleOperationRecord | None,
    integration_generation: int | None,
) -> dict[str, object]:
    door = contract.closeout_door
    observed: dict[str, object] = {
        "contractPath": contract.contract_path.as_posix(),
        "operationKind": "integrate" if integration_generation is not None else "closeout",
        "generation": integration_generation or (closeout.generation if closeout else 0),
        "disposition": door.disposition if door is not None else "missing",
        "generationId": door.generationId if door is not None else "",
        "operationFingerprint": door.operationFingerprint if door is not None else "",
        "claimedOperationKey": door.claimedOperationKey if door is not None else "",
    }
    if closeout is not None:
        observed.update(
            {
                "closeoutOperationStatus": closeout.status,
                "generationDisposition": closeout.generationDisposition,
                "closeoutFinalizedContractSha256": closeout_contract_sha256(contract),
            }
        )
    return observed


def _door_cells(evidence: dict[str, object]) -> dict[str, object]:
    return {
        key: evidence[key]
        for key in (
            "disposition",
            "generationId",
            "operationFingerprint",
            "claimedOperationKey",
        )
    }


def _closeout_record(contract: WorktreeContract) -> LifecycleOperationRecord | None:
    return located_lifecycle_operation_store(contract, "closeout").read()
