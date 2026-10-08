"""The sprint edit of a retirement: one validated candidate that no longer commands the master.

Pure candidate construction. It removes the master's membership entry, its graph node and every
edge touching it, and leaves one plain row that records the retirement. That row takes the place
of the master's typed row, or of the one legacy seat row that correlates with the master; a
sprint that holds no row for the master gains the row at the end of its list.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.models.task_retirement import MasterRetirementProof, RetirementEdgeSelection
from agents_remember.tasks import (
    SprintExecutionEdge,
    SprintExecutionEndpoint,
    SubTaskRef,
    TaskDocSourceSnapshot,
    TaskDocument,
)
from agents_remember.tasks.document_refs import ResolvedTaskDocument, TaskDocumentTopology
from agents_remember.tasks.sprint_rows import master_rows

from .task_execution_graph_mutations import remove_retired_master_edges
from .task_execution_topology import ExecutionTopologyError, verify_sprint_judgment_ids
from .task_retirement_shared import RetireMasterPayload, digest, optional_digest
from .task_sprint_candidates import SprintLinkageError, _detached_data
from .task_sprint_context import _validate_candidate


@dataclass(frozen=True)
class SprintEdit:
    """The sprint as it reads once the master is retired, and the row that records it."""

    candidate: TaskDocument
    row: SubTaskRef


def new_proof(
    master_ref: TaskDocumentRef,
    archive_ref: TaskDocumentRef,
    payload: RetireMasterPayload,
    master_source: TaskDocSourceSnapshot,
    readiness_facts: Sequence[str],
) -> MasterRetirementProof:
    """The record of a retirement that edits no sprint; the sprint edit fills in what it removed."""

    assert master_source.json_bytes is not None
    return MasterRetirementProof(
        masterRef=master_ref,
        archiveRef=archive_ref,
        reason=payload.reason,
        retiredAt=datetime.now(UTC).isoformat(timespec="seconds"),
        removedOrchestrates=[],
        removedGraphNodes=0,
        removedEdges=[],
        affirmedEdges=payload.removeEdges,
        masterJsonSha256=digest(master_source.json_bytes),
        masterMarkdownSha256=optional_digest(master_source.markdown_bytes),
        readinessFacts=list(readiness_facts),
    )


def retirement_sprint_edit(
    topology: TaskDocumentTopology,
    sprint: ResolvedTaskDocument,
    master: ResolvedTaskDocument,
    payload: RetireMasterPayload,
    proof: MasterRetirementProof,
) -> SprintEdit:
    """Validate the sprint as it is, remove the master from it, and validate the result."""

    master_ref = master.ref
    _validate_candidate(topology, sprint.ref, {sprint.ref: sprint.document})
    _require_graph_keeps_a_node(sprint.ref, sprint.document, master_ref)
    graph_sprint, removed_edges = _remove_edges(topology, sprint, payload)
    rows = master_rows(sprint.document, sprint.path.parent, master_ref)
    standing = rows.row
    data, removed_entries, removed_nodes = _detached_data(graph_sprint, master_ref, master)
    proof = proof.model_copy(
        update={
            "removedOrchestrates": removed_entries,
            "removedGraphNodes": removed_nodes,
            "removedEdges": removed_edges,
        }
    )
    legacy = standing if standing is not None and standing.masterRef is None else None
    row = SubTaskRef(
        number=standing.number if standing is not None else _free_row_number(sprint, master),
        name=standing.name if standing is not None else master.document.title,
        # The seat document of a legacy seat row stays reachable from the row that replaces it.
        file=legacy.file if legacy is not None else "",
        status="abandoned",
        scope=standing.scope if standing is not None else "",
        retirement=proof,
    )
    retained = row.model_dump(mode="json", exclude_none=True)
    if standing is None:
        data["subTasks"].append(retained)
    else:
        # The retained plain row occupies the original place of the row it replaces: the typed
        # row is already gone from the candidate, the legacy seat row is still in it.
        index = sprint.document.subTasks.index(standing)
        if legacy is not None:
            data["subTasks"][index] = retained
        else:
            data["subTasks"].insert(index, retained)
    candidate = TaskDocument.model_validate(data)
    _validate_candidate(topology, sprint.ref, {sprint.ref: candidate})
    return SprintEdit(candidate, row)


def _free_row_number(sprint: ResolvedTaskDocument, master: ResolvedTaskDocument) -> str:
    """A row number no row of the sprint uses: the master's id, its folder name, or a suffixed id."""

    taken = {row.number for row in sprint.document.subTasks}
    for number in (master.document.id, master.path.parent.name):
        if number not in taken:
            return number
    suffix = 2
    while f"{master.document.id}-{suffix}" in taken:
        suffix += 1
    return f"{master.document.id}-{suffix}"


