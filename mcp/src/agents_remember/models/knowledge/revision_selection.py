"""The explicit before/after revision selection one subject comparison records (ICR-R07@v1).

This module owns the *value* the primary statement comparison is made with: which retained
revision each snapshot's side is compared on, every head the snapshots retain, and the exact
statement that says why that pair -- or no pair -- was used. It holds no SQL, no Git resolution
and no selection policy: the policy that computes it lives in
:mod:`agents_remember.application.review_revision_comparison`, and this module makes the result
unrepresentable to misread rather than merely documented.

* **A pair or an explicit non-pair, never a silent default.** ``state`` says which question was
  answered: ``compared`` names the before head and the after head the two snapshots were compared
  on; ``added``/``removed`` name the one head a known-empty side is shown against (ICR-R06@v1's
  one-sided contract, reused rather than restated); ``ambiguous`` names the multiple heads no
  winner was chosen from; ``unresolved`` names the lineage failure no comparison was fabricated
  from. The validators enforce the pairing, so a state cannot carry the ids of another.
* **Heads are recorded beside the pair.** Even a ``compared`` selection carries every head each
  side retains, and both sides' full retained revision lists travel as ``before_retained`` and
  ``after_retained``. An intermediate revision is therefore addressable -- through the
  comparison's own per-side explicit revision selector -- rather than merely counted, which is
  what "intermediate revisions remain selectable history" means as a value.
* **No field a fabrication could occupy.** There is no timestamp, no similarity score, no
  ordering rank and no "latest" flag anywhere in this vocabulary, so a newest-version claim
  invented from recency or text resemblance cannot be recorded here even by a caller that wanted
  to. The lists are stored sorted so two runs over the same snapshots render the same value, and
  that order is a rendering determinism the policy module states -- never a semantic revision
  rule, which this value could not express.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from agents_remember.models.knowledge.base import (
    PROSE_MAX_LENGTH,
    REFERENCE_MAX_LENGTH,
    KnowledgeModel,
)

__all__ = [
    "ReviewRevisionSelection",
    "RevisionSelectionState",
]

# Which question one subject comparison answered about its two revision populations. ``compared``
# is the unique-head pair; ``added``/``removed`` is a known-empty side shown under ICR-R06@v1;
# ``ambiguous`` is multiple legitimate heads with no winner chosen; ``unresolved`` is a lineage
# failure (a cycle that leaves no head, or an authored edge the snapshot does not retain) with no
# comparison fabricated from it.
RevisionSelectionState = Literal["compared", "ambiguous", "unresolved", "added", "removed"]


class ReviewRevisionSelection(KnowledgeModel):
    """One subject's explicit before/after revision selection, recorded with its own statement.

    ``before_revision_id``/``after_revision_id`` are the selected pair when the two snapshots were
    compared on one revision each, and are absent exactly when no pair was selected: an ambiguous
    or unresolved selection records its heads and its retained revisions but no pair, because a
    pair beside either state would be the arbitrary winner the packet forbids. ``statement`` is
    the human-readable record of the same fact -- the compared heads, the named ambiguity, or the
    named lineage failure -- so a reviewer never has to reconstruct the rule from the ids.
    """

    record_kind: Literal["invariant", "family"]
    record_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    state: RevisionSelectionState
    before_revision_id: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    after_revision_id: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    before_heads: tuple[str, ...] = ()
    after_heads: tuple[str, ...] = ()
    before_retained: tuple[str, ...] = ()
    after_retained: tuple[str, ...] = ()
    statement: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)

    @model_validator(mode="after")
    def _require_the_state_to_match_the_pair(self) -> ReviewRevisionSelection:
        """Refuse a selection whose state and whose recorded pair disagree.

        A comparison names both revisions; a one-sided side names the one head it shows; an
        ambiguous or unresolved selection names none, because a pair there would be a winner
        chosen where the policy chose none.
        """

        before, after = self.before_revision_id, self.after_revision_id
        if self.state == "compared" and (before is None or after is None):
            raise ValueError(
                "a compared selection records the before head and the after head it compared; "
                "a comparison missing one of them cannot be reproduced"
            )
        if self.state == "added" and (before is not None or after is None):
            raise ValueError(
                "an added selection records only the after head it shows; the before side "
                "retained no revision to record"
            )
        if self.state == "removed" and (before is None or after is not None):
            raise ValueError(
                "a removed selection records only the before head it shows; the after side "
                "retained no revision to record"
            )
        if self.state in ("ambiguous", "unresolved") and (before is not None or after is not None):
            raise ValueError(
                "an ambiguous or unresolved selection records no pair: a revision id beside "
                "either state would be the arbitrary winner the selection refused to choose"
            )
        return self

    @model_validator(mode="after")
    def _require_the_pair_to_come_from_the_heads(self) -> ReviewRevisionSelection:
        """Refuse a selected revision that is not one of the recorded heads.

        The pair is drawn from the heads the snapshots retain, never from a revision the
        snapshots do not hold or from one a successor replaced. A selected id outside the heads
        is how a collection-order or presence-based default would enter this value wearing the
        heads' names.
        """

        pairs = (
            (self.before_revision_id, self.before_heads, "before"),
            (self.after_revision_id, self.after_heads, "after"),
        )
        for selected, heads, side in pairs:
            if selected is not None and selected not in heads:
                raise ValueError(
                    f"the selected {side} revision is the {side} head the snapshots were "
                    "compared on; a selected revision outside the recorded heads is not a "
                    "head selection"
                )
        return self

    @model_validator(mode="after")
    def _require_the_heads_to_come_from_the_retained(self) -> ReviewRevisionSelection:
        """Refuse a head no retained population contains.

        Heads are revisions the snapshots retain, so every recorded head is a member of its
        side's retained list. A head outside it would be a revision invented beside the
        snapshots rather than read from them.
        """

        for heads, retained, side in (
            (self.before_heads, self.before_retained, "before"),
            (self.after_heads, self.after_retained, "after"),
        ):
            outsiders = sorted(set(heads) - set(retained))
            if outsiders:
                raise ValueError(
                    f"a {side} head is a retained revision of the {side} snapshot; "
                    f"{outsiders!r} is recorded as a head but not as retained"
                )
        return self
