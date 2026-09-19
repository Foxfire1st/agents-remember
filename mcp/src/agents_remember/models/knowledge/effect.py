"""The authored-effect record group: what a change was *intended* to do, and who said so.

This module owns the frozen payload vocabulary of three member kinds -- an invariant effect claim, a
preservation claim and an unresolved question -- and nothing about where they are stored. Like the
requirement-revision and authored-facet groups before it, this record group adds **no** envelope, no
identity mechanism and no second revision aggregate: ``knowledge_record`` carries the kind, the
authority home, the lifecycle and the governing route, ``record_revision`` carries the payload and
its content digest, and the shapes below are what the envelope's registry resolves for those kinds.

Six properties are load-bearing and none is incidental:

* **The effect vocabulary is closed, and it is spelled once.** :data:`ADMITTED_EFFECT_LABELS` names
  the nine labels ``Doc13:98`` names, in that order, and :data:`EffectLabel` is the literal type
  built from exactly that tuple, so a synonym, a compound label, a free-text label and a tenth
  member are all the same refusal: the payload does not validate. Code never derives a label from
  anything -- a comparison that observes a condition set growing does not make the effect
  ``strengthen``, and no field here could receive such a derivation.
* **Inputs and outputs are exact revision references, never prose.** Each names one exact stored
  revision by identity, and the two sets are what the one cardinality rule in
  :func:`cardinality_violation` reads. Nothing in this vocabulary parses a reference, splits it on
  a separator, or infers an identity from it.
* **One cardinality rule, one refusal.** :func:`cardinality_violation` is the single definition of
  what a declared label may claim about counts, and it is applied at construction here and again at
  the storage boundary by the record group's own precondition. Two enforcement points, one rule:
  the storage check builds its ``invalid_payload`` refusal from *this* function rather than
  restating the predicate.
* **There is no field that is a truth verdict.** No boolean, score, confidence, verdict or
  ``verified`` field exists on a claim, and ``extra="forbid"`` is what refuses a payload arriving
  with one. Storage authority is not semantic endorsement: a stored claim is a claim whose declared
  shape was accepted.
* **A preservation claim is a separate record, and it is not an effect.** There is no ``preserve``
  member in :data:`ADMITTED_EFFECT_LABELS`, no preservation flag on an effect claim, and no field
  here on which the two could be confused.
* **The author is not a payload field.** A claim's authorship *is* the revision's ``provenance`` on
  ``record_revision``, stamped from the admission, so a payload field naming an actor, an
  authorization, an operation or a recorded time would be a second, competing statement of one
  fact -- and ``extra="forbid"`` is what refuses one that arrives.

Membership is declared by the member. Each member carries the ``change_set_id`` of the one change
set it belongs to, so "a member is never silently shared between two change sets" is a property of
the member's own immutable declaration rather than a rule two records have to agree about, and the
change set's membership is *computed* from it (``Doc13:97``'s "it does not infer the intended
change" is about meaning; which records belong to which change set is a membership fact).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Literal

from pydantic import Field, model_validator

from agents_remember.models.knowledge.base import (
    PROSE_MAX_LENGTH,
    REFERENCE_MAX_LENGTH,
    UUID_PATTERN,
    KnowledgeModel,
)

# The nine effect labels ``Doc13:98`` names, in that document's own order. The tuple is the one
# declaration; the literal type below is derived from it and a case asserts the two agree, so a
# tenth member cannot be added to the vocabulary without the schema admitting it.
ADMITTED_EFFECT_LABELS: tuple[str, ...] = (
    "restore",
    "clarify",
    "introduce",
    "strengthen",
    "weaken",
    "replace",
    "split",
    "merge",
    "retire",
)

EffectLabel = Literal[
    "restore",
    "clarify",
    "introduce",
    "strengthen",
    "weaken",
    "replace",
    "split",
    "merge",
    "retire",
]

# The two labels whose own declaration is about many-ness, and therefore the only two the one
# cardinality rule reads a count for. They are named here rather than compared inline so the rule
# below reads as the sentence requirement 1.2 states.
DIVISION_EFFECT_LABELS: frozenset[str] = frozenset({"split", "merge"})

# The reference bound the opaque-reference reading shares with every other stored reference: a
# reference is stored verbatim, so it is bounded rather than parsed.
EffectReference = Annotated[str, Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)]

# An exact stored revision identity. It is an identity and not a composite address: the shipped
# endpoint checks resolve a revision by ``(repository_id, revision_id)``, so a reference that
# carried its own kind or namespace would be a second addressing scheme beside the stored key.
RevisionReference = Annotated[str, Field(pattern=UUID_PATTERN)]


def cardinality_violation(
    effect: str, inputs: tuple[str, ...], outputs: tuple[str, ...]
) -> str | None:
    """Return the fact that makes one declared label, input set and output set inadmissible.

    This is requirement 1.2's whole predicate, stated once:

    * no revision may appear as both an input and an output of the same claim -- an effect that
      names one revision on both sides claims no change;
    * ``split`` is admitted only with two or more outputs;
    * ``merge`` is admitted only with two or more inputs;
    * every other label admits any counts.

    It returns the *fact*, as text, or ``None`` when the declaration is admissible. The
    construction validator raises it as a ``ValueError``; the storage boundary builds its typed
    ``invalid_payload`` refusal from the same text, so the two enforcement points cannot disagree
    about which declarations are admitted. It reads only the declared label and the two declared
    reference sets -- no content, no diff and no text -- which is what makes it a shape check
    rather than a judgement of the label's truth.
    """

    shared = sorted(set(inputs) & set(outputs))
    if shared:
        return (
            f"the declared input and output are the same revision {shared[0]!r}, so no change is "
            "claimed"
        )
    if effect == "split" and len(outputs) < 2:
        return f"`split` is admitted only with two or more outputs and {len(outputs)} was declared"
    if effect == "merge" and len(inputs) < 2:
        return f"`merge` is admitted only with two or more inputs and {len(inputs)} was declared"
    return None


def cardinality_rule_text(effect: str) -> str:
    """Render the admitted combinations for one declared label, as a refusal's ``expected`` fact.

    A refusal that says only "the counts are wrong" sends the author nowhere. This names what the
    declared label *does* admit, in the same words requirement 1.2 uses, so the remedy is the author
    correcting one of the two sides rather than guessing at the rule.
    """

    if effect == "split":
        return "`split` admits two or more outputs and no revision on both sides"
    if effect == "merge":
        return "`merge` admits two or more inputs and no revision on both sides"
    return f"`{effect}` admits any input and output counts provided no revision is on both sides"


class InvariantEffectClaimPayload(KnowledgeModel):
    """One authored claim about what a change was intended to do to an invariant revision.

    ``inputs`` and ``outputs`` are the explicit references requirement 1.1 requires, each an exact
    stored revision identity. ``rationale`` is the author's own account and is refused when it is
    blank after trimming, because a claim whose rationale says nothing is a label without a claim.

    ``assessment_refs`` are **named references that may remain unresolved**: the assessment record
    is another leaf's (``KS-R15@v1``), so this record group stores the reference and reports one
    that resolves to nothing as an unresolved reference -- never a refused write, never a
    filled-in substitute, and never a claim that an assessment happened. They are bounded
    reference text rather than exact identities precisely because their future shape is not this
    leaf's to fix.
    """

    effect_kind: Literal["invariant_effect_claim"] = "invariant_effect_claim"
    change_set_id: RevisionReference
    effect: EffectLabel
    inputs: tuple[RevisionReference, ...] = ()
    outputs: tuple[RevisionReference, ...] = ()
    rationale: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    assessment_refs: tuple[EffectReference, ...] = ()

    @model_validator(mode="after")
    def _require_an_admissible_declaration(self) -> InvariantEffectClaimPayload:
        """Apply requirement 1.2's one cardinality rule at construction."""

        violation = cardinality_violation(self.effect, self.inputs, self.outputs)
        if violation is not None:
            raise ValueError(violation)
        return self

    @model_validator(mode="after")
    def _require_a_rationale_that_says_something(self) -> InvariantEffectClaimPayload:
        """Refuse a rationale that is empty after trimming, not only one of length zero."""

        if not self.rationale.strip():
            raise ValueError(
                "a rationale must not be blank: the label carries no explanation of its own, so a "
                "blank rationale is a claim nobody made"
            )
        return self


