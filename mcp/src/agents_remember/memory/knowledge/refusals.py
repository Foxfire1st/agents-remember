"""Typed refusal vocabulary, the exceptions that carry it, and the open-time SQLite mapping.

A refusal is a returned value, not an exception: callers branch on a code instead of parsing a
message. The exceptions here exist for the cases a return value cannot express -- a storage
failure the operation cannot classify, and a refusal raised from inside a transaction that must
be rolled back before it can be returned.

Every refusal in this package is built by one of the factories here, so the codes, the offending
record and the advertised next action stay in one place instead of being spelled out per call
site.
"""

from __future__ import annotations

from dataclasses import dataclass

import apsw

from agents_remember.errors import AgentsRememberError
from agents_remember.models.knowledge.result import (
    KnowledgeOperation,
    KnowledgeRefusal,
    KnowledgeRefusalCode,
)


class KnowledgeStorageError(AgentsRememberError):
    """A storage failure that no contract refusal code describes.

    It is a defect report, not an expected outcome: a reachable expected failure returns a
    :class:`KnowledgeRefusal` with one of the contract codes.
    """


class KnowledgeRefused(AgentsRememberError):
    """Internal control flow carrying a typed refusal out of a transaction.

    The operation rolls the transaction back and converts this into a returned refusal, so a
    caller never sees the exception and can never observe a partially applied batch.
    """

    def __init__(self, refusal_value: KnowledgeRefusal) -> None:
        super().__init__(f"{refusal_value.code}: {refusal_value.detail}")
        self.refusal = refusal_value


@dataclass(frozen=True)
class RefusalFacts:
    """The optional identifying facts a refusal can carry about the offending record."""

    table: str | None = None
    record_id: str | None = None
    expected: str | None = None
    observed: str | None = None


def refusal(
    code: KnowledgeRefusalCode,
    operation: KnowledgeOperation,
    detail: str,
    *,
    next_action: str,
    facts: RefusalFacts | None = None,
) -> KnowledgeRefusal:
    """Build one refusal with the exact code, offending record and next action."""

    resolved = facts or RefusalFacts()
    return KnowledgeRefusal(
        code=code,
        operation=operation,
        detail=detail,
        table=resolved.table,
        record_id=resolved.record_id,
        expected=resolved.expected,
        observed=resolved.observed,
        next_action=next_action,
    )


def scope_refusal(
    operation: KnowledgeOperation, bound_repository_id: str, requested_repository_id: str
) -> KnowledgeRefusal | None:
    """Refuse a request addressed to a namespace other than the bound one."""

    if requested_repository_id == bound_repository_id:
        return None
    return refusal(
        "unauthorized_scope",
        operation,
        "the request names a repository namespace the destination is not bound to",
        facts=RefusalFacts(
            table="repository",
            record_id=requested_repository_id,
            expected=bound_repository_id,
            observed=requested_repository_id,
        ),
        next_action=(
            "Address the bound namespace, or open the destination through the admission that "
            "carries the intended one."
        ),
    )


def invalid_payload_refusal(invariant_id: str, detail: str) -> KnowledgeRefusal:
    """Refuse one authored draft that is not a well-formed revision aggregate."""

    return refusal(
        "invalid_payload",
        "create_invariant_revision",
        f"the revision payload is not a valid authored aggregate: {detail}",
        facts=RefusalFacts(table="invariant_revision", record_id=invariant_id),
        next_action=(
            f"Correct the explicit payload for invariant {invariant_id} and submit it again."
        ),
    )


def duplicate_revision_refusal(
    revision_id: str, expected_digest: str, observed_digest: str
) -> KnowledgeRefusal:
    """Refuse a reused revision identity whose payload seals different content."""

    return refusal(
        "duplicate_identity",
        "create_invariant_revision",
        "the revision identity already exists with a different sealed payload",
        facts=RefusalFacts(
            table="invariant_revision",
            record_id=revision_id,
            expected=expected_digest,
            observed=observed_digest,
        ),
        next_action=(
            "Keep the stored revision intact and author a successor with its own identity and "
            "this revision as an exact predecessor."
        ),
    )


