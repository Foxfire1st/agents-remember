"""Bounded sprint-graph construction for the closeout queue."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from agents_remember.models.queue.closeout_queue import (
    MAX_CLOSEOUT_CANDIDATES,
    MAX_CLOSEOUT_GRAPH_EDGES,
    MAX_CLOSEOUT_MASTERS,
    CloseoutQueueCandidateView,
    CloseoutQueueState,
    SchedulingGrade,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import (
    SprintExecutionGraph,
    SprintExecutionNode,
    derived_leaf_placement,
    leaf_placement_facts,
)
from agents_remember.tasks.document_refs import (
    ResolvedTaskDocument,
    TaskDocumentRefError,
    TaskDocumentTopology,
)

from .closeout_queue_errors import CloseoutQueueError
from .closeout_queue_evidence import PRIORITY_RANK, GradeAuthority, planning_authorities


def acquisition_facts(graph: QueueGraphContext, state: CloseoutQueueState) -> dict[str, Any]:
    """In-flight organizational leafs observed at blocker acquisition (L13-R3).

    Facts only — they inform the strategist/orchestrator start-anyway judgment; the
    mechanism never decides from them. In-flight means the candidate holds or held
    the landing lane past plain declaration.
    """

    in_flight = sorted(
        (
            {
                "candidate": candidate.taskDocumentRef.key,
                "owningMaster": candidate.owningMaster.key,
                "state": candidate.state,
            }
            for candidate in state.candidates.values()
            if candidate.state != "declared"
            and (master := graph.masters.get(candidate.owningMaster)) is not None
            and master.document.executionNature == "organizational"
        ),
        key=lambda row: row["candidate"],
    )
    return {"inFlightOrganizationalLeafs": in_flight}


@dataclass(frozen=True)
class QueueGraphContext:
    """One bounded, immutable scheduling projection of the sprint topology."""

    sprint: ResolvedTaskDocument
    graph: SprintExecutionGraph
    masters: dict[TaskDocumentRef, ResolvedTaskDocument]
    revision: str
    node_order: dict[SprintExecutionNode, int]
    nodes_by_master: dict[TaskDocumentRef, tuple[SprintExecutionNode, ...]]
    # Leaf document ref -> authored (or derived, L11-R2) segment node; lump masters
    # resolve through ``nodes_by_master`` instead.
    leaf_nodes: dict[TaskDocumentRef, SprintExecutionNode]
    leaf_facts: tuple[dict[str, Any], ...]
    incomplete_predecessors: dict[SprintExecutionNode, tuple[SprintExecutionNode, ...]]
    grade_authority: GradeAuthority


def graph_context(
    topology: TaskDocumentTopology,
    sprint_ref: TaskDocumentRef,
    *,
    strict_registers: bool = True,
) -> QueueGraphContext:
    """Resolve, validate, cap, and index one sprint execution graph.

    ``strict_registers`` guards mutations: a malformed canonical planning register
    refuses with the repair named. Read paths (L13-R4) pass ``False`` so a malformed
    register degrades the projection instead of failing it.
    """

    try:
        sprint = topology.resolve(sprint_ref)
    except TaskDocumentRefError as exc:
        raise CloseoutQueueError(exc.status, str(exc)) from exc
    graph = sprint.document.executionGraph
    if graph is None:
        raise CloseoutQueueError(
            "task-execution-topology-migration-required",
            "sprint has no executionGraph; the sprint runs atomic-sequentially by default "
            "(masters land one at a time through the series lane), or bootstrap a graph "
            "with task_doc.author_execution_graph",
        )
    if len(graph.nodes) > MAX_CLOSEOUT_MASTERS:
        raise CloseoutQueueError(
            "closeout-queue-master-capacity-exceeded",
            f"sprint has more than {MAX_CLOSEOUT_MASTERS} graph masters; split it before queue admission",
        )
    if len(graph.edges) > MAX_CLOSEOUT_GRAPH_EDGES:
        raise CloseoutQueueError(
            "closeout-queue-edge-capacity-exceeded",
            f"sprint has more than {MAX_CLOSEOUT_GRAPH_EDGES} dependency edges; split it before queue admission",
        )
    try:
        masters = topology.validate_execution_topology(sprint_ref)
    except TaskDocumentRefError as exc:
        raise CloseoutQueueError(exc.status, str(exc)) from exc
    master_map = {master.ref: master for master in masters}
    if (
        sum(len(master_map[ref].document.subTasks) for ref in graph.master_refs())
        > MAX_CLOSEOUT_CANDIDATES
    ):
        raise CloseoutQueueError(
            "closeout-queue-capacity-exceeded",
            f"sprint has more than {MAX_CLOSEOUT_CANDIDATES} leaf candidates; split it before queue admission",
        )
    payload = {
        "executionGraph": graph.model_dump(mode="json"),
        "executionNatures": [
            {
                "taskDocumentRef": ref.model_dump(mode="json"),
                "executionNature": master_map[ref].document.executionNature,
            }
            for ref in graph.master_refs()
        ],
    }
    revision = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    completed = {ref for ref, master in master_map.items() if master.document.status == "Completed"}
    leaf_nodes, leaf_facts = _leaf_node_index(graph, master_map, completed)
    try:
        judgments, priorities = planning_authorities(sprint, strict=strict_registers)
    except CloseoutQueueError as exc:
        raise CloseoutQueueError(
            exc.status,
            f"{exc}; recovery: repair the sprint's canonical register section with "
            "task_doc.set_section (the register shape is validated at write time)",
        ) from exc
    return QueueGraphContext(
        sprint,
        graph,
        master_map,
        revision,
        {node: index for index, node in enumerate(graph.nodes)},
        _nodes_by_master(graph),
        leaf_nodes,
        leaf_facts,
        incomplete_predecessor_map(graph, completed=completed),
        GradeAuthority(sprint, judgments, priorities),
    )


def _nodes_by_master(
    graph: SprintExecutionGraph,
) -> dict[TaskDocumentRef, tuple[SprintExecutionNode, ...]]:
    grouped: dict[TaskDocumentRef, list[SprintExecutionNode]] = {}
    for node in graph.nodes:
        grouped.setdefault(node.ref, []).append(node)
    return {ref: tuple(nodes) for ref, nodes in grouped.items()}


def _leaf_node_index(
    graph: SprintExecutionGraph,
    masters: dict[TaskDocumentRef, ResolvedTaskDocument],
    completed: set[TaskDocumentRef],
) -> tuple[dict[TaskDocumentRef, SprintExecutionNode], tuple[dict[str, Any], ...]]:
    """Fold authored and derived (L11-R2) leaf placements into one leaf->node index."""

    leaf_nodes: dict[TaskDocumentRef, SprintExecutionNode] = {}
    facts: list[dict[str, Any]] = []
    for master in masters.values():
        placement = derived_leaf_placement(
            graph,
            master.ref,
            [row.number for row in master.document.subTasks],
            completed,
        )
        targets = {**placement.placed, **placement.derived}
        master_dir = Path(master.ref.path).parent
        for row in master.document.subTasks:
            node = targets.get(row.number)
            if node is None or not row.file:
                continue
            leaf_ref = TaskDocumentRef(
                repository=master.ref.repository,
                path=f"{master_dir}/{Path(row.file).stem}.json",
            )
            leaf_nodes[leaf_ref] = node
        facts.extend(leaf_placement_facts(master.ref.key, placement))
    return leaf_nodes, tuple(facts)


def candidate_node(
    graph: QueueGraphContext, owning_master: TaskDocumentRef, leaf_ref: TaskDocumentRef
) -> SprintExecutionNode | None:
    """The graph node scheduling one candidate: the lump, or its leaf's segment."""
    nodes = graph.nodes_by_master.get(owning_master, ())
    if len(nodes) == 1:
        return nodes[0]
    return graph.leaf_nodes.get(leaf_ref)