# What a preservation claim's subject may be: a named knowledge reference of one exact kind. The
# kind is declared rather than inferred, so resolution is a per-kind existence lookup and never a
# guess, and a subject that names nothing is reported unresolved instead of being refused.
PreservationSubjectKind = Literal[
    "invariant",
    "invariant_revision",
    "source_anchor",
    "semantic_change_set",
]


class PreservationSubject(KnowledgeModel):
    """The named knowledge reference a preservation claim is *about* -- never an effect label.

    ``Doc13:98`` makes preservation claims separate records with their own subject, and requirement
    3.1 fixes what that subject may be: an invariant identity or revision, an anchor, or a change
    set. There is deliberately no subject kind for an effect claim, so "this revision preserves
    that claim's meaning" is not a shape this record can take -- preservation is about a knowledge
    reference, and an effect is not one.
    """

    kind: PreservationSubjectKind
    reference_id: str = Field(pattern=UUID_PATTERN)


class PreservationClaimPayload(KnowledgeModel):
    """One authored claim that a named piece of knowledge is preserved, by a named author.

    The statement is the author's own sentence and is refused when blank; the subject is the exact
    reference above; the author is the revision's ``provenance``. A subject that resolves to
    nothing is reported as an unresolved reference -- the same state an unresolved assessment
    reference has -- because "nothing is stored under this identity yet" is a fact about the
    subject, not an inadmissible declaration.
    """

    preservation_kind: Literal["preservation_claim"] = "preservation_claim"
    change_set_id: RevisionReference
    subject: PreservationSubject
    statement: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)

    @model_validator(mode="after")
    def _require_a_statement_that_says_something(self) -> PreservationClaimPayload:
        if not self.statement.strip():
            raise ValueError(
                "a preservation claim's statement must not be blank: the claim is someone's "
                "assertion, and an empty one asserts nothing"
            )
        return self