def duplicate_invariant_refusal(
    invariant_id: str, expected_label: str, observed_label: str
) -> KnowledgeRefusal:
    """Refuse a reused invariant identity carrying a different display label."""

    return refusal(
        "duplicate_identity",
        "create_invariant",
        "the invariant identity already exists with a different display label",
        facts=RefusalFacts(
            table="invariant",
            record_id=invariant_id,
            expected=expected_label,
            observed=observed_label,
        ),
        next_action=(
            "Reuse the stored invariant identity and author a new revision, or edit the label "
            "with its expected row digest."
        ),
    )


def repository_rebind_refusal(
    repository_id: str, expected_home: str, observed_home: str
) -> KnowledgeRefusal:
    """Refuse rebinding a populated store to another namespace or authority home."""

    return refusal(
        "unauthorized_scope",
        "create_repository",
        "the store already carries a different authority home for this namespace",
        facts=RefusalFacts(
            table="repository",
            record_id=repository_id,
            expected=expected_home,
            observed=observed_home,
        ),
        next_action=(
            "Use the stored authority home, or initialize a separate candidate database for the "
            "new one."
        ),
    )


def unknown_invariant_refusal(invariant_id: str) -> KnowledgeRefusal:
    """Refuse a revision whose invariant identity is not in the bound namespace."""

    return refusal(
        "unknown_invariant",
        "create_invariant_revision",
        "the revision's invariant identity is not in this namespace",
        facts=RefusalFacts(table="invariant", record_id=invariant_id),
        next_action=(
            "Create the invariant identity in this repository namespace first, then author its "
            "revision."
        ),
    )


def dangling_predecessor_refusal(predecessor_id: str, invariant_id: str) -> KnowledgeRefusal:
    """Refuse a declared predecessor that no revision in the namespace carries."""

    return refusal(
        "invalid_reference",
        "create_invariant_revision",
        "a declared predecessor does not exist in this namespace",
        facts=RefusalFacts(
            table="invariant_predecessor", record_id=predecessor_id, expected=invariant_id
        ),
        next_action=(
            "Declare only existing revision identities of the same invariant, or author the "
            "predecessor in the same committed aggregate."
        ),
    )


def cross_invariant_predecessor_refusal(
    predecessor_id: str, expected_invariant_id: str, observed_invariant_id: str
) -> KnowledgeRefusal:
    """Refuse a predecessor that belongs to a different invariant."""

    return refusal(
        "invalid_reference",
        "create_invariant_revision",
        "a declared predecessor belongs to a different invariant",
        facts=RefusalFacts(
            table="invariant_predecessor",
            record_id=predecessor_id,
            expected=expected_invariant_id,
            observed=observed_invariant_id,
        ),
        next_action=(
            "Predecessors must stay within one invariant; a cross-invariant relationship belongs "
            "to a family revision, not to lineage."
        ),
    )


def lineage_cycle_refusal(
    revision_id: str, cycle_members: tuple[str, ...], *, candidate_on_cycle: bool
) -> KnowledgeRefusal:
    """Refuse when the candidate's lineage would leave it on, or below, a cycle.

    The rule has two branches and the message names which one applied, because the remedy differs:
    a candidate that would join a cycle must be re-authored, while a candidate below an already
    stored cycle is refused because the ancestor cycle has to be resolved first. Claiming
    self-reachability in the second branch would be false -- nothing points at that candidate.
    """

    detail, next_action = _lineage_cycle_wording(
        candidate_on_cycle=candidate_on_cycle, revision_id=revision_id, relation="invariant"
    )
    return refusal(
        "lineage_cycle",
        "create_invariant_revision",
        detail,
        facts=RefusalFacts(
            table="invariant_predecessor",
            record_id=revision_id,
            observed=", ".join(cycle_members),
        ),
        next_action=next_action,
    )


