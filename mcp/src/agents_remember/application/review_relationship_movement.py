"""The recorded before/after relationship union of one reviewed subject (ICR-R08@v1).

This module owns one traversal and one projection. The traversal is **the union both snapshots
record**, not the candidate's graph read on its own: the relationships come from the shipped
comparison's own union items -- each carrying the side payloads that selected it -- and the
snapshots' own predecessor tables, so a relationship only the baseline reached stays in the display
that way. The projection is **both sides of each association**, and it is one value per relationship
rather than two one-sided rows.

**Why one value and not two rows.** A realization claim cannot be re-anchored in place (the schema
refuses an anchor update) and cannot be rewritten in place, so what an author's "the realization
moved from A to B" *is*, in this store, is a removed claim at A and a new claim at B whose revision
names the first one's revision in its authored lineage. Rendering those as two unrelated locations is
exactly the packet's non-conforming example: the old association vanishes, because nothing in the
display says the two rows are one association under one invariant identity. So the before sides and
the after side are paired here by the author's own records and by nothing else, and every paired
movement **names the basis it paired on** (``pairing_basis``) rather than asserting a bare movement.

**The union is not bounded by the comparison's selected page** (ICR-R08@v1's ruling). A read selects
the revision a relationship cites, not the revisions that succeeded it, so an association an author
re-recorded onto a successor revision can be recorded in the snapshot while being outside the page.
For every baseline-only relationship this traversal therefore establishes the **head** of that
association's authored successor line -- through :func:`successor_line`, which calls ICR-R07@v1's own
head rule rather than inventing a second one -- and reads the relationships recorded at that head
through the shipped read owners. A head that *is* established and records a relationship yields one
movement with both addresses; a head that is not (a split that never rejoins, or a cycle) yields the
baseline side with the head stated as unresolved **and its reason**, never as the claim that nothing is
recorded; and a head that records nothing yields a sentence that says what was actually read.

**What the sides carry, and what may not be inferred from them.** Each side states which snapshot
fact it is: the relationship row and its recorded address (``recorded``), the row with an address
this display could not read (``unresolved``), an identity the snapshot records with no governing
route (``ungoverned``), an identity whose route declarations were not read (``unavailable``) or an
identity the snapshot does not record at all (``not_recorded``). The last three are different facts
and none is a missing route, so none is defaulted to another or to the repository root. Every
unresolved side stays visible with its own code and reason, which is the packet's Failure And
Recovery Behavior taken as a value.

**The rename inference is labelled and proves nothing.** When one movement's recorded sides name
different addresses, the labelled inference that
:mod:`agents_remember.application.review_rename_inference` owns is attached beside it -- the pairing
Git's own similarity detection reported over the two bound tree objects, the exact command, and the
sentence that says it is an inference about the source and never proof that the invariant moved. No
side, identity, attribution or association is derived from it, and this traversal never reads it.

**Three responsibilities are their own modules, and this one calls them.** The relationships one
snapshot's union items record, with its identity rows and its own edges:
:mod:`agents_remember.application.review_recorded_relationships`. The reviewed identity's recorded
governing-route associations: :mod:`agents_remember.application.review_governing_route`. The labelled
Git rename inference: :mod:`agents_remember.application.review_rename_inference`. What remains here is
the traversal and its display: pairing the recorded sides by the author's own edges, building one
movement per relationship, naming the authored lineage and the unresolved states, and rendering the
pane's address view.

**Scope.** This module reads; it stores nothing, selects no subject, ranks no relationship and
concludes nothing. The head-selection rule is ICR-R07@v1's and is called, not re-derived; the
attribution partition is ICR-R04@v1's and is untouched; the comparison's own coverage, change state
and reached-via values are carried rather than recomputed.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from agents_remember.application.review_governing_route import governing_route_movements
from agents_remember.application.review_recorded_relationships import (
    RecordedRelationship,
    RecordedSnapshot,
    RelationshipLine,
    authored_descendants,
    authored_successor,
    head_anchor_resolver,
    read_line_relationships,
    read_snapshot_relationships,
    recorded_snapshot,
    successor_line,
)
from agents_remember.application.review_relationship_display import (
    ContinuationSearch,
    paired_movement,
    single_sided_movement,
    source_locations,
)
from agents_remember.application.review_rename_inference import (
    RenameInferenceProbe,
    RenameInferenceSources,
    git_rename_inference,
    with_rename_inferences,
)
from agents_remember.memory.knowledge.connection import open_read_only_database
from agents_remember.memory.knowledge.diff_display import TreeSide
from agents_remember.memory.knowledge.refusals import KnowledgeStorageError
from agents_remember.models.knowledge.diff import KnowledgeDiffItem
from agents_remember.models.knowledge.read import KnowledgeReadSeed
from agents_remember.models.knowledge.review_relationships import (
    ReviewPairingBasis,
    ReviewRelationshipMovement,
)

__all__ = [
    "RelationshipSources",
    "relationship_movements",
    "source_locations",
]

# The declared order of the movement stream. It is the comparison's own kind order -- revisions, then
# families, then memberships, then realizations, then the advertised frontier -- with the reviewed
# identity's governing route last, because it is the one association the union does not carry as an
# item. Inside a kind the order is the recorded identity, the transition and the displayed addresses,
# so two runs over the same snapshots render the same stream: a display whose row order moved between
# runs could not be compared by a reader.
_KIND_ORDER: Mapping[str, int] = {
    "invariant": 1,
    "family": 2,
    "membership": 3,
    "realization": 4,
    "advertised_family": 5,
    "governing_route": 6,
}


@dataclass(frozen=True)
class RelationshipSources:
    """Everything one relationship-union traversal reads, as one value.

    The two snapshots, the reviewed subject, the two bound code trees and the rename-inference seam
    travel together because they are one measurement: a side read from another snapshot's file, or a
    route read under the other snapshot's namespace, would be a different comparison wearing this
    one's identity.
    """

    repository_id: str
    before_database: Path
    after_database: Path
    selector: KnowledgeReadSeed | None = None
    before_code: TreeSide | None = None
    after_code: TreeSide | None = None
    rename_probe: RenameInferenceProbe = git_rename_inference


def relationship_movements(
    items: Sequence[KnowledgeDiffItem],
    sources: RelationshipSources,
) -> tuple[ReviewRelationshipMovement, ...]:
    """Traverse the recorded before/after relationship union and display both sides of each entry.

    The union items are the shipped comparison's own page, so a relationship only the baseline
    selected is traversed exactly like one both snapshots hold; nothing here re-selects, widens a
    frontier or reads the candidate's graph as though it were the whole history. The reviewed
    subject's governing routes are read for that identity on each snapshot and displayed as their own
    movements, one per route, because they are recorded associations the comparison's union does not
    carry as items.
    """

    before_connection = open_read_only_database(Path(sources.before_database))
    after_connection = open_read_only_database(Path(sources.after_database))
    try:
        before = recorded_snapshot(
            "before", sources.before_database, sources.repository_id, before_connection
        )
        after = recorded_snapshot(
            "after", sources.after_database, sources.repository_id, after_connection
        )
        movements = _movements(items, before, after, sources)
        routes = governing_route_movements(sources.selector, sources.repository_id, before, after)
    finally:
        before_connection.close()
        after_connection.close()
    return with_rename_inferences(
        movements,
        routes,
        RenameInferenceSources(
            before_code=sources.before_code,
            after_code=sources.after_code,
            probe=sources.rename_probe,
        ),
    )


# --- the traversal --------------------------------------------------------------------------


def _movements(
    items: Sequence[KnowledgeDiffItem],
    before: RecordedSnapshot,
    after: RecordedSnapshot,
    sources: RelationshipSources,
) -> tuple[ReviewRelationshipMovement, ...]:
    """Build one movement per candidate-side relationship, and one per withdrawn baseline side.

    The candidate's own union is walked first: every relationship it records is displayed with the
    baseline sides the author's records connect to it. Whatever baseline side no candidate
    relationship continues is then searched one step further, at the uniquely established head of its
    authored successor line, because the comparison's page is a selection and the store is not
    (ICR-R08@v1's ruling). Only what neither read finds is displayed as a one-sided association, and
    its sentence states exactly which populations were searched.
    """

    repository_id = sources.repository_id
    baseline = read_snapshot_relationships(items, before, repository_id)
    selected = read_snapshot_relationships(items, after, repository_id)
    recorded = frozenset(side.relationship_id for side in selected)
    line_relationships = _line_relationships(baseline, selected, after, sources)
    candidate = (*selected, *line_relationships)
    pairs = tuple(
        (candidate_side, _matching(baseline, candidate_side, recorded, after.edges))
        for candidate_side in candidate
    )
    paired = {index for _side, matches in pairs for index in matches}
    movements = [
        paired_movement(
            candidate_side,
            _at(baseline, matches),
            after.edges,
            _pairing_basis(_at(baseline, matches), candidate_side, after),
        )
        for candidate_side, matches in pairs
    ]
    movements.extend(
        single_sided_movement(side, _search_of(side, candidate, after, sources))
        for index, side in enumerate(baseline)
        if index not in paired
    )
    return tuple(sorted(movements, key=_movement_order))


def _line_relationships(
    baseline: Sequence[RecordedRelationship],
    selected: Sequence[RecordedRelationship],
    after: RecordedSnapshot,
    sources: RelationshipSources,
) -> tuple[RecordedRelationship, ...]:
    """Read every relationship recorded on the authored lines of the baseline-only associations.

    The comparison's page is a selection and the store is not (ICR-R08@v1's ruling), so an association
    the candidate re-recorded onto any successor revision is displayed rather than called withdrawn.
    **The whole line is read, not its head**: a relationship recorded at an intermediate successor is
    as recorded as one at the end, and stopping at the head would report a display boundary as the
    store's state. Each association kind contributes the lines it actually has -- a realization its
    citation's line, a membership both its family revision's line and its member revision's line, an
    advertised link its family revision's line -- and each line is read by the owner for the kind of
    revision it holds. A read the storage owner refuses is not fatal: the line then records nothing
    this traversal can display, which the one-sided sentence states with its reason, because a review
    that refused to open would hide every movement it did reach.
    """

    found: list[RecordedRelationship] = []
    recorded = _ids(selected)
    seen: set[tuple[str, tuple[str, ...]]] = set()
    resolver = None
    for side in baseline:
        if any(
            other.relationship_id == side.relationship_id
            or _continues(side, other, _ids(selected), after.edges)
            for other in selected
        ):
            continue
        for line in _lines_of(side, after):
            if not line.revision_ids or (line.kind, line.revision_ids) in seen:
                continue
            seen.add((line.kind, line.revision_ids))
            if resolver is None:
                resolver = head_anchor_resolver(
                    after.database, sources.repository_id, sources.after_code
                )
            try:
                rows = read_line_relationships(after, sources.repository_id, resolver, line)
            except KnowledgeStorageError:
                continue
            # A row the comparison already selected is the same recorded relationship, and reading the
            # line must not display it twice: the line read is a reach for what the page did *not*
            # hold, so an identity the page holds is skipped here rather than paired with itself.
            found.extend(row for row in rows if row.relationship_id not in recorded)
    return tuple(found)


def _line_citations(side: RecordedRelationship) -> tuple[str, ...]:
    """The revisions whose authored lines one baseline relationship could continue on.

    A realization cites one invariant revision; a membership cites a family revision **and** a member
    revision, so it has two lines. They are the citations the heads are asked about, and the line read
    is built from them.
    """

    citations: list[str] = []
    if side.record_kind == "invariant" or side.member_revision_id is not None:
        citation = side.revision_id if side.record_kind == "invariant" else side.member_revision_id
        if citation is not None:
            citations.append(citation)
    if side.record_kind == "family" and side.revision_id is not None:
        citations.append(side.revision_id)
    return tuple(citations)


def _is_line_head(
    baseline_sides: tuple[RecordedRelationship, ...],
    candidate_side: RecordedRelationship,
    after: RecordedSnapshot,
) -> bool:
    """Whether one line-read row **is** the uniquely established head of a line it continues.

    This is the only condition under which a movement may say "uniquely established head": the row's
    own revision has to be the head ICR-R07's rule establishes for the line its baseline side cites.
    An intermediate descendant fails it, and a line with several ends fails it because no head exists
    to be -- which is what keeps that sentence true about the store.
    """

    if candidate_side.revision_id is None:
        return False
    for side in baseline_sides:
        for citation in _line_citations(side):
            line = successor_line(after, citation)
            if (
                line.head_revision_id is not None
                and line.head_revision_id == candidate_side.revision_id
            ):
                return True
    return False


def _lines_of(side: RecordedRelationship, after: RecordedSnapshot) -> tuple[RelationshipLine, ...]:
    """The authored lines one baseline relationship's continuation could be recorded on.

    A realization cites one invariant revision, while a membership cites a family revision *and* a
    member revision, so a membership may move because either line moved. Every line is offered to the
    read and the pairing rules decide which candidate rows continue the association; nothing here
    decides it by assumption.
    """

    lines: list[RelationshipLine] = []
    if side.record_kind == "invariant" or side.member_revision_id is not None:
        citation = side.revision_id if side.record_kind == "invariant" else side.member_revision_id
        lines.append(
            RelationshipLine(
                kind="invariant",
                revision_ids=authored_descendants(citation or "", after.edges),
            )
        )
    if side.record_kind == "family":
        lines.append(
            RelationshipLine(
                kind="family",
                revision_ids=authored_descendants(side.revision_id or "", after.edges),
            )
        )
    return tuple(lines)


def _line_revisions(side: RecordedRelationship, after: RecordedSnapshot) -> tuple[str, ...]:
    """Every revision of every line one baseline side's continuation was searched on."""

    return tuple(
        sorted({revision for line in _lines_of(side, after) for revision in line.revision_ids})
    )


