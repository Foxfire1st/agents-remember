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


# -- the candidate-change batch boundary ------------------------------------------------
#
# The batch composes the single-record operations, so its refusals are built here rather than by
# the per-record factories: a caller that submitted one batch needs to know which command in it
# failed, and the code has to name the batch as the operation it addressed. The per-command
# refusals keep their own codes and remedies -- they are relabelled onto the batch operation, not
# rewritten -- so a batch failure reads exactly like the single-record failure it is.


def batch_target_not_candidate_refusal(lane: str, repository_id: str) -> KnowledgeRefusal:
    """Refuse a batch addressed to a lane that is not a writable candidate."""

    return refusal(
        "target_not_candidate",
        "change_candidate",
        f"the addressed lane {lane!r} is not a candidate this operation may write",
        facts=RefusalFacts(table="repository", record_id=repository_id, observed=lane),
        next_action=(
            "Address the explicitly admitted draft or task candidate. A historical or accepted "
            "snapshot is read-only here, and this operation promotes nothing."
        ),
    )


def batch_task_binding_unresolved_refusal(
    task_ref: str | None, repository_id: str
) -> KnowledgeRefusal:
    """Refuse a task-candidate batch whose task binding this operation cannot resolve.

    The packet requires a task-bound candidate to use its existing contract authority, and that
    authority is resolved by the application that owns the task contract -- not here. Accepting the
    lane on the strength of a caller-supplied reference would be the fail-open the requirement
    forbids, so the lane is refused until a resolved binding is available to require and check. The
    refusal names what is missing rather than degrading to the draft lane.
    """

    return refusal(
        "unauthorized_scope",
        "change_candidate",
        "the addressed lane is a task candidate, and this operation holds no resolved task binding "
        "it could validate",
        facts=RefusalFacts(
            table="repository",
            record_id=repository_id,
            expected="a resolved, owner-validated task binding",
            observed=task_ref if task_ref is not None else "<no task reference supplied>",
        ),
        next_action=(
            "Resolve the candidate's task binding through its existing contract owner and submit "
            "the batch through that admission; a draft candidate that asserts no task authority "
            "uses the draft lane instead."
        ),
    )


def batch_promotion_not_supported_refusal(record_id: str) -> KnowledgeRefusal:
    """Refuse a command that would store accepted origin data or promote a proposal."""

    return refusal(
        "promotion_not_supported",
        "change_candidate",
        "the batch carries accepted origin data, which this candidate-only operation never stores",
        facts=RefusalFacts(table="invariant_revision", record_id=record_id),
        next_action=(
            "Author the record as proposed. Acceptance is decided by the owner of that process, "
            "not by a candidate change batch."
        ),
    )


def batch_absent_target_refusal(*, table: str, record_id: str) -> KnowledgeRefusal:
    """Refuse a batch that expects no record but names an identity that is already stored."""

    return refusal(
        "duplicate_identity",
        "change_candidate",
        "the batch expects this identity to be absent, but a record already carries it",
        facts=RefusalFacts(table=table, record_id=record_id),
        next_action=(
            "Keep the stored record intact and author a separately identified record, or reread "
            "the current rows and state the expectation that actually holds."
        ),
    )


def batch_duplicate_expectation_refusal(*, table: str, record_id: str) -> KnowledgeRefusal:
    """Refuse a command whose target identity is stated by two commands in the same batch."""

    return refusal(
        "duplicate_identity",
        "change_candidate",
        "two commands in this batch address the same record identity",
        facts=RefusalFacts(table=table, record_id=record_id),
        next_action=(
            "Give each command its own identity. A batch is one authored act, so an identity "
            "authored twice in it is a contradiction rather than a sequence."
        ),
    )


def batch_stale_record_refusal(
    *, table: str, record_id: str, expected: str, observed: str
) -> KnowledgeRefusal:
    """Refuse a batch whose expected record state is not the state that is stored."""

    return refusal(
        "stale_precondition",
        "change_candidate",
        "a stored record differs from the state the batch was authored against",
        facts=RefusalFacts(table=table, record_id=record_id, expected=expected, observed=observed),
        next_action=(
            "Reread the exact record identities and author a new explicit batch; the stored state "
            "was left untouched."
        ),
    )


def batch_context_refusal(*, expected: str, observed: str) -> KnowledgeRefusal:
    """Refuse a batch whose expected dataset identity is not the one the candidate holds."""

    return refusal(
        "stale_precondition",
        "change_candidate",
        "the candidate's logical dataset identity differs from the context the batch names",
        facts=RefusalFacts(table="repository", expected=expected, observed=observed),
        next_action=(
            "Reread the candidate's identities and author a new batch against them; the batch is "
            "never silently rebased onto the state that arrived meanwhile."
        ),
    )


