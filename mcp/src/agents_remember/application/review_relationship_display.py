"""What one recorded relationship is displayed as (ICR-R08@v1).

The traversal produces the population; this module displays one relationship of it, and it owns the
three things that surround a movement:

* **both recorded sides**, each stating which snapshot fact it is -- the relationship row and its
  address (``recorded``), the row with an address this display could not read (``unresolved``), an
  identity the snapshot records with no governing route (``ungoverned``) or an identity the snapshot
  does not record at all (``not_recorded``). The last two are different facts and neither is a missing
  route.
* **the authored lineage and the unresolved states**, read from the snapshots' own predecessor rows and
  from the reads the sides carry: a succession, a split, a merge, and one gap per fact this display
  could not establish, each with its side, its code and the read's own reason.
* **the pane's address view**, one location per recorded realization side, each carrying the identity
  both sides sit under and the movement it belongs to, so the row that used to show only the
  candidate's address now names the association as well.

Nothing here selects, ranks or concludes: the recorded facts are rendered as they are, a movement the
author's edges do not connect stays two one-sided movements, and no similarity, version, label or
insertion order participates in any of it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from agents_remember.application.review_recorded_relationships import (
    RecordedRelationship,
)
from agents_remember.models.knowledge.review import ReviewSourceLocation
from agents_remember.models.knowledge.review_relationships import (
    ReviewAuthoredLineage,
    ReviewPairingBasis,
    ReviewRelationshipGap,
    ReviewRelationshipMovement,
    ReviewRelationshipSide,
    ReviewRelationshipTransition,
)

__all__ = [
    "ContinuationSearch",
    "paired_movement",
    "single_sided_movement",
    "source_locations",
]


@dataclass(frozen=True)
class ContinuationSearch:
    """What one baseline side's continuation search read, so its sentence can be *tested*.

    A one-sided movement states why the other side is not there, and those sentences may only say what
    was actually read. This value carries every fact a truthful sentence needs: the candidate
    relationships this review holds (so a negative can be tested against them), the rows that cite the
    exact same thing (so a relationship the store *does* record is named instead of denied), **every
    revision of the citation's authored lines that was read** and the rows found on them (so a negative
    about the line is complete, and a split's lineage sentence can name what the line holds), and the
    line's shape -- its ends, and the unique head when there is one.
    """

    candidates: tuple[RecordedRelationship, ...] = ()
    same_citation: tuple[RecordedRelationship, ...] = ()
    successors: tuple[str, ...] = ()
    head_revision_id: str | None = None
    head_detail: str = ""
    head_state: Literal["none", "established", "unresolved"] = "none"
    head_candidates: tuple[str, ...] = ()
    head_read: bool = False
    line_revisions: tuple[str, ...] = ()
    line_rows: tuple[RecordedRelationship, ...] = ()
    repository_id: str = ""


def source_locations(
    movements: Sequence[ReviewRelationshipMovement],
) -> tuple[ReviewSourceLocation, ...]:
    """The address view of the same projection: one location per recorded realization side.

    The pane has always listed one location per selected claim, and that is what this renders: the
    before side's location and the after side's when they are two recorded relationships or two
    recorded addresses, and the single location when both snapshots recorded the same row at the same
    address. Each location now also carries the movement it belongs to -- both sides, the identity
    both sit under and the transition between them -- so the two rows of a moved realization are
    readable as one association instead of two unrelated locations.

    A side whose recorded address could not be read has no address to list, and it is not invented:
    the movement carries that side with its own unresolved state and reason, and this view lists the
    sides that have an address. One row per *recorded side* rather than one per movement that displays
    it, because this view is a list of addresses and an address two movements continue is one address.
    """

    locations: list[ReviewSourceLocation] = []
    seen: set[tuple[str, str]] = set()
    for movement in movements:
        if movement.relationship_kind != "realization":
            continue
        for side in _displayed_sides(movement):
            if side.path is None or (str(side.relationship_id), side.path) in seen:
                continue
            seen.add((str(side.relationship_id), side.path))
            locations.append(_location(movement, side))
    return tuple(locations)


def paired_movement(
    candidate_side: RecordedRelationship,
    baseline_sides: tuple[RecordedRelationship, ...],
    edges: frozenset[tuple[str, str]],
    pairing_basis: ReviewPairingBasis,
) -> ReviewRelationshipMovement:
    """Display one candidate-side relationship with every baseline side this review paired to it.

    ``pairing_basis`` is the recorded relation the pairing was made on, and the statement spells it
    out: two rows are displayed as one association because of *that* relation, never because their
    addresses look alike. A candidate row read at the head of a successor line says so as well, so a
    reader can tell a row the comparison selected from one this review read outside its page.
    """

    before = tuple(_side_of(relationship) for relationship in baseline_sides)
    after = _side_of(candidate_side)
    transition: ReviewRelationshipTransition = (
        "added"
        if not baseline_sides
        else _transition(candidate_side.kind, baseline_sides, candidate_side)
    )
    return ReviewRelationshipMovement(
        relationship_kind=candidate_side.kind,  # type: ignore[arg-type]
        record_kind=candidate_side.record_kind,
        record_id=candidate_side.record_id,
        transition=transition,
        before=before,
        after=after,
        pairing_basis=pairing_basis if baseline_sides else None,
        lineage=_lineage(baseline_sides, candidate_side, edges),
        gaps=_paired_gaps(before, after, candidate_side, baseline_sides, edges),
        statement=_paired_statement(candidate_side, baseline_sides, transition, pairing_basis),
    )


def single_sided_movement(
    baseline_side: RecordedRelationship,
    search: ContinuationSearch,
) -> ReviewRelationshipMovement:
    """Display one baseline-side association no candidate-side relationship continues.

    Two different one-sided facts live here and the comparison's own coverage decides between them: a
    relationship the candidate's snapshot does not hold at all is ``retracted``, and one it holds
    while the declared selection did not reach it is ``outside_selection`` -- "present but outside the
    selected scope is not deletion" as a state of this vocabulary rather than a note a reader has to
    remember.

    The sentence is built from ``search`` and from nothing else, because a reason may only state what
    was read: a candidate relationship citing the same revision is *named* rather than denied, a
    successor line whose head could not be established is displayed as unresolved with its reason, and
    only a negative the search actually establishes is asserted.
    """

    outside = baseline_side.item_coverage == "present_outside_selection"
    return ReviewRelationshipMovement(
        relationship_kind=baseline_side.kind,  # type: ignore[arg-type]
        record_kind=baseline_side.record_kind,
        record_id=baseline_side.record_id,
        transition="outside_selection" if outside else "retracted",
        before=(_side_of(baseline_side),),
        gaps=_single_sided_gaps(baseline_side, search),
        lineage=_withdrawal_lineage(baseline_side, search),
        statement=(
            _outside_selection_statement(baseline_side)
            if outside
            else _withdrawal_statement(baseline_side, search)
        ),
    )


def _side_of(relationship: RecordedRelationship) -> ReviewRelationshipSide:
    """Render one recorded relationship side, with the state that says which fact it is."""

    return ReviewRelationshipSide(
        side=relationship.side,  # type: ignore[arg-type]
        state="recorded" if relationship.anchor_readable else "unresolved",
        item_coverage=relationship.item_coverage,
        relationship_id=relationship.relationship_id,
        path=relationship.path,
        role=relationship.role,
        rationale=relationship.rationale,
        resolution=relationship.resolution,
        resolution_detail=relationship.resolution_detail,
        recorded_source_identity=relationship.recorded_source_identity,
        observed_source_identity=relationship.observed_source_identity,
        change_state=relationship.change_state,
        reached_via=relationship.reached_via,
        record_kind=relationship.record_kind,
        record_id=relationship.record_id,
        revision_id=relationship.revision_id,
        member_revision_id=relationship.member_revision_id,
        detail=_side_detail(relationship),
    )


def _outside_selection_statement(baseline_side: RecordedRelationship) -> str:
    """State that one association is displayed because the other side's selection did not reach it."""

    return (
        f"the baseline records {_recorded_word(baseline_side)} and the candidate's declared selection "
        "did not reach this relationship: the candidate's snapshot holds the record, so this is a fact "
        "about the selection and never a deletion, and the recorded side is displayed as it stands"
    )