def candidate_predecessors(
    graph: QueueGraphContext, owning_master: TaskDocumentRef, leaf_ref: TaskDocumentRef
) -> list[SprintExecutionNode]:
    """Incomplete predecessors of the candidate's own node (L11-R3).

    An edge into a segment blocks exactly that segment's leafs. A candidate whose leaf
    cannot be mapped falls back to the union of its master's nodes -- conservative:
    it may block more, never less.
    """

    node = candidate_node(graph, owning_master, leaf_ref)
    if node is not None:
        return list(graph.incomplete_predecessors[node])
    return list(master_incomplete_predecessors(graph, owning_master))


def predecessor_label(node: SprintExecutionNode) -> str:
    """One predecessor node as a waiting-reason label; segments carry their leaf list."""
    if node.kind == "segment":
        return f"{node.ref.key} (leafs: {', '.join(node.leafIds)})"
    return node.ref.key


def predecessor_waiting_reasons(
    graph: QueueGraphContext, owning_master: TaskDocumentRef, leaf_ref: TaskDocumentRef
) -> list[str]:
    """The candidate's ``predecessor-incomplete:`` waiting reasons, leaf-aware (L11-R3)."""
    return [
        f"predecessor-incomplete: {predecessor_label(node)}"
        for node in candidate_predecessors(graph, owning_master, leaf_ref)
    ]


def ready_sort_key(graph: QueueGraphContext, view: CloseoutQueueCandidateView) -> tuple[Any, ...]:
    """Priority rank, then the candidate node's declaration order, then leaf identity."""
    grade = cast(SchedulingGrade, view.grade)
    node = candidate_node(graph, view.owningMaster, view.taskDocumentRef)
    order = (
        graph.node_order[node]
        if node is not None
        else min(
            (graph.node_order[owned] for owned in graph.nodes_by_master.get(view.owningMaster, ())),
            default=-1,
        )
    )
    return (
        PRIORITY_RANK[grade.priority],
        order,
        view.taskDocumentRef.key,
    )


def master_incomplete_predecessors(
    graph: QueueGraphContext, master_ref: TaskDocumentRef
) -> tuple[SprintExecutionNode, ...]:
    """Union of every incomplete predecessor across all of the master's nodes."""
    return tuple(
        dict.fromkeys(
            predecessor
            for node in graph.nodes_by_master.get(master_ref, ())
            for predecessor in graph.incomplete_predecessors[node]
        )
    )


def incomplete_predecessor_map(
    graph: SprintExecutionGraph,
    *,
    completed: set[TaskDocumentRef],
) -> dict[SprintExecutionNode, tuple[SprintExecutionNode, ...]]:
    """Build every node's predecessor set in one bounded O(V+E) pass.

    Completion is master-granular: a node counts complete when its master document is
    Completed. An edge into a segment therefore blocks exactly that segment's leafs
    until the predecessor's master completes (L11-R3).
    """

    incomplete: dict[SprintExecutionNode, list[SprintExecutionNode]] = {
        node: [] for node in graph.nodes
    }
    successors: dict[SprintExecutionNode, list[SprintExecutionNode]] = {
        node: [] for node in graph.nodes
    }
    for edge in graph.edges:
        predecessor = graph.resolve_endpoint(edge.predecessor)
        successors[predecessor].append(graph.resolve_endpoint(edge.successor))
    for predecessor in graph.nodes:
        if predecessor.ref in completed:
            continue
        for successor in successors[predecessor]:
            incomplete[successor].append(predecessor)
    return {node: tuple(predecessors) for node, predecessors in incomplete.items()}
