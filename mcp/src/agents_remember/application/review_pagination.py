"""The review surface's page arithmetic: one collection's bounds, scope and reset (``ICR-R10@v1``).

Two bounded collections are composed into one review -- the **knowledge comparison**, whose page is
the shipped comparison's own window of its before/after union, and the **review matrix records**,
whose page is the shipped view's own window of its selection. Each owner mints and validates its own
cursor, and this module owns the one thing that is neither owner's: how a page of either collection
is *stated* on the review surface.

**It mints no cursor and re-derives no binding.** The continuation a page publishes is the owner's own
opaque token, carried verbatim: the comparison's ``knowledge-diff-cursor/v1`` for the knowledge
collection. That is what keeps one pagination authority: a caller that handed another owner's cursor
to the comparison would be presenting a position in one walk to a different walk, and the comparison
refuses that instead of serving a slice of it.

**The counts are the owner's own.** ``total``, ``returned`` and ``remaining`` are read off the
comparison's ``KnowledgeDiffCounts`` exactly as that owner published them; nothing here subtracts two
of them to invent the third. The arithmetic this module does perform
is the check that the three agree, which :class:`ReviewCollectionPage` enforces at construction -- a
page whose numbers do not add up cannot be built, so ``remaining=100`` can never be printed beside a
walk that does not have a hundred rows left in it.

**A cursor that no longer binds is a reset, not a fallback.** Both owners reject a cursor presented
against a moved snapshot with their own typed refusal and their own next action. This module maps
that rejection onto the surface's single ``comparison_page_reset`` refusal, carrying the owner's
expected and observed identities through and stating the *new generation* action ICR-R10 requires:
the cursor is not re-resolved against the current generation, and the page the surface serves beside
the refusal is the current comparison's first page rather than a page stitched from two states.
"""

from __future__ import annotations

from agents_remember.models.knowledge.diff import (
    KnowledgeDiffPage,
    KnowledgeDiffResult,
    continue_diff_from_cursor,
)
from agents_remember.models.knowledge.review import (
    REVIEW_PAGE_RESET_NEXT_ACTION,
    ReviewCollectionPage,
    ReviewPagedCollection,
    ReviewRefusal,
)

__all__ = [
    "MOVED_SNAPSHOT_CODES",
    "comparison_page",
    "comparison_reset",
    "reset_comparison_page",
    "unreadable_page_refusal",
]

# The one owner refusal code that means "this cursor bound a comparison that has moved": the
# comparison's own binding mismatch. It alone is mapped onto the moved-snapshot action.
#
# Everything else keeps the owner's own next action and is reported as ``comparison_page_unreadable``
# instead, because telling a reader to open a new comparison when nothing moved would be a false
# statement about what happened. The two directions are told apart by asking the *owner's own
# decoder* whether the token is one of its cursors at all (``continue_diff_from_cursor`` for the
# comparison) rather than by guessing from the refusal text.
MOVED_SNAPSHOT_CODES: frozenset[str] = frozenset({"continuation_binding_mismatch"})

# The one owner refusal code that means "this text is not a cursor of the collection you named at
# all": the comparison's token codec could not read it back. It is a caller's mistake about *which
# walk* the token belongs to, so it earns the owner's own remedy rather than the moved-snapshot
# action.
_UNREADABLE_CURSOR = "continuation_unreadable"