def batch_context_digest_refusal(expected: str, observed: str) -> KnowledgeRefusal:
    """Refuse a context whose sealed digest does not match the context presented."""

    return refusal(
        "stale_precondition",
        "change_candidate",
        "the context digest does not seal the context the batch carries",
        facts=RefusalFacts(table="repository", expected=expected, observed=observed),
        next_action=(
            "Reresolve the context from the admission that owns it and submit the batch against "
            "that resolution."
        ),
    )


def batch_refusal(refused: KnowledgeRefusal, *, index: int, command: str) -> KnowledgeRefusal:
    """Return one command's refusal restated as a refusal of the batch that carried it.

    The code, the offending record and the remedy are preserved; only the operation becomes the
    batch, and the position and kind of the failing command are added, because that is what the
    caller submitted.
    """

    return refusal(
        refused.code,
        "change_candidate",
        f"command {index} ({command}) refused: {refused.detail}",
        facts=RefusalFacts(
            table=refused.table,
            record_id=refused.record_id,
            expected=refused.expected,
            observed=refused.observed,
        ),
        next_action=refused.next_action,
    )


def batch_lineage_cycle_refusal(
    revision_id: str, cycle_members: tuple[str, ...], *, family: bool
) -> KnowledgeRefusal:
    """Refuse a batch that leaves a lineage graph cyclic after it was applied.

    The rule and its two branches are the shared ones; this restates the refusal for the batch
    operation, because the caller submitted a batch and needs to know which graph came out cyclic.
    """

    relation = "family" if family else "invariant"
    return refusal(
        "lineage_cycle",
        "change_candidate",
        f"the completed batch leaves the {relation} lineage cyclic at revision {revision_id}",
        facts=RefusalFacts(
            table="family_predecessor" if family else "invariant_predecessor",
            record_id=revision_id,
            observed=", ".join(cycle_members),
        ),
        next_action=(
            "Author an acyclic predecessor set; a lineage is a set of exact ancestors, so a "
            "circular one describes no order at all. The transaction left no row behind."
        ),
    )


