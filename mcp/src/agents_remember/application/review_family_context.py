"""The comparison-bound family review context of one review (ICR-R31@v1).

This module owns the composition the accepted family-centred reviewer needs and neither
``ICR-R09@v1``'s flat subject catalogue nor ``ICR-R08@v1``'s movement union produces: for the
review's selected subject, **which recorded families it belongs to on each of the two bound
snapshots, each selected family revision's own authored joint guarantee, and that revision's complete
recorded member roster -- unchanged siblings included** -- composed into
:mod:`agents_remember.models.knowledge.review_family_context`.

**It composes; it duplicates no owner.** Three owners are called:

* the **selection policy** is ``recorded-family-frontier/v1``
  (:mod:`agents_remember.memory.knowledge.read`), run through the shipped read operation
  :func:`agents_remember.application.knowledge_read.read_knowledge_scope`. One read per side with the
  reviewed identity's own seed answers *which family revisions the seed reaches directly*
  (``directly_containing_families``, the policy's frozen family set, computed before any page is cut
  and therefore complete whatever budget the page applied).
* the **head rule** is ``ICR-R07@v1``'s own :func:`…review_revision_comparison.revision_heads`, called
  over the selected family revisions and the snapshots' own authored predecessor edges. This module
  chooses no revision by label, by version or by order: an ambiguous or a cyclic lineage is carried as
  exactly that, with its inspectable candidate heads.
* the **roster, guarantee, member content and page of one selected revision** are read by
  :mod:`agents_remember.application.review_family_rosters`, which calls the family, membership and
  read owners and verifies the family revision's seal; this module decides only which revisions those
  reads are for. The **recorded movements** are ``ICR-R08@v1``'s own values, passed in and
  *referenced* by identity: a member context names the relationship union's own identity for its
  membership row when that union's page reached it, and says nothing about the movement's transition.

**What it refuses.** No family is inferred from a folder, a label, a shared source file or a
similarity score; no guarantee is derived from members; no revision is chosen when the authored
lineage leaves several heads; and no verdict about a member's consequence for its family's guarantee
exists anywhere in the value this returns. A snapshot that does not record the reviewed identity
reports ``not_recorded`` on that side, a read the owner refused reports ``unreadable`` with the
owner's own words, and a measured zero is spelled ``no_family_recorded`` rather than ``empty``.

**Continuation.** The one bounded collection this composition continues is *one family revision's
recorded member roster*, under the review surface's ``family_members`` collection name. The cursor is
the read owner's own, minted for one snapshot and one family revision and presented back through the
same owner; a cursor for another snapshot or another family revision is therefore refused by that
owner rather than reinterpreted here, and the response serves the first page of every family context
it composed instead of a page stitched from two states.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from agents_remember.application.knowledge_read import read_knowledge_scope
from agents_remember.application.review_family_rosters import (
    FAMILY_MEMBERS_COLLECTION,
    FamilyRosterRead,
    FamilyRosterSide,
    RosterContext,
    RosterReadRequest,
    family_context_cursor_refusal,
    family_guarantee,
    family_member_page,
    open_family_side,
    read_family_roster,
    side_statement,
)
from agents_remember.application.review_revision_comparison import revision_heads
from agents_remember.memory.knowledge import families
from agents_remember.models.knowledge.base import PROSE_MAX_LENGTH
from agents_remember.models.knowledge.read import (
    MAX_PAGE_ITEMS,
    FamilyIdentitySeed,
    FamilyRevisionSeed,
    KnowledgeReadBudget,
    KnowledgeReadRequest,
    KnowledgeReadSeed,
)
from agents_remember.models.knowledge.result import KnowledgeRefusal
from agents_remember.models.knowledge.review import ReviewCollectionPage, ReviewRefusal
from agents_remember.models.knowledge.review_family_context import (
    ReviewFamilyContext,
    ReviewFamilyContextEntry,
    ReviewFamilyContextState,
    ReviewFamilyEntryState,
    ReviewFamilyGuarantee,
    ReviewFamilyRevisionContext,
    ReviewFamilyRosterPage,
    ReviewFamilySideName,
    ReviewFamilySideState,
)
from agents_remember.models.knowledge.review_relationships import ReviewRelationshipMovement
from agents_remember.models.knowledge.revision_selection import (
    ReviewRevisionSelection,
    RevisionSelectionState,
)

__all__ = [
    "FAMILY_MEMBERS_COLLECTION",
    "FamilyContextOutcome",
    "FamilyContextSources",
    "review_family_context",
]

# The read owner's own refusal code for "this snapshot records no such identity". It is a *measured*
# answer about the snapshot rather than a failure to read it, and the two are kept apart below.
_SELECTOR_ABSENT = "selector_absent"

# The seed kinds this composition resolves. An identity seed names a family or an invariant; an exact
# revision seed names one revision of one of them. Anything else -- a path seed, most importantly --
# names no identity whose recorded families could be resolved, and is reported as such.
_FAMILY_SEED_KINDS = ("family", "family_revision")
_INVARIANT_SEED_KINDS = ("invariant", "invariant_revision")
_IDENTITY_SEED_KINDS = (*_FAMILY_SEED_KINDS, *_INVARIANT_SEED_KINDS)


@dataclass(frozen=True)
class FamilyContextSources:
    """Everything this composition reads, as one value.

    The two datasets, the namespace they are bound to, the two bound code trees, the reviewed
    selector and the relationship union travel together because they are one measurement: a roster
    read from another snapshot, a guarantee read under another namespace, or a movement reference
    taken from another review would be a different comparison wearing this one's identity.
    """

    repository_id: str
    namespace: str
    before_database: Path
    after_database: Path
    selector: KnowledgeReadSeed | None = None
    before_code_root: Path | None = None
    after_code_root: Path | None = None
    before_code_tree_id: str | None = None
    after_code_tree_id: str | None = None
    movements: Sequence[ReviewRelationshipMovement] = ()
    page_size: int = 0


@dataclass(frozen=True)
class FamilyContextOutcome:
    """The context this composition built, with the page or the refusal a requested cursor earned.

    ``page`` is present exactly when the request continued one family revision's roster and the owner
    that minted the cursor served it; ``refusal`` is present exactly when a cursor was presented and
    no roster walk this response composed bound it. They are never both present: a cursor is either
    continued or refused.
    """

    context: ReviewFamilyContext
    page: ReviewCollectionPage | None = None
    refusal: ReviewRefusal | None = None


# Which question a family selection asked, which is what decides both the population the head rule
# runs over and the sentence the selection publishes. The three are separate values rather than a
# boolean because each names a different recorded fact: the family's own revisions, one named exact
# revision, or the family revisions a subject's memberships cite.
_AXIS_FAMILY_IDENTITY = "family_identity"
_AXIS_FAMILY_REVISION = "family_revision"
_AXIS_INVARIANT = "invariant"

# The clause each axis states about the revision pair it selected. Every one of them is a recorded
# fact about the two snapshots and none of them is a claim about a revision nobody read.
_SELECTION_BASIS: dict[str, str] = {
    _AXIS_FAMILY_IDENTITY: "each the unique revision its own snapshot records for this family",
    _AXIS_FAMILY_REVISION: "each the exact revision this selection names",
    _AXIS_INVARIANT: (
        "each the family revision its own snapshot records a membership of the selected subject in"
    ),
}

# The clause each axis states about a one-sided selection, where there is no pair at all.
_ADDED_BASIS: dict[str, str] = {
    _AXIS_FAMILY_IDENTITY: "records no revision of the family",
    _AXIS_FAMILY_REVISION: "records not the revision this selection names",
    _AXIS_INVARIANT: "records no membership of the selected subject in the family",
}


@dataclass(frozen=True)
class _FamilyRevisions:
    """One family's populations, their heads, and the history the family owner records.

    ``before``/``after`` are the populations the head rule runs over and ``before_heads``/
    ``after_heads`` are what it establishes. ``before_recorded``/``after_recorded`` are the family
    owner's own lists of every revision each snapshot records for this family -- a *different*
    population, because a revision that cites no member is recorded without being part of a
    membership-derived selection. ``axis`` says which question was asked, so the sentence can state
    the recorded fact it actually measured instead of a generic one.
    """

    before: tuple[str, ...] = ()
    after: tuple[str, ...] = ()
    before_heads: tuple[str, ...] = ()
    after_heads: tuple[str, ...] = ()
    before_recorded: tuple[str, ...] = ()
    after_recorded: tuple[str, ...] = ()
    axis: str = _AXIS_INVARIANT

    def other_recorded(self) -> tuple[str, ...]:
        """Every recorded revision of this family that the selection did not choose, sorted.

        The two snapshots are read as one history: a revision both of them retain is one revision,
        so a count over this list can never be inflated by a revision recorded twice -- and it can
        never understate the history either, because it is the family owner's own list on each side
        rather than the population this composition selected.
        """

        selected = {
            self.before_heads[0] if len(self.before_heads) == 1 else None,
            self.after_heads[0] if len(self.after_heads) == 1 else None,
        }
        chosen = {revision for revision in selected if revision is not None}
        return tuple(sorted((set(self.before_recorded) | set(self.after_recorded)) - chosen))


@dataclass
class _Applicable:
    """One side's family populations, or the stated reason none could be reported.

    Two populations travel here and they are deliberately different. ``families`` is what the head
    rule runs over: for an invariant selection, the family revisions whose recorded memberships cite
    one of the selected invariant's own revisions; for a family selection, **every revision the
    family owner records in this snapshot**, because the selection *is* the family and a revision that
    cites no member is still a revision of it. ``recorded`` is the family owner's own list for every
    applicable family, so a sentence about the family's recorded history can be measured against the
    store rather than against the population this composition happened to read (ICR-R31 fix round 1).

    ``absent`` is the *measured* answer that this snapshot records no such identity (so no family is
    applicable and none is missing), while ``reason`` is a read that did not serve a page at all --
    the two are never collapsed, because one is a fact about the snapshot and the other is a part
    this composition could not establish.
    """

    families: dict[str, tuple[str, ...]] = field(default_factory=dict)
    recorded: dict[str, tuple[str, ...]] = field(default_factory=dict)
    absent: str | None = None
    reason: str | None = None

    @property
    def read(self) -> bool:
        """Whether this side's recorded scope was actually read."""

        return self.reason is None

    def population(self, family_id: str) -> tuple[str, ...]:
        """The revisions of one family the head rule runs over on this side."""

        return self.families.get(family_id, ())

    def revisions(self, family_id: str) -> tuple[str, ...]:
        """Every revision of one family this snapshot records, as the family owner lists them."""

        return self.recorded.get(family_id, ())


