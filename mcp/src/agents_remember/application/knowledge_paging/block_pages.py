"""One bounded knowledge block for several seeds (MIK-R02 rule 2, applied to ``read_ar_files``).

``read_ar_files`` asks about several paths at once, and its knowledge block answers every one. The
threshold bounds that *block* -- not each seed's page -- so N seeds never make N thresholds. The
source bytes and onboarding the same response carries are not knowledge rows and are outside it.

Seeds are laid out in request order. Each seed's page is cut so that the whole block -- the pages
already laid out, this one, every block-level summary the envelope adds, and the entries still to
come -- stays within the threshold. Once a seed's next row no longer fits beside what the block
already holds, that seed and every seed after it form the block's *tail*: each tail seed returns
only its counts and a position-0 continuation, which ``knowledge_read`` resumes like any other.
When those deferred entries alone would not fit, the tail collapses into one deferred entry whose
single continuation walks every tail seed in turn, so the block stays within the threshold however
many seeds it answers. A tail that would queue more seeds than one continuation carries
(:data:`~agents_remember.models.knowledge.continuation.MAX_QUEUED_SEEDS` behind its first) is
refused by name, ``seed_queue_exceeded``, instead of being minted into a token. Entries that are
not pages (a refusal, an unseedable path) are carried as they are. A row is flagged
``oversized_row`` only when it does not fit even as a block of its own.

A seed is prepared by the response that owns it: a path seed by the family-complete leaf read
(MIK-R01), an identity seed by the scope read. Each kind collapses into a continuation of its own.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from agents_remember.application.knowledge_leaf.pages import PreparedLeaf
from agents_remember.application.knowledge_paging.pager import PageCut, cut_page
from agents_remember.application.knowledge_paging.scope_pages import PreparedScope
from agents_remember.application.knowledge_paging.threshold import (
    ENVELOPE_RESERVE_TOKENS,
    KNOWLEDGE_PAGE_THRESHOLD_TOKENS,
    response_tokens,
)
from agents_remember.models.knowledge.continuation import MAX_QUEUED_SEEDS

__all__ = ["SEED_QUEUE_EXCEEDED", "BlockEntry", "BlockEnvelope", "bounded_block"]

# The refusal a block tail earns when its seeds would not fit one continuation's queue.
SEED_QUEUE_EXCEEDED = "seed_queue_exceeded"

# A seed ready to be cut: a leaf (path seed) or a scope (identity seed).
Prepared = PreparedLeaf | PreparedScope
# One seed of the block: a prepared seed, or an entry carried as it is.
BlockEntry = Prepared | dict[str, Any]
# The whole block around its laid-out seed entries, including every block-level summary.
BlockEnvelope = Callable[[list[dict[str, Any]]], dict[str, Any]]


def bounded_block(entries: Sequence[BlockEntry], envelope: BlockEnvelope) -> dict[str, Any]:
    """Lay the seeds out in order within one threshold and return the complete block."""

    laid: list[dict[str, Any]] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, Prepared):
            laid.append(entry)
            continue
        # The tail is sized so that at least this seed's next row still has room beside it.
        after = _tail(envelope, [*laid, _first_row(entry)], entries[index + 1 :])

        def render(
            cut: PageCut,
            entry: Prepared = entry,
            after: list[dict[str, Any]] = after,
        ) -> dict[str, Any]:
            return envelope([*laid, entry.render(cut), *after])

        def alone(cut: PageCut, entry: Prepared = entry) -> dict[str, Any]:
            return envelope([entry.render(cut)])

        cut, _body = cut_page(entry.rows, entry.position, render, alone=alone)
        if cut.blocked:
            return envelope([*laid, *_tail(envelope, laid, entries[index:])])
        laid.append(entry.render(cut))
        if not cut.complete:
            return envelope([*laid, *after])
    return envelope(laid)


def _tail(
    envelope: BlockEnvelope, laid: Sequence[dict[str, Any]], tail: Sequence[BlockEntry]
) -> list[dict[str, Any]]:
    """The tail entries as deferred seeds, or collapsed into one when those would not fit."""

    deferred = [_placeholder(entry) for entry in tail]
    budget = KNOWLEDGE_PAGE_THRESHOLD_TOKENS - ENVELOPE_RESERVE_TOKENS
    if response_tokens(envelope([*laid, *deferred])) <= budget:
        return deferred
    return _collapsed_tail(tail)


def _collapsed_tail(tail: Sequence[BlockEntry]) -> list[dict[str, Any]]:
    """The carried entries as they are, then each kind's prepared seeds collapsed into one."""

    carried: list[dict[str, Any]] = [entry for entry in tail if not isinstance(entry, Prepared)]
    leaves = [entry for entry in tail if isinstance(entry, PreparedLeaf)]
    scopes = [entry for entry in tail if isinstance(entry, PreparedScope)]
    return [*carried, *(_collapsed(group) for group in (leaves, scopes) if group)]


def _collapsed(group: Sequence[PreparedLeaf] | Sequence[PreparedScope]) -> dict[str, Any]:
    """One kind's tail seeds as one deferred entry, or the named refusal when they cannot queue."""

    if len(group) - 1 > MAX_QUEUED_SEEDS:
        first = group[0].seed_json
        return {
            "state": "refused",
            "refusalCode": SEED_QUEUE_EXCEEDED,
            "seedCount": len(group),
            "firstSeed": first,
            "refusalDetail": (
                f"the block is full, and its {len(group)} remaining seeds would queue more than "
                f"{MAX_QUEUED_SEEDS} seeds behind one continuation; nothing was read for them. "
                f"Read them in requests of at most {MAX_QUEUED_SEEDS + 1} seeds, starting from "
                f"{first}"
            ),
        }
    if isinstance(group[0], PreparedLeaf):
        leaves = [one for one in group if isinstance(one, PreparedLeaf)]
        return leaves[0].collapsed(leaves[1:])
    scopes = [one for one in group if isinstance(one, PreparedScope)]
    return scopes[0].collapsed(scopes[1:])


def _first_row(entry: Prepared) -> dict[str, Any]:
    """The entry's page holding just its next row (its deferred form when no row remains)."""

    if entry.position >= len(entry.rows):
        return entry.deferred()
    start = entry.position
    return entry.render(PageCut(start=start, end=start + 1, total=len(entry.rows)))


def _placeholder(entry: BlockEntry) -> dict[str, Any]:
    """The smallest form a later entry can take on its own."""

    return entry.deferred() if isinstance(entry, Prepared) else entry