def _require_graph_keeps_a_node(
    sprint_ref: TaskDocumentRef, sprint: TaskDocument, master_ref: TaskDocumentRef
) -> None:
    """A sprint's execution graph cannot be empty, so its only graphed master cannot be removed."""

    graph = sprint.executionGraph
    if graph is not None and all(node.ref == master_ref for node in graph.nodes):
        raise SprintLinkageError(
            f"master {master_ref.key} is the only node of sprint {sprint_ref.key}'s executionGraph, and a graph cannot be empty, so it cannot be retired from this sprint; attach another master to the sprint with task_doc.attach_master first, or keep this master in place"
        )


def _remove_edges(
    topology: TaskDocumentTopology,
    sprint: ResolvedTaskDocument,
    payload: RetireMasterPayload,
) -> tuple[TaskDocument, list[SprintExecutionEdge]]:
    graph = sprint.document.executionGraph
    if graph is None:
        if payload.removeEdges:
            raise SprintLinkageError(
                f"master {payload.masterRef.key} has no graph edges; removeEdges must name existing outgoing edges"
            )
        return sprint.document, []
    graph, touching = remove_retired_master_edges(graph, payload.masterRef)
    outgoing = [edge for edge in touching if _endpoint_ref(edge.predecessor) == payload.masterRef]
    outgoing_keys = selection_keys(outgoing)
    selected = selection_keys(payload.removeEdges)
    if selected - outgoing_keys or len(selected) != len(payload.removeEdges):
        raise SprintLinkageError(
            f"master {payload.masterRef.key} removeEdges must name unique existing outgoing edges; inspect its sprint executionGraph and repeat task_doc.retire_master"
        )
    unfinished = [
        edge
        for edge in outgoing
        if topology.resolve(_endpoint_ref(edge.successor)).document.status != "Completed"
        and _edge_key(edge) not in selected
    ]
    if unfinished:
        raise SprintLinkageError(
            f"master {payload.masterRef.key} has unfinished successor edges: {[edge.model_dump(mode='json') for edge in unfinished]!r}; affirm each exact predecessor/successor pair in fields.removeEdges for task_doc.retire_master"
        )
    try:
        verify_sprint_judgment_ids(
            topology,
            sprint.ref,
            [("remove_edge", edge.judgmentId) for edge in touching if edge.judgmentId is not None],
        )
    except ExecutionTopologyError as exc:
        raise SprintLinkageError(str(exc)) from exc
    return sprint.document.model_copy(update={"executionGraph": graph}), touching


def _endpoint_ref(endpoint: TaskDocumentRef | SprintExecutionEndpoint) -> TaskDocumentRef:
    return endpoint.ref if isinstance(endpoint, SprintExecutionEndpoint) else endpoint


def _edge_key(edge: SprintExecutionEdge | RetirementEdgeSelection) -> str:
    return RetirementEdgeSelection(
        predecessor=edge.predecessor, successor=edge.successor
    ).model_dump_json()


def selection_keys(
    edges: Sequence[SprintExecutionEdge] | Sequence[RetirementEdgeSelection],
) -> set[str]:
    return {_edge_key(edge) for edge in edges}
