"""Whether the comparison a review renders is still the one the reader was shown (ICR-R17@v1).

Two responsibilities, and they are one: the identity a comparison declares, and the staleness state
that identity earns when a *previous* binding identity is carried beside it.

**The identity is carried, never recomputed.** :func:`comparison_identity` reads the comparison
operation's own ``binding``/``binding_digest``/``selector_digest`` and copies them into the payload's
:class:`~agents_remember.models.knowledge.review.ComparisonIdentity`. It hashes nothing, resolves
nothing and re-derives nothing: the digest an assessment was bound against is the one the comparison
owner published, and a second spelling of it here is how two readers come to compare two different
generations.

**Staleness is a comparison of two identities, and one of them is the caller's.** The reader that is
looking at a displayed comparison carries *that* comparison's ``binding_digest`` on its next read
(``ReviewSurfaceRequest.previous_binding_digest``, supplied by the client's refresh control). When it
disagrees with the comparison rendered now, the state is ``stale``: the identity the reader was
looking at is retained as a **labelled previous input** (``previous_comparison_ref``) and submission
is disabled against the new one, which is what stops a judgement made about inputs that have since
moved from being re-presented as a review of what is there now. When the digest agrees, or when the
caller carried none, the state is ``current`` -- and the model itself refuses a ``stale`` state with
no previous reference, so this module cannot produce a stale claim that names nothing.

**The previous identity is evidence of what was displayed, not an authority.** It selects no
dataset, resolves no candidate and is never substituted for the identity the comparison declares; it
is only ever compared against it. A caller that carries an identity the current comparison does not
match gets the honest ``stale`` answer rather than a fallback: the review is still the candidate's
own comparison, rendered in full, with the mismatch stated.

This module lives beside the review adapter rather than inside it because the adapter is at the
repository's file-size rail and this is one responsibility with one implementation; the adapter
imports these names, and there is no second copy of either rule there.
"""

from __future__ import annotations

from agents_remember.models.knowledge.diff import KnowledgeDiffResult
from agents_remember.models.knowledge.review import ComparisonIdentity, ReviewStaleness

__all__ = [
    "comparison_identity",
    "review_staleness",
]

# What a moved comparison says, in one sentence: the candidate changed and a new comparison is the
# action. It is worded here rather than at a render site so the sentence a reader sees beside a stale
# payload is the one the state itself publishes.
_MOVED_STATEMENT = "Candidate changed — open a new comparison"
_MOVED_FIELDS: tuple[str, ...] = ("comparison-binding",)


def comparison_identity(comparison: KnowledgeDiffResult) -> ComparisonIdentity:
    """The comparison's own declared identity, carried verbatim and never recomputed.

    The three fields read here are the operation's own published ones, and the assertion is the
    operation's contract rather than a fallback: a page result that reports no binding, no binding
    digest or no selector digest declares no identity, and a review of it could name no generation
    for a reader to hold on to. Refusing loudly is what keeps ``current``/``stale`` answerable at all.
    """

    binding = comparison.binding
    digest = comparison.binding_digest
    selector_digest = comparison.selector_digest
    assert binding is not None and digest is not None and selector_digest is not None
    return ComparisonIdentity(
        reference=digest,
        policy_version=comparison.policy_version,
        binding_digest=digest,
        selector_digest=selector_digest,
        before_snapshot_digest=binding.before.logical_digest,
        after_snapshot_digest=binding.after.logical_digest,
        before_code_tree_id=binding.before_code_tree_id,
        after_code_tree_id=binding.after_code_tree_id,
    )


def review_staleness(
    identity: ComparisonIdentity, previous_binding_digest: str | None
) -> ReviewStaleness:
    """Whether the comparison rendered now is still the one a displayed assessment was made against.

    ``previous_binding_digest`` is the identity a reader was looking at, carried on the read that
    replaces it. Its absence is a read that is not replacing anything -- the first read of a
    comparison, and every read of the leaf's own recorded generation -- and it is ``current`` by
    construction rather than by assumption, because there is no previous input to label.
    """

    if previous_binding_digest is None or previous_binding_digest == identity.binding_digest:
        return ReviewStaleness(
            state="current",
            statement="the displayed comparison is the candidate's current comparison",
        )
    return ReviewStaleness(
        state="stale",
        statement=_MOVED_STATEMENT,
        previous_comparison_ref=previous_binding_digest,
        moved=_MOVED_FIELDS,
    )