def review_family_context(
    sources: FamilyContextSources, *, continuation: str | None = None
) -> FamilyContextOutcome:
    """Compose the comparison-bound family context, continuing one roster walk when asked.

    The request names one optional cursor. It is offered to each applicable family revision's own
    read, and the owner that minted it is the one that serves it; every other family is served its
    first page, so a continuation advances exactly one walk and never re-resolves another's position.
    """

    if sources.selector is None:
        return FamilyContextOutcome(
            context=_stated(
                "no_subject_selected",
                "this review selected no subject, so no family context was composed: a task-context "
                "review compares no knowledge operand and claims nothing about recorded families; "
                "the complete source inventory beside it is measured independently of any family",
            )
        )
    seed_kind = str(getattr(sources.selector, "kind", ""))
    if seed_kind not in _IDENTITY_SEED_KINDS:
        return FamilyContextOutcome(
            context=_stated(
                "unavailable",
                "the family context resolves an invariant or a family selection; this request named "
                f"a {seed_kind or 'unrecognized'} seed, whose recorded families this composition does "
                "not resolve, and it substitutes no identity for it",
            )
        )
    before = open_family_side(
        "before",
        sources.before_database,
        namespace=sources.namespace,
        code_root=sources.before_code_root,
        code_tree_id=sources.before_code_tree_id,
    )
    after = open_family_side(
        "after",
        sources.after_database,
        namespace=sources.namespace,
        code_root=sources.after_code_root,
        code_tree_id=sources.after_code_tree_id,
    )
    try:
        return _compose(sources, before, after, seed_kind, continuation)
    finally:
        before.close()
        after.close()


