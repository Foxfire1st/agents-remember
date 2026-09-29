"""Pages of a named view of a memory tree, cut by the shared threshold (MIK-R02).

A view renderer orders its whole selection and hands back one row-limited slice at a time. On a
converted memory tree the bound is the token threshold instead, so this module reads the view's
whole ordered row list (walking the renderer's own slices to the end), and the pager cuts that list.
Nothing about what is selected or how it is ordered changes; the rows are the renderer's rows.

The page is still the view's own payload: ``rows`` is the page's slice,
``counts.rows_returned`` and ``counts.rows_remaining`` describe the page and what is ahead of it,
and ``continuation`` carries the shared-format token (so the payload's own validator -- rows
remaining exactly when a continuation is carried -- still holds). A family view is one family, so
every page after its first continues it and starts with the family's reference row (identity and
title, read from the index).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents_remember.application.knowledge_paging.pager import PageCut, PageRow
from agents_remember.application.knowledge_views import (
    TRAVERSAL_POLICY,
    VIEW_RENDERER_VERSION,
    read_knowledge_view,
)
from agents_remember.kernel.canonical_json import sha256_digest
from agents_remember.memory.knowledge_index import KnowledgeIndex
from agents_remember.models.knowledge.read import KnowledgeReadContext
from agents_remember.models.knowledge.view import (
    CountQuantity,
    CurationQueueRow,
    ViewContinuation,
    ViewPayload,
    ViewRefusal,
    ViewRequest,
)

__all__ = [
    "VIEW_POLICY",
    "VIEW_POLICY_VERSION",
    "WholeView",
    "family_reference",
    "read_whole_view",
    "view_page_payload",
    "view_rows",
]

VIEW_POLICY, VIEW_POLICY_VERSION = TRAVERSAL_POLICY.rsplit("/", 1)

# The one family a family view reads; every row of that view belongs to it.
_FAMILY_GROUP = "family"


@dataclass(frozen=True)
class WholeView:
    """One view's whole ordered row list, with the payload of its first slice for the envelope."""

    first: ViewPayload
    rows: tuple[Any, ...]

    @property
    def manifest_digest(self) -> str:
        """The digest of the ordered selection: each row's identity, under this renderer version."""

        return sha256_digest(
            {"renderer": VIEW_RENDERER_VERSION, "rows": [_row_identity(row) for row in self.rows]}
        )


def read_whole_view(
    database_path: Path, context: KnowledgeReadContext, request: ViewRequest
) -> WholeView | ViewRefusal:
    """Read every row of one view by walking the renderer's own slices to the end."""

    result = read_knowledge_view(database_path, context, request)
    if result.payload is None:
        assert result.refusal is not None
        return result.refusal
    first = result.payload
    rows: list[Any] = list(getattr(first, "rows", ()))
    continuation = first.continuation
    while continuation is not None:
        nxt = read_knowledge_view(
            database_path, context, request.model_copy(update={"continuation": continuation})
        )
        if nxt.payload is None:
            assert nxt.refusal is not None
            return nxt.refusal
        rows.extend(getattr(nxt.payload, "rows", ()))
        continuation = nxt.payload.continuation
    return WholeView(first=first, rows=tuple(rows))


def view_rows(whole: WholeView, family: Mapping[str, Any] | None) -> tuple[PageRow, ...]:
    """The view's rows as page rows; every row of a family view belongs to its one family.

    A family view's first page is where the family begins, so its first row carries the family's
    reference row, and every later page -- which continues the family -- starts with it.
    """

    if whole.first.view != "family" or family is None:
        return tuple(PageRow(row.model_dump(mode="json")) for row in whole.rows)
    return tuple(
        PageRow(
            row.model_dump(mode="json"),
            group=_FAMILY_GROUP,
            reference=family if position == 0 else None,
        )
        for position, row in enumerate(whole.rows)
    )


def family_reference(
    index_path: Path, tree_key: str, family_revision_id: str | None
) -> dict[str, Any] | None:
    """The reference row of the family a family view reads: its identity and its title."""

    if family_revision_id is None:
        return None
    with KnowledgeIndex(index_path, expected_key=tree_key) as index:
        text_id = index.text_id(family_revision_id)
        family_id = None if text_id is None else text_id.split("@", 1)[0]
        record = None if family_id is None else index.record(family_id).value
    title = None if record is None else record.document.get("title")
    return {
        "kind": "family_header_reference",
        "family_revision_id": family_revision_id,
        "family": text_id,
        "title": title,
    }


def view_page_payload(
    whole: WholeView, cut: PageCut, continuation: str | None, *, index_complete: bool
) -> dict[str, Any]:
    """The view payload of one page, validated as the view's own payload type."""

    first = whole.first
    counts = first.counts.model_copy(
        update={
            "rows_returned": CountQuantity(state="counted", value=cut.rows_on_page),
            "rows_remaining": CountQuantity(state="counted", value=cut.remaining),
        }
    )
    token = (
        None
        if continuation is None
        else ViewContinuation(
            token=continuation,
            view=first.view,
            snapshot_logical_digest=first.snapshot.logical_digest,
        )
    )
    completeness = first.completeness.model_copy(
        update={"complete_within_declared_scope": continuation is None}
    )
    page = type(first).model_validate(
        {
            **first.model_dump(),
            "rows": [row.model_dump() for row in whole.rows[cut.start : cut.end]],
            "counts": counts.model_dump(),
            "continuation": None if token is None else token.model_dump(),
            "completeness": completeness.model_dump(),
        }
    )
    body = page.model_dump(mode="json")
    if not index_complete:
        # Complete within what the index holds, and the index is not the whole tree.
        body["completeness"]["complete_within_declared_scope"] = False
    return body


def _row_identity(row: Any) -> dict[str, Any]:
    """What one row is about, independent of how its facts were resolved on this read."""

    if isinstance(row, CurationQueueRow):
        disposition = None if row.disposition is None else row.disposition.disposition_id
        return {"item": row.item.work_item_id, "disposition": disposition}
    return {
        "subject": row.subject.model_dump(mode="json"),
        "fact": getattr(row, "fact_kind", None),
        "position": row.order.position,
    }
