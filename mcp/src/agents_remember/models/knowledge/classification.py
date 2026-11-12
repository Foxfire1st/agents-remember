"""``authored`` or ``mechanical``: the closed provenance class, and the closed registry of rules.

``Doc13:243`` states a prohibition and names no owner: display or retrieval ordering "may use
declared priorities, registered roles, stable ordering, and explicit trigger rules. It must not
invent semantic importance from a symbol name or present a mechanical score as assessed risk."
``KS-R20@v1`` §2 is where that prohibition acquires an owning surface, and this module is the whole
of its answer. The answer is **data**, not vigilance:

* **The class set is closed at two members** and there is no third. A value a view orders or
  qualifies is either ``authored`` -- a stored record a named author wrote, carrying its author and
  its rationale -- or ``mechanical`` -- computed by a named, versioned rule from stored facts.
  There is no ``unknown``, no ``null``, no ``mixed`` and no default. A value that cannot be
  classified is not emitted with an empty class: the view reports it as an unresolved limitation
  (:class:`~agents_remember.models.knowledge.view.UnresolvedLimitation`).
* **The rule set is closed and versioned.** :data:`MECHANICAL_RULES` is the registry, and
  :func:`mechanical_rule` is the only way to obtain a rule. A candidate rule that is not in the
  registry raises rather than producing a classification, so no inline comparator and no ad-hoc
  heuristic can acquire a class by being spelled like one. Adding a rule is a deliberate edit to
  this tuple, reviewed like any other change -- not a side effect of writing a new renderer.
* **The two determinations never merge.** :class:`Provenance` refuses a value that carries both an
  author and a rule, refuses one that carries neither, and refuses an authored value whose stated
  reason is a mechanical rule. That is requirement 2.3's "the mechanical determination never writes
  an authored record, and an authored determination never cites a mechanical rule as its reason",
  made unrepresentable rather than documented.

**What this module deliberately does not have.** No ordering comparator, no sort key, no score, no
rank, no weight, no severity, no percentage, and no field a caller could read as an assessment.
Ordering is performed by the view layer against a rule named here; the rule *identifies* the inputs,
it does not carry a preference between two records. In particular nothing here derives an ordering
key from a symbol name's spelling, a path prefix, a directory depth, a file extension or a
repository location, and nothing here reads another view's result.

**Derived, never canonical.** Recomputing a classification writes nothing: there is no store, no
cache and no persistence in this module, so a change to a rule's version cannot alter a stored
record, a dataset digest or an authored value.
"""

from __future__ import annotations

from typing import Literal, get_args

from pydantic import Field, model_validator

from agents_remember.models.knowledge.base import (
    LABEL_MAX_LENGTH,
    PROSE_MAX_LENGTH,
    REFERENCE_MAX_LENGTH,
    KnowledgeModel,
)
from agents_remember.models.knowledge.graph import RealizationRole

__all__ = [
    "AUTHORED_CLASS",
    "CLASSIFICATION_CLASSES",
    "MECHANICAL_CLASS",
    "MECHANICAL_RULES",
    "MECHANICAL_RULE_REGISTRY_VERSION",
    "ORDERING_INPUTS",
    "REGISTERED_ROLE_ORDER",
    "AuthoredDetermination",
    "MechanicalRule",
    "MechanicalRuleNotRegistered",
    "OrderingInput",
    "Provenance",
    "ProvenanceClass",
    "mechanical_rule",
    "mechanical_rules_for",
    "ordered_input_of",
]

# The closed two-member set. ``CLASSIFICATION_CLASSES`` is derived from the literal rather than
# restated, so a third member cannot be added to one and forgotten in the other.
ProvenanceClass = Literal["authored", "mechanical"]
AUTHORED_CLASS: ProvenanceClass = "authored"
MECHANICAL_CLASS: ProvenanceClass = "mechanical"
CLASSIFICATION_CLASSES: tuple[str, ...] = get_args(ProvenanceClass)