def _compose(
    sources: FamilyContextSources,
    before: FamilyRosterSide,
    after: FamilyRosterSide,
    seed_kind: str,
    continuation: str | None,
) -> FamilyContextOutcome:
    """Read both sides' applicable families, then each family's selected revision and roster."""

    applicable = {"before": _applicable(before, sources), "after": _applicable(after, sources)}
    if not applicable["before"].read and not applicable["after"].read:
        return FamilyContextOutcome(
            context=_stated(
                "unavailable",
                "neither snapshot's recorded scope could be read for this selection, so no family "
                f"context is claimed: before: {applicable['before'].reason}; "
                f"after: {applicable['after'].reason}",
            )
        )
    family_ids = sorted(set(applicable["before"].families) | set(applicable["after"].families))
    if not family_ids:
        return FamilyContextOutcome(context=_no_family(applicable, seed_kind))
    entries: list[ReviewFamilyContextEntry] = []
    continued: ReviewFamilyRosterPage | None = None
    owner_refusal: KnowledgeRefusal | None = None
    for family_id in family_ids:
        entry, bound, cursor_refusal = _entry(
            family_id, applicable, (before, after), sources, continuation
        )
        entries.append(entry)
        if bound is not None:
            continued = bound
        if cursor_refusal is not None:
            owner_refusal = cursor_refusal
    return _outcome(entries, applicable, continued, continuation, cursor_refusal=owner_refusal)


