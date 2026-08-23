"""Mechanistic pre-closeout admission and deterministic sprint scheduling."""

from __future__ import annotations

import hashlib
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
from agents_remember.controlplane.integration_authority_lock import integration_authority_lock
from agents_remember.kernel.authority import require_within_coordination
from agents_remember.kernel.memory_ledger import find_mapping, load_ledger
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.queue.closeout_queue import (
    LANE_OCCUPYING_STATES,
    ActiveAtomicBlocker,
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
from agents_remember.worktrees.atomic_series_seal import require_series_path_accepting_leaves
from agents_remember.worktrees.modules.git import branch_commit, is_ancestor
from agents_remember.worktrees.route_review import (
    RouteReviewError,
    code_candidate_tree,
)
from agents_remember.worktrees.scheduling_mode import (
    SchedulingMode,
    resolve_scheduling_mode,
    sequential_lane_owner,
)
from agents_remember.worktrees.source_lineage import (
    lineage_refusal,
    source_lineage_for_contract,
)
from agents_remember.worktrees.task_resolver import series_contract_path
from agents_remember.worktrees.worktree_contract import (
    ContractError,
    WorktreeContract,
    load_contract,
    write_contract,
)

from .closeout_queue_blocker import _abort_blocker, _acquire_blocker, _release_blocker
from .closeout_queue_candidate_evidence import (
    commit_tree,
    ledger_mapping,
    memory_candidate_tree,
    queue_candidate_failure_evidence,
    require_source_bases_current,
    route_review_blockers,
    route_review_fact,
)
from .closeout_queue_door import (
    candidate_closeout_door_blocker,
    owned_candidate_lifecycle_operation,
)
from .closeout_queue_errors import (
    CloseoutQueueError,
    bounded_queue_failure_detail,
    queue_task_ref,
)
from .closeout_queue_evidence import (
    canonical_grade,
    curator_evidence,
    curator_evidence_blockers,
    register_section_facts,
)
from .closeout_queue_graph import (
    QueueGraphContext as _GraphContext,
)
from .closeout_queue_graph import (
    acquisition_facts,
    predecessor_waiting_reasons,
    ready_sort_key,
)
from .closeout_queue_graph import (
    graph_context as _graph_context,
)
from .closeout_queue_state import (
    initial_queue_state,
    queue_action,
    queue_request_fingerprint,
)


@dataclass(frozen=True)
class QueueActor:
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
    action = queue_action(request.action)
    sprint_ref = queue_task_ref(request.sprint_task_document_ref, "sprint_task_document_ref")
    topology = TaskDocumentTopology(config.coordination_root)
    timestamp = (now or now_iso()).strip()
    if not timestamp:
        raise CloseoutQueueError("closeout-queue-time-invalid", "timestamp must not be blank")
    if action == "status":
        # The only read action (L13-R4): never raises on an absent graph or a
        # missing/malformed register — it reports the degraded projection instead.
        return _status_readout(config, topology, sprint_ref, actor=actor, timestamp=timestamp)
    graph = _graph_context(topology, sprint_ref)
    initial = initial_queue_state(sprint_ref, graph.revision, timestamp)
    store = CloseoutQueueStore(config.coordination_root, sprint_ref)
    _authorize_status_scope(actor, graph)
    if graph.sprint.document.status == "Completed":
        raise CloseoutQueueError(
            "closeout-queue-sprint-completed",
            "completed sprint queues are reclaimed and cannot accept mutations",
        )
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
            fingerprint=queue_request_fingerprint(request, actor.identity),
            recorded_at=timestamp,
            actor=actor.identity,
            rationale=request.rationale,
        ),
        transform=apply_current_graph,
    )
    graph = _graph_context(topology, sprint_ref)
    _authorize_status_scope(actor, graph)
    return _projection(topology, graph, state, action, actor)