def _ids(relationships: Sequence[RecordedRelationship]) -> frozenset[str]:
    """The recorded relationship identities one population holds."""

    return frozenset(side.relationship_id for side in relationships)


def _search_of(
    baseline_side: RecordedRelationship,
    candidate: Sequence[RecordedRelationship],
    after: RecordedSnapshot,
    sources: RelationshipSources,
) -> ContinuationSearch:
    """What the traversal searched for one baseline side's continuation, with its own result.

    The value exists so a one-sided movement's sentences can be *tested* rather than assumed: it names
    the candidate relationships this review read, the rows recorded on the citation's authored lines
    (so a line that *does* hold a relationship is named instead of denied), every revision of those
    lines that was read (so a negative about them is complete), and the line's shape -- its ends, and
    the unique head when there is one. A sentence stated from anything less than this is the assertion
    V1/V2(a) removed and the lineage sentence the second round caught.
    """

    line = successor_line(after, baseline_side.revision_id or "")
    revisions = _line_revisions(baseline_side, after)
    on_the_line = tuple(
        other
        for other in candidate
        if other.origin == "successor_head"
        and (
            (other.revision_id is not None and other.revision_id in revisions)
            or (other.member_revision_id is not None and other.member_revision_id in revisions)
        )
    )
    return ContinuationSearch(
        candidates=tuple(candidate),
        same_citation=_same_citation(baseline_side, candidate),
        successors=line.successors,
        head_revision_id=line.head_revision_id,
        head_detail=line.detail,
        head_state=line.state,
        head_candidates=line.candidate_heads,
        head_read=bool(on_the_line),
        line_revisions=revisions,
        line_rows=on_the_line,
        repository_id=sources.repository_id,
    )