def _outcome(
    entries: Sequence[ReviewFamilyContextEntry],
    applicable: dict[str, _Applicable],
    continued: ReviewFamilyRosterPage | None,
    continuation: str | None,
    *,
    cursor_refusal: KnowledgeRefusal | None,
) -> FamilyContextOutcome:
    """State the composed context, any continued page, and any refusal the offered cursor earned."""

    unread = [side for side, read in applicable.items() if not read.read]
    complete = all(entry.state == "recorded" for entry in entries) and not unread
    state = "recorded" if complete else "partial"
    context = ReviewFamilyContext(
        state=state,
        detail=_context_detail(state, entries, unread),
        entries=tuple(entries),
        families_total=len(entries),
        families_returned=len(entries),
        families_remaining=0,
        membership_rows_total=sum(
            entry.before.members_total + entry.after.members_total for entry in entries
        ),
        unique_member_revision_total=_unique_members(entries),
        limitations=tuple(
            f"family_context_side_unread:{side}:{applicable[side].reason}" for side in unread
        ),
    )
    if continued is None:
        if continuation is None:
            return FamilyContextOutcome(context=context)
        return FamilyContextOutcome(
            context=context,
            refusal=family_context_cursor_refusal(continuation, cursor_refusal),
        )
    return FamilyContextOutcome(
        context=context,
        page=family_member_page(continued, continued_from=continuation),
    )


def _unique_members(entries: Sequence[ReviewFamilyContextEntry]) -> int:
    """The number of distinct member revisions behind every roster this context carried."""

    return len(
        {
            member.invariant_revision_id
            for entry in entries
            for side in (entry.before, entry.after)
            for member in side.members
        }
    )


def _context_detail(
    state: ReviewFamilyContextState,
    entries: Sequence[ReviewFamilyContextEntry],
    unread: Sequence[str],
) -> str:
    """One sentence naming the state and, when it is partial, exactly which parts are incomplete."""

    if state == "recorded":
        return (
            "every recorded family the two snapshots place this selection in is composed: "
            f"{len(entries)} family context(s), each with its selected before/after family "
            "revision, that revision's own authored guarantee and its recorded member roster"
        )
    incomplete = [
        f"{entry.family_id} ({entry.state}: {entry.detail})"
        for entry in entries
        if entry.state != "recorded"
    ]
    reasons = ", ".join(
        [*incomplete, *(f"the {side} snapshot's scope was not read" for side in unread)]
    )
    return (
        "this family context is partial: every part it did establish is carried, and the parts it "
        f"could not establish are stated with their reason -- {reasons}"
    )[:PROSE_MAX_LENGTH]


def _axis(sources: FamilyContextSources) -> str:
    """Which question the reviewed selector asks, in the vocabulary the sentences are built from."""

    selector = sources.selector
    if isinstance(selector, FamilyRevisionSeed):
        return _AXIS_FAMILY_REVISION
    if isinstance(selector, FamilyIdentitySeed):
        return _AXIS_FAMILY_IDENTITY
    return _AXIS_INVARIANT


def _not_recorded_detail(axis: str, revisions: _FamilyRevisions, side: str) -> str:
    """One sentence stating, for the axis the selection was made on, why a side selected nothing.

    The three axes state three different recorded facts, and the difference matters to a reviewer: an
    invariant whose membership this snapshot does not record, a selection naming an exact revision
    this snapshot does not record, and a family this snapshot records no revision of at all. Each
    sentence also names how many revisions of the family this snapshot *does* record, so an empty
    selection is never read as an empty family.
    """

    recorded = revisions.before_recorded if side == "before" else revisions.after_recorded
    if axis == _AXIS_INVARIANT:
        return (
            f"this snapshot records no membership of the selected subject in this family, so it has "
            f"no selected family revision here and no roster is claimed for it; it records "
            f"{len(recorded)} revision(s) of the family in total"
        )
    if axis == _AXIS_FAMILY_REVISION:
        return (
            "this snapshot does not record the exact family revision this selection names, so no "
            f"roster is claimed for it; it records {len(recorded)} revision(s) of the family"
        )
    return (
        "this snapshot records no revision of the family this selection names, so no roster is "
        "claimed for it and no guarantee is presented as the family's own"
    )