def _status_readout(
    config: McpRuntimeConfig,
    topology: TaskDocumentTopology,
    sprint_ref: TaskDocumentRef,
    *,
    actor: QueueActor,
    timestamp: str,
) -> dict[str, Any]:
    """Project the sprint queue for the one read action (L13-R4).

    ``status`` never raises on an absent executionGraph or missing/malformed
    canonical registers: a graph-less sprint projects the atomic-sequential
    default with its series lane owner, and a graph sprint with degraded
    registers still projects, with the register facts attached. Only mutations
    stay guarded.
    """

    try:
        sprint = topology.resolve(sprint_ref)
    except TaskDocumentRefError as exc:
        raise CloseoutQueueError(
            exc.status,
            bounded_queue_failure_detail(
                exc,
                stage="queue-status-resolution",
                side="task-document",
                name="sprint",
            ),
        ) from exc
    if sprint.document.kind != "master" or not sprint.document.orchestrates:
        raise CloseoutQueueError(
            "closeout-queue-sprint-required",
            f"closeout queue status requires an orchestration sprint: {sprint_ref.key}",
        )
    if sprint.document.executionGraph is None:
        try:
            mode = resolve_scheduling_mode(topology, sprint_ref)
        except TaskDocumentRefError as exc:
            raise CloseoutQueueError(
                exc.status,
                bounded_queue_failure_detail(
                    exc,
                    stage="queue-scheduling-mode",
                    side="task-document",
                    name="sprint",
                ),
            ) from exc
        _authorize_degraded_scope(actor, mode)
        return _degraded_projection(topology, mode, timestamp)
    graph = _graph_context(topology, sprint_ref, strict_registers=False)
    _authorize_status_scope(actor, graph)
    store = CloseoutQueueStore(config.coordination_root, sprint_ref)
    initial = initial_queue_state(sprint_ref, graph.revision, timestamp)
    # Status never rewrites state or races the task-document-owned sprint transition.
    state = store.read(initial)
    payload = _projection(topology, graph, state, "status", actor)
    registers = payload.get("registers") or {}
    degraded = {name: fact for name, fact in registers.items() if fact != "ok"}
    if degraded:
        payload["state"] = "degraded"
        payload["legalNextOperations"] = [
            "repair the register section with task_doc.set_section "
            f"(registers: {degraded!r}); the write path validates the canonical shape"
        ]
        payload["summary"] = (
            f"Sprint queue projected with degraded planning registers: {degraded!r}. "
            "Scheduling reads report facts; mutations stay guarded."
        )
    return payload


def _authorize_degraded_scope(actor: QueueActor, mode: SchedulingMode) -> None:
    sprint_roles = {"architect", "strategist", "orchestrator"}
    if actor.task_document_ref == mode.sprint.ref and actor.role in sprint_roles:
        return
    if actor.role == "manager" and any(
        actor.task_document_ref == master.ref for master in mode.masters
    ):
        return
    raise CloseoutQueueError(
        "closeout-queue-caller-refused",
        "closeout queue access requires the sprint architect/strategist/orchestrator or a commanded manager",
    )