def comparison_reset(result: KnowledgeDiffResult, *, cursor: str | None) -> ReviewRefusal | None:
    """The refusal a comparison earned for a cursor it could not continue, or ``None``.

    Two facts are separated here, and the cursor's own owner decides which one this is. A token the
    comparison's decoder reads back **is** one of its cursors, so a refusal on it is a binding
    mismatch -- the snapshot pair, the selector or the display policy moved -- and it earns the
    new-generation action. A token the decoder does not read back is not this collection's cursor at
    all: nothing moved, the caller presented the other walk's token, and that earns the owner's own
    next action under ``comparison_page_unreadable`` rather than a generation change that did not
    happen.

    No other refused comparison -- an absent dataset, an unknown selector, a reading failure -- is
    routed here, because none of those is a page of a walk that somebody is holding.
    """

    shipped = result.refusal
    if cursor is None or shipped is None or shipped.code not in MOVED_SNAPSHOT_CODES:
        return None
    if continue_diff_from_cursor(cursor) is None:
        return unreadable_page_refusal(
            detail=f"{shipped.code}: {shipped.detail}",
            next_action=shipped.next_action,
            offending_input=cursor,
            expected=None if shipped.expected is None else str(shipped.expected),
            observed=None if shipped.observed is None else str(shipped.observed),
        )
    return ReviewRefusal(
        code="comparison_page_reset",
        detail=f"{shipped.code}: {shipped.detail}",
        next_action=REVIEW_PAGE_RESET_NEXT_ACTION,
        offending_input=cursor,
        expected=None if shipped.expected is None else str(shipped.expected),
        observed=None if shipped.observed is None else str(shipped.observed),
    )


def unreadable_page_refusal(
    *,
    detail: str,
    next_action: str,
    offending_input: str,
    expected: str | None = None,
    observed: str | None = None,
) -> ReviewRefusal:
    """One refusal for a cursor that is not the cursor of the collection the request names.

    The owner's own words travel through unchanged, and so does the owner's own next action: the
    reader's mistake is about *which walk* the token belongs to, and the sentence that repairs it is
    the owner's, not a new-generation action this surface would be inventing.
    """

    return ReviewRefusal(
        code="comparison_page_unreadable",
        detail=detail,
        next_action=next_action,
        offending_input=offending_input,
        expected=expected,
        observed=observed,
    )


def comparison_page(
    page: KnowledgeDiffPage,
    *,
    collection: ReviewPagedCollection,
    scope: tuple[str, ...],
    continued_from: str | None,
) -> ReviewCollectionPage:
    """State one comparison window as the review surface's own page.

    ``items_returned`` is cumulative over the comparison's walk and ``items_remaining`` is what
    follows it, which is exactly the pair this page publishes: a later page reports a larger returned
    total rather than the size of its own window, so the three numbers add to the comparison's total
    on every page and a reader paging to the end lands on ``remaining=0``.
    """

    counts = page.counts
    return ReviewCollectionPage(
        collection=collection,
        state="first_page" if continued_from is None else "continued",
        # The comparison measures its whole selection, so its total is the selection's own size on
        # every page and ``items_returned`` is cumulative over the walk.
        total_basis="selection",
        total=counts.items_total,
        returned=counts.items_returned,
        remaining=counts.items_remaining,
        continuation=page.continuation,
        scope=scope,
        continued_from=continued_from,
    )


def reset_comparison_page(
    page: KnowledgeDiffPage,
    refusal_value: ReviewRefusal,
    *,
    collection: ReviewPagedCollection,
    scope: tuple[str, ...],
) -> ReviewCollectionPage:
    """The current comparison's **first page**, served beside the refusal a stale cursor earned.

    This is ICR-R10's failure behavior made one value: a cursor for a moved snapshot is refused with
    an explicit new-generation action, and the page that travels with the refusal is the first page of
    the comparison that is there now -- the last coherent page of this generation -- rather than no
    page at all, an empty page, or a window taken at a position from the generation that moved. The
    walk therefore restarts from a stated position instead of continuing from an unreachable one. The
    refused cursor stays named on the refusal's own ``offending_input``/``expected``/``observed``
    identities, which is where a reader looks for which two comparisons disagreed.
    """

    counts = page.counts
    return ReviewCollectionPage(
        collection=collection,
        state="reset",
        total_basis="selection",
        total=counts.items_total,
        returned=counts.items_returned,
        remaining=counts.items_remaining,
        continuation=page.continuation,
        scope=scope,
        reset=refusal_value,
        # A reset page followed nothing: it is the *first* page of the comparison that is there now,
        # and the cursor it refused is named by the refusal beside it rather than in the field a
        # continued page uses to say where its window was taken.
        continued_from=None,
    )
