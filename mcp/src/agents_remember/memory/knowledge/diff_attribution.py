"""The attribution partition: one measured change population, divided once (ICR-R04).

*Attribution* is a recorded relationship between a realization claim and the path it names, so the
question this module answers is a path-level one: of the changes the *source* measurement observed,
which ones does either bound snapshot register an attribution for? The answer is one partition with
three buckets -- attributed, confirmed unregistered, undetermined -- and they are disjoint and
exhaustive over that measured population by construction.

Four rules, each of which is a way a count could otherwise lie:

* **The denominator is the measurement, not the comparison.** An unchanged mapped path is context and
  never a change, and the value carries the whole observation so a *partial* one states the scope of its
  own total (paths whose names cannot be carried as text are outside every count here).
* **A mapping is valid only when the recorded bytes are at the recorded path.** A stale recording, an
  unresolvable locator and a missing path are purported mappings: counted, carried unresolved, and never
  promoted to an attribution.
* **Several links to one path count it once.** A path is one changed path however many claims name it.
* **A side nobody read supports no negative conclusion.** Confirmed-unregistered requires every required
  snapshot/scope to have been completely inspected or to be a legitimately known-empty side; otherwise
  the change is undetermined. The licensing states are named in one table, and :func:`licenses_absence` is
  the one predicate read off it -- the confirmed-unregistered bucket and the subject-scope question that
  decides the exclusive-outside label both call it, so neither can disagree about what "completely
  inspected" means.

The arithmetic is pure: the caller supplies the observation, the registered mappings it read from the
two snapshots, and each side's inspection state. :mod:`agents_remember.memory.knowledge.diff_display`
re-exports every name here, so the display seam and its importers are unchanged.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from agents_remember.memory.knowledge.tree_observation import TreePaths
from agents_remember.models.knowledge.diff import (
    AttributionLink,
    AttributionSide,
    AttributionSideState,
    ChangedPathAttribution,
    ReadSide,
    SourceAttribution,
)

__all__ = [
    "AttributionReader",
    "MappingFact",
    "SideInspection",
    "licenses_absence",
    "partition_attribution",
    "unavailable_attribution",
]


@dataclass(frozen=True)
class MappingFact:
    """One recorded realization claim a snapshot registers at one measured changed path.

    A *registered* mapping is a claim row a snapshot actually holds, keyed by exact equality between
    the path whose attribution is being decided and the path the claim's anchor records. ``resolved``
    is the second, separate fact: whether that claim's anchor observation found the **recorded bytes at
    the recorded path** in that snapshot's own bound tree (``exact_recorded_blob``). A *stale* or
    *unresolved* purported mapping -- a recorded identity the bytes no longer match, a locator that does
    not bind, a path that is not there -- is carried with ``resolved=False`` and its own ``resolution``
    rather than dropped, because "no mapping is registered here" and "a mapping is registered and its
    evidence did not resolve" are different facts a reader acts on differently.

    ``subject_link`` says whether this claim's own recorded scope is the subject a review selected. It
    is a fact about the *record*, computed by whoever read the snapshot, and never a relevance rule:
    a claim outside the selection is reported as outside it and not as absent.
    """

    side: ReadSide
    path: str
    claim_id: str
    resolution: str
    resolved: bool
    subject_link: bool = False


@dataclass(frozen=True)
class SideInspection:
    """One bound snapshot's contribution to the partition, and how completely it was inspected."""

    side: ReadSide
    state: AttributionSideState
    registered_mapping_count: int | None
    detail: str


# The seam that answers "which registered mappings does each bound snapshot hold at these measured
# paths". It is a callable rather than a class so the comparison can be handed the reader that owns
# the two open snapshots, and so a case can substitute one observation without a repository.
AttributionReader = Callable[[TreePaths], SourceAttribution]


def unavailable_attribution(observed: TreePaths, reason: str) -> SourceAttribution:
    """Return the partition of a measurement that was not made: no total, and the reason it is not zero.

    The source observation's own sentence travels with the reason, because "the trees could not be
    compared", "a side named no tree" and "the knowledge half could not be read" are three states a
    reader acts on differently.
    """

    return SourceAttribution(
        state="unavailable",
        detail=(
            f"{reason}. The source observation beside this value says: {observed.detail}. No total is "
            "reported and none is reported as zero: an unmeasured change population has no "
            "denominator, so no path can be called attributed, confirmed unregistered or of "
            "undetermined attribution"
        ),
    )