def _no_family(applicable: dict[str, _Applicable], seed_kind: str) -> ReviewFamilyContext:
    """The measured zero -- or the stated failure to measure it -- when no family is applicable."""

    unread = [side for side, read in applicable.items() if not read.read]
    selected = "family" if seed_kind in _FAMILY_SEED_KINDS else "invariant"
    if unread:
        return _stated(
            "unavailable",
            f"no family membership for the selected {selected} was found on "
            f"{'/'.join(side for side in applicable if applicable[side].read) or 'either snapshot'}, "
            f"and {'/'.join(unread)} could not be read, so this is not a measured zero: "
            + "; ".join(f"{side}: {applicable[side].reason}" for side in unread),
        )
    if selected == "family":
        # A family selection reaches this only when neither snapshot records the family at all: a
        # family the snapshot records with no memberships has a context (its guarantee and a measured
        # empty roster), so this state is a zero of the *family* population and nothing else.
        return _stated(
            "no_family_recorded",
            "the recorded scope was read on both snapshots and neither records the selected family "
            "or any revision of it, so no family context exists for it; this is a measured zero of "
            "the family population and not an unread, unavailable or filtered scope",
        )
    return _stated(
        "no_family_recorded",
        f"the recorded scope was read on both snapshots and holds no family membership for the "
        f"selected {selected}, so no family context exists for it; this is a measured zero and not "
        "an unread, unavailable or filtered scope",
    )


def _stated(state: ReviewFamilyContextState, detail: str) -> ReviewFamilyContext:
    """One context that carries a state and its sentence and no family at all."""

    return ReviewFamilyContext(state=state, detail=detail[:PROSE_MAX_LENGTH])


# --- the sides' applicable families ---------------------------------------------------------


def _required_selector(sources: FamilyContextSources) -> KnowledgeReadSeed:
    """The reviewed selector every path past the entry guard has already established.

    The guard answers a review that selected no subject before any side is opened, so a side reaching
    this is one a named identity selected; stating that here keeps the read request's own type exact
    rather than making every caller handle an absence the composition already answered.
    """

    selector = sources.selector
    if selector is None:  # pragma: no cover - the entry guard answers a subjectless review first
        raise ValueError("a family context is composed for a named selection, not for no subject")
    return selector


def _applicable(side: FamilyRosterSide, sources: FamilyContextSources) -> _Applicable:
    """One side's family populations, by the axis the reviewed selector names.

    A **family** selection is answered from the family owner, and that is the fix this leaf needed: a
    family's revisions are the revisions the snapshot records for it, and a revision that cites no
    member is one of them. Deriving this population from membership-bearing read rows instead made a
    memberless head invisible, which silently resolved an authored ambiguity (and made a family with a
    recorded guarantee and no members read as no family at all).

    An **invariant** selection keeps the policy's own answer: the families whose recorded memberships
    cite a selected revision of the reviewed identity are the applicable families, and the revisions
    that cite it are the ones whose rosters that subject's context is about. The family's *whole*
    recorded revision list is still read from the owner, for the counts the sentences publish.
    """

    if side.unreadable is not None or side.read_context is None:
        return _Applicable(reason=side.unreadable or "the snapshot could not be opened")
    selector = _required_selector(sources)
    if isinstance(selector, (FamilyIdentitySeed, FamilyRevisionSeed)):
        return _applicable_family(side, selector)
    return _applicable_invariant(side, selector)


def _applicable_family(
    side: FamilyRosterSide, selector: FamilyIdentitySeed | FamilyRevisionSeed
) -> _Applicable:
    """One side's population for a family selection: every revision the family owner records.

    A `FamilyRevisionSeed` names one exact revision, so its population is that revision where the
    snapshot records it -- an explicit revision choice is not a head selection -- while the family's
    whole recorded list still travels for the counts. A snapshot that records neither the identity nor
    any revision of it answers ``absent``: a measured fact about this snapshot, not an unread scope.
    """

    assert side.store is not None
    family_id = selector.family_id
    recorded = families.list_family_revision_ids(side.store, family_id)
    identity = families.get_family(side.store, family_id)
    if identity is None and not recorded:
        return _Applicable(
            absent=(f"this snapshot records neither the family {family_id} nor any revision of it")
        )
    if isinstance(selector, FamilyRevisionSeed):
        named = selector.revision_id
        population = (named,) if named in recorded else ()
    else:
        population = recorded
    return _Applicable(families={family_id: population}, recorded={family_id: recorded})