def family_lineage_cycle_refusal(
    revision_id: str, cycle_members: tuple[str, ...], *, candidate_on_cycle: bool
) -> KnowledgeRefusal:
    """Refuse the family-lineage form of the same two-branch rule.

    The wording is shared with the invariant lineage so the two relations cannot drift into
    describing different rules; only the object noun, the operation and the edge table differ.
    """

    detail, next_action = _lineage_cycle_wording(
        candidate_on_cycle=candidate_on_cycle, revision_id=revision_id, relation="family"
    )
    return refusal(
        "lineage_cycle",
        "create_family_revision",
        detail,
        facts=RefusalFacts(
            table="family_predecessor",
            record_id=revision_id,
            observed=", ".join(cycle_members),
        ),
        next_action=next_action,
    )


def _lineage_cycle_wording(
    *, candidate_on_cycle: bool, revision_id: str, relation: str
) -> tuple[str, str]:
    """Return the detail and next action for one branch of the lineage-cycle rule."""

    if candidate_on_cycle:
        return (
            "the declared predecessors would put this revision on a lineage cycle: it would be "
            "reachable from itself",
            "Author a successor whose predecessor set is acyclic, and resolve the cycle at "
            f"revision {revision_id} itself; a convergent lineage is authored as one acyclic set.",
        )
    return (
        "the declared predecessors descend from a revision that is already on a lineage cycle, "
        f"so inserting this revision would leave the {relation}'s lineage cyclic",
        "Author a successor whose predecessor set is acyclic, and resolve the ancestor cycle "
        "reported in observed; this revision is not itself on that cycle.",
    )


def unknown_family_refusal(family_id: str) -> KnowledgeRefusal:
    """Refuse a family revision whose family identity is not in the bound namespace."""

    return refusal(
        "unknown_family",
        "create_family_revision",
        "the revision's family identity is not in this namespace",
        facts=RefusalFacts(table="family", record_id=family_id),
        next_action=(
            "Create the family identity in this repository namespace first, then author its "
            "revision."
        ),
    )


def invalid_family_payload_refusal(family_id: str, detail: str) -> KnowledgeRefusal:
    """Refuse one authored family draft that is not a well-formed revision aggregate."""

    return refusal(
        "invalid_payload",
        "create_family_revision",
        f"the family revision payload is not a valid authored aggregate: {detail}",
        facts=RefusalFacts(table="family_revision", record_id=family_id),
        next_action=(f"Correct the explicit payload for family {family_id} and submit it again."),
    )


def duplicate_family_refusal(
    family_id: str, expected_label: str, observed_label: str
) -> KnowledgeRefusal:
    """Refuse a reused family identity carrying a different display label."""

    return refusal(
        "duplicate_identity",
        "create_family",
        "the family identity already exists with a different display label",
        facts=RefusalFacts(
            table="family",
            record_id=family_id,
            expected=expected_label,
            observed=observed_label,
        ),
        next_action=(
            "Reuse the stored family identity and author a new revision, or edit the label with "
            "its expected row digest."
        ),
    )


def duplicate_family_revision_refusal(
    revision_id: str, expected_digest: str, observed_digest: str
) -> KnowledgeRefusal:
    """Refuse a reused family revision identity whose payload seals different content."""

    return refusal(
        "duplicate_identity",
        "create_family_revision",
        "the family revision identity already exists with a different sealed payload",
        facts=RefusalFacts(
            table="family_revision",
            record_id=revision_id,
            expected=expected_digest,
            observed=observed_digest,
        ),
        next_action=(
            "Keep the stored family revision intact and author a successor with its own identity "
            "and this revision as an exact predecessor."
        ),
    )


