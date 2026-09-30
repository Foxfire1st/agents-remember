"""The path-based knowledge reader: browse a repository's knowledge at any memory tree (MIK-R29).

The dashboard's Knowledge area asks one question per call, addressed by repository, memory tree
(:mod:`.selection`: ``published`` by default, a memory commit, or a live leaf's candidate) and a
path or record ID -- the same values its URL carries, so every view can be shared:

========================  ==========================================================================
``selections``            the commit selector's choices (:func:`.selection.selection_options`)
``tree``                  one directory level of the explorer (:func:`.paths.tree_listing`)
``path``                  the path view of a file, or the bounded summary of a directory (:func:`.paths.path_view`)
``subtree``               every entry under a directory, paged by the shared continuation (:func:`.subtree.subtree_page`)
``record``                the truth view of one record, with its timeline (:func:`.truth.record_view`)
``records``               every record by kind (:func:`.truth.record_list`)
``census``                the census view (:func:`.truth.census_view`)
``without-proof``         invariants no proof names, by path (:func:`.paths.without_proof`)
``code``                  a code file at the selection's code tree, at a locator (:func:`.truth.code_view`)
========================  ==========================================================================

**Read-only** (rule 6): the reader never writes a repository. It is served from the derived index
of the selected tree (MIK-R23; building it writes only the coordination runtime's cache) and from
Git objects; a working-tree selection reads its files from disk. **Failure**: a partial index names
its problems in ``selection``; an unavailable file, tree or state is named on the part of the view it
affects, never replaced by an empty result.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Final

from agents_remember.application.knowledge_reader.files import ReaderRequestError
from agents_remember.application.knowledge_reader.paths import (
    path_view,
    tree_listing,
    without_proof,
)
from agents_remember.application.knowledge_reader.selection import (
    READ_FAILURES,
    ReaderSelection,
    ReaderUnavailable,
    open_selection,
    selection_options,
)
from agents_remember.application.knowledge_reader.subtree import subtree_page
from agents_remember.application.knowledge_reader.truth import (
    census_view,
    code_view,
    record_list,
    record_view,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.serving.knowledge_reader import KnowledgeReaderQuery

__all__ = ["READER_VIEWS", "ReaderQuery", "read_knowledge_reader"]

READER_VIEWS: Final = (
    "selections",
    "tree",
    "path",
    "subtree",
    "record",
    "records",
    "census",
    "without-proof",
    "code",
)


ReaderQuery = KnowledgeReaderQuery


def read_knowledge_reader(config: McpRuntimeConfig, query: ReaderQuery) -> dict[str, Any]:
    """Answer one reader question; the answer's ``state`` says what kind of answer it is."""

    if query.view not in READER_VIEWS:
        return _invalid(query, f"the view is one of {', '.join(READER_VIEWS)}")
    if query.view == "selections":
        return selection_options(config, query.repository_id)
    opened = open_selection(config, query.repository_id, query.commit)
    if isinstance(opened, ReaderUnavailable):
        return opened.to_document(query.repository_id, query.commit or "published")
    with opened as selection:
        try:
            answer = _answer(selection, query)
        except ReaderRequestError as error:
            return _invalid(query, str(error))
        except READ_FAILURES as error:
            answer = {"state": "unavailable", "detail": f"{type(error).__name__}: {error}"}
        return {"view": query.view, "selection": selection.to_document(), **answer}


def _answer(selection: ReaderSelection, query: ReaderQuery) -> dict[str, Any]:
    if query.view == "census":
        return census_view(selection, query.census_id)
    if query.view == "code":
        if not query.path:
            raise ReaderRequestError("the code view names a path")
        return code_view(selection, query.path, query.locator, query.blob)
    if query.view == "record":
        return _record(selection, query.record_id)
    views: dict[str, Callable[[], dict[str, Any]]] = {
        "tree": lambda: tree_listing(selection, query.path or ""),
        "path": lambda: path_view(selection, query.path or ""),
        "subtree": lambda: subtree_page(selection, query.path or "", query.continuation),
        "without-proof": lambda: without_proof(selection, query.path),
        "records": lambda: record_list(selection),
    }
    return {"state": "view", **views[query.view]()}


def _record(selection: ReaderSelection, record_id: str | None) -> dict[str, Any]:
    if not record_id:
        raise ReaderRequestError("the record view names a record ID")
    found = record_view(selection, record_id)
    if found is None:
        return {"state": "not-found", "detail": f"the selected tree holds no record {record_id}"}
    return {"state": "view", **found}


def _invalid(query: ReaderQuery, detail: str) -> dict[str, Any]:
    return {
        "state": "invalid-request",
        "view": query.view,
        "selection": {"repo": query.repository_id, "commit": query.commit or "published"},
        "detail": detail,
    }