def _single_sided_gaps(
    relationship: RecordedRelationship, search: ContinuationSearch
) -> tuple[ReviewRelationshipGap, ...]:
    """The gaps one one-sided association states: its own unresolved facts, and its successor line.

    The line's gap exists because a head that could not be established is a fact about *this* display
    and not about the store: the association may well be recorded at one of the line's several ends,
    and saying "no relationship is recorded" there would be the assertion ICR-R08@v1's ruling forbids.
    It is stated on the before side, because the movement displays no after side to attach it to.
    """

    gaps = list(_side_gaps((_side_of(relationship),)))
    if relationship.record_id is None:
        gaps.append(_identity_unresolved_gap(relationship))
    if search.head_state == "unresolved" and search.line_revisions:
        gaps.append(
            ReviewRelationshipGap(
                side=relationship.side,  # type: ignore[arg-type]
                field="successor_line",
                code="successor_line_unresolved",
                detail=(
                    f"{search.head_detail}, so the association's continuation is not one revision; "
                    f"this review read every revision of the line "
                    f"({', '.join(search.line_revisions)}) and found no relationship for this "
                    "citation there, which is what is asserted -- no negative about an unread "
                    "revision"
                ),
            )
        )
    return tuple(gaps)


def _side_detail(relationship: RecordedRelationship) -> str:
    """State which fact one side is: a recorded address, or the reason there is none."""

    if not relationship.anchor_readable:
        return (
            f"the {relationship.side} snapshot records this relationship's claim "
            f"{relationship.relationship_id} but no readable source address beside it, so its "
            "address is unresolved rather than absent and the other side is not substituted"
        )
    return (
        f"the {relationship.side} snapshot records {relationship.record_kind} "
        f"{relationship.record_id or 'unresolved'} through relationship "
        f"{relationship.relationship_id} at {relationship.path}"
    )


