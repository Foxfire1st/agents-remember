"""The recorded population one review selection reaches (``ICR-R26@v1``).

A review's applicability classification turns on two questions, and this module answers both from
recorded facts: *which identity did the request select, and which revisions does the selection
record for it*, and *which other identities do the recorded relationships of that selection reach*.

**What it reads, and why from two places.** The comparison's own union items, ICR-R07's recorded
revision selection and ICR-R08's traversal of the recorded before/after relationship union say what
the comparison *displayed*; they are a bounded page (ICR-R10), so on their own they would make a
record's meaning depend on the page the caller asked for. The snapshots' own owners therefore answer
the page-independent half -- the selected identity's recorded revisions
(:func:`~agents_remember.memory.knowledge.read_queries.fetch_revision_ids`), the realizations
recorded under them, the families that directly contain them and those families' recorded members --
and the two are merged so the recorded population only ever *grows* past what the page showed. This
module opens those two snapshot files read-only for that read and nothing else: it selects no
subject, widens no frontier, resolves no reference and writes nothing.

**Why this is its own module.** The classification that consumes it is
:mod:`agents_remember.application.review_record_applicability`; the population it is classified
against is a different responsibility with different inputs, and the review adapter is over the
repository's soft file-size rail. One implementation of "what does this selection reach", called by
its one consumer.

A path seed names no identity at all, and that is a *state* rather than a failure: the population
then carries no subject, and every record of that review is classified by the recorded relationship
that reaches it instead of by a subject it never had.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import apsw

from agents_remember.application.review_attribution import selected_subject
from agents_remember.application.review_candidate_resolution import review_namespace
from agents_remember.application.review_revision_comparison import SubjectRevisionSelection
from agents_remember.memory.knowledge.connection import open_read_only_database
from agents_remember.memory.knowledge.read_queries import (
    fetch_family_ids_for_revisions,
    fetch_invariant_revisions,
    fetch_memberships_of_families_full,
    fetch_memberships_of_invariants,
    fetch_realizations_for_invariants,
    fetch_revision_ids,
)
from agents_remember.memory.knowledge.refusals import KnowledgeStorageError
from agents_remember.models.knowledge.diff import KnowledgeDiffItem
from agents_remember.models.knowledge.read import KnowledgeReadSeed
from agents_remember.models.knowledge.review import ComparisonIdentity, ReviewEntry
from agents_remember.models.knowledge.review_relationships import ReviewRelationshipMovement

__all__ = [
    "ApplicabilitySources",
    "ComparisonFacts",
    "RecordedIdentity",
    "RecordedSelection",
    "selected_subject_population",
]


@dataclass(frozen=True)
class RecordedIdentity:
    """One recorded identity a relationship side or a union item names, with its recorded path."""

    kind: str
    record_id: str


# A relationship row whose own identity could not be established is recorded as a movement with an
# identity gap rather than as an identity invented here, so ``_Population.reach`` is handed this
# stand-in and records nothing: it is the display's own "no identity" marker, never a subject.
_NO_IDENTITY = RecordedIdentity(kind="unresolved", record_id="unresolved")


@dataclass(frozen=True)
class RecordedSelection:
    """The recorded population one review selection reaches, as the records are classified against it.

    ``subject`` is the identity the request's own seed named (absent for a path seed, which names no
    identity at all), and ``subject_revisions`` are the revisions the selection **records** for it:
    the selected identity's recorded revisions as the two snapshots' own owners list them --
    page-independent, so a bounded page cannot shrink them -- together with every revision the
    comparison's page selected for that identity.

    ``published_retained`` is ICR-R07's own retained list for the page this request asked for, and it
    is deliberately *separate* from ``subject_revisions``: R07 publishes a selection for the page it
    answered, so a label may cite that list only where it really carries the matched revisions.

    Every other mapping is a *recorded* fact read from the comparison's union, R08's traversal and
    the snapshots' own relationship rows: which revisions belong to which identity, which
    relationship rows are recorded and which identity each of them sits under, which identities a
    recorded relationship reaches, and the spelling of the relationship that reached each of them.
    """

    subject: RecordedIdentity | None
    subject_revisions: frozenset[str]
    revision_identity: Mapping[str, RecordedIdentity]
    relationship_ids: frozenset[str]
    relationship_identity: Mapping[str, RecordedIdentity]
    path_identity: Mapping[str, RecordedIdentity]
    related: Mapping[tuple[str, str], str]
    generation: ComparisonIdentity | None
    published_retained: frozenset[str]

    def identity_of(self, reference: str) -> RecordedIdentity | None:
        """The identity one recorded reference names, when the selection reaches it."""

        return self.revision_identity.get(reference)

    def identity_of_relationship(self, reference: str) -> RecordedIdentity | None:
        """The identity one recorded relationship row sits under, when the union holds it."""

        return self.relationship_identity.get(reference)

    def is_subject(self, identity: RecordedIdentity | None) -> bool:
        """Whether one identity is the selected subject's own."""

        return identity is not None and identity == self.subject

    def relationship_of(self, identity: RecordedIdentity) -> str | None:
        """The recorded relationship path that reaches one non-selected identity, if any."""

        return self.related.get((identity.kind, identity.record_id))