def _same_citation(
    baseline_side: RecordedRelationship, candidate: Sequence[RecordedRelationship]
) -> tuple[RecordedRelationship, ...]:
    """The candidate rows this review read that cite the *exact same thing* as one baseline side.

    The test is kind-specific because the citations are. A realization's citation is the invariant
    revision it realizes; a membership's is a member revision **and** the family revision that holds
    it, so a sibling membership of the same family revision holding a *different* member is not this
    association's record and is never named as one; an advertised link is matched on its recorded
    relationship identity.
    """

    if baseline_side.record_kind == "invariant":
        return tuple(
            other
            for other in candidate
            if other.kind == baseline_side.kind and other.revision_id == baseline_side.revision_id
        )
    if baseline_side.kind == "membership":
        return tuple(
            other
            for other in candidate
            if other.kind == "membership"
            and other.member_revision_id == baseline_side.member_revision_id
            and other.member_revision_id is not None
        )
    return tuple(
        other
        for other in candidate
        if other.kind == baseline_side.kind
        and other.relationship_id == baseline_side.relationship_id
    )


def _pairing_basis(
    baseline_sides: tuple[RecordedRelationship, ...],
    candidate_side: RecordedRelationship,
    after: RecordedSnapshot,
) -> ReviewPairingBasis:
    """Name the recorded relation one pairing was made on, from the facts the pairing itself used.

    The distinction the sentences turn on is between a row that **is** the line's uniquely established
    head and a row read somewhere else on the line: an intermediate descendant, or a line whose ends
    are several so that no head exists. Only the first may be described as the head, so they are two
    bases rather than one, and the choice is made here from ICR-R07's own head rule.
    """

    if any(side.relationship_id == candidate_side.relationship_id for side in baseline_sides):
        return "same_recorded_relationship"
    if candidate_side.kind == "advertised_family":
        return "recorded_family_revision"
    if candidate_side.kind == "membership" and all(
        side.member_revision_id == candidate_side.member_revision_id for side in baseline_sides
    ):
        return "same_member_revision"
    if candidate_side.origin == "successor_head":
        return (
            "authored_successor_head_revision"
            if _is_line_head(baseline_sides, candidate_side, after)
            else "authored_successor_line_revision"
        )
    if candidate_side.kind == "membership":
        return "authored_successor_member_revision"
    return "authored_successor_revision"


