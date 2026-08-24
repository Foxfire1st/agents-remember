"""Pure exact closeout source classifier for integration authority."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from agents_remember.models.lifecycles.operation import IntegrationPublicationIntent
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
    """Classify current door+journal authority without consulting queue state."""

    if publication is not None and not publication.doorGenerationId:
        return IntegrationDoorAuthorityEvidence("not-applicable", {}, {})
    door = contract.closeout_door
    source = None
    if (
        door is not None
        and door.disposition == "claimed"
        and door.operationKind
        in {
            "closeout",
            "direct-landing",
        }
    ):
        source = located_lifecycle_operation_store(contract, door.operationKind).read()
    observed = _observed_source(door=door, source=source)
    if publication is None:
        expected: dict[str, object] = {
            "disposition": "claimed",
            "sourceOperationStatus": "completed",
            "sourceGenerationDisposition": "active",
        }
        valid = bool(
            door is not None
            and source is not None
            and source_operation_matches(contract, door, source)
        )
        return IntegrationDoorAuthorityEvidence(
            "claimed" if valid else "preclaim-refused",
            expected,
            observed,
        )
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


def _observed_source(*, door, source) -> dict[str, object]:
    return {
        "sprintTaskDocument": door.sprintTaskDocumentRef.key if door is not None else "",
        "candidateTaskDocument": door.taskDocumentRef.key if door is not None else "",
        "disposition": door.disposition if door is not None else "missing",
        "doorGenerationId": door.generationId if door is not None else "",
        "sourceOperationKind": source.operationKind if source is not None else None,
        "sourceOperationGeneration": source.generation if source is not None else None,
        "sourceOperationFingerprint": source.fingerprint if source is not None else "",
        "sourceOperationKey": source.operationKey if source is not None else "",
        "sourceJournalSha256": source_journal_sha256(source) if source is not None else "",
        "sourceOperationStatus": source.status if source is not None else "missing",
        "sourceGenerationDisposition": (
            source.generationDisposition if source is not None else "missing"
        ),
    }


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
