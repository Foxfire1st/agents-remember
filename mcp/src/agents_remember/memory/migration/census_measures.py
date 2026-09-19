"""The truth-coverage census: ``N = T + F + U + P``, the six measures, and the four-axis slices.

This module is ``Doc12``'s apparatus as arithmetic, and the arithmetic is deliberately small. What
matters is the separation it keeps, because every prohibition in the packet is a prohibition on
collapsing one of these cells into another:

* ``N`` is a claim count over **assessable** claims. The eligibility rule is stated once, here, and it
  is the same rule every comparison uses: a census observation enters the cohort exactly when its
  record kind is ``census_claim``, its ``applicability`` is ``assessable`` and it carries a claim text.
  A piece recorded ``non_claim`` or ``historical_non_applicable`` is counted in its own bucket and does
  **not** enter ``N`` -- which is what makes ``Doc12``'s Example 3 and requirement 5.2 read alike.
* ``T``, ``F`` and ``U`` come from a curator's authored assessment and from nothing else. This module
  never derives one: an assessment whose disposition is unresolved feeds ``U``; a claim with **no**
  assessment feeds ``P``. There is no code path from an import outcome to ``T``, ``F`` or ``U``, and
  ``unassessed`` is derivable only as "no assessment exists".
* ``K`` is **never read here**. The coverage measure takes the reference inventory's size as an
  argument and returns ``None`` when no independently reviewed inventory supplies it. A ``K`` this
  module could compute would be derived from the corpus being measured, which is the one thing
  ``Doc12:110`` forbids outright.

The six measures are reported **together** and never composed into one number: ``T / N``,
``T / (T + F)`` -- which "must be accompanied by unresolved and unassessed counts" -- ``F / N``,
``(T + F + U) / N``, ``C / K`` and realization coverage. A zero denominator is reported as
``not_applicable`` with the counts beside it rather than as a perfect score, and a ratio that could be
read as a verdict is paired with the counts that qualify it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from agents_remember.models.knowledge.census import (
    CLASSIFIED_CLAIM_KINDS,
    COHORT_APPLICABILITY,
    CensusClaim,
    CensusDispositionKind,
    CensusInventoryRow,
)

# The four cells of the accounting, as one closed vocabulary so a measure names a cell rather than a
# letter. ``pending`` and ``unresolved`` are the two that a composite index would hide, which is why
# the report always carries both counts.
CellName = Literal["T", "F", "U", "P"]
CELL_NAMES: tuple[CellName, ...] = ("T", "F", "U", "P")

# ``Doc12:99-104``'s six measure names, in the doc's own order. The report carries an entry per name --
# including the ones it cannot compute -- so a missing measure is visible as a missing measure rather
# than as an absent row.
MeasureName = Literal[
    "verified_truth_share",
    "correctness_among_resolved_claims",
    "contradiction_share",
    "assessment_completion",
    "relevant_truth_coverage",
    "realization_coverage",
]
MEASURE_NAMES: tuple[MeasureName, ...] = (
    "verified_truth_share",
    "correctness_among_resolved_claims",
    "contradiction_share",
    "assessment_completion",
    "relevant_truth_coverage",
    "realization_coverage",
)

# The four slice axes ``Doc12:118`` names. Each slice key is a recorded association, never a derived
# one: the family and the route come from the records the census read, and the category from the
# curator's authored claim kind.
SliceAxis = Literal["family", "category", "consequence", "source_route"]
SLICE_AXES: tuple[SliceAxis, ...] = ("family", "category", "consequence", "source_route")

# The consequence axis's two values. A claim either carries a consequence attribution or it does not,
# and "not recorded" is its own value rather than a missing row: a slice breakdown that silently
# dropped the unattributed mass would report a coverage the corpus does not have.
CONSEQUENCE_UNRECORDED = "unrecorded"


@dataclass(frozen=True)
class Separation:
    """One ``N = T + F + U + P`` separation, with the two out-of-cohort counts beside it.

    ``non_claim`` and ``historical_non_applicable`` are reported here rather than inside ``N`` because
    ``Doc12:95`` requires those pieces to carry an explicit disposition instead of vanishing -- and a
    count that appears nowhere is indistinguishable from one that vanished.
    """

    total: int
    supported: int
    contradicted: int
    unresolved: int
    pending: int
    non_claim: int = 0
    historical_non_applicable: int = 0

    def __post_init__(self) -> None:
        if self.total != self.supported + self.contradicted + self.unresolved + self.pending:
            raise ValueError(
                "the claim cohort must equal T + F + U + P; a separation whose own cells disagree "
                "would report an accounting that does not close"
            )

    @property
    def cell_counts(self) -> Mapping[CellName, int]:
        """Return the four cells by name, so a report never has to remember the field order."""

        return {
            "T": self.supported,
            "F": self.contradicted,
            "U": self.unresolved,
            "P": self.pending,
        }


@dataclass(frozen=True)
class Measure:
    """One measure's value, or the recorded reason it could not be computed."""

    name: MeasureName
    numerator: int | None
    denominator: int | None
    state: Literal["computed", "not_applicable", "not_measurable"]
    note: str

    @property
    def value(self) -> float | None:
        """Return the ratio, or ``None`` when the measure has no value.

        ``None`` rather than a sentinel number: the packet requires that a zero denominator be
        reported as *not applicable or not yet measurable*, and a zero or a one in its place is exactly
        the automatic perfect score ``Doc12:106`` refuses.
        """

        if self.state != "computed" or not self.denominator:
            return None
        return self.numerator / self.denominator if self.numerator is not None else None

    def render(self) -> str:
        """Render the measure with its counts, never as a bare percentage."""

        if self.value is None:
            return f"{self.name}: {self.state} ({self.note})"
        return f"{self.name}: {self.numerator}/{self.denominator} = {self.value:.6f} ({self.note})"


