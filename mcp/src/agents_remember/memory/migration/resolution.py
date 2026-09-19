"""Reference resolution with its three recorded states, and mechanical mismatch reporting.

Two rules govern everything in this module, and both are refusals of a capability rather than
features of it:

* **Resolution has exactly three states** -- ``resolved``, ``unresolved`` and ``ambiguous`` -- and all
  three are *reportable*. None is a refusal of the run and none is silently defaulted
  (``KS-R21@v1`` §3.1).
* **No reference is repaired by resemblance** (§3.2). There is no fuzzy match, no prefix fallback, no
  canonicalisation step and no function anywhere here that creates a row to receive a dangling
  reference. :func:`resolve_reference` compares exact spellings against a caller-supplied candidate set
  and returns one of three values. A reference that matched nothing stays unmatched, which is the whole
  point: it is the fact the census exists to report.

The same boundary governs the mismatch report. A mismatch is reported as **the mechanical fact**
-- which artifact, which reference, which baseline, what was observed to contradict what -- and the
report has no field that could hold a semantic verdict. ``Doc13:460`` gives the four-way distinction
between an implementation defect, incorrect attribution, changed intent needing approval and an
unsupported claim to a **curator**, so §3.4 requires the pipeline to report *that* something is
contradicted and *where*, and never *which* of the four it is.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from agents_remember.memory.migration.baseline import FrozenBaseline

# The three resolution states, closed. ``ReportableReferenceState`` is a separate name because the
# vocabulary is used in two places -- a single resolution and an aggregate count -- and a second
# literal at the second site is how two spellings of one state start to drift.
ReferenceState = Literal["resolved", "unresolved", "ambiguous"]
REFERENCE_STATES: tuple[ReferenceState, ...] = ("resolved", "unresolved", "ambiguous")

# The mechanical mismatch kinds. Every one is a *fact* two observations disagree about; none is a
# judgement about which observation is right, and none could be, because this module never reads the
# code a claim is about.
MismatchKind = Literal[
    "declared_source_absent",
    "metadata_contradicts_front_matter",
    "anchor_claimed_by_two_records",
    "route_reference_ambiguous",
    "cited_revision_absent",
    "artifact_unreadable",
]
MISMATCH_KINDS: tuple[MismatchKind, ...] = (
    "declared_source_absent",
    "metadata_contradicts_front_matter",
    "anchor_claimed_by_two_records",
    "route_reference_ambiguous",
    "cited_revision_absent",
    "artifact_unreadable",
)


@dataclass(frozen=True)
class Reference:
    """One reference read out of an artifact, with where it was read from.

    ``reference_text`` is stored **verbatim**. Normalising it here -- lower-casing, trimming
    separators, resolving a relative form against the artifact's own directory -- would be the
    canonicalisation §3.2 forbids, because a reference canonicalised into a match is a reference that
    was repaired rather than resolved.
    """

    reference_text: str
    reference_kind: str
    artifact_path: str
    location: str
    baseline: FrozenBaseline


@dataclass(frozen=True)
class Resolution:
    """One reference's resolved state and the exact candidates it was compared against."""

    reference: Reference
    state: ReferenceState
    candidates: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.state == "resolved" and len(self.candidates) != 1:
            raise ValueError(
                "a resolved reference names exactly one candidate; a resolution carrying a "
                "different number would report a state its own candidate set contradicts"
            )
        if self.state == "unresolved" and self.candidates:
            raise ValueError(
                "an unresolved reference names nothing; a resolution carrying candidates reports a "
                "state its own candidate set contradicts"
            )
        if self.state == "ambiguous" and len(self.candidates) < 2:
            raise ValueError(
                "an ambiguous reference names more than one candidate; a resolution carrying fewer "
                "reports a state its own candidate set contradicts"
            )


@dataclass(frozen=True)
class Mismatch:
    """One mechanical mismatch: the artifact, the reference, the baseline and the contradiction.

    There is no verdict field, by construction. The four dispositions ``Doc13:460`` names are a
    curator's authored work and are stored as that curator's record, not here.
    """

    mismatch_kind: MismatchKind
    artifact_path: str
    reference_text: str
    baseline: FrozenBaseline
    observed: str
    expected: str

    def render(self) -> str:
        """Render the fact, so a report can quote it without adding an interpretation."""

        return (
            f"{self.mismatch_kind}: {self.artifact_path} -> {self.reference_text!r} "
            f"at {self.baseline.code_tree_id[:12]}/{self.baseline.memory_tree_id[:12]}: "
            f"observed {self.observed!r}, expected {self.expected!r}"
        )


@dataclass(frozen=True)
class ReferenceCounts:
    """The resolved/unresolved/ambiguous partition, with the three totals that must agree."""

    resolved: int
    unresolved: int
    ambiguous: int

    @property
    def total(self) -> int:
        """Return how many references were resolved, so a caller can check the partition is total."""

        return self.resolved + self.unresolved + self.ambiguous


def resolve_reference(reference: Reference, candidates: Mapping[str, Sequence[str]]) -> Resolution:
    """Resolve one reference against an exact candidate set, in one of three recorded states.

    ``candidates`` maps a reference **kind** to the exact spellings that kind admits at the frozen
    baseline (source paths, route paths, requirement identifiers, family identifiers, revisions).
    The comparison is exact string equality and nothing else: no case folding, no separator
    normalisation, no prefix test. A kind with no entry resolves to ``unresolved`` rather than
    raising, because "this census knows nothing about that kind of reference" is a fact worth
    reporting rather than an error that stops the run.
    """

    admitted = tuple(candidates.get(reference.reference_kind, ()))
    matches = tuple(candidate for candidate in admitted if candidate == reference.reference_text)
    if len(matches) == 1:
        return Resolution(reference=reference, state="resolved", candidates=matches)
    if not matches:
        return Resolution(reference=reference, state="unresolved", candidates=())
    return Resolution(reference=reference, state="ambiguous", candidates=matches)


