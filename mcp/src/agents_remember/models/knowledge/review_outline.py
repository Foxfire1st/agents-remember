"""The index-only navigation outline of a review comparison (MIK-R86)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from agents_remember.models.knowledge.base import (
    LABEL_MAX_LENGTH,
    PROSE_MAX_LENGTH,
    UUID_PATTERN,
    KnowledgeModel,
)
from agents_remember.models.knowledge.review_meaning_effects import ReviewOutlineMeaning

OutlineChange = Literal["added", "removed", "changed", "unchanged", "unknown"]


class ReviewOutlineMember(KnowledgeModel):
    selector_id: str = Field(pattern=UUID_PATTERN)
    id: str
    membership: Literal["both", "joined", "left"]
    # The member's recorded effects in attempt order with the rule-8 state (MIK-R43). The
    # tree badge joins the labels; the state ``none`` shows no badge.
    meaning: ReviewOutlineMeaning


class ReviewOutlineFamily(KnowledgeModel):
    selector_id: str = Field(pattern=UUID_PATTERN)
    id: str
    title: str = Field(max_length=LABEL_MAX_LENGTH)
    change: OutlineChange
    members: tuple[ReviewOutlineMember, ...] = ()
    # The family's recorded effects in attempt order with the rule-8 state (MIK-R43).
    meaning: ReviewOutlineMeaning


class ReviewOutlineInvariant(KnowledgeModel):
    selector_id: str = Field(pattern=UUID_PATTERN)
    id: str
    summary: str = Field(max_length=120)
    change: OutlineChange
    # The invariant's recorded effects in attempt order with the rule-8 state (MIK-R43).
    meaning: ReviewOutlineMeaning


class ReviewOutline(KnowledgeModel):
    state: Literal["outlined", "unavailable"]
    detail: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    families: tuple[ReviewOutlineFamily, ...] = ()
    invariants: tuple[ReviewOutlineInvariant, ...] = ()

    @model_validator(mode="after")
    def _unavailable_lists_nothing(self) -> ReviewOutline:
        if self.state == "unavailable" and (self.families or self.invariants):
            raise ValueError("an unavailable outline carries its reason, never a partial tree")
        return self
