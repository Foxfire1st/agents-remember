"""The refusal vocabulary of the guarded common-base merge.

One factory per observable failure point of the merge contract, in one module so the codes, the
offending record and the advertised next action stay together instead of being spelled out at each
call site. The shared ``refusal`` factory and the exception types stay in :mod:`refusals`; this
module only names the failures a merge can observe.

Two of these are *structural* refusals in the strong sense: a schema disagreement and a missing
canonical table are refused **before a session exists**, because SQLite's changeset application can
skip a table it cannot match and still report success. The rest are refusals of an application or
of a validation that ran against the result, and every one of them leaves the caller's inputs
untouched.
"""

from __future__ import annotations

from agents_remember.memory.knowledge.refusals import RefusalFacts, refusal
from agents_remember.models.knowledge.result import KnowledgeOperation, KnowledgeRefusal


def schema_mismatch_refusal(
    operation: KnowledgeOperation,
    detail: str,
    *,
    table: str | None = None,
    expected: str | None = None,
    observed: str | None = None,
) -> KnowledgeRefusal:
    """Refuse an input whose structure is not the supported schema generation.

    A version string is not the check: two databases can both say ``user_version = 1`` and disagree
    about a column type, a foreign key or a trigger. The refused input is not migrated, repaired or
    re-created -- schema reconciliation is explicitly outside this operation.
    """

    return refusal(
        "schema_mismatch",
        operation,
        f"the input's declared structure is not the supported schema: {detail}",
        facts=RefusalFacts(table=table, expected=expected, observed=observed),
        next_action=(
            "Re-select an input written by the supported schema generation. A structural "
            "difference is reported, never reconciled: this operation performs no migration."
        ),
    )


def missing_required_table_refusal(
    operation: KnowledgeOperation, table: str, *, observed: str
) -> KnowledgeRefusal:
    """Refuse an input that lacks a canonical table the merge would have to carry changes for.

    It gets its own code because the failure it prevents is the silent one: a changeset whose table
    does not exist on the target is either an error or -- worse -- no operation at all, and a
    success return code would then stand for a change that never happened.
    """

    return refusal(
        "missing_required_table",
        operation,
        f"the canonical table {table} is absent from an input of this merge",
        facts=RefusalFacts(table=table, expected=table, observed=observed),
        next_action=(
            "Re-select an input that carries every canonical table. A partial schema is refused "
            "before any session exists; SQLite can report success while omitting its changes."
        ),
    )


def conflicting_values_refusal(
    operation: KnowledgeOperation,
    *,
    table: str,
    record_id: str,
    expected: str | None,
    observed: str | None,
) -> KnowledgeRefusal:
    """Refuse a merge in which both sides changed the same field of the same row differently.

    The whole application is rolled back and neither side is preferred: latest-writer-wins is not a
    policy this operation implements, and stripping the conflicting operation would publish a
    candidate that silently dropped one side's intended change.
    """

    return refusal(
        "conflicting_values",
        operation,
        "both sides changed the same row's stored value and the two values disagree",
        facts=RefusalFacts(table=table, record_id=record_id, expected=expected, observed=observed),
        next_action=(
            "Reconcile the two authored values explicitly and merge the reconciled datasets "
            "again. No candidate was published and both inputs are unchanged."
        ),
    )


def duplicate_identity_refusal(
    operation: KnowledgeOperation,
    *,
    table: str,
    record_id: str,
    expected: str | None,
    observed: str | None,
) -> KnowledgeRefusal:
    """Refuse a merge in which both sides independently inserted the same identity.

    The refusal applies **even when the two payloads are equal**. Two independent insertions of one
    identity are two authored acts that happen to collide; treating them as one because the bytes
    match would be this operation deciding that two authors meant the same thing.
    """

    return refusal(
        "duplicate_identity",
        operation,
        "both sides inserted a row under the same identity",
        facts=RefusalFacts(table=table, record_id=record_id, expected=expected, observed=observed),
        next_action=(
            "Author one explicit reconciliation that keeps a single identity, then merge again. "
            "Identical payloads do not make two independent insertions one insertion."
        ),
    )


