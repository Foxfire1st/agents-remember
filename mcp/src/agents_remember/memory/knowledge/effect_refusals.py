"""The authored-effect record group's own refusal factories.

Two of the storage contract's codes carry a fact only this record group can state, and they are built
here rather than at the raise site so the code, the offending record and the remedy are declared
once:

* ``invalid_payload`` for a declared effect claim this record group cannot admit. The refusal carries
  the **declared label and the observed input and output counts** as facts -- and, where the two sides
  name the same revision, that reference -- because the remedy is the author correcting one of the
  two sides, and a message that said only "the counts are wrong" would send them nowhere. The
  predicate that decides is :func:`…models.knowledge.effect.cardinality_violation`, so the refusal and
  the construction validator cannot disagree about what is admitted. An out-of-vocabulary label is
  the same code and carries the observed label beside the nine admitted values.
* ``duplicate_identity`` for a second effect claim declaring exactly the label and the reference sets
  a stored one already declares for the same change set. Two *differently labelled* claims for one
  comparison are deliberately **not** this refusal: that is the authored disagreement the design
  requires to stay visible, so both rows are kept.

The other refusals this record group reaches are the shipped ones and are deliberately not restated
here: a reference that resolves to nothing is ``invalid_reference`` through
:func:`…endpoints`, a dataset whose recorded generation predates the succession table is
``unsupported_schema`` through :func:`…facets.generation_mismatch_refusal`'s own factory, a reused
record identity is ``stale_precondition`` through the batch's insertion check, and a succession graph
that reaches itself is ``lineage_cycle`` through the shared walk.
"""

from __future__ import annotations

from typing import Literal

from agents_remember.memory.knowledge.refusals import RefusalFacts, refusal
from agents_remember.models.knowledge.effect import ADMITTED_EFFECT_LABELS
from agents_remember.models.knowledge.result import KnowledgeOperation, KnowledgeRefusal

# The operation this record group's refusals belong to. Recording authored work is the candidate
# batch's act -- the requirement places these records on that one write path and this group has no
# standalone operation beside it -- so a refusal names the batch even before the batch restates it
# with the failing command's own position and kind. The literal spelling rather than the wide
# vocabulary is deliberate: it is assignable both to the refusal's operation and to the shared
# endpoint check's own narrower set, so neither call site needs a cast.
EFFECT_OPERATION: Literal["change_candidate"] = "change_candidate"

# The rendering of the nine admitted values a label refusal carries as its ``expected`` fact, and the
# one the label check's own ``next_action`` names.
ADMITTED_LABEL_LISTING = " | ".join(ADMITTED_EFFECT_LABELS)


def effect_label_refusal(
    operation: KnowledgeOperation, *, record_id: str, observed: str
) -> KnowledgeRefusal:
    """Refuse an effect label outside the closed nine-member set, naming label and admitted values."""

    return refusal(
        "invalid_payload",
        operation,
        f"the declared effect {observed!r} is not one of the nine admitted labels",
        facts=RefusalFacts(
            table="record_revision",
            record_id=record_id,
            expected=ADMITTED_LABEL_LISTING,
            observed=observed,
        ),
        next_action=(
            f"Declare one of the nine admitted labels, exactly as spelled: {ADMITTED_LABEL_LISTING}. "
            "There is no synonym, no compound label, no free-text label and no locally added member, "
            "and no nearest match is substituted. Nothing was written."
        ),
    )


def effect_cardinality_refusal(
    operation: KnowledgeOperation,
    *,
    record_id: str,
    observed: str,
    detail: str,
    expected: str,
) -> KnowledgeRefusal:
    """Refuse a declaration this record group's one cardinality rule does not admit.

    ``observed`` is the declared label and the observed input and output counts, and it is a *fact*
    rather than part of the prose, so a caller branches on it without reading the sentence. The code
    is the shipped ``invalid_payload`` -- the same code every other inadmissible declared payload of
    this record group reaches, and the same one requirement 1.2, requirement 2.1, the Failure And
    Recovery table, Examples 5 and 6 and the Verification Evidence all name for it.
    """

    return refusal(
        "invalid_payload",
        operation,
        f"the declared effect claim is not admissible: {detail}",
        facts=RefusalFacts(
            table="record_revision",
            record_id=record_id,
            expected=expected,
            observed=observed,
        ),
        next_action=(
            "Correct the declared label or the declared references and submit the claim again; the "
            "label is never rewritten to match the cardinality and the record is never accepted to "
            "be helpful. Nothing was written."
        ),
    )


def succession_cycle_refusal(
    successor_change_set_id: str, cycle_members: tuple[str, ...]
) -> KnowledgeRefusal:
    """Refuse a change-set succession that reaches itself, naming the change sets involved.

    The code is the shipped ``lineage_cycle`` -- the same code the two predecessor graphs and the
    decision supersession edge reach -- because the fact is the same one: a lineage is a set of exact
    ancestors, so a circular one describes no order at all. The self-naming case and the longer cycle
    are one refusal from one rule, so a successor that names itself is not reported as a missing
    reference.
    """

    return refusal(
        "lineage_cycle",
        EFFECT_OPERATION,
        f"the declared change-set succession leaves the graph cyclic at change set "
        f"{successor_change_set_id}",
        facts=RefusalFacts(
            table="change_set_predecessor",
            record_id=successor_change_set_id,
            observed=", ".join(cycle_members),
        ),
        next_action=(
            "Author an acyclic predecessor set: a successor names an *earlier* change set, never "
            "itself. The transaction left no row behind."
        ),
    )


def duplicate_effect_claim_refusal(
    operation: KnowledgeOperation, *, record_id: str, observed: str, detail: str
) -> KnowledgeRefusal:
    """Refuse a second claim that declares exactly what a stored claim already declares.

    Only an identical declaration reaches this refusal: the same label *and* the same input and output
    reference sets, for the same change set. Two differently labelled claims for one comparison are
    two authored records and are both stored, which is why this refusal names the stored claim it
    duplicates rather than reporting a conflict to be resolved.
    """

    return refusal(
        "duplicate_identity",
        operation,
        f"a stored effect claim already declares this one: {detail}",
        facts=RefusalFacts(
            table="record_revision",
            record_id=record_id,
            expected="a declaration no stored claim of this change set already makes",
            observed=observed,
        ),
        next_action=(
            "Read the stored claims of this change set and author a claim that says something they "
            "do not, or record the disagreement under a different label; a duplicate is not a "
            "conflict and is not resolved by keeping one of the two. Nothing was written."
        ),
    )