@dataclass(frozen=True)
class ComparisonFacts:
    """The recorded facts of one comparison that an applicability classification reads.

    The comparison's own union items, ICR-R07's revision selection, the comparison identity ICR-R11
    publishes and ICR-R08's recorded relationship union travel together because they are one
    measurement: a revision population read from another comparison's items, or a generation compared
    against another comparison's tree, would be a different review wearing this one's identity.
    """

    items: Sequence[KnowledgeDiffItem]
    selected: SubjectRevisionSelection | None
    generation: ComparisonIdentity | None
    relationships: Sequence[ReviewRelationshipMovement]


@dataclass(frozen=True)
class RecordedSnapshots:
    """The two snapshot files one comparison read, for the recorded population of one selection.

    They are carried because a record's *meaning* is a fact about the selection rather than about the
    page the comparison returned: the comparison's own items are a bounded window (ICR-R10), and a
    classification built from that window alone would call the selected subject's own record
    historical -- and a related sibling's record unrelated -- as soon as a caller asked for a smaller
    page. The window still decides what is displayed; these files are what the selection *records*.
    """

    repository_id: str
    before: Path
    after: Path


@dataclass(frozen=True)
class ApplicabilitySources:
    """One comparison's recorded facts, the request's own selector and the known-subject reader.

    ``known_subjects`` is a reader rather than a value because it is needed only when a record names
    an identity the selection does not reach -- which is exactly when the difference between
    "another subject this comparison records" and "an identity nothing records" has to be decided --
    so the ordinary review never pays for the catalogue read.
    """

    comparison: ComparisonFacts
    selector: KnowledgeReadSeed | None
    known_subjects: Callable[[], Sequence[ReviewEntry]]
    snapshots: RecordedSnapshots


