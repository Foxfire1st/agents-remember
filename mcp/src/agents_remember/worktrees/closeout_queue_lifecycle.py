"""Lifecycle-owned transitions from queue selection through integration consumption."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from pydantic import ValidationError

from agents_remember.controlplane.closeout_queue_store import (
    CloseoutQueueStore,
    QueueTransaction,
)
from agents_remember.models.closeout_queue import (
    CloseoutCandidateRecord,
    CloseoutQueueState,
    QueueEventAction,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks.document_refs import TaskDocumentRefError, TaskDocumentTopology
from agents_remember.tasks.leaf_doc import resolve_terminal_leaf_doc
from agents_remember.worktrees.worktree_contract import WorktreeContract

from .closeout_queue import (
    CloseoutQueueError,
    _active_lane_owner,
    _candidate_blockers,
    _candidate_or_error,
    _graph_context,
    _initial_state,
    _post_closeout_blockers,
    _waiting_reasons,
    now_iso,
)
from .closeout_queue_candidate_evidence import (
    commit_tree,
)
from .closeout_queue_candidate_evidence import (
    operation_owner_fingerprint as _operation_owner,
)
from .closeout_queue_evidence import curator_evidence

T = TypeVar("T")


@dataclass(frozen=True)
class QueueBinding:
    sprint_ref: TaskDocumentRef
    candidate_ref: TaskDocumentRef


@dataclass(frozen=True)
class _LiveParents:
    master_ref: TaskDocumentRef
    sprint_ref: TaskDocumentRef


@dataclass(frozen=True)
class _LifecycleTransition:
    action: QueueEventAction
    request_id: str
    operation_key: str
    transform: Any


@dataclass(frozen=True)
class _LifecycleCandidateContext:
    topology: TaskDocumentTopology
    graph: Any
    state: CloseoutQueueState
    contract: WorktreeContract
    operation_key: str


def contract_queue_binding(contract: WorktreeContract) -> QueueBinding | None:
    """Resolve queue scope from the explicit sprint graph introduced by L1/L3."""

    if contract.kind != "leaf":
        return None
    stored = _stored_queue_binding(contract)
    topology = TaskDocumentTopology(contract.coordination_root)
    parents = _live_parent_refs(topology, contract, stored)
    if parents is None:
        return None
    master_ref, sprint_ref = parents.master_ref, parents.sprint_ref
    try:
        sprint = topology.resolve(sprint_ref)
    except TaskDocumentRefError as exc:
        if _is_unbound_legacy_absence(stored, exc):
            return None
        raise CloseoutQueueError(
            "closeout-queue-bound-topology-invalid",
            f"queue-managed contract lost its canonical sprint: {exc}",
        ) from exc
    if sprint.document.executionGraph is None:
        # Explicit staged boundary: L9 owns the one-time cutover of legacy task trees.
        if stored is not None:
            raise CloseoutQueueError(
                "closeout-queue-bound-topology-invalid",
                "queue-managed contract lost its executionGraph",
            )
        return None
    _graph_context(topology, sprint_ref)
    found = resolve_terminal_leaf_doc(contract.task_root, contract.leaf_id)
    if found is None:
        raise CloseoutQueueError(
            "closeout-candidate-task-document-missing",
            "a graph-managed leaf contract has no canonical leaf task document",
        )
    candidate_ref = topology.canonical_ref(contract.repo_name, found[0])
    if topology.parent(candidate_ref) != master_ref:
        raise CloseoutQueueError(
            "closeout-candidate-master-mismatch",
            "leaf task document no longer belongs to the contract's graph-managed master",
        )
    resolved = QueueBinding(sprint_ref, candidate_ref)
    if stored is not None and stored != resolved:
        raise CloseoutQueueError(
            "closeout-queue-bound-topology-invalid",
            "live topology no longer matches the contract's immutable queue binding",
        )
    return resolved


def publish_queue_bound_task_facts(
    contract: WorktreeContract,
    publication: Callable[[], T],
    *,
    topology_stable: bool,
) -> T:
    """Serialize a lifecycle-owned leaf/master task publication with its sprint queue."""

    binding = contract_queue_binding(contract)
    if binding is None:
        return publication()
    topology = TaskDocumentTopology(contract.coordination_root)
    master_ref = topology.parent(binding.candidate_ref)
    if master_ref is None:
        raise CloseoutQueueError(
            "closeout-queue-bound-topology-invalid",
            "queue-managed leaf task document has no owning master",
        )
    return CloseoutQueueStore(
        contract.coordination_root, binding.sprint_ref
    ).publish_task_facts_update(
        publication,
        owning_master=master_ref,
        topology_stable=topology_stable,
    )


def _is_unbound_legacy_absence(stored: QueueBinding | None, error: TaskDocumentRefError) -> bool:
    return stored is None and error.status in {
        "task-document-not-found",
        "task-document-parent-missing",
    }


def _live_parent_refs(
    topology: TaskDocumentTopology,
    contract: WorktreeContract,
    stored: QueueBinding | None,
) -> _LiveParents | None:
    try:
        master_ref = topology.canonical_ref(contract.repo_name, contract.task_root / "task.json")
        sprint_ref = topology.parent(master_ref)
    except TaskDocumentRefError as exc:
        if _is_unbound_legacy_absence(stored, exc):
            return None
        raise CloseoutQueueError(
            "closeout-queue-bound-topology-invalid",
            f"queue-managed contract lost its canonical topology: {exc}",
        ) from exc
    if sprint_ref is None and stored is not None:
        raise CloseoutQueueError(
            "closeout-queue-bound-topology-invalid",
            "queue-managed contract no longer has a sprint parent",
        )
    if sprint_ref is None:
        return None
    return _LiveParents(master_ref, sprint_ref)


def _stored_queue_binding(contract: WorktreeContract) -> QueueBinding | None:
    values = (
        contract.queue_sprint_task_document.strip(),
        contract.queue_candidate_task_document.strip(),
    )
    if not any(values):
        return None
    if not all(values):
        raise CloseoutQueueError(
            "closeout-queue-contract-binding-invalid",
            "queue-managed contract carries a partial task-document binding",
        )
    try:
        return QueueBinding(*(_task_ref_from_key(value) for value in values))
    except (TypeError, ValidationError, ValueError) as exc:
        raise CloseoutQueueError(
            "closeout-queue-contract-binding-invalid",
            f"queue-managed contract binding is malformed: {exc}",
        ) from exc


def _task_ref_from_key(value: str) -> TaskDocumentRef:
    repository, separator, path = value.partition("/")
    if separator != "/":
        raise ValueError("canonical task-document key has no repository/path boundary")
    return TaskDocumentRef(repository=repository, path=path)


def claim_queue_candidate_for_closeout(
    contract: WorktreeContract, operation_key: str
) -> CloseoutCandidateRecord | None:
    """Atomically bind the selected candidate to the closeout lifecycle operation."""

    binding = contract_queue_binding(contract)
    if binding is None:
        return None
    key = _required_operation_key(operation_key, "closeout")
    owner = _operation_owner(key)
    return _transition_candidate(
        contract,
        binding,
        _LifecycleTransition("claim-closeout", f"closeout-claim:{owner}", key, _claim_closeout),
    )


def certify_queue_candidate_closeout(
    contract: WorktreeContract, operation_key: str
) -> CloseoutCandidateRecord | None:
    """Bind the exact closeout commits and make the candidate integration-ready."""

    binding = contract_queue_binding(contract)
    if binding is None:
        return None
    key = _required_operation_key(operation_key, "closeout")
    owner = _operation_owner(key)
    return _transition_candidate(
        contract,
        binding,
        _LifecycleTransition(
            "certify-closeout", f"closeout-certify:{owner}", key, _certify_closeout
        ),
    )


def claim_queue_candidate_for_integration(
    contract: WorktreeContract, operation_key: str
) -> CloseoutCandidateRecord | None:
    """Atomically bind one certified candidate to the integration lifecycle operation."""

    binding = contract_queue_binding(contract)
    if binding is None:
        return None
    key = _required_operation_key(operation_key, "integration")
    owner = _operation_owner(key)
    return _transition_candidate(
        contract,
        binding,
        _LifecycleTransition(
            "claim-integration", f"integration-claim:{owner}", key, _claim_integration
        ),
    )


def require_queue_candidate_current(
    coordination_root: Path,
    sprint_ref: TaskDocumentRef,
    candidate_ref: TaskDocumentRef,
) -> CloseoutCandidateRecord:
    """Revalidate a declared pre-closeout candidate for diagnostic callers."""

    topology = TaskDocumentTopology(coordination_root)
    initial_graph = _graph_context(topology, sprint_ref)

    def inspect(state: CloseoutQueueState) -> CloseoutCandidateRecord:
        graph = _graph_context(topology, sprint_ref)
        candidate = _candidate_or_error(state, candidate_ref)
        blockers = _candidate_blockers(topology, graph, candidate)
        if blockers:
            raise CloseoutQueueError(
                "closeout-candidate-stale",
                f"candidate facts changed before closeout: {blockers!r}",
            )
        return candidate

    return CloseoutQueueStore(coordination_root, sprint_ref).inspect(
        _initial_state(sprint_ref, initial_graph.revision, now_iso()), inspect
    )


def require_queue_candidate_for_integration(
    contract: WorktreeContract,
    *,
    operation_key: str,
    code_commit: str,
    memory_content_commit: str,
    ledger_commit: str,
) -> CloseoutCandidateRecord | None:
    """Recompute the complete legal claim immediately before source refs move."""

    binding = contract_queue_binding(contract)
    if binding is None:
        return None
    topology = TaskDocumentTopology(contract.coordination_root)
    key = _required_operation_key(operation_key, "integration")
    owner = _operation_owner(key)
    initial_graph = _graph_context(topology, binding.sprint_ref)

    def inspect(state: CloseoutQueueState) -> CloseoutCandidateRecord:
        graph = _graph_context(topology, binding.sprint_ref)
        candidate = _candidate_or_error(state, binding.candidate_ref)
        if (
            candidate.state != "integration-in-flight"
            or candidate.inFlightOwnerFingerprint != owner
        ):
            raise CloseoutQueueError(
                "closeout-candidate-integration-claim-required",
                "integration requires the exact candidate claimed by this lifecycle operation",
            )
        blockers = _candidate_blockers(topology, graph, candidate)
        blockers.extend(
            _integration_commit_blockers(
                candidate, contract, code_commit, memory_content_commit, ledger_commit
            )
        )
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
                "closeout-candidate-integration-blocked",
                f"candidate is not legal at the irreversible integration boundary: {blockers!r}",
            )
        return candidate

    return CloseoutQueueStore(contract.coordination_root, binding.sprint_ref).inspect(
        _initial_state(binding.sprint_ref, initial_graph.revision, now_iso()), inspect
    )


def complete_queue_candidate_integration(
    contract: WorktreeContract,
    *,
    operation_key: str,
    code_commit: str,
    memory_content_commit: str,
    ledger_commit: str,
) -> None:
    """Consume the in-flight queue record after landing; safe on recovery retry."""

    binding = contract_queue_binding(contract)
    if binding is None:
        return
    key = _required_operation_key(operation_key, "integration")
    owner = _operation_owner(key)
    topology = TaskDocumentTopology(contract.coordination_root)
    graph = _graph_context(topology, binding.sprint_ref)
    initial = _initial_state(binding.sprint_ref, graph.revision, now_iso())
    store = CloseoutQueueStore(contract.coordination_root, binding.sprint_ref)

    def complete(state: CloseoutQueueState) -> CloseoutQueueState:
        candidate = state.candidates.get(binding.candidate_ref.key)
        if candidate is None:
            return state
        if (
            candidate.state != "integration-in-flight"
            or candidate.inFlightOwnerFingerprint != owner
        ):
            raise CloseoutQueueError(
                "closeout-candidate-integration-owner-mismatch",
                "only the owning integration lifecycle may consume this candidate",
            )
        expected = (
            candidate.closeoutCodeCommit or "",
            candidate.closeoutMemoryContentCommit or "",
            candidate.closeoutLedgerCommit or "",
        )
        if expected != (code_commit, memory_content_commit, ledger_commit):
            raise CloseoutQueueError(
                "closeout-candidate-integration-commit-mismatch",
                "landed commits do not match the queue-certified closeout commits",
            )
        candidates = dict(state.candidates)
        candidates.pop(binding.candidate_ref.key)
        return state.model_copy(update={"candidates": candidates})

    store.transact(
        initial=initial,
        event=_internal_event(
            "complete-integration",
            f"integration-complete:{owner}",
            {
                "candidate": binding.candidate_ref.key,
                "commits": [code_commit, memory_content_commit, ledger_commit],
            },
        ),
        transform=complete,
    )


def release_queue_candidate_after_reversible_operation(
    contract: WorktreeContract,
    *,
    operation_key: str,
    operation_kind: str,
) -> None:
    """Release task-addressed lifecycle ownership after cancellation or reversible failure."""

    binding = contract_queue_binding(contract)
    if binding is None:
        return
    if operation_kind not in {"closeout", "integrate"}:
        raise CloseoutQueueError(
            "closeout-queue-operation-kind-invalid",
            f"unsupported lifecycle operation kind: {operation_kind!r}",
        )
    key = _required_operation_key(operation_key, operation_kind)
    owner = _operation_owner(key)
    topology = TaskDocumentTopology(contract.coordination_root)
    graph = _graph_context(topology, binding.sprint_ref)
    initial = _initial_state(binding.sprint_ref, graph.revision, now_iso())

    def release(state: CloseoutQueueState) -> CloseoutQueueState:
        candidate = _candidate_or_error(state, binding.candidate_ref)
        if operation_kind == "closeout":
            if candidate.state == "selected":
                target = candidate.model_copy(update={"state": "declared"})
            elif (
                candidate.state == "closeout-in-flight"
                and candidate.inFlightOwnerFingerprint == owner
            ):
                target = candidate.model_copy(
                    update={"state": "declared", "inFlightOwnerFingerprint": None}
                )
            elif candidate.state == "certified":
                return state
            else:
                raise CloseoutQueueError(
                    "closeout-candidate-closeout-owner-mismatch",
                    "reversible closeout release does not own the candidate",
                )
        elif candidate.state == "certified":
            return state
        elif (
            candidate.state == "integration-in-flight"
            and candidate.inFlightOwnerFingerprint == owner
        ):
            target = candidate.model_copy(
                update={"state": "certified", "inFlightOwnerFingerprint": None}
            )
        else:
            raise CloseoutQueueError(
                "closeout-candidate-integration-owner-mismatch",
                "reversible integration release does not own the candidate",
            )
        candidates = dict(state.candidates)
        candidates[binding.candidate_ref.key] = target
        return state.model_copy(update={"candidates": candidates})

    CloseoutQueueStore(contract.coordination_root, binding.sprint_ref).transact(
        initial=initial,
        event=_internal_event(
            "release-selection",
            f"{operation_kind}-release:{owner}",
            {"candidate": binding.candidate_ref.key, "operationKind": operation_kind},
        ),
        transform=release,
    )


def _transition_candidate(
    contract: WorktreeContract,
    binding: QueueBinding,
    transition: _LifecycleTransition,
) -> CloseoutCandidateRecord:
    topology = TaskDocumentTopology(contract.coordination_root)
    initial_graph = _graph_context(topology, binding.sprint_ref)
    initial = _initial_state(binding.sprint_ref, initial_graph.revision, now_iso())

    def apply(state: CloseoutQueueState) -> CloseoutQueueState:
        graph = _graph_context(topology, binding.sprint_ref)
        candidate = _candidate_or_error(state, binding.candidate_ref)
        updated = transition.transform(
            _LifecycleCandidateContext(topology, graph, state, contract, transition.operation_key),
            candidate,
        )
        candidates = dict(state.candidates)
        candidates[binding.candidate_ref.key] = updated
        return state.model_copy(update={"candidates": candidates})

    state = CloseoutQueueStore(contract.coordination_root, binding.sprint_ref).transact(
        initial=initial,
        event=_internal_event(
            transition.action,
            transition.request_id,
            {
                "candidate": binding.candidate_ref.key,
                "ownerFingerprint": _operation_owner(transition.operation_key),
            },
        ),
        transform=apply,
    )
    return _candidate_or_error(state, binding.candidate_ref)


def _claim_closeout(
    context: _LifecycleCandidateContext,
    candidate: CloseoutCandidateRecord,
) -> CloseoutCandidateRecord:
    operation_key = context.operation_key
    owner = _operation_owner(operation_key)
    if candidate.state == "closeout-in-flight" and candidate.inFlightOwnerFingerprint == owner:
        return candidate
    if candidate.state != "selected":
        raise CloseoutQueueError(
            "closeout-candidate-selection-required",
            "closeout requires the orchestrator-selected candidate",
        )
    blockers = _candidate_blockers(context.topology, context.graph, candidate)
    blockers.extend(
        _waiting_reasons(
            context.graph,
            candidate,
            _active_lane_owner(context.state),
            context.state.activeBarrier,
        )
    )
    if blockers:
        raise CloseoutQueueError(
            "closeout-candidate-not-ready",
            f"selected candidate changed before closeout: {blockers!r}",
        )
    if Path(candidate.contractPath) != context.contract.contract_path:
        raise CloseoutQueueError("closeout-candidate-contract-mismatch", "queue contract changed")
    return candidate.model_copy(
        update={"state": "closeout-in-flight", "inFlightOwnerFingerprint": owner}
    )


def _certify_closeout(
    context: _LifecycleCandidateContext,
    candidate: CloseoutCandidateRecord,
) -> CloseoutCandidateRecord:
    operation_key = context.operation_key
    owner = _operation_owner(operation_key)
    if candidate.state == "certified":
        return candidate
    if candidate.state != "closeout-in-flight" or candidate.inFlightOwnerFingerprint != owner:
        raise CloseoutQueueError(
            "closeout-candidate-closeout-owner-mismatch",
            "only the owning closeout lifecycle may certify this candidate",
        )
    refreshed_memory_evidence = (
        curator_evidence(context.contract) if candidate.memoryMode == "external" else []
    )
    blockers = _post_closeout_blockers(
        context.topology,
        context.graph,
        candidate,
        context.contract,
        expected_memory_evidence=refreshed_memory_evidence,
    )
    if blockers:
        raise CloseoutQueueError(
            "closeout-candidate-certification-blocked",
            f"closeout result does not preserve the declared candidate: {blockers!r}",
        )
    return candidate.model_copy(
        update={
            "state": "certified",
            "inFlightOwnerFingerprint": None,
            "closeoutCodeCommit": context.contract.code_commit,
            "closeoutMemoryContentCommit": context.contract.memory_content_commit or None,
            "closeoutLedgerCommit": context.contract.ledger_commit or None,
            "memoryEvidence": refreshed_memory_evidence,
        }
    )


def _claim_integration(
    context: _LifecycleCandidateContext,
    candidate: CloseoutCandidateRecord,
) -> CloseoutCandidateRecord:
    operation_key = context.operation_key
    owner = _operation_owner(operation_key)
    if candidate.state == "integration-in-flight" and candidate.inFlightOwnerFingerprint == owner:
        return candidate
    if candidate.state != "certified":
        raise CloseoutQueueError(
            "closeout-candidate-certification-required",
            "integration requires an exact certified closeout candidate",
        )
    blockers = _post_closeout_blockers(context.topology, context.graph, candidate, context.contract)
    if blockers:
        raise CloseoutQueueError(
            "closeout-candidate-integration-blocked",
            f"certified candidate changed before integration: {blockers!r}",
        )
    return candidate.model_copy(
        update={"state": "integration-in-flight", "inFlightOwnerFingerprint": owner}
    )


def _integration_commit_blockers(
    candidate: CloseoutCandidateRecord,
    contract: WorktreeContract,
    code_commit: str,
    memory_content_commit: str,
    ledger_commit: str,
) -> list[str]:
    blockers: list[str] = []
    if code_commit != candidate.closeoutCodeCommit or code_commit != contract.code_commit:
        blockers.append("integration-code-commit-not-certified")
    if (memory_content_commit or None) != candidate.closeoutMemoryContentCommit:
        blockers.append("integration-memory-commit-not-certified")
    if (ledger_commit or None) != candidate.closeoutLedgerCommit:
        blockers.append("integration-ledger-commit-not-certified")
    if commit_tree(contract.code_worktree, code_commit) != candidate.candidateTree:
        blockers.append("integration-code-tree-not-certified")
    return blockers


def _internal_event(
    action: QueueEventAction, request_id: str, payload: dict[str, Any]
) -> QueueTransaction:
    fingerprint = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return QueueTransaction(
        action=action,
        request_id=request_id,
        fingerprint=fingerprint,
        recorded_at=now_iso(),
        actor="lifecycle-operation",
        rationale="plane-owned closeout queue transition",
    )


def _required_operation_key(value: str, kind: str) -> str:
    key = value.strip()
    if not re.fullmatch(r"[0-9a-f]{64}", key):
        raise CloseoutQueueError(
            "closeout-queue-operation-key-required",
            f"queued {kind} requires the plane-owned 64-hex lifecycle operation key",
        )
    return key
