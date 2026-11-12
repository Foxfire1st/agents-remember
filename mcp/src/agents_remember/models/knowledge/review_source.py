"""The source pane's measured inventory, attribution, and recorded source locations."""

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
from agents_remember.models.knowledge.diff import SourceAttribution
from agents_remember.models.knowledge.review_relationships import (
    ReviewRelationshipMovement,
    ReviewRelationshipTransition,
)


class ReviewUnresolvedReference(KnowledgeModel):
    """One reference the surface could not resolve, named rather than dropped or made anonymous.

    It exists so that "cannot be resolved" is a *displayed* fact: a row whose author or examined
    inputs are missing carries one of these instead of an empty attribution, and no rendering is
    able to present the row as one whose basis is simply uninteresting.
    """

    field: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    recorded_reference: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    detail: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)


ReviewRemainingCountName = Literal[
    "locations_remaining",
    "changed_paths_outside_selection",
    "unattributed_changed_paths",
    "unknown_attribution_changed_paths",
    "references_unresolved",
    "records_present_outside_selection",
]


class ReviewRemainingCount(KnowledgeModel):
    """One persistent count exposing what the selection did not reach.

    ``value`` is ``None`` exactly when the quantity has no meaning for this rendering, and the
    reason is then stated: a zero that means "none" and a zero that means "not measured here" are
    different facts and a reviewer must be able to tell them apart.
    """

    name: ReviewRemainingCountName
    value: int | None = Field(default=None, ge=0)
    reason: str | None = Field(default=None, max_length=PROSE_MAX_LENGTH)

    @model_validator(mode="after")
    def _require_a_stated_state(self) -> ReviewRemainingCount:
        if self.value is None and not (self.reason or "").strip():
            raise ValueError(
                "a count without a meaning states why; an unexplained absent count reads as a zero"
            )
        if self.value is not None and self.reason is not None:
            raise ValueError("a measured count carries its value and no not-applicable reason")
        return self


class ReviewSourceLocation(KnowledgeModel):
    """One selected source location under its recorded claim, as the read reported it.

    ``role`` is the author's recorded word and stays ``None`` when the record carries none: a
    missing role is displayed as unclassified rather than guessed from a path, and there is no
    field here for an importance or a ranking derived from a name.

    ``invariant_id`` is the **preserved canonical identity** the location's recorded relationship
    sits under (ICR-R08@v1), which is what makes two rows one association: a realization the author
    moved from A to B is two locations carrying the same ``invariant_id``, each naming the side of the
    movement its own address is (``recorded_side``) and carrying the whole relationship in
    ``movement``. ``counterpart_path`` is the other side's recorded address when the movement records
    exactly one there, and is absent when the other side records several or none -- the movement lists
    every one of them, and no single address is chosen to stand for them. The identity is absent
    exactly when the traversal could not establish it, and the movement beside it states that as a gap
    rather than the location inventing one.
    """

    claim_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    invariant_revision_id: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    path: str = Field(min_length=1, max_length=PATH_MAX_LENGTH)
    role: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)
    rationale: str | None = Field(default=None, max_length=PROSE_MAX_LENGTH)
    recorded_source_identity: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    observed_source_identity: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)
    resolution: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    change_state: Literal["changed", "unchanged", "not_selected"]
    before_only: bool = False
    reached_via: tuple[str, ...] = ()
    invariant_id: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    transition: ReviewRelationshipTransition = "unchanged"
    recorded_side: Literal["before", "after"] | None = None
    counterpart_path: str | None = Field(default=None, max_length=PATH_MAX_LENGTH)
    movement: ReviewRelationshipMovement | None = None


ReviewFileStatus = Literal["added", "deleted", "modified", "type_changed", "unknown"]


ReviewFileContent = Literal["text", "binary", "symlink", "submodule", "unknown"]


class ReviewChangedFile(KnowledgeModel):
    """One path the bound source pair differs at, as an address, a status and a renderability.

    This is the surface's own inventory entry, and it is deliberately three separate facts.
    ``path`` is the **raw filename** exactly as Git recorded it -- a tab or a newline inside it is
    part of the address the same file is expanded with and never a separator -- so the string here
    is the string a later read of that file must use. ``status`` is what happened to the path and
    carries no assessment of it. ``content`` states whether the path's content can be rendered at
    all, which is a property of the content and not of the change: a binary path, a symlink and a
    submodule pointer are listed exactly like a text file and are simply not text.

    ``mode_change`` separates "the bytes moved" from "only the permission word moved" without
    turning either into a different status, and ``detail`` is present exactly when ``content`` is
    ``unknown``, where it says whether the classification was not measured or was not reported for
    this path.
    """

    path: str = Field(min_length=1, max_length=PATH_MAX_LENGTH)
    status: ReviewFileStatus
    content: ReviewFileContent
    mode_change: bool = False
    detail: str | None = Field(default=None, max_length=PROSE_MAX_LENGTH)

    @model_validator(mode="after")
    def _require_an_unknown_content_to_state_its_reason(self) -> ReviewChangedFile:
        if (self.content == "unknown") != (self.detail is not None):
            raise ValueError(
                "an entry whose content cannot be classified states why, and a classified entry "
                "states no reason; an unexplained unknown reads like a measured absence"
            )
        return self