def selected_subject_population(sources: ApplicabilitySources) -> RecordedSelection:
    """Read the recorded population one review selection reaches, from the owners that publish it.

    The subject is the one :func:`selected_subject` names -- the shipped narrowing of a review seed,
    spelled per seed kind -- and a *revision* seed's identity is resolved through the union item that
    carries that revision, because a revision seed names a revision and the subject is the identity
    the snapshot records it under. A path seed names no identity and this returns a population with
    no subject, which is why every record of such a review is classified by recorded relationship
    rather than by subject.
    """

    named = selected_subject(sources.selector)
    facts = sources.comparison
    subject = _subject_identity(named, facts.items)
    recorded = _recorded_population(sources, subject)
    # The recorded population of the *selection*, read from the snapshots' own owners, is merged
    # under the page's own facts. Nothing here replaces what the comparison returned: the union only
    # grows, so a caller's page size can narrow what is displayed and never what a record means.
    snapshots = _snapshot_population(subject, sources.snapshots)
    return RecordedSelection(
        subject=subject,
        subject_revisions=frozenset(recorded.revisions | snapshots.revisions),
        revision_identity={**snapshots.revision_identity, **recorded.revision_identity},
        relationship_ids=frozenset(
            {
                *_relationship_ids(facts.relationships),
                *snapshots.relationship_identity.keys(),
            }
        ),
        relationship_identity={
            **snapshots.relationship_identity,
            **recorded.relationship_identity,
        },
        path_identity={**snapshots.path_identity, **recorded.path_identity},
        # Three tiers, in the order of what each one states best. The snapshots' own relationship
        # rows say *how* an identity is reached and do not depend on the page, so they win; a
        # relationship row the traversal read says the same thing for an identity the snapshot read
        # did not reach; the comparison's own selection of an identity says *which* identity a
        # selected revision belongs to and is the weakest statement of how it is reached. The order
        # is fixed, so two page sizes render the same related identity with the same spelling.
        related={**recorded.related, **recorded.reached, **snapshots.snapshot},
        generation=facts.generation,
        # ICR-R07's own selection is published for the page the caller asked for, so its retained
        # list is carried beside the recorded population: a label may cite it only when it really
        # lists the revisions that matched.
        published_retained=frozenset(_retained_revisions(facts.selected, subject)),
    )


def _subject_identity(named, items: Sequence[KnowledgeDiffItem]) -> RecordedIdentity | None:
    """One review seed's subject identity, in the union's own kind vocabulary."""

    if named is None:
        return None
    kind = str(named.kind)
    if kind in ("invariant", "family"):
        return RecordedIdentity(kind=kind, record_id=str(named.identity))
    for item in items:
        if item.kind in ("invariant", "family") and item.item_id == str(named.identity):
            return RecordedIdentity(kind=str(item.kind), record_id=str(item.record_id))
    return None


def _recorded_population(
    sources: ApplicabilitySources, subject: RecordedIdentity | None
) -> _Population:
    """The revisions, the revision index, the related identities and the relationship index.

    The revisions and identities come from the comparison's own union items -- what the selection
    actually selected, per side -- and the related identities additionally from the recorded
    relationships ICR-R08 traversed, each with the recorded relationship spelling that reached it.
    The subject's own revisions are separated from every other identity's because "this record is
    about the selected subject" and "this record is about something the selection reaches" are the
    two facts the whole classification turns on.
    """

    facts = sources.comparison
    population = _Population(subject=subject)
    # The comparison's own union items are recorded first: they are what *names* the identity each
    # selected revision belongs to. The recorded relationships are traversed second, so the spelling
    # a related identity is reached by is the relationship that reached it (a realization's row and
    # address, a membership's family) rather than the comparison's selection of it.
    for item in facts.items:
        _record_union_item(population, item)
    for movement in facts.relationships:
        _record_movement(population, movement)
    population.revisions.update(_retained_revisions(facts.selected, subject))
    return population


