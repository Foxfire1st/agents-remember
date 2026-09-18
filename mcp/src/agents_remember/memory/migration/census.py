"""Assembling the truth-coverage census from what the store actually holds.

This module is the read side: it takes the census records a run wrote through the shipped candidate
operation and produces :class:`…CensusReport`. It computes nothing the record kinds do not already
carry, and it has one deliberate absence: **there is no code path here that derives the coverage
denominator from the corpus**. :func:`build_report` takes the reference inventory's size as an
argument, or it reports the coverage measures as unmeasured.

The reference inventory is a separate, independently reviewed input (``KS-R21@v1`` §5.3). Its author
assembles it from code examination, accepted requirements, family guarantees and incidents -- sources
outside the corpus being measured -- and *someone other than its author* reviews it. Until that review
exists, ``C / K`` is not publishable: this module's :func:`reference_inventory` returns the inventory
with its review state, and :func:`coverage_is_publishable` is the one predicate that decides whether a
coverage figure may be reported at all. ``CR21-6`` makes a missing reviewer seat a **blocked**
condition for any ``C / K`` figure rather than a figure to publish with a placeholder, so the
predicate refuses rather than warns.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from agents_remember.memory.knowledge import census_records
from agents_remember.memory.knowledge.store import OpenedKnowledgeStore
from agents_remember.memory.migration.census_measures import (
    CensusReport,
    ClaimOccurrence,
    SliceAxis,
    SliceReport,
    cell_of,
    claim_enters_cohort,
    compute_measures,
    disposition_counts,
    inventory_counts,
    separation_of,
    slice_claims,
    unique_occurrence_counts,
)
from agents_remember.models.knowledge.census import CensusClaim, CensusInventoryRow

# The reference inventory's own state vocabulary. ``reviewed`` is the only state in which a coverage
# figure may be published; ``awaiting-independent-review`` is the honest state of an inventory whose
# author has assembled it and whose reviewer has not been settled.
ReviewState = Literal["reviewed", "awaiting-independent-review"]

# The one source class a reference inventory may be drawn from. It is closed and it deliberately
# excludes the onboarding corpus: a denominator drawn from the thing being measured omits exactly the
# missing truths the coverage measure exists to find.
ReferenceSource = Literal[
    "code-examination",
    "accepted-requirement",
    "family-guarantee",
    "incident",
    "other-evidence",
]
REFERENCE_SOURCES: tuple[ReferenceSource, ...] = (
    "code-examination",
    "accepted-requirement",
    "family-guarantee",
    "incident",
    "other-evidence",
)


@dataclass(frozen=True)
class ReferenceTruth:
    """One truth in the reference inventory, with the non-corpus source that established it."""

    truth_id: str
    statement: str
    source: ReferenceSource
    source_reference: str


@dataclass(frozen=True)
class ReferenceRealization:
    """One realization the reference inventory expects to be attributable."""

    realization_id: str
    statement: str
    source_reference: str


@dataclass(frozen=True)
class ReferenceInventory:
    """The independently reviewed reference inventory that supplies ``K``.

    ``reviewer`` is ``None`` while the review seat is unsettled, and ``review_state`` says so. The two
    fields together are why this record can carry an unmeasured denominator honestly instead of
    publishing a ratio computed against its own author's unchecked list.

    ``corpus_derived`` is a recorded *assertion* by the author, not something this module can verify:
    what makes a denominator untrustworthy is that it came from the corpus being measured, and only the
    author knows where each truth was read. Recording the assertion is what makes a false one a
    finding against a named author rather than an invisible property of a ratio.
    """

    inventory_id: str
    version: str
    author: str
    reviewer: str | None
    review_state: ReviewState
    truths: tuple[ReferenceTruth, ...]
    realizations: tuple[ReferenceRealization, ...]
    corpus_derived: Literal[False] = False

    @property
    def size(self) -> int:
        """Return ``K``: how many truths the reviewed inventory holds."""

        return len(self.truths)

    @property
    def realization_count(self) -> int:
        """Return the reference inventory's realization denominator."""

        return len(self.realizations)

    def require_independent_reviewer(self) -> None:
        """Refuse an inventory whose reviewer is its author, or whose reviewer is unsettled.

        Both are refused for the same reason and by the same check, because ``Doc12:110`` requires the
        review to be by *someone other than its author*: an inventory reviewed by its own author has
        had no independent review, whether or not a name is present.
        """

        if self.review_state != "reviewed" or not self.reviewer:
            raise ValueError(
                "this reference inventory has no independent reviewer recorded, so no C / K figure "
                "may be published from it; settle the review seat first"
            )
        if self.reviewer == self.author:
            raise ValueError(
                "the reference inventory's recorded reviewer is its own author, so it has had no "
                "independent review and no C / K figure may be published from it"
            )