class ReviewUnrepresentablePath(KnowledgeModel):
    """One changed path whose **name** cannot be carried as text by this vocabulary.

    A Git pathname is bytes and this surface carries text. The runner preserves the bytes exactly, so
    a name that is not valid UTF-8 arrives as lone surrogates -- a value a ``str`` field refuses --
    and such a path would otherwise vanish from a response that claimed to be complete, or crash the
    response that was supposed to state the fact. It is carried here instead.

    ``path_bytes`` is the path's exact bytes rendered ASCII-safely (``b'src/caf\xe9-latin1.py'``), so
    the value is valid text and still says precisely which file changed; ``status`` and
    ``mode_change`` are what Git reported for it. Nothing is re-encoded: a re-encoded name would
    address a file this repository does not hold, which is worse than an unusual spelling.
    """

    # The byte form of a pathname is longer than the pathname (up to four characters per byte), so it
    # is bounded by the prose limit rather than by the path limit.
    path_bytes: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    status: ReviewFileStatus
    mode_change: bool = False
    detail: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)


class ReviewSourceInventory(KnowledgeModel):
    """The complete source change set of one bound pair: measured, partial, or unavailable.

    This is the value R02 exists for. It is measured from the two code trees the resolution bound
    and from nothing else, so it is present and complete for a candidate that records no invariant,
    for a candidate whose datasets do not exist, and for a comparison whose knowledge half is a
    different question entirely. The knowledge selection may filter *attribution*; it can never
    remove an entry from here.

    ``state`` is the honesty boundary. ``measured`` means this list is the whole change set of the
    pair, including the measured empty set two identical trees produce. ``unavailable`` means the
    measurement was not made at all, and then ``entries`` is empty **because nothing was observed**:
    the ``detail`` carries the reason, and the count is zero rather than reported as "no changes".
    ``partial`` is the third state and it is not a weaker measurement of the path set -- every
    changed path is listed and one field of some entries could not be classified, which each entry
    states for itself.

    ``listed_total`` is the length of the list above and is checked against it, so a count can never
    describe a population the response does not carry. Both code tree ids travel with the list, so a
    caller can reproduce the exact measurement the entries came from.

    ``unrepresentable_paths`` is the rest of the measured population, and it exists so the two lists
    together are the whole change set: a changed path whose *name* is not valid text cannot be
    ``entries`` without the value itself being unrepresentable, and dropping it would make a partial
    change set read as a complete one. An inventory that carries any of them is ``partial`` by
    construction, because it is exactly that.
    """

    state: Literal["measured", "unavailable"]
    entries: tuple[ReviewChangedFile, ...] = ()
    listed_total: int = Field(ge=0)
    detail: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    partial: bool = False
    command: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    before_code_tree_id: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)
    after_code_tree_id: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)
    unrepresentable_paths: tuple[ReviewUnrepresentablePath, ...] = ()

    @model_validator(mode="after")
    def _require_the_count_to_describe_the_list(self) -> ReviewSourceInventory:
        if self.listed_total != len(self.entries):
            raise ValueError(
                "an inventory's count is the length of the list beside it; a count of a population "
                "the response does not carry is how a partial list is read as a whole one"
            )
        if self.state == "unavailable" and self.entries:
            raise ValueError(
                "an unavailable measurement lists nothing: entries beside it would be read as an "
                "observed change set"
            )
        if self.state == "unavailable" and self.partial:
            raise ValueError(
                "an unavailable measurement is not a partial one; there is no measured remainder "
                "to label"
            )
        if self.unrepresentable_paths and (self.state != "measured" or not self.partial):
            raise ValueError(
                "an inventory that could not carry some changed paths as text is a measured, "
                "partial one; an unrepresentable path beside a complete or unavailable inventory "
                "describes no measurement"
            )
        return self


class ReviewSourcePane(KnowledgeModel):
    """Pane 2 -- Source: the whole-task inventory, selected locations, and what the selection missed.

    ``inventory`` is required and comes first, because it is the pane's one fact that does not depend
    on a knowledge selection: the complete source change set of the comparison's bound pair. Every
    field below it is the *attribution* half -- which of those changes a recorded realization claim
    reaches -- and that half may legitimately be empty, filtered or unmeasured without shortening the
    inventory above it.

    ``attribution`` is that half as **one** accounting rather than three independent lists: the
    measured change population partitioned once into attributed, confirmed unregistered and
    undetermined, with each bound snapshot's inspection state beside it. The three path lists below it
    are that value's own buckets, so a path can appear in exactly one of them and a change nobody
    looked for is never listed as a change nobody registered.
    """

    inventory: ReviewSourceInventory
    locations: tuple[ReviewSourceLocation, ...] = ()
    # The recorded before/after relationship union this review traversed (ICR-R08@v1): every
    # realization, family membership, advertised frontier link and the reviewed identity's governing
    # route, each displayed with both sides. It is a separate collection from ``locations`` because
    # it holds the relationships that have no source address at all -- a membership and a route
    # association -- and because a movement whose two sides are one row at one address is still one
    # relationship rather than two locations.
    relationships: tuple[ReviewRelationshipMovement, ...] = ()
    remaining: tuple[ReviewRemainingCount, ...] = ()
    expansion_reference: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    expansion_command: str | None = Field(default=None, max_length=PROSE_MAX_LENGTH)
    unattributed_changed_paths: tuple[str, ...] = ()
    attributed_changed_paths: tuple[str, ...] = ()
    unknown_attribution_changed_paths: tuple[str, ...] = ()
    attribution: SourceAttribution | None = None
    unresolved: tuple[ReviewUnresolvedReference, ...] = ()
