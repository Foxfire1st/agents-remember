"""Which of a review's measured source changes each bound snapshot registers an attribution for.

``ICR-R04@v1`` owns one accounting: the measured source change population of a bound code-tree pair,
partitioned **once** into attributed, confirmed unregistered and undetermined changes. This module is
the *acquisition* half of that accounting -- it reads the registered mappings of the two bound
snapshots -- and :func:`agents_remember.memory.knowledge.diff_display.partition_attribution` is the
arithmetic. Neither half decides anything about a change's meaning, and neither is a second owner of
the records: the mappings are read through the memory layer's own registered-path lookup, and the
anchors are observed by the memory layer's own resolver.

Four rules make the acquisition truthful, and each is a way the numbers could otherwise lie:

* **The mapping is looked up by exact path equality, in the snapshot.** The one primitive that answers
  "which realization claims does this snapshot register at this path" is
  :func:`~agents_remember.memory.knowledge.read_queries.fetch_realizations_at_path`, which the
  registered-scope construction reads too -- so a review's attribution and a scope's edges cannot come
  to disagree about what "registered here" means. A change attributed to another invariant is
  therefore *found* rather than assumed absent: the lookup is not restricted to the selected subject,
  which is exactly the case ``ICR-R04`` refuses to report as globally unregistered.
* **A mapping is valid only when the recorded bytes are at the recorded path.** ``exact_recorded_blob``
  is that state and the only value this module reads as *resolved*. ``recorded_blob_mismatch`` is the
  shipped **stale** state -- the recorded identity no longer describes what is at the path, or the
  recorded locator does not bind those bytes -- and so are ``path_absent``, ``unsupported_locator``,
  ``entry_not_blob`` and an observation the resolver never made. Every one of them is a *purported*
  mapping: carried with its own resolution, counted as unresolved, and never promoted to an
  attribution. This is the packet's own precedence sentence, and the repository's shipped vocabulary
  agrees with it: ``resolved`` is ``exact_recorded_blob``
  (:mod:`agents_remember.memory.knowledge.read_owner_revisions`), ``unresolved`` is every observation
  that is not exact (:mod:`agents_remember.memory.knowledge.read`), and ``stale`` is
  ``recorded_blob_mismatch`` (:mod:`agents_remember.memory.knowledge.citation_closure`).
* **A snapshot that was not read supports no negative conclusion.** A side whose dataset is absent,
  damaged or unopenable is ``unavailable``, and every path without a resolved mapping is then of
  undetermined attribution rather than confirmed unregistered. The one exception the packet names is
  a *legitimately known-empty* side: the before half of a leaf whose recorded origin identifies it as
  an explicitly empty first generation, whose bytes the origin record is checked against, is
  ``known_empty`` and counts as completely inspected for absence (``ICR-R05`` owns that record).
* **Every count states its scope.** The denominator is the paths the caller's own source observation
  measured, the granularity is the changed path, and each bound snapshot's inspection state travels
  beside them. An observation that was not made at all yields no total -- never a zero.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import apsw

from agents_remember.application.knowledge_before_half import (
    damaged_before_half_reason,
    read_before_half,
)
from agents_remember.memory.knowledge.connection import open_read_only_database
from agents_remember.memory.knowledge.diff_display import (
    MappingFact,
    SideInspection,
    TreePaths,
    licenses_absence,
    partition_attribution,
    unavailable_attribution,
)
from agents_remember.memory.knowledge.read_anchors import anchor_resolver_for
from agents_remember.memory.knowledge.read_queries import (
    fetch_memberships_of_families_full,
    fetch_realizations_at_path,
    fetch_revision_ids,
)
from agents_remember.memory.knowledge.refusals import KnowledgeStorageError
from agents_remember.models.knowledge.diff import (
    AttributionSideState,
    ReadSide,
    SourceAttribution,
)
from agents_remember.models.knowledge.read import (
    FamilyIdentitySeed,
    FamilyRevisionSeed,
    InvariantIdentitySeed,
    InvariantRevisionSeed,
    KnowledgeReadContext,
    KnowledgeReadSeed,
)

__all__ = [
    "AttributionSideInput",
    "SelectedSubject",
    "review_attribution",
    "selected_subject",
]

# The one anchor resolution that establishes a registered path mapping: the recorded bytes are at the
# recorded path. Every other state is a purported mapping whose evidence did not resolve -- a *stale*
# one (``recorded_blob_mismatch``: the recorded identity no longer describes what is there, or the
# recorded locator does not bind those bytes), an *unresolved* one (``path_absent``,
# ``unsupported_locator``, ``entry_not_blob``), or one this snapshot's tree could not be asked about
# (``recorded_object_unavailable``, ``not_requested``). Reading a stale mapping as valid would present
# a claim whose evidence is gone as an established attribution, which is the one thing the packet's
# precedence sentence forbids.
_RESOLVED_MAPPINGS: frozenset[str] = frozenset({"exact_recorded_blob"})

# The subject kinds whose own recorded scope a claim's row can be compared against. A *revision*
# subject is narrower than its identity, and a family subject is answered by the family's own
# membership rows rather than by a name.
_SUBJECT_SCOPE = Literal["invariant", "invariant_revision", "family", "family_revision"]

_WHY_UNAVAILABLE = "the source observation this partition is the denominator of was not made"


@dataclass(frozen=True)
class SelectedSubject:
    """The subject one review selected, as the membership question needs it.

    ``kind`` says which recorded scope the subject *is*: an invariant identity or one of its exact
    revisions, or a family identity or one of its exact revisions. ``identity`` is that record's own
    id -- never a display label, a path or a version, none of which selects anything.
    """

    kind: _SUBJECT_SCOPE
    identity: str


@dataclass(frozen=True)
class AttributionSideInput:
    """One bound snapshot the partition is decided from.

    ``context`` is the read context that already binds one side's tree, exactly as the comparison
    opened it (:func:`agents_remember.application.knowledge_diff.open_diff_side`), so the anchor
    observation this module makes is the same observation the comparison's own read made against the
    same root and tree. ``connection`` is the already-open read-only handle when the caller has one --
    the comparison does -- and ``None`` when this module opens the file itself; it closes only what it
    opened.

    ``context`` is ``None`` for a side that never became readable at all -- an absent dataset, a file
    that is not a dataset, a namespace the bytes do not hold -- and ``unreadable`` then says why. Such
    a side is a fact of the *pair* and not a caller's error, and it is carried rather than omitted: a
    dropped side would leave the ones that remain looking complete, which is exactly the negative
    conclusion an unread snapshot cannot support.

    A before side's **half directory** is the directory its dataset file lives in, which is where a
    recorded first-generation origin sits (``ICR-R05``'s layout), so no caller has to name it.
    """

    side: ReadSide
    database: Path
    context: KnowledgeReadContext | None = None
    connection: apsw.Connection | None = None
    unreadable: str | None = None


@dataclass(frozen=True)
class _SideReading:
    """One side's inspection: what it registered, in what state, and whether its subject scope read."""

    inspection: SideInspection
    facts: tuple[MappingFact, ...] = ()
    subject_scope_established: bool = True


def selected_subject(selector: KnowledgeReadSeed | None) -> SelectedSubject | None:
    """Return the subject one review selector names, or ``None`` when it names no invariant or family.

    The narrowing is written out per seed kind rather than reached through ``getattr``, so the identity
    a membership question is asked about is a field of the selector that carries it. A path selector
    selects recorded realizations at a location and names no invariant or family at all, so it yields
    ``None`` -- and the partition then labels its links as carrying no selected-subject membership
    rather than inventing one.
    """

    if isinstance(selector, InvariantIdentitySeed):
        return SelectedSubject(kind="invariant", identity=str(selector.invariant_id))
    if isinstance(selector, InvariantRevisionSeed):
        return SelectedSubject(kind="invariant_revision", identity=str(selector.revision_id))
    if isinstance(selector, FamilyIdentitySeed):
        return SelectedSubject(kind="family", identity=str(selector.family_id))
    if isinstance(selector, FamilyRevisionSeed):
        return SelectedSubject(kind="family_revision", identity=str(selector.revision_id))
    return None


def review_attribution(
    observed: TreePaths,
    *,
    sides: Sequence[AttributionSideInput],
    subject: SelectedSubject | None = None,
) -> SourceAttribution:
    """Partition one observed change population by the registered mappings of the bound snapshots.

    The observation is the caller's own -- the same value its inventory was rendered from -- so the
    denominator this partition counts is the measurement the response publishes and not a second one.
    An observation that was not made returns an unavailable partition: no total, and the observation's
    own reason, because an unmeasured population has no denominator to be zero of.
    """

    if not observed.available:
        return unavailable_attribution(observed, _WHY_UNAVAILABLE)
    readings = tuple(_read_side(entry, observed.paths, subject) for entry in sides)
    inspections = tuple(reading.inspection for reading in readings)
    complete = licenses_absence(inspections)
    facts = tuple(fact for reading in readings for fact in reading.facts)
    return partition_attribution(
        observed,
        facts,
        inspections,
        subject_selected=subject is not None,
        # One predicate decides both questions: `licenses_absence` is the partition owner's own rule
        # for "every bound side licenses a negative conclusion", and the subject-scope question adds
        # only its own second condition (that each side answered the membership question).
        subject_scope_complete=complete
        and all(reading.subject_scope_established for reading in readings),
    )


def _read_side(
    entry: AttributionSideInput,
    paths: Sequence[str],
    subject: SelectedSubject | None,
) -> _SideReading:
    """Read one side's registered mappings, or report why this side could not be read at all."""

    context = entry.context
    if context is None:
        return _unavailable_side(
            entry,
            entry.unreadable
            or "this snapshot's dataset was never bound to a read context, so no mapping was read",
        )
    damage = _damaged_half(entry)
    if damage is not None:
        return _unavailable_side(entry, damage)
    connection = entry.connection
    owned = connection is None
    try:
        if connection is None:
            connection = open_read_only_database(entry.database)
        return _registered_mappings(connection, context, entry, paths, subject)
    except (apsw.Error, OSError, KnowledgeStorageError, KeyError, ValueError) as error:
        return _unavailable_side(entry, f"{type(error).__name__}: {error}")
    finally:
        if owned and connection is not None:
            connection.close()


def _registered_mappings(
    connection: apsw.Connection,
    context: KnowledgeReadContext,
    entry: AttributionSideInput,
    paths: Sequence[str],
    subject: SelectedSubject | None,
) -> _SideReading:
    """Read every claim this snapshot registers at the measured paths, and observe each anchor."""

    namespace = context.repository_id
    resolver = anchor_resolver_for(context)
    matches, established = _subject_matcher(connection, namespace, subject)
    facts: list[MappingFact] = []
    for path in paths:
        for row in fetch_realizations_at_path(connection, namespace, path):
            observation = resolver(row)
            # A resolver that observed nothing at all is the state the surface already renders for a
            # claim whose locator it could not resolve, so the mapping is carried unresolved rather
            # than dropped: a registered claim that produced no observation is still a claim this
            # snapshot registers at this path.
            resolution = "unsupported_locator" if observation is None else observation.resolution
            facts.append(
                MappingFact(
                    side=entry.side,
                    path=path,
                    claim_id=str(row["claim_id"]),
                    resolution=resolution,
                    resolved=resolution in _RESOLVED_MAPPINGS,
                    subject_link=matches is not None and matches(row),
                )
            )
    state, detail = _inspection_state(entry, len(facts))
    return _SideReading(
        inspection=SideInspection(
            side=entry.side,
            state=state,
            registered_mapping_count=len(facts),
            detail=detail,
        ),
        facts=tuple(facts),
        subject_scope_established=established,
    )


def _subject_matcher(
    connection: apsw.Connection,
    namespace: str,
    subject: SelectedSubject | None,
) -> tuple[Callable[[Mapping[str, object]], bool] | None, bool]:
    """Return the predicate that answers whether one registered claim is a link to the subject.

    Three answers, and they are three different facts: no subject was selected, so no claim is a link
    to one; the subject is an invariant identity or revision, which the claim's own row answers
    directly; or the subject is a family, whose members are the revisions its own membership rows
    record. A family whose memberships cannot be read yields ``established=False``: its links are
    still counted and attributed, and the *label* degrades to membership-unknown rather than claiming
    an exclusive-outside conclusion a read did not support.
    """

    if subject is None:
        return None, True
    if subject.kind == "invariant":
        return lambda row: str(row["invariant_id"]) == subject.identity, True
    if subject.kind == "invariant_revision":
        return lambda row: str(row["invariant_revision_id"]) == subject.identity, True
    revision_ids = _family_revision_ids(connection, namespace, subject)
    if revision_ids is None:
        return None, False
    members = frozenset(
        str(row[2])
        for row in fetch_memberships_of_families_full(connection, namespace, revision_ids)
    )
    return lambda row: str(row["invariant_revision_id"]) in members, True


def _family_revision_ids(
    connection: apsw.Connection, namespace: str, subject: SelectedSubject
) -> tuple[str, ...] | None:
    """The family revisions one family subject spans, or ``None`` when they cannot be read."""

    if subject.kind == "family_revision":
        return (subject.identity,)
    try:
        return fetch_revision_ids(
            connection, namespace, "family_revision", "family_id", subject.identity
        )
    except (apsw.Error, KnowledgeStorageError):
        return None


def _inspection_state(
    entry: AttributionSideInput, registered: int
) -> tuple[AttributionSideState, str]:
    """Whether this side was completely inspected, or is a legitimately known-empty side.

    ``known_empty`` is a stronger statement than a scan that found nothing, so it is claimed only for
    a before half whose own recorded origin identifies an explicitly empty first generation and whose
    dataset the origin record is checked against. A side that registered nothing without such a record
    is ``inspected``: the read completed, and that is all that was established.
    """

    if registered == 0 and entry.side == "before" and _identified_first_generation(entry.database):
        return (
            "known_empty",
            "this before half records an explicitly identified empty first generation whose origin "
            "matches the dataset beside it, so no registered mapping exists here by construction and "
            "its absence is not an unread silence",
        )
    return (
        "inspected",
        f"every registered claim at the measured changed paths was read from this snapshot ({registered} "
        "mapping(s)), and each one's anchor was observed against this side's own bound code tree",
    )


def _damaged_half(entry: AttributionSideInput) -> str | None:
    """Why a before half is present-but-not-what-it-claims, or ``None`` when it is sound.

    A half whose recorded origin disagrees with the bytes beside it is *damaged*: R05's own reader
    refuses to call it the generation it names, and the review's preflight refuses the pair as
    "present but cannot be read". The partition therefore must not read it either -- a half that cannot
    be read is not a half that registered nothing, so it is ``unavailable`` and every path it did not
    map is of undetermined attribution rather than confirmed unregistered.

    Only a *before* side has this layout: a before half records its generation beside the dataset
    (``ICR-R05``), while a candidate carries a receipt instead, so the question is asked of the side
    that can answer it. ``None`` is the answer for an absent dataset too -- absence is its own state
    with its own reason, and this reader does not restate it.
    """

    if entry.side != "before":
        return None
    try:
        return damaged_before_half_reason(Path(entry.database))
    except (OSError, KnowledgeStorageError, apsw.Error) as error:
        return f"{type(error).__name__}: {error}"


def _identified_first_generation(database: Path) -> bool:
    """Whether the half one dataset sits in records it as an identified empty first generation."""

    try:
        half = read_before_half(Path(database).parent)
    except (OSError, KnowledgeStorageError, apsw.Error):
        return False
    return half.state == "identified" and half.origin is not None


def _unavailable_side(entry: AttributionSideInput, reason: str) -> _SideReading:
    """One side that could not be read: no mapping, no negative conclusion, and its own reason."""

    return _SideReading(
        inspection=SideInspection(
            side=entry.side,
            state="unavailable",
            registered_mapping_count=None,
            detail=(
                f"this snapshot could not be inspected ({reason}), so a changed path with no resolved "
                "mapping cannot be called confirmed unregistered on the strength of this side: an "
                "unread snapshot is not one that registered nothing"
            ),
        ),
        subject_scope_established=False,
    )