def _applicable_invariant(side: FamilyRosterSide, selector: KnowledgeReadSeed) -> _Applicable:
    """One side's applicable families, from the selection policy's own frozen family set."""

    assert side.read_context is not None  # the caller answers an unopenable side before this read
    result = read_knowledge_scope(
        side.database,
        side.read_context,
        KnowledgeReadRequest(seed=selector, budget=KnowledgeReadBudget(max_items=MAX_PAGE_ITEMS)),
    )
    if result.state != "page":
        code = None if result.refusal is None else result.refusal.code
        detail = (
            "the read returned neither a page nor a refusal"
            if result.refusal is None
            else f"{result.refusal.code}: {result.refusal.detail}"
        )
        if code == _SELECTOR_ABSENT:
            return _Applicable(absent=detail)
        return _Applicable(reason=detail)
    grouped: dict[str, list[str]] = {}
    for row in result.directly_containing_families:
        revisions = grouped.setdefault(row.family_id, [])
        if row.family_revision_id not in revisions:
            revisions.append(row.family_revision_id)
    assert side.store is not None
    populations = {family_id: tuple(sorted(revisions)) for family_id, revisions in grouped.items()}
    return _Applicable(
        families=populations,
        recorded={
            family_id: families.list_family_revision_ids(side.store, family_id)
            for family_id in populations
        },
    )


# --- one family's context -------------------------------------------------------------------


def _entry(
    family_id: str,
    applicable: dict[str, _Applicable],
    sides: tuple[FamilyRosterSide, FamilyRosterSide],
    sources: FamilyContextSources,
    continuation: str | None,
) -> tuple[ReviewFamilyContextEntry, ReviewFamilyRosterPage | None, KnowledgeRefusal | None]:
    """Compose one family's context: its selection, its two sides and its inspectable candidates."""

    before, after = sides
    revisions = _FamilyRevisions(
        before=applicable["before"].population(family_id),
        after=applicable["after"].population(family_id),
        before_heads=_heads(applicable["before"].population(family_id), before),
        after_heads=_heads(applicable["after"].population(family_id), after),
        before_recorded=applicable["before"].revisions(family_id),
        after_recorded=applicable["after"].revisions(family_id),
        axis=_axis(sources),
    )
    selection = _selection(family_id, revisions)
    label, label_side = _label(family_id, sides)
    if selection.state in ("ambiguous", "unresolved"):
        return _unresolved_entry(family_id, selection, sides, label, label_side), None, None
    before_read = read_family_roster(
        before,
        family_id,
        RosterContext(
            family_revision_id=selection.before_revision_id,
            recorded_revision_ids=revisions.before_recorded,
            not_recorded_detail=_not_recorded_detail(revisions.axis, revisions, "before"),
        ),
        _roster_request(sources, continuation),
    )
    after_read = read_family_roster(
        after,
        family_id,
        RosterContext(
            family_revision_id=selection.after_revision_id,
            recorded_revision_ids=revisions.after_recorded,
            not_recorded_detail=_not_recorded_detail(revisions.axis, revisions, "after"),
        ),
        _roster_request(sources, None if before_read.bound else continuation),
    )
    entry = ReviewFamilyContextEntry(
        family_id=family_id,
        display_label=label,
        label_side=label_side,
        selection=selection,
        before=before_read.context,
        after=after_read.context,
        state=_entry_state(before_read.context, after_read.context),
        detail=_entry_detail(selection, before_read.context, after_read.context),
    )
    bound = _bound_page(before_read) or _bound_page(after_read)
    refusal = (
        before_read.cursor_refusal
        if before_read.cursor_refusal is not None
        else after_read.cursor_refusal
    )
    return entry, bound, refusal


def _roster_request(sources: FamilyContextSources, continuation: str | None) -> RosterReadRequest:
    """The page bound, movement union and cursor one roster read is asked for."""

    return RosterReadRequest(
        page_size=sources.page_size, movements=sources.movements, continuation=continuation
    )


def _bound_page(read: FamilyRosterRead) -> ReviewFamilyRosterPage | None:
    """The page of the one read that served the presented cursor, when this read is that one."""

    if not read.bound or read.page is None:
        return None
    return read.page


def _heads(revisions: Sequence[str], side: FamilyRosterSide) -> tuple[str, ...]:
    """The heads of one side's selected family revisions, through ``ICR-R07@v1``'s own rule."""

    if not revisions:
        return ()
    return revision_heads(revisions, _touching(side.edges, revisions))


