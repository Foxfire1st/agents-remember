"""The relationships one snapshot's union items record, read from that snapshot (ICR-R08@v1).

The traversal this serves reads the union both snapshots record, so its inputs are three facts per
side and this module owns all three: the shipped comparison's own union items (each carrying the side
payloads that selected it), the identity each association revision belongs to -- read from the
snapshot's own revision rows, because the selection read does not project it -- and the snapshot's own
authored predecessor edges.

Nothing here selects, pairs or concludes. A relationship is read exactly as the two records state it,
including the three cases a reader must not see merged: a claim whose anchor row could not be read
keeps that fact, a membership carries the member revision it holds as well as the family revision it
sits in, and an item that is not a relationship at all -- a revision is one of the two things a
relationship relates -- is reported as no relationship rather than as an empty one.

**A relationship's own authored line is read here too, because the comparison's page is not the store.**
A read selects the revision a membership cites and not the revisions that succeeded it, so a
relationship an author re-recorded onto a successor revision is outside the page while being recorded
in the snapshot. :func:`successor_line` states one revision's authored successor line -- its ends, and
the unique head when there is one -- by **ICR-R07@v1's own head rule**
(:func:`~agents_remember.application.review_revision_comparison.revision_heads`, called rather than
re-derived), and :func:`read_line_relationships` reads the relationships recorded at **every revision
of that line**, through the shipped read owners and with the anchor resolution the read seam uses, so a
row read here carries the same observation vocabulary as a selected one. Reading the whole line is
what lets a sentence be *complete* about it: a relationship recorded at an intermediate successor is
found, and a line that records none can be said to record none.

The kind decides which owner answers, and the two are different owners because the two associations
cite different things: a realization's line is a line of *invariant* revisions, read by
:func:`~agents_remember.memory.knowledge.read_queries.fetch_realizations_for_invariants`, while a
membership's family side is a line of *family* revisions holding members, read by
:func:`~agents_remember.memory.knowledge.read_queries.fetch_memberships_of_families_full` -- the owner
keyed by the family revision. A membership's member side is a line of invariant revisions too, so both
lines are read and a membership recorded on either is found.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import apsw

from agents_remember.application.review_revision_comparison import (
    read_snapshot_edges,
    revision_heads,
)
from agents_remember.memory.knowledge.diff_display import TreeSide
from agents_remember.memory.knowledge.logical import dataset_identity
from agents_remember.memory.knowledge.read_anchors import AnchorResolver, anchor_resolver_for
from agents_remember.memory.knowledge.read_queries import (
    fetch_family_revisions,
    fetch_invariant_revisions,
    fetch_memberships_of_families_full,
    fetch_realizations_for_invariants,
)
from agents_remember.models.knowledge.diff import DiffCoverage, KnowledgeDiffItem
from agents_remember.models.knowledge.read import KnowledgeReadContext, ReadItem

__all__ = [
    "RecordedRelationship",
    "RecordedSnapshot",
    "RelationshipLine",
    "SuccessorLine",
    "authored_ancestor",
    "authored_successor",
    "head_anchor_resolver",
    "read_line_relationships",
    "read_snapshot_relationships",
    "recorded_change_state",
    "recorded_snapshot",
    "side_payload",
    "successor_line",
]

# How a successor line ended. ``established`` names the one revision the line's authored edges lead
# to; ``unresolved`` is the state where they do not lead to exactly one and the reason says which
# shape they are; ``none`` is the measured fact that the revision records no successor at all.
SuccessorLineState = Literal["none", "established", "unresolved"]

# The union item kinds that are recorded relationships, with the kind of identity each one sits
# under. A claim and a membership cite a revision and are recorded under an identity; an advertised
# frontier link is a membership the page advertises rather than traverses, and it is recorded under
# its family's identity like the membership it is. A revision item is not a relationship: it is one
# of the two things a relationship relates.
_RELATIONSHIP_KINDS: Mapping[str, Literal["invariant", "family"]] = {
    "realization": "invariant",
    "membership": "family",
    "advertised_family": "family",
}


@dataclass(frozen=True)
class RecordedSnapshot:
    """One snapshot as this traversal reads it: its side name, its file, its edges and a connection."""

    side: str
    database: Path
    connection: apsw.Connection
    edges: frozenset[tuple[str, str]]


@dataclass(frozen=True)
class RecordedRelationship:
    """One recorded side of one relationship, as the store reports it and this read reached it.

    ``origin`` says which of the two reads produced the value: ``selection`` is a side of the
    comparison's own union item, and ``successor_head`` is a row read at the uniquely established head
    of that association's authored successor line, outside the comparison's page. Both are recorded
    facts of the same snapshot; only the first is a side the comparison selected, and the display
    states the difference rather than blurring it.
    """

    kind: str
    item_id: str
    item_coverage: DiffCoverage
    side: str
    relationship_id: str
    record_kind: Literal["invariant", "family"]
    record_id: str | None
    revision_id: str | None
    member_revision_id: str | None
    path: str | None
    role: str | None
    rationale: str | None
    resolution: str | None
    resolution_detail: str | None
    recorded_source_identity: str | None
    observed_source_identity: str | None
    anchor_readable: bool
    change_state: Literal["changed", "unchanged", "not_selected"] | None
    reached_via: tuple[str, ...]
    origin: Literal["selection", "successor_head"] = "selection"


@dataclass(frozen=True)
class RecordedIdentityIndex:
    """The identity each association revision belongs to, per snapshot, read from that snapshot."""

    invariants: Mapping[str, str]
    families: Mapping[str, str]


def recorded_snapshot(
    side: str, database: Path, repository_id: str, connection: apsw.Connection
) -> RecordedSnapshot:
    """Read one snapshot's own authored edges and hold them beside its file and connection.

    The edge reader is ICR-R07@v1's, called rather than re-implemented: it reads both predecessor
    tables of one snapshot through its own read-only connection, which is the one implementation of
    "the authored old/new relations this snapshot records".
    """

    return RecordedSnapshot(
        side=side,
        database=Path(database),
        connection=connection,
        edges=read_snapshot_edges(Path(database), repository_id),
    )


@dataclass(frozen=True)
class SuccessorLine:
    """One revision's authored successor line, with the one head it leads to or the reason it does not.

    ``successors`` is what the revision records directly; ``head_revision_id`` is the line's unique
    end when there is one. An ``unresolved`` line carries every revision that could be its end and the
    sentence that says why no single one was chosen, so a caller states the unresolved selection
    instead of asserting that nothing is recorded.
    """

    state: SuccessorLineState
    revision_id: str
    successors: tuple[str, ...] = ()
    head_revision_id: str | None = None
    candidate_heads: tuple[str, ...] = ()
    detail: str = ""


def authored_descendants(revision_id: str, edges: frozenset[tuple[str, str]]) -> tuple[str, ...]:
    """Every revision one revision is an authored ancestor of, in sorted order.

    The walk follows the snapshots' own ``(successor, predecessor)`` edges outwards. It terminates
    because it visits each revision once, so a cycle in a corrupted graph cannot spin: the revisions
    it has already seen are never re-entered, and :func:`successor_line` reports the cycle's effect
    rather than walking it forever.
    """

    seen: set[str] = set()
    frontier = [revision_id]
    while frontier:
        current = frontier.pop()
        for successor, predecessor in edges:
            if predecessor == current and successor not in seen:
                seen.add(successor)
                frontier.append(successor)
    return tuple(sorted(seen))


def authored_ancestor(
    ancestor: str | None, descendant: str | None, edges: frozenset[tuple[str, str]]
) -> bool:
    """Whether one revision is an authored ancestor of another, through the recorded edges.

    The walk goes *forward* from ``ancestor`` along the snapshots' own ``(successor, predecessor)``
    rows, so a head reached over several authored steps still answers yes. A revision is its own
    ancestor here; a caller that must not treat an unchanged citation as a successor asks for the
    strict form itself. Nothing but the authored edges is consulted: no display version, no label and
    no insertion order.
    """

    if ancestor is None or descendant is None:
        return False
    if ancestor == descendant:
        return True
    return descendant in set(authored_descendants(ancestor, edges))


def authored_successor(
    earlier: str | None, later: str | None, edges: frozenset[tuple[str, str]]
) -> bool:
    """Whether one revision is a *strict* authored successor of another.

    This is the form a pairing asks for: two different recorded rows are one association only when the
    candidate's citation is a step further along the author's own line than the baseline's, never
    because both happen to cite the same revision.
    """

    return earlier != later and authored_ancestor(earlier, later, edges)


def successor_line(snapshot: RecordedSnapshot, revision_id: str) -> SuccessorLine:
    """Establish the head of one revision's authored successor line, or the reason there is none.

    The head rule is ICR-R07@v1's own
    (:func:`~agents_remember.application.review_revision_comparison.revision_heads`): a head is a
    revision of the line no other revision of the line names as its predecessor. One head is
    ``established``; several mean the author recorded a split that never rejoins and **no** one of
    them is chosen; none means every revision of the line names a successor, which is a cycle rather
    than a newest revision. Both of the latter are ``unresolved`` with their reason, because a caller
    that cannot name the head must say so instead of denying that a relationship is recorded.
    """

    successors = tuple(
        sorted(successor for successor, predecessor in snapshot.edges if predecessor == revision_id)
    )
    if not successors:
        return SuccessorLine(
            state="none",
            revision_id=revision_id,
            detail=(
                f"the {snapshot.side} snapshot records no authored successor of {revision_id}, so "
                "this revision is the end of its own authored line"
            ),
        )
    line = authored_descendants(revision_id, snapshot.edges)
    heads = revision_heads(line, sorted(snapshot.edges))
    if len(heads) == 1:
        return SuccessorLine(
            state="established",
            revision_id=revision_id,
            successors=successors,
            head_revision_id=heads[0],
            candidate_heads=heads,
            detail=(
                f"the {snapshot.side} snapshot records {len(successors)} authored successor(s) of "
                f"{revision_id}, whose uniquely established head is {heads[0]}"
            ),
        )
    if not heads:
        return SuccessorLine(
            state="unresolved",
            revision_id=revision_id,
            successors=successors,
            detail=(
                f"the {snapshot.side} snapshot records {len(successors)} authored successor(s) of "
                f"{revision_id} and every revision of that line names a successor, so the authored "
                "lineage is cyclic and no head is established"
            ),
        )
    return SuccessorLine(
        state="unresolved",
        revision_id=revision_id,
        successors=successors,
        candidate_heads=heads,
        detail=(
            f"the {snapshot.side} snapshot records {len(successors)} authored successor(s) of "
            f"{revision_id} and {len(heads)} revisions of that line with no recorded successor "
            f"({', '.join(heads)}), so no single head is established and none was chosen"
        ),
    )


@dataclass(frozen=True)
class RelationshipLine:
    """One authored line to read, and what its revisions are revisions *of*.

    ``kind`` is the kind of revision the ids name -- ``invariant`` for a realization's or a
    membership's member side, ``family`` for a membership's or an advertised link's family side -- and
    it is what selects the read owner: the owner keyed by an invariant revision cannot answer for a
    family revision, and asking it anyway is how a whole branch of this reach came to read nothing.
    """

    kind: Literal["invariant", "family"]
    revision_ids: tuple[str, ...]


def read_line_relationships(
    snapshot: RecordedSnapshot,
    repository_id: str,
    resolver: AnchorResolver,
    line: RelationshipLine,
) -> tuple[RecordedRelationship, ...]:
    """Read every relationship one snapshot records at the revisions of one authored line.

    This is the reach ICR-R08@v1's ruling establishes, and it covers the **whole line** rather than its
    head: a relationship recorded at an intermediate successor is as recorded as one at the end, and a
    read that stopped at the head would report the selection's boundary as the store's state. Each kind
    is read by the owner that owns it -- realizations through the claim/anchor reader with the same
    anchor seam a selected row uses, family memberships through the family-revision owner -- so no
    query here is a second implementation of a shipped read.
    """

    if not line.revision_ids:
        return ()
    if line.kind == "family":
        return tuple(
            _line_membership(snapshot, repository_id, row)
            for row in fetch_memberships_of_families_full(
                snapshot.connection, repository_id, line.revision_ids
            )
        )
    return tuple(
        _line_realization(snapshot, repository_id, row, resolver, line.revision_ids)
        for row in fetch_realizations_for_invariants(
            snapshot.connection, repository_id, line.revision_ids
        )
    )


def head_anchor_resolver(
    database: Path, repository_id: str, code: TreeSide | None
) -> AnchorResolver:
    """The anchor resolver one side's exact code tree asks for, built from that side's own file.

    The dataset's identity is read from the file and the resolver is the shipped one, so a row read at
    a successor head carries the same resolution vocabulary and the same recorded identities as a row
    the comparison selected. A side that named no tree resolves nothing and says so for every anchor,
    exactly as a selected read does.
    """

    context = KnowledgeReadContext(
        repository_id=repository_id,
        knowledge=dataset_identity(Path(database)),
        repository_root=None if code is None or code.root is None else str(code.root),
        code_tree_id=None if code is None else code.tree_id,
    )
    return anchor_resolver_for(context)


def _revision_identity(
    snapshot: RecordedSnapshot, repository_id: str, revision_id: str
) -> str | None:
    """The identity one invariant revision belongs to, read from the snapshot's own row.

    A row read at a successor head carries no identity of its own, and the head is what the movement
    is displayed *under*: without this the movement would be a moved association with no preserved
    identity, which is the fact the packet's conforming example turns on.
    """

    rows = fetch_invariant_revisions(snapshot.connection, repository_id, (revision_id,))
    return None if not rows else str(rows[0]["invariant_id"])


def _line_realization(
    snapshot: RecordedSnapshot,
    repository_id: str,
    row: Mapping[str, Any],
    resolver: AnchorResolver,
    line_revisions: tuple[str, ...],
) -> RecordedRelationship:
    """One realization read on a successor line, as the display's own recorded side."""

    del line_revisions
    identity = _revision_identity(snapshot, repository_id, str(row["invariant_revision_id"]))
    anchor = resolver(dict(row))
    return RecordedRelationship(
        kind="realization",
        item_id=str(row["claim_id"]),
        item_coverage="present_outside_selection",
        origin="successor_head",
        side=snapshot.side,
        relationship_id=str(row["claim_id"]),
        record_kind="invariant",
        record_id=identity,
        revision_id=str(row["invariant_revision_id"]),
        member_revision_id=None,
        path=None if anchor is None else anchor.path,
        role=str(row["role"]),
        rationale=str(row["rationale"]),
        resolution=None if anchor is None else anchor.resolution,
        resolution_detail=None if anchor is None else anchor.detail,
        recorded_source_identity=None if anchor is None else anchor.recorded_source_identity,
        observed_source_identity=None if anchor is None else anchor.observed_source_identity,
        anchor_readable=anchor is not None,
        change_state="not_selected",
        reached_via=(),
    )


