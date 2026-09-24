"""The recorded roster of one selected family revision, read and stated for the review context.

``ICR-R31@v1``'s context needs, for one exact family revision, the roster its author recorded: every
membership row, each member's exact invariant revision and its recorded content, the realization
claims that reach source expressions, and the page and cursor that reach the rest. This module owns
that read and the values it states; the composition that decides *which* families and revisions a
review context is about lives next door in
:mod:`agents_remember.application.review_family_context`, which calls this module.

Three owners are called and none is duplicated: the **selection policy** runs through the shipped
read operation :func:`agents_remember.application.knowledge_read.read_knowledge_scope` with an exact
``FamilyRevisionSeed``, so the roster, its counts and its snapshot-bound continuation are the read
owner's own; the **authored guarantee** comes from the family owner
(:mod:`agents_remember.memory.knowledge.families`), which verifies the revision's payload seal on the
way out; and the **sharing fact** -- the other family revisions one member revision is recorded in --
comes from the membership owner (:mod:`agents_remember.memory.knowledge.memberships`).

What it refuses is the shape of every value below: a side that was not read states which fact it is
instead of an empty roster, a membership row whose revision content fell outside the page is stated
as ``content_not_on_page`` rather than filled in from a second selection, and the guarantee is never
assembled from the members it is stored beside.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from agents_remember.application.knowledge_read import open_read_context, read_knowledge_scope
from agents_remember.application.review_candidate_resolution import refusal
from agents_remember.memory.knowledge import families, memberships
from agents_remember.memory.knowledge.connection import open_read_only_database
from agents_remember.memory.knowledge.read_queries import fetch_predecessor_edges
from agents_remember.memory.knowledge.refusals import KnowledgeStorageError
from agents_remember.memory.knowledge.store import (
    OpenedKnowledgeStore,
    open_existing_knowledge_store,
)
from agents_remember.models.knowledge.base import PROSE_MAX_LENGTH
from agents_remember.models.knowledge.read import (
    MAX_PAGE_ITEMS,
    MAX_PAGE_UTF8_BYTES,
    FamilyRevisionSeed,
    KnowledgeReadBudget,
    KnowledgeReadContext,
    KnowledgeReadPage,
    KnowledgeReadRequest,
    KnowledgeReadResult,
    ReadItem,
    continue_from_cursor,
)
from agents_remember.models.knowledge.result import KnowledgeRefusal
from agents_remember.models.knowledge.review import ReviewCollectionPage, ReviewRefusal
from agents_remember.models.knowledge.review_family_context import (
    ReviewFamilyGuarantee,
    ReviewFamilyMember,
    ReviewFamilyMemberSource,
    ReviewFamilyRevisionContext,
    ReviewFamilyRosterPage,
    ReviewFamilySideName,
    ReviewFamilySideState,
)
from agents_remember.models.knowledge.review_relationships import ReviewRelationshipMovement

__all__ = [
    "FAMILY_MEMBERS_COLLECTION",
    "FamilyRosterRead",
    "FamilyRosterSide",
    "RosterContext",
    "RosterReadRequest",
    "family_collection_refusal",
    "family_context_cursor_refusal",
    "family_guarantee",
    "family_member_page",
    "open_family_side",
    "read_family_roster",
    "side_statement",
]

# The review surface's collection name for one family revision's recorded member roster: the name a
# request presents its cursor under and the name the payload's page states, so a cursor and the walk
# it belongs to cannot be paired by guesswork.
FAMILY_MEMBERS_COLLECTION = "family_members"

# The read owner's own refusal code for "this cursor binds another selection": the one answer that
# means the cursor belongs to a walk this read is not positioned in.
_CURSOR_MISMATCH = "continuation_binding_mismatch"


@dataclass(frozen=True)
class RosterReadRequest:
    """What one roster read is asked for: its page bound, the union's movements and its cursor.

    The three travel as one value because they are one request: the bound the page honours, the
    movement union whose identities a member context may reference, and the cursor the walk is asked
    to continue. A caller able to pass two of them could state a page whose bound and position
    disagree.
    """

    page_size: int = 0
    movements: Sequence[ReviewRelationshipMovement] = ()
    continuation: str | None = None


@dataclass(frozen=True)
class RosterContext:
    """Which revision one roster read is for, and the two facts that qualify it.

    ``family_revision_id`` is ``None`` when this selection established no revision on this side, and
    ``not_recorded_detail`` is then the sentence stating which fact that is -- which differs by the
    axis the selection was made on (an invariant records no membership here; a family records no such
    revision), so the sentence is supplied by the composition that knows the axis rather than guessed
    here. ``recorded_revision_ids`` is the family owner's own list of every revision of this family
    the snapshot records, which the side context publishes so a reader can see the history its
    selected revision came from.
    """

    family_revision_id: str | None = None
    recorded_revision_ids: tuple[str, ...] = ()
    not_recorded_detail: str = (
        "this snapshot records no revision of the selected family, so it has no selected family "
        "revision here and no roster is claimed for it"
    )


# --- the opened snapshot ---


@dataclass
class FamilyRosterSide:
    """One bound snapshot as this composition reads it: its store, context and authored edges.

    ``unreadable`` is the reason this side could not be opened at all, and the other three fields are
    then empty and every family context on this side states that reason rather than an absence.
    """

    name: ReviewFamilySideName
    database: Path
    namespace: str
    store: OpenedKnowledgeStore | None = None
    read_context: KnowledgeReadContext | None = None
    edges: frozenset[tuple[str, str]] = frozenset()
    unreadable: str | None = None

    def close(self) -> None:
        if self.store is not None:
            self.store.close()


@dataclass
class FamilyRosterRead:
    """One side's roster read of one family revision, with what the offered cursor did.

    ``bound`` is true when the cursor the request presented was served by *this* read, which is how
    the response knows which of its family contexts the published page belongs to. ``cursor_refusal``
    is the owner's own refusal when the cursor was offered here and this walk did not mint it; it is a
    routing fact and not a fact about the side, so the side is still read from its first page.
    """

    context: ReviewFamilyRevisionContext
    page: ReviewFamilyRosterPage | None = None
    bound: bool = False
    cursor_refusal: KnowledgeRefusal | None = None


def open_family_side(
    name: ReviewFamilySideName,
    database: Path,
    *,
    namespace: str,
    code_root: Path | None,
    code_tree_id: str | None,
) -> FamilyRosterSide:
    """Open one snapshot's store, read context and authored edges, or state why it could not be."""

    side = FamilyRosterSide(name=name, database=database, namespace=namespace)
    try:
        side.read_context = open_read_context(
            database, namespace, repository_root=code_root, code_tree_id=code_tree_id
        )
        side.store = open_existing_knowledge_store(database, namespace)
        side.edges = _authored_edges(database, namespace)
    except (KnowledgeStorageError, OSError, ValueError) as error:
        side.store = None
        side.read_context = None
        side.unreadable = f"{type(error).__name__}: {error}"
    return side