def _transition(
    kind: str,
    baseline_sides: tuple[RecordedRelationship, ...],
    candidate_side: RecordedRelationship,
) -> ReviewRelationshipTransition:
    """Name how the association moved, from the recorded facts on both sides.

    A family association whose member revision is the one the baseline recorded is ``reassigned``:
    the member stayed and the family it sits in changed, which is a different fact from the member
    itself moving. A realization is ``moved`` when any recorded fact of it differs, and ``unchanged``
    when the candidate recorded the same address, role, rationale and revision -- which is what "the
    author re-authored the same association under a new row identity" looks like from here.
    """

    if not baseline_sides:
        return "moved"
    facts = {_association_facts(side) for side in baseline_sides}
    if facts == {_association_facts(candidate_side)}:
        return "unchanged"
    if kind != "realization" and all(
        side.member_revision_id == candidate_side.member_revision_id for side in baseline_sides
    ):
        return "reassigned"
    return "moved"


def _paired_statement(
    candidate_side: RecordedRelationship,
    baseline_sides: tuple[RecordedRelationship, ...],
    transition: str,
    pairing_basis: ReviewPairingBasis,
) -> str:
    """State the whole movement: both recorded associations, their identity, and **why** they are one.

    The basis is the point of the sentence. Two recorded rows are displayed as one association because
    of a *recorded* relation -- the row selected twice, the same member under a moved family revision,
    or an authored old/new edge between the revisions the rows cite -- and the sentence says which,
    because the addresses themselves prove nothing: an absence of one address and the presence of
    another is a deletion and an addition unless an author's own record connects them.
    """

    after = _recorded_word(candidate_side)
    identity = candidate_side.record_id or "an identity neither side resolves"
    if not baseline_sides:
        return (
            f"the candidate records {after} under {candidate_side.record_kind} {identity} and no "
            "relationship this review read -- in the comparison's union or at the head of that "
            "citation's authored successor line -- is connected to it, so this association is "
            "displayed as the candidate's added side and no baseline side is substituted"
        )
    before = "; ".join(_recorded_word(side) for side in baseline_sides)
    return (
        f"the baseline records {before} and the candidate records {after} under "
        f"{candidate_side.record_kind} {identity}; the association is displayed as {transition} "
        f"paired by {_basis_sentence(pairing_basis, candidate_side, baseline_sides)}, which is a "
        "recorded relation and not a resemblance between the two addresses"
    )