# The four admitted ordering inputs, quoted from ``Doc13:243``. An ordering that cannot name which
# of these four produced it is not admitted at all -- there is no fifth value and no "other".
OrderingInput = Literal[
    "declared_priority",
    "registered_role",
    "stable_ordering",
    "explicit_trigger_rule",
]
ORDERING_INPUTS: tuple[str, ...] = get_args(OrderingInput)

# What a mechanical rule is allowed to determine. A rule that determines an ordering returns a
# position for a row; a rule that determines a consequence returns a no-consequence statement. Both
# are closed members: a rule cannot determine an effect label, an assessment or a severity.
DeterminationKind = Literal["ordering", "no_consequence"]

# The registry's own version. It moves when the tuple changes, and it is recorded beside every
# mechanical classification so a reader can tell which registry produced it.
MECHANICAL_RULE_REGISTRY_VERSION = 1

# The declared order of the registered realization-role vocabulary, read from the vocabulary's own
# declaration rather than hand-copied, so a role added to the vocabulary cannot silently acquire a
# position here. ``registered_role`` orders *by grouping rows under their recorded role*, and this
# sequence is the grouping order -- it is a declared registry sequence, reported ``mechanical``, and
# it is not a judgment that one role matters more than another.
REGISTERED_ROLE_ORDER: tuple[str, ...] = get_args(RealizationRole)


class MechanicalRuleNotRegistered(Exception):
    """A classification was requested from a rule the registry does not carry.

    This is deliberately an exception and not a refusal value: a caller that names an unregistered
    rule has a programming defect, and returning an empty or default classification would be exactly
    the "emit the row as if it were classified" failure requirement 2.1 forbids.
    """


class MechanicalRule(KnowledgeModel):
    """One registered mechanical rule: its identity, its version and the input it is admitted on.

    ``rule_id`` is an identity, not a description, and ``rule_version`` moves when the rule's
    behaviour moves. A classification that named only one of the two could not be reproduced after
    the rule changed, which is why both travel together and why :func:`mechanical_rule` requires
    both.
    """

    rule_id: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    rule_version: int = Field(ge=1)
    determination: DeterminationKind
    # ``ordering_input`` is set exactly for an ordering rule. It is the value a view reports as the
    # ordering key's *input*, and requirement 2.5 admits only these four.
    ordering_input: OrderingInput | None = None
    # The stored or registered values the rule reads. Naming them is what keeps the rule mechanical:
    # a rule that read a symbol name's spelling could not name a stored value here.
    reads: tuple[str, ...] = ()
    summary: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)

    @model_validator(mode="after")
    def _require_an_admitted_ordering_input(self) -> MechanicalRule:
        """An ordering rule names one of the four inputs; a consequence rule names none.

        Requirement 2.5 is a closure, not a preference: "An ordering that cannot name which of the
        four produced it is not admitted." Enforcing it at the registry's own boundary means an
        unadmitted ordering input cannot be registered at all, which is stronger than refusing it
        later at a call site that might be missed.
        """

        if self.determination == "ordering" and self.ordering_input is None:
            raise ValueError(
                "an ordering rule must name one of the four admitted ordering inputs; an "
                "ordering that cannot name its input is not admitted"
            )
        if self.determination == "no_consequence" and self.ordering_input is not None:
            raise ValueError("a no-consequence rule does not order anything and names no input")
        if not self.reads:
            raise ValueError(
                "a mechanical rule reads at least one stored or registered value; a rule that "
                "reads nothing is a constant, and a constant is not a determination"
            )
        return self


# ---------------------------------------------------------------------------
# The registry.
#
# Four ordering rules -- exactly one per admitted ordering input -- and four no-consequence rules.
# Every rule reads recorded values; none reads a name, a path, a depth, an extension or another
# view's result. The registry is a tuple so its order is a property of this declaration and not of a
# mapping's iteration.
# ---------------------------------------------------------------------------

