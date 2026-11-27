"""The ``knowledge_read`` views that answer one record or one table rather than a tree page.

``record`` is one record's truth view; ``weakness`` and ``dependent`` list the marks of those two
record kinds; ``writers`` is the table of how each writer did; ``consolidation_proposal`` names the
families a consolidation pass could take. Each reads the dataset the read already selected and
verified, and answers through the same refusal shape as every other view.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

from agents_remember.application.knowledge_consolidation_proposal import (
    PROPOSAL_VIEW,
    ProposalRefusal,
    ProposalRequest,
    proposal_view,
)
from agents_remember.application.knowledge_currentness import CodeTree
from agents_remember.application.knowledge_reader.marks import (
    dependent_list,
    weakness_list,
)
from agents_remember.application.knowledge_reader.selection import (
    READ_FAILURES,
    selection_for_dataset,
)
from agents_remember.application.knowledge_reader.truth import record_view
from agents_remember.application.knowledge_writers_view import (
    WritersRequest,
    WriterTableRefusal,
    writers_view,
)
from agents_remember.application.published_intent import (
    SelectedKnowledgeDataset,
)
from agents_remember.application.record_standing_answers import record_answer_standing
from agents_remember.models.tools.tool_answer import (
    TOOL_ANSWER_LIST_MAX,
)

from .read_request import (
    ReadToolRequest,
    _NoCodeTreeError,
    _observe_source,
    _ObservedSource,
    _refused_read,
)

# The views this module answers: each reads one record, one listing or one table of a selected
# tree, never a page of it.
SINGLE_RECORD_VIEWS: Final = ("record", "weakness", "dependent", "writers", PROPOSAL_VIEW)


def _record_view(
    request: ReadToolRequest,
    selected: SelectedKnowledgeDataset,
    repository_id: str,
    *,
    workspace_root: str | None = None,
    coordination_root: str | None = None,
) -> dict[str, Any]:
    """One record's truth view: its fields, its links with their states, and its history rows.

    MIK-R73 rule 2. The reader computes the answer (:func:`record_view`), over the dataset this
    read already selected and verified, so the mounted view and the dashboard's record view cannot
    answer differently. The answer is bounded the way every answer is: the link lists and the
    timeline carry their first rows and their totals, and the record's prose is named by its
    location rather than carried (MIK-R109 rule 2).
    """

    if not request.record_id:
        return _refused_read(
            "record",
            repository_id,
            "invalid-input",
            "the record view names a `recordId`: a record's text ID (INV-…, FAM-…, DEC-…, ASM-…)",
        )
    tree = selected.memory_tree
    if tree is None:
        return _refused_read(
            "record",
            repository_id,
            "snapshot_unavailable",
            "the selection names no converted memory tree, so no record can be read",
        )
    memory_root = Path(request.memory_root)
    source = _observed_source_or_none(request, workspace_root)
    code_tree = _code_tree_of(source)
    try:
        selection = selection_for_dataset(
            repository_id=repository_id,
            memory_root=memory_root,
            database_path=selected.database_path,
            tree_key=tree.tree_key,
            code_tree=code_tree,
            coordination_root=None if coordination_root is None else Path(coordination_root),
        )
    except READ_FAILURES as error:
        return _refused_read(
            "record",
            repository_id,
            "snapshot_unavailable",
            f"the selected index could not be opened: {error}",
        )
    try:
        found = record_view(selection, request.record_id)
    except READ_FAILURES as error:
        return _refused_read(
            "record", repository_id, "unavailable", f"the record could not be read: {error}"
        )
    finally:
        selection.close()
        if source is not None:
            source.close()
    if found is None:
        return _refused_read(
            "record",
            repository_id,
            "selector_absent",
            f"the selected tree holds no record {request.record_id}",
        )
    prose = found.pop("prose", None)
    # MIK-R109 rule 2: the mounted answer names the record, its file and its heading; the record's
    # own document stays in the reader's structure (and in the file it names), never in this answer.
    record_document = found.get("record")
    if isinstance(record_document, dict):
        record_document.pop("document", None)
    answer: dict[str, Any] = {
        "ok": True,
        "operation": "knowledge_read",
        "view": "record",
        "state": "view",
        **record_answer_standing(found),
        "repositoryId": repository_id,
        "memoryRoot": memory_root.as_posix(),
        "memoryTree": {"treeKey": tree.tree_key, "indexState": tree.index_state},
        **found,
    }
    if isinstance(prose, dict):
        # The prose file is named, never carried: this read names where a record is.
        answer["prose"] = {"path": prose.get("path"), "state": prose.get("state")}
    _bound_record_lists(answer)
    return answer


def _one_view(
    request: ReadToolRequest,
    selected: SelectedKnowledgeDataset,
    repository_id: str,
    *,
    workspace_root: str | None = None,
    coordination_root: str | None = None,
) -> dict[str, Any]:
    """The answer one single-record view holds: a record's truth view, the marks' listing, the
    writers' table, or the families a consolidation pass could take."""

    if request.view == "writers":
        return _writers_view(request, selected, coordination_root)
    if request.view == PROPOSAL_VIEW:
        return _proposal_view(request, selected, coordination_root)
    if request.view == "record":
        return _record_view(
            request,
            selected,
            repository_id,
            workspace_root=workspace_root,
            coordination_root=coordination_root,
        )
    return _marks_view(
        request,
        selected,
        repository_id,
        workspace_root=workspace_root,
        coordination_root=coordination_root,
    )