def _line_membership(
    snapshot: RecordedSnapshot, repository_id: str, row: tuple[str, str, str, Any]
) -> RecordedRelationship:
    """One membership read on a successor line, with the family identity of its family revision."""

    member_id, family_revision_id, invariant_revision_id = (
        str(row[0]),
        str(row[1]),
        str(row[2]),
    )
    families = {
        str(entry["revision_id"]): str(entry["family_id"])
        for entry in fetch_family_revisions(
            snapshot.connection, repository_id, (family_revision_id,)
        )
    }
    return RecordedRelationship(
        kind="membership",
        item_id=member_id,
        item_coverage="present_outside_selection",
        origin="successor_head",
        side=snapshot.side,
        relationship_id=member_id,
        record_kind="family",
        record_id=families.get(family_revision_id),
        revision_id=family_revision_id,
        member_revision_id=invariant_revision_id,
        path=None,
        role=None,
        rationale=None,
        resolution=None,
        resolution_detail=None,
        recorded_source_identity=None,
        observed_source_identity=None,
        anchor_readable=True,
        change_state="not_selected",
        reached_via=(),
    )


def side_payload(item: KnowledgeDiffItem, side: str) -> ReadItem | None:
    """Return one union item's payload for one side, or ``None`` when that side holds no record."""

    return item.before if side == "before" else item.after


