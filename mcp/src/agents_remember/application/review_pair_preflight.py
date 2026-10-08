"""The refusals that answer for a resolved review *pair* before any subject is read from it.

Two entry reads ask the whole pair a question before a reviewer opens it: the subject catalogue
(which identities the pair records) and the changed-intent summary (how the pair's authored intent
differs). Both must refuse by the same rule and in the same order, or the entry could offer a count
for a pair its catalogue refuses. This module is that one rule:

1. a closed leaf's record answers in its own words -- a half this leaf never recorded is a fact about
   the repository's history, not content that was expected and lost (ICR-R12);
2. a live pair with an absent half names the half and the file, because authoring a candidate and
   placing the dataset it forks from are different actions;
3. a half that is present but cannot be read as a dataset, and a candidate receipt that cannot be
   read, are refused by name rather than raised out of a later read as a traceback.

It reads each half's bytes only as far as those checks need; what the pair *holds* is the caller's
question.
"""

from __future__ import annotations

from agents_remember.application.knowledge_before_half import unreadable_half_refusal
from agents_remember.application.review_candidate_resolution import (
    ReviewCandidateResolution,
    candidate_receipt_refusal,
    missing_dataset_half,
    refusal,
)
from agents_remember.application.review_committed_leaf import closed_leaf_dataset_refusal
from agents_remember.application.review_legacy_comparison import knowledge_unavailable_refusal
from agents_remember.models.knowledge.review import ReviewRefusal

__all__ = ["absent_pair_refusal", "pair_preflight_refusal"]


def pair_preflight_refusal(resolved: ReviewCandidateResolution) -> ReviewRefusal | None:
    """The first refusal the resolved pair earns as a whole, or ``None`` when both halves read."""

    return (
        knowledge_unavailable_refusal(resolved)
        or closed_leaf_dataset_refusal(resolved)
        or absent_pair_refusal(resolved)
        or unreadable_half_refusal(resolved.baseline_database, resolved.candidate_database)
        or candidate_receipt_refusal(resolved)
    )


def absent_pair_refusal(resolved: ReviewCandidateResolution) -> ReviewRefusal | None:
    """The refusal one half of a resolved pair being absent earns, or ``None`` when both are there.

    It is the *live* pair's answer and it is deliberately narrower than the closed leaf's: it names
    the half and the file, and it says that neither half is read out of the live coordination tree.
    Which half is missing is the fact a reader acts on -- authoring a candidate and placing the
    dataset it forks from are different actions -- so the two are never reported as one.
    """

    absent = missing_dataset_half(resolved)
    if absent is None:
        return None
    half, database = absent
    return refusal(
        "candidate_dataset_absent",
        (
            f"the resolved {half} dataset is absent, so the pair has nothing to compare; the review "
            "reads neither of its two halves out of the live coordination tree and substitutes no "
            "other dataset"
        ),
        next_action=(
            "restore the named recorded memory tree or rebuild its derived index, then reopen the "
            "review; no current memory tree is substituted"
        ),
        offending_input=database.name,
    )
