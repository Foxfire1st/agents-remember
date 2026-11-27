"""MCP view adapter for ``knowledge_read`` drafting/verification views (impact, search, records)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents_remember.application.knowledge_code_pairing import (
    NoCodeTreeError,
    code_tree_of,
    observe_code_source,
)
from agents_remember.application.knowledge_impact import ImpactQuery, impact_view
from agents_remember.application.knowledge_reader.records_batch import records_view
from agents_remember.application.knowledge_reader.selection import (
    READ_FAILURES,
    selection_for_dataset,
)
from agents_remember.application.knowledge_search import SearchQuery, search_view
from agents_remember.application.published_intent import SelectedKnowledgeDataset
from agents_remember.kernel.primitives.runtime_config import RepositoryScope

QUERY_VIEWS = frozenset({"impact", "search", "records"})


@dataclass(frozen=True)
class QueryViewContext:
    """Mount and configuration context passed into MCP query views."""

    repositories: Mapping[str, RepositoryScope] | None = None
    workspace_root: str | None = None
    coordination_root: str | None = None


def run_query_view(
    request: Any,
    selected: SelectedKnowledgeDataset,
    repository_id: str,
    *,
    context: QueryViewContext | None = None,
) -> dict[str, Any]:
    """Execute ``impact``, ``search``, or ``records`` over one selected converted memory tree."""

    tree = selected.memory_tree
    if tree is None:
        return _refused(
            request.view,
            repository_id,
            "snapshot_unavailable",
            "the selection names no converted memory tree, so this view cannot run",
        )
    ctx = context if context is not None else QueryViewContext()
    memory_root = Path(request.memory_root)
    try:
        source = observe_code_source(
            request,
            repositories=ctx.repositories,
            workspace_root=ctx.workspace_root,
        )
    except NoCodeTreeError:
        source = None
    code_tree = code_tree_of(source)
    try:
        selection = selection_for_dataset(
            repository_id=repository_id,
            memory_root=memory_root,
            database_path=selected.database_path,
            tree_key=tree.tree_key,
            code_tree=code_tree,
            coordination_root=None
            if ctx.coordination_root is None
            else Path(ctx.coordination_root),
        )
    except READ_FAILURES as error:
        if source is not None:
            source.close()
        return _refused(
            request.view,
            repository_id,
            "snapshot_unavailable",
            f"the selected index could not be opened: {error}",
        )
    try:
        payload = _dispatch(request, selection)
    except READ_FAILURES as error:
        return _refused(
            request.view,
            repository_id,
            "unavailable",
            f"the query could not be completed: {error}",
        )
    finally:
        selection.close()
        if source is not None:
            source.close()

    if "refusalCode" in payload:
        return _refused(
            request.view,
            repository_id,
            str(payload["refusalCode"]),
            str(payload.get("refusalDetail") or "invalid query request"),
        )

    continuation = payload.pop("continuation", None)
    complete = bool(payload.get("enumerationComplete", True)) and tree.index_state == "complete"
    payload["enumerationComplete"] = complete
    answer: dict[str, Any] = {
        "ok": True,
        "state": "view",
        "view": request.view,
        "repositoryId": repository_id,
        "snapshot": tree.tree_key,
        "completeWithinDeclaredScope": complete,
        "memoryTree": {
            "memoryRoot": memory_root.as_posix(),
            "treeId": tree.tree_key,
            "indexState": tree.index_state,
            "problems": [{"path": p, "detail": d} for p, d in tree.problems],
        },
        "indexComplete": tree.index_state == "complete",
        "payload": payload,
    }
    if continuation is not None:
        answer["continuation"] = continuation
    return answer


def _dispatch(request: Any, selection: Any) -> dict[str, Any]:
    view = request.view
    if view == "impact":
        return impact_view(
            selection,
            ImpactQuery(
                files=tuple(request.files or ()),
                folder=request.folder or request.source_path,
                compact=bool(request.compact),
                continuation=request.continuation,
            ),
        )
    if view == "search":
        return search_view(
            selection,
            SearchQuery(
                query=request.query or "",
                kinds=None if request.kinds is None else tuple(request.kinds),
                files=tuple(request.files or ()),
                folder=request.folder or request.source_path,
                compact=bool(request.compact),
                continuation=request.continuation,
            ),
        )
    return records_view(
        selection,
        request.record_ids or (),
        continuation=request.continuation,
    )


def _refused(view: str, repository_id: str, code: str, detail: str) -> dict[str, Any]:
    return {
        "ok": True,
        "state": "refused",
        "view": view,
        "repositoryId": repository_id,
        "refusalCode": code,
        "refusalDetail": detail,
    }


__all__ = ["QUERY_VIEWS", "QueryViewContext", "run_query_view"]