def batch_command_refusal(
    code: KnowledgeRefusalCode,
    command: str,
    *,
    detail: str,
    next_action: str,
    facts: RefusalFacts | None = None,
) -> KnowledgeRefusal:
    """Build one batch-scoped refusal the per-record factories cannot express.

    ``command`` names the failing command as ``"<index>:<kind>"``, so one argument carries both the
    position the caller has to look at and the kind of command that was there.
    """

    position, _, kind = command.partition(":")
    return refusal(
        code,
        "change_candidate",
        f"command {position} ({kind}) refused: {detail}",
        facts=facts,
        next_action=next_action,
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


# -- the candidate lifecycle and snapshot publication boundary --------------------------
#
# A candidate is durable working state, so its failures are stated as facts about *which*
# working object was addressed and what was actually there. Every one of them preserves the
# existing bytes: nothing in this group removes, replaces or repairs a database, a receipt or
# a published snapshot to make a later step succeed.


def selected_input_unavailable_refusal(
    operation: KnowledgeOperation, detail: str, *, record_id: str | None = None
) -> KnowledgeRefusal:
    """Refuse when one explicitly selected input is absent or unreadable.

    A missing input is an input error, never an empty dataset: the alternative -- answering an
    absent selection with a fresh schema -- is how a reader comes to report "no knowledge" for a
    candidate whose file simply was not there.
    """

    return refusal(
        "selected_input_unavailable",
        operation,
        detail,
        facts=RefusalFacts(record_id=record_id),
        next_action=(
            "Supply the exact selected input again, or admit a new candidate at an explicitly "
            "chosen destination. Nothing here falls back to HEAD, a branch name or Markdown."
        ),
    )


def candidate_binding_changed_refusal(
    operation: KnowledgeOperation,
    detail: str,
    *,
    expected: str | None = None,
    observed: str | None = None,
) -> KnowledgeRefusal:
    """Refuse a candidate whose recorded binding is not the admission's.

    This is the restart and branch-switch refusal: the working database is preserved exactly as
    it was, because the authored work it holds is not reproducible from the new baseline.
    """

    return refusal(
        "candidate_binding_changed",
        operation,
        detail,
        facts=RefusalFacts(expected=expected, observed=observed),
        next_action=(
            "Resume the candidate the receipt was written for, or explicitly admit a new "
            "candidate for the new baseline. The existing working database and its journals are "
            "left untouched."
        ),
    )


def candidate_snapshot_unpublished_refusal(
    operation: KnowledgeOperation, *, expected: str, observed: str, destination_ref: str
) -> KnowledgeRefusal:
    """Refuse a read whose runtime candidate is not the dataset the closed snapshot holds.

    It is a report, not a repair: the publication is a separate explicit operation owned by the
    caller, and no read publishes rows or attaches them to an older snapshot.
    """

    return refusal(
        "candidate_snapshot_unpublished",
        operation,
        "the runtime candidate's logical dataset is not the one the closed snapshot holds",
        facts=RefusalFacts(record_id=destination_ref, expected=expected, observed=observed),
        next_action=(
            "Publish the candidate through the snapshot publication operation and capture that "
            "exact closed file into the selected memory tree, then read again."
        ),
    )


def snapshot_incomplete_refusal(
    operation: KnowledgeOperation, detail: str, *, stage_ref: str
) -> KnowledgeRefusal:
    """Refuse a private stage that was not completed, verified or durably flushed.

    Both halves use this one code: a closed snapshot that could not be frozen, and a candidate
    whose receipt or database could not be sealed in its private stage. In both cases nothing
    outside the operation's own stage exists afterwards, so the caller has one decision to make --
    stop, or retry from the same explicitly selected input -- and the destination or the admitted
    path was never touched.
    """

    return refusal(
        "snapshot_incomplete",
        operation,
        f"the private stage was not completed: {detail}",
        facts=RefusalFacts(record_id=stage_ref),
        next_action=(
            "Investigate the reported stage failure, then re-run the operation from the same "
            "expected input. The destination was not replaced and the incomplete stage is removed "
            "by the operation that made it."
        ),
    )


def destination_stale_refusal(
    operation: KnowledgeOperation,
    *,
    destination_ref: str,
    expected: str | None,
    observed: str,
) -> KnowledgeRefusal:
    """Refuse a publication whose admitted destination is not the destination that is there."""

    return refusal(
        "destination_stale",
        operation,
        "the destination does not match the identity the publication was admitted against",
        facts=RefusalFacts(
            record_id=destination_ref,
            expected=expected if expected is not None else "<absent>",
            observed=observed,
        ),
        next_action=(
            "Reread the destination's logical identity and publish against the identity that is "
            "actually there. No destination was replaced."
        ),
    )


def publication_failed_refusal(
    operation: KnowledgeOperation, detail: str, *, destination_ref: str, observed: str
) -> KnowledgeRefusal:
    """Refuse a publication whose install or readback did not complete."""

    return refusal(
        "publication_failed",
        operation,
        f"the closed snapshot was not installed: {detail}",
        facts=RefusalFacts(
            record_id=destination_ref, expected="<the frozen snapshot>", observed=observed
        ),
        next_action=(
            "Inspect the destination path, then publish again from the same expected candidate. "
            "No success is reported for bytes this operation did not install."
        ),
    )


def publication_durability_unconfirmed_refusal(
    operation: KnowledgeOperation, detail: str, *, destination_ref: str, observed: str
) -> KnowledgeRefusal:
    """Refuse to claim success when the replacement completed but could not be confirmed.

    The complete new file may already be at the destination. Reporting that honestly is the
    point: claiming the old file was restored would be false, and claiming success from an
    unverified readback would be unchecked.
    """

    return refusal(
        "publication_durability_unconfirmed",
        operation,
        f"the destination could not be revalidated after the replacement: {detail}",
        facts=RefusalFacts(record_id=destination_ref, observed=observed),
        next_action=(
            "Reopen the destination directly and revalidate its logical identity. The complete "
            "new snapshot may already be in place; do not restore the previous file blindly."
        ),
    )


# ---------------------------------------------------------------------------
# Authored judgment facets. Three failures this record group can reach are not any earlier
# failure's fact, and each reuses the code that already names it rather than coining a new one:
# accepted origin data in a facet command is the same ``promotion_not_supported`` a proposed-only
# candidate path refuses, a supersession graph that reaches itself is the same ``lineage_cycle``
# the two predecessor graphs refuse, and a dataset that predates the tables an operation needs is
# the same ``unsupported_schema`` the open path and the portable reader already refuse.


def facet_promotion_not_supported_refusal(record_id: str) -> KnowledgeRefusal:
    """Refuse a facet command that would store accepted origin data.

    Requirement 3.2: this leaf authors proposed origin data only and exposes no promotion
    operation. The code is the shipped ``promotion_not_supported``; the factory is separate
    because the offending record is a facet envelope and the record group's own table is what a
    caller has to look at, exactly as the invariant and family duplicates have separate factories
    under one ``duplicate_identity`` code.
    """

    return refusal(
        "promotion_not_supported",
        "change_candidate",
        "the batch carries a facet authored as accepted origin data, which this candidate-only "
        "operation never stores",
        facts=RefusalFacts(table="knowledge_record", record_id=record_id),
        next_action=(
            "Author the facet as proposed. Acceptance is decided by the owner of that process, not "
            "by a candidate change batch, and no facet command promotes a proposal."
        ),
    )


def facet_supersession_cycle_refusal(
    revision_id: str, cycle_members: tuple[str, ...]
) -> KnowledgeRefusal:
    """Refuse a batch that leaves the decision supersession graph cyclic after it was applied.

    Requirement 5.2: the edge graph is checked by the same shared lineage rule the invariant and
    family graphs are, and a cycle refuses the whole batch with the involved decisions named. The
    code is the shipped ``lineage_cycle``; the table it names is this record kind's own edge table,
    so a caller knows which graph came out cyclic.
    """

    return refusal(
        "lineage_cycle",
        "change_candidate",
        "the completed batch leaves the decision supersession graph cyclic at revision "
        f"{revision_id}",
        facts=RefusalFacts(
            table="facet_decision_supersession",
            record_id=revision_id,
            observed=", ".join(cycle_members),
        ),
        next_action=(
            "Author a supersession chain that terminates: a decision supersedes an earlier decision, "
            "so a circular one describes no order at all. The transaction left no row behind, and "
            "the superseded decisions are unchanged."
        ),
    )


def batch_supersession_cycle_refusal(
    revision_id: str, cycle_members: tuple[str, ...]
) -> KnowledgeRefusal:
    """Refuse a batch whose own declarations leave the supersession graph cyclic.

    The batch-scoped twin of :func:`facet_supersession_cycle_refusal`: same code, same facts, and
    the position-naming detail a batch refusal carries, because what a caller has to look at is the
    declaration it wrote rather than a stored row that already existed.
    """

    return refusal(
        "lineage_cycle",
        "change_candidate",
        "the completed batch leaves the decision supersession graph cyclic at revision "
        f"{revision_id}",
        facts=RefusalFacts(
            table="facet_decision_supersession",
            record_id=revision_id,
            observed=", ".join(cycle_members),
        ),
        next_action=(
            "Author a supersession chain that terminates: a decision supersedes an earlier "
            "decision, so a circular one describes no order at all. The transaction left no row "
            "behind, and the superseded decisions are unchanged."
        ),
    )


def generation_mismatch_refusal(
    operation: KnowledgeOperation,
    detail: str,
    *,
    required: int,
    observed: int,
) -> KnowledgeRefusal:
    """Refuse an operation that would require a generation the dataset does not declare.

    Requirement 8.4, and the disposition ``KS-R10@v1`` §5 fixes for the same case: a dataset whose
    recorded generation predates the tables an operation needs is **not** migrated, widened or
    written through. The observed and required generation travel as the refusal's facts, and
    nothing was written.

    The code is the shipped ``unsupported_schema``. The factory is separate from the portable
    reader's because the remedy differs: an artifact is re-imported by a build that implements its
    generation, while an open dataset is served by the build that declares *its* generation --
    which this build does, for every generation it registers. What is refused is the write that
    would need the later one.
    """

    return refusal(
        "unsupported_schema",
        operation,
        f"the dataset declares a schema generation that does not carry the tables this operation "
        f"writes: {detail}",
        facts=RefusalFacts(expected=str(required), observed=str(observed)),
        next_action=(
            "Write through a dataset that declares the required generation. This operation performs "
            "no migration, no implicit upgrade and no partial table set; the dataset is unchanged."
        ),
    )


def explanation_revision_refusal(
    operation: KnowledgeOperation,
    detail: str,
    *,
    explanation_id: str,
    revision_id: str,
    expected: str,
) -> KnowledgeRefusal:
    """Refuse a revision an explanation's own identity does not carry.

    A designation names one exact revision of one exact explanation. A revision of another
    explanation -- or one that is not stored at all -- is a different fact from a stale digest: the
    remedy is to author or cite the revision rather than to reread the row, which is why it is
    ``invalid_reference`` and not ``stale_precondition``.
    """

    return refusal(
        "invalid_reference",
        operation,
        detail,
        facts=RefusalFacts(
            table="explanation_revision",
            record_id=revision_id,
            expected=expected,
            observed=f"{explanation_id}/{revision_id}",
        ),
        next_action=(
            "Author the revision for this explanation, or name a revision this explanation "
            "carries. An explanation's revisions are append-only, so a successor names its exact "
            "predecessor and nothing is rewritten in place."
        ),
    )