def _authored_edges(database: Path, namespace: str) -> frozenset[tuple[str, str]]:
    """Every authored ``(successor, predecessor)`` edge one snapshot records.

    The edges are the selection policy's own read over a read-only connection -- the same values
    ``ICR-R07@v1``'s selection reads -- so a head rule applied to them is applied to the snapshot's
    own authored lineage and to nothing a caller derived.
    """

    connection = open_read_only_database(Path(database))
    try:
        return frozenset(fetch_predecessor_edges(connection, namespace))
    finally:
        connection.close()


# --- the roster read ---


def read_family_roster(
    side: FamilyRosterSide,
    family_id: str,
    context: RosterContext,
    request: RosterReadRequest,
) -> FamilyRosterRead:
    """One snapshot's context for one family: its revision, guarantee, roster page and cursor fact."""

    family_revision_id = context.family_revision_id
    if family_revision_id is None:
        return FamilyRosterRead(
            context=side_statement(side, family_id, "not_recorded", context.not_recorded_detail)
        )
    if side.unreadable is not None or side.read_context is None or side.store is None:
        return FamilyRosterRead(
            context=side_statement(
                side, family_id, "unreadable", side.unreadable or "the side could not be opened"
            )
        )
    result, bound, cursor_refusal = _read_roster(side, family_id, family_revision_id, request)
    if result is None or result.page is None:
        return FamilyRosterRead(
            context=side_statement(
                side, family_id, "unreadable", cursor_refusal_detail(cursor_refusal)
            ),
            cursor_refusal=cursor_refusal,
        )
    guarantee = family_guarantee(side.store, family_id, family_revision_id)
    if guarantee is None:
        return FamilyRosterRead(
            context=side_statement(
                side,
                family_id,
                "unreadable",
                f"the family revision {family_revision_id} is in this snapshot's selected family "
                "set but the family owner returned no sealed aggregate for it, so no guarantee is "
                "presented for it",
            )
        )
    page = _roster_page(
        side,
        result,
        family_revision_id,
        request.page_size,
        request.continuation if bound else None,
    )
    members = _members(side, result, family_revision_id, request.movements)
    return FamilyRosterRead(
        context=ReviewFamilyRevisionContext(
            side=side.name,
            family_id=family_id,
            state="recorded",
            family_revision_id=family_revision_id,
            recorded_revision_ids=context.recorded_revision_ids,
            guarantee=guarantee,
            members=members,
            members_total=page.members_total,
            page=page,
            detail=_roster_detail(side, family_revision_id, page, len(members)),
        ),
        page=page,
        bound=bound,
        cursor_refusal=cursor_refusal,
    )


