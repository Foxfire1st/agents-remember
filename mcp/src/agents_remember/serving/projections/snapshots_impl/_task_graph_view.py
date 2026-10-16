"""Graph presentation reads each authored document as the authority for its own stage."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.observer.projection_graph import (
    GraphNodeLike,
    GraphPredecessorFacts,
    MasterGraphFacts,
    TaskExecutionGraphView,
    build_execution_graph_view,
)
from agents_remember.observer.task_entries import relative_task_reference
from agents_remember.tasks import SprintExecutionGraph, TaskDocument, build_graph_titles
from agents_remember.tasks.document import SubTaskRef


def declared_task_reference(parent: TaskDocumentRef, row: SubTaskRef) -> TaskDocumentRef | None:
    return (
        row.masterRef
        or (row.retirement.masterRef if row.retirement is not None else None)
        or relative_task_reference(parent, row.file)
    )


def leaf_statuses(
    parent: TaskDocumentRef,
    doc: TaskDocument,
    documents: Mapping[TaskDocumentRef, TaskDocument],
) -> dict[str, str]:
    statuses = {}
    for row in doc.subTasks:
        reference = declared_task_reference(parent, row)
        child = documents.get(reference) if reference is not None else None
        statuses[row.number] = child.status if child is not None else row.status
    return statuses


def execution_graph_view(
    graph: SprintExecutionGraph,
    master_docs: Mapping[TaskDocumentRef, TaskDocument],
) -> TaskExecutionGraphView:
    """Walk one persisted graph and build its render-ready per-node view.

    The observer's graph-view builder is primitives-only (layering: the
    observer package must not import tasks), so this serving-layer seam does
    the tasks-domain work -- derived waves, resolved edge endpoints, joined
    titles, and per-master facts -- and feeds the builder plain data.
    """

    titles = build_graph_titles(graph, master_docs)
    facts = {
        ref: MasterGraphFacts(
            status=doc.status,
            executionNature=doc.executionNature,
            leaf_statuses=leaf_statuses(ref, doc, master_docs),
        )
        for ref, doc in master_docs.items()
    }
    predecessor_edges: dict[GraphNodeLike, list[GraphPredecessorFacts]] = {
        node: [] for node in graph.nodes
    }
    for edge in graph.edges:
        predecessor = graph.resolve_endpoint(edge.predecessor)
        successor = graph.resolve_endpoint(edge.successor)
        predecessor_edges[successor].append(
            GraphPredecessorFacts(
                predecessor=predecessor,
                reason=edge.reason,
                judgmentId=edge.judgmentId,
            )
        )
    return build_execution_graph_view(
        graph.nodes,
        graph.derived_waves(),
        predecessor_edges,
        facts,
        titles,
    )


def documents_by_ref(
    docs: list[tuple[Path, TaskDocument]],
) -> dict[TaskDocumentRef, TaskDocument]:
    """Index every valid task document by its task-document reference.

    The sprint graph view joins titles, natures and stages from the documents
    its persisted references name, including the leaves of segmented masters.
    """

    documents: dict[TaskDocumentRef, TaskDocument] = {}
    for path, doc in docs:
        repository = doc.repo
        # Task payloads are always depth-3 under the tasks root (``_iter_task_json``), so the
        # repo-relative path is ``parents[1]``-relative by construction; no fallback needed.
        relative = path.relative_to(path.parents[1]).as_posix()
        ref = TaskDocumentRef(repository=repository, path=relative)
        documents[ref] = doc
    return documents
