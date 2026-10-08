"""The shared review refusal vocabulary, independent of full review payload composition."""

from typing import Literal

from pydantic import Field

from agents_remember.models.knowledge.base import (
    PROSE_MAX_LENGTH,
    REFERENCE_MAX_LENGTH,
    KnowledgeModel,
)

__all__ = ["ReviewRefusal", "ReviewRefusalCode"]

ReviewRefusalCode = Literal[
    "candidate_unresolved",
    "candidate_not_live",
    "candidate_dataset_absent",
    "subject_unresolved",
    "comparison_refused",
    "comparison_page_reset",
    "comparison_page_unreadable",
    "source_content_unresolved",
    "review_adapter_unavailable",
    # The leaf-wide tree view only (MIK-R42): its worklist computation could not start before its
    # deadline because the reviewer was computing other worklists, and an input it read kept
    # changing while it was composed.
    "reviewer_busy",
    "inputs_changing",
]


class ReviewRefusal(KnowledgeModel):
    """One typed review refusal, naming the offending input and the concrete next action.

    A refusal is a state and never a degraded success: a caller that receives one has no panes, and
    must not be able to read the absence of panes as a review of an empty candidate.
    """

    code: ReviewRefusalCode
    detail: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    next_action: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    offending_input: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    expected: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    observed: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
