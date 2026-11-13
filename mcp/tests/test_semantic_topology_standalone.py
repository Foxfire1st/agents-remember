"""The sprint-less branch of semantic-topology/v2, decided once in tasks.semantic_topology."""

from __future__ import annotations

from agents_remember.tasks.document_refs import ResolvedTaskDocument
from agents_remember.tasks.semantic_topology import semantic_topology_fingerprint
from test_closeout_projection_member_helpers import (
    _bound_sprint,
    _documents,
    _semantic_index,
)


def test_a_standalone_master_fingerprint_is_stable_and_differs_from_its_sprint_case() -> None:
    sprint, master, candidate = _documents()
    graph = sprint.document.executionGraph
    assert graph is not None
    index = _semantic_index(graph, master)

    first = semantic_topology_fingerprint(None, master, candidate, graph_index=None)
    second = semantic_topology_fingerprint(None, master, candidate, graph_index=None)
    assert first == second

    commanded = semantic_topology_fingerprint(
        _bound_sprint(sprint, index), master, candidate, graph_index=index
    )
    assert commanded != first


#: Fingerprints computed on the clean base tree for ``_documents()``: the same fixture before the
#: sprint-less branch existed. A change here means a sprint case's canonical value moved.
_PINNED_WITH_GRAPH = "643f6709f510f1ae381e681a5a39bf89f42585e639975d75246749b4502ceda0"
_PINNED_WITHOUT_GRAPH = "e7c441a4e3b2f295ff24d0af282da910903dc93ad31a7cf38a14e39c843ec8c7"


def test_the_sprint_cases_fingerprint_is_unchanged() -> None:
    sprint, master, candidate = _documents()
    graph = sprint.document.executionGraph
    assert graph is not None
    index = _semantic_index(graph, master)
    assert (
        semantic_topology_fingerprint(
            _bound_sprint(sprint, index), master, candidate, graph_index=index
        )
        == _PINNED_WITH_GRAPH
    )
    plain_doc = sprint.document.model_copy(update={"executionGraph": None})
    plain = ResolvedTaskDocument(ref=sprint.ref, path=sprint.path, document=plain_doc)
    assert (
        semantic_topology_fingerprint(plain, master, candidate, graph_index=None)
        == _PINNED_WITHOUT_GRAPH
    )