def _basis_sentence(
    pairing_basis: ReviewPairingBasis,
    candidate_side: RecordedRelationship,
    baseline_sides: tuple[RecordedRelationship, ...],
) -> str:
    """The recorded relation one pairing was made on, in one clause, with the ids it used.

    A table rather than a chain of branches: every basis is a different recorded relation and each one
    reads its own ids out of the two sides, so a basis added to the vocabulary is a row here rather
    than an eighth return.
    """

    spec = _BASIS_SENTENCES[pairing_basis]
    return spec.sentence.format(
        after=candidate_side.relationship_id,
        revision=candidate_side.revision_id or "an unresolved revision",
        member=candidate_side.member_revision_id or "an unresolved member revision",
        member_clause=(
            ""
            if candidate_side.member_revision_id is None
            else f" holding member revision {candidate_side.member_revision_id}"
        ),
        identity=candidate_side.record_id or "an unresolved identity",
        kind=candidate_side.record_kind,
        cited=", ".join(sorted({side.revision_id or "unresolved" for side in baseline_sides})),
    )


@dataclass(frozen=True)
class _BasisSentence:
    """One pairing basis's own sentence, with the fields it reads from the two displayed sides."""

    sentence: str


_BASIS_SENTENCES: Mapping[ReviewPairingBasis, _BasisSentence] = {
    "same_recorded_relationship": _BasisSentence(
        "the same recorded relationship {after}, which both snapshots record"
    ),
    "same_governed_identity": _BasisSentence(
        "the {kind} identity {identity}'s own recorded governing-route association, read on each "
        "snapshot"
    ),
    "same_member_revision": _BasisSentence(
        "the member revision {member}, which the baseline row also holds, under the family revision "
        "the candidate moved to"
    ),
    "authored_successor_head_revision": _BasisSentence(
        "the uniquely established head {revision} of the authored successor line of the revision the "
        "baseline row cites ({cited}), read outside the comparison's selected page"
    ),
    "authored_successor_line_revision": _BasisSentence(
        "the authored successor line of the revision the baseline row cites ({cited}), every authored "
        "revision of which this review read outside the comparison's selected page, and this row "
        "records revision {revision}{member_clause} rather than the line's head"
    ),
    "authored_successor_member_revision": _BasisSentence(
        "the authored successor line of the member revision the baseline row holds, ending at "
        "{member}"
    ),
    "recorded_family_revision": _BasisSentence(
        "the authored successor line of the family revision the baseline row holds, ending at "
        "{revision}"
    ),
    "authored_successor_revision": _BasisSentence(
        "the authored successor line of the revision the baseline row cites ({cited}), which the "
        "candidate's revision {revision} descends from"
    ),
}


def _recorded_word(relationship: RecordedRelationship) -> str:
    """One side's recorded association in one phrase, for the movement's own sentence."""

    address = "no readable address" if relationship.path is None else relationship.path
    revision = relationship.revision_id or "an unresolved revision"
    return f"{relationship.kind} {relationship.relationship_id} at {address} (revision {revision})"


