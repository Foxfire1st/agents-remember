"""Transfer a certified queue projection into journal-owned integration authority."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime

from agents_remember.controlplane.closeout_queue_store import (
    CloseoutQueueStore,
    QueueTransaction,
)
from agents_remember.models.lifecycles.operation import (
    IntegrationPublicationIntent,
    IntegrationQualityCertification,
)
from agents_remember.models.queue.closeout_queue import CloseoutCandidateRecord, CloseoutQueueState
from agents_remember.tasks.document_refs import TaskDocumentTopology
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    located_lifecycle_operation_store,
)
from agents_remember.worktrees.integration.organizational_completion import (
    OrganizationalCompletionContext,
    OrganizationalCompletionPlan,
    organizational_completion_plan,
    prepare_organizational_master_completion,
)
from agents_remember.worktrees.queue.closeout_queue import (
    CloseoutQueueError,
    _candidate_blockers,
    _candidate_or_error,
    _graph_context,
    now_iso,
)
from agents_remember.worktrees.queue.closeout_queue_lifecycle import (
    _integration_commit_blockers,
    contract_queue_binding,
)
from agents_remember.worktrees.queue.closeout_queue_state import initial_queue_state
from agents_remember.worktrees.worktree_contract import WorktreeContract


@dataclass(frozen=True)
class IntegrationBoundaryFacts:
    """The exact certified projection and optional organizational completion plan."""

    candidate: CloseoutCandidateRecord | None
    organizational_completion: OrganizationalCompletionPlan | None


def preview_integration_boundary(contract: WorktreeContract) -> IntegrationBoundaryFacts:
    """Read the exact certified projection before journal claim transfer."""

    binding = contract_queue_binding(contract)
    if binding is None:
        return IntegrationBoundaryFacts(None, None)
    topology = TaskDocumentTopology(contract.coordination_root)
    graph = _graph_context(topology, binding.sprint_ref)
    initial = initial_queue_state(binding.sprint_ref, graph.revision, now_iso())

    def inspect(state: CloseoutQueueState) -> IntegrationBoundaryFacts:
        live_graph = _graph_context(topology, binding.sprint_ref)
        candidate = _candidate_or_error(state, binding.candidate_ref)
        if candidate.state != "certified":
            raise CloseoutQueueError(
                "closeout-candidate-certification-required",
                "integration publication requires the exact certified waiting projection",
            )
        blockers = _candidate_blockers(topology, live_graph, candidate)
        blockers.extend(
            _integration_commit_blockers(
                candidate,
                contract,
                contract.code_commit,
                contract.memory_content_commit,
                contract.ledger_commit,
            )
        )
        if blockers:
            raise CloseoutQueueError(
                "closeout-candidate-integration-blocked",
                f"certified candidate changed before journal transfer: {blockers!r}",
            )
        master = live_graph.masters[candidate.owningMaster]
        completion = organizational_completion_plan(
            OrganizationalCompletionContext(
                topology=topology,
                sprint=live_graph.sprint,
                master=master,
                candidate=candidate,
                candidates=state.candidates,
            ),
            contract=contract,
        )
        return IntegrationBoundaryFacts(candidate, completion)

    return CloseoutQueueStore(contract.coordination_root, binding.sprint_ref).inspect(
        initial,
        inspect,
    )


def preview_organizational_completion(
    contract: WorktreeContract,
) -> OrganizationalCompletionPlan | None:
    """Read the final-leaf completion plan without acquiring lifecycle authority."""

    return preview_integration_boundary(contract).organizational_completion


def prepare_integration_publication_intent(
    contract: WorktreeContract,
    *,
    operation_key: str,
    generation: int,
    facts: IntegrationBoundaryFacts,
    certification: IntegrationQualityCertification | None,
) -> IntegrationPublicationIntent:
    """Choose the full postclaim publication identity before protected ref movement."""

    candidate = facts.candidate
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
    binding = contract_queue_binding(contract)
    if candidate is None:
        if binding is not None:
            raise RuntimeError("queue-bound integration publication has no certified candidate")
        return IntegrationPublicationIntent(
            operationKey=operation_key,
            generation=generation,
            preparedAt=prepared_at,
            claimState="not-applicable",
            organizationalCompletion=organizational,
        )
    if binding is None or candidate.taskDocumentRef != binding.candidate_ref:
        raise RuntimeError("integration publication candidate contradicts contract binding")
    door = contract.closeout_door
    if door is None or door.disposition != "claimed":
        raise RuntimeError("integration publication candidate has no claimed closeout door")
    return IntegrationPublicationIntent(
        operationKey=operation_key,
        generation=generation,
        preparedAt=prepared_at,
        claimState="intent",
        queueSprintTaskDocument=binding.sprint_ref.key,
        queueCandidateTaskDocument=binding.candidate_ref.key,
        queueCandidateSha256=_candidate_sha256(candidate),
        closeoutDoorGenerationId=door.generationId,
        closeoutOperationFingerprint=door.operationFingerprint,
        closeoutOperationKey=door.claimedOperationKey,
        organizationalCompletion=organizational,
    )


def transfer_integration_claim(
    contract: WorktreeContract,
    intent: IntegrationPublicationIntent,
    *,
    commits: tuple[str, str, str],
) -> IntegrationPublicationIntent:
    """Consume/prove the exact certified projection, then return journal proof."""

    if intent.claimState in {"not-applicable", "proven"}:
        return intent
    binding = contract_queue_binding(contract)
    if binding is None or (
        binding.sprint_ref.key != intent.queueSprintTaskDocument
        or binding.candidate_ref.key != intent.queueCandidateTaskDocument
    ):
        raise CloseoutQueueError(
            "closeout-candidate-claim-binding-mismatch",
            "journaled integration claim contradicts the contract queue binding",
        )
    topology = TaskDocumentTopology(contract.coordination_root)
    graph = _graph_context(topology, binding.sprint_ref)
    initial = initial_queue_state(binding.sprint_ref, graph.revision, now_iso())
    store = CloseoutQueueStore(contract.coordination_root, binding.sprint_ref)
    candidate = (
        store.inspect(initial, lambda state: state.candidates.get(binding.candidate_ref.key))
        if store.exists()
        else None
    )
    if candidate is not None:
        event = _claim_transfer_event(intent)

        def consume(state: CloseoutQueueState) -> CloseoutQueueState:
            current = _candidate_or_error(state, binding.candidate_ref)
            if current.state != "certified" or _candidate_sha256(current) != (
                intent.queueCandidateSha256
            ):
                raise CloseoutQueueError(
                    "closeout-candidate-claim-conflict",
                    "certified projection changed after journal claim intent",
                )
            live_graph = _graph_context(topology, binding.sprint_ref)
            blockers = _candidate_blockers(topology, live_graph, current)
            blockers.extend(_integration_commit_blockers(current, contract, *commits))
            if blockers:
                raise CloseoutQueueError(
                    "closeout-candidate-integration-blocked",
                    f"candidate is not legal at journal claim transfer: {blockers!r}",
                )
            candidates = dict(state.candidates)
            candidates.pop(binding.candidate_ref.key)
            return state.model_copy(update={"candidates": candidates})

        store.transact(initial=initial, event=event, transform=consume)
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
    """Load the exact proof just persisted by the running integration owner."""

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


def _candidate_sha256(candidate: CloseoutCandidateRecord) -> str:
    payload = json.dumps(candidate.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _claim_transfer_event(
    intent: IntegrationPublicationIntent,
) -> QueueTransaction:
    """Identify only the disposable waiting projection consumed by this transaction."""

    payload = {
        "action": "complete-integration",
        "sprint": intent.queueSprintTaskDocument,
        "candidate": intent.queueCandidateTaskDocument,
        "closeoutDoorGenerationId": intent.closeoutDoorGenerationId,
    }
    fingerprint = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return QueueTransaction(
        action="complete-integration",
        request_id=f"integration-projection-transfer:{fingerprint}",
        fingerprint=fingerprint,
        recorded_at=now_iso(),
        actor="integration-projection",
        rationale="transfer certified waiting projection into journal-owned integration authority",
    )