def _read_roster(
    side: FamilyRosterSide,
    family_id: str,
    family_revision_id: str,
    request: RosterReadRequest,
) -> tuple[KnowledgeReadResult | None, bool, KnowledgeRefusal | None]:
    """Read one family revision's roster, offering the request's cursor to this walk.

    A cursor the owner refuses as another selection's is not a fact about this side: the side is read
    again from its own first page and the refusal is carried back so the response can state once that
    no walk it composed bound the cursor.
    """

    result = _roster_read(side, family_id, family_revision_id, request)
    if request.continuation is None:
        return result, False, None
    if result.state == "page":
        return result, True, None
    owner_refusal = result.refusal
    if owner_refusal is not None and owner_refusal.code == _CURSOR_MISMATCH:
        # A cursor another walk minted is not a fact about this side: the side is read from its own
        # first page and the refusal is carried back, so a caller can state once that no walk this
        # response composed bound it.
        first = _roster_read(
            side,
            family_id,
            family_revision_id,
            RosterReadRequest(page_size=request.page_size, movements=request.movements),
        )
        return first, False, owner_refusal
    return result, False, None


def _roster_read(
    side: FamilyRosterSide,
    family_id: str,
    family_revision_id: str,
    request: RosterReadRequest,
) -> KnowledgeReadResult:
    """One read of one family revision's recorded scope, at the request's own page bound."""

    assert side.read_context is not None
    return read_knowledge_scope(
        side.database,
        side.read_context,
        KnowledgeReadRequest(
            seed=FamilyRevisionSeed(family_id=family_id, revision_id=family_revision_id),
            budget=KnowledgeReadBudget(
                max_items=request.page_size if request.page_size else MAX_PAGE_ITEMS,
                max_utf8_bytes=MAX_PAGE_UTF8_BYTES,
            ),
            continuation=request.continuation,
        ),
    )


def _roster_page(
    side: FamilyRosterSide,
    result: KnowledgeReadResult,
    family_revision_id: str,
    page_size: int,
    continued_from: str | None,
) -> ReviewFamilyRosterPage:
    """State the read owner's own page for one family revision's recorded roster."""

    page: KnowledgeReadPage | None = result.page
    assert page is not None
    return ReviewFamilyRosterPage(
        scope=(
            f"side={side.name}",
            f"family_revision_id={family_revision_id}",
            f"policy_version={result.policy_version}",
            f"page_size={page_size if page_size else MAX_PAGE_ITEMS}",
        ),
        state="continued" if continued_from is not None else "first_page",
        counts=page.counts,
        complete=page.enumeration_complete,
        members_total=page.counts.memberships_total,
        continuation=page.continuation,
        continued_from=continued_from,
    )