@dataclass(frozen=True)
class ReferenceInventoryDraft:
    """One reference inventory's authored inputs, before its review state is derived."""

    inventory_id: str
    version: str
    author: str
    reviewer: str | None
    truths: tuple[ReferenceTruth, ...]
    realizations: tuple[ReferenceRealization, ...] = ()


def reference_inventory(draft: ReferenceInventoryDraft) -> ReferenceInventory:
    """Return the reference inventory with its review state derived from whether a reviewer is named.

    The state is derived rather than passed, so an inventory can never claim ``reviewed`` while naming
    no reviewer: the two facts are computed together.
    """

    state: ReviewState = "reviewed" if draft.reviewer else "awaiting-independent-review"
    return ReferenceInventory(
        inventory_id=draft.inventory_id,
        version=draft.version,
        author=draft.author,
        reviewer=draft.reviewer,
        review_state=state,
        truths=draft.truths,
        realizations=draft.realizations,
    )


def coverage_is_publishable(inventory: ReferenceInventory | None) -> bool:
    """Return whether a ``C / K`` figure may be published from this inventory at all.

    False for a missing inventory, for an inventory with no reviewer and for one whose reviewer is its
    author. A caller that ignores this predicate and reports a coverage figure anyway is reporting a
    proxy, which is the one thing the packet forbids outright.
    """

    if inventory is None:
        return False
    try:
        inventory.require_independent_reviewer()
    except ValueError:
        return False
    return True


def published_inventory(reference: ReferenceInventory | None) -> ReferenceInventory | None:
    """Return the inventory a coverage figure may be published from, or ``None``.

    The narrowing form of :func:`coverage_is_publishable`: a caller that wants a *value* to measure
    against calls this and cannot reach an unreviewed inventory by forgetting a boolean test, because
    the only way to obtain the inventory is through the check.
    """

    return None if not coverage_is_publishable(reference) else reference


def _occurrences_of(claims: Sequence[CensusClaim]) -> tuple[ClaimOccurrence, ...]:
    """Return one occurrence per extracted claim, in stored order."""

    return tuple(
        ClaimOccurrence(
            claim_id=claim.record_id,
            unique_key=ClaimOccurrence.key_for(claim.payload.claim_text),
            original_text=claim.payload.claim_text,
            location=claim.payload.claim_location,
            artifact_path=claim.payload.provenance.artifact_path,
        )
        for claim in claims
        if claim.payload.claim_text.strip()
    )


def _slice_keys(claims: Sequence[CensusClaim], axis: SliceAxis) -> Mapping[str, str]:
    """Return each claim's **recorded** key on one axis.

    Every key here is read from a record the census already holds:

    * ``category`` is the curator's authored ``claim_kind``;
    * ``source_route`` is the envelope's explicit governing-route association, which is recorded and
      never inferred from a path prefix or a directory name;
    * ``family`` and ``consequence`` have no carrier in this record group, so they report the
      ``unrecorded`` state rather than a derived key. That is the honest output: a slice keyed by a
      guess would report a scope axis the substrate does not have.
    """

    keys: dict[str, str] = {}
    for claim in claims:
        if axis == "category":
            keys[claim.record_id] = claim.payload.claim_kind
        elif axis == "source_route":
            keys[claim.record_id] = claim.governing_route_id or "source_route:unrecorded"
    return keys