def _touching(
    edges: frozenset[tuple[str, str]], population: Sequence[str]
) -> tuple[tuple[str, str], ...]:
    """The authored edges whose two endpoints are both in one selected population."""

    members = set(population)
    return tuple(
        sorted(
            (successor, predecessor)
            for successor, predecessor in edges
            if successor in members and predecessor in members
        )
    )


def _selection(family_id: str, revisions: _FamilyRevisions) -> ReviewRevisionSelection:
    """State the family's revision selection in ``ICR-R07@v1``'s own value and vocabulary.

    The head rule above is that packet's own. What this function adds is the state and the sentence
    for the population the axis selected -- the family's own recorded revisions for a family
    selection, the revisions a subject's memberships cite for an invariant selection: one unique head
    per side is compared, a side whose population is empty is the one-sided addition or removal, and
    several heads or a lineage that establishes none is carried as exactly that -- with no revision
    chosen and no guarantee presented as the family's own.
    """

    state = _selection_state(revisions)
    if state == "compared":
        before_selected: str | None = revisions.before_heads[0]
        after_selected: str | None = revisions.after_heads[0]
    elif state == "added":
        before_selected, after_selected = None, revisions.after_heads[0]
    elif state == "removed":
        before_selected, after_selected = revisions.before_heads[0], None
    else:
        before_selected, after_selected = None, None
    if state in ("compared", "added", "removed"):
        return ReviewRevisionSelection(
            record_kind="family",
            record_id=family_id,
            state=state,
            before_revision_id=before_selected,
            after_revision_id=after_selected,
            before_heads=revisions.before_heads,
            after_heads=revisions.after_heads,
            before_retained=revisions.before,
            after_retained=revisions.after,
            statement=_selection_sentence(
                family_id, state, before_selected, after_selected, revisions
            ),
        )
    ambiguous = bool(revisions.before_heads) or bool(revisions.after_heads)
    return ReviewRevisionSelection(
        record_kind="family",
        record_id=family_id,
        state="ambiguous" if ambiguous else "unresolved",
        before_heads=revisions.before_heads,
        after_heads=revisions.after_heads,
        before_retained=revisions.before,
        after_retained=revisions.after,
        statement=_unresolved_sentence(family_id, ambiguous, revisions),
    )


def _selection_state(revisions: _FamilyRevisions) -> RevisionSelectionState:
    """Which question the family's two selected revision populations answered."""

    before_heads, after_heads = revisions.before_heads, revisions.after_heads
    if not revisions.before and not revisions.after:
        return "unresolved"
    if not revisions.before:
        return "added" if len(after_heads) == 1 else "unresolved"
    if not revisions.after:
        return "removed" if len(before_heads) == 1 else "unresolved"
    if len(before_heads) != 1 or len(after_heads) != 1:
        return "ambiguous" if before_heads and after_heads else "unresolved"
    return "compared"


def _selection_sentence(
    family_id: str,
    state: RevisionSelectionState,
    before_selected: str | None,
    after_selected: str | None,
    revisions: _FamilyRevisions,
) -> str:
    """The human-readable record of one established family revision selection.

    The history it states is measured from the family owner's own revision lists on both snapshots,
    not from the population this composition selected: a revision that cites no member is recorded
    history a reviewer may open even though no membership reached it, and a sentence counting only
    the selected population printed zero while the store recorded further revisions of the same
    family (ICR-R31 fix round 1, F2).
    """

    other = len(revisions.other_recorded())
    if state in ("added", "removed"):
        shown_side = "after" if state == "added" else "before"
        shown = after_selected if state == "added" else before_selected
        other_side = "before" if state == "added" else "after"
        return (
            f"the {other_side} snapshot {_ADDED_BASIS[revisions.axis]} {family_id}, so the "
            f"{shown_side} family revision {shown} is shown as "
            f"{'an addition' if state == 'added' else 'a removal'} under the one-sided contract; the "
            f"two snapshots record {len(revisions.before_recorded)} before and "
            f"{len(revisions.after_recorded)} after revision(s) of this family, and {other} other "
            "recorded revision(s) of this family remain selectable history"
        )
    return (
        f"the family {family_id} is read at the before family revision {before_selected} and the "
        f"after family revision {after_selected}, {_SELECTION_BASIS[revisions.axis]}; the two "
        f"snapshots record {len(revisions.before_recorded)} before and "
        f"{len(revisions.after_recorded)} after revision(s) of this family, and {other} other "
        "recorded revision(s) of this family remain selectable history"
    )


