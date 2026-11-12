"""One inventory entry's **actual content** at both bound endpoints (ICR-R03).

The review's Source pane lists changes; this vocabulary is what one of those entries opens *into*.
It is a separate module from :mod:`agents_remember.models.knowledge.review` because it answers a
different question -- not "what did this task change" but "what exactly do these two immutable
objects hold at this path" -- and because the answer has to be able to carry a whole file's text.

Three prohibitions are structural here, not notes a renderer is asked to remember:

* **A side's content is a state, never a blank.** :class:`ReviewSourceSide` carries ``text`` exactly
  when that side's bytes were read *and* are renderable, so an added file has no empty before text,
  a binary file has no empty document, and a submodule pointer is not a zero-length file. A state
  that is not text and carries text cannot be built.
* **A truncated expansion says so.** ``byte_length`` is the exact size of the object the side holds
  and ``truncated`` is true exactly when the carried text is a bounded prefix of it, so "this is
  what the file says" and "this is the first part of what the file says" are different answers.
* **The generation is stated.** Every expansion names the two tree object ids it was opened
  against and whether those are still the pair the leaf's review binds now, so content can never be
  read as belonging to a generation the caller did not ask about.

Nothing in this module selects, ranks, diffs or concludes. The text below is the object's bytes as
Git reports them; what the two sides mean to each other is the renderer's business.
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
from agents_remember.models.knowledge.review import (
    ReviewFileStatus,
    ReviewRefusal,
)

__all__ = [
    "ReviewSourceAdmission",
    "ReviewSourceContentRequest",
    "ReviewSourceContentResult",
    "ReviewSourceCurrentness",
    "ReviewSourceExpansion",
    "ReviewSourceExpansionStatus",
    "ReviewSourcePathBound",
    "ReviewSourceSide",
    "ReviewSourceSideState",
]

# The renderability states one bound endpoint can be in for one path. They are deliberately six
# different facts and not a text/no-text pair:
#
# * ``present``    -- a regular file's bytes, carried as text;
# * ``absent``     -- this endpoint holds no entry at this path at all (the before side of an
#                     addition, the after side of a deletion);
# * ``binary``     -- bytes were read and cannot be carried as text;
# * ``symlink``    -- the entry's content is a link target, not a document's bytes;
# * ``submodule``  -- the entry records a submodule pointer, and no file bytes exist at all;
# * ``unavailable`` -- the entry could not be read (a missing object, a root that is not there, an
#                     entry kind this surface does not render), with the reason stated.
#
# ``absent`` and ``unavailable`` are the two a reader must never conflate: the first is a measured
# fact about the endpoint, the second is a measurement that was not made.
ReviewSourceSideState = Literal[
    "present",
    "absent",
    "binary",
    "symlink",
    "submodule",
    "unavailable",
]

# The two states whose content is carried as text: a regular file's bytes, and a symlink's target.
# Every other state carries no text, so a reader can never mistake a missing or unrenderable side
# for an empty document.
_TEXT_STATES: tuple[ReviewSourceSideState, ...] = ("present", "symlink")

# The two facts the expansion states about the generation it was opened against. They are a closed
# set rather than a free-form flag: the endpoints either are still the ones the leaf's review binds
# now, or the leaf has moved past them, or (``unmeasured``) the recheck could not answer.
ReviewSourceCurrentness = Literal["current", "superseded", "unmeasured"]

# The two measurements that bound a path, named on every expansion. The first is the requested
# generation's own change set; the second is the one this leaf's review publishes, used only when the
# requested pair's measurement could not be made. A changed path is read only when one of them lists
# it; the one other admitted population -- an unchanged path a realization recorded in the same
# comparison's knowledge is anchored at (``admission`` below) -- is bounded by the requested
# generation's own measurement, which is what establishes that it is unchanged. No path outside those
# two populations is read, whatever state the requested generation is in.
ReviewSourcePathBound = Literal["requested_generation", "leaf_change_set"]

# Why the path was opened at all, named on every expansion so a renderer states it rather than
# inferring it. ``changed`` is a path a measured change set lists. ``attributed_unchanged`` is a path
# the requested pair's measured change set does *not* list, opened only because a realization the
# bound comparison's own knowledge records is anchored at it; it is context for that realization and
# never a member of the change inventory or its counts.
ReviewSourceAdmission = Literal["changed", "attributed_unchanged"]

# An expansion's status: the inventory's own vocabulary for a changed path, plus ``unchanged`` for an
# attributed path the measured pair did not change. ``unchanged`` is a measurement (the pair was
# compared and does not differ here) and is kept apart from ``unknown`` (no comparison was made).
ReviewSourceExpansionStatus = ReviewFileStatus | Literal["unchanged"]


class ReviewSourceSide(KnowledgeModel):
    """One bound endpoint's content for one path, or the exact state that says why there is none.

    ``object_id`` is the object this side's address resolves to at its tree -- a blob id for a file
    or a symlink, and the recorded *commit* for a submodule entry -- so a reader has the identity
    even when the bytes are not carried. ``byte_length`` is the object's exact size and is present
    whenever the object was reached; ``truncated`` is true exactly when ``text`` is a bounded prefix
    of those bytes rather than all of them.
    """

    state: ReviewSourceSideState
    text: str | None = None
    detail: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    object_id: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    byte_length: int | None = Field(default=None, ge=0)
    truncated: bool = False

    @model_validator(mode="after")
    def _require_text_exactly_when_the_side_is_textual(self) -> ReviewSourceSide:
        if self.state == "present" and self.text is None:
            raise ValueError(
                "a present side carries the text it measured; a present side with no text is a "
                "blank that reads like an empty file"
            )
        if self.state not in _TEXT_STATES and self.text is not None:
            raise ValueError(
                "only a textual side (a regular file's bytes or a symlink's target) carries text: "
                "an absent, binary, submodule or unavailable side with text would present itself "
                "as a document the endpoint does not hold"
            )
        if self.truncated and self.text is None:
            raise ValueError(
                "only carried text can be a truncated expansion; a side with no text has no "
                "bounded prefix to label"
            )
        if self.state == "absent" and self.object_id is not None:
            raise ValueError(
                "an absent side holds no object, so it names none; an object id here would read as "
                "an entry this endpoint does not have"
            )
        return self


class ReviewSourceExpansion(KnowledgeModel):
    """One inventory entry opened: both endpoints' content, and the generation they came from.

    This is the value the packet requires an entry to expand into. ``before_code_tree_id`` and
    ``after_code_tree_id`` are the *requested* generation -- the two object ids the caller carried
    in from the listing it is reading, echoed back -- so the content is never silently re-read from
    a newer generation. ``currentness`` states whether that generation is still the pair the leaf's
    review binds, and ``currentness_detail`` says what that means for the bytes beside it.

    ``path_bound`` states which measurement admitted ``path``, because two can and only one of them
    is the requested generation's: ``requested_generation`` when the requested pair's own change set
    listed the path, and ``leaf_change_set`` when that measurement could not be made and the change
    set this leaf's review actually publishes bounded the request instead. The second is not a wider
    read -- the path is still a changed path of a measured pair -- and it is stated rather than
    implied, so a reader can always tell which change set the row came from.

    ``admission`` states why the path was opened: ``changed`` for a path a measured change set lists,
    and ``attributed_unchanged`` for a path the requested pair's measured change set does not list but
    that a realization recorded in the bound comparison's knowledge is anchored at. The second is
    always ``unchanged`` in ``status`` and always bounded by the requested generation's own
    measurement, because "unchanged" is only a fact about a pair that was measured.

    ``command`` is the exact reproduction of the two reads, naming both trees and the path, so a
    reader can obtain the same bytes without this surface. It is evidence *beside* the content and
    never a substitute for it: a reference with no text is the failure this vocabulary exists to
    make unrepresentable.
    """

    path: str = Field(min_length=1, max_length=PATH_MAX_LENGTH)
    status: ReviewSourceExpansionStatus
    mode_change: bool = False
    language: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    before: ReviewSourceSide
    after: ReviewSourceSide
    before_code_tree_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    after_code_tree_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    currentness: ReviewSourceCurrentness
    currentness_detail: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    path_bound: ReviewSourcePathBound
    path_bound_detail: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    admission: ReviewSourceAdmission
    admission_detail: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    reference: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    command: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)

    @model_validator(mode="after")
    def _require_attributed_context_to_be_a_measured_unchanged_path(
        self,
    ) -> ReviewSourceExpansion:
        attributed = self.admission == "attributed_unchanged"
        if attributed != (self.status == "unchanged"):
            raise ValueError(
                "an attributed unchanged path is exactly the expansion whose status is 'unchanged': "
                "a changed path opened as context, or unchanged context presented as a change, "
                "would misstate what the measured pair holds"
            )
        if attributed and self.path_bound != "requested_generation":
            raise ValueError(
                "unchanged is a fact about a measured pair, so an attributed unchanged path is "
                "bounded by the requested generation's own measurement and by no other"
            )
        return self


class ReviewSourceContentRequest(KnowledgeModel):
    """One expansion read: the task context, the path, and the exact generation being read.

    The two tree ids are not resolved by the server from the request's task context: they are the
    ids the caller read out of the inventory it is looking at, and the server checks them against
    the pair it binds. That is what makes "the entry I selected" and "the generation I expand"
    the same generation by construction instead of by timing.
    """

    repository_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    master: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    leaf_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    path: str = Field(min_length=1, max_length=PATH_MAX_LENGTH)
    before_code_tree_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    after_code_tree_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)


class ReviewSourceContentResult(KnowledgeModel):
    """The typed outcome of one expansion read: the content, or one typed refusal.

    It mirrors :class:`~agents_remember.models.knowledge.review.KnowledgeReviewResult`'s shape on
    purpose: a caller that received a refusal has no content, and must not be able to read the
    absence of content as an empty file.
    """

    state: Literal["content", "refused"]
    operation: Literal["read_review_source_content"] = "read_review_source_content"
    repository_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    expansion: ReviewSourceExpansion | None = None
    refusal: ReviewRefusal | None = None

    @model_validator(mode="after")
    def _require_one_outcome(self) -> ReviewSourceContentResult:
        if self.state == "content" and (self.expansion is None or self.refusal is not None):
            raise ValueError("a content result carries its expansion and no refusal")
        if self.state == "refused" and (self.refusal is None or self.expansion is not None):
            raise ValueError("a refused content read carries its refusal and no expansion")
        return self
