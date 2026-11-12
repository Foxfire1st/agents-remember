"""One recorded realization claim of a family member, as the review payload references it.

This is the source-reference half of the family review vocabulary in
:mod:`agents_remember.models.knowledge.review_family_context`, which re-exports every name here: the
claim's identity, its stored role and rationale, the address the side's read observed, the anchor's
structured recorded locator, the line ranges the read's anchor resolver placed it on, and the one
rule (:func:`source_locator_state`) that names which of those facts a side established.
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
from agents_remember.models.knowledge.source import LineRangeLocator, SourceLocator

__all__ = ["ReviewFamilyMemberSource", "ReviewSourceLocatorState", "source_locator_state"]

# What one realization claim's recorded locator addresses on one side, as the read observed it.
# ``resolved`` carries the one-based line range(s) the locator addresses in the exact recorded blob;
# ``whole_file`` is a ``file`` locator on the exact recorded blob, which addresses every line and so
# names no range; ``unresolved`` is a recorded locator this read could not place on the recorded
# bytes (the ``resolution`` beside it says why), which is never answered with a guessed range; and
# ``not_observed`` is a claim this read reported no anchor observation for at all.
ReviewSourceLocatorState = Literal["resolved", "whole_file", "unresolved", "not_observed"]


class ReviewFamilyMemberSource(KnowledgeModel):
    """One recorded realization claim of a member revision, as an inspectable source reference.

    This is a *reference*: the claim's own identity, the author's recorded role and rationale, and
    the address the read observed for it. Whether the address resolves, whether the bytes moved and
    what any of it means stay with the owners -- the source inventory and the relationship union --
    and nothing here restates them. ``resolution`` is absent exactly when this read observed the
    anchor not at all, which ``detail`` states rather than leaving an empty resolution to be read as
    a measured agreement.

    ``role`` and ``rationale`` are the claim's stored values carried unchanged; the store holds both
    for every claim, so neither is optional here. ``locator`` is the anchor's own
    structured recorded locator, and ``resolved_ranges`` are the lines the read's anchor resolver
    placed it on in the exact recorded blob of *this* side, so two claims at one path keep two
    regions and a reader never recovers a region from ``detail``. ``locator_state`` names which of
    those facts this side established.
    """

    claim_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    invariant_revision_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    role: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    rationale: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    path: str | None = Field(default=None, max_length=PATH_MAX_LENGTH)
    recorded_source_identity: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)
    observed_source_identity: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)
    resolution: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)
    detail: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    locator: SourceLocator | None = None
    resolved_ranges: tuple[LineRangeLocator, ...] = ()
    locator_state: ReviewSourceLocatorState

    @model_validator(mode="after")
    def _require_an_address_to_travel_with_its_observation(self) -> ReviewFamilyMemberSource:
        if (self.path is None) != (self.resolution is None):
            raise ValueError(
                "a source reference carries the address this read observed and the resolution it "
                "reached, or neither; an address without an observation reads as a resolved "
                "realization and a resolution without an address names nothing"
            )
        if (self.path is None) != (self.locator is None):
            raise ValueError(
                "a recorded locator is part of the address the read observed; one without the "
                "other would place a region at no path or a path with no recorded region"
            )
        return self

    @model_validator(mode="after")
    def _require_the_locator_state_to_match_what_it_carries(self) -> ReviewFamilyMemberSource:
        """Refuse a locator state that disagrees with the locator, ranges and resolution beside it.

        Each refused reading is a way a range could be invented: ranges presented as unresolved, a
        resolved state with no range, a whole file claimed for a narrower locator, or any region
        stated on bytes the read did not find at the recorded path.
        """

        exact = self.resolution == "exact_recorded_blob"
        expected = source_locator_state(
            self.locator, exact=exact, ranged=bool(self.resolved_ranges)
        )
        if self.resolved_ranges and not exact:
            raise ValueError(
                "resolved ranges address the exact recorded blob; beside any other resolution they "
                "would be a region on bytes this read did not find at the recorded path"
            )
        if self.locator_state != expected:
            raise ValueError(
                f"the locator state {self.locator_state!r} disagrees with the carried locator, "
                f"ranges and resolution, which state {expected!r}"
            )
        return self


def source_locator_state(
    locator: SourceLocator | None, *, exact: bool, ranged: bool
) -> ReviewSourceLocatorState:
    """The one locator state a carried locator, resolution and range set support together.

    The projection states a source's locator state through this rule and the model's validator
    checks it through the same rule, so the two cannot come to disagree about what a range means.
    """

    if locator is None:
        return "not_observed"
    if ranged:
        return "resolved"
    if exact and locator.kind == "file":
        return "whole_file"
    return "unresolved"