@dataclass(frozen=True)
class ClaimOccurrence:
    """One claim as extracted, with the identity that decides whether it is a repeat.

    ``unique_key`` is the text normalised for comparison -- case-folded and inner-whitespace-collapsed
    -- so "the store is append-only" on forty cards is one unique claim with forty occurrences. The
    **original** text is what the census stores and reports; the key exists only for this count, and
    it is a comparison key rather than a second claim identity.
    """

    claim_id: str
    unique_key: str
    original_text: str
    location: str
    artifact_path: str

    @staticmethod
    def key_for(text: str) -> str:
        """Return the comparison key one claim text reduces to."""

        return " ".join(text.split()).casefold()


@dataclass(frozen=True)
class UniqueOccurrenceCounts:
    """Both counts ``Doc12:112`` requires, always reported together.

    Counting only occurrences inflates; counting only uniques hides the incorrect local occurrence and
    the missing second realization. Neither is reported without the other, and no displayed coverage
    figure is computed from occurrences.
    """

    unique_claims: int
    occurrences: int
    repeats: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.occurrences < self.unique_claims:
            raise ValueError(
                "an occurrence count below the unique count would mean a unique claim with no "
                "occurrence, which is not a state this census can produce"
            )


@dataclass(frozen=True)
class SliceReport:
    """One slice's separation: the same four-way accounting, keyed by one axis value.

    ``Doc12:118`` makes slices part of the output rather than an optional breakdown, so every slice
    carries the full separation and a global-only number does not satisfy the requirement even when it
    is correct.
    """

    axis: SliceAxis
    key: str
    separation: Separation
    unique_claims: int
    occurrences: int