@dataclass
class _Population:
    """The recorded population one selection reaches, as it is accumulated.

    It is one mutable accumulator rather than five values threaded through the readers below,
    because every reader adds to the *same* population and a helper handed four of the five could
    record a relationship under an index the caller is not building.
    """

    subject: RecordedIdentity | None
    revisions: set[str] = field(default_factory=set)
    revision_identity: dict[str, RecordedIdentity] = field(default_factory=dict)
    relationship_identity: dict[str, RecordedIdentity] = field(default_factory=dict)
    path_identity: dict[str, RecordedIdentity] = field(default_factory=dict)
    related: dict[tuple[str, str], str] = field(default_factory=dict)
    # The same facts as ``related``, recorded from the snapshots' own relationship rows rather than
    # from the comparison's selection of an identity: a membership row that reaches a member is a
    # better statement of how than the union item that selected it.
    snapshot: dict[tuple[str, str], str] = field(default_factory=dict)
    # The same facts as ``related``, recorded from the relationship rows themselves rather than from
    # the comparison's selection of an identity. Two mappings rather than one because the spellings
    # differ in authority, not in kind: a recorded relationship row that reached an identity is the
    # better statement of *how* it is reached, and a union item is the better statement of *which*
    # identity a selected revision belongs to.
    reached: dict[tuple[str, str], str] = field(default_factory=dict)

    def reach(self, identity: RecordedIdentity, path: str) -> None:
        """Record one identity a recorded relationship reaches, and the relationship that reaches it.

        The identity is an *identity* -- an invariant or a family -- and deliberately never enters
        the revision index or the subject's revisions: a relationship row's own identity is not a
        revision, and recording it as one is how a record that names an identity in a revision field
        came to be matched as a retained revision instead of being reported unresolved
        (``ICR-R26@v1``'s Failure And Recovery Behavior).
        """

        if identity == _NO_IDENTITY or self.subject is None:
            return
        if identity != self.subject:
            self.reached.setdefault((identity.kind, identity.record_id), path)


def _record_union_item(population: _Population, item: KnowledgeDiffItem) -> None:
    """Record one union item's identity and the revisions it carries, per side."""

    if item.kind not in ("invariant", "family"):
        return
    identity = RecordedIdentity(kind=str(item.kind), record_id=str(item.record_id))
    revisions = {str(item.item_id), *(_side_revisions(item))}
    for revision_id in revisions:
        if revision_id:
            population.revision_identity.setdefault(revision_id, identity)
    if population.subject is not None and identity == population.subject:
        population.revisions.update(value for value in revisions if value)
    else:
        population.related.setdefault((identity.kind, identity.record_id), _union_path(item))


def _record_movement(population: _Population, movement: ReviewRelationshipMovement) -> None:
    """Record one movement's identities, revisions and authored lineage as facts of this selection."""

    population.reach(_movement_identity(movement) or _NO_IDENTITY, _movement_path(movement))
    for side in (*movement.before, movement.after):
        _record_side(population, side, movement)


def _record_side(population: _Population, side, movement: ReviewRelationshipMovement) -> None:
    """Record one recorded side's address, revisions and lineage, when it names an identity."""

    if side is None:
        return
    identity = _side_identity(side)
    if identity is None:
        return
    if side.relationship_id is not None:
        population.relationship_identity.setdefault(str(side.relationship_id), identity)
    if side.path is not None:
        population.path_identity.setdefault(str(side.path), identity)
    path = _movement_path(movement)
    # Only the association's own revision is attributed to the side's identity. A membership's
    # ``member_revision_id`` is a revision of the *member* identity, which the membership row does
    # not name: it is the member's own identity item in the comparison's union that names it, and
    # attributing it here would report a member invariant's revision under the family it sits in.
    if side.revision_id is not None:
        _reach_revision(population, identity, str(side.revision_id), path)
    for lineage in movement.lineage:
        population.revision_identity.setdefault(str(lineage.revision_id), identity)
        for related_revision in lineage.related_revision_ids:
            population.revision_identity.setdefault(str(related_revision), identity)


def _reach_revision(
    population: _Population, identity: RecordedIdentity, revision_id: str, path: str
) -> None:
    """Record one cited revision as the subject's own, or as an identity the selection reaches."""

    population.revision_identity.setdefault(revision_id, identity)
    if population.subject is None:
        return
    if identity == population.subject:
        population.revisions.add(revision_id)
    else:
        population.related.setdefault((identity.kind, identity.record_id), path)


def _side_revisions(item: KnowledgeDiffItem) -> tuple[str, ...]:
    return tuple(
        str(payload.revision_id)
        for payload in (item.before, item.after)
        if payload is not None and payload.revision_id is not None
    )


