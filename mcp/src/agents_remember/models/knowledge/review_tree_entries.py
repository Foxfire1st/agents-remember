"""Each realization and proof entry of a tree comparison, located on both code sides (MIK-R31).

The reviewer's central reading path shows every code and test location of the selected family's
members as a focused expression card. A card needs, per entry, what the landed dataset payload does
not carry for a tree comparison: the entry's text ID and kind, its authored role and rationale (a
realization) or facet (a proof), and the range its locator resolves to on each code side by the
MIK-R08 definition 3 rule (a ``symbol`` is the extent the shipped extractor binds uniquely, a
``line_range`` is mapped from the anchor's ``blob`` through the zero-context diff, a ``file`` is every
line). This module fixes that shape; :mod:`agents_remember.application.review_tree_entries` fills it.

One :class:`ReviewTreeEntry` is one entry ID of K_B or K_C. Its two sides are the code base B and the
code candidate C:

* ``recorded`` says whether that side's memory tree holds the entry (K_B for ``before``, K_C for
  ``after``), so an entry this leaf added or retired is named, never inferred; it is ``None`` when
  that memory tree could not be read, because then nobody knows;
* ``state`` is what the range resolution established on that code side: ``resolved`` (a range and
  its content identity), ``unresolved`` (the locator does not resolve there; ``reason`` says why and
  no range is guessed), ``absent`` (the code tree holds no regular file at the path) or
  ``unavailable`` (the side could not be read at all; ``reason`` names what). ``absent`` and
  ``unavailable`` are different facts and are never merged.

The locator a side is resolved with is that side's own entry when the side records it, else the
other side's entry: a card for an added entry still shows the code region it names at B.
``currentness`` is the side's own MIK-R03 entry state, present only where the side records the
entry. ``change`` compares the two resolved ranges' content identities; it is ``undetermined`` when
either side has no resolved range, so a card is never counted as changed or unchanged by guess.

``excerpt`` is the resolved range's text, read from that side's exact blob, bounded by
:data:`EXCERPT_MAX_LINES` and :data:`EXCERPT_MAX_CHARACTERS` (``excerpt_truncated`` then says the text
is a prefix of the range). An ``unchanged`` entry carries its excerpt on the ``after`` side only: the
two ranges have one content identity, so the text is the same bytes and is shown once.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from agents_remember.models.knowledge.base import (
    GIT_OBJECT_PATTERN,
    PATH_MAX_LENGTH,
    PROSE_MAX_LENGTH,
    REFERENCE_MAX_LENGTH,
    KnowledgeModel,
)

__all__ = [
    "EXCERPT_MAX_CHARACTERS",
    "EXCERPT_MAX_LINES",
    "ReviewEntryChange",
    "ReviewEntryRangeState",
    "ReviewTreeEntry",
    "ReviewTreeEntrySide",
]

ReviewEntryRangeState = Literal["resolved", "unresolved", "absent", "unavailable"]
ReviewEntryChange = Literal["changed", "unchanged", "undetermined"]
EXCERPT_MAX_LINES = 400
EXCERPT_MAX_CHARACTERS = 48_000


class ReviewTreeEntrySide(KnowledgeModel):
    """One entry on one code side: whether that memory tree records it, and where it lands."""

    recorded: bool | None
    state: ReviewEntryRangeState
    path: str = Field(min_length=1, max_length=PATH_MAX_LENGTH)
    blob: str | None = Field(default=None, pattern=GIT_OBJECT_PATTERN)
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    content: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    excerpt: str | None = Field(default=None, max_length=EXCERPT_MAX_CHARACTERS)
    excerpt_truncated: bool = False
    reason: str | None = Field(default=None, max_length=PROSE_MAX_LENGTH)
    role: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    facet: str | None = Field(default=None, max_length=PROSE_MAX_LENGTH)
    rationale: str | None = Field(default=None, max_length=PROSE_MAX_LENGTH)
    currentness: Literal["current", "stale", "unverifiable"] | None = None
    currentness_reason: str | None = Field(default=None, max_length=PROSE_MAX_LENGTH)


class ReviewTreeEntry(KnowledgeModel):
    """One realization or proof entry of the comparison, on the code base and the code candidate.

    ``invariant_key`` is the identity the landed review payload addresses the invariant by (the
    index projection's name-based UUID), so a renderer joins an entry to a family member by value
    rather than by a display label.
    """

    id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    kind: Literal["realization", "proof"]
    invariant: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    invariant_key: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    before: ReviewTreeEntrySide
    after: ReviewTreeEntrySide
    change: ReviewEntryChange