def _degraded_projection(
    topology: TaskDocumentTopology, mode: SchedulingMode, timestamp: str
) -> dict[str, Any]:
    """The atomic-sequential default projection for a sprint without a graph."""

    owner = sequential_lane_owner(topology, mode)
    legal = [
        "work the lane-owning master to completion (worktree_closeout_apply, then "
        "worktree_integrate, then worktree_cleanup releases the lane)",
        "bootstrap a dependency graph with task_doc.author_execution_graph "
        "(first add_node batch creates it; set_nature covers every commanded master)",
    ]
    if owner is None:
        legal.insert(
            0,
            "start the next master's series (dispatch its manager or start its first "
            "leaf); masters run one at a time under the atomic-sequential default",
        )
    summary = (
        f"Sprint {mode.sprint.ref.key} has no executionGraph: atomic-sequential default; "
        f"lane owner: {owner.ref.key if owner is not None else 'none'}."
    )
    return {
        "ok": True,
        "operation": "closeout_queue",
        "action": "status",
        "state": "degraded",
        "summary": summary,
        "sprintTaskDocumentRef": mode.sprint.ref.model_dump(mode="json"),
        "revision": 0,
        "graphRevision": hashlib.sha256(
            f"closeout-queue:no-graph:{mode.sprint.ref.key}".encode()
        ).hexdigest(),
        "mode": mode.mode,
        "registers": register_section_facts(mode.sprint),
        "laneOwner": owner.ref.key if owner is not None else None,
        "legalNextOperations": legal,
        "leafPlacementFacts": [],
        "activeBlocker": None,
        "ready": [],
        "waiting": [],
        "blocked": [],
        "inFlight": [],
        "updatedAt": timestamp,
    }


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
    if context.action == "acquire-blocker":
        _require_sprint_role(actor, context.graph, "orchestrator")
        return _acquire_blocker(
            context.graph,
            current,
            context.request,
            context.timestamp,
            actor.identity,
        )
    if context.action == "release-blocker":
        _require_sprint_role(actor, context.graph, "orchestrator")
        return _release_blocker(context.graph, current, context.request, context.config)
    if context.action == "abort-blocker":
        _require_sprint_role(actor, context.graph, "orchestrator")
        return _abort_blocker(context.graph, current, context.request)
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
    with integration_authority_lock(
        context.config.coordination_root,
        context.graph.sprint.ref.repository,
    ):
        return _declare_candidate_under_authority(state, context, actor)


def _declare_candidate_under_authority(
    state: CloseoutQueueState, context: _ActionContext, actor: QueueActor
) -> CloseoutQueueState:
    request = context.request
    path, contract, leaf_ref, owning_master = _declaration_identity(context)
    if actor.role != "manager" or actor.task_document_ref != owning_master:
        raise CloseoutQueueError(
            "closeout-queue-caller-refused",
            "only the owning master manager may declare its reviewed leaf candidate",
        )
    if context.graph.masters[owning_master].document.executionNature == "atomic":
        parent_path = series_contract_path(context.graph.masters[owning_master].path.parent)
        try:
            require_series_path_accepting_leaves(
                parent_path,
                operation="closeout candidate declaration",
            )
        except RuntimeError as exc:
            raise CloseoutQueueError(
                "closeout-candidate-parent-series-sealed",
                bounded_queue_failure_detail(
                    exc,
                    stage="queue-parent-series-authority",
                    side="contract",
                    name="series-contract",
                ),
            ) from exc
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
            "closeout-candidate-contract-required",
            "declare requires contract_path: bind the leaf worktree contract "
            "(enclosures/<leaf>/series-contract.md), or use the direct landing "
            "operation for sanctioned branch-addressed execution",
        )
    path = require_within_coordination(context.config, request.contract_path, "contract_path")
    try:
        contract = load_contract(path)
    except ContractError as exc:
        raise CloseoutQueueError(
            "closeout-candidate-contract-invalid",
            bounded_queue_failure_detail(
                exc,
                stage="queue-candidate-contract",
                side="contract",
                name="leaf-contract",
            ),
        ) from exc
    if contract.kind != "leaf":
        raise CloseoutQueueError(
            "closeout-candidate-leaf-required",
            "closeout candidate declaration requires the leaf worktree contract; "
            f"{path} is a {contract.kind} contract -- re-stamp the leaf enclosure "
            "(enclosures/<leaf>/series-contract.md), or use the direct landing "
            "operation for branch-addressed execution",
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


def _projection(
    topology: TaskDocumentTopology,
    graph: _GraphContext,
    state: CloseoutQueueState,
    action: QueueAction,
    actor: QueueActor,
) -> dict[str, Any]:
    groups = _project_candidates(topology, graph, state, actor)
    total = sum(len(items) for items in groups.values())
    lane_owner = _active_lane_owner(state)
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
        "mode": "dag",
        "registers": register_section_facts(graph.sprint),
        "laneOwner": lane_owner.key if lane_owner is not None else None,
        "acquisitionFacts": (
            acquisition_facts(graph, state) if action == "acquire-blocker" else None
        ),
        "leafPlacementFacts": list(graph.leaf_facts),
        "activeBlocker": state.activeBlocker.model_dump(mode="json")
        if state.activeBlocker
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
        blocker_evidence: list[dict[str, object]] = []
        blockers = _candidate_blockers(
            topology,
            graph,
            candidate,
            failure_evidence=blocker_evidence,
        )
        if candidate.state != "declared":
            blockers.extend(_waiting_reasons(graph, candidate, lane_owner, state.activeBlocker))
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
            reasons = _waiting_reasons(graph, candidate, lane_owner, state.activeBlocker)
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
            blockerEvidence=blocker_evidence,
            legalNextOperations=legal,
            grade=candidate.grade,
        )
        groups[_group_name(classification)].append(view)
    groups["ready"].sort(key=lambda view: ready_sort_key(graph, view))
    for name in ("waiting", "blocked", "inFlight"):
        groups[name].sort(key=lambda item: item.taskDocumentRef.key)
    return groups