def _retained_revisions(
    selection: SubjectRevisionSelection | None, subject: RecordedIdentity | None
) -> tuple[str, ...]:
    """The revisions ICR-R07's own selection retains for the selected subject, when it selected it."""

    recorded = None if selection is None else selection.selection
    if recorded is None or subject is None:
        return ()
    if str(recorded.record_kind) != subject.kind or str(recorded.record_id) != subject.record_id:
        return ()
    return (*recorded.before_retained, *recorded.after_retained)


def _movement_identity(movement: ReviewRelationshipMovement) -> RecordedIdentity | None:
    if movement.record_id is None:
        return None
    return RecordedIdentity(kind=str(movement.record_kind), record_id=str(movement.record_id))


def _side_identity(side) -> RecordedIdentity | None:
    if side.record_id is None or side.record_kind is None:
        return None
    return RecordedIdentity(kind=str(side.record_kind), record_id=str(side.record_id))


def _relationship_ids(relationships: Sequence[ReviewRelationshipMovement]) -> tuple[str, ...]:
    return tuple(
        str(side.relationship_id)
        for movement in relationships
        for side in (*movement.before, movement.after)
        if side is not None and side.relationship_id is not None
    )


def _movement_path(movement: ReviewRelationshipMovement) -> str:
    """The recorded relationship spelling that reached one identity, in the movement's own words."""

    side = movement.after if movement.after is not None else next(iter(movement.before), None)
    parts = [str(movement.relationship_kind)]
    if side is not None and side.relationship_id is not None:
        parts.append(str(side.relationship_id))
    if side is not None and side.path is not None:
        parts.append(f"at {side.path}")
    return " ".join(parts)


def _union_path(item: KnowledgeDiffItem) -> str:
    """Why the comparison's own selection reached one identity, in the read's recorded words.

    A union item carries the reasons the read selected it -- the identity seed, a recorded family
    membership, an advertised frontier -- so an identity reached as another identity's family member
    is spelled with that recorded relationship rather than with a selection this module inferred.
    """

    stages = sorted(
        {
            str(reason.stage)
            for payload in (item.before, item.after)
            if payload is not None
            for reason in payload.selection_reasons
        }
    )
    reached = ", ".join(stages) or "recorded selection"
    return f"the comparison's recorded {item.kind} selection of {item.record_id} ({reached})"


# --- the selection's own recorded population, read from the snapshots -------------------------
#
# The comparison's items are a bounded page (ICR-R10). A record's *meaning* is not: it is a fact
# about the selection, and the two snapshots record it whether or not the page that answered this
# request reached it. These readers ask the shipped owners of the recorded population -- the same
# ``read_queries`` owners ICR-R07 and ICR-R08 read through -- for the selected identity's own
# revisions, the realizations recorded under them, the families that directly contain them and those
# families' recorded members. Nothing here decides what the comparison selects: the selection is
# ICR-R07's, and this is the recorded reach of the subject it selected, so a smaller page size cannot
# turn the subject's own record historical or a related sibling's record unrelated.

# The two revision tables and their identity columns, as the shipped listing owner names them.
_REVISION_TABLES: Mapping[str, tuple[str, str]] = {
    "invariant": ("invariant_revision", "invariant_id"),
    "family": ("family_revision", "family_id"),
}


def _snapshot_population(
    subject: RecordedIdentity | None, snapshots: RecordedSnapshots
) -> _Population:
    """The recorded population of one selected identity, read from both snapshots' own owners.

    A side that cannot be opened contributes nothing rather than failing the review: the other side's
    recorded relationships are still facts about the selection, and a review whose knowledge half is
    unreadable is a state this surface already reports elsewhere. Nothing is inferred from a side
    that could not be read.
    """

    population = _Population(subject=subject)
    if subject is None or subject.kind not in _REVISION_TABLES:
        return population
    for database in (snapshots.before, snapshots.after):
        try:
            connection = open_read_only_database(Path(database))
        except (OSError, apsw.Error, KnowledgeStorageError):
            continue
        try:
            namespace = review_namespace(snapshots.repository_id, database)
            _read_recorded_reach(population, connection, namespace, subject)
        except (OSError, apsw.Error, KnowledgeStorageError, ValueError):
            continue
        finally:
            connection.close()
    return population