def cursor_refusal_detail(refusal_value: KnowledgeRefusal | None) -> str:
    """The reason one side states when a roster read served neither a page nor a usable refusal."""

    if refusal_value is None:
        return "the read returned neither a page nor a refusal, so no roster is claimed for it"
    return f"{refusal_value.code}: {refusal_value.detail}"


def _roster_detail(
    side: FamilyRosterSide, family_revision_id: str, page: ReviewFamilyRosterPage, carried: int
) -> str:
    """One sentence stating how much of the roster this page carried and how to reach the rest.

    The completed case splits in two, because ``complete`` is the WALK's flag and not the page's (ICR-L24
    fix round 3, V9): a walk the read took in one page carried every recorded membership HERE, while a
    walk whose final page is a continuation carried only that page's share -- saying "all carried here"
    for the second would be false about the store, and the pages before it are what carried the rest.
    """

    if page.complete and page.state == "first_page":
        return (
            f"the {side.name} snapshot's recorded roster of family revision {family_revision_id} was "
            f"read whole: {page.members_total} recorded membership(s), all carried here"
        )
    if page.complete:
        return (
            f"the {side.name} snapshot's recorded roster of family revision {family_revision_id} holds "
            f"{page.members_total} recorded membership(s); this page carried {carried} of them and "
            f"completes the read walk, the pages before it carried the rest"
        )
    return (
        f"the {side.name} snapshot's recorded roster of family revision {family_revision_id} holds "
        f"{page.members_total} recorded membership(s) and this page carried {carried}: the remainder "
        f"is {page.counts.primary_items_remaining} of the read walk's "
        f"{page.counts.primary_items_total} item(s) and is reached with the continuation beside it"
    )


def side_statement(
    side: FamilyRosterSide, family_id: str, state: ReviewFamilySideState, detail: str
) -> ReviewFamilyRevisionContext:
    """One side context that states which fact it is and carries no roster and no count at all."""

    return ReviewFamilyRevisionContext(
        side=side.name,
        family_id=family_id,
        state=state,
        detail=detail[:PROSE_MAX_LENGTH],
    )


# --- the authored values ---


def family_guarantee(
    store: OpenedKnowledgeStore, family_id: str, revision_id: str
) -> ReviewFamilyGuarantee | None:
    """One family revision's authored guarantee, from the owner that verifies its seal."""

    stored = families.get_family_revision(store, revision_id)
    if stored is None:
        return None
    revision = stored.revision
    return ReviewFamilyGuarantee(
        family_id=family_id,
        revision_id=revision.revision_id,
        display_version=revision.display_version,
        joint_guarantee=revision.joint_guarantee,
        state_at_origin=str(revision.state_at_origin),
        acceptance_ref=revision.acceptance_ref,
        provenance=revision.provenance.model_dump(mode="json"),
        payload_digest=revision.payload_digest,
    )


def _members(
    side: FamilyRosterSide,
    result: KnowledgeReadResult,
    family_revision_id: str,
    movements: Sequence[ReviewRelationshipMovement],
) -> tuple[ReviewFamilyMember, ...]:
    """The membership rows this page carried, each with its exact member revision's own content.

    A membership row is the recorded fact and is always carried; the member revision's statement is
    carried when the same page selected that revision, and is otherwise stated as
    ``content_not_on_page`` rather than filled in from a second read of a different selection.
    """

    page: KnowledgeReadPage | None = result.page
    assert page is not None
    content = {
        str(item.revision_id): item
        for item in page.items
        if item.kind == "invariant_revision" and item.revision_id is not None
    }
    claims: dict[str, list[ReadItem]] = {}
    for item in page.items:
        if item.kind == "realization_claim" and item.invariant_revision_id is not None:
            claims.setdefault(str(item.invariant_revision_id), []).append(item)
    lookups = _RosterLookups(
        content=content,
        claims={revision_id: tuple(rows) for revision_id, rows in claims.items()},
        movements=_movement_identities(movements),
        family_revision_id=family_revision_id,
    )
    return tuple(
        _member(side, item, lookups)
        for item in page.items
        if item.kind == "family_membership" and item.member_id is not None
    )