def _withdrawal_statement(baseline_side: RecordedRelationship, search: ContinuationSearch) -> str:
    """State that one baseline association is withdrawn, and only what the reads establish.

    Four states, in the order a truthful sentence has to consider them:

    * the candidate records a relationship for the **same citation** -- the same revision, or a
      membership holding the same member -- under a row no authored edge connects to this one. It is
      *named*, because one payload may not deny a relationship another row of it displays;
    * the citation has an authored successor line whose head is **unresolved** (a split that never
      rejoins, or a cycle): whether a relationship is recorded there is stated as unresolved, with the
      reason, and no negative is asserted (ICR-R08@v1's ruling);
    * the line's head **is** established: the sentence says which head this review read and that it
      records no relationship citing this revision;
    * the citation records **no successor at all**: the negative is stated about the populations this
      review actually read, and never as a claim about the snapshot's whole content.
    """

    citation = baseline_side.revision_id or "the revision this relationship cited"
    prefix = (
        f"the baseline records {_recorded_word(baseline_side)} and the candidate records no "
        "relationship under this row"
    )
    if search.same_citation:
        recorded = "; ".join(_recorded_word(other) for other in search.same_citation)
        return (
            f"{prefix}, while it does record {recorded} for the same citation ({citation}); that "
            "relationship is displayed as its own movement rather than paired with this one, because "
            "no authored edge connects the two rows"
        )
    if search.line_rows:
        recorded = "; ".join(_recorded_word(other) for other in search.line_rows)
        return (
            f"{prefix}; the candidate's authored line of {citation} records {recorded}, which this "
            "review displays as its own movement rather than pairing it with this one, because no "
            "authored relation connects the two rows"
        )
    if search.head_state == "unresolved":
        return (
            f"{prefix}; {search.head_detail}, and this review read every revision of that line "
            f"({', '.join(search.line_revisions)}) and found no relationship for this citation there, "
            "so the association is displayed as withdrawn with its old address while the revision was "
            "split"
        )
    if search.line_revisions:
        unique = (
            f", whose uniquely established head is {search.head_revision_id}"
            if search.head_revision_id is not None
            else ""
        )
        return (
            f"{prefix}; the candidate's snapshot records {len(search.successors)} authored "
            f"successor(s) of {citation} ({', '.join(search.successors)}){unique}, and this review read "
            f"every revision of that line ({', '.join(search.line_revisions)}) and found no "
            "relationship for this citation there, so nothing this review read claims the association "
            "now and it is displayed as withdrawn with its old address"
        )
    return (
        f"{prefix}, and no relationship citing {citation} was read: the candidate's snapshot records "
        "no authored successor of it and this review read the comparison's union, so this is a "
        "withdrawn association rather than a movement, and the old address is still displayed"
    )


def _identity_unresolved_gap(relationship: RecordedRelationship) -> ReviewRelationshipGap:
    """State that a relationship's identity could not be resolved, naming the relationship."""

    return ReviewRelationshipGap(
        side=relationship.side,  # type: ignore[arg-type]
        field="identity",
        code="identity_not_recorded",
        detail=(
            f"the {relationship.side} snapshot records relationship {relationship.relationship_id} "
            f"citing revision {relationship.revision_id or 'none'} and this traversal could not read "
            "the identity that revision belongs to, so no preserved identity is displayed for it"
        ),
    )


def _association_facts(relationship: RecordedRelationship) -> tuple[object, ...]:
    """The recorded facts of one association, without the row identity it happens to be stored in.

    The row identity is deliberately not compared: the schema refuses an in-place rewrite of a claim,
    so a relationship whose content is unchanged necessarily appears under a new row identity when it
    is re-authored, and reporting that as a movement would report the storage's immutability as a fact
    about the association.
    """

    return (
        relationship.path,
        relationship.role,
        relationship.rationale,
        relationship.revision_id,
        relationship.record_id,
        relationship.member_revision_id,
    )


def _successors(revision_id: str | None, edges: frozenset[tuple[str, str]]) -> tuple[str, ...]:
    """Return every revision the named one is an authored predecessor of, in sorted order."""

    if revision_id is None:
        return ()
    return tuple(sorted(child for child, parent in edges if parent == revision_id))


def _predecessors(revision_id: str | None, edges: frozenset[tuple[str, str]]) -> tuple[str, ...]:
    """Return every authored predecessor the named revision recorded, in sorted order."""

    if revision_id is None:
        return ()
    return tuple(sorted(parent for child, parent in edges if child == revision_id))


