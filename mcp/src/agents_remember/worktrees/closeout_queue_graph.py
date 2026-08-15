"""Bounded sprint-graph construction for the closeout queue."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from agents_remember.models.closeout_queue import (
    MAX_CLOSEOUT_CANDIDATES,
    MAX_CLOSEOUT_GRAPH_EDGES,
    MAX_CLOSEOUT_MASTERS,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import SprintExecutionGraph
from agents_remember.tasks.document_refs import (
    ResolvedTaskDocument,
    TaskDocumentRefError,
    TaskDocumentTopology,
)

from .closeout_queue_errors import CloseoutQueueError
from .closeout_queue_evidence import GradeAuthority, planning_authorities


@dataclass(frozen=True)
class QueueGraphContext:
    """One bounded, immutable scheduling projection of the sprint topology."""

    sprint: ResolvedTaskDocument
    graph: SprintExecutionGraph
    masters: dict[TaskDocumentRef, ResolvedTaskDocument]
    revision: str
    node_order: dict[TaskDocumentRef, int]
    incomplete_predecessors: dict[TaskDocumentRef, tuple[TaskDocumentRef, ...]]
    grade_authority: GradeAuthority


def graph_context(topology: TaskDocumentTopology, sprint_ref: TaskDocumentRef) -> QueueGraphContext:
    """Resolve, validate, cap, and index one sprint execution graph."""

    try:
        sprint = topology.resolve(sprint_ref)
    except TaskDocumentRefError as exc:
        raise CloseoutQueueError(exc.status, str(exc)) from exc
    graph = sprint.document.executionGraph
    if graph is None:
        raise CloseoutQueueError(
            "task-execution-topology-migration-required", "sprint has no executionGraph"
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
    if sum(len(master_map[ref].document.subTasks) for ref in graph.nodes) > MAX_CLOSEOUT_CANDIDATES:
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
            for ref in graph.nodes
        ],
    }
    revision = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    node_order = {ref: index for index, ref in enumerate(graph.nodes)}
    incomplete_predecessors = incomplete_predecessor_map(
        graph,
        completed={
            ref for ref, master in master_map.items() if master.document.status == "Completed"
        },
    )
    judgments, priorities = planning_authorities(sprint)
    return QueueGraphContext(
        sprint,
        graph,
        master_map,
        revision,
        node_order,
        incomplete_predecessors,
        GradeAuthority(sprint, judgments, priorities),
    )


def incomplete_predecessor_map(
    graph: SprintExecutionGraph,
    *,
    completed: set[TaskDocumentRef],
) -> dict[TaskDocumentRef, tuple[TaskDocumentRef, ...]]:
    """Build every predecessor set in one bounded O(V+E) pass."""

    incomplete: dict[TaskDocumentRef, list[TaskDocumentRef]] = {ref: [] for ref in graph.nodes}
    successors: dict[TaskDocumentRef, list[TaskDocumentRef]] = {ref: [] for ref in graph.nodes}
    for edge in graph.edges:
        successors[edge.predecessor].append(edge.successor)
    for predecessor in graph.nodes:
        if predecessor in completed:
            continue
        for successor in successors[predecessor]:
            incomplete[successor].append(predecessor)
    return {ref: tuple(predecessors) for ref, predecessors in incomplete.items()}