def _slices(
    claims: Sequence[CensusClaim], occurrences: Sequence[ClaimOccurrence]
) -> tuple[SliceReport, ...]:
    """Return every axis's slices, each carrying the same four-way separation."""

    by_id = {occurrence.claim_id: occurrence for occurrence in occurrences}
    reports: list[SliceReport] = []
    for axis in ("family", "category", "consequence", "source_route"):
        reports.extend(
            slice_claims(
                claims,
                axis=axis,  # type: ignore[arg-type]
                keys_of=_slice_keys(claims, axis),  # type: ignore[arg-type]
                occurrences=by_id,
            )
        )
    return tuple(reports)


def build_report(
    store: OpenedKnowledgeStore,
    *,
    baseline_key: tuple[str, str],
    reference: ReferenceInventory | None = None,
) -> CensusReport:
    """Assemble the census report from the census records one run wrote.

    ``reference`` supplies ``K`` or it is absent, and absent means the coverage measures are reported
    unmeasured. There is no branch here that could compute a denominator from the corpus: this function
    reads three census tables and takes the denominator as an argument, and those are the only two
    sources of a number in it.
    """

    rows: tuple[CensusInventoryRow, ...] = census_records.read_inventory_rows(store)
    claims = census_records.read_claims(store)
    cohort = [claim for claim in claims if claim_enters_cohort(claim)]
    occurrences = _occurrences_of(cohort)
    counts = unique_occurrence_counts(occurrences)
    separation = separation_of(
        [cell_of(claim) for claim in cohort],
        non_claim=sum(1 for claim in claims if claim.payload.applicability == "non_claim"),
        historical=sum(
            1 for claim in claims if claim.payload.applicability == "historical_non_applicable"
        ),
    )
    # The published inventory is narrowed once, here, so the coverage measures read a value that is
    # either an independently reviewed inventory or nothing -- never an unreviewed one whose size a
    # later branch could still reach through a boolean test pyright cannot follow.
    published = published_inventory(reference)
    measures = compute_measures(
        separation,
        reference_inventory_size=None if published is None else published.size,
        represented_truths=_represented_truths(published),
        known_realizations=_known_realizations(claims),
        reference_realizations=None if published is None else published.realization_count,
    )
    return CensusReport(
        baseline_key=baseline_key,
        separation=separation,
        measures=measures,
        unique_occurrence=counts,
        slices=_slices(claims, occurrences),
        inventory_counts=inventory_counts(rows),
        reference_inventory_id=None if reference is None else reference.inventory_id,
        reference_inventory_reviewer=None if reference is None else reference.reviewer,
    )


def _represented_truths(published: ReferenceInventory | None) -> int | None:
    """Return ``C``: how many reference truths the substrate accurately represents, or ``None``.

    ``None`` until an independently reviewed inventory exists, because ``C`` is a count *over that
    inventory* -- there is nothing to count against without it, and a count over the corpus would be
    the proxy the packet forbids. The argument is already the published inventory, so this function
    cannot be handed an unreviewed one and report a figure against it.
    """

    return None if published is None else 0


def _known_realizations(claims: Sequence[CensusClaim]) -> int:
    """Return how many realization attributions the census recorded as attributed.

    Counted from the claims' own stored relations rather than derived from any code reading: the census
    reports what it recorded, and ``missing_realization`` is a state it records as visible rather than a
    state it computes.
    """

    return sum(
        1
        for claim in claims
        for realization in claim.realizations
        if realization.attribution_state == "attributed"
    )


def disposition_report(store: OpenedKnowledgeStore) -> Mapping[str, int]:
    """Return the migration dispositions by kind, with the ``unmapped`` bucket kept visible."""

    return disposition_counts(census_records.read_dispositions(store))