MECHANICAL_RULES: tuple[MechanicalRule, ...] = (
    MechanicalRule(
        rule_id="ordering.declared-priority",
        rule_version=1,
        determination="ordering",
        ordering_input="declared_priority",
        reads=(
            "authored_priority.position",
            "authored_priority.author",
            "authored_priority.rationale",
        ),
        summary=(
            "Order rows by the position a stored authored priority record declares for each "
            "subject. The position is an authored value and is reported as such; this rule only "
            "says which recorded value orders the rows."
        ),
    ),
    MechanicalRule(
        rule_id="ordering.registered-role",
        rule_version=1,
        determination="ordering",
        ordering_input="registered_role",
        reads=("realization_claim.role", "REGISTERED_ROLE_ORDER"),
        summary=(
            "Group rows under the realization role each row records, in the declared order of the "
            "registered-role vocabulary itself. The role is a recorded vocabulary value; it is "
            "never parsed out of a document and never inferred from a path."
        ),
    ),
    MechanicalRule(
        rule_id="ordering.declared-tiebreak",
        rule_version=1,
        determination="ordering",
        ordering_input="stable_ordering",
        reads=("row.subject_record_id",),
        summary=(
            "The declared, versioned stable ordering applied when every declared key ties: record "
            "identity ascending. This is the only lexical tiebreak this substrate admits, it "
            "applies only after every declared key has tied, and it is reported mechanical."
        ),
    ),
    MechanicalRule(
        rule_id="ordering.trigger-rule",
        rule_version=1,
        determination="ordering",
        ordering_input="explicit_trigger_rule",
        reads=("detection_condition.condition_code", "detection_condition.matched_facts"),
        summary=(
            "Order rows by the registered trigger rule that produced them: the recorded detection "
            "condition code, then the order the condition's own matched facts were recorded in. "
            "The condition is a registered rule with an identity, not a computed heuristic."
        ),
    ),
    MechanicalRule(
        rule_id="consequence.anchor-byte-equal",
        rule_version=1,
        determination="no_consequence",
        reads=("anchor_resolution.before_blob_identity", "anchor_resolution.after_blob_identity"),
        summary=(
            "A recorded source location whose blob identity is equal in the two exact compared "
            "states has no content consequence at that location. The comparison is over recorded "
            "object identities, not over symbol names or file extensions."
        ),
    ),
    MechanicalRule(
        rule_id="consequence.anchor-text-whitespace-only",
        rule_version=1,
        determination="no_consequence",
        reads=("anchor_resolution.before_text", "anchor_resolution.after_text"),
        summary=(
            "Two exact states whose recorded text at one anchor differs by whitespace only have no "
            "content consequence. The rule compares the recorded text; it does not judge whether "
            "the change matters."
        ),
    ),
    MechanicalRule(
        rule_id="consequence.record-payload-byte-equal",
        rule_version=1,
        determination="no_consequence",
        reads=("record_revision.payload_digest",),
        summary=(
            "A record whose canonical payload digest is equal in the two exact compared states is "
            "unchanged in content. The digest is the record's own recorded digest, and the rule "
            "asserts equality rather than interpreting what changed."
        ),
    ),
    MechanicalRule(
        rule_id="consequence.path-absent-in-both-states",
        rule_version=1,
        determination="no_consequence",
        reads=("anchor_resolution.state",),
        summary=(
            "A recorded path that resolves absent in both exact compared states presents no change "
            "at that path. Absence in one state and presence in the other is not this rule, and "
            "the rule never reports absence as a consequence-free *presence*."
        ),
    ),
)

_MECHANICAL_RULES_BY_IDENTITY: dict[tuple[str, int], MechanicalRule] = {
    (rule.rule_id, rule.rule_version): rule for rule in MECHANICAL_RULES
}


def mechanical_rule(rule_id: str, rule_version: int) -> MechanicalRule:
    """The one registered rule with this identity and version, or raise.

    Both halves of the identity are required. Accepting a rule id alone would let a classification
    silently follow a rule whose behaviour had moved, which is the drift requirement 2.2's version
    exists to prevent.
    """

    rule = _MECHANICAL_RULES_BY_IDENTITY.get((rule_id, rule_version))
    if rule is None:
        raise MechanicalRuleNotRegistered(
            f"{rule_id!r} version {rule_version!r} is not a registered mechanical rule; the "
            f"registry carries {sorted(_MECHANICAL_RULES_BY_IDENTITY)}"
        )
    return rule