def _marks_view(
    request: ReadToolRequest,
    selected: SelectedKnowledgeDataset,
    repository_id: str,
    *,
    workspace_root: str | None = None,
    coordination_root: str | None = None,
) -> dict[str, Any]:
    """The weaknesses or the dependents of one memory tree, bounded and naming how to read on.

    MIK-R118 rule 6 and MIK-R124 rule 7 read through this same tool. A weakness row holds the id,
    the file, the kind, the sentence and the measurement; a dependent row holds the document, its
    derived state and, where it is stale, each link that moved. Neither row carries the record's
    own text beyond what the requirement names.

    The page is what the answer can actually deliver: the caller's ``limit`` capped at the
    transport's list bound, delivered from ``continuation`` (a ``rows:<n>`` cursor) onward, so the
    counts are true and a page beyond the bound is reachable (F14). An anterior page's cursor is
    the only continuation accepted; a token this view cannot read refuses, never restarts the walk.
    """

    tree = selected.memory_tree
    if tree is None:
        return _refused_read(
            request.view,
            repository_id,
            "snapshot_unavailable",
            "the selection names no converted memory tree, so no record can be read",
        )
    start = _marks_offset(request)
    if isinstance(start, str):
        return _refused_read(request.view, repository_id, "bad_continuation", start)
    memory_root = Path(request.memory_root)
    source = _observed_source_or_none(request, workspace_root)
    code_tree = _code_tree_of(source)
    try:
        selection = selection_for_dataset(
            repository_id=repository_id,
            memory_root=memory_root,
            database_path=selected.database_path,
            tree_key=tree.tree_key,
            code_tree=code_tree,
            coordination_root=None if coordination_root is None else Path(coordination_root),
        )
    except READ_FAILURES as error:
        return _refused_read(
            request.view,
            repository_id,
            "snapshot_unavailable",
            f"the selected index could not be opened: {error}",
        )
    try:
        rows = _marks_rows(request, selection)
    except READ_FAILURES as error:
        return _refused_read(
            request.view, repository_id, "unavailable", f"the records could not be read: {error}"
        )
    finally:
        selection.close()
        if source is not None:
            source.close()
    limit = min(max(1, request.limit), TOOL_ANSWER_LIST_MAX)
    page = rows[start : start + limit]
    remaining = max(0, len(rows) - start - len(page))
    answer: dict[str, Any] = {
        "ok": True,
        "operation": "knowledge_read",
        "view": request.view,
        "state": "view",
        "repositoryId": repository_id,
        "memoryRoot": memory_root.as_posix(),
        "memoryTree": {"treeKey": tree.tree_key, "indexState": tree.index_state},
        "rows": page,
        "counts": {
            "rowsReturned": len(page),
            "total": len(rows),
            "rowsRemaining": remaining,
            "offset": start,
        },
    }
    if remaining:
        # MIK-R108: the answer names how to read on rather than carrying the rest, and the cursor
        # it names reaches rows the transport's list bound would otherwise hide.
        answer["readOn"] = {
            "how": (
                "the same view answers the next rows: pass `continuation` as given here, or "
                "narrow it with `sourcePath` (a folder), `weaknessKind`, `staleOnly` or `standsOn`"
            ),
            "continuation": _marks_cursor(start + len(page)),
            "rowsRemaining": remaining,
            "limit": limit,
        }
    return answer


