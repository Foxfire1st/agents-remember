"""The read surface's refusal and submission states; no record collections are composed."""

from __future__ import annotations

from agents_remember.models.knowledge.review import (
    PROPOSED_ASSESSMENT_DISPOSITIONS,
    KnowledgeReviewResult,
    ReviewRefusal,
    ReviewSubmission,
)


def refused(repository_id: str, refusal_value: ReviewRefusal) -> KnowledgeReviewResult:
    """One refused review read, carrying its named refusal and no payload."""

    return KnowledgeReviewResult(
        state="refused", repository_id=repository_id, refusal=refusal_value
    )


def submission(stale: bool) -> ReviewSubmission:
    """Whether an assessment may be submitted, and through what.

    This increment ships no serving route that publishes an assessment, so the surface is
    display-only and says so. It grows no private write path to compensate: the published
    dispositions are the existing authority's own vocabulary, and the next action names that
    authority rather than a control this surface invented.
    """

    if stale:
        return ReviewSubmission(
            state="disabled_stale",
            reason="Candidate changed — open a new comparison",
            next_action="open a new comparison against the candidate's current inputs",
            proposed_dispositions=PROPOSED_ASSESSMENT_DISPOSITIONS,
        )
    return ReviewSubmission(
        state="unavailable",
        reason="This review is read-only; it does not publish judgments or add a private write path.",
        next_action="Author judgments through the existing writer with its author, task and authority provenance.",
        proposed_dispositions=PROPOSED_ASSESSMENT_DISPOSITIONS,
    )