def read_snapshot_relationships(
    items: Sequence[KnowledgeDiffItem], snapshot: RecordedSnapshot, repository_id: str
) -> tuple[RecordedRelationship, ...]:
    """Read every relationship one snapshot's union items record, in the union's own order."""

    payloads = tuple(
        (item, payload)
        for item in items
        if item.kind in _RELATIONSHIP_KINDS and (payload := side_payload(item, snapshot.side))
    )
    identities = _identity_index(
        snapshot, repository_id, tuple(payload for _item, payload in payloads)
    )
    return tuple(
        relationship
        for item, payload in payloads
        if (relationship := _relationship(item, payload, snapshot.side, identities)) is not None
    )


def _identity_index(
    snapshot: RecordedSnapshot,
    repository_id: str,
    payloads: Sequence[ReadItem],
) -> RecordedIdentityIndex:
    """Resolve the identity each association revision belongs to, from that snapshot's own rows.

    A realization item's payload carries the revision its claim cites but not the identity that
    revision belongs to (the selection read does not project it), and a membership's payload carries
    its family identity only when the family was selected. Both are read here from the snapshot's own
    revision rows, so the identity displayed is the one the snapshot records rather than one inferred
    from the item's shape.
    """

    invariant_revisions = sorted(
        {
            str(payload.invariant_revision_id)
            for payload in payloads
            if payload.kind == "realization_claim" and payload.invariant_revision_id is not None
        }
    )
    family_revisions = sorted(
        {
            str(payload.family_revision_id)
            for payload in payloads
            if payload.kind in ("family_membership", "advertised_family")
            and payload.family_revision_id is not None
        }
    )
    return RecordedIdentityIndex(
        invariants={
            str(row["revision_id"]): str(row["invariant_id"])
            for row in fetch_invariant_revisions(
                snapshot.connection, repository_id, invariant_revisions
            )
        },
        families={
            str(row["revision_id"]): str(row["family_id"])
            for row in fetch_family_revisions(snapshot.connection, repository_id, family_revisions)
        },
    )