@dataclass(frozen=True)
class CensusReport:
    """The whole census output: the accounting, the six measures, both counts and the four slices."""

    baseline_key: tuple[str, str]
    separation: Separation
    measures: tuple[Measure, ...]
    unique_occurrence: UniqueOccurrenceCounts
    slices: tuple[SliceReport, ...]
    inventory_counts: Mapping[str, int]
    reference_inventory_id: str | None
    reference_inventory_reviewer: str | None

    def measure(self, name: MeasureName) -> Measure:
        """Return one measure by name, refusing a name this report does not carry."""

        for measure in self.measures:
            if measure.name == name:
                return measure
        raise KeyError(f"{name!r} is not one of the reported measures {MEASURE_NAMES}")

    def slice_axis(self, axis: SliceAxis) -> tuple[SliceReport, ...]:
        """Return one axis's slices in their recorded key order."""

        return tuple(item for item in self.slices if item.axis == axis)

    def render(self) -> str:
        """Render the report as lines of facts, measures together and never as one score."""

        lines = [
            f"baseline: code {self.baseline_key[0]} memory {self.baseline_key[1]}",
            (
                "accounting: N = T + F + U + P = "
                f"{self.separation.total} = {self.separation.supported} + "
                f"{self.separation.contradicted} + {self.separation.unresolved} + "
                f"{self.separation.pending}"
            ),
            (
                f"out of cohort: non_claim {self.separation.non_claim}, "
                f"historical_non_applicable {self.separation.historical_non_applicable}"
            ),
            (
                f"unique claims {self.unique_occurrence.unique_claims}, "
                f"occurrences {self.unique_occurrence.occurrences}"
            ),
        ]
        lines.extend(measure.render() for measure in self.measures)
        for axis in SLICE_AXES:
            for item in self.slice_axis(axis):
                lines.append(
                    f"slice {axis}={item.key}: N={item.separation.total} "
                    f"T={item.separation.supported} F={item.separation.contradicted} "
                    f"U={item.separation.unresolved} P={item.separation.pending} "
                    f"(unique {item.unique_claims}, occurrences {item.occurrences})"
                )
        return "\n".join(lines)


def claim_enters_cohort(claim: CensusClaim) -> bool:
    """Return whether one claim is eligible for the cohort ``N``.

    This is the eligibility rule ``CR21-4`` requires be stated, and it is stated as a predicate rather
    than as prose in three places. A claim enters when it is assessable and carries text. Its
    ``claim_kind`` is deliberately **not** part of the test: ``unclassified`` is a reportable state that
    counts as unresolved, and a claim a curator has classified as historical still enters the cohort --
    what keeps it out of the truth measures is its applicability and its assessment, not its kind.
    """

    return claim.payload.applicability == COHORT_APPLICABILITY and bool(
        claim.payload.claim_text.strip()
    )


def assessment_cell(
    *,
    assessed: bool,
    disposition: Literal["concern_found", "no_concern_found", "unresolved"] | None,
) -> CellName:
    """Return the cell one claim's assessment state belongs to, and derive nothing else.

    The four cases are the whole mapping, and the ``None`` disposition case is the one that matters:
    a claim with no assessment is ``P`` -- pending -- and never ``T``, never ``F`` and never ``U``.
    ``unassessed`` is the only status this function can derive, and it derives it from the *absence* of
    an assessment rather than from anything an import did.
    """

    if not assessed or disposition is None:
        return "P"
    if disposition == "no_concern_found":
        return "T"
    if disposition == "concern_found":
        return "F"
    return "U"


def separation_of(
    cells: Iterable[CellName], *, non_claim: int = 0, historical: int = 0
) -> Separation:
    """Build one separation from the cells its cohort claims were assigned to."""

    counts = {name: 0 for name in CELL_NAMES}
    for cell in cells:
        counts[cell] += 1
    return Separation(
        total=sum(counts.values()),
        supported=counts["T"],
        contradicted=counts["F"],
        unresolved=counts["U"],
        pending=counts["P"],
        non_claim=non_claim,
        historical_non_applicable=historical,
    )