def partition_attribution(
    observed: TreePaths,
    mappings: Sequence[MappingFact],
    sides: Sequence[SideInspection],
    *,
    subject_selected: bool,
    subject_scope_complete: bool,
) -> SourceAttribution:
    """Partition one measured change population by the registered mappings of both snapshots.

    The whole accounting, in four steps and in this order:

    1. the population is the observation's own ``paths`` -- the paths the *source* measurement
       measured. That is what makes an unchanged mapped path context rather than a change, and it is
       the denominator every count below is a count of. The observation travels whole rather than as a
       list of strings because a *partial* one states its own scope: paths whose names cannot be
       carried as text are outside this denominator, and the value says so;
    2. a path is attributed when at least one mapping that **resolved** names it, from either
       snapshot, however many claims name it: several links to one path count it once, so no
       duplicate can inflate a total;
    3. a path with no resolved mapping is confirmed unregistered only when every required
       snapshot/scope was completely inspected, or is a legitimately known-empty side
       (``_completion``); otherwise its attribution is undetermined, because a side nobody read
       cannot be read as a side that registered nothing;
    4. within the attributed paths, a known link to the selected subject establishes selected
       attribution. Links only outside it may be called exclusive only when the subject's own scope
       was completely inspected; otherwise the label is that the link is known and the selection's
       membership is not established.
    """

    by_path = _mappings_by_path(mappings)
    complete = licenses_absence(sides)
    entries = tuple(
        _path_attribution(
            path,
            by_path.get(path, ()),
            complete=complete,
            subject_selected=subject_selected,
            subject_scope_complete=subject_scope_complete,
        )
        for path in sorted(set(observed.paths))
    )
    counted = _bucket_counts(entries)
    return SourceAttribution(
        state="measured",
        changed_total=len(entries),
        attributed_total=counted["attributed"],
        confirmed_unregistered_total=counted["confirmed_unregistered"],
        unknown_attribution_total=counted["unknown_attribution"],
        complete=complete,
        subject_scope_complete=subject_scope_complete if subject_selected else None,
        sides=tuple(
            AttributionSide(
                side=entry.side,
                state=entry.state,
                registered_mapping_count=entry.registered_mapping_count,
                detail=entry.detail,
            )
            for entry in sides
        ),
        paths=entries,
        detail=_partition_detail(observed, entries, counted, sides, complete=complete),
    )


def _mappings_by_path(
    mappings: Sequence[MappingFact],
) -> Mapping[str, tuple[MappingFact, ...]]:
    """Group the registered mappings by the measured path each one names."""

    grouped: dict[str, list[MappingFact]] = {}
    for mapping in mappings:
        grouped.setdefault(mapping.path, []).append(mapping)
    return {path: tuple(entries) for path, entries in grouped.items()}


# The two side states that license an absence conclusion, each for its own reason and neither
# reducible to the other: an ``inspected`` side was scanned to completion, and a ``known_empty`` side
# was *created* empty by an origin record that is checked against its bytes (``ICR-R05``), so its
# silence is a fact about the generation rather than the result of a scan. ``unavailable`` licenses
# nothing: a side that was not read cannot say that nothing is registered. The table is what makes the
# distinction explicit -- a state added later licenses nothing until it is named here.
_LICENSED_ABSENCE: Mapping[AttributionSideState, bool] = {
    "inspected": True,
    "known_empty": True,
    "unavailable": False,
}


def _licenses_absence(state: AttributionSideState) -> bool:
    """Whether one side's own inspection state licenses a negative attribution conclusion."""

    return _LICENSED_ABSENCE[state]


def licenses_absence(inspections: Sequence[SideInspection]) -> bool:
    """Whether every bound snapshot licenses a negative attribution conclusion about a path.

    The packet's rule is a disjunction over the *sides*, and each disjunct is a different fact:
    ``inspected`` is a scan that completed, ``known_empty`` is R05's identified empty first generation
    whose emptiness is established by its own record rather than by a scan. Both license "no valid
    registered attribution here"; ``unavailable`` does not, and neither does a comparison that bound no
    side at all (there is then nothing that could have been inspected, which is not the same as having
    inspected everything).

    This is the one predicate behind both the confirmed-unregistered bucket and the exclusive-outside
    label: the partition reads it to decide the bucket, and the caller that acquires the sides reads it
    to decide whether the selected subject's own scope was completely inspected. One table, one
    predicate, two readers -- so the two questions cannot come to disagree.
    """

    return bool(inspections) and all(_licenses_absence(entry.state) for entry in inspections)


def _path_attribution(
    path: str,
    facts: Sequence[MappingFact],
    *,
    complete: bool,
    subject_selected: bool,
    subject_scope_complete: bool,
) -> ChangedPathAttribution:
    """Return one measured path's bucket, its link when it has one, and the fact that decided it."""

    resolved = tuple(fact for fact in facts if fact.resolved)
    unresolved = len(facts) - len(resolved)
    if resolved:
        return ChangedPathAttribution(
            path=path,
            bucket="attributed",
            link=_link(
                resolved,
                subject_selected=subject_selected,
                subject_scope_complete=subject_scope_complete,
            ),
            mapped_sides=_mapped_sides(resolved),
            unresolved_mapping_count=unresolved,
            detail=(
                f"{len(resolved)} registered realization claim(s) hold the recorded bytes at this "
                f"path, and {unresolved} purported mapping(s) name it without resolving to them "
                "(a stale recording, an unresolvable locator or a path that is not there); the path "
                "is counted once at the changed-path granularity, whatever the number of links"
                + _incomplete_suffix(complete)
            ),
        )
    if complete:
        return ChangedPathAttribution(
            path=path,
            bucket="confirmed_unregistered",
            unresolved_mapping_count=unresolved,
            detail=(
                "no registered realization claim holds its recorded bytes at this path, and every "
                "required snapshot/scope was completely inspected or is a legitimately known-empty "
                "side, so the absence of a valid registered attribution is established rather than "
                "assumed"
            ),
        )
    return ChangedPathAttribution(
        path=path,
        bucket="unknown_attribution",
        unresolved_mapping_count=unresolved,
        detail=(
            "no registered realization claim resolves this path and at least one required "
            "snapshot/scope was not completely inspected, so no negative attribution conclusion is "
            "available: an unread side is not a side that registered nothing"
        ),
    )