def _relationship(
    item: KnowledgeDiffItem,
    payload: ReadItem,
    side: str,
    identities: RecordedIdentityIndex,
) -> RecordedRelationship | None:
    """Read one union item's payload for one side as a recorded relationship, or ``None``.

    ``None`` is returned for an item whose payload is not a relationship at all -- the union carries
    revisions beside relationships, and a revision is one of the two things a relationship relates.
    """

    kind = _RELATIONSHIP_KINDS.get(item.kind)
    if kind is None:
        return None
    revision_id = (
        payload.invariant_revision_id if kind == "invariant" else payload.family_revision_id
    )
    identity = _association_identity(kind, payload, revision_id, identities)
    anchor = payload.anchor if kind == "invariant" else None
    return RecordedRelationship(
        kind=item.kind,
        item_id=item.item_id,
        item_coverage=item.coverage,
        side=side,
        relationship_id=_relationship_id(item, payload),
        record_kind=kind,
        record_id=identity,
        revision_id=None if revision_id is None else str(revision_id),
        member_revision_id=_member_revision(payload, kind),
        path=None if anchor is None else anchor.path,
        role=payload.role if kind == "invariant" else None,
        rationale=payload.rationale if kind == "invariant" else None,
        resolution=None if anchor is None else anchor.resolution,
        resolution_detail=None if anchor is None else anchor.detail,
        recorded_source_identity=None if anchor is None else anchor.recorded_source_identity,
        observed_source_identity=None if anchor is None else anchor.observed_source_identity,
        anchor_readable=kind != "invariant" or anchor is not None,
        change_state=recorded_change_state(item) if kind == "invariant" else None,
        reached_via=tuple(item.reached_via),
    )


def _association_identity(
    kind: str,
    payload: ReadItem,
    revision_id: str | None,
    identities: RecordedIdentityIndex,
) -> str | None:
    """Return the identity one association sits under, as that snapshot records it."""

    if kind == "invariant":
        return None if revision_id is None else identities.invariants.get(str(revision_id))
    return payload.family_id or (
        None if revision_id is None else identities.families.get(str(revision_id))
    )


def _relationship_id(item: KnowledgeDiffItem, payload: ReadItem) -> str:
    """Return the recorded relationship's own identity: the claim, the member, or the pair."""

    return str(payload.claim_id or payload.member_id or item.item_id)


def _member_revision(payload: ReadItem, kind: str) -> str | None:
    """Return the member revision a family association holds, and nothing for a claim."""

    if kind == "invariant" or payload.invariant_revision_id is None:
        return None
    return str(payload.invariant_revision_id)


def recorded_change_state(
    item: KnowledgeDiffItem,
) -> Literal["changed", "unchanged", "not_selected"]:
    """The comparison's own statement about one claim's source observation, carried verbatim."""

    change = item.source_change
    if change is None:
        return "not_selected"
    if change.source_observation_changed or change.source_change_only:
        return "changed"
    return "unchanged"