def unique_occurrence_counts(claims: Sequence[ClaimOccurrence]) -> UniqueOccurrenceCounts:
    """Return both counts over a set of extracted claim occurrences."""

    repeats: dict[str, int] = {}
    for occurrence in claims:
        repeats[occurrence.unique_key] = repeats.get(occurrence.unique_key, 0) + 1
    return UniqueOccurrenceCounts(
        unique_claims=len(repeats),
        occurrences=len(claims),
        repeats={key: count for key, count in sorted(repeats.items()) if count > 1},
    )


def _ratio(name: MeasureName, numerator: int, denominator: int, note: str) -> Measure:
    """Return one computed measure, or the recorded reason it is not applicable."""

    if denominator == 0:
        return Measure(
            name=name,
            numerator=numerator,
            denominator=denominator,
            state="not_applicable",
            note="a zero denominator means not applicable or not yet measurable, not a perfect score",
        )
    return Measure(
        name=name, numerator=numerator, denominator=denominator, state="computed", note=note
    )


def compute_measures(
    separation: Separation,
    *,
    reference_inventory_size: int | None,
    represented_truths: int | None,
    known_realizations: int | None,
    reference_realizations: int | None,
) -> tuple[Measure, ...]:
    """Return the six measures, in ``Doc12:99-104``'s order, each with its counts.

    ``T / (T + F)`` is computed only from the resolved subset, and its note names the unresolved and
    unassessed counts that ``Doc12:100`` requires accompany it -- so a caller cannot report the
    correctness figure without the two counts that qualify it.

    The coverage pair takes its denominator as an argument and returns ``not_measurable`` when the
    argument is ``None``. There is deliberately no branch here that could compute ``K`` from anything
    this module holds: this function is given the reference inventory's size or it is given nothing.
    """

    supported, contradicted = separation.supported, separation.contradicted
    unresolved, pending = separation.unresolved, separation.pending
    measures = [
        _ratio(
            "verified_truth_share",
            supported,
            separation.total,
            "the fraction of the declared cohort established as supported so far; not an estimate "
            "that the remaining claims are false",
        ),
        _ratio(
            "correctness_among_resolved_claims",
            supported,
            supported + contradicted,
            f"accuracy within the resolved subset; accompanied by U={unresolved} and P={pending}",
        ),
        _ratio(
            "contradiction_share",
            contradicted,
            separation.total,
            "the fraction of the cohort established as contradicted",
        ),
        _ratio(
            "assessment_completion",
            supported + contradicted + unresolved,
            separation.total,
            "the fraction assessed, including cases where the evidence remains inconclusive; "
            f"P={pending} is the unassessed remainder",
        ),
        _coverage_measure(
            "relevant_truth_coverage",
            numerator=represented_truths,
            denominator=reference_inventory_size,
            note=(
                "the fraction of an independently reviewed reference inventory the system "
                "accurately represents; reported only relative to that inventory, never as a claim "
                "to have enumerated every possible truth about the program"
            ),
        ),
        _coverage_measure(
            "realization_coverage",
            numerator=known_realizations,
            denominator=reference_realizations,
            note=(
                "known, correctly attributed realizations over the reference inventory's "
                "realizations: whether the locations needed to preserve the selected truths are "
                "exposed"
            ),
        ),
    ]
    return tuple(measures)


def _coverage_measure(
    name: MeasureName, *, numerator: int | None, denominator: int | None, note: str
) -> Measure:
    """Return one coverage measure, refusing to invent a denominator it was not given."""

    if denominator is None or numerator is None:
        return Measure(
            name=name,
            numerator=numerator,
            denominator=denominator,
            state="not_measurable",
            note=(
                "no independently reviewed reference inventory supplies this denominator; the "
                "coverage denominator cannot come from the corpus being measured, so this measure "
                "stays unmeasured rather than approximated"
            ),
        )
    return _ratio(name, numerator, denominator, note)


