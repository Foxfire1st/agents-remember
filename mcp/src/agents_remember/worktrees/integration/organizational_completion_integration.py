"""Transfer claimed door and source-journal proof into integration authority."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime

from agents_remember.models.lifecycles.door import CloseoutDoorGeneration
from agents_remember.models.lifecycles.operation import (
    IntegrationPublicationIntent,
    IntegrationQualityCertification,
    LifecycleOperationRecord,
)
from agents_remember.tasks.document_refs import TaskDocumentTopology
from agents_remember.worktrees.integration.closeout.recovery_projection import (
    closeout_generation_retained,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_identity import (
    closeout_contract_sha256,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    located_lifecycle_operation_store,
)
from agents_remember.worktrees.integration.organizational_completion import (
    OrganizationalCompletionContext,
    OrganizationalCompletionPlan,
    organizational_completion_plan,
    prepare_organizational_master_completion,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract


@dataclass(frozen=True)
class IntegrationBoundaryFacts:
    """Exact claimed source authority and optional organizational completion plan."""

    door: CloseoutDoorGeneration | None
    source_operation: LifecycleOperationRecord | None
    organizational_completion: OrganizationalCompletionPlan | None


def preview_integration_boundary(contract: WorktreeContract) -> IntegrationBoundaryFacts:
    """Re-prove the claimed door, source journal, and current task topology."""

    door = contract.closeout_door
    if door is None:
        return IntegrationBoundaryFacts(None, None, None)
    if door.disposition != "claimed" or door.operationKind not in {"closeout", "direct-landing"}:
        raise RuntimeError("integration requires one exact claimed closeout source door")
    source = located_lifecycle_operation_store(contract, door.operationKind).read()
    if source is None:
        raise RuntimeError("claimed closeout door has no addressable source journal")
    if not source_operation_matches(contract, door, source):
        raise RuntimeError("claimed closeout door contradicts its source journal")

    completion = None
    if contract.kind == "leaf":
        topology = TaskDocumentTopology(contract.coordination_root)
        sprint = topology.resolve(door.sprintTaskDocumentRef)
        master = topology.resolve(door.owningMasterTaskDocumentRef)
        candidate = topology.resolve(door.taskDocumentRef)
        if (
            topology.parent(candidate.ref) != master.ref
            or topology.parent(master.ref) != sprint.ref
        ):
            raise RuntimeError("claimed door task topology changed before integration")
        completion = organizational_completion_plan(
            OrganizationalCompletionContext(
                topology=topology,
                sprint=sprint,
                master=master,
                candidate=door,
            ),
            contract=contract,
        )
    return IntegrationBoundaryFacts(door, source, completion)


def preview_organizational_completion(
    contract: WorktreeContract,
) -> OrganizationalCompletionPlan | None:
    return preview_integration_boundary(contract).organizational_completion


def prepare_integration_publication_intent(
    contract: WorktreeContract,
    *,
    operation_key: str,
    generation: int,
    facts: IntegrationBoundaryFacts,
    certification: IntegrationQualityCertification | None,
) -> IntegrationPublicationIntent:
    """Bind the source authority before protected refs or task truth move."""

    completion = facts.organizational_completion
    if (completion is None) != (certification is None):
        raise RuntimeError(
            "organizational completion plan and quality certification must be one identity"
        )
    prepared_at = datetime.now(UTC).replace(microsecond=0).isoformat()
    organizational = (
        prepare_organizational_master_completion(
            completion,
            certification=certification,
            completed_at=prepared_at,
        )
        if completion is not None and certification is not None
        else None
    )
    door = facts.door
    source = facts.source_operation
    if door is None or source is None:
        if door is not None or source is not None:
            raise RuntimeError("integration source authority is partial")
        return IntegrationPublicationIntent(
            operationKey=operation_key,
            generation=generation,
            preparedAt=prepared_at,
            claimState="not-applicable",
            organizationalCompletion=organizational,
        )
    if not source_operation_matches(contract, door, source):
        raise RuntimeError("integration source authority changed before journal intent")
    return IntegrationPublicationIntent(
        operationKey=operation_key,
        generation=generation,
        preparedAt=prepared_at,
        claimState="intent",
        sprintTaskDocument=door.sprintTaskDocumentRef.key,
        candidateTaskDocument=door.taskDocumentRef.key,
        doorGenerationId=door.generationId,
        sourceOperationKind=source.operationKind,
        sourceOperationGeneration=source.generation,
        sourceOperationFingerprint=source.fingerprint,
        sourceOperationKey=source.operationKey,
        sourceJournalSha256=source_journal_sha256(source),
        organizationalCompletion=organizational,
    )


def transfer_integration_claim(
    contract: WorktreeContract,
    intent: IntegrationPublicationIntent,
    *,
    commits: tuple[str, str, str],
) -> IntegrationPublicationIntent:
    """Prove the journaled source authority without consuming disposable projection state."""

    if intent.claimState == "not-applicable":
        return intent
    facts = preview_integration_boundary(contract)
    door = facts.door
    source = facts.source_operation
    if door is None or source is None:
        raise RuntimeError("journaled integration source authority disappeared")
    expected = (
        door.sprintTaskDocumentRef.key,
        door.taskDocumentRef.key,
        door.generationId,
        source.operationKind,
        source.generation,
        source.fingerprint,
        source.operationKey,
        source_journal_sha256(source),
    )
    observed = (
        intent.sprintTaskDocument,
        intent.candidateTaskDocument,
        intent.doorGenerationId,
        intent.sourceOperationKind,
        intent.sourceOperationGeneration,
        intent.sourceOperationFingerprint,
        intent.sourceOperationKey,
        intent.sourceJournalSha256,
    )
    if observed != expected:
        raise RuntimeError("journaled integration claim contradicts current door/journal authority")
    if commits != (
        contract.code_commit,
        contract.memory_content_commit,
        contract.ledger_commit,
    ):
        raise RuntimeError("integration commits changed after source authority admission")
    if intent.claimState == "proven":
        return intent
    return intent.model_copy(
        update={
            "claimState": "proven",
            "claimTransferredAt": datetime.now(UTC).replace(microsecond=0).isoformat(),
        }
    )


def recorded_organizational_quality_certification(
    contract: WorktreeContract,
    *,
    operation_key: str,
) -> IntegrationQualityCertification:
    record = located_lifecycle_operation_store(contract, "integrate").read()
    if (
        record is None
        or record.operationKey != operation_key
        or record.status != "running"
        or record.qualityCertification is None
    ):
        raise RuntimeError(
            "organizational full-gate success was not durably certified before integration"
        )
    return record.qualityCertification


def source_operation_matches(
    contract: WorktreeContract,
    door: CloseoutDoorGeneration,
    source: LifecycleOperationRecord,
) -> bool:
    publication = source.doorPublication
    return bool(
        source.operationKind == door.operationKind
        and source.fingerprint == door.operationFingerprint
        and source.operationKey == door.claimedOperationKey
        and source.status == "completed"
        and source.generationDisposition == "active"
        and closeout_generation_retained(source)
        and publication is not None
        and publication.state == "proven"
        and publication.generation == door
        and (
            source.operationKind != "closeout"
            or source.closeoutFinalizedContractSha256 == closeout_contract_sha256(contract)
        )
    )


def source_journal_sha256(source: LifecycleOperationRecord) -> str:
    payload = json.dumps(
        source.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