def _group_name(classification: str) -> str:
    return "inFlight" if classification == "in-flight" else classification


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
    record = owned_candidate_lifecycle_operation(candidate)
    if record is None or record.status == "completed":
        return []
    if record.status in {"failed", "cancelled"}:
        return [] if record.irreversibleBoundaryEntered else [observe]
    return [observe]


def _integration_actor(
    graph: _GraphContext, candidate: CloseoutCandidateRecord, actor: QueueActor
) -> bool:
    return (
        candidate.owningMaster in graph.masters
        and actor.role == "manager"
        and actor.task_document_ref == candidate.owningMaster
    )


def _candidate_blockers(
    topology: TaskDocumentTopology,
    graph: _GraphContext,
    candidate: CloseoutCandidateRecord,
    *,
    failure_evidence: list[dict[str, object]] | None = None,
) -> list[str]:
    blockers: list[str] = []
    door_blocker = candidate_closeout_door_blocker(candidate)
    if door_blocker is not None:
        blockers.append(door_blocker)
    if candidate.graphRevision != graph.revision:
        blockers.append("graph-revision-stale")
    if candidate.owningMaster not in graph.masters:
        blockers.append("owning-master-no-longer-commanded")
    if candidate.state in {"closeout-in-flight", "integration-in-flight"}:
        operation = owned_candidate_lifecycle_operation(candidate)
        if operation is None:
            blockers.append("lifecycle-operation-owner-unavailable")
        elif operation.status in {"completed", "failed", "cancelled"}:
            blockers.append("lifecycle-operation-owner-terminal")
    try:
        blockers.extend(_live_candidate_blockers(topology, graph, candidate))
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
        blockers.append("candidate-revalidation-failed")
        if failure_evidence is not None:
            failure_evidence.append(queue_candidate_failure_evidence(exc))
    return list(dict.fromkeys(blockers))


def _live_candidate_blockers(
    topology: TaskDocumentTopology,
    graph: _GraphContext,
    candidate: CloseoutCandidateRecord,
) -> list[str]:
    contract = load_contract(Path(candidate.contractPath))
    if candidate.state not in {"certified", "integration-in-flight"} and not (
        candidate.state == "closeout-in-flight" and contract.closeout_status == "completed"
    ):
        return _pre_closeout_blockers(topology, graph, candidate, contract)
    refreshed_memory_evidence = None
    if candidate.state == "closeout-in-flight" and candidate.memoryMode == "external":
        with suppress(CloseoutQueueError):
            refreshed_memory_evidence = curator_evidence(contract)
    return _post_closeout_blockers(
        topology,
        graph,
        candidate,
        contract,
        expected_memory_evidence=refreshed_memory_evidence,
    )


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
    # External-memory closeout refreshes verification metadata after candidate selection.
    # The lifecycle operation and ledger mapping below bind that deterministic result;
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
    """Stale-base blockers; each names its recovery (L13-R2: sync first, then retry)."""

    blockers: list[str] = []
    lineage = lineage_refusal(source_lineage_for_contract(contract))
    if lineage is not None:
        blockers.append(lineage[0])
    if (
        branch_commit(contract.code_repo_path, contract.code_source_branch)
        != candidate.codeBaseCommit
    ):
        blockers.append("code-source-moved: run worktree_sync, then retry")
    if contract.memory_mode != "external":
        return blockers
    if contract.memory_repo_path is None or candidate.memoryBaseCommit is None:
        blockers.append("memory-source-missing")
        return blockers
    if (
        branch_commit(contract.memory_repo_path, contract.memory_source_branch)
        != candidate.memoryBaseCommit
    ):
        blockers.append("memory-source-moved: run worktree_sync, then retry")
    if ledger_mapping(contract) != candidate.ledgerMemoryCommit:
        blockers.append("ledger-base-mapping-changed: run worktree_sync, then retry")
    return blockers