@dataclass(frozen=True)
class _RosterLookups:
    """The page's own lookups for one roster: member content, claim references and union identities.

    They travel as one value because they are one page's reading: a content item paired with another
    page's claims, or a movement identity taken from another review, would be a different roster
    wearing this one's membership rows.
    """

    content: Mapping[str, ReadItem]
    claims: Mapping[str, tuple[ReadItem, ...]]
    movements: frozenset[str]
    family_revision_id: str


def _member(side: FamilyRosterSide, item: ReadItem, lookups: _RosterLookups) -> ReviewFamilyMember:
    """One membership row as a member context, with its exact revision and its source references."""

    member_id = str(item.member_id)
    revision_id = str(item.invariant_revision_id)
    revision = lookups.content.get(revision_id)
    identity = _member_identity(side, revision_id, revision)
    others = _other_families(side, revision_id, lookups.family_revision_id)
    return ReviewFamilyMember(
        member_id=member_id,
        invariant_revision_id=revision_id,
        invariant_id=identity[0],
        display_label=identity[1],
        display_version=None if revision is None else revision.display_version,
        state="recorded" if revision is not None else "content_not_on_page",
        statement=None if revision is None else revision.statement,
        applicability=None if revision is None else revision.applicability,
        essential_conditions=() if revision is None else revision.essential_conditions,
        exclusions=() if revision is None else revision.exclusions,
        lifecycle=None if revision is None else revision.lifecycle,
        provenance={} if revision is None or revision.provenance is None else revision.provenance,
        payload_digest=None if revision is None else revision.payload_digest,
        other_family_revision_ids=others,
        sources=tuple(_source(claim) for claim in lookups.claims.get(revision_id, ())),
        movement_reference=member_id if member_id in lookups.movements else None,
        detail=_member_detail(side, item, revision, others),
    )


def _member_identity(
    side: FamilyRosterSide, revision_id: str, revision: ReadItem | None
) -> tuple[str | None, str | None]:
    """The member's invariant identity and recorded label, from the page or from the store owner."""

    if revision is not None:
        return revision.invariant_id, revision.display_label
    if side.store is None:
        return None, None
    stored = side.store.get_revision(revision_id)
    if stored is None:
        return None, None
    identity = side.store.get_invariant(stored.revision.invariant_id)
    return stored.revision.invariant_id, None if identity is None else identity.display_label


def _other_families(
    side: FamilyRosterSide, revision_id: str, family_revision_id: str
) -> tuple[str, ...]:
    """The other family revisions the membership owner records this exact revision in."""

    if side.store is None:
        return ()
    recorded = memberships.list_families_for_invariant_revision(side.store, revision_id)
    return tuple(
        sorted(
            {
                member.family_revision_id
                for member in recorded.members
                if member.family_revision_id != family_revision_id
            }
        )
    )


def _source(claim: ReadItem) -> ReviewFamilyMemberSource:
    """One recorded realization claim as an inspectable source reference."""

    anchor = claim.anchor
    return ReviewFamilyMemberSource(
        claim_id=str(claim.claim_id),
        invariant_revision_id=str(claim.invariant_revision_id),
        role=str(claim.role),
        rationale=str(claim.rationale),
        path=None if anchor is None else anchor.path,
        recorded_source_identity=None if anchor is None else anchor.recorded_source_identity,
        observed_source_identity=None if anchor is None else anchor.observed_source_identity,
        resolution=None if anchor is None else anchor.resolution,
        detail=(
            f"the author recorded this realization at {anchor.path} ({anchor.resolution}): "
            f"{anchor.detail}"
            if anchor is not None
            else "this read observed no address for this recorded realization, so it reports the "
            "claim's own identity and no resolution"
        ),
    )