def _marks_cursor(start: int) -> str:
    """The continuation a marks view's next page starts at."""

    return f"rows:{start}"


def _marks_offset(request: ReadToolRequest) -> int | str:
    """The row a marks view's page starts at, or why the continuation cannot be read."""

    token = request.continuation
    if token is None or not token.strip():
        return 0
    head, _, tail = token.strip().partition(":")
    if head != "rows" or not tail.isdigit():
        return (
            f"the continuation {token!r} is not one this view minted: it reads `rows:<n>` as the "
            "previous page's `readOn` carried it; narrow the view instead of restarting it"
        )
    return int(tail)


def _marks_rows(request: ReadToolRequest, selection: Any) -> list[dict[str, Any]]:
    """The rows one marks view holds, before the answer's bound is applied."""

    if request.view == "weakness":
        return weakness_list(
            selection,
            folder=request.source_path,
            kind=request.weakness_kind,
            include_closed=request.include_closed,
        )
    return dependent_list(
        selection,
        folder=request.source_path,
        stale_only=request.stale_only,
        stands_on=request.stands_on,
    )


def _observed_source_or_none(
    request: ReadToolRequest, workspace_root: str | None
) -> _ObservedSource | None:
    """The code source a record read assesses its links at, when one resolves.

    A record read is a read of recorded knowledge first: a checkout that resolves no tree still
    answers the record and reports its anchor states as unverifiable, naming why.
    """

    try:
        return _observe_source(request, workspace_root)
    except _NoCodeTreeError:
        return None


def _code_tree_of(source: _ObservedSource | None) -> CodeTree | None:
    """The `CodeTree` a record read assesses links at, or ``None`` when nothing resolved."""

    if source is None or source.repository_root is None or source.code_tree_id is None:
        return None
    return CodeTree(
        Path(source.repository_root),
        source.code_tree_id,
        None if source.capture is None else source.capture.object_directory,
    )


def _bound_record_lists(answer: dict[str, Any]) -> None:
    """Cap the record view's link lists and timeline, each with its total beside it."""

    for key in ("outgoing", "incoming"):
        rows = answer.get(key)
        if not isinstance(rows, list):
            continue
        answer[f"{key}Count"] = len(rows)
        if len(rows) > TOOL_ANSWER_LIST_MAX:
            answer[key] = rows[:TOOL_ANSWER_LIST_MAX]
            answer[f"{key}Truncated"] = True
    timeline = answer.get("timeline")
    if isinstance(timeline, dict) and isinstance(timeline.get("events"), list):
        events = timeline["events"]
        answer["timelineCount"] = len(events)
        if len(events) > TOOL_ANSWER_LIST_MAX:
            answer["timeline"] = {**timeline, "events": events[:TOOL_ANSWER_LIST_MAX]}
            answer["timelineTruncated"] = True


def _writers_view(
    request: ReadToolRequest, selected: SelectedKnowledgeDataset, coordination_root: str | None
) -> dict[str, Any]:
    """The table of how each writer did, or its refusal (the application owns the table)."""

    try:
        return writers_view(
            selected,
            WritersRequest(
                memory_root=request.memory_root,
                repository_id=request.repository_id,
                limit=request.limit,
                coordination_root=coordination_root,
            ),
        )
    except WriterTableRefusal as refusal:
        return _refused_read(request.view, request.repository_id, refusal.code, refusal.detail)


def _proposal_view(
    request: ReadToolRequest, selected: SelectedKnowledgeDataset, coordination_root: str | None
) -> dict[str, Any]:
    """The families a consolidation pass could take, or its refusal (the application chooses)."""

    try:
        return proposal_view(
            selected,
            ProposalRequest(
                memory_root=request.memory_root,
                repository_id=request.repository_id,
                limit=request.limit,
                coordination_root=coordination_root,
                master_id=request.master_id,
                record_id=request.record_id,
                source_path=request.source_path,
            ),
        )
    except ProposalRefusal as refusal:
        return _refused_read(request.view, request.repository_id, refusal.code, refusal.detail)