class UnresolvedQuestionPayload(KnowledgeModel):
    """One authored question a change set leaves open.

    A question is a first-class member rather than a nullable text column, and there is
    deliberately **no** field recording that it was answered: a change set does not default a
    question to answered and does not drop one in order to look finished, so the only way a
    question stops being open is for a later change set to be authored.
    """

    question_kind: Literal["unresolved_question"] = "unresolved_question"
    change_set_id: RevisionReference
    statement: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)

    @model_validator(mode="after")
    def _require_a_statement_that_says_something(self) -> UnresolvedQuestionPayload:
        if not self.statement.strip():
            raise ValueError("an unresolved question must state the question it leaves open")
        return self


# The three member kinds this record group declares, and the one frozen shape each resolves to. The
# envelope's registry unpacks this mapping rather than restating the pairs, so a member kind cannot
# exist in the vocabulary without a registered shape and the two key sets cannot drift.
INVARIANT_EFFECT_CLAIM_KIND = "invariant_effect_claim"
INVARIANT_EFFECT_CLAIM_SCHEMA = "invariant-effect-claim/v1"
PRESERVATION_CLAIM_KIND = "preservation_claim"
PRESERVATION_CLAIM_SCHEMA = "preservation-claim/v1"
UNRESOLVED_QUESTION_KIND = "unresolved_question"
UNRESOLVED_QUESTION_SCHEMA = "unresolved-question/v1"

MEMBER_KINDS: tuple[str, ...] = (
    INVARIANT_EFFECT_CLAIM_KIND,
    PRESERVATION_CLAIM_KIND,
    UNRESOLVED_QUESTION_KIND,
)

MEMBER_PAYLOAD_MODELS: Mapping[tuple[str, str], type[KnowledgeModel]] = {
    (INVARIANT_EFFECT_CLAIM_KIND, INVARIANT_EFFECT_CLAIM_SCHEMA): InvariantEffectClaimPayload,
    (PRESERVATION_CLAIM_KIND, PRESERVATION_CLAIM_SCHEMA): PreservationClaimPayload,
    (UNRESOLVED_QUESTION_KIND, UNRESOLVED_QUESTION_SCHEMA): UnresolvedQuestionPayload,
}

# The four candidate commands that write this record group, as the one vocabulary the batch's
# dispatch, its preconditions and its duplicate check all read. The change-set command is a member
# here even though its payload model lives beside the views it serves, because the *acts* this record
# group performs are one closed set and a dispatch table built from half of it would be a second
# declaration that could drift.
EFFECT_COMMAND_KINDS: tuple[str, ...] = (
    "add_invariant_effect_claim",
    "add_preservation_claim",
    "add_unresolved_question",
    "add_semantic_change_set",
)

# The canonical tables this record group's commands write. Declared here, beside the commands that
# address them, for the same reason ``EVIDENCE_WRITABLE_TABLES`` is declared beside the
# supporting-record pair: the write side of the group states its own table set once, and
# ``candidate_records`` folds it into the batch's writable union by subtracting the envelope names
# rather than by editing another group's list.
#
# The set is exactly the two envelope tables, and that is the group's shape rather than an omission.
# Each of the four commands writes one ``knowledge_record`` row and its one sealed ``record_revision``
# -- the payload *is* the registered frozen model, so the group appends no columns of its own -- and
# the succession edge a change set declares is written only as part of the aggregate that owns it, on
# the same shipped rule that keeps the invariant and family predecessor rows out of the mutable set.
# It follows that this group contributes no table to ``MutableRecordTable``, and the facet case asserts
# the derived effect-only remainder is empty so that absence is a checked fact.
EFFECT_WRITABLE_TABLES: tuple[str, ...] = (
    "knowledge_record",
    "record_revision",
)