def delete_reference_conflict_refusal(
    operation: KnowledgeOperation,
    *,
    facts: RefusalFacts,
) -> KnowledgeRefusal:
    """Refuse a merge in which one side removed a row the other side's new row still references.

    The refusal covers both orientations, because SQLite reports the same foreign-key fact whichever
    side performed the removal. The row-level identity is deliberately absent: for a foreign-key
    conflict the engine hands the conflict callback no change at all, so naming a row here would be
    this operation's guess rather than the engine's report.
    """

    return refusal(
        "delete_reference_conflict",
        operation,
        "one side's change left a reference to a row the other side removed",
        facts=RefusalFacts(
            table=facts.table,
            record_id=facts.record_id,
            expected=facts.expected,
            observed=facts.observed,
        ),
        next_action=(
            "Restore the removed row or retract the new reference in an authored reconciliation, "
            "then merge again. A row the result still points at is never deleted to finish a merge."
        ),
    )


def duplicate_relationship_refusal(
    operation: KnowledgeOperation,
    *,
    table: str,
    record_id: str,
    expected: str,
    observed: str,
) -> KnowledgeRefusal:
    """Refuse a merge whose union duplicates a unique declared relationship under new identities."""

    return refusal(
        "duplicate_relationship",
        operation,
        "the two sides declare the same unique relationship under different identities",
        facts=RefusalFacts(table=table, record_id=record_id, expected=expected, observed=observed),
        next_action=(
            "Decide which relationship identity is the real one in an authored reconciliation. "
            "A second row for the same declared tuple is refused, not silently dropped."
        ),
    )


def immutable_revision_changed_refusal(
    operation: KnowledgeOperation, detail: str, *, table: str, record_id: str
) -> KnowledgeRefusal:
    """Refuse an input that altered an immutable revision in place behind its identity.

    This is the input-integrity refusal, and it fires even when the file passes its own
    ``integrity_check``: the row is well formed and its content is not what the base sealed, which
    is exactly the case a byte-level schema check cannot see. The compared value is the stored
    payload digest, so re-stating the same payload in different JSON key order is not a change.
    """

    return refusal(
        "immutable_revision_changed",
        operation,
        f"an immutable revision differs from the common base: {detail}",
        facts=RefusalFacts(table=table, record_id=record_id),
        next_action=(
            "Re-select the input that still holds the sealed aggregate, or author a new successor "
            "revision with exact predecessors. A sealed revision is never rewritten in place."
        ),
    )


def session_unavailable_refusal(
    operation: KnowledgeOperation, detail: str, *, requirement: str
) -> KnowledgeRefusal:
    """Refuse when the selected binding/build cannot produce or apply a changeset at all.

    This is a capability failure with a concrete installation requirement, not a fallback
    opportunity: there is one supported binding, and a build without session support refuses rather
    than switching to a second mechanism.
    """

    return refusal(
        "session_unavailable",
        operation,
        f"the selected binding cannot use SQLite's session/changeset machinery: {detail}",
        facts=RefusalFacts(expected=requirement, observed=detail),
        next_action=(
            f"Install a binding/build that provides the required capability ({requirement}). Do "
            "not substitute a second backend, a patchset or a hand-written row diff."
        ),
    )


def changeset_incomplete_refusal(
    operation: KnowledgeOperation,
    detail: str,
    *,
    table: str | None = None,
    expected: str | None = None,
    observed: str | None = None,
) -> KnowledgeRefusal:
    """Refuse a delta that did not prove complete coverage of its base-to-side change.

    Replaying the changeset into a fresh copy of the base must reproduce the side's whole logical
    dataset. When it does not, the delta omitted something -- the class of failure SQLite reports as
    success -- and the operation refuses rather than applying a delta it cannot vouch for.
    """

    return refusal(
        "changeset_incomplete",
        operation,
        f"the base-to-side changeset did not reproduce its side: {detail}",
        facts=RefusalFacts(table=table, expected=expected, observed=observed),
        next_action=(
            "Re-derive the delta with every canonical table attached to the session and verify the "
            "replay again. A SQLite success return code is not evidence of complete application."
        ),
    )


def changeset_postcondition_failed_refusal(
    operation: KnowledgeOperation,
    detail: str,
    *,
    facts: RefusalFacts | None = None,
) -> KnowledgeRefusal:
    """Refuse a merge whose applied result does not carry an intended change.

    The check is over the *result*, not over the return code: every operation the changeset
    materialised is looked for in the merged candidate, so an application that reported success
    while dropping an operation is caught here even when the replay passed.
    """

    return refusal(
        "changeset_postcondition_failed",
        operation,
        f"the merged candidate does not carry an intended change: {detail}",
        facts=facts,
        next_action=(
            "Investigate the application step before publishing anything. No candidate was "
            "published, and the merged temporary is discarded."
        ),
    )