def _member_detail(
    side: FamilyRosterSide, item: ReadItem, revision: ReadItem | None, others: tuple[str, ...]
) -> str:
    """One sentence stating which facts of this membership row this read established."""

    member_id = str(item.member_id)
    revision_id = str(item.invariant_revision_id)
    family_revision_id = str(item.family_revision_id)
    shared = (
        ""
        if not others
        else f"; the membership owner records the same exact revision in {len(others)} further "
        "family revision(s), which this value names rather than copying"
    )
    content = (
        "and its recorded revision content"
        if revision is not None
        else "while its revision content fell outside this page and is stated as such, not filled in"
    )
    return (
        f"the {side.name} snapshot records membership {member_id} placing the exact invariant "
        f"revision {revision_id} in family revision {family_revision_id} {content}{shared}"
    )


def _movement_identities(movements: Sequence[ReviewRelationshipMovement]) -> frozenset[str]:
    """The recorded membership identities the relationship union's own values display."""

    identities: set[str] = set()
    for movement in movements:
        if movement.relationship_kind != "membership":
            continue
        for side in (*movement.before, *([] if movement.after is None else [movement.after])):
            if side.relationship_id is not None:
                identities.add(side.relationship_id)
    return frozenset(identities)


# --- the page and the refusal a cursor earns ---


def family_member_page(
    page: ReviewFamilyRosterPage, *, continued_from: str | None
) -> ReviewCollectionPage:
    """State one continued roster walk as the review surface's own page (``ICR-R10@v1``).

    The counts are the read owner's own -- ``primary_items_total``, ``primary_items_returned`` and
    ``primary_items_remaining`` -- so the arithmetic the surface publishes is the owner's, and the
    cursor is the owner's opaque snapshot-bound continuation. The page's own scope names the exact
    side and family revision the walk belongs to, so a reader never has to guess which of the
    response's family contexts the published page is about.
    """

    return ReviewCollectionPage(
        collection=FAMILY_MEMBERS_COLLECTION,
        state="continued" if continued_from is not None else "first_page",
        total_basis="selection",
        total=page.counts.primary_items_total,
        returned=page.counts.primary_items_returned,
        remaining=page.counts.primary_items_remaining,
        continuation=page.continuation,
        scope=page.scope,
        continued_from=continued_from,
    )


def family_collection_refusal() -> ReviewRefusal:
    """The refusal a request that named the family collection without a cursor earns.

    This collection is not one walk but the set of per-family roster walks this response composed,
    so naming it addresses no single page: the cursor is what names the one walk a page belongs to.
    The response still carries every walk's first page on the family contexts themselves, which is
    what the action sentence points the reader at rather than an arbitrary one of them.
    """

    return refusal(
        "comparison_page_unreadable",
        "the family_members collection is the set of recorded family-revision roster walks this "
        "response composed, and no cursor was presented, so no one walk was addressed",
        next_action=(
            "present the continuation a family context's roster page published, which names the "
            "exact snapshot and family revision it continues; this response carries every walk's "
            "first page on the family contexts themselves"
        ),
    )


def family_context_cursor_refusal(
    cursor: str, owner_refusal: KnowledgeRefusal | None
) -> ReviewRefusal:
    """The refusal a family roster cursor that bound no composed walk earns.

    The vocabulary is ``ICR-R10@v1``'s: a cursor is a position in one named walk of one named
    snapshot, it is never re-resolved against a generation that moved, and the reader is told what to
    do instead. The action sentence is this collection's own -- the response serves the first page of
    every family context it composed, each with its own continuation -- rather than the comparison's,
    because telling a family-cursor reader that the comparison's first page was served would name a
    page this response does not carry.
    """

    detail = (
        "the cursor bound no family revision's recorded roster walk in this comparison"
        if owner_refusal is None
        else f"{owner_refusal.code}: {owner_refusal.detail}"
    )
    if continue_from_cursor(cursor) is None:
        return refusal(
            "comparison_page_unreadable",
            detail,
            next_action=(
                "present a cursor this surface published beside a family context's roster page; a "
                "token of another format addresses no walk any response can continue"
            ),
            offending_input=cursor,
        )
    return refusal(
        "comparison_page_reset",
        detail,
        next_action=(
            "open a new comparison: this cursor is a position in one family revision's recorded "
            "member walk of two named snapshots, so it cannot be continued once either moved; this "
            "response serves the first page of every family context it composed, each with its own "
            "continuation"
        ),
        offending_input=cursor,
    )
