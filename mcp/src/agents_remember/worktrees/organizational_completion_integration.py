"""Queue-to-repository publication for final organizational leaf integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from agents_remember.controlplane.closeout_queue_store import CloseoutQueueStore
from agents_remember.controlplane.integration_authority_lock import integration_authority_lock
from agents_remember.models.closeout_queue import CloseoutCandidateRecord, CloseoutQueueState
from agents_remember.models.lifecycles.operation import IntegrationQualityCertification
from agents_remember.tasks.document_refs import TaskDocumentTopology
from agents_remember.worktrees.closeout_queue import (
    CloseoutQueueError,
    _active_lane_owner,
    _candidate_blockers,
    _candidate_or_error,
    _graph_context,
    _initial_state,
    _waiting_reasons,
    now_iso,
)
from agents_remember.worktrees.closeout_queue_candidate_evidence import commit_tree
from agents_remember.worktrees.closeout_queue_lifecycle import (
    _integration_boundary_context,
    _integration_commit_blockers,
    _IntegrationBoundaryContext,
    _queue_candidate_integration_was_completed,
    _require_integration_boundary_candidate,
    contract_queue_binding,
    integration_queue_completion_evidence,
)
from agents_remember.worktrees.lifecycle_operation_store import (
    LifecycleOperationStore,
    operation_record_path,
)
from agents_remember.worktrees.organizational_completion import (
    OrganizationalCompletionContext,
    OrganizationalCompletionPlan,
    organizational_completion_plan,
    publish_organizational_master_completion,
    require_published_organizational_master_completion,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract

T = TypeVar("T")


@dataclass(frozen=True)
class IntegrationBoundaryFacts:
    """The exact queue candidate and optional logical-master completion edge."""

    candidate: CloseoutCandidateRecord | None
    organizational_completion: OrganizationalCompletionPlan | None


def organizational_completion_scope_block(
    *,
    preflight_present: bool,
    locked: OrganizationalCompletionPlan | None,
) -> dict[str, object] | None:
    """Refuse a final/non-final classification change before protected publication."""

    if preflight_present == (locked is not None):
        return None
    reason = (
        "organizational completion scope changed between the quality preflight and "
        "protected publication; retry against one stable task generation"
    )
    return {
        "state": "organizational-completion-scope-changed",
        "reason": reason,
        "summary": reason,
        "developer_decision_required": False,
        "safeToReplace": True,
        "superRefsMoved": False,
    }


def preview_organizational_completion(
    contract: WorktreeContract,
) -> OrganizationalCompletionPlan | None:
    """Read the exact final-leaf decision used by integration dry-run."""

    binding = contract_queue_binding(contract)
    if binding is None:
        return None
    topology = TaskDocumentTopology(contract.coordination_root)
    graph = _graph_context(topology, binding.sprint_ref)
    initial = _initial_state(binding.sprint_ref, graph.revision, now_iso())

    def inspect(state: CloseoutQueueState) -> OrganizationalCompletionPlan | None:
        live_graph = _graph_context(topology, binding.sprint_ref)
        candidate = state.candidates.get(binding.candidate_ref.key)
        if candidate is None:
            return None
        if candidate.state not in {"certified", "integration-in-flight"}:
            return None
        blockers = _candidate_blockers(topology, live_graph, candidate)
        if blockers:
            raise CloseoutQueueError(
                "closeout-candidate-integration-blocked",
                f"candidate is not legal for completion preview: {blockers!r}",
            )
        master = live_graph.masters[candidate.owningMaster]
        return organizational_completion_plan(
            OrganizationalCompletionContext(
                topology=topology,
                sprint=live_graph.sprint,
                master=master,
                candidate=candidate,
                candidates=state.candidates,
            ),
            contract=contract,
        )

    return CloseoutQueueStore(contract.coordination_root, binding.sprint_ref).inspect(
        initial,
        inspect,
    )


def publish_queue_candidate_integration_result_under_authority(
    contract: WorktreeContract,
    publication: Callable[[IntegrationBoundaryFacts], T],
    *,
    operation_key: str,
    commits: tuple[str, str, str],
    recovery: bool = False,
) -> T:
    """Publish one leaf landing with its final organizational scope pinned."""

    binding = contract_queue_binding(contract)
    if binding is None:
        with integration_authority_lock(contract.coordination_root, contract.repo_name):
            return publication(
                IntegrationBoundaryFacts(candidate=None, organizational_completion=None)
            )
    context, initial = _integration_boundary_context(
        contract,
        binding,
        operation_key=operation_key,
        commits=commits,
    )

    def validate_and_publish(state: CloseoutQueueState) -> T:
        graph = _graph_context(context.topology, context.binding.sprint_ref)
        if recovery and context.binding.candidate_ref.key not in state.candidates:
            _require_completed_integration_recovery(context, state, graph=graph)
            with integration_authority_lock(contract.coordination_root, contract.repo_name):
                return publication(
                    IntegrationBoundaryFacts(candidate=None, organizational_completion=None)
                )
        candidate = (
            _require_integration_recovery_candidate(context, state, graph=graph)
            if recovery
            else _require_integration_boundary_candidate(context, state, graph=graph)
        )
        master = graph.masters[candidate.owningMaster]
        completion = organizational_completion_plan(
            OrganizationalCompletionContext(
                topology=context.topology,
                sprint=graph.sprint,
                master=master,
                candidate=candidate,
                candidates=state.candidates,
            ),
            contract=contract,
        )
        facts = IntegrationBoundaryFacts(candidate, completion)
        with integration_authority_lock(contract.coordination_root, contract.repo_name):
            result = publication(facts)
            if completion is not None and getattr(result, "returncode", None) == 0:
                fingerprint = _operation_completion_fingerprint(
                    contract,
                    operation_key=operation_key,
                    expected=completion.fingerprint,
                )
                publish_organizational_master_completion(
                    completion,
                    certified_fingerprint=fingerprint,
                )
            return result

    return CloseoutQueueStore(contract.coordination_root, binding.sprint_ref).inspect(
        initial, validate_and_publish
    )


def _require_completed_integration_recovery(
    context: _IntegrationBoundaryContext,
    state: CloseoutQueueState,
    *,
    graph: Any,
) -> None:
    contract = context.contract
    if (
        contract.integration_status != "completed"
        or contract.integrated_code_commit != context.commits[0]
        or contract.integrated_memory_content_commit != context.commits[1]
        or contract.integrated_ledger_commit != context.commits[2]
    ):
        raise CloseoutQueueError(
            "closeout-candidate-completed-contract-mismatch",
            "queue completion recovery requires the exact finalized integration contract",
        )
    record = LifecycleOperationStore(
        operation_record_path(contract.worktree_group, "integrate")
    ).read()
    expected_queue_completion = integration_queue_completion_evidence(
        contract,
        operation_key=context.operation_key,
        commits=context.commits,
    )
    assert expected_queue_completion is not None
    if (
        record is None
        or record.operationKey != context.operation_key
        or record.status != "running"
        or record.queueCompletion != expected_queue_completion
    ):
        raise CloseoutQueueError(
            "closeout-candidate-completed-operation-mismatch",
            "queue completion recovery requires its exact durable removal intent",
        )
    _queue_candidate_integration_was_completed(
        state,
        context.binding,
        owner=context.owner,
        commits=context.commits,
    )
    master_ref = context.topology.parent(context.binding.candidate_ref)
    master = graph.masters.get(master_ref) if master_ref is not None else None
    certification = record.qualityCertification
    if certification is None:
        if master is not None and master.document.status == "Completed":
            raise CloseoutQueueError(
                "organizational-completion-quality-unproven",
                "a completed organizational master has no durable full-gate certification",
            )
        return
    if (
        master is None
        or master.document.executionNature != "organizational"
        or certification.codeCommit != context.commits[0]
        or certification.candidateTree != commit_tree(contract.code_worktree, context.commits[0])
    ):
        raise CloseoutQueueError(
            "organizational-completion-quality-unproven",
            "queue completion recovery certification does not name this final candidate",
        )
    require_published_organizational_master_completion(
        master.document,
        fingerprint=certification.completionFingerprint,
    )


def _operation_completion_fingerprint(
    contract: WorktreeContract,
    *,
    operation_key: str,
    expected: str,
) -> str:
    record = LifecycleOperationStore(
        operation_record_path(contract.worktree_group, "integrate")
    ).read()
    if (
        record is None
        or record.operationKey != operation_key
        or record.status != "running"
        or record.qualityCertification is None
        or record.qualityCertification.completionFingerprint != expected
        or record.qualityCertification.codeCommit != contract.code_commit
        or record.qualityCertification.candidateTree
        != commit_tree(contract.code_worktree, contract.code_commit)
    ):
        raise CloseoutQueueError(
            "organizational-completion-quality-unproven",
            "logical master completion requires this operation's exact durable full-gate proof",
        )
    return record.qualityCertification.completionFingerprint


def recorded_organizational_quality_certification(
    contract: WorktreeContract,
    *,
    operation_key: str,
) -> IntegrationQualityCertification:
    """Load the exact proof just persisted by the running integration owner."""

    record = LifecycleOperationStore(
        operation_record_path(contract.worktree_group, "integrate")
    ).read()
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


def _require_integration_recovery_candidate(
    context: _IntegrationBoundaryContext,
    state: CloseoutQueueState,
    *,
    graph: Any,
) -> CloseoutCandidateRecord:
    """Retain the immutable queue claim while Git recovery proves torn ref state."""

    candidate = _candidate_or_error(state, context.binding.candidate_ref)
    if (
        candidate.state != "integration-in-flight"
        or candidate.inFlightOwnerFingerprint != context.owner
    ):
        raise CloseoutQueueError(
            "closeout-candidate-integration-claim-required",
            "integration recovery requires the exact candidate claimed by this operation",
        )
    blockers = _integration_commit_blockers(candidate, context.contract, *context.commits)
    blockers.extend(
        _waiting_reasons(
            graph,
            candidate,
            _active_lane_owner(state),
            state.activeBarrier,
        )
    )
    if blockers:
        raise CloseoutQueueError(
            "closeout-candidate-recovery-blocked",
            f"candidate is not legal for exact integration recovery: {blockers!r}",
        )
    return candidate