def _lineage(
    baseline_sides: tuple[RecordedRelationship, ...],
    candidate_side: RecordedRelationship,
    edges: frozenset[tuple[str, str]],
) -> tuple[ReviewAuthoredLineage, ...]:
    """Display the author's own edges the movement's revisions take part in.

    Three relations and no others: the candidate revision's declaration that a baseline revision is
    its predecessor; a baseline revision the author recorded **several** successors of, which is the
    split the packet names; and a candidate revision that recorded several predecessors, which is the
    merge. Each is read from the snapshots' own predecessor rows and each names every other revision
    of the relation, so a split is displayed with all of its successors rather than with the one this
    pairing happened to follow.
    """

    entries: dict[tuple[str, str], ReviewAuthoredLineage] = {}
    predecessors = _predecessors(candidate_side.revision_id, edges)
    for baseline_side in baseline_sides:
        _add_succession(entries, baseline_side, candidate_side, predecessors)
        _add_split(entries, baseline_side, edges)
    _add_merge(entries, candidate_side, predecessors)
    return tuple(entries.values())


def _add_succession(
    entries: dict[tuple[str, str], ReviewAuthoredLineage],
    baseline_side: RecordedRelationship,
    candidate_side: RecordedRelationship,
    predecessors: tuple[str, ...],
) -> None:
    """Record the authored successor edge between one baseline side and the candidate's."""

    revision_id = baseline_side.revision_id
    if revision_id is None or revision_id not in predecessors:
        return
    assert candidate_side.revision_id is not None
    entries[("succession", revision_id)] = ReviewAuthoredLineage(
        kind="succession",
        side="after",
        revision_id=candidate_side.revision_id,
        related_revision_ids=(revision_id,),
        statement=(
            f"the candidate's revision {candidate_side.revision_id} records {revision_id} as an "
            "authored predecessor, which is the author's own statement that it replaced it"
        ),
    )


def _add_split(
    entries: dict[tuple[str, str], ReviewAuthoredLineage],
    baseline_side: RecordedRelationship,
    edges: frozenset[tuple[str, str]],
) -> None:
    """Record one baseline revision the author recorded several successors of."""

    successors = _successors(baseline_side.revision_id, edges)
    if len(successors) < 2:
        return
    assert baseline_side.revision_id is not None
    entries[("split", baseline_side.revision_id)] = ReviewAuthoredLineage(
        kind="split",
        side="before",
        revision_id=baseline_side.revision_id,
        related_revision_ids=successors,
        statement=(
            f"the candidate's snapshot records {len(successors)} authored successors of "
            f"{baseline_side.revision_id} ({', '.join(successors)}), so the revision was split and "
            "no single successor is displayed as its replacement"
        ),
    )


def _add_merge(
    entries: dict[tuple[str, str], ReviewAuthoredLineage],
    candidate_side: RecordedRelationship,
    predecessors: tuple[str, ...],
) -> None:
    """Record a candidate revision that recorded several predecessors."""

    if len(predecessors) < 2 or candidate_side.revision_id is None:
        return
    entries[("merge", candidate_side.revision_id)] = ReviewAuthoredLineage(
        kind="merge",
        side="after",
        revision_id=candidate_side.revision_id,
        related_revision_ids=predecessors,
        statement=(
            f"the candidate's revision {candidate_side.revision_id} records "
            f"{len(predecessors)} authored predecessors ({', '.join(predecessors)}), so this "
            "association continues more than one recorded revision"
        ),
    )


def _withdrawal_lineage(
    baseline_side: RecordedRelationship, search: ContinuationSearch
) -> tuple[ReviewAuthoredLineage, ...]:
    """The authored split one withdrawn association sits under, stated from what was read.

    The sentence is built from the same :class:`ContinuationSearch` the movement's reason is, because
    the split branch of this sentence is where a denial used to survive the migration: saying "a
    relationship for none of them" from the successor *edges* alone is false whenever one of those
    successors records a relationship. Two facts are stated instead, and both are tested: how many
    authored successors the author recorded, and what this review read on the line -- the rows it holds
    being named as their own movements, or the complete negative that no revision of the line records
    this citation.
    """

    successors = search.successors
    if len(successors) < 2 or baseline_side.revision_id is None:
        return ()
    read = (
        f"this review read every revision of that line ({', '.join(search.line_revisions)})"
        if search.line_revisions
        else "this review read the line's revisions"
    )
    recorded = (
        "the relationships recorded there are displayed as their own movements rather than paired "
        f"with this one ({', '.join(other.relationship_id for other in search.line_rows)})"
        if search.line_rows
        else "no revision of that line records a relationship for this citation"
    )
    return (
        ReviewAuthoredLineage(
            kind="split",
            side="before",
            revision_id=baseline_side.revision_id,
            related_revision_ids=successors,
            statement=(
                f"the candidate's snapshot records {len(successors)} authored successors of "
                f"{baseline_side.revision_id} ({', '.join(successors)}), so the revision was split and "
                f"no single successor is displayed as its replacement; {read} and {recorded}"
            ),
        ),
    )