def _movement_order(movement: ReviewRelationshipMovement) -> tuple[object, ...]:
    """One movement's position in the declared stream order."""

    return (
        _KIND_ORDER[movement.relationship_kind],
        movement.record_id or "",
        movement.transition,
        "" if movement.after is None else movement.after.path or "",
        tuple(side.path or "" for side in movement.before),
    )


def _at(
    relationships: Sequence[RecordedRelationship], indices: Sequence[int]
) -> tuple[RecordedRelationship, ...]:
    """Return the recorded relationships at the named positions, in their own order."""

    return tuple(relationships[index] for index in indices)


def _matching(
    baseline: Sequence[RecordedRelationship],
    candidate_side: RecordedRelationship,
    recorded: frozenset[str],
    edges: frozenset[tuple[str, str]],
) -> tuple[int, ...]:
    """Return the positions of the baseline sides one candidate side is the continuation of.

    Two and only two things continue a baseline association, and both are the author's own records:
    the **same recorded relationship row**, which both snapshots selected and which therefore needs
    no edge to be recognised; or an **authored replacement**, where the candidate recorded a new
    relationship on a revision whose authored predecessor is the revision the baseline relationship
    cited -- and the baseline row is *withdrawn*, meaning the candidate records no relationship under
    that identity any more. The withdrawal half is what keeps a genuinely new relationship apart from
    its siblings: a baseline relationship the candidate still records was not replaced by anything,
    and pairing a new claim with every claim that happened to cite the same predecessor revision
    would display one movement where the snapshots record one replacement and several surviving
    siblings.
    """

    return tuple(
        index
        for index, baseline_side in enumerate(baseline)
        if _continues(baseline_side, candidate_side, recorded, edges)
    )