def _unresolved_sentence(family_id: str, ambiguous: bool, revisions: _FamilyRevisions) -> str:
    """The human-readable record of a family selection that chose no revision, with its reason."""

    if ambiguous:
        return (
            (
                f"ambiguous family revision selection for {family_id}: the recorded memberships place "
                "the selected subject in several legitimate family revisions with no authored "
                "successor ordering between them (before heads "
                f"[{', '.join(revisions.before_heads)}], after heads "
                f"[{', '.join(revisions.after_heads)}]), so no revision was chosen; each candidate's own "
                "guarantee is listed instead"
            )
            if revisions.axis == _AXIS_INVARIANT
            else (
                f"ambiguous family revision selection for {family_id}: the snapshots record several "
                "legitimate head revisions of this family with no authored successor ordering between "
                f"them (before heads [{', '.join(revisions.before_heads)}], after heads "
                f"[{', '.join(revisions.after_heads)}]), so no revision was chosen; each candidate's own "
                "guarantee is listed instead"
            )
        )
    return (
        f"unresolved family revision selection for {family_id}: the "
        f"{'revisions whose recorded memberships cite the selected subject' if revisions.axis == _AXIS_INVARIANT else 'recorded revisions of this family'} "
        f"(before [{', '.join(revisions.before)}], after [{', '.join(revisions.after)}]) establish "
        "no unique head -- the authored lineage leaves several ends or none"
        + (
            ", or a snapshot records no membership here"
            if revisions.axis == _AXIS_INVARIANT
            else ", or a snapshot records no revision of it"
        )
        + " -- so no revision was chosen and no guarantee is presented as this family's own"
    )


def _unresolved_entry(
    family_id: str,
    selection: ReviewRevisionSelection,
    sides: tuple[FamilyRosterSide, FamilyRosterSide],
    label: str | None,
    label_side: ReviewFamilySideName | None,
) -> ReviewFamilyContextEntry:
    """One family context that chose no revision: its candidate heads and their own guarantees."""

    before, after = sides
    candidates = (
        *family_guarantees(before, family_id, selection.before_heads),
        *family_guarantees(after, family_id, selection.after_heads),
    )
    side_state: ReviewFamilySideState = "not_resolved" if candidates else "unreadable"
    return ReviewFamilyContextEntry(
        family_id=family_id,
        display_label=label,
        label_side=label_side,
        selection=selection,
        before=side_statement(before, family_id, side_state, selection.statement),
        after=side_statement(after, family_id, side_state, selection.statement),
        candidates=candidates,
        state="unresolved" if candidates else "unavailable",
        detail=selection.statement,
    )


def family_guarantees(
    side: FamilyRosterSide, family_id: str, revision_ids: Sequence[str]
) -> tuple[ReviewFamilyGuarantee, ...]:
    """Each named revision's own guarantee from the family owner, or nothing when it is not readable."""

    if side.store is None:
        return ()
    return tuple(
        guarantee
        for guarantee in (
            family_guarantee(side.store, family_id, revision_id) for revision_id in revision_ids
        )
        if guarantee is not None
    )


def _label(
    family_id: str, sides: tuple[FamilyRosterSide, FamilyRosterSide]
) -> tuple[str | None, ReviewFamilySideName | None]:
    """The family's recorded display label and the snapshot it was read from.

    The candidate's label wins when the candidate records the identity, because that is the side a
    reader acts on; a family only the baseline records keeps the baseline's own wording rather than
    receiving a label nothing recorded.
    """

    for side in reversed(sides):
        if side.store is None:
            continue
        identity = families.get_family(side.store, family_id)
        if identity is not None:
            return identity.display_label, side.name
    return None, None


def _entry_state(
    before: ReviewFamilyRevisionContext, after: ReviewFamilyRevisionContext
) -> ReviewFamilyEntryState:
    """Whether the two sides make a complete family context, a partial one or an unavailable one."""

    sides = (before, after)
    if any(side.state == "unreadable" for side in sides):
        return "unavailable"
    if any(side.page is not None and not side.page.complete for side in sides):
        return "partial"
    if any(member.state == "content_not_on_page" for side in sides for member in side.members):
        return "partial"
    return "recorded"


def _entry_detail(
    selection: ReviewRevisionSelection,
    before: ReviewFamilyRevisionContext,
    after: ReviewFamilyRevisionContext,
) -> str:
    """One sentence carrying the selection's own statement and each side's own statement."""

    return f"{selection.statement}; before: {before.detail}; after: {after.detail}"[
        :PROSE_MAX_LENGTH
    ]
