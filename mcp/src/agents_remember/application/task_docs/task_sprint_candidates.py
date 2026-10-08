"""Pure sprint detachment candidates, shared by detach and explicit retirement."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agents_remember.errors import AgentsRememberError
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import SprintExecutionEndpoint, SprintExecutionGraph, TaskDocument
from agents_remember.tasks.document_refs import ResolvedTaskDocument


class SprintLinkageError(AgentsRememberError):
    """A sprint linkage edit is structurally invalid or unverifiable."""


def _detach_candidate(
    sprint: TaskDocument, master_ref: TaskDocumentRef, master: ResolvedTaskDocument | None
) -> tuple[TaskDocument, list[str], int]:
    data, removed_entries, removed_nodes = _detached_data(sprint, master_ref, master)
    # Valid at read; the batch only removes pieces, so re-validation cannot fail.
    return TaskDocument.model_validate(data), removed_entries, removed_nodes


def _detached_data(
    sprint: TaskDocument, master_ref: TaskDocumentRef, master: ResolvedTaskDocument | None
) -> tuple[dict[str, Any], list[str], int]:
    """The sprint's fields without the master: its membership entry, its typed row, its node.

    Not validated here. A caller that puts something in the master's place, as a retirement
    does with its row, validates the document it ends with: a sprint that gives up its last
    master is a sprint only together with the row that records why.
    """

    graph = sprint.executionGraph
    removed_nodes = 0
    if graph is not None:
        _require_no_touching_edges(graph, master_ref)
        remaining = [node for node in graph.nodes if node.ref != master_ref]
        removed_nodes = len(graph.nodes) - len(remaining)
        if not remaining:
            raise SprintLinkageError(
                "task-sprint-linkage-graph-empty: detaching the last master would empty the "
                "executionGraph; the graph has no retire operation"
            )
        # The graph was valid at read; dropping whole nodes cannot invalidate it
        # (touching edges were refused above), so construction cannot fail.
        data_graph: Any = SprintExecutionGraph(nodes=remaining, edges=list(graph.edges)).model_dump(
            mode="json"
        )
    names = {Path(master_ref.path).parent.name}
    if master is not None:
        names |= {master.document.id, master.document.title}
    removed_entries = [entry for entry in sprint.orchestrates if entry in names]
    kept_rows = [
        row.model_dump(mode="json", by_alias=True, exclude_none=True)
        for row in sprint.subTasks
        if row.masterRef != master_ref
    ]
    data = sprint.model_dump(by_alias=True)
    data["subTasks"] = kept_rows
    data["orchestrates"] = [entry for entry in sprint.orchestrates if entry not in names]
    if graph is not None:
        data["executionGraph"] = data_graph
    return data, removed_entries, removed_nodes


def _require_no_touching_edges(graph: SprintExecutionGraph, master_ref: TaskDocumentRef) -> None:
    def touches(endpoint: TaskDocumentRef | SprintExecutionEndpoint) -> bool:
        ref = endpoint.ref if isinstance(endpoint, SprintExecutionEndpoint) else endpoint
        return ref == master_ref

    touching = [
        edge for edge in graph.edges if touches(edge.predecessor) or touches(edge.successor)
    ]
    if touching:
        raise SprintLinkageError(
            f"task-sprint-linkage-node-in-use: {len(touching)} edge(s) still touch "
            f"{master_ref.key}; remove them with task_doc.author_execution_graph first"
        )