def _paired_gaps(
    before: tuple[ReviewRelationshipSide, ...],
    after: ReviewRelationshipSide,
    candidate_side: RecordedRelationship,
    baseline_sides: tuple[RecordedRelationship, ...],
    edges: frozenset[tuple[str, str]],
) -> tuple[ReviewRelationshipGap, ...]:
    """Every fact this display could not establish about the paired sides, each with its reason."""

    gaps = list(_side_gaps((*before, after)))
    gaps.extend(_identity_gaps(before, after))
    if candidate_side.record_id is None:
        gaps.append(_identity_unresolved_gap(candidate_side))
    if not baseline_sides:
        gaps.extend(_unpaired_predecessor_gaps(candidate_side, edges))
    return tuple(gaps)


def _side_gaps(sides: Sequence[ReviewRelationshipSide]) -> tuple[ReviewRelationshipGap, ...]:
    """One gap per side whose recorded address could not be read or did not resolve exactly.

    Two states, two codes, and both are the packet's Failure And Recovery Behavior as a value: a side
    whose address row could not be read keeps that address unresolved with its reason, and a side whose
    stored address did not resolve to the recorded object in *its own* snapshot's tree -- the file is
    gone, the bytes differ, the object is unavailable -- keeps the read's own sentence beside it. The
    second is the one a deleted source produces, and neither state is filled in from the other side.
    """

    gaps: list[ReviewRelationshipGap] = []
    for side in sides:
        unresolved = _side_gap(side)
        if unresolved is not None:
            gaps.append(unresolved)
    return tuple(gaps)


def _side_gap(side: ReviewRelationshipSide) -> ReviewRelationshipGap | None:
    """One side's own unresolved-address gap, or ``None`` when its address resolved exactly."""

    if side.state == "unresolved":
        return ReviewRelationshipGap(
            side=side.side,
            field="anchor",
            code="anchor_unrecorded",
            detail=(
                f"the {side.side} side's relationship is recorded and its source address is not "
                "readable, so the address is displayed as unresolved with this reason rather than "
                "dropped or filled from the other side"
            ),
        )
    if side.resolution is not None and side.resolution != "exact_recorded_blob":
        return ReviewRelationshipGap(
            side=side.side,
            field="anchor",
            code="anchor_unresolved",
            detail=(
                f"the {side.side} side's recorded address at {side.path} did not resolve exactly "
                f"against that snapshot's code tree ({side.resolution}): "
                f"{side.resolution_detail or 'the read recorded no further reason'}. The recorded "
                "address and the observed identity beside it are displayed as they are, and no "
                "working tree or HEAD is substituted for the tree that was asked"
            ),
        )
    return None


def _identity_gaps(
    before: tuple[ReviewRelationshipSide, ...], after: ReviewRelationshipSide
) -> tuple[ReviewRelationshipGap, ...]:
    """One gap when the two sides name different identities, naming both."""

    named = {side.record_id for side in before if side.record_id is not None}
    if not named or after.record_id in named:
        return ()
    return (
        ReviewRelationshipGap(
            side="after",
            field="identity",
            code="identity_differs",
            detail=(
                f"the baseline sides sit under {', '.join(sorted(named))} and the candidate side "
                f"sits under {after.record_id or 'an identity its snapshot does not record'}; the "
                "association is displayed under the candidate's identity with this difference "
                "stated rather than being paired under either one silently"
            ),
        ),
    )