def mechanical_rules_for(determination: DeterminationKind) -> tuple[MechanicalRule, ...]:
    """Every registered rule that makes this determination, in registry order."""

    return tuple(rule for rule in MECHANICAL_RULES if rule.determination == determination)


def ordered_input_of(rule_id: str, rule_version: int) -> OrderingInput:
    """The admitted ordering input the named rule orders on, or raise if it orders nothing."""

    rule = mechanical_rule(rule_id, rule_version)
    if rule.ordering_input is None:
        raise MechanicalRuleNotRegistered(
            f"{rule_id!r} version {rule_version!r} determines {rule.determination!r} and names no "
            "ordering input"
        )
    return rule.ordering_input


class AuthoredDetermination(KnowledgeModel):
    """An authored reason, as the author wrote it: who decided, and on what grounds.

    ``author_ref`` names the actor the substrate recorded. It is deliberately a reference and not
    free text: an authored determination is attributable or it is not authored. ``rationale`` is the
    author's own words, copied and never composed by a renderer.
    """

    author_ref: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    rationale: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)


class Provenance(KnowledgeModel):
    """The provenance class of exactly one value, as a sibling field of that value.

    Requirement 2.6 is the whole reason this is a value beside the fact rather than a mode on the
    payload: a consumer can tell, for every ordered row and every no-consequence statement, whether
    it is reading an authored finding or a mechanical observation, without knowing which renderer
    produced the payload and without consulting a view-level flag.

    The validator is the enforcement of requirement 2.3's two prohibitions:

    * an **authored** value carries an author and a rationale and names **no** rule -- so an authored
      determination cannot cite a mechanical rule as its reason;
    * a **mechanical** value names a **registered** rule and its version and carries **no** author --
      so the mechanical determination cannot present itself as, or write, an authored record.

    A value that could not be classified never reaches this model at all: the view reports it as an
    unresolved limitation, because constructing a ``Provenance`` requires one of the two members and
    there is no constructor for "empty".
    """

    provenance_class: ProvenanceClass
    authored: AuthoredDetermination | None = None
    rule_id: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)
    rule_version: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _require_exactly_one_branch(self) -> Provenance:
        named_rule = self.rule_id is not None or self.rule_version is not None
        if self.provenance_class == AUTHORED_CLASS:
            if self.authored is None:
                raise ValueError(
                    "an authored classification carries the author and the rationale that make it "
                    "authored; a class without them is an unclassified value wearing a class"
                )
            if named_rule:
                raise ValueError(
                    "an authored determination never cites a mechanical rule as its reason; it is "
                    "authored because a named author decided it"
                )
        else:
            if self.authored is not None:
                raise ValueError(
                    "a mechanical determination is produced by a rule and has no author; it never "
                    "writes an authored record"
                )
            if self.rule_id is None or self.rule_version is None:
                raise ValueError(
                    "a mechanical classification names the registered rule and version that "
                    "produced it; a rule not in the registry cannot produce a classification"
                )
            mechanical_rule(self.rule_id, self.rule_version)
        return self


def authored_provenance(author_ref: str, rationale: str) -> Provenance:
    """The one way to build an authored classification, so the branch cannot be half-filled."""

    return Provenance(
        provenance_class=AUTHORED_CLASS,
        authored=AuthoredDetermination(author_ref=author_ref, rationale=rationale),
    )


def mechanical_provenance(rule_id: str, rule_version: int) -> Provenance:
    """The one way to build a mechanical classification, checking the registry on the way."""

    mechanical_rule(rule_id, rule_version)
    return Provenance(provenance_class=MECHANICAL_CLASS, rule_id=rule_id, rule_version=rule_version)
