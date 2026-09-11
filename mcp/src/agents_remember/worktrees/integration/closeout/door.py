"""Single contract publication owner for closeout-door generations."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from agents_remember.kernel.atomic_write import atomic_write_text
from agents_remember.models.lifecycles.door import (
    CloseoutDoorDisposition,
    CloseoutDoorGeneration,
    DoorDependencyInputs,
    DoorPublicationEvidence,
    closeout_door_dependencies,
    require_closeout_door_dependencies,
)
from agents_remember.models.lifecycles.operation import LifecycleOperationRecord
from agents_remember.models.task_intent import TaskIntentIdentity
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_candidate import (
    fingerprint_payload,
)
from agents_remember.worktrees.worktree_contract import (
    ContractError,
    WorktreeContract,
    load_contract,
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
) -> CloseoutDoorGeneration:
    """Claim the exact already-published waiting generation for one journal intent."""

    if disposition != "claimed":
        raise RuntimeError(
            "cancel, retire, and supersede are journal outcomes, not door dispositions"
        )
    waiting = live_closeout_door(contract, record)
    if waiting is None or waiting.disposition != "waiting":
        raise RuntimeError("closeout operation requires one exact waiting door generation")
    if predecessor_generation_id and waiting.predecessorGenerationId != predecessor_generation_id:
        raise RuntimeError(
            "claimed door predecessor assertion does not match its source generation"
        )
    if waiting.contractPath != record.contractPath or waiting.taskId != record.taskId:
        raise RuntimeError("waiting door does not identify the accepted closeout operation task")
    if (
        not isinstance(waiting.taskIntent, TaskIntentIdentity)
        or record.taskIntent != waiting.taskIntent
    ):
        raise RuntimeError("waiting door and operation must bind the same canonical task intent")
    require_closeout_door_dependencies(waiting)
    return waiting.model_copy(
        update={
            "disposition": "claimed",
            "operationKind": record.operationKind,
            "operationFingerprint": record.fingerprint,
            "claimedOperationKey": record.operationKey,
        }
    )


def successor_waiting_door(
    claimed: CloseoutDoorGeneration,
    *,
    declared_by: str,
    declared_at: str,
) -> CloseoutDoorGeneration:
    """Derive one deterministic schedulable source successor from a claimed generation."""

    if claimed.disposition != "claimed":
        raise RuntimeError("a waiting successor requires one exact claimed predecessor")
    dependencies = closeout_door_dependencies(
        DoorDependencyInputs(
            candidate_tree=claimed.candidateTree,
            memory_candidate_tree=claimed.memoryCandidateTree,
            task_topology_fingerprint=claimed.taskTopologyFingerprint,
            task_intent=claimed.taskIntent,
            review=claimed.reviewProvenance,
            memory=claimed.memoryProvenance,
            ledger=claimed.ledgerProvenance,
            admission=claimed.admissionProvenance,
            scheduling=claimed.schedulingProvenance,
            predecessor=claimed.generationId,
        )
    )
    identity = {
        "schema": "ar-closeout-door-successor/v1",
        "predecessorGenerationId": claimed.generationId,
        "taskId": claimed.taskId,
        "taskDocumentRef": claimed.taskDocumentRef.model_dump(mode="json"),
        "owningMasterTaskDocumentRef": claimed.owningMasterTaskDocumentRef.model_dump(mode="json"),
        "sprintTaskDocumentRef": claimed.sprintTaskDocumentRef.model_dump(mode="json"),
        "contractPath": claimed.contractPath,
        "candidateTree": claimed.candidateTree,
        "memoryCandidateTree": claimed.memoryCandidateTree,
        "codeBaseCommit": claimed.codeBaseCommit,
        "memoryBaseCommit": claimed.memoryBaseCommit,
        "ledgerMemoryCommit": claimed.ledgerMemoryCommit,
        "taskTopologyFingerprint": claimed.taskTopologyFingerprint,
        "taskIntent": claimed.taskIntent.model_dump(mode="json", by_alias=True),
        "reviewProvenance": claimed.reviewProvenance.model_dump(mode="json"),
        "memoryProvenance": claimed.memoryProvenance.model_dump(mode="json"),
        "ledgerProvenance": claimed.ledgerProvenance.model_dump(mode="json"),
        "admissionProvenance": claimed.admissionProvenance.model_dump(mode="json"),
        "schedulingProvenance": claimed.schedulingProvenance.model_dump(mode="json"),
        "dependencies": dependencies.model_dump(mode="json"),
    }
    return claimed.model_copy(
        update={
            "generationId": fingerprint_payload(identity),
            "predecessorGenerationId": claimed.generationId,
            "disposition": "waiting",
            "declaredBy": declared_by,
            "declaredAt": declared_at,
            "dependencies": dependencies,
            "operationKind": None,
            "operationFingerprint": "",
            "claimedOperationKey": "",
        }
    )


def door_journal_path(contract: WorktreeContract) -> Path:
    """Where a declared closeout door generation is published for one contract."""

    return contract.worktree_group / "reports" / "closeout-door.json"


def read_published_door(contract: WorktreeContract) -> CloseoutDoorGeneration | None:
    """Read the declared door generation, or ``None`` when none is published."""

    try:
        payload = json.loads(door_journal_path(contract).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    try:
        return CloseoutDoorGeneration.model_validate(payload)
    except ValueError:
        return None


def write_published_door(contract: WorktreeContract, generation: CloseoutDoorGeneration) -> None:
    """Publish one door generation to its journal, atomically."""

    atomic_write_text(
        door_journal_path(contract),
        json.dumps(generation.model_dump(mode="json"), sort_keys=True, separators=(",", ":")),
    )


def live_closeout_door(
    contract: WorktreeContract,
    record: LifecycleOperationRecord | None = None,
) -> CloseoutDoorGeneration | None:
    """The live closeout door for one contract, read from its own journal.

    The worktree contract no longer stores a door generation. A door is declared,
    claimed and proven in the journal alone: first the operation record's own
    publication, then the contract's declared-door journal. A contract with
    neither has no live door -- that is an absence, not a conflict.
    """

    if record is not None and record.doorPublication is not None:
        return record.doorPublication.generation
    if contract.kind not in {"series", "leaf"}:
        return None
    if record is not None:
        return read_published_door(contract)
    try:
        from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (  # noqa: PLC0415
            located_lifecycle_operation_store,
        )

        store = located_lifecycle_operation_store(contract, "closeout")
        retained = store.read()
    except Exception:  # a contract outside the journal has no live door, never an error
        return read_published_door(contract)
    if retained is None or retained.doorPublication is None:
        return read_published_door(contract)
    return retained.doorPublication.generation


def classify_door_publication(
    intent: DoorPublicationEvidence,
    live: WorktreeContract,
) -> DoorPublicationClassification:
    """Classify one journal-owned door intent against the contract's live door.

    The contract is no longer the door's store, so there are no contract bytes to
    hash or compare. The journal owns the transition: a proven intent is published
    once the live door IS the intent's generation; an intent that has not been
    proven is still accepted-before.
    """

    expected: dict[str, object] = {
        "generationId": intent.generation.generationId,
        "disposition": intent.generation.disposition,
    }
    current = live_closeout_door(live)
    observed: dict[str, object] = {
        "generationId": current.generationId if current is not None else "",
        "disposition": current.disposition if current is not None else "",
    }
    if intent.state == "proven":
        state: Literal["published", "developer-decision"] = (
            "published" if current == intent.generation else "developer-decision"
        )
        return DoorPublicationClassification(state, expected, observed)
    return DoorPublicationClassification("accepted-before", expected, observed)


def prepare_door_publication(
    contract: WorktreeContract,
    generation: CloseoutDoorGeneration,
) -> DoorPublicationEvidence:
    """Return the journal-owned intent for one door generation."""

    _require_door_transition(live_closeout_door(contract), generation)
    return DoorPublicationEvidence(state="intent", generation=generation)


def publish_door_intent(
    contract_path: Path,
    intent: DoorPublicationEvidence,
) -> DoorPublicationEvidence:
    """Publish and prove exactly the intended door generation, idempotently.

    The generation is written to the contract's own door journal, never to the
    worktree contract, so there are no contract bytes to re-read or compare.
    """

    try:
        contract = load_contract(contract_path)
    except (ContractError, OSError, UnicodeError, ValueError) as exc:
        raise DoorPublicationError(
            "closeout-door-publication-conflict",
            "the contract is unreadable for its door publication",
            DoorPublicationClassification(
                "developer-decision",
                {"generationId": intent.generation.generationId},
                {"readStatus": "unreadable", "errorType": type(exc).__name__},
            ),
        ) from exc
    write_published_door(contract, intent.generation)
    if intent.state == "proven":
        return intent
    return intent.model_copy(update={"state": "proven"})


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
            "taskDocumentRef",
            "owningMasterTaskDocumentRef",
            "sprintTaskDocumentRef",
            "candidateTree",
            "memoryCandidateTree",
            "taskTopologyFingerprint",
            "taskIntent",
            "reviewProvenance",
            "memoryProvenance",
            "ledgerProvenance",
            "admissionProvenance",
            "schedulingProvenance",
            "declaredBy",
            "declaredAt",
        )
        if any(getattr(current, field) != getattr(updated, field) for field in immutable):
            raise RuntimeError("closeout-door generation identity is immutable")
        allowed = {
            "waiting": {"waiting", "deferred", "withdrawn", "claimed"},
            "deferred": {"waiting", "deferred", "withdrawn"},
            "withdrawn": {"withdrawn"},
            "claimed": {"claimed"},
        }
        if updated.disposition not in allowed[current.disposition]:
            raise RuntimeError("invalid closeout-door disposition transition")
        if current.disposition == "claimed" and (
            current.operationKind != updated.operationKind
            or current.operationFingerprint != updated.operationFingerprint
            or current.claimedOperationKey != updated.claimedOperationKey
        ):
            raise RuntimeError("claimed closeout-door operation identity is immutable")
        return
    successor_edge = (current.disposition, updated.disposition)
    if updated.predecessorGenerationId != current.generationId or successor_edge not in {
        ("claimed", "waiting"),
        ("withdrawn", "waiting"),
        ("waiting", "waiting"),
        ("deferred", "deferred"),
    }:
        raise RuntimeError("new closeout-door generation requires the exact predecessor link")
    if current.disposition == "claimed" and (
        updated.operationKind is not None
        or updated.operationFingerprint
        or updated.claimedOperationKey
    ):
        raise RuntimeError("a claimed cancellation successor must clear operation identity")