def resolve_all(
    references: Iterable[Reference], candidates: Mapping[str, Sequence[str]]
) -> tuple[Resolution, ...]:
    """Resolve every reference in order, preserving the order they were read in."""

    return tuple(resolve_reference(reference, candidates) for reference in references)


def count_resolutions(resolutions: Sequence[Resolution]) -> ReferenceCounts:
    """Return the three-state partition over a set of resolutions."""

    return ReferenceCounts(
        resolved=sum(1 for item in resolutions if item.state == "resolved"),
        unresolved=sum(1 for item in resolutions if item.state == "unresolved"),
        ambiguous=sum(1 for item in resolutions if item.state == "ambiguous"),
    )


def mismatches_from_resolutions(resolutions: Sequence[Resolution]) -> tuple[Mismatch, ...]:
    """Report every non-resolved reference as a mechanical mismatch, verbatim.

    An unresolved reference and an ambiguous one are different facts and are reported as different
    kinds: a reference that names nothing is not the same observation as one that names two things,
    and collapsing them would hide the second -- which is the one that indicates the census's own
    candidate set is wrong rather than incomplete.
    """

    reported: list[Mismatch] = []
    for resolution in resolutions:
        if resolution.state == "unresolved":
            reported.append(
                Mismatch(
                    mismatch_kind="declared_source_absent",
                    artifact_path=resolution.reference.artifact_path,
                    reference_text=resolution.reference.reference_text,
                    baseline=resolution.reference.baseline,
                    observed="the reference names nothing at the frozen baseline",
                    expected="exactly one stored record or scoped source artifact",
                )
            )
        elif resolution.state == "ambiguous":
            reported.append(
                Mismatch(
                    mismatch_kind="route_reference_ambiguous",
                    artifact_path=resolution.reference.artifact_path,
                    reference_text=resolution.reference.reference_text,
                    baseline=resolution.reference.baseline,
                    observed=f"the reference names {len(resolution.candidates)} candidates",
                    expected="exactly one stored record or scoped source artifact",
                )
            )
    return tuple(reported)


def artifact_mismatches(
    *,
    artifact_path: str,
    declared_source_path: str | None,
    present_sources: Iterable[str],
    baseline: FrozenBaseline,
) -> tuple[Mismatch, ...]:
    """Report the mechanical facts one artifact's declared source path implies, and nothing more.

    ``declared_source_absent`` is the packet's own worked example: "a file card whose declared source
    path is absent at the baseline" is reported as the mechanical fact and is **not** classified as a
    documentation defect or as an implementation problem. The comparison is exact spelling against the
    set of source paths present at the baseline.
    """

    if declared_source_path is None:
        return ()
    if declared_source_path in set(present_sources):
        return ()
    return (
        Mismatch(
            mismatch_kind="declared_source_absent",
            artifact_path=artifact_path,
            reference_text=declared_source_path,
            baseline=baseline,
            observed="the declared source path is not present at the frozen baseline",
            expected="a source path present at the frozen baseline",
        ),
    )


def metadata_contradiction(
    *,
    artifact_path: str,
    declared_path: str,
    actual_path: str,
    baseline: FrozenBaseline,
) -> Mismatch | None:
    """Report a card whose declared metadata contradicts the front matter it points at, or nothing.

    The contradiction is between two **declared** values -- what the artifact says its source is and
    what the artifact it points at says it is -- which is why this is mechanical. Nothing about the
    code either file describes is read.
    """

    if declared_path == actual_path:
        return None
    return Mismatch(
        mismatch_kind="metadata_contradicts_front_matter",
        artifact_path=artifact_path,
        reference_text=declared_path,
        baseline=baseline,
        observed=f"the pointed-at artifact declares path {actual_path!r}",
        expected=f"the declared path {declared_path!r}",
    )


def duplicate_anchor_claims(
    claims: Mapping[str, Sequence[str]],
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Return every anchor two records claim, which is a mechanical fact about the corpus.

    ``claims`` maps an anchor to the record identities that declare it. Only anchors with more than
    one claimant are returned, in a deterministic order, and the result says *which* records disagree
    rather than which of them is right.
    """

    duplicated = (
        (anchor, tuple(records)) for anchor, records in claims.items() if len(set(records)) > 1
    )
    return tuple(sorted(duplicated, key=lambda item: item[0]))


def render_report(mismatches: Sequence[Mismatch]) -> str:
    """Render the whole report as lines of facts, in a stable order.

    Sorted by artifact and then by reference, so two runs over the same corpus render byte-identical
    text and a difference between two reports is a real difference rather than a reordering.
    """

    ordered = sorted(mismatches, key=lambda item: (item.artifact_path, item.reference_text))
    return "\n".join(item.render() for item in ordered)


def iter_kinds(mismatches: Sequence[Mismatch]) -> Iterator[tuple[MismatchKind, int]]:
    """Return each mismatch kind with its count, in the declared kind order and never at zero."""

    for kind in MISMATCH_KINDS:
        count = sum(1 for item in mismatches if item.mismatch_kind == kind)
        if count:
            yield kind, count