def _continues(
    baseline_side: RecordedRelationship,
    candidate_side: RecordedRelationship,
    recorded: frozenset[str],
    edges: frozenset[tuple[str, str]],
) -> bool:
    """Whether one baseline side is the association one candidate side continues."""

    if baseline_side.kind != candidate_side.kind:
        return False
    if baseline_side.relationship_id == candidate_side.relationship_id:
        return True
    if baseline_side.relationship_id in recorded:
        return False
    if (
        baseline_side.item_coverage == "present_outside_selection"
        and candidate_side.origin == "selection"
    ):
        # The candidate's snapshot holds this relationship and its declared selection did not reach
        # it: a selection fact, not a replacement, and the design's own named misreading is exactly
        # reading it as one. A row read at a successor head is a different matter: it is *not* the
        # same record, and the pairing below is what decides whether it continues this side.
        return False
    return _replaced(baseline_side, candidate_side, edges)


def _replaced(
    baseline_side: RecordedRelationship,
    candidate_side: RecordedRelationship,
    edges: frozenset[tuple[str, str]],
) -> bool:
    """Whether the candidate's row is the same association one authored step further along.

    A realization is paired when the revision the candidate cites is the baseline revision or an
    authored *descendant* of it -- through the recorded edges, so a head reached over several
    authored steps is still the same line -- which is the relation ICR-R08@v1's ruling selects the
    member side by. A membership is paired **member-wise**: the same member revision under a moved
    family revision is the association continuing (the family moved, the member did not), and a
    member that itself moved must be an authored descendant of the baseline member revision. The
    family revision's own edge is deliberately *not* enough on its own: it would pair a member with a
    row holding a different member, which is how a display comes to assert that one member's
    association continued another member's row.
    """

    family_moved = authored_successor(baseline_side.revision_id, candidate_side.revision_id, edges)
    if baseline_side.kind == "advertised_family":
        return family_moved
    if baseline_side.kind == "membership":
        if baseline_side.member_revision_id == candidate_side.member_revision_id:
            # The same member, recorded again in the family's successor revision: the member stayed
            # and the family moved, which is a reassignment and not a member moving.
            return family_moved
        # A different member continues this association only when the member itself moved along its
        # own authored line *and* the family revision is the same or a successor of this one. The
        # family identity follows from the family revision's own line, so a different family holding
        # the same member can never pair here.
        return authored_successor(
            baseline_side.member_revision_id, candidate_side.member_revision_id, edges
        ) and (baseline_side.revision_id == candidate_side.revision_id or family_moved)
    # A realization: the candidate's citation is a step further along the author's own line than the
    # baseline's. Two rows citing the same revision are *not* one association -- that is what the
    # same-recorded-row rule above is for -- so this asks the strict form.
    return authored_successor(baseline_side.revision_id, candidate_side.revision_id, edges)


# --- one movement ---------------------------------------------------------------------------
