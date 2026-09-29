"""Bounded pages of a memory tree's knowledge, and the one continuation that resumes them (MIK-R02).

Every bounded read of a converted memory tree is cut by one declared token threshold
(:mod:`.threshold`) into pages of whole rows (:mod:`.pager`). Each page carries the shared
continuation (:mod:`agents_remember.models.knowledge.continuation`), bound to the memory tree, the
seed, the selection policy and version, the selection-manifest digest and the position
(:mod:`.bindings`), and the mounted ``knowledge_read`` resumes it whichever surface minted it
(:mod:`.tree_read`). Two responses are paged today:

* the selective scope read behind the published-intent block of ``read_ar_files``
  (:mod:`.scope_pages`);
* the named views of ``knowledge_read`` (:mod:`.view_pages`).

**The seam for the next responses.** A response owner that pages -- the family-complete leaf read
(MIK-R01) and the route-chain family entries (MIK-R05) -- supplies its ordered selection as
:class:`~.pager.PageRow` values (a family header row carries the ``reference`` row a later page of
its family starts with; its members name the family as their ``group``), a manifest digest over
that order, and a policy name and version; the pager, the threshold, the continuation and its
binding checks are shared. Route-chain entries are further rows after the family content, so they
count toward the same threshold and resume through the same token.

Reads of an unconverted database are not paged here: they keep their own budgets and cursors
until the database route is retired.

:mod:`.tree_read` is imported by its own module path rather than re-exported here, because it
depends on the published-intent route, which in turn pages through :mod:`.scope_pages`.
"""

from __future__ import annotations

from agents_remember.application.knowledge_paging.pager import (
    OVERSIZED_ROW,
    PageBinding,
    PageCut,
    PageRow,
    cut_page,
    page_block,
)
from agents_remember.application.knowledge_paging.threshold import (
    KNOWLEDGE_PAGE_THRESHOLD_TOKENS,
    KNOWLEDGE_PAGE_TOKENIZER,
    threshold_block,
)

__all__ = [
    "KNOWLEDGE_PAGE_THRESHOLD_TOKENS",
    "KNOWLEDGE_PAGE_TOKENIZER",
    "OVERSIZED_ROW",
    "PageBinding",
    "PageCut",
    "PageRow",
    "cut_page",
    "page_block",
    "threshold_block",
]
