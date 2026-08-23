"""Single contract publication owner for closeout-door generations."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from agents_remember.models.lifecycles.door import (
    CloseoutDoorDisposition,
    CloseoutDoorGeneration,
    DoorPublicationEvidence,
)
from agents_remember.models.lifecycles.operation import LifecycleOperationRecord
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_candidate import (
    fingerprint_payload,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_identity import (
    closeout_contract_sha256,
)
from agents_remember.worktrees.worktree_contract import (
    ContractError,
    WorktreeContract,
    contract_publication_text,
    load_contract,
    write_contract,
)


@dataclass(frozen=True)
class DoorContractReadFailure:
    """Bounded canonical-reader failure used by read-only status projection."""

    errorType: str
    detail: str


@dataclass(frozen=True)
class DoorPublicationClassification:
    """One exact accepted/published/conflicting observation of a door intent."""

    state: Literal["accepted-before", "published", "developer-decision"]
    expected: dict[str, object]
    observed: dict[str, object]

    def decision_payload(self) -> dict[str, object]:
        detail = "the contract is unreadable or outside the journaled door publication"
        return {
            "state": "closeout-door-publication-conflict",
            "reason": detail,
            "summary": detail,
            "developerDecisionRequired": True,
            "decisionSurface": detail,
            "nextAction": "developer-decision",
            "expected": self.expected,
            "observed": self.observed,
        }


class DoorPublicationError(RuntimeError):
    """Typed interruption/conflict from the canonical door publisher."""

    def __init__(
        self,
        status: Literal[
            "closeout-door-publication-interrupted",
            "closeout-door-publication-conflict",
        ],
        detail: str,
        classification: DoorPublicationClassification,
    ) -> None:
        self.status = status
        self.detail = detail
        self.classification = classification
        super().__init__(detail)


def door_generation_for_operation(
    contract: WorktreeContract,
    record: LifecycleOperationRecord,
    disposition: CloseoutDoorDisposition,
    *,
    predecessor_generation_id: str = "",
    successor_generation_id: str = "",
) -> CloseoutDoorGeneration:
    """Derive one stable door identity from an accepted operation generation."""

    generation_id = fingerprint_payload(
        {
            "taskId": record.taskId,
            "contractPath": record.contractPath,
            "operationKind": record.operationKind,
            "operationFingerprint": record.fingerprint,
            "generation": record.generation,
            "predecessorGenerationId": predecessor_generation_id,
            "codeBaseCommit": contract.code_base_commit,
            "memoryBaseCommit": contract.memory_base_commit,
        }
    )
    claimed = disposition == "claimed"
    return CloseoutDoorGeneration(
        generationId=generation_id,
        predecessorGenerationId=predecessor_generation_id,
        successorGenerationId=successor_generation_id,
        disposition=disposition,
        taskId=record.taskId,
        taskName=record.taskName,
        contractPath=record.contractPath,
        codeBaseCommit=contract.code_base_commit,
        memoryBaseCommit=contract.memory_base_commit,
        taskStateFingerprint=record.candidateState,
        operationKind=record.operationKind if claimed else None,
        operationFingerprint=record.fingerprint if claimed else "",
        claimedOperationKey=record.operationKey if claimed else "",
    )


def successor_waiting_door(
    contract: WorktreeContract,
    predecessor: CloseoutDoorGeneration,
) -> CloseoutDoorGeneration:
    """Create a distinct, unclaimed future generation after supersession."""

    generation_id = fingerprint_payload(
        {
            "predecessorGenerationId": predecessor.generationId,
            "taskId": contract.task_id,
            "contractPath": contract.contract_path.resolve().as_posix(),
            "codeBaseCommit": contract.code_base_commit,
            "memoryBaseCommit": contract.memory_base_commit,
            "taskStateFingerprint": predecessor.taskStateFingerprint,
            "disposition": "waiting",
        }
    )
    return CloseoutDoorGeneration(
        generationId=generation_id,
        predecessorGenerationId=predecessor.generationId,
        disposition="waiting",
        taskId=contract.task_id,
        taskName=contract.task_name,
        contractPath=contract.contract_path.as_posix(),
        codeBaseCommit=contract.code_base_commit,
        memoryBaseCommit=contract.memory_base_commit,
        taskStateFingerprint=predecessor.taskStateFingerprint,
    )


def prepare_door_publication(
    contract: WorktreeContract,
    generation: CloseoutDoorGeneration,
) -> DoorPublicationEvidence:
    """Return exact before/after contract-byte evidence before publication."""

    _require_door_transition(contract.closeout_door, generation)
    updated = replace(contract, closeout_door=generation)
    published_text = contract_publication_text(contract.contract_path, updated)
    return DoorPublicationEvidence(
        state="intent",
        generation=generation,
        expectedBeforeContractSha256=closeout_contract_sha256(contract),
        expectedPublishedContractSha256=hashlib.sha256(published_text.encode("utf-8")).hexdigest(),
    )


def classify_door_publication(
    intent: DoorPublicationEvidence,
    live: WorktreeContract | DoorContractReadFailure,
) -> DoorPublicationClassification:
    """Classify one canonical contract read without performing another read."""

    expected: dict[str, object] = {
        "beforeContractSha256": intent.expectedBeforeContractSha256,
        "publishedContractSha256": intent.expectedPublishedContractSha256,
        "generationId": intent.generation.generationId,
        "disposition": intent.generation.disposition,
    }
    if isinstance(live, DoorContractReadFailure):
        return DoorPublicationClassification(
            "developer-decision",
            expected,
            {
                "readStatus": "unreadable",
                "side": "contract",
                "name": Path(intent.generation.contractPath).name,
                "errorType": live.errorType,
            },
        )
    current_sha = closeout_contract_sha256(live)
    current_door = live.closeout_door
    observed: dict[str, object] = {
        "readStatus": "readable",
        "contractSha256": current_sha,
        "generationId": current_door.generationId if current_door else "",
        "disposition": current_door.disposition if current_door else "",
    }
    if current_sha == intent.expectedPublishedContractSha256:
        state: Literal["published", "developer-decision"] = (
            "published" if current_door == intent.generation else "developer-decision"
        )
        return DoorPublicationClassification(state, expected, observed)
    if current_sha == intent.expectedBeforeContractSha256:
        try:
            _require_door_transition(current_door, intent.generation)
        except RuntimeError as exc:
            return DoorPublicationClassification(
                "developer-decision",
                expected,
                {
                    **observed,
                    "transitionFailure": {
                        "stage": "door-transition-validation",
                        "side": "contract",
                        "name": Path(intent.generation.contractPath).name,
                        "errorType": type(exc).__name__,
                    },
                },
            )
        return DoorPublicationClassification("accepted-before", expected, observed)
    return DoorPublicationClassification("developer-decision", expected, observed)


def observe_door_publication(
    contract_path: Path,
    intent: DoorPublicationEvidence,
) -> DoorPublicationClassification:
    """Read through the canonical contract reader and classify its exact outcome."""

    try:
        live: WorktreeContract | DoorContractReadFailure = load_contract(contract_path)
    except (ContractError, OSError, UnicodeError, ValueError) as exc:
        live = DoorContractReadFailure(type(exc).__name__, "")
    return classify_door_publication(intent, live)


def publish_door_intent(
    contract_path: Path,
    intent: DoorPublicationEvidence,
) -> DoorPublicationEvidence:
    """Publish or prove exactly the intended contract generation idempotently."""

    classification = observe_door_publication(contract_path, intent)
    if classification.state == "published":
        return intent.model_copy(
            update={
                "state": "proven",
                "observedPublishedContractSha256": intent.expectedPublishedContractSha256,
            }
        )
    if classification.state == "developer-decision":
        raise DoorPublicationError(
            "closeout-door-publication-conflict",
            "the contract is unreadable or outside the journaled door publication",
            classification,
        )
    try:
        current = load_contract(contract_path)
        revalidated = classify_door_publication(intent, current)
        if revalidated.state != "accepted-before":
            raise DoorPublicationError(
                "closeout-door-publication-conflict",
                "the contract changed after door publication preflight",
                revalidated,
            )
        write_contract(contract_path, replace(current, closeout_door=intent.generation))
    except DoorPublicationError:
        raise
    except (ContractError, OSError, RuntimeError, UnicodeError, ValueError) as exc:
        after = observe_door_publication(contract_path, intent)
        if after.state == "published":
            return intent.model_copy(
                update={
                    "state": "proven",
                    "observedPublishedContractSha256": intent.expectedPublishedContractSha256,
                }
            )
        if after.state == "accepted-before":
            raise DoorPublicationError(
                "closeout-door-publication-interrupted",
                "the journaled closeout-door publication did not change contract bytes",
                after,
            ) from exc
        raise DoorPublicationError(
            "closeout-door-publication-conflict",
            "the contract changed or became unreadable during door publication",
            after,
        ) from exc
    after = observe_door_publication(contract_path, intent)
    if after.state == "accepted-before":
        raise DoorPublicationError(
            "closeout-door-publication-interrupted",
            "the journaled closeout-door publication did not change contract bytes",
            after,
        )
    if after.state == "developer-decision":
        raise DoorPublicationError(
            "closeout-door-publication-conflict",
            "the door publication did not produce its exact intended contract",
            after,
        )
    return intent.model_copy(
        update={
            "state": "proven",
            "observedPublishedContractSha256": intent.expectedPublishedContractSha256,
        }
    )


def _require_door_transition(
    current: CloseoutDoorGeneration | None,
    updated: CloseoutDoorGeneration,
) -> None:
    if current is None:
        return
    if current.generationId == updated.generationId:
        immutable = (
            "predecessorGenerationId",
            "taskId",
            "taskName",
            "contractPath",
            "codeBaseCommit",
            "memoryBaseCommit",
            "taskStateFingerprint",
        )
        if any(getattr(current, field) != getattr(updated, field) for field in immutable):
            raise RuntimeError("closeout-door generation identity is immutable")
        allowed = {
            "waiting": {"waiting", "claimed", "withdrawn", "superseded"},
            "claimed": {"claimed", "cancelled", "retired", "superseded"},
            "cancelled": {"cancelled", "superseded"},
            "withdrawn": {"withdrawn", "superseded"},
            "retired": {"retired"},
            "superseded": {"superseded"},
        }
        if updated.disposition not in allowed[current.disposition]:
            raise RuntimeError("invalid closeout-door disposition transition")
        if current.successorGenerationId and (
            updated.successorGenerationId != current.successorGenerationId
        ):
            raise RuntimeError("closeout-door successor link is immutable once published")
        return
    if (
        current.successorGenerationId != updated.generationId
        or updated.predecessorGenerationId != current.generationId
    ):
        raise RuntimeError("new closeout-door generation requires the exact predecessor link")
