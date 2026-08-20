"""Joined master/leaf titles for one sprint execution graph.

The persisted graph stores refs and leaf ids only; the human-readable render
(mermaid diagram, dashboard projection) joins titles from the commanded master
documents. This module owns that join so the renderer and the projection share
one source of truth. ``build_graph_titles`` is pure (in-memory docs);
``read_graph_titles`` is the disk-backed form used by application writers that
regenerate a sprint's ``task.md``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from agents_remember.models.task_document_ref import TaskDocumentRef

from .document import SprintExecutionGraph, TaskDocument


@dataclass(frozen=True)
class SprintGraphTitles:
    """Titles joined for one execution graph render.

    Keys are the same identities the graph uses: a master ref's ``key``
    (``repository/path``) for ``master_titles`` and the leaf id for
    ``leaf_titles``. Absent keys mean the source document was missing or
    invalid; callers fall back to the raw key / leaf id.
    """

    master_titles: dict[str, str] = field(default_factory=dict)
    leaf_titles: dict[str, str] = field(default_factory=dict)


def build_graph_titles(
    graph: SprintExecutionGraph,
    masters: Mapping[TaskDocumentRef, TaskDocument],
) -> SprintGraphTitles:
    """Join one graph's master/leaf titles from in-memory master documents.

    Leaf titles come from the master's ``subTasks`` index rows: the graph's
    leaf ids ARE the row ``number`` values (sprint-wide unique per the L11
    contract), so the row ``name`` is the leaf's title. Masters the caller did
    not supply are skipped -- the caller's fallback labels cover them.
    """

    master_titles: dict[str, str] = {}
    leaf_titles: dict[str, str] = {}
    for ref in graph.master_refs():
        master = masters.get(ref)
        if master is None:
            continue
        master_titles[ref.key] = master.title
        for row in master.subTasks:
            leaf_titles[row.number] = row.name
    return SprintGraphTitles(master_titles=master_titles, leaf_titles=leaf_titles)


def read_graph_titles(tasks_root: Path, graph: SprintExecutionGraph) -> SprintGraphTitles:
    """Read the commanded master documents a graph references and join titles.

    ``tasks_root`` is the ``tasks/`` directory; a master ref's full path is
    ``tasks_root / repository / path``. Missing or invalid master documents are
    tolerated -- their nodes render with the ref-key / leaf-id fallback.
    """

    masters: dict[TaskDocumentRef, TaskDocument] = {}
    for ref in graph.master_refs():
        path = tasks_root / ref.repository / ref.path
        try:
            masters[ref] = TaskDocument.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    return build_graph_titles(graph, masters)
