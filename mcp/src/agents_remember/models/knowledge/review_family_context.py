"""The comparison-bound family review context: recorded families, guarantees and member revisions.

``ICR-R31@v1`` asks one question of the normal task review that neither the flat subject catalogue
(``ICR-R09@v1``) nor the relationship movement union (``ICR-R08@v1``) answers on its own: **which
recorded families does the selected subject belong to on each snapshot, what is each selected family
revision's own authored joint guarantee, and which exact member revisions does that revision record
-- including the unchanged siblings**. No existing payload value carries that composition, so it has
its own vocabulary here, and every rule below is a refusal to let a rendering invent one:

* **A guarantee is the family's own authored text, never assembled from members.** The value here
  carries the family revision's stored ``joint_guarantee``, its display version, its provenance and
  its payload seal exactly as the family owner read them. There is no field for a derived summary,
  and no field that could hold a verdict about whether the guarantee still holds.
* **A membership cites one exact family revision and one exact member revision.** The member value
  carries both identities, so a shared member is *one* canonical revision referenced beneath every
  family that records it rather than a copy per context, and a successor family revision's roster is
  read from its own rows rather than inherited from a moving pointer.
* **A side states which snapshot fact it is.** ``recorded`` is a roster the snapshot really holds,
  ``not_recorded`` is a snapshot that records no membership of the selected subject under this
  family, ``not_resolved`` is an authored lineage this context could not establish a single family
  revision from, and ``unreadable`` is a snapshot whose read the owner refused. None of the four is
  an empty roster, and the roster page beneath ``recorded`` says separately how much of it this page
  carried and how to reach the rest.
* **The five status dimensions stay separate.** The member's statement, the recorded relationships,
  the mechanical source changes, the execution observations and the authored assessments are owned
  by their own modules and are only *referenced* here (by identity, and through the owner's own
  published collection named on :class:`ReviewFamilyContextReferences`). No field in this module can
  hold a Changed/Passed conclusion, so a member change cannot be rendered as a claim about its
  family's guarantee.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from agents_remember.models.knowledge.base import (
    LABEL_MAX_LENGTH,
    PROSE_MAX_LENGTH,
    REFERENCE_MAX_LENGTH,
    SHA256_PATTERN,
    KnowledgeModel,
)
from agents_remember.models.knowledge.read import KnowledgeReadCounts
from agents_remember.models.knowledge.review_family_source import (
    ReviewFamilyMemberSource,
    ReviewSourceLocatorState,
    source_locator_state,
)
from agents_remember.models.knowledge.revision_selection import ReviewRevisionSelection

__all__ = [
    "FAMILY_CONTEXT_JOIN_KEY",
    "ReviewFamilyContext",
    "ReviewFamilyContextEntry",
    "ReviewFamilyContextReferences",
    "ReviewFamilyContextState",
    "ReviewFamilyEntryState",
    "ReviewFamilyGuarantee",
    "ReviewFamilyMember",
    "ReviewFamilyMemberSource",
    "ReviewFamilyRevisionContext",
    "ReviewFamilyRosterPage",
    "ReviewFamilySideName",
    "ReviewFamilySideState",
    "ReviewSourceLocatorState",
    "source_locator_state",
]

# What this context answers: whether the recorded scope was read, held no applicable family, could
# not be read, or was never asked about a subject at all. ``recorded`` means every composed family
# context is complete; ``partial`` means at least one of them states an unresolved, truncated or
# unreadable part beside the parts it did establish; ``no_family_recorded`` is a *measured* zero and
# is deliberately not spelled ``empty``; ``no_subject_selected`` is the task-context review, which
# compared no operand and therefore claims nothing about families; ``unavailable`` is a selection
# this composition could not resolve at all (an unrecognized seed kind, or neither snapshot read).
ReviewFamilyContextState = Literal[
    "recorded",
    "partial",
    "no_family_recorded",
    "no_subject_selected",
    "unavailable",
]

# Which snapshot one side context or one label belongs to. The two are the model's own two sides, so
# a value cannot name a third one and a rendering never has to map a spelling.
ReviewFamilySideName = Literal["before", "after"]

# One snapshot's side of one family context. The four are distinct facts and none of them is an
# empty roster: ``recorded`` holds the side's own selected family revision, ``not_recorded`` is the
# snapshot recording no membership of the selected subject under this family, ``not_resolved`` is an
# authored lineage this context could not reduce to one head (so nothing was chosen), and
# ``unreadable`` is the read owner refusing this side.
ReviewFamilySideState = Literal["recorded", "not_recorded", "not_resolved", "unreadable"]

# One family's context in the response: ``recorded`` is complete on every side it has, ``partial``
# carries a stated incomplete part, ``unresolved`` names an ambiguous or broken authored lineage with
# its inspectable candidates and no chosen revision, and ``unavailable`` is the composition failing
# to read a side at all.
ReviewFamilyEntryState = Literal["recorded", "partial", "unresolved", "unavailable"]

# The one key that joins a member context to the evidence and assessment owners' own collections:
# every record those owners publish about a member names the exact invariant revision it is about,
# and so does the member value here. It is a value rather than only prose because a rendering that
# joins on a display label or a path instead would be inventing the association.
FAMILY_CONTEXT_JOIN_KEY = "invariant_revision_id"

# The owners' own published collections this context points into. They are named, never copied: each
# one keeps its own status dimension, its own counts and its own refusal, and a reader follows the
# name to the collection rather than reading a second-hand sentence about it here.
RELATIONSHIP_UNION_OWNER = "source.relationships"
SOURCE_INVENTORY_OWNER = "source.inventory"
EVIDENCE_LINKS_OWNER = "evidence.evidence_links"
OBSERVATIONS_OWNER = "evidence.observations"
ASSESSMENTS_OWNER = "evidence.assessments"
APPLICABILITY_OWNER = "knowledge.applicability"


class ReviewFamilyGuarantee(KnowledgeModel):
    """One family revision's authored joint guarantee, exactly as the family owner stored it.

    The guarantee is the family's own claim about a set of invariant revisions holding together; it
    is never assembled from its members and it is never rewritten when one of them changes, so this
    value is one immutable family revision's text with its own display version, provenance and
    payload seal. ``state_at_origin`` and ``acceptance_ref`` travel together because an accepted
    origin without the authority that accepted it would be an acceptance nobody recorded.
    """

    family_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    revision_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    display_version: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    joint_guarantee: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    state_at_origin: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    acceptance_ref: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    provenance: dict[str, object] = Field(default_factory=dict)
    payload_digest: str = Field(pattern=SHA256_PATTERN)


class ReviewFamilyMember(KnowledgeModel):
    """One recorded membership of a selected family revision, with its exact member revision.

    ``member_id`` is the membership row's own identity, which is also the identity the relationship
    union publishes for this association when its page reached the row; ``invariant_revision_id`` is
    the exact member revision the row cites, so the same revision recorded under two families is one
    canonical identity referenced twice rather than one fact copied twice. ``other_family_revision_ids``
    is that sharing made inspectable from either context, read from the membership owner's own rows.
    """

    member_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    invariant_revision_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    invariant_id: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    display_label: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)
    display_version: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)
    state: Literal["recorded", "content_not_on_page"]
    statement: str | None = Field(default=None, max_length=PROSE_MAX_LENGTH)
    applicability: str | None = Field(default=None, max_length=PROSE_MAX_LENGTH)
    essential_conditions: tuple[str, ...] = ()
    exclusions: tuple[str, ...] = ()
    lifecycle: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)
    provenance: dict[str, object] = Field(default_factory=dict)
    payload_digest: str | None = Field(default=None, pattern=SHA256_PATTERN)
    other_family_revision_ids: tuple[str, ...] = ()
    sources: tuple[ReviewFamilyMemberSource, ...] = ()
    # The recorded membership identity under which the payload's own relationship union displays this
    # association, or ``None`` when that union's page did not reach the membership row. The movement
    # itself -- its transition, its pairing basis, its lineage -- is the union's value and is never
    # restated here.
    movement_reference: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    detail: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)

    @model_validator(mode="after")
    def _require_the_content_state_to_match_the_content(self) -> ReviewFamilyMember:
        """Refuse a member whose stated state and carried revision content disagree.

        An invariant revision always stores a statement, so ``recorded`` without one is a member
        presented as read while its content is missing, and ``content_not_on_page`` beside a
        statement is the reverse. Either would make a truncated roster page read as a whole one.
        """

        if self.state == "recorded" and self.statement is None:
            raise ValueError(
                "a member whose revision content this page carried states the recorded content; a "
                "member without it is ``content_not_on_page``"
            )
        if self.state == "content_not_on_page" and self.statement is not None:
            raise ValueError(
                "a member states that its revision content was not on this page exactly when it "
                "carries no content; content beside that state is a contradiction"
            )
        if self.movement_reference is not None and self.movement_reference != self.member_id:
            raise ValueError(
                "a movement reference names this membership row's own recorded identity; another "
                "identity would point a reader at a different association"
            )
        return self


class ReviewFamilyRosterPage(KnowledgeModel):
    """The read owner's own window of one family revision's recorded scope, stated as its page.

    ``counts`` is the read operation's own count value carried whole, so the walk's arithmetic
    (``primary_items_returned`` plus ``primary_items_remaining`` equals ``primary_items_total``) is
    checked by the owner's own validator rather than restated here. ``complete`` is the owner's own
    ``enumeration_complete``: a page is either the whole selected scope or a position in it with the
    cursor that reaches the rest, and the validator refuses the two from disagreeing -- a truncated
    roster must never be presentable as a complete one. ``members_total`` is the owner's measured
    count of the family revision's recorded membership rows, which is the number a reader needs in
    order to see how much of the roster this page actually carried.

    ``complete`` describes the WALK, not the page. It is ``True`` when the read enumerated the whole
    selected scope, which for a multi-page walk happens on the FINAL page -- and that page carries
    only its own share of the selection, not every page's. So a complete walk is the whole roster
    exactly when it is also a single page (``state == "first_page"``); that is the only case in which
    a page may be read as "the roster, whole", and the only case the context's validator holds to
    ``len(members) == members_total``. Reading a complete final page as though it carried every
    recorded membership compares a page-scoped list against a revision-wide count, and doing that
    raised an unhandled ``ValidationError`` for an ordinary multi-page roster (ICR-L24 fix round 3).
    """

    scope: tuple[str, ...] = ()
    state: Literal["first_page", "continued"]
    counts: KnowledgeReadCounts
    complete: bool
    members_total: int = Field(ge=0)
    continuation: str | None = Field(default=None, max_length=PROSE_MAX_LENGTH)
    continued_from: str | None = Field(default=None, max_length=PROSE_MAX_LENGTH)

    @model_validator(mode="after")
    def _require_the_cursor_and_the_remainder_to_agree(self) -> ReviewFamilyRosterPage:
        if self.complete == (self.continuation is not None):
            raise ValueError(
                "a complete enumeration carries no continuation and an incomplete one carries the "
                "cursor that reaches the rest; either reading alone is how a truncated roster is "
                "taken for the whole one"
            )
        if (self.state == "continued") != (self.continued_from is not None):
            raise ValueError(
                "a continued page names the cursor it continued, and a first page continued nothing"
            )
        if (self.counts.primary_items_remaining > 0) != (self.continuation is not None):
            raise ValueError(
                "the owner's own remainder and the owner's own cursor describe one state; a page "
                "reporting items ahead with no way to reach them is not a page of anything"
            )
        if self.members_total != self.counts.memberships_total:
            raise ValueError(
                "this page's member total is the read owner's own count of the seeded family "
                "revision's membership rows; a second number here could describe another selection"
            )
        return self


class ReviewFamilyRevisionContext(KnowledgeModel):
    """One snapshot's context for one family: the selected revision, its guarantee and its members.

    ``members`` is exactly the roster this page carried, and the two counts beside it keep that
    honest: ``members_total`` is the owner's measured roster size for the selected revision, and a
    complete page is the whole roster only when it is also the walk's own first page
    (``state == "first_page"``): a completed continued page carries only its own share of the
    selection and says so, and the pages before it carried the rest. A member whose revision content
    fell outside the page is still listed -- the membership row and its exact member revision are
    the recorded fact -- with its own stated state rather than a silently missing statement.

    ``recorded_revision_ids`` is the family owner's own list of **every** revision of this family the
    snapshot records, which is a different population from the revisions a selection reached: a family
    revision that cites no member is a recorded revision this selection may not have chosen. It is
    published because a reader must be able to see the whole recorded history the selected revision
    was chosen from -- and because a sentence that counted only the selected population was false
    about the store. A recorded side's selected revision is one of these by construction.
    """

    side: ReviewFamilySideName
    family_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    state: ReviewFamilySideState
    family_revision_id: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    recorded_revision_ids: tuple[str, ...] = ()
    guarantee: ReviewFamilyGuarantee | None = None
    members: tuple[ReviewFamilyMember, ...] = ()
    members_total: int = Field(default=0, ge=0)
    page: ReviewFamilyRosterPage | None = None
    detail: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)

    @model_validator(mode="after")
    def _require_the_state_to_match_what_it_carries(self) -> ReviewFamilyRevisionContext:
        recorded = self.state == "recorded"
        if recorded != (self.family_revision_id is not None and self.guarantee is not None):
            raise ValueError(
                "a recorded side names the exact family revision it read and that revision's own "
                "guarantee; a side that read neither is not a recorded side"
            )
        if recorded != (self.page is not None):
            raise ValueError(
                "a recorded side carries the roster page it read; a side without one states which "
                "fact it is instead of rendering an empty roster"
            )
        if recorded and self.family_revision_id not in self.recorded_revision_ids:
            raise ValueError(
                "a recorded side's selected revision is one of the revisions the family owner "
                "records for this snapshot; a selected revision outside that list would be a "
                "revision this snapshot does not hold"
            )
        if not recorded and (self.members or self.members_total):
            raise ValueError(
                "a side that recorded no context carries no members and counts none; members "
                "beside it would be read as a measured empty roster"
            )
        if self.page is not None:
            # A walk the read took in ONE page is the whole roster, so that page must carry every
            # recorded membership: carrying fewer would present a truncated roster as the whole, which
            # is what this guard exists to refuse. A walk whose FINAL page is a continuation carried
            # only that page's share of the selection -- the pages before it carried the rest -- so
            # comparing its carried rows against the revision-wide count would compare two different
            # populations (a page against a revision) and refuse a page that is entirely truthful.
            # That comparison raised a ValidationError for an ordinary multi-page roster and the route
            # answered the reader's own continuation request with HTTP 500 (ICR-L24 fix round 3, V9;
            # the defect is R31's, corrected here -- see the note in ``ReviewFamilyRosterPage``).
            single_page_walk = self.page.complete and self.page.state == "first_page"
            if single_page_walk and len(self.members) != self.members_total:
                raise ValueError(
                    "a roster the read took in one page carries every recorded membership of the "
                    "selected family revision; carrying fewer would present a truncated roster as "
                    "the whole"
                )
            if len(self.members) > self.members_total:
                raise ValueError(
                    "a roster cannot carry more memberships than the owner counted for the exact "
                    "family revision these members cite"
                )
        return self


class ReviewFamilyContextEntry(KnowledgeModel):
    """One family's full context: both snapshots' selected revisions, guarantees and rosters.

    ``selection`` is the explicit before/after family revision selection in ``ICR-R07@v1``'s own
    value and vocabulary -- the unique-head pair, the one-sided addition or removal, or the explicit
    ambiguity or lineage failure that chose no revision. ``candidates`` therefore exists only for the
    two states that chose nothing, and each candidate carries its own guarantee so a reader can
    inspect the heads rather than being shown one of them as the answer.
    """

    family_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    display_label: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)
    label_side: ReviewFamilySideName | None = None
    selection: ReviewRevisionSelection
    before: ReviewFamilyRevisionContext
    after: ReviewFamilyRevisionContext
    candidates: tuple[ReviewFamilyGuarantee, ...] = ()
    state: ReviewFamilyEntryState
    detail: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)

    @model_validator(mode="after")
    def _require_the_entry_to_describe_one_family(self) -> ReviewFamilyContextEntry:
        if self.selection.record_kind != "family" or self.selection.record_id != self.family_id:
            raise ValueError(
                "a family context entry is the selection of the family it carries; another "
                "record's selection beside it describes a different subject"
            )
        if self.before.side != "before" or self.after.side != "after":
            raise ValueError("a family context entry carries one context per snapshot side")
        if (self.display_label is None) != (self.label_side is None):
            raise ValueError(
                "a recorded label names the snapshot it was read from; a label without its side "
                "could be read as a fact of both snapshots and one side without a label means none"
            )
        return self

    @model_validator(mode="after")
    def _require_the_state_to_match_its_candidates_and_sides(self) -> ReviewFamilyContextEntry:
        """Keep the entry's state, its selection, its candidates and its sides one fact.

        Four readings are refused, and each is a different way the same value could lie: a chosen
        revision presented beside a selection that chose none, an ambiguity carrying no inspectable
        candidate, candidate guarantees presented as the family's own guarantee, and a complete
        context built on a selection that established no pair.
        """

        unresolved = self.selection.state in ("ambiguous", "unresolved")
        chosen = self.selection.state in ("compared", "added", "removed")
        if self.state == "unresolved" and not (unresolved and self.candidates):
            raise ValueError(
                "an unresolved family context is a selection that chose no revision and carries "
                "the heads it declined to choose between; without both it is not that state"
            )
        if self.state in ("recorded", "partial") and (not chosen or self.candidates):
            raise ValueError(
                "a recorded or partial family context is built on a selection that established a "
                "pair or a one-sided head and carries no candidates; anything else would present "
                "a chosen revision as a selection that never chose one"
            )
        if self.state == "unavailable" and not (
            self.before.state == "unreadable"
            or self.after.state == "unreadable"
            or (self.selection.state == "unresolved" and not self.candidates)
        ):
            raise ValueError(
                "an unavailable family context names the side whose read failed or the lineage "
                "this context could not establish; without one the state would describe an "
                "absence nothing measured"
            )
        if unresolved and (self.before.guarantee is not None or self.after.guarantee is not None):
            raise ValueError(
                "an unresolved family context carries no chosen revision's guarantee, so no "
                "guarantee may be presented as this family's own on either side"
            )
        if self.selection.state == "added" and self.before.state != "not_recorded":
            raise ValueError(
                "an added family is one the before snapshot records no membership for; a before "
                "context beside it would be a roster for a side that chose no revision"
            )
        if self.selection.state == "removed" and self.after.state != "not_recorded":
            raise ValueError(
                "a removed family is one the after snapshot records no membership for; an after "
                "context beside it would be a roster for a side that chose no revision"
            )
        return self


class ReviewFamilyContextReferences(KnowledgeModel):
    """Where each independent fact beside this context is owned, and the key that joins them.

    The guarantee text, the member statements, the recorded relationships, the mechanical source
    changes, the execution observations and the authored assessments keep their own owners and their
    own status dimensions; this context copies none of them and concludes nothing from them. A
    rendering follows these names into the payload's own collections, joined on ``join_key``.
    """

    relationship_union: str = RELATIONSHIP_UNION_OWNER
    source_inventory: str = SOURCE_INVENTORY_OWNER
    evidence_links: str = EVIDENCE_LINKS_OWNER
    observations: str = OBSERVATIONS_OWNER
    assessments: str = ASSESSMENTS_OWNER
    applicability: str = APPLICABILITY_OWNER
    join_key: str = FAMILY_CONTEXT_JOIN_KEY
    detail: str = (
        "each of these collections is published by its own owner in this payload and keeps its own "
        "state; this context references them by identity and draws no conclusion from any of them"
    )


class ReviewFamilyContext(KnowledgeModel):
    """The comparison-bound family context of one review: its families, counts and references.

    ``families_total`` is measured, not assumed: it is the number of families the two snapshots'
    own recorded memberships place the selected subject in, and ``families_returned`` is how many of
    them this response composed. They are equal by construction -- the composition reads every
    applicable family -- and the validator refuses a context that claims otherwise, because a
    remainder with no way to reach it is exactly what ``ICR-R10@v1`` forbids. The two membership
    counts are the other half of the same honesty: ``membership_rows_total`` counts recorded
    membership rows across the composed contexts, while ``unique_member_revision_total`` counts the
    distinct member revisions behind them, so a member shared by two families is visibly one
    revision and two rows rather than two revisions.
    """

    state: ReviewFamilyContextState
    detail: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    entries: tuple[ReviewFamilyContextEntry, ...] = ()
    families_total: int = Field(default=0, ge=0)
    families_returned: int = Field(default=0, ge=0)
    families_remaining: int = Field(default=0, ge=0)
    membership_rows_total: int = Field(default=0, ge=0)
    unique_member_revision_total: int = Field(default=0, ge=0)
    references: ReviewFamilyContextReferences = Field(default_factory=ReviewFamilyContextReferences)
    limitations: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _require_the_family_counts_to_describe_the_entries(self) -> ReviewFamilyContext:
        if self.families_returned + self.families_remaining != self.families_total:
            raise ValueError(
                "returned plus remaining must equal the measured family total; a context that "
                "reports otherwise cannot be read as a complete account of the applicable families"
            )
        if self.families_returned != len(self.entries):
            raise ValueError(
                "the returned family count is the number of family contexts beside it; a count of "
                "a population the response does not carry is how a shorter list reads as the whole"
            )
        if self.state != "partial" and self.families_remaining:
            raise ValueError(
                "a state other than partial claims every applicable family was composed; a "
                "remainder beside it is a truncated context presented as a whole one"
            )
        if self.state == "recorded" and any(entry.state != "recorded" for entry in self.entries):
            raise ValueError(
                "a recorded context carries only complete family contexts; one partial, "
                "unresolved or unreadable family makes the whole context partial"
            )
        if self.state in ("no_family_recorded", "no_subject_selected") and (
            self.entries or self.families_total
        ):
            raise ValueError(
                "a measured zero family population and a review that selected no subject both "
                "carry no family context; entries beside either state would be families this "
                "composition claims to have read when it composed none"
            )
        if self.state in ("recorded", "partial") and not self.entries:
            raise ValueError(
                "a context that read the recorded scope and composed no family says so with "
                "``no_family_recorded``; an empty recorded context would be indistinguishable "
                "from a read that never happened"
            )
        if self.membership_rows_total != sum(
            entry.before.members_total + entry.after.members_total for entry in self.entries
        ):
            raise ValueError(
                "the membership row total is the sum of the recorded rosters this context "
                "composed; a second number would describe a population nothing read"
            )
        distinct = {
            member.invariant_revision_id
            for entry in self.entries
            for side in (entry.before, entry.after)
            for member in side.members
        }
        if self.unique_member_revision_total != len(distinct):
            raise ValueError(
                "the unique member revision total is the number of distinct member revisions "
                "carried; counting rows instead would inflate the subject total with repeated "
                "memberships"
            )
        return self