def dangling_family_predecessor_refusal(predecessor_id: str, family_id: str) -> KnowledgeRefusal:
    """Refuse a declared family predecessor that no revision in the namespace carries."""

    return refusal(
        "invalid_reference",
        "create_family_revision",
        "a declared predecessor does not exist in this namespace",
        facts=RefusalFacts(
            table="family_predecessor", record_id=predecessor_id, expected=family_id
        ),
        next_action=(
            "Declare only existing family revision identities of the same family, or author the "
            "predecessor in the same committed aggregate."
        ),
    )


def cross_family_predecessor_refusal(
    predecessor_id: str, expected_family_id: str, observed_family_id: str
) -> KnowledgeRefusal:
    """Refuse a family predecessor that belongs to a different family."""

    return refusal(
        "invalid_reference",
        "create_family_revision",
        "a declared predecessor belongs to a different family",
        facts=RefusalFacts(
            table="family_predecessor",
            record_id=predecessor_id,
            expected=expected_family_id,
            observed=observed_family_id,
        ),
        next_action=(
            "Predecessors must stay within one family; a relationship across families is authored "
            "as its own family revision, not as lineage."
        ),
    )


def duplicate_anchor_refusal(
    anchor_id: str, expected_digest: str, observed_digest: str
) -> KnowledgeRefusal:
    """Refuse a reused anchor identity whose stored payload differs."""

    return refusal(
        "duplicate_identity",
        "create_source_anchor",
        "the anchor identity already exists with a different stored payload",
        facts=RefusalFacts(
            table="source_anchor",
            record_id=anchor_id,
            expected=expected_digest,
            observed=observed_digest,
        ),
        next_action=(
            "Keep the stored anchor intact and record a new anchor identity for a different "
            "location or source object."
        ),
    )


def missing_relation_endpoint_refusal(
    *,
    operation: KnowledgeOperation,
    table: str,
    relation_id: str,
    endpoint_id: str,
    endpoint_kind: str,
) -> KnowledgeRefusal:
    """Refuse a relation whose declared endpoint is not stored in this namespace.

    The refusal names both the relation and the missing endpoint, because the remedy is to
    author the endpoint (or correct the identity) rather than to retry the relation.
    """

    return refusal(
        "invalid_reference",
        operation,
        f"the relation names a {endpoint_kind} that is not in this namespace",
        facts=RefusalFacts(
            table=table, record_id=endpoint_id, expected=endpoint_kind, observed=relation_id
        ),
        next_action=(
            f"Record the {endpoint_kind} in this repository namespace first, or correct its "
            "identity; the transaction left no relation row behind."
        ),
    )


def duplicate_relation_identity_refusal(
    *,
    operation: KnowledgeOperation,
    table: str,
    record_id: str,
    expected: str,
    observed: str,
) -> KnowledgeRefusal:
    """Refuse a reused relation identity whose authored payload differs from the stored row."""

    return refusal(
        "duplicate_identity",
        operation,
        "the relation identity already exists with a different authored payload",
        facts=RefusalFacts(table=table, record_id=record_id, expected=expected, observed=observed),
        next_action=(
            "Keep the stored relation intact and author a new relation identity, or remove the "
            "stored relation explicitly with its expected row digest."
        ),
    )


def duplicate_relationship_refusal(
    *,
    operation: KnowledgeOperation,
    table: str,
    record_id: str,
    existing_id: str,
    detail: str,
) -> KnowledgeRefusal:
    """Refuse a second relation row for an endpoint pair that is already related once."""

    return refusal(
        "relationship_constraint",
        operation,
        detail,
        facts=RefusalFacts(table=table, record_id=record_id, observed=existing_id),
        next_action=(
            "Reuse the stored relation identity, or author a relation over a different endpoint "
            "pair; the same two endpoints are related exactly once."
        ),
    )