def slice_claims(
    claims: Sequence[CensusClaim],
    *,
    axis: SliceAxis,
    keys_of: Mapping[str, str],
    occurrences: Mapping[str, ClaimOccurrence],
) -> tuple[SliceReport, ...]:
    """Return one axis's slices, each carrying the same four-way separation.

    ``keys_of`` maps a claim's record id to its **recorded** key on this axis. A claim absent from the
    mapping is keyed by the axis's own unrecorded state rather than being dropped: a breakdown that
    silently omitted what it could not key would report a coverage the corpus does not have.
    """

    grouped: dict[str, list[CensusClaim]] = {}
    for claim in claims:
        if not claim_enters_cohort(claim):
            continue
        key = keys_of.get(claim.record_id) or _unrecorded_key(axis)
        grouped.setdefault(key, []).append(claim)
    reports: list[SliceReport] = []
    for key in sorted(grouped):
        members = grouped[key]
        cells: list[CellName] = [cell_of(claim) for claim in members]
        repeated = [
            occurrences[claim.record_id] for claim in members if claim.record_id in occurrences
        ]
        reports.append(
            SliceReport(
                axis=axis,
                key=key,
                separation=separation_of(cells),
                unique_claims=unique_occurrence_counts(repeated).unique_claims,
                occurrences=len(repeated),
            )
        )
    return tuple(reports)


def _unrecorded_key(axis: SliceAxis) -> str:
    """Return the slice key a claim with no recorded association on this axis carries."""

    return CONSEQUENCE_UNRECORDED if axis == "consequence" else f"{axis}:unrecorded"


def cell_of(claim: CensusClaim) -> CellName:
    """Return the cell a claim's **stored** assessment state places it in.

    The claim's disposition travels on its evidence relations, and the rule is the one
    :func:`assessment_cell` states -- this function only reads it out. A claim that carries no
    evidence relation at all, or only ``unassessed`` ones, has no assessment and is ``P``. A claim
    whose stored verdict is a curator's authored ``no_concern_found`` or ``concern_found`` is ``T`` or
    ``F``. A stored ``unresolved`` is ``U``. There is no branch here that could produce a verdict the
    pipeline never received, and ``assessed`` with no disposition recorded is reported ``U`` rather
    than assumed supported: an unread verdict is not evidence of truth.
    """

    dispositions = [evidence.assessment_disposition for evidence in claim.evidence]
    recorded = [item for item in dispositions if item is not None]
    if not recorded:
        return "P"
    if "concern_found" in recorded:
        return "F"
    if "unresolved" in recorded:
        return "U"
    return "T"


def inventory_counts(rows: Sequence[CensusInventoryRow]) -> Mapping[str, int]:
    """Return the inventory's counts by parse outcome, and the absent-surface count separately."""

    counts: dict[str, int] = {}
    for row in rows:
        counts[row.payload.outcome] = counts.get(row.payload.outcome, 0) + 1
        counts[f"inventory_state:{row.payload.inventory_state}"] = (
            counts.get(f"inventory_state:{row.payload.inventory_state}", 0) + 1
        )
    return dict(sorted(counts.items()))


def claim_kind_counts(claims: Sequence[CensusClaim]) -> Mapping[str, int]:
    """Return how many claims carry each claim kind, including ``unclassified`` and the four kinds."""

    counts = {kind: 0 for kind in ("unclassified", *CLASSIFIED_CLAIM_KINDS)}
    for claim in claims:
        counts[claim.payload.claim_kind] = counts.get(claim.payload.claim_kind, 0) + 1
    return counts


def disposition_counts(dispositions: Sequence[object]) -> Mapping[str, int]:
    """Return how many artifacts carry each migration disposition, including ``unmapped``.

    The ``unmapped`` bucket is the one this function exists to keep visible: ``KS-R21@v1`` §2.2 makes
    it "a named state" appearing "in the census's dispositions", so a report that showed only imported
    and dispositioned counts would hide exactly the artifacts the importer refused to place.
    """

    counts: dict[str, int] = {}
    for disposition in dispositions:
        kind: CensusDispositionKind = disposition.payload.disposition_kind  # type: ignore[attr-defined]
        counts[kind] = counts.get(kind, 0) + 1
    return dict(sorted(counts.items()))
