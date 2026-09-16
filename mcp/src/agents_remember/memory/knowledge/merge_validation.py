"""What the merge proves about its own result, after SQLite applied the changeset.

Applying a changeset is not evidence that the intended changes landed. This module is the
postcondition half of the merge: it reads the merged candidate and the three inputs and refuses
when the result does not carry what the operation said it would. Four checks, each answering a
different question:

* **Structural validity** -- no foreign-key violation, every authored row still reading back as its
  typed aggregate, and every declared predecessor edge present. A merge may legitimately produce a
  *conflict*; it must never produce a malformed or dangling aggregate.
* **Immutability relative to the base** -- every revision the common base sealed is still identical
  to the base's row, and every edge the base sealed is still present. A sealed revision is
  immutable, so a merge may add successors and may never rewrite one.
* **Application postconditions** -- every operation the changeset materialised is looked for in the
  result: an insert's row is there holding the side's values, an update's supplied columns hold the
  side's values, a delete's row is gone.
* **Input integrity** -- each side's sealed revisions still match the common base. This is the
  refusal for a side that rewrote a revision in place behind its identity, which is the one input
  defect no schema comparison can see: the file is internally consistent and its content is simply
  not the aggregate the base sealed.

Every check reads through read-only connections and writes nothing. A refusal here means no
candidate was published and the merged temporary is discarded by its owner.

The digest comparison is deliberately the *stored* payload digest recomputed from the stored row:
re-stating the same payload with different JSON key order is not a change to a sealed revision,
while changing the statement behind the digest is.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import apsw

from agents_remember.memory.knowledge import logical, records, schema
from agents_remember.memory.knowledge.connection import fetch_one, open_read_only_database
from agents_remember.memory.knowledge.merge_changeset import (
    NOT_SUPPLIED,
    MaterializedChange,
)
from agents_remember.memory.knowledge.merge_refusals import (
    changeset_postcondition_failed_refusal,
    immutable_revision_changed_refusal,
)
from agents_remember.memory.knowledge.refusals import RefusalFacts
from agents_remember.models.knowledge.context import KNOWLEDGE_SCHEMA_NAME
from agents_remember.models.knowledge.result import (
    KnowledgeOperation,
    KnowledgeRefusal,
)

# The two immutable aggregates: their revision table and their predecessor-edge table. They are
# compared the same way, so the check is written once over both shapes.
_IMMUTABLE_AGGREGATES: tuple[tuple[str, str], ...] = (
    ("invariant_revision", "invariant_predecessor"),
    ("family_revision", "family_predecessor"),
)


@dataclass(frozen=True)
class MergeInputs:
    """The four datasets of one merge, named once so a validation pass cannot mix them up."""

    base: Path
    left: Path
    right: Path
    merged: Path


def require_structural_validity(
    path: Path, operation: KnowledgeOperation, *, role: str
) -> KnowledgeRefusal | None:
    """Refuse a dataset that is not structurally valid as this schema's knowledge.

    The typed-row read is the whole check: decoding every canonical table through the logical body
    constructor proves the JSON columns hold unambiguous JSON and the sealed aggregates still match
    their stored digests, because the decoders recompute the seal rather than trusting it.
    """

    connection = open_read_only_database(path)
    try:
        violations = [tuple(row) for row in connection.execute("PRAGMA foreign_key_check")]
        if violations:
            return changeset_postcondition_failed_refusal(
                operation,
                f"the {role} dataset holds {len(violations)} foreign-key violation(s)",
                facts=RefusalFacts(observed=str(violations[0])),
            )
        try:
            logical.logical_body(connection, KNOWLEDGE_SCHEMA_NAME)
        except (apsw.Error, ValueError) as error:
            return changeset_postcondition_failed_refusal(
                operation,
                f"the {role} dataset does not read back as its typed aggregates: {error}",
            )
        return _require_sealed_aggregates(connection, operation, role)
    finally:
        connection.close()


def require_immutable_revisions_preserved(
    operation: KnowledgeOperation, *, base: Path, candidate: Path, role: str
) -> KnowledgeRefusal | None:
    """Refuse a candidate that does not preserve every revision and edge the base sealed.

    Immutability is checked against the *base* rather than against whichever side happens to be
    present: a revision the common base sealed is the same revision in every dataset derived from
    it, so a difference is an in-place rewrite of a sealed aggregate rather than an authored
    successor.

    This is called twice, on two different candidates and with two different reachability facts.
    On a **side input** the call is reachable and has failing cases: a side that dropped a sealed
    aggregate reaches it. On the **merged candidate** it is not reachable by a black-box case, and
    that is stated rather than implied: the merged candidate is a copy of the left side with the
    right delta applied, the left side has already passed this same comparison, and the right delta
    cannot remove or rewrite a sealed aggregate without the right side failing it first -- a
    ``DELETE`` or ``UPDATE`` of a sealed revision is refused by that side's own trigger, and a side
    that dropped the trigger is refused by the preflight. A mutation that deletes the merged-
    candidate *call* therefore leaves every case green: a non-experiment, not a covered guard. It
    stays because publication is where the invariant has to hold, so the check that the bytes about
    to be published preserve the base's sealed aggregates belongs at that point rather than only at
    the inputs. Its own policy is exercised directly by
    ``test_knowledge_guarded_merge_boundaries.py::test_a_candidate_missing_a_sealed_aggregate_is_refused``.
    """

    base_reader = open_read_only_database(base)
    candidate_reader = open_read_only_database(candidate)
    try:
        for table, edge_table in _IMMUTABLE_AGGREGATES:
            difference = _first_revision_difference(
                operation, role, table, base_reader, candidate_reader
            )
            if difference is not None:
                return difference
            difference = _first_missing_edge(
                _EdgeCheck(
                    operation=operation,
                    role=role,
                    table=table,
                    edge_table=edge_table,
                    readers=(base_reader, candidate_reader),
                )
            )
            if difference is not None:
                return difference
        return None
    finally:
        base_reader.close()
        candidate_reader.close()


def require_side_inputs_preserved(
    operation: KnowledgeOperation, *, base: Path, side: Path, side_role: str
) -> KnowledgeRefusal | None:
    """Refuse a side whose sealed revisions are not the aggregates the common base sealed.

    This runs *before* the merge, so an input that rewrote a revision in place is refused as an
    input defect instead of being carried into a merged candidate.
    """

    return require_immutable_revisions_preserved(
        operation, base=base, candidate=side, role=f"{side_role} input"
    )


def require_applied_changes(
    operation: KnowledgeOperation,
    *,
    merged: Path,
    side: Path,
    operations: tuple[MaterializedChange, ...],
) -> KnowledgeRefusal | None:
    """Refuse a merge whose result does not carry every operation the changeset materialised.

    The comparison is per supplied column and against the *side the operation came from*.

    **What it would catch.** A merged candidate that a successful application did not actually
    produce: an operation the engine reported as applied whose row is absent from the result, whose
    supplied columns hold a different value than the side's, or -- for a ``DELETE`` -- a row that is
    still there.

    **Why this schema cannot produce that state.** The only producer of the merged candidate is
    SQLite's own changeset application over a copy of the left side. Every canonical table is a plain
    ``STRICT`` table and every declared trigger only ``RAISE(ABORT)``: none of them can silently
    skip, rewrite or downgrade a row, so an operation the engine reports as applied is applied. An
    input whose trigger set is not the declared one never reaches the application, because the
    preflight refuses it first.

    **No black-box case reaches this call site, and that is a fact about the code rather than a gap
    in the cases.** Removing the call leaves every case green, so it is a non-experiment: no merge of
    two datasets this schema admits can falsify it. It is kept because publication is where the
    merged bytes have to be the ones the merge intended, so a future change to the application step
    should fail here loudly rather than publish a candidate that lost a change. Its policy is
    exercised directly, so the refusal it would return is demonstrated rather than assumed, by
    ``test_knowledge_guarded_merge_boundaries.py::test_a_candidate_that_dropped_an_intended_change_is_refused``.
    """

    merged_reader = open_read_only_database(merged)
    side_reader = open_read_only_database(side)
    try:
        for change in operations:
            difference = _first_unapplied_change(operation, change, merged_reader, side_reader)
            if difference is not None:
                return difference
        return None
    finally:
        merged_reader.close()
        side_reader.close()


def _first_unapplied_change(
    operation: KnowledgeOperation,
    change: MaterializedChange,
    merged_reader: apsw.Connection,
    side_reader: apsw.Connection,
) -> KnowledgeRefusal | None:
    """Return the refusal for one operation the result does not carry, or None."""

    key = change.primary_key()
    record_id = _render_key(change.table, key)
    observed_row = _row_of(merged_reader, change.table, key)
    if change.operation == "DELETE":
        if observed_row is None:
            return None
        return changeset_postcondition_failed_refusal(
            operation,
            f"the result still holds the row the delta deleted from {change.table}",
            facts=RefusalFacts(table=change.table, record_id=record_id),
        )
    if observed_row is None:
        return changeset_postcondition_failed_refusal(
            operation,
            f"the result does not hold the row the delta wrote into {change.table}",
            facts=RefusalFacts(table=change.table, record_id=record_id),
        )
    expected_row = _row_of(side_reader, change.table, key)
    if expected_row is None:
        return changeset_postcondition_failed_refusal(
            operation,
            f"the side the delta was derived from does not hold its own "
            f"{change.operation} row in {change.table}",
            facts=RefusalFacts(table=change.table, record_id=record_id),
        )
    return _first_differing_column(operation, change, record_id, expected_row, observed_row)


def _first_differing_column(
    operation: KnowledgeOperation,
    change: MaterializedChange,
    record_id: str,
    expected_row: tuple[Any, ...],
    observed_row: tuple[Any, ...],
) -> KnowledgeRefusal | None:
    """Return the refusal for the first supplied column the result does not match, or None."""

    columns = schema.CANONICAL_COLUMNS[change.table]
    for column in sorted(change.supplied):
        position = columns.index(column)
        expected = expected_row[position]
        observed = observed_row[position]
        if expected == observed:
            continue
        return changeset_postcondition_failed_refusal(
            operation,
            f"the result's {column} in {change.table} is not the value the delta carried",
            facts=RefusalFacts(
                table=change.table,
                record_id=record_id,
                expected=_render(column, expected),
                observed=_render(column, observed),
            ),
        )
    return None


def _require_sealed_aggregates(
    connection: apsw.Connection, operation: KnowledgeOperation, role: str
) -> KnowledgeRefusal | None:
    """Refuse a dataset whose sealed revision payloads no longer match their stored rows.

    The decoder recomputes each payload's digest and raises when the stored row does not match it,
    so this is where a row edited behind its seal is caught with the file still open rather than
    during a later read by an unrelated consumer.
    """

    for table, edge_table in _IMMUTABLE_AGGREGATES:
        for revision_id, row in _revision_rows(connection, table):
            predecessors = records.decode_predecessor_rows(
                list(
                    connection.execute(
                        f"SELECT parent_revision_id FROM {edge_table} WHERE child_revision_id = ?",
                        (revision_id,),
                    )
                )
            )
            try:
                _decode_revision(table, row, predecessors)
            except ValueError as error:
                return immutable_revision_changed_refusal(
                    operation,
                    f"a {table} row in the {role} dataset does not match its payload digest: "
                    f"{error}",
                    table=table,
                    record_id=revision_id,
                )
    return None


def _first_revision_difference(
    operation: KnowledgeOperation,
    role: str,
    table: str,
    base_reader: apsw.Connection,
    candidate_reader: apsw.Connection,
) -> KnowledgeRefusal | None:
    """Return the refusal for the first sealed revision the candidate does not preserve, or None."""

    candidate_rows = dict(_revision_rows(candidate_reader, table))
    for revision_id, row in _revision_rows(base_reader, table):
        if candidate_rows.get(revision_id) == row:
            continue
        return immutable_revision_changed_refusal(
            operation,
            f"the {role} does not preserve the sealed {table} aggregate the common base holds",
            table=table,
            record_id=revision_id,
        )
    return None


@dataclass(frozen=True)
class _EdgeCheck:
    """One sealed-edge comparison: which aggregate, which role, and the two readers it needs."""

    operation: KnowledgeOperation
    role: str
    table: str
    edge_table: str
    readers: tuple[apsw.Connection, apsw.Connection]


def _first_missing_edge(check: _EdgeCheck) -> KnowledgeRefusal | None:
    """Return the refusal for the first sealed predecessor edge the candidate dropped, or None."""

    base_reader, candidate_reader = check.readers
    candidate_edges = _edge_set(candidate_reader, check.edge_table)
    for edge in sorted(_edge_set(base_reader, check.edge_table)):
        if edge in candidate_edges:
            continue
        return immutable_revision_changed_refusal(
            check.operation,
            f"the {check.role} does not preserve a sealed {check.table} predecessor edge the "
            "base holds",
            table=check.edge_table,
            record_id=f"{edge[0]}->{edge[1]}",
        )
    return None


def _edge_set(connection: apsw.Connection, edge_table: str) -> set[tuple[str, str]]:
    """Return every declared predecessor edge of one edge table."""

    return {
        (str(row[0]), str(row[1]))
        for row in connection.execute(
            f"SELECT child_revision_id, parent_revision_id FROM {edge_table}"
        )
    }


def _revision_rows(connection: apsw.Connection, table: str) -> list[tuple[str, tuple[Any, ...]]]:
    """Return each revision row of one aggregate table, paired with its identity."""

    columns = ", ".join(schema.CANONICAL_COLUMNS[table])
    return [
        (str(row[2]), tuple(row)) for row in connection.execute(f"SELECT {columns} FROM {table}")
    ]


def _decode_revision(table: str, row: tuple[Any, ...], predecessors: tuple[str, ...]) -> object:
    """Decode one stored revision row through the decoder that owns its seal."""

    decoder = (
        records.decode_revision_row
        if table == "invariant_revision"
        else records.decode_family_revision_row
    )
    return decoder(row, predecessors)


def _row_of(
    connection: apsw.Connection, table: str, key: tuple[Any, ...]
) -> tuple[Any, ...] | None:
    """Return one row of one canonical table by its declared primary key, or None."""

    keys = logical.PRIMARY_KEYS[table]
    columns = schema.CANONICAL_COLUMNS[table]
    where = " AND ".join(f"{column} IS ?" for column in keys)
    row = fetch_one(
        connection,
        f"SELECT {', '.join(columns)} FROM {table} WHERE {where}",
        tuple(key),
    )
    return None if row is None else tuple(row)


def _render_key(table: str, key: tuple[Any, ...]) -> str:
    """Render one operation's key as the readable identity a refusal carries."""

    keys = logical.PRIMARY_KEYS[table]
    return "/".join(f"{column}={value}" for column, value in zip(keys, key, strict=False))


def _render(column: str, value: Any) -> str:
    """Render one column's value for a refusal, naming the not-supplied marker explicitly."""

    if value is NOT_SUPPLIED or value == NOT_SUPPLIED:
        return f"{column}=<not-supplied>"
    return f"{column}={value}"
