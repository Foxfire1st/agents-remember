"""Pure exact closeout source classifier for integration authority."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from agents_remember.models.lifecycles.door import CloseoutDoorGeneration
from agents_remember.models.lifecycles.operation import (
    IntegrationPublicationIntent,
    LifecycleOperationRecord,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    located_lifecycle_operation_store,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_public_evidence import (
    public_lifecycle_evidence_pair,
)
from agents_remember.worktrees.integration.organizational_completion_integration import (
    source_journal_sha256,
    source_operation_matches,
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
            return "the live closeout source no longer permits a new integration claim"
        return "the closeout source contradicts this proven journaled integration claim"


class IntegrationDoorAuthorityConflict(RuntimeError):
    def __init__(self, evidence: IntegrationDoorAuthorityEvidence) -> None:
        self.evidence = evidence
        super().__init__(evidence.detail)


def classify_integration_door_authority(
    contract: WorktreeContract,
    publication: IntegrationPublicationIntent | None,
) -> IntegrationDoorAuthorityEvidence:
    """Classify current door+journal authority without consulting queue state.

    A leaf integration must inherit one exact claimed closeout/direct-landing
    source.  An ordinary series integration is the aggregation boundary above
    those leaf claims, so it has no source door of its own.  Its journal records
    that absence explicitly as ``not-applicable``; the direct-execution policy
    gate does not participate in this classification.
    """

    not_applicable = _not_applicable_authority(contract, publication)
    if not_applicable is not None:
        return not_applicable
    door = contract.closeout_door
    source = _claimed_source_operation(contract, door)
    observed = _observed_source(door=door, source=source)
    if publication is None:
        return _fresh_claim_authority(contract, door, source, observed)
    return _journaled_claim_authority(publication, observed)


def _not_applicable_authority(
    contract: WorktreeContract,
    publication: IntegrationPublicationIntent | None,
) -> IntegrationDoorAuthorityEvidence | None:
    # Preserve an already-journaled no-door operation exactly as the recovery
    # protocol recorded it.  This is retained operation authority, not admission
    # for a new branch-direct delivery; fresh operations are classified below.
    if publication is not None and not publication.doorGenerationId:
        return IntegrationDoorAuthorityEvidence("not-applicable", {}, {})
    fresh_series = (
        publication is None and contract.kind == "series" and contract.closeout_door is None
    )
    if fresh_series:
        return _series_without_door_authority(contract)
    return None


def _claimed_source_operation(
    contract: WorktreeContract,
    door: CloseoutDoorGeneration | None,
) -> LifecycleOperationRecord | None:
    if (
        door is None
        or door.disposition != "claimed"
        or door.operationKind not in {"closeout", "direct-landing"}
    ):
        return None
    return located_lifecycle_operation_store(contract, door.operationKind).read()


def _fresh_claim_authority(
    contract: WorktreeContract,
    door: CloseoutDoorGeneration | None,
    source: LifecycleOperationRecord | None,
    observed: dict[str, object],
) -> IntegrationDoorAuthorityEvidence:
    expected: dict[str, object] = {
        "disposition": "claimed",
        "sourceOperationStatus": "completed",
        "sourceGenerationDisposition": "active",
    }
    valid = bool(
        door is not None and source is not None and source_operation_matches(contract, door, source)
    )
    return IntegrationDoorAuthorityEvidence(
        "claimed" if valid else "preclaim-refused",
        expected,
        observed,
    )


def _journaled_claim_authority(
    publication: IntegrationPublicationIntent,
    observed: dict[str, object],
) -> IntegrationDoorAuthorityEvidence:
    expected = {
        "sprintTaskDocument": publication.sprintTaskDocument,
        "candidateTaskDocument": publication.candidateTaskDocument,
        "disposition": "claimed",
        "doorGenerationId": publication.doorGenerationId,
        "sourceOperationKind": publication.sourceOperationKind,
        "sourceOperationGeneration": publication.sourceOperationGeneration,
        "sourceOperationFingerprint": publication.sourceOperationFingerprint,
        "sourceOperationKey": publication.sourceOperationKey,
        "sourceJournalSha256": publication.sourceJournalSha256,
    }
    valid = _source_cells(observed) == _source_cells(expected)
    return IntegrationDoorAuthorityEvidence(
        (
            "claimed"
            if valid
            else ("residual-conflict" if publication.claimState == "proven" else "preclaim-refused")
        ),
        expected,
        observed,
    )


def _series_without_door_authority(
    contract: WorktreeContract,
) -> IntegrationDoorAuthorityEvidence:
    expected: dict[str, object] = {
        "contractKind": "series",
        "closeoutDoor": "absent",
    }
    observed: dict[str, object] = {
        "contractKind": contract.kind,
        "closeoutDoor": (
            "absent" if contract.closeout_door is None else contract.closeout_door.disposition
        ),
    }
    valid = contract.kind == "series" and contract.closeout_door is None
    return IntegrationDoorAuthorityEvidence(
        "not-applicable" if valid else "preclaim-refused",
        expected,
        observed,
    )


def integration_door_decision_payload(
    evidence: IntegrationDoorAuthorityEvidence,
) -> dict[str, object]:
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


def _observed_source(
    *,
    door: CloseoutDoorGeneration | None,
    source: LifecycleOperationRecord | None,
) -> dict[str, object]:
    door_evidence = (
        {
            "sprintTaskDocument": "",
            "candidateTaskDocument": "",
            "disposition": "missing",
            "doorGenerationId": "",
        }
        if door is None
        else {
            "sprintTaskDocument": door.sprintTaskDocumentRef.key,
            "candidateTaskDocument": door.taskDocumentRef.key,
            "disposition": door.disposition,
            "doorGenerationId": door.generationId,
        }
    )
    source_evidence = (
        {
            "sourceOperationKind": None,
            "sourceOperationGeneration": None,
            "sourceOperationFingerprint": "",
            "sourceOperationKey": "",
            "sourceJournalSha256": "",
            "sourceOperationStatus": "missing",
            "sourceGenerationDisposition": "missing",
        }
        if source is None
        else {
            "sourceOperationKind": source.operationKind,
            "sourceOperationGeneration": source.generation,
            "sourceOperationFingerprint": source.fingerprint,
            "sourceOperationKey": source.operationKey,
            "sourceJournalSha256": source_journal_sha256(source),
            "sourceOperationStatus": source.status,
            "sourceGenerationDisposition": source.generationDisposition,
        }
    )
    return {**door_evidence, **source_evidence}


def _source_cells(evidence: dict[str, object]) -> dict[str, object]:
    return {
        key: evidence.get(key)
        for key in (
            "sprintTaskDocument",
            "candidateTaskDocument",
            "disposition",
            "doorGenerationId",
            "sourceOperationKind",
            "sourceOperationGeneration",
            "sourceOperationFingerprint",
            "sourceOperationKey",
            "sourceJournalSha256",
        )
    }