def _read_recorded_reach(
    population: _Population,
    connection: apsw.Connection,
    repository_id: str,
    subject: RecordedIdentity,
) -> None:
    """Record one snapshot's own answer for the selected identity's recorded relationships."""

    table, identity_column = _REVISION_TABLES[subject.kind]
    revisions = fetch_revision_ids(
        connection, repository_id, table, identity_column, subject.record_id
    )
    if not revisions:
        return
    population.revisions.update(revisions)
    for revision_id in revisions:
        population.revision_identity.setdefault(revision_id, subject)
    if subject.kind == "invariant":
        _read_realizations(population, connection, repository_id, subject, revisions)
        _read_containing_families(population, connection, repository_id, revisions)
    else:
        _read_family_members(population, connection, repository_id, revisions, subject)


def _read_realizations(
    population: _Population,
    connection: apsw.Connection,
    repository_id: str,
    subject: RecordedIdentity,
    revisions: tuple[str, ...],
) -> None:
    """Record the realization rows of the subject's own revisions: its recorded addresses."""

    for row in fetch_realizations_for_invariants(connection, repository_id, revisions):
        population.relationship_identity.setdefault(str(row["claim_id"]), subject)
        if row.get("path") is not None:
            population.path_identity.setdefault(str(row["path"]), subject)


def _read_containing_families(
    population: _Population,
    connection: apsw.Connection,
    repository_id: str,
    revisions: tuple[str, ...],
) -> None:
    """Record every family that directly contains one of the subject's revisions, and its members.

    A family's own members are the recorded closure the specification's "related family/closure
    context" names, and the membership row is the recorded relationship that reaches each of them --
    so the family and each member identity are recorded with that row's own spelling, and the member
    revision is indexed under the *member's* identity rather than under the family it sits in.
    """

    memberships = fetch_memberships_of_invariants(connection, repository_id, revisions)
    if not memberships:
        return
    family_revisions = tuple(sorted({row[1] for row in memberships}))
    families = fetch_family_ids_for_revisions(connection, repository_id, family_revisions)
    for member_id, family_revision_id, _member_revision_id in memberships:
        family_id = families.get(family_revision_id)
        if family_id is None:
            continue
        family = RecordedIdentity(kind="family", record_id=family_id)
        population.revision_identity.setdefault(family_revision_id, family)
        population.relationship_identity.setdefault(member_id, family)
        population.snapshot.setdefault(
            (family.kind, family.record_id),
            f"recorded family membership {member_id} in family revision {family_revision_id}",
        )
    _read_family_members(population, connection, repository_id, family_revisions, subject=None)


def _read_family_members(
    population: _Population,
    connection: apsw.Connection,
    repository_id: str,
    family_revisions: tuple[str, ...],
    subject: RecordedIdentity | None,
) -> None:
    """Record every invariant the named family revisions hold as a member.

    ``subject`` is the selected identity when the selection *is* a family -- there the family's own
    members are what the selection reaches -- and ``None`` when the family was reached from an
    invariant, where the member identities are the context the selection reaches through it.
    """

    rows = fetch_memberships_of_families_full(connection, repository_id, family_revisions)
    if not rows:
        return
    member_revisions = tuple(sorted({row[2] for row in rows}))
    identities = {
        str(row["revision_id"]): str(row["invariant_id"])
        for row in fetch_invariant_revisions(connection, repository_id, member_revisions)
    }
    for member_id, family_revision_id, member_revision_id, _provenance in rows:
        member_identity = identities.get(member_revision_id)
        if member_identity is None:
            continue
        member = RecordedIdentity(kind="invariant", record_id=member_identity)
        population.revision_identity.setdefault(member_revision_id, member)
        if member == subject:
            population.revisions.add(member_revision_id)
            continue
        population.snapshot.setdefault(
            (member.kind, member.record_id),
            f"recorded family membership {member_id} in family revision {family_revision_id}",
        )
