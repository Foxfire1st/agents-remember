"""Graph-title preparation invariants for task-document publication batches."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import SprintGraphTitles, TaskDocument, build_graph_titles

from .task_doc_route_review import TaskDocError

type GraphPublicationDocument = tuple[TaskDocumentRef, Path, TaskDocument]


def require_single_graph_document(
    documents: Sequence[TaskDocument],
) -> TaskDocument | None:
    """Return the batch's graph document, refusing unsupported cardinality.

    Rendering currently accepts one optional graph-title context. A batch with
    more than one graph-bearing document has no defined title authority, so it
    must fail before task publication or disposable projection invalidation.
    """

    graph_documents = [document for document in documents if document.executionGraph is not None]
    if len(graph_documents) > 1:
        raise TaskDocError(
            "task-document-publication-graph-cardinality: one publication batch "
            "may contain at most one graph-bearing document; "
            f"found {len(graph_documents)}"
        )
    return graph_documents[0] if graph_documents else None


def build_publication_batch_graph_titles(
    documents: Sequence[GraphPublicationDocument],
) -> SprintGraphTitles | None:
    """Build the sole in-memory graph title context for a publication batch."""

    graph_document = require_single_graph_document(
        [document for _ref, _root, document in documents]
    )
    graph = graph_document.executionGraph if graph_document is not None else None
    if graph is None:
        return None
    masters = {ref: document for ref, _root, document in documents}
    return build_graph_titles(graph, masters)