def _unpaired_predecessor_gaps(
    candidate_side: RecordedRelationship, edges: frozenset[tuple[str, str]]
) -> tuple[ReviewRelationshipGap, ...]:
    """State the authored predecessor the union records no relationship for, when there is one.

    This is the fact that separates "the candidate recorded a new association" from "the candidate
    continued an association whose earlier side this review's union does not hold": the author's edge
    exists and is displayed with the movement, and the earlier side is named as unrecorded rather
    than being left for a reader to assume.
    """

    unrecorded = tuple(revision for revision in _predecessors(candidate_side.revision_id, edges))
    if not unrecorded:
        return ()
    return (
        ReviewRelationshipGap(
            side="after",
            field="predecessor",
            code="predecessor_records_no_relationship",
            detail=(
                f"the candidate's revision {candidate_side.revision_id} records "
                f"{', '.join(unrecorded)} as an authored predecessor, and the before union holds no "
                "relationship for it, so this association's earlier recorded side is outside the "
                "selected union rather than absent from the snapshot"
            ),
        ),
    )


# --- the address view ---------------------------------------------------------------------------


def _displayed_sides(
    movement: ReviewRelationshipMovement,
) -> tuple[ReviewRelationshipSide, ...]:
    """The sides one movement contributes to the address view, in before-then-after order.

    The after side is always listed, and a before side is listed when it is a different recorded
    relationship or a different recorded address -- which is exactly the case where two rows of the
    old view were one association, and where listing only the candidate's row would lose the old one.
    Two sides that are the same row at the same address are one location, as they always were.
    """

    after = movement.after
    if after is None:
        return movement.before
    distinct = tuple(
        side
        for side in movement.before
        if (side.relationship_id, side.path) != (after.relationship_id, after.path)
    )
    return (*distinct, after)


def _location(
    movement: ReviewRelationshipMovement, side: ReviewRelationshipSide
) -> ReviewSourceLocation:
    """Render one recorded realization side as the pane's own location, with its movement.

    The pane's own fields keep the meaning they always had -- the address, the claim, the recorded
    role and the resolution against the bound tree -- and the movement is carried beside them, so the
    address view and the two-sided view are one projection rather than two that could disagree.
    """

    path = side.path
    claim_id = side.relationship_id
    assert path is not None and claim_id is not None
    return ReviewSourceLocation(
        claim_id=claim_id,
        invariant_revision_id=side.revision_id,
        path=path,
        role=side.role,
        rationale=side.rationale,
        recorded_source_identity=side.recorded_source_identity or claim_id,
        observed_source_identity=side.observed_source_identity,
        resolution=side.resolution or "unsupported_locator",
        change_state=side.change_state or "unchanged",
        before_only=_baseline_only(side),
        reached_via=side.reached_via,
        invariant_id=movement.record_id,
        transition=movement.transition,
        recorded_side=side.side,
        counterpart_path=_counterpart_path(movement, side),
        movement=movement,
    )


def _baseline_only(side: ReviewRelationshipSide) -> bool:
    """Whether one location's union item was recorded by the baseline and reached on no other side.

    This is the comparison's own coverage of the item the side came from, carried rather than
    re-derived: ``selected_both`` means both snapshots selected it and every other coverage means
    this side is the only one the union holds.
    """

    return side.side == "before" and side.item_coverage != "selected_both"


def _counterpart_path(
    movement: ReviewRelationshipMovement, side: ReviewRelationshipSide
) -> str | None:
    """The other side's recorded address, when that side records exactly one.

    One address is named; several are not, because choosing one of them to stand for the others is
    the reduction this display exists to avoid. A movement that records two baseline addresses and
    one candidate address therefore names the candidate address on each baseline row and names
    nothing on the candidate row -- the movement itself lists both baseline rows.
    """

    other = _other_side(movement, side)
    paths = {entry.path for entry in other if entry.path is not None}
    return next(iter(paths)) if len(paths) == 1 else None


def _other_side(
    movement: ReviewRelationshipMovement, side: ReviewRelationshipSide
) -> tuple[ReviewRelationshipSide, ...]:
    """The recorded sides of one movement other than the one a location was rendered from."""

    if side.side == "after":
        return movement.before
    return () if movement.after is None else (movement.after,)
