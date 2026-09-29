"""Cutting one ordered selection into pages that stay within the shared threshold (MIK-R02 rule 2).

The pager knows rows, not knowledge. A surface hands it the whole ordered selection as
:class:`PageRow` values -- a family header, a member, an entry, a route-chain entry or a reference
row, in the order the owning response defines -- plus a ``render`` callback that builds the complete
response for one :class:`PageCut`. The pager then finds the longest run of whole rows from the
start position whose *rendered response* fits, so everything the response carries beside its rows
(the envelope, the counts, the continuation itself) is inside the measurement.

* **Rows are indivisible.** A row is never shortened. When even one row does not fit, that row is
  returned alone and the cut is flagged ``oversized``; it is the only way a response exceeds the
  threshold.
* **A page that continues a family starts with its header.** A row may belong to a ``group`` (the
  family it is part of), and the group's header row carries the ``reference`` row a later page
  starts with. When a page begins inside a group whose header was on an earlier page, the cut
  carries that reference row first. A reference row repeats an identity already returned, so it is
  not counted as a returned row.
* **The walk is exact.** A cut covers ``[start, end)`` of one selection; the next page starts at
  ``end``. Every row therefore appears on exactly one page, and ``returned``/``remaining`` are the
  walk's figures, not the page's.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from agents_remember.application.knowledge_paging.threshold import (
    ENVELOPE_RESERVE_TOKENS,
    KNOWLEDGE_PAGE_THRESHOLD_TOKENS,
    response_tokens,
    threshold_block,
)

__all__ = [
    "OVERSIZED_ROW",
    "PageBinding",
    "PageCut",
    "PageRow",
    "RenderPage",
    "cut_page",
    "header_reference",
    "page_block",
]

# The flag an oversized single-row page carries.
OVERSIZED_ROW = "oversized_row"


@dataclass(frozen=True)
class PageRow:
    """One indivisible row of an ordered selection, as a page emits it.

    ``group`` names the family a row belongs to, if any. A header row of that group also carries
    ``reference``: the short reference row (identity and title) a later page of the group starts
    with.
    """

    body: Mapping[str, Any]
    group: str | None = None
    reference: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class PageCut:
    """One page of a walk: rows ``[start, end)`` of ``total``, and how the page begins."""

    start: int
    end: int
    total: int
    header_reference: Mapping[str, Any] | None = None
    oversized: bool = False
    # No row fits beside what the response already carries, though the next row alone would.
    blocked: bool = False

    @property
    def rows_on_page(self) -> int:
        return self.end - self.start

    @property
    def remaining(self) -> int:
        return self.total - self.end

    @property
    def complete(self) -> bool:
        return self.end >= self.total


@dataclass(frozen=True)
class PageBinding:
    """What a walk is bound to, stated on every page and carried by its continuation."""

    memory_tree_id: str
    selection_policy: str
    policy_version: str
    manifest_digest: str
    code_tree_id: str | None = None


RenderPage = Callable[[PageCut], dict[str, Any]]


def header_reference(rows: Sequence[PageRow], start: int) -> Mapping[str, Any] | None:
    """The reference row a page starting at ``start`` begins with, or ``None``.

    Only a page that starts *inside* a group -- on a row of the group that is not its header, with
    the header on an earlier page -- continues a family.
    """

    if start <= 0 or start >= len(rows):
        return None
    first = rows[start]
    if first.group is None or first.reference is not None:
        return None
    for row in rows[:start]:
        if row.group == first.group and row.reference is not None:
            return row.reference
    return None


def cut_page(
    rows: Sequence[PageRow],
    start: int,
    render: RenderPage,
    *,
    alone: RenderPage | None = None,
    threshold: int = KNOWLEDGE_PAGE_THRESHOLD_TOKENS,
) -> tuple[PageCut, dict[str, Any]]:
    """Cut the page starting at ``start`` and return it with its rendered response.

    The run is first estimated from each row's own token count over the rendered empty page, then
    verified against the rendered response itself and shortened until it fits. ``start`` equal to
    the selection's length yields the empty last page of an empty selection.

    ``alone`` renders the page as a response of its own, for a page laid out beside others (a seed
    of a ``read_ar_files`` block). A row that does not fit is ``oversized`` only when it does not
    fit alone either; otherwise the cut comes back empty and ``blocked``, and the caller defers it.
    """

    total = len(rows)
    budget = threshold - ENVELOPE_RESERVE_TOKENS
    reference = header_reference(rows, start)
    empty = PageCut(start=start, end=start, total=total, header_reference=reference)
    if start >= total:
        return empty, render(empty)
    end = _estimated_end(rows, start, response_tokens(render(empty)), budget)
    while True:
        cut = replace(empty, end=end)
        body = render(cut)
        if response_tokens(body) <= budget:
            return cut, body
        if cut.rows_on_page == 1:
            if alone is not None and response_tokens(alone(cut)) <= budget:
                blocked = replace(empty, blocked=True)
                return blocked, render(blocked)
            flagged = replace(cut, oversized=True)
            return flagged, render(flagged)
        end -= 1


def _estimated_end(rows: Sequence[PageRow], start: int, used: int, budget: int) -> int:
    """The end of the longest run whose summed row sizes fit beside the empty page, at least one."""

    end = start
    while end < len(rows):
        # One more token for the separator a row adds to the list it is placed in.
        cost = response_tokens(rows[end].body) + 1
        if used + cost > budget:
            break
        used += cost
        end += 1
    return max(end, start + 1)


def page_block(cut: PageCut, binding: PageBinding) -> dict[str, Any]:
    """The page facts every bounded response states (MIK-R02 rules 1, 3 and 5).

    The continuation itself travels where each surface already carries one, beside this block.
    """

    block: dict[str, Any] = {
        "threshold": threshold_block(),
        "memoryTreeId": binding.memory_tree_id,
        "selectionPolicy": binding.selection_policy,
        "selectionPolicyVersion": binding.policy_version,
        "manifestDigest": binding.manifest_digest,
        "codeTreeId": binding.code_tree_id,
        "start": cut.start,
        "rowsOnPage": cut.rows_on_page,
        "total": cut.total,
        "returned": cut.end,
        "remaining": cut.remaining,
        "enumerationComplete": cut.complete,
    }
    if cut.header_reference is not None:
        block["headerReference"] = dict(cut.header_reference)
    if cut.oversized:
        block["flags"] = [OVERSIZED_ROW]
    return block