def _incomplete_suffix(complete: bool) -> str:
    """The label an attributed path carries when a required snapshot was not inspected.

    The packet asks for exactly this sentence, and it is the difference between two readings of one
    attributed path: "a registered claim names this path" is established, and "these are all the
    claims that name it" is not, because a snapshot nobody read may name it too.
    """

    if complete:
        return ""
    return (
        ", and its additional mappings may be unknown: at least one required snapshot/scope was not "
        "completely inspected, so this attribution is established without claiming to be complete"
    )


def _link(
    resolved: Sequence[MappingFact],
    *,
    subject_selected: bool,
    subject_scope_complete: bool,
) -> AttributionLink:
    """Return how the resolved links of one attributed path relate to the selected subject."""

    if not subject_selected:
        return "no_subject_selected"
    if any(fact.subject_link for fact in resolved):
        return "selected_subject"
    if subject_scope_complete:
        return "outside_selection_complete"
    return "outside_selection_membership_unknown"


def _mapped_sides(resolved: Sequence[MappingFact]) -> tuple[ReadSide, ...]:
    """Return the snapshots whose registered mappings resolved one path, in declared side order."""

    reached = {fact.side for fact in resolved}
    return tuple(side for side in ("before", "after") if side in reached)


def _bucket_counts(entries: Sequence[ChangedPathAttribution]) -> dict[str, int]:
    """Return one count per bucket, over the partition's own paths."""

    return {
        bucket: sum(1 for entry in entries if entry.bucket == bucket)
        for bucket in ("attributed", "confirmed_unregistered", "unknown_attribution")
    }


def _partition_detail(
    observed: TreePaths,
    entries: Sequence[ChangedPathAttribution],
    counted: Mapping[str, int],
    sides: Sequence[SideInspection],
    *,
    complete: bool,
) -> str:
    """State the partition's scope, its three counts and each snapshot's own inspection.

    The scope sentence is not decoration: ``changed_total`` is the *carriable* measured population, and
    when the observation is partial the value says so here rather than relying on a reader opening the
    inventory beside it. A reader who took a smaller total for the whole population would be reading a
    dropped path as a decided one.
    """

    inspected = "; ".join(f"{entry.side} {entry.state}" for entry in sides) or "no side was bound"
    return (
        f"{len(entries)} measured changed path(s) at the changed-path granularity: "
        f"{counted['attributed']} attributed, "
        f"{counted['confirmed_unregistered']} confirmed to have no valid registered attribution, and "
        f"{counted['unknown_attribution']} of undetermined attribution. Snapshot inspection: "
        f"{inspected}. Every required snapshot/scope completely inspected or legitimately known "
        f"empty: {complete}. {_denominator_scope(observed)} A resolved path-level mapping establishes "
        "that a recorded claim's bytes are at this path and never that every change inside it realizes "
        "that claim"
    )


def _denominator_scope(observed: TreePaths) -> str:
    """State what the denominator counts, and what it does not, when the observation is partial.

    Two ways an observation is partial and each is stated in its own words: paths whose *names* cannot
    be carried as text are outside the population entirely (counted, named by their byte form and
    carried by the inventory), while a failed content classification leaves the path set whole. A
    complete observation says the plain fact instead of staying silent, so the two cases read
    differently rather than one of them reading as an omission.
    """

    if not observed.partial:
        return "This denominator is the whole measured change population of the bound pair."
    uncarried = len(observed.unrepresentable)
    if uncarried:
        return (
            f"This denominator is the CARRIABLE measured change population: {uncarried} further "
            "changed path(s) could not be carried as names in this vocabulary and are outside every "
            "count here, preserved with their byte forms by the inventory beside this value. This "
            f"observation is partial: {_closed(observed.detail)}"
        )
    return (
        "This denominator is the measured path set, and one field of the observation could not be "
        f"classified. This observation is partial: {_closed(observed.detail)}"
    )


def _closed(detail: str) -> str:
    """One observation's own sentence, closed so the next sentence does not run into it.

    The producer's detail is prose without a guaranteed terminator, and this fragment is concatenated
    with the partition's closing statement: two sentences that read as one is exactly the kind of
    unreadable scope statement this function exists to prevent.
    """

    stripped = detail.strip()
    return stripped if stripped.endswith(".") else f"{stripped}."