def missing_expected_row_refusal(
    *, operation: KnowledgeOperation, table: str, record_id: str
) -> KnowledgeRefusal:
    """Refuse an explicit removal whose named row is not stored."""

    return refusal(
        "missing_expected_row",
        operation,
        "the row this request names is not stored in this namespace",
        facts=RefusalFacts(table=table, record_id=record_id),
        next_action=(
            "Read the current rows and name an existing identity; a removal never falls back to "
            "the nearest or most recent row."
        ),
    )


def stale_expected_row_refusal(
    *,
    operation: KnowledgeOperation,
    table: str,
    record_id: str,
    expected: str,
    observed: str,
) -> KnowledgeRefusal:
    """Refuse an explicit removal whose named row is not the row the caller expected."""

    return refusal(
        "stale_precondition",
        operation,
        "the stored row differs from the identity the removal was authored against",
        facts=RefusalFacts(table=table, record_id=record_id, expected=expected, observed=observed),
        next_action=(
            "Reread the row and author the removal against its current digest; the stored row was "
            "left untouched."
        ),
    )


def referenced_anchor_refusal(anchor_id: str, referencing_claim: str) -> KnowledgeRefusal:
    """Refuse removing an anchor that a stored realization claim still cites."""

    return refusal(
        "relationship_constraint",
        "remove_source_anchor",
        "the anchor is still cited by a stored realization claim",
        facts=RefusalFacts(table="source_anchor", record_id=anchor_id, observed=referencing_claim),
        next_action=(
            "Remove the citing realization claim first, or keep the anchor; an anchor is never "
            "removed because its source disappeared."
        ),
    )


def lock_capability_refusal(operation: KnowledgeOperation, detail: str) -> KnowledgeRefusal:
    """Refuse when the resource lock cannot exclude on this filesystem."""

    return refusal(
        "lock_capability_unavailable",
        operation,
        detail,
        next_action=(
            "Move the candidate database and its lock to a local POSIX filesystem where flock "
            "excludes, then retry the same expected state."
        ),
    )


def candidate_busy_refusal(operation: KnowledgeOperation, detail: str) -> KnowledgeRefusal:
    """Refuse when another writer holds the candidate database."""

    return refusal(
        "candidate_busy",
        operation,
        f"the candidate database is locked by another writer: {detail}",
        next_action=(
            "Resolve the contention and retry the same expected state; do not rebase the batch "
            "onto a fresher snapshot."
        ),
    )


@dataclass(frozen=True)
class SqliteFailureContext:
    """Which operation and table a surviving SQLite failure belongs to.

    The mapping needs this because a constraint failure carries no operation identity of its own,
    and a refusal that names the wrong table or operation sends the caller to the wrong row.
    """

    operation: KnowledgeOperation
    table: str
    record_id: str


def map_sqlite_error(error: apsw.Error, context: SqliteFailureContext) -> KnowledgeRefusal:
    """Translate a surviving SQLite constraint failure into a typed refusal.

    The store's own preconditions classify the expected refusals before any DML; a failure
    reaching here is one the database caught that those preconditions did not name.
    """

    message = str(error)
    lowered = message.lower()
    facts = RefusalFacts(table=context.table, record_id=context.record_id)
    if "immutable_revision" in lowered:
        return refusal(
            "immutable_revision",
            context.operation,
            message,
            facts=facts,
            next_action=(
                "Create a successor revision with exact predecessor identities; an existing "
                "revision is never rewritten in place."
            ),
        )
    if "foreign key" in lowered:
        return refusal(
            "invalid_reference",
            context.operation,
            message,
            facts=facts,
            next_action="Name existing endpoints of the same object, then resubmit the aggregate.",
        )
    if "constraint" in lowered:
        return refusal(
            "relationship_constraint",
            context.operation,
            message,
            facts=facts,
            next_action="Correct the offending relationship and resubmit the whole aggregate.",
        )
    return refusal(
        "invalid_payload",
        context.operation,
        message,
        facts=facts,
        next_action="Correct the payload and resubmit; the transaction left no row behind.",
    )
