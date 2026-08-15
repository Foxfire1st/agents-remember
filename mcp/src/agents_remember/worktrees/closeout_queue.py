"""Mechanistic pre-closeout admission and deterministic sprint scheduling."""

from __future__ import annotations

import hashlib
import json
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from pydantic import ValidationError

from agents_remember.controlplane.closeout_queue_store import (
    CloseoutQueueStore,
    QueueTransaction,
)
from agents_remember.kernel.authority import require_within_coordination
from agents_remember.kernel.memory_ledger import find_mapping, load_ledger
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.closeout_queue import (
    ActiveAtomicBarrier,
    CandidateAdmissionFacts,
    CloseoutCandidateRecord,
    CloseoutQueueCandidateView,
    CloseoutQueueRequest,
    CloseoutQueueState,
    EvidenceFact,
    QueueAction,
    QueueEventAction,
    SchedulingGrade,
    SchedulingGradeInput,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import completion_blockers
from agents_remember.tasks.document_refs import TaskDocumentRefError, TaskDocumentTopology
from agents_remember.tasks.leaf_doc import (
    TerminalLeafResolutionError,
    resolve_terminal_leaf_doc,
)
from agents_remember.worktrees.lifecycle_operation_store import (
    LifecycleOperationStore,
    operation_record_path,
)
from agents_remember.worktrees.modules.git import head_commit, is_ancestor
from agents_remember.worktrees.route_review import (
    RouteReviewError,
    code_candidate_tree,
)
from agents_remember.worktrees.source_lineage import (
    lineage_refusal,
    source_lineage_for_contract,
)
from agents_remember.worktrees.worktree_contract import (
    ContractError,
    WorktreeContract,
    load_contract,
    write_contract,
)

from .closeout_queue_candidate_evidence import (
    commit_tree,
    ledger_mapping,
    memory_candidate_tree,
    operation_owner_fingerprint,
    require_atomic_master_landed,
    require_source_bases_current,
    route_review_blockers,
    route_review_fact,
)
from .closeout_queue_errors import CloseoutQueueError
from .closeout_queue_evidence import (
    PRIORITY_RANK,
    canonical_barrier_abort,
    canonical_grade,
    curator_evidence,
    curator_evidence_blockers,
)
from .closeout_queue_graph import (
    QueueGraphContext as _GraphContext,
)
from .closeout_queue_graph import (
    graph_context as _graph_context,
)

_ACTIONS = frozenset(
    {
        "status",
        "declare",
        "withdraw",
        "set-grade",
        "set-admission",
        "select",
        "release-selection",
        "acquire-barrier",
        "release-barrier",
        "abort-barrier",
    }
)


@dataclass(frozen=True)
class QueueActor:
    """Plane-proven structural caller; never accepted from the public request."""

    role: str
    task_document_ref: TaskDocumentRef

    @property
    def identity(self) -> str:
        return f"{self.role}@{self.task_document_ref.key}"


def _authorize_status_scope(actor: QueueActor, graph: _GraphContext) -> None:
    sprint_roles = {"architect", "strategist", "orchestrator"}
    if actor.task_document_ref == graph.sprint.ref and actor.role in sprint_roles:
        return
    if actor.role == "manager" and actor.task_document_ref in graph.masters:
        return
    raise CloseoutQueueError(
        "closeout-queue-caller-refused",
        "closeout queue access requires the sprint architect/strategist/orchestrator or a commanded manager",
    )


def _require_sprint_role(actor: QueueActor, graph: _GraphContext, role: str) -> None:
    if actor.role != role or actor.task_document_ref != graph.sprint.ref:
        raise CloseoutQueueError(
            "closeout-queue-caller-refused",
            f"{role} authority on the canonical sprint document is required",
        )


def _authorize_candidate_action(
    actor: QueueActor,
    graph: _GraphContext,
    action: QueueAction,
    candidate: CloseoutCandidateRecord,
) -> None:
    if action in {"set-grade", "select", "release-selection"}:
        _require_sprint_role(actor, graph, "orchestrator")
        return
    if action == "set-admission":
        if actor.role == "manager" and actor.task_document_ref == candidate.owningMaster:
            return
    elif action == "withdraw":
        if (actor.role == "manager" and actor.task_document_ref == candidate.owningMaster) or (
            actor.role == "orchestrator" and actor.task_document_ref == graph.sprint.ref
        ):
            return
    else:
        raise AssertionError(f"unhandled candidate authorization: {action}")
    raise CloseoutQueueError(
        "closeout-queue-caller-refused",
        f"{action} requires the owning manager or the sprint orchestrator authority assigned by doctrine",
    )


@dataclass(frozen=True)
class _ActionContext:
    config: McpRuntimeConfig
    topology: TaskDocumentTopology
    graph: _GraphContext
    request: CloseoutQueueRequest
    action: QueueAction
    timestamp: str


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def closeout_queue_tool(
    config: McpRuntimeConfig,
    request: CloseoutQueueRequest,
    *,
    actor: QueueActor,
    now: str | None = None,
) -> dict[str, Any]:
    """Apply one durable queue transition or return the current recomputed projection."""

    action = _queue_action(request.action)
    sprint_ref = _task_ref(request.sprint_task_document_ref, "sprint_task_document_ref")
    topology = TaskDocumentTopology(config.coordination_root)
    graph = _graph_context(topology, sprint_ref)
    timestamp = (now or now_iso()).strip()
    if not timestamp:
        raise CloseoutQueueError("closeout-queue-time-invalid", "timestamp must not be blank")
    initial = _initial_state(sprint_ref, graph.revision, timestamp)
    store = CloseoutQueueStore(config.coordination_root, sprint_ref)
    _authorize_status_scope(actor, graph)
    if graph.sprint.document.status == "Completed":
        if action != "status":
            raise CloseoutQueueError(
                "closeout-queue-sprint-completed",
                "completed sprint queues are reclaimed and cannot accept mutations",
            )
        # Task-document publication is the sole close/reopen owner and holds this store's lock.
        # A status read never rewrites state, so it cannot race a serialized sprint reopen.
        state = store.read(initial)
    elif action == "status":
        state = store.read(initial)
    else:
        request_id = (request.request_id or "").strip()
        if not request_id:
            raise CloseoutQueueError(
                "closeout-queue-request-id-required",
                f"{action} requires a stable request_id for crash-safe retry",
            )

        def apply_current_graph(current: CloseoutQueueState) -> CloseoutQueueState:
            if request.expected_revision != current.revision:
                raise CloseoutQueueError(
                    "closeout-queue-revision-stale",
                    f"mutation expected revision {request.expected_revision}, current is {current.revision}; read status and retry with a new request id",
                )
            live_graph = _graph_context(topology, sprint_ref)
            _authorize_status_scope(actor, live_graph)
            if live_graph.sprint.document.status == "Completed":
                raise CloseoutQueueError(
                    "closeout-queue-sprint-completed",
                    "completed sprint queues are reclaimed and cannot accept mutations",
                )
            return _apply_action(
                current,
                _ActionContext(config, topology, live_graph, request, action, timestamp),
                actor,
            )

        state = store.transact(
            initial=initial,
            event=QueueTransaction(
                action=cast(QueueEventAction, action),
                request_id=request_id,
                fingerprint=_request_fingerprint(request, actor),
                recorded_at=timestamp,
                actor=actor.identity,
                rationale=request.rationale,
            ),
            transform=apply_current_graph,
        )
    # A task-doc graph publication uses the same store lock. Re-read after the state operation so
    # the response either projects the transaction's graph or visibly blocks it as stale.
    graph = _graph_context(topology, sprint_ref)
    _authorize_status_scope(actor, graph)
    return _projection(topology, graph, state, action, actor)


def _apply_action(
    state: CloseoutQueueState, context: _ActionContext, actor: QueueActor
) -> CloseoutQueueState:
    if state.closed:
        raise CloseoutQueueError(
            "closeout-queue-closed", "a completed sprint queue cannot accept mutations"
        )
    current = state.model_copy(update={"graphRevision": context.graph.revision})
    if context.action == "declare":
        return _declare_candidate(current, context, actor)
    if context.action in {"withdraw", "set-grade", "set-admission", "select", "release-selection"}:
        return _apply_candidate_action(current, context, actor)
    if context.action == "acquire-barrier":
        _require_sprint_role(actor, context.graph, "orchestrator")
        return _acquire_barrier(
            context.graph,
            current,
            context.request,
            context.timestamp,
            actor.identity,
        )
    if context.action == "release-barrier":
        _require_sprint_role(actor, context.graph, "orchestrator")
        return _release_barrier(context.graph, current, context.request)
    if context.action == "abort-barrier":
        _require_sprint_role(actor, context.graph, "orchestrator")
        return _abort_barrier(context.graph, current, context.request)
    raise AssertionError(f"unhandled closeout queue action: {context.action}")


def _apply_candidate_action(
    state: CloseoutQueueState, context: _ActionContext, actor: QueueActor
) -> CloseoutQueueState:
    request = context.request
    candidate_ref = _required_candidate_ref(request)
    candidate = state.candidates.get(candidate_ref.key)
    if candidate is None:
        if context.action == "withdraw":
            return state
        raise CloseoutQueueError(
            "closeout-candidate-not-declared", f"candidate is not declared: {candidate_ref.key}"
        )
    _authorize_candidate_action(actor, context.graph, context.action, candidate)
    candidates = dict(state.candidates)
    if context.action == "release-selection":
        candidates[candidate_ref.key] = _release_selection(candidate)
        return state.model_copy(update={"candidates": candidates})
    if candidate.state != "declared":
        raise CloseoutQueueError(
            "closeout-candidate-in-flight-immutable",
            f"{candidate.state} candidate is frozen until its owning lifecycle releases it",
        )
    if context.action == "withdraw":
        candidates.pop(candidate_ref.key)
    elif context.action == "set-grade":
        grade, judgment_digest, evidence = _grade(
            request.grade,
            context.graph,
            candidate.taskDocumentRef,
            candidate.owningMaster,
        )
        candidates[candidate_ref.key] = candidate.model_copy(
            update={
                "grade": grade,
                "gradeJudgmentDigest": judgment_digest,
                "gradeEvidence": evidence,
            }
        )
    elif context.action == "set-admission":
        candidates[candidate_ref.key] = candidate.model_copy(
            update={"admission": _admission(request.admission)}
        )
    else:
        projection = _project_candidates(context.topology, context.graph, state, actor)
        first_ready = projection["ready"][0].taskDocumentRef if projection["ready"] else None
        if candidate_ref != first_ready:
            raise CloseoutQueueError(
                "closeout-candidate-not-ready",
                "selection must take the first deterministic ready candidate; reprioritize "
                f"canonically before selecting {candidate_ref.key}",
            )
        candidates[candidate_ref.key] = candidate.model_copy(update={"state": "selected"})
    return state.model_copy(update={"candidates": candidates})


def _release_selection(candidate: CloseoutCandidateRecord) -> CloseoutCandidateRecord:
    if candidate.state == "declared":
        return candidate
    if candidate.state == "selected":
        return candidate.model_copy(update={"state": "declared"})
    raise CloseoutQueueError(
        "closeout-candidate-release-lifecycle-owned",
        "an active lifecycle is released only by task-addressed cancellation or failure recovery",
    )


def _declare_candidate(
    state: CloseoutQueueState, context: _ActionContext, actor: QueueActor
) -> CloseoutQueueState:
    request = context.request
    path, contract, leaf_ref, owning_master = _declaration_identity(context)
    if actor.role != "manager" or actor.task_document_ref != owning_master:
        raise CloseoutQueueError(
            "closeout-queue-caller-refused",
            "only the owning master manager may declare its reviewed leaf candidate",
        )
    existing = state.candidates.get(leaf_ref.key)
    if existing is not None:
        raise CloseoutQueueError(
            "closeout-candidate-already-declared",
            "an existing candidate must be withdrawn before a new declaration",
        )
    memory_evidence = curator_evidence(contract) if contract.memory_mode == "external" else []
    route_review = route_review_fact(contract)
    memory_tree = memory_candidate_tree(contract)
    mapping = ledger_mapping(contract)
    require_source_bases_current(contract)
    bound_contract = _bind_queue_contract(
        contract,
        sprint_ref=context.graph.sprint.ref,
        candidate_ref=leaf_ref,
    )
    if bound_contract != contract:
        write_contract(bound_contract.contract_path, bound_contract)
        contract = bound_contract
    candidate = CloseoutCandidateRecord(
        taskDocumentRef=leaf_ref,
        owningMaster=owning_master,
        contractPath=path.as_posix(),
        candidateTree=code_candidate_tree(contract),
        memoryCandidateTree=memory_tree,
        graphRevision=context.graph.revision,
        codeBaseCommit=contract.code_base_commit,
        memoryBaseCommit=contract.memory_base_commit or None,
        ledgerMemoryCommit=mapping,
        routeReview=route_review,
        memoryMode=cast(Any, contract.memory_mode),
        memoryReadiness="ready" if contract.memory_mode == "external" else "not-applicable",
        memoryEvidence=memory_evidence,
        admission=_admission(request.admission),
        grade=None,
        gradeJudgmentDigest=None,
        gradeEvidence=[],
        state="declared",
        declaredBy=actor.identity,
        declaredAt=context.timestamp,
    )
    candidates = dict(state.candidates)
    candidates[leaf_ref.key] = candidate
    return state.model_copy(update={"candidates": candidates})


def _declaration_identity(
    context: _ActionContext,
) -> tuple[Path, WorktreeContract, TaskDocumentRef, TaskDocumentRef]:
    request = context.request
    if request.contract_path is None:
        raise CloseoutQueueError(
            "closeout-candidate-contract-required", "declare requires contract_path"
        )
    path = require_within_coordination(context.config, request.contract_path, "contract_path")
    contract = load_contract(path)
    if contract.kind != "leaf":
        raise CloseoutQueueError(
            "closeout-candidate-leaf-required", "only leaf contracts can declare candidates"
        )
    if contract.closeout_status != "not-started" or contract.integration_status != "not-started":
        raise CloseoutQueueError(
            "closeout-candidate-too-late",
            "candidate declaration must happen before closeout or integration moves history",
        )
    leaf_ref, leaf = _leaf_identity(context.topology, contract)
    blockers = completion_blockers(leaf)
    if blockers:
        raise CloseoutQueueError(
            "closeout-candidate-task-incomplete",
            f"candidate task has unresolved work units: {[item.model_dump() for item in blockers]!r}",
        )
    owning_master = context.topology.parent(leaf_ref)
    if owning_master is None or owning_master not in context.graph.masters:
        raise CloseoutQueueError(
            "closeout-candidate-master-mismatch",
            f"leaf owner is not commanded by sprint {context.graph.sprint.ref.key}",
        )
    if context.topology.parent(owning_master) != context.graph.sprint.ref:
        raise CloseoutQueueError(
            "closeout-candidate-sprint-mismatch", "leaf owner belongs to a different sprint"
        )
    return path, contract, leaf_ref, owning_master


def _bind_queue_contract(
    contract: WorktreeContract,
    *,
    sprint_ref: TaskDocumentRef,
    candidate_ref: TaskDocumentRef,
) -> WorktreeContract:
    expected = (sprint_ref.key, candidate_ref.key)
    observed = (
        contract.queue_sprint_task_document,
        contract.queue_candidate_task_document,
    )
    if observed == expected:
        return contract
    if any(observed):
        raise CloseoutQueueError(
            "closeout-candidate-contract-binding-mismatch",
            f"contract already names a different queue binding: {observed!r}",
        )
    return replace(
        contract,
        queue_sprint_task_document=sprint_ref.key,
        queue_candidate_task_document=candidate_ref.key,
    )


def _acquire_barrier(
    graph: _GraphContext,
    state: CloseoutQueueState,
    request: CloseoutQueueRequest,
    timestamp: str,
    actor_identity: str,
) -> CloseoutQueueState:
    master_ref = _task_ref(request.barrier_master_ref, "barrier_master_ref")
    master = graph.masters.get(master_ref)
    if master is None:
        raise CloseoutQueueError(
            "atomic-barrier-master-unknown", f"master is not in the sprint graph: {master_ref.key}"
        )
    if master.document.executionNature != "atomic":
        raise CloseoutQueueError(
            "atomic-barrier-nature-required", "only an atomic master can own a barrier"
        )
    if state.activeBarrier is not None:
        if (
            state.activeBarrier.master == master_ref
            and state.activeBarrier.graphRevision == graph.revision
        ):
            return state
        if state.activeBarrier.master == master_ref:
            raise CloseoutQueueError(
                "atomic-barrier-graph-stale",
                "the active barrier belongs to an older graph revision; release it first",
            )
        raise CloseoutQueueError(
            "atomic-barrier-active", f"barrier is already held by {state.activeBarrier.master.key}"
        )
    incomplete = _incomplete_predecessors(graph, master_ref)
    if incomplete:
        raise CloseoutQueueError(
            "atomic-barrier-predecessors-incomplete",
            f"atomic barrier predecessors are incomplete: {[ref.key for ref in incomplete]!r}",
        )
    if any(candidate.state != "declared" for candidate in state.candidates.values()):
        raise CloseoutQueueError(
            "atomic-barrier-in-flight-conflict", "the sprint landing lane is not drained"
        )
    rationale = request.rationale.strip()
    if not rationale:
        raise CloseoutQueueError(
            "atomic-barrier-rationale-required", "barrier acquisition requires rationale"
        )
    return state.model_copy(
        update={
            "activeBarrier": ActiveAtomicBarrier(
                master=master_ref,
                graphRevision=graph.revision,
                acquiredBy=actor_identity,
                acquiredAt=timestamp,
                rationale=rationale,
            )
        }
    )


def _release_barrier(
    graph: _GraphContext, state: CloseoutQueueState, request: CloseoutQueueRequest
) -> CloseoutQueueState:
    if state.activeBarrier is None:
        raise CloseoutQueueError("atomic-barrier-not-active", "no atomic barrier is active")
    asserted = _task_ref(request.barrier_master_ref, "barrier_master_ref")
    if asserted != state.activeBarrier.master:
        raise CloseoutQueueError(
            "atomic-barrier-owner-mismatch",
            f"active barrier belongs to {state.activeBarrier.master.key}",
        )
    if any(candidate.owningMaster == asserted for candidate in state.candidates.values()):
        raise CloseoutQueueError(
            "atomic-barrier-candidates-remain",
            "the atomic master still has declared or lifecycle-owned candidates",
        )
    master = graph.masters.get(asserted)
    if master is None or master.document.status != "Completed":
        raise CloseoutQueueError(
            "atomic-barrier-master-incomplete",
            "normal barrier release requires the canonical atomic master completion edge",
        )
    require_atomic_master_landed(master)
    if not request.rationale.strip():
        raise CloseoutQueueError(
            "atomic-barrier-rationale-required", "barrier release requires rationale"
        )
    return state.model_copy(update={"activeBarrier": None})


def _abort_barrier(
    graph: _GraphContext, state: CloseoutQueueState, request: CloseoutQueueRequest
) -> CloseoutQueueState:
    if state.activeBarrier is None:
        raise CloseoutQueueError("atomic-barrier-not-active", "no atomic barrier is active")
    asserted = _task_ref(request.barrier_master_ref, "barrier_master_ref")
    if asserted != state.activeBarrier.master:
        raise CloseoutQueueError(
            "atomic-barrier-owner-mismatch",
            f"active barrier belongs to {state.activeBarrier.master.key}",
        )
    if any(candidate.owningMaster == asserted for candidate in state.candidates.values()):
        raise CloseoutQueueError(
            "atomic-barrier-candidates-remain",
            "withdraw or finish every atomic candidate before recording an abort",
        )
    canonical_barrier_abort(
        request.barrier_judgment_id,
        authority=graph.grade_authority,
        master_ref=asserted,
        graph_revision=graph.revision,
    )
    return state.model_copy(update={"activeBarrier": None})


def _projection(
    topology: TaskDocumentTopology,
    graph: _GraphContext,
    state: CloseoutQueueState,
    action: QueueAction,
    actor: QueueActor,
) -> dict[str, Any]:
    groups = _project_candidates(topology, graph, state, actor)
    total = sum(len(items) for items in groups.values())
    return {
        "ok": True,
        "operation": "closeout_queue",
        "action": action,
        "state": "projected",
        "summary": (
            f"Recomputed {total} closeout candidates against graph {graph.revision[:12]}; "
            f"ready={len(groups['ready'])}, waiting={len(groups['waiting'])}, "
            f"blocked={len(groups['blocked'])}, inFlight={len(groups['inFlight'])}."
        ),
        "sprintTaskDocumentRef": graph.sprint.ref.model_dump(mode="json"),
        "revision": state.revision,
        "graphRevision": graph.revision,
        "activeBarrier": state.activeBarrier.model_dump(mode="json")
        if state.activeBarrier
        else None,
        **{
            name: [view.model_dump(mode="json") for view in views] for name, views in groups.items()
        },
        "updatedAt": state.updatedAt,
    }


def _project_candidates(
    topology: TaskDocumentTopology,
    graph: _GraphContext,
    state: CloseoutQueueState,
    actor: QueueActor,
) -> dict[str, list[CloseoutQueueCandidateView]]:
    groups: dict[str, list[CloseoutQueueCandidateView]] = {
        "ready": [],
        "waiting": [],
        "blocked": [],
        "inFlight": [],
    }
    lane_owner = _active_lane_owner(state)
    for candidate in state.candidates.values():
        blockers = _candidate_blockers(topology, graph, candidate)
        if candidate.state != "declared":
            blockers.extend(_waiting_reasons(graph, candidate, lane_owner, state.activeBarrier))
            blockers = list(dict.fromkeys(blockers))
        if blockers:
            classification = "blocked"
            reasons = blockers
            legal = _blocked_legal_operations(graph, candidate, actor)
        elif candidate.state != "declared":
            classification = "in-flight"
            reasons = []
            legal = _in_flight_legal_operations(graph, candidate, actor)
        else:
            reasons = _waiting_reasons(graph, candidate, lane_owner, state.activeBarrier)
            classification = "waiting" if reasons else "ready"
            legal = _declared_legal_operations(graph, candidate, actor, ready=not reasons)
        view = CloseoutQueueCandidateView(
            taskDocumentRef=candidate.taskDocumentRef,
            owningMaster=candidate.owningMaster,
            contractPath=candidate.contractPath,
            candidateTree=candidate.candidateTree,
            graphRevision=candidate.graphRevision,
            candidateState=candidate.state,
            classification=cast(Any, classification),
            reasons=reasons,
            legalNextOperations=legal,
            grade=candidate.grade,
        )
        groups[_group_name(classification)].append(view)
    groups["ready"].sort(key=lambda view: _ready_sort_key(graph, view))
    for name in ("waiting", "blocked", "inFlight"):
        groups[name].sort(key=lambda item: item.taskDocumentRef.key)
    return groups


def _declared_legal_operations(
    graph: _GraphContext,
    candidate: CloseoutCandidateRecord,
    actor: QueueActor,
    *,
    ready: bool,
) -> list[str]:
    if actor.role == "orchestrator" and actor.task_document_ref == graph.sprint.ref:
        return [*(["select"] if ready else []), "set-grade", "withdraw"]
    if actor.role == "manager" and actor.task_document_ref == candidate.owningMaster:
        return ["set-admission", "withdraw"]
    return []


def _blocked_legal_operations(
    graph: _GraphContext, candidate: CloseoutCandidateRecord, actor: QueueActor
) -> list[str]:
    if candidate.state == "declared":
        return _declared_legal_operations(graph, candidate, actor, ready=False)
    if candidate.state == "selected":
        return (
            ["release-selection"]
            if actor.role == "orchestrator" and actor.task_document_ref == graph.sprint.ref
            else []
        )
    if candidate.state == "certified":
        return []
    return _lifecycle_operation_legal(graph, candidate, actor)


def _in_flight_legal_operations(
    graph: _GraphContext, candidate: CloseoutCandidateRecord, actor: QueueActor
) -> list[str]:
    if candidate.state == "selected":
        if actor.role == "manager" and actor.task_document_ref == candidate.owningMaster:
            return ["worktree_closeout_apply"]
        if actor.role == "orchestrator" and actor.task_document_ref == graph.sprint.ref:
            return ["release-selection"]
        return []
    if candidate.state == "closeout-in-flight":
        return _lifecycle_operation_legal(graph, candidate, actor)
    if candidate.state == "certified":
        return ["worktree_integrate"] if _integration_actor(graph, candidate, actor) else []
    return _lifecycle_operation_legal(graph, candidate, actor)


def _lifecycle_operation_legal(
    graph: _GraphContext, candidate: CloseoutCandidateRecord, actor: QueueActor
) -> list[str]:
    kind = "closeout" if candidate.state == "closeout-in-flight" else "integrate"
    authorized = (
        actor.role == "manager" and actor.task_document_ref == candidate.owningMaster
        if kind == "closeout"
        else _integration_actor(graph, candidate, actor)
    )
    if not authorized:
        return []
    observe = "worktree_closeout_apply" if kind == "closeout" else "worktree_integrate"
    record = _owned_lifecycle_operation(candidate)
    if record is None or record.status == "completed":
        return []
    if record.status in {"failed", "cancelled"}:
        return [] if record.irreversibleBoundaryEntered else [observe]
    legal = [observe]
    if not record.irreversibleBoundaryEntered:
        legal.append("worktree_operation_cancel")
    return legal


def _integration_actor(
    graph: _GraphContext, candidate: CloseoutCandidateRecord, actor: QueueActor
) -> bool:
    return (
        candidate.owningMaster in graph.masters
        and actor.role == "manager"
        and actor.task_document_ref == candidate.owningMaster
    )


def _owned_lifecycle_operation(candidate: CloseoutCandidateRecord) -> Any | None:
    kind = "closeout" if candidate.state == "closeout-in-flight" else "integrate"
    try:
        contract = load_contract(Path(candidate.contractPath))
        record = LifecycleOperationStore(
            operation_record_path(contract.worktree_group, cast(Any, kind))
        ).read()
    except (ContractError, OSError, RuntimeError, ValidationError):
        return None
    if (
        record is None
        or record.operationKind != kind
        or Path(record.contractPath) != Path(candidate.contractPath)
        or operation_owner_fingerprint(record.operationKey) != candidate.inFlightOwnerFingerprint
    ):
        return None
    return record


def _candidate_blockers(
    topology: TaskDocumentTopology,
    graph: _GraphContext,
    candidate: CloseoutCandidateRecord,
) -> list[str]:
    blockers: list[str] = []
    if candidate.graphRevision != graph.revision:
        blockers.append("graph-revision-stale")
    if candidate.owningMaster not in graph.masters:
        blockers.append("owning-master-no-longer-commanded")
    if candidate.state in {"closeout-in-flight", "integration-in-flight"}:
        operation = _owned_lifecycle_operation(candidate)
        if operation is None:
            blockers.append("lifecycle-operation-owner-unavailable")
        elif operation.status in {"completed", "failed", "cancelled"}:
            blockers.append("lifecycle-operation-owner-terminal")
    try:
        contract = load_contract(Path(candidate.contractPath))
        if candidate.state in {"certified", "integration-in-flight"} or (
            candidate.state == "closeout-in-flight" and contract.closeout_status == "completed"
        ):
            refreshed_memory_evidence = None
            if candidate.state == "closeout-in-flight" and candidate.memoryMode == "external":
                with suppress(CloseoutQueueError):
                    refreshed_memory_evidence = curator_evidence(contract)
            blockers.extend(
                _post_closeout_blockers(
                    topology,
                    graph,
                    candidate,
                    contract,
                    expected_memory_evidence=refreshed_memory_evidence,
                )
            )
        else:
            blockers.extend(_pre_closeout_blockers(topology, graph, candidate, contract))
    except (
        CloseoutQueueError,
        ContractError,
        OSError,
        RouteReviewError,
        RuntimeError,
        TaskDocumentRefError,
        TerminalLeafResolutionError,
        ValidationError,
        ValueError,
    ) as exc:
        blockers.append(f"candidate-revalidation-failed: {exc}")
    return list(dict.fromkeys(blockers))


def _pre_closeout_blockers(
    topology: TaskDocumentTopology,
    graph: _GraphContext,
    candidate: CloseoutCandidateRecord,
    contract: WorktreeContract,
) -> list[str]:
    blockers = _common_candidate_blockers(topology, graph, candidate, contract)
    if contract.closeout_status != "not-started":
        blockers.append("closeout-already-started")
    if contract.integration_status != "not-started":
        blockers.append("integration-already-started")
    if code_candidate_tree(contract) != candidate.candidateTree:
        blockers.append("candidate-tree-stale")
    if memory_candidate_tree(contract) != candidate.memoryCandidateTree:
        blockers.append("memory-candidate-tree-stale")
    blockers.extend(_source_and_ledger_blockers(candidate, contract))
    return blockers


def _post_closeout_blockers(
    topology: TaskDocumentTopology,
    graph: _GraphContext,
    candidate: CloseoutCandidateRecord,
    contract: WorktreeContract,
    *,
    expected_memory_evidence: list[EvidenceFact] | None = None,
) -> list[str]:
    blockers = _common_candidate_blockers(
        topology,
        graph,
        candidate,
        contract,
        expected_memory_evidence=expected_memory_evidence,
    )
    if contract.closeout_status != "completed":
        blockers.append("closeout-not-certified")
        return blockers
    if contract.integration_status not in {"not-started", "completed"}:
        blockers.append("integration-state-invalid")
    blockers.extend(_closed_tree_blockers(candidate, contract))
    blockers.extend(_certified_commit_blockers(candidate, contract))
    blockers.extend(_source_and_ledger_blockers(candidate, contract))
    blockers.extend(_closed_ledger_blockers(contract))
    return blockers


def _closed_tree_blockers(
    candidate: CloseoutCandidateRecord, contract: WorktreeContract
) -> list[str]:
    if (
        not contract.code_commit
        or commit_tree(contract.code_worktree, contract.code_commit) != candidate.candidateTree
    ):
        return ["closeout-code-tree-mismatch"]
    # External-memory closeout deliberately refreshes verification metadata after the
    # pre-closeout candidate was selected, so its content tree is expected to change. The
    # owning lifecycle operation and the ledger mapping below bind that deterministic result;
    # pre-closeout revalidation already proved the curated input tree immediately before claim.
    if contract.memory_mode == "external" and (
        not contract.memory_content_commit or contract.memory_worktree is None
    ):
        return ["closeout-memory-commit-missing"]
    return []


def _certified_commit_blockers(
    candidate: CloseoutCandidateRecord, contract: WorktreeContract
) -> list[str]:
    blockers: list[str] = []
    pairs = (
        (candidate.closeoutCodeCommit, contract.code_commit, "closeout-code-commit-changed"),
        (
            candidate.closeoutMemoryContentCommit,
            contract.memory_content_commit,
            "closeout-memory-commit-changed",
        ),
        (
            candidate.closeoutLedgerCommit,
            contract.ledger_commit,
            "closeout-ledger-commit-changed",
        ),
    )
    blockers.extend(
        label for expected, observed, label in pairs if expected and expected != observed
    )
    return blockers


def _closed_ledger_blockers(contract: WorktreeContract) -> list[str]:
    if contract.memory_mode != "external":
        return []
    if contract.ledger_path is None:
        return ["closeout-ledger-missing"]
    row = find_mapping(load_ledger(contract.ledger_path), contract.code_commit)
    if row is None or row.memory_commit != contract.memory_content_commit:
        return ["closeout-ledger-mapping-mismatch"]
    if (
        contract.memory_worktree is None
        or not contract.ledger_commit
        or not is_ancestor(
            contract.memory_worktree,
            contract.memory_content_commit,
            contract.ledger_commit,
        )
    ):
        return ["closeout-memory-commit-unreachable"]
    return []


def _common_candidate_blockers(
    topology: TaskDocumentTopology,
    graph: _GraphContext,
    candidate: CloseoutCandidateRecord,
    contract: WorktreeContract,
    *,
    expected_memory_evidence: list[EvidenceFact] | None = None,
) -> list[str]:
    blockers: list[str] = []
    leaf_ref, leaf = _leaf_identity(topology, contract)
    if leaf_ref != candidate.taskDocumentRef:
        blockers.append("leaf-task-document-changed")
    elif topology.parent(leaf_ref) != candidate.owningMaster:
        blockers.append("owning-master-changed")
    if completion_blockers(leaf):
        blockers.append("leaf-task-incomplete")
    if contract.code_base_commit != candidate.codeBaseCommit:
        blockers.append("code-base-changed")
    if contract.memory_mode != candidate.memoryMode:
        blockers.append("memory-mode-changed")
    if (contract.memory_base_commit or None) != candidate.memoryBaseCommit:
        blockers.append("memory-base-changed")
    blockers.extend(route_review_blockers(contract, candidate.routeReview))
    expected = (
        candidate.memoryEvidence if expected_memory_evidence is None else expected_memory_evidence
    )
    blockers.extend(
        curator_evidence_blockers(
            contract,
            expected,
            required=candidate.memoryMode == "external",
        )
    )
    blockers.extend(_grade_blockers(graph, candidate))
    return blockers


def _source_and_ledger_blockers(
    candidate: CloseoutCandidateRecord, contract: WorktreeContract
) -> list[str]:
    blockers: list[str] = []
    lineage = lineage_refusal(source_lineage_for_contract(contract))
    if lineage is not None:
        blockers.append(lineage[0])
    if (
        head_commit(contract.code_repo_path, contract.code_source_branch)
        != candidate.codeBaseCommit
    ):
        blockers.append("code-source-moved")
    if contract.memory_mode != "external":
        return blockers
    if contract.memory_repo_path is None or candidate.memoryBaseCommit is None:
        blockers.append("memory-source-missing")
        return blockers
    if (
        head_commit(contract.memory_repo_path, contract.memory_source_branch)
        != candidate.memoryBaseCommit
    ):
        blockers.append("memory-source-moved")
    if ledger_mapping(contract) != candidate.ledgerMemoryCommit:
        blockers.append("ledger-base-mapping-changed")
    return blockers


def _grade_blockers(graph: _GraphContext, candidate: CloseoutCandidateRecord) -> list[str]:
    if candidate.grade is None:
        return []
    try:
        grade, digest, evidence = _grade(
            SchedulingGradeInput(
                priority=candidate.grade.priority,
                judgmentId=candidate.grade.judgmentId,
                urgency=candidate.grade.urgency,
                risk=candidate.grade.risk,
            ),
            graph,
            candidate.taskDocumentRef,
            candidate.owningMaster,
        )
    except CloseoutQueueError as exc:
        return [f"grade-evidence-invalid: {exc}"]
    blockers = []
    if grade != candidate.grade or digest != candidate.gradeJudgmentDigest:
        blockers.append("grade-judgment-stale")
    if evidence != candidate.gradeEvidence:
        blockers.append("grade-evidence-stale")
    return blockers


def _waiting_reasons(
    graph: _GraphContext,
    candidate: CloseoutCandidateRecord,
    lane_owner: TaskDocumentRef | None,
    active_barrier: ActiveAtomicBarrier | None,
) -> list[str]:
    reasons = [
        f"predecessor-incomplete: {ref.key}"
        for ref in _incomplete_predecessors(graph, candidate.owningMaster)
    ]
    if candidate.grade is None:
        reasons.append("explicit-grade-required")
    if lane_owner is not None and lane_owner != candidate.taskDocumentRef:
        reasons.append(f"integration-lane-owned-by: {lane_owner.key}")
    master = graph.masters.get(candidate.owningMaster)
    if active_barrier is not None and active_barrier.graphRevision != graph.revision:
        reasons.append("atomic-barrier-graph-revision-stale")
    elif active_barrier is not None and active_barrier.master != candidate.owningMaster:
        reasons.append(f"atomic-barrier-held-by: {active_barrier.master.key}")
    elif (
        master is not None
        and master.document.executionNature == "atomic"
        and (active_barrier is None or active_barrier.master != candidate.owningMaster)
    ):
        reasons.append("atomic-barrier-required")
    if not candidate.admission.resourceReady:
        reasons.append(f"resource-unavailable: {candidate.admission.resourceReason}")
    if not candidate.admission.admissionReady:
        reasons.append(f"admission-blocked: {candidate.admission.admissionReason}")
    return reasons


def _incomplete_predecessors(
    graph: _GraphContext, master_ref: TaskDocumentRef
) -> list[TaskDocumentRef]:
    return list(graph.incomplete_predecessors[master_ref])


def _active_lane_owner(state: CloseoutQueueState) -> TaskDocumentRef | None:
    return next(
        (
            candidate.taskDocumentRef
            for candidate in state.candidates.values()
            if candidate.state != "declared"
        ),
        None,
    )


def _leaf_identity(
    topology: TaskDocumentTopology, contract: WorktreeContract
) -> tuple[TaskDocumentRef, Any]:
    found = resolve_terminal_leaf_doc(contract.task_root, contract.leaf_id)
    if found is None:
        raise CloseoutQueueError(
            "closeout-candidate-task-document-missing",
            f"leaf {contract.leaf_id!r} has no canonical task document",
        )
    return topology.canonical_ref(contract.repo_name, found[0]), found[1]


def _grade(
    raw: SchedulingGradeInput | None,
    graph: _GraphContext,
    candidate_ref: TaskDocumentRef,
    owning_master: TaskDocumentRef,
) -> tuple[SchedulingGrade, str, list[EvidenceFact]]:
    return canonical_grade(
        raw.model_dump(mode="json") if raw is not None else None,
        authority=graph.grade_authority,
        candidate_ref=candidate_ref,
        owning_master=owning_master,
    )


def _admission(raw: CandidateAdmissionFacts | None) -> CandidateAdmissionFacts:
    try:
        return CandidateAdmissionFacts.model_validate(raw or {})
    except ValidationError as exc:
        raise CloseoutQueueError("closeout-admission-invalid", str(exc)) from exc


def _task_ref(
    raw: TaskDocumentRef | dict[str, Any] | None,
    label: str,
) -> TaskDocumentRef:
    if raw is None:
        raise CloseoutQueueError("closeout-queue-reference-required", f"{label} is required")
    if isinstance(raw, TaskDocumentRef):
        return raw
    try:
        return TaskDocumentRef.model_validate(raw)
    except ValidationError as exc:
        raise CloseoutQueueError("closeout-queue-reference-invalid", f"{label}: {exc}") from exc


def _required_candidate_ref(request: CloseoutQueueRequest) -> TaskDocumentRef:
    return _task_ref(request.candidate_task_document_ref, "candidate_task_document_ref")


def _candidate_or_error(
    state: CloseoutQueueState, candidate_ref: TaskDocumentRef
) -> CloseoutCandidateRecord:
    candidate = state.candidates.get(candidate_ref.key)
    if candidate is None:
        raise CloseoutQueueError(
            "closeout-candidate-not-declared",
            f"candidate is not declared in the sprint queue: {candidate_ref.key}",
        )
    return candidate


def _queue_action(value: str) -> QueueAction:
    action = value.strip()
    if action not in _ACTIONS:
        raise CloseoutQueueError(
            "closeout-queue-action-invalid", f"unsupported closeout queue action: {value!r}"
        )
    return cast(QueueAction, action)


def _ready_sort_key(graph: _GraphContext, view: CloseoutQueueCandidateView) -> tuple[Any, ...]:
    grade = cast(SchedulingGrade, view.grade)
    return (
        PRIORITY_RANK[grade.priority],
        graph.node_order[view.owningMaster],
        view.taskDocumentRef.key,
    )


def _group_name(classification: str) -> str:
    return "inFlight" if classification == "in-flight" else classification


def _initial_state(
    sprint_ref: TaskDocumentRef, graph_revision: str, timestamp: str
) -> CloseoutQueueState:
    return CloseoutQueueState(
        sprintTaskDocumentRef=sprint_ref,
        revision=0,
        graphRevision=graph_revision,
        candidates={},
        activeBarrier=None,
        appliedRequests=[],
        updatedAt=timestamp,
    )


def _request_fingerprint(request: CloseoutQueueRequest, actor: QueueActor) -> str:
    payload = {
        "request": request.model_dump(mode="json", exclude_none=True),
        "actor": actor.identity,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
