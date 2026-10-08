"""The recorded before/after relationship union of one review (ICR-R08@v1).

This module owns the *values* one relationship comparison is displayed as, and it owns them as one
two-sided fact rather than two one-sided rows. The rules the shape enforces:

* **A relationship is displayed with both sides.** One :class:`ReviewRelationshipMovement` carries
  the recorded sides the two snapshots hold for one association, so a realization the author moved
  from path A to path B is *one* value showing A and B -- not two unrelated locations, and never
  only the side the candidate holds. The ``before`` collection is a tuple because the recorded facts
  can be many-to-one: an association the author recorded twice on the baseline and once on the
  candidate is displayed with all three sides, not with one of them chosen.
* **The canonical identity is carried, and its absence is a stated gap.** ``record_kind`` and
  ``record_id`` are the identity the association sits under -- the invariant whose realization moved,
  or the family whose membership moved. Two sides that name different identities are not silently
  paired under one of them: the movement carries an identity gap naming both.
* **A state is never blank.** Every side states which snapshot fact it is: ``recorded`` (the
  relationship and its recorded address are both there), ``unresolved`` (the relationship is
  recorded and its address is not readable), ``ungoverned`` (the snapshot records the identity and no
  governing route), ``unavailable`` (the snapshot records the identity and its route declarations
  were not read) or ``not_recorded`` (the snapshot does not record the identity at all). Each carries
  the sentence that says so, and the three last states are deliberately different facts.
* **Authored lineage is the author's own edge, and it is labelled as such.** A successor, a split and
  a merge are read from the snapshots' own predecessor tables; nothing here derives a lineage from a
  path's similarity, a label, a version or an insertion order, and the vocabulary has no field a
  similarity score could be recorded in.
* **A rename is an inference with its own label, never a movement.** :class:`ReviewRenameInference`
  can only be constructed as Git's own similarity detection over the two bound trees; its state
  distinguishes a measured pairing, a measured non-pairing and an inference that was not measured at
  all; and its ``statement`` says, in the value itself, that it is not proof that an invariant moved.
  No side, identity or association is ever built from it -- the movement's sides are the recorded
  anchors, and this value travels beside them as a labelled suggestion about the source.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from agents_remember.models.knowledge.base import (
    LABEL_MAX_LENGTH,
    PATH_MAX_LENGTH,
    PROSE_MAX_LENGTH,
    REFERENCE_MAX_LENGTH,
    KnowledgeModel,
)
from agents_remember.models.knowledge.diff import DiffCoverage

__all__ = [
    "RENAME_INFERENCE_KIND",
    "AuthoredLineageKind",
    "RenameInferenceState",
    "ReviewAuthoredLineage",
    "ReviewPairingBasis",
    "ReviewRelationshipGap",
    "ReviewRelationshipGapCode",
    "ReviewRelationshipKind",
    "ReviewRelationshipMovement",
    "ReviewRelationshipSide",
    "ReviewRelationshipSideName",
    "ReviewRelationshipState",
    "ReviewRelationshipTransition",
]

# The relationship kinds the union holds. Three of them are union items the comparison already
# selects (a realization claim, a family membership and an advertised frontier link); the fourth is
# the reviewed identity's own governing-route association, which is a recorded join row rather than
# a union item and is read for that identity on each snapshot.
ReviewRelationshipKind = Literal[
    "realization", "membership", "advertised_family", "governing_route"
]

ReviewRelationshipSideName = Literal["before", "after"]

# What one side of one relationship is. ``recorded`` is the ordinary case: the snapshot holds the
# relationship row and the address it names. ``unresolved`` is the relationship recorded with an
# address this display could not read, ``ungoverned`` is an identity the snapshot records with no
# governing route, ``unavailable`` is an identity the snapshot records whose route declarations this
# display could not read, and ``not_recorded`` is an identity the snapshot does not record at all --
# the last three are different facts and none of them is a missing route.
ReviewRelationshipState = Literal[
    "recorded", "absent", "unresolved", "ungoverned", "unavailable", "not_recorded"
]

# How one relationship moved between the two snapshots. ``retracted`` and ``added`` are one-sided
# facts: the association is recorded by one snapshot and the other snapshot does not hold it at all,
# and it is displayed rather than dropped. ``outside_selection`` is the third one-sided fact and is
# deliberately not ``retracted``: the other snapshot *holds* the record and the declared selection did
# not reach it, which is a fact about the selection rather than a deletion -- the comparison's own
# named misreading, kept out of this vocabulary by having its own word. ``reassigned`` is the
# family-association case: both snapshots record the association and the family revision it sits in
# is not the same one. A governing route is never ``reassigned``: a family declares a set of routes,
# so a changed route reads as one ``retracted`` and one ``added`` relationship. ``unresolved`` states
# that no association comparison was measurable: a governing-route side whose route declarations
# were not read is compared with nothing.
ReviewRelationshipTransition = Literal[
    "unchanged", "moved", "reassigned", "retracted", "outside_selection", "added", "unresolved"
]

# The three authored relations the lineage entries name. They are read from the snapshots' own
# predecessor tables -- the child's declaration of which revision it replaced -- and from nothing
# else.
AuthoredLineageKind = Literal["succession", "split", "merge"]

# The one basis a rename inference may claim. It is a literal with one member so no caller can
# record a rename of its own as though a tool had measured it.
RENAME_INFERENCE_KIND: Literal["git_rename_detection"] = "git_rename_detection"

# ``inferred`` is a pairing Git's own similarity detection reported; ``not_paired`` is a measured
# absence of that pairing; ``unavailable`` is the state where the inference was not measured at all
# and no pairing may be reported either way.
RenameInferenceState = Literal["inferred", "not_paired", "unavailable"]

# The codes a gap is stated with. Each one is a different reason the display could not establish a
# fact, so a reader can act on the code and not only on the prose beside it.
ReviewRelationshipGapCode = Literal[
    "anchor_unrecorded",
    "anchor_unresolved",
    "identity_not_recorded",
    "identity_differs",
    "predecessor_records_no_relationship",
    "successor_line_unresolved",
    "route_not_recorded",
    "route_unavailable",
]

# Why one movement's two sides are displayed as one association. Every value is a *recorded* relation
# and none of them is a similarity: the same relationship row selected by both snapshots, the same
# member revision under a moved family revision, or an authored old/new edge between the revisions the
# two rows cite. ``authored_successor_head_revision`` is the same authored line read one step further:
# the candidate row was not selected by the comparison at all and was read at the uniquely established
# head of that line (ICR-R08@v1's ruling), which the movement states so a reader knows the row came
# from outside the comparison's page.
ReviewPairingBasis = Literal[
    "same_recorded_relationship",
    "same_governed_identity",
    "same_member_revision",
    "authored_successor_revision",
    "authored_successor_member_revision",
    "authored_successor_head_revision",
    "authored_successor_line_revision",
    "recorded_family_revision",
]


class ReviewRelationshipGap(KnowledgeModel):
    """One fact this display could not establish about one side, named with its side and reason.

    It is the shape the packet's Failure And Recovery Behavior takes as a value: an old or new
    anchor that could not be resolved stays *visible* -- the side it belongs to, the field it is
    about, the code that names the reason and the sentence that states it -- instead of the
    association being dropped from the display or rendered as though it had no second side.
    """

    side: ReviewRelationshipSideName
    field: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    code: ReviewRelationshipGapCode
    detail: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)


class ReviewRelationshipSide(KnowledgeModel):
    """One snapshot's recorded side of one relationship, or its own stated non-recording.

    Every field is what *that* snapshot recorded: the relationship row's own identity, the address
    and role its author wrote, the identity and revision the association cites, and the resolution
    the read reached against that snapshot's code tree. ``item_coverage`` is the shipped
    comparison's own statement about the union item this side came from -- both snapshots selected
    it, the other snapshot holds it but did not select it, or the other snapshot does not hold it at
    all -- which is what makes "this side exists only on the baseline" a carried fact rather than a
    second derivation. ``detail`` is required because every side states which fact it is, including
    the two that are not a relationship at all.
    """

    side: ReviewRelationshipSideName
    state: ReviewRelationshipState
    item_coverage: DiffCoverage = "selected_both"
    relationship_id: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    path: str | None = Field(default=None, max_length=PATH_MAX_LENGTH)
    role: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)
    rationale: str | None = Field(default=None, max_length=PROSE_MAX_LENGTH)
    resolution: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)
    resolution_detail: str | None = Field(default=None, max_length=PROSE_MAX_LENGTH)
    recorded_source_identity: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)
    observed_source_identity: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)
    change_state: Literal["changed", "unchanged", "not_selected"] | None = None
    reached_via: tuple[str, ...] = ()
    record_kind: Literal["invariant", "family"] | None = None
    record_id: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    revision_id: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    member_revision_id: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    route_id: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    route_path: str | None = Field(default=None, max_length=PATH_MAX_LENGTH)
    detail: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)

    @model_validator(mode="after")
    def _require_a_recorded_side_to_name_its_relationship(self) -> ReviewRelationshipSide:
        """Refuse a side that says it holds a relationship without carrying its identity.

        ``recorded`` is a claim about a stored row, so it carries the row's own identity; a side that
        could not read one is ``unresolved`` and says why. Anything else would let a blank
        relationship id be displayed as a recorded association.
        """

        if self.state == "recorded" and self.relationship_id is None:
            raise ValueError(
                "a recorded relationship side names the relationship it read; a side with no "
                "identity is unresolved and states the reason instead"
            )
        return self


class ReviewAuthoredLineage(KnowledgeModel):
    """One authored old/new relation the movement's revisions take part in.

    ``kind`` is the relation the author's own predecessor rows establish -- a successor naming its
    predecessor, one revision with several recorded successors, or one revision naming several
    predecessors. ``related_revision_ids`` is every other revision of that relation, so a split is
    displayed with all of the successors its author recorded rather than with the one a pairing
    happened to pick.
    """

    kind: AuthoredLineageKind
    side: ReviewRelationshipSideName
    revision_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    related_revision_ids: tuple[str, ...] = Field(min_length=1)
    statement: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)


class ReviewRenameInference(KnowledgeModel):
    """Git's own rename detection over the two bound code trees, labelled as the inference it is.

    The value exists so a source rename can be *displayed* without ever being promoted to a movement:
    ``basis`` is Git's detection and can be nothing else, ``state`` separates a measured pairing from
    a measured non-pairing and from an inference that was not measured, and the command that produced
    it travels with it. The two paths are present exactly when Git paired them, and the statement
    says that this is a similarity inference about the source -- never that the invariant moved,
    because the movement displayed beside it is the pair of recorded anchors.
    """

    state: RenameInferenceState
    basis: Literal["git_rename_detection"] = RENAME_INFERENCE_KIND
    before_path: str | None = Field(default=None, max_length=PATH_MAX_LENGTH)
    after_path: str | None = Field(default=None, max_length=PATH_MAX_LENGTH)
    similarity: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)
    command: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    statement: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)

    @model_validator(mode="after")
    def _require_a_pair_exactly_when_git_paired_one(self) -> ReviewRenameInference:
        """Keep a pairing and its state one fact, and keep the unavailable state pairless."""

        paired = self.before_path is not None and self.after_path is not None
        if (self.state == "inferred") != paired:
            raise ValueError(
                "an inferred rename carries the pair Git reported, and a state that reports no "
                "pairing carries no paths; either would otherwise read as the other"
            )
        if self.state == "unavailable" and self.similarity is not None:
            raise ValueError(
                "an inference that was not measured carries no similarity word: a score beside it "
                "would read as a measurement of the two trees that was never made"
            )
        return self


class ReviewRelationshipMovement(KnowledgeModel):
    """One relationship of the recorded before/after union, displayed with both of its sides.

    ``before`` holds every recorded side the baseline union holds for this association and ``after``
    holds the candidate's one side, so the two shapes the packet names are the same value: a
    realization the author moved from A to B is ``before=(A,)`` with ``after=B`` under one invariant
    identity, and a realization the candidate withdrew is ``before=(A,)`` with no after side at all
    -- still displayed, as a ``retracted`` association, rather than vanishing with the link.

    ``change_state`` is the shipped comparison's own statement about the relationship's *source
    observation* (``changed``/``unchanged``/``not_selected``) and is carried rather than recomputed;
    each side carries its own, because the comparison makes it per union item. ``pairing_basis`` is
    **why** the two sides are one association and is present exactly when there are two sides: the
    display states the recorded relation it paired on rather than asserting a bare movement.
    ``lineage``, ``gaps`` and ``rename_inference`` are the three things that may surround a movement:
    the author's recorded edges, the facts this display could not establish, and Git's labelled rename
    inference -- which never stands in for either of the first two.
    """

    relationship_kind: ReviewRelationshipKind
    record_kind: Literal["invariant", "family"]
    record_id: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    transition: ReviewRelationshipTransition
    before: tuple[ReviewRelationshipSide, ...] = ()
    after: ReviewRelationshipSide | None = None
    pairing_basis: ReviewPairingBasis | None = None
    lineage: tuple[ReviewAuthoredLineage, ...] = ()
    gaps: tuple[ReviewRelationshipGap, ...] = ()
    rename_inference: ReviewRenameInference | None = None
    statement: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)

    @model_validator(mode="after")
    def _require_the_transition_to_match_its_sides(self) -> ReviewRelationshipMovement:
        """Refuse a transition whose sides do not state it.

        A one-sided transition carrying two sides, or a two-sided one carrying a single side, is
        exactly how a retraction comes to read as a change and how a movement comes to read as an
        addition, so the correspondence is a property of the value rather than a rule a renderer is
        asked to keep.
        """

        sides = (len(self.before), 0 if self.after is None else 1)
        if self.transition == "added" and sides != (0, 1):
            raise ValueError(
                "an added relationship is the candidate's one recorded side and no before side; a "
                "movement with two sides states a transition that has them"
            )
        if self.transition in ("retracted", "outside_selection") and sides != (1, 0):
            raise ValueError(
                "a one-sided relationship is displayed as the single recorded side it is -- "
                "withdrawn, or held by the other snapshot outside the declared selection -- and once "
                "per recorded side rather than summed"
            )
        if self.transition in ("unchanged", "moved", "reassigned", "unresolved") and 0 in sides:
            raise ValueError(
                "a two-sided transition displays the side each snapshot recorded; a movement with "
                "one side is an addition or a retraction and says so"
            )
        return self

    @model_validator(mode="after")
    def _require_every_gap_to_name_a_displayed_side(self) -> ReviewRelationshipMovement:
        """Refuse a gap about a side this movement does not display."""

        after_named = self.after is not None
        for gap in self.gaps:
            if gap.side == "before" and not self.before:
                raise ValueError(
                    "a gap about the before side belongs to a movement that displays one; the "
                    "absence of a whole side is the transition, not a gap"
                )
            if gap.side == "after" and not after_named:
                raise ValueError(
                    "a gap about the after side belongs to a movement that displays one; the "
                    "absence of a whole side is the transition, not a gap"
                )
        return self

    @model_validator(mode="after")
    def _require_a_paired_movement_to_name_its_pairing_basis(self) -> ReviewRelationshipMovement:
        """Refuse a movement that pairs two sides without saying what paired them.

        The display may pair two recorded rows only on a recorded relation, and a reader has to be
        able to see which one: a two-sided movement names its basis, and a one-sided movement names
        none because nothing was paired.
        """

        paired = self.after is not None and bool(self.before)
        if paired and self.pairing_basis is None:
            raise ValueError(
                "a movement with two sides states the recorded relation it paired them on; an "
                "unstated pairing is how a display comes to assert a movement it cannot show"
            )
        if not paired and self.pairing_basis is not None:
            raise ValueError("a one-sided movement paired nothing, so it names no pairing basis")
        return self

    @model_validator(mode="after")
    def _require_an_unresolved_identity_to_state_itself(self) -> ReviewRelationshipMovement:
        """Refuse a movement that names no identity without saying why none could be named.

        The identity is what the association is preserved *under*, so a movement without one is a
        display that could not establish the packet's core fact -- and it says so, in a gap, rather
        than carrying an identity-shaped blank or a relationship row's id in the identity's place.
        """

        if self.record_id is None and not any(
            gap.code == "identity_not_recorded" for gap in self.gaps
        ):
            raise ValueError(
                "a movement that could not establish the identity its association sits under states "
                "that as an identity gap; an unexplained absence reads as an identity that is not "
                "there to display"
            )
        return self