def _grade_blockers(graph: _GraphContext, candidate: CloseoutCandidateRecord) -> list[str]:
    if candidate.grade is None:
        return []
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
    active_blocker: ActiveAtomicBlocker | None,
) -> list[str]:
    reasons = predecessor_waiting_reasons(graph, candidate.owningMaster, candidate.taskDocumentRef)
    if candidate.grade is None:
        reasons.append("explicit-grade-required")
    if lane_owner is not None and lane_owner != candidate.taskDocumentRef:
        reasons.append(f"integration-lane-owned-by: {lane_owner.key}")
    master = graph.masters.get(candidate.owningMaster)
    if active_blocker is not None and active_blocker.graphRevision != graph.revision:
        reasons.append("atomic-blocker-graph-revision-stale")
    elif active_blocker is not None and active_blocker.master != candidate.owningMaster:
        reasons.append(_blocker_held_reason(active_blocker, lane_owner))
    elif (
        master is not None
        and master.document.executionNature == "atomic"
        and (active_blocker is None or active_blocker.master != candidate.owningMaster)
    ):
        reasons.append("atomic-blocker-required")
    if not candidate.admission.resourceReady:
        reasons.append(f"resource-unavailable: {candidate.admission.resourceReason}")
    if not candidate.admission.admissionReady:
        reasons.append(f"admission-blocked: {candidate.admission.admissionReason}")
    return reasons


def _blocker_held_reason(
    active_blocker: ActiveAtomicBlocker, lane_owner: TaskDocumentRef | None
) -> str:
    """The blocker-held fact plus the identity of the lane candidate it owns (L13-R3)."""

    reason = f"atomic-blocker-held-by: {active_blocker.master.key}"
    if lane_owner is not None and _candidate_master_ref(lane_owner) == active_blocker.master:
        reason = f"{reason} (owner candidate: {lane_owner.key})"
    return reason


def _candidate_master_ref(candidate_ref: TaskDocumentRef) -> TaskDocumentRef:
    """The canonical master ref of a leaf candidate ref (the owning directory)."""

    return TaskDocumentRef(
        repository=candidate_ref.repository,
        path=f"{Path(candidate_ref.path).parent.as_posix()}/task.json",
    )


def _active_lane_owner(state: CloseoutQueueState) -> TaskDocumentRef | None:
    return next(
        (
            candidate.taskDocumentRef
            for candidate in state.candidates.values()
            if candidate.state in LANE_OCCUPYING_STATES
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
        raise CloseoutQueueError(
            "closeout-admission-invalid",
            bounded_queue_failure_detail(
                exc,
                stage="queue-admission-validation",
                side="request",
                name="candidate-admission",
            ),
        ) from exc


def _required_candidate_ref(request: CloseoutQueueRequest) -> TaskDocumentRef:
    return queue_task_ref(request.candidate_task_document_ref, "candidate_task_document_ref")


def _candidate_or_error(
    state: CloseoutQueueState, candidate_ref: TaskDocumentRef
) -> CloseoutCandidateRecord:
    candidate = state.candidates.get(candidate_ref.key)
    if candidate is None:
        raise CloseoutQueueError("closeout-candidate-not-declared", candidate_ref.key)
    return candidate
