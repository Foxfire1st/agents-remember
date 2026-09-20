"""Base-to-side deltas: session changesets, their materialised operations and the coverage replay.

A delta is produced by SQLite's session extension, and this module owns the three facts that make
one trustworthy:

1. **It is a changeset, never a patchset.** A patchset carries only the new values, so applying it
   cannot detect that the target row moved; a changeset carries the original values too, which is
   what lets the application report a conflict instead of overwriting. The binding below asks the
   session for ``changeset()`` and never for ``patchset()``.
2. **The operations are materialised immediately.** An APSW ``TableChange`` is a view into a
   changeset cursor: it expires when the iterator advances. Every reported field is therefore copied
   out inside the loop, and ``apsw.no_change`` -- which marks a column the changeset does not supply
   and is *not* SQL ``NULL`` -- is translated into the explicit not-supplied marker at this boundary
   rather than being conflated with a stored ``None``.
3. **Coverage is proven by replay, not by a return code.** Applying the delta to a fresh copy of the
   base must reproduce the side's whole logical dataset. A table that was never attached to the
   session contributes no operation at all and the application still returns success; the only place
   that omission is observable is a comparison of the replayed result against the side it came from.

**Direction is load-bearing and silent when wrong.** The session lives on the connection holding
the *newer* content -- the side -- with the validated base attached as a second schema, and driving
it from the opposite side yields an empty changeset with no error. The caller compares the tables a
delta touched against the tables whose rows differ, which is what turns that class of mistake into a
refusal instead of a clean-looking no-op merge.
"""

from __future__ import annotations

import hashlib
import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import apsw

from agents_remember.memory.knowledge import logical
from agents_remember.memory.knowledge.connection import BUSY_TIMEOUT_MILLISECONDS
from agents_remember.memory.knowledge.merge_refusals import (
    changeset_incomplete_refusal,
    session_unavailable_refusal,
)
from agents_remember.memory.knowledge.schema_generations import SchemaGeneration
from agents_remember.models.knowledge.candidate import SnapshotIdentity
from agents_remember.models.knowledge.merge import (
    AuthoredDecision,
    AuthoredReconciliation,
    ChangeOperationKind,
)
from agents_remember.models.knowledge.result import KnowledgeOperation, KnowledgeRefusal

# The schema name the validated base is attached under. One constant, because the session's
# ``diff`` call and the attach statement have to name the same schema or the diff silently compares
# the side against itself.
BASE_SCHEMA_NAME = "ks_base"

# The capability this module needs from the selected binding. It is stated as an installation
# requirement rather than as a fallback because there is exactly one supported binding.
REQUIRED_SESSION_CAPABILITY = "apsw with SQLite session/changeset support (ENABLE_SESSION)"

# What one column holds when the changeset does not supply it. It is deliberately not ``None``:
# ``None`` in a materialised row is a stored SQL NULL, and conflating the two would make "this
# column did not change" and "this column changed to NULL" the same value.
NOT_SUPPLIED = "<not-supplied>"

# The conflict code SQLite reports when it could not apply a change because the result would break
# a foreign key. It is the one conflict that arrives without a row-level change in hand.
_FOREIGN_KEY_CONFLICT = int(apsw.SQLITE_CHANGESET_FOREIGN_KEY)

# The conflict action returned for a conflict the *caller* already decided, and the one returned
# for every conflict it did not. ``OMIT`` and ``REPLACE`` are unreachable without an authored
# decision naming exactly that row: this package still never drops a conflicting operation on its
# own judgement, and never replaces one side's value with the other's as an automatic resolution.
# The distinction is the whole point -- "the caller reconciled this row explicitly" and "the merge
# picked a winner" are different facts, and only the first is expressible here.
_ABORT = int(apsw.SQLITE_CHANGESET_ABORT)
_OMIT = int(apsw.SQLITE_CHANGESET_OMIT)
_REPLACE = int(apsw.SQLITE_CHANGESET_REPLACE)


@dataclass(frozen=True)
class MaterializedChange:
    """One changeset operation, copied out of the cursor before it expires.

    ``old`` and ``new`` are positional tuples in the table's declared column order; a value is
    either a stored SQL value, ``None`` for a stored SQL NULL, or :data:`NOT_SUPPLIED`. ``supplied``
    names the columns on the operation's *carrying* side -- ``new`` for an insert or an update,
    ``old`` for a delete -- which is what makes "this operation sets this column" checkable without
    re-reading the changeset.
    """

    table: str
    operation: ChangeOperationKind
    primary_key_columns: tuple[int, ...]
    old: tuple[Any, ...] | None
    new: tuple[Any, ...] | None
    supplied: frozenset[str]
    # The selected generation's declared column order for this table. SQLite reports a changed
    # column by position, so reading one back needs the order of the generation the operation was
    # materialized under, not the running build's.
    columns: tuple[str, ...] = ()

    def primary_key(self) -> tuple[Any, ...]:
        """Return the operation's key values, read from the side that carries them.

        A changeset supplies only the columns an operation changes, and a key column is by
        definition unchanged by an ``UPDATE``: SQLite therefore reports the update's *new* entries
        with the not-supplied marker exactly where the key is, and a key read from that side is a
        row identity that names no row. The old side is read first whenever it is present, which
        resolves both operations that carry one -- a ``DELETE`` has only an old side, an ``UPDATE``
        has both and only the old side holds the key -- and an ``INSERT``, the one operation with no
        old side, carries every column including the key. This is the same rule
        :func:`_conflicting_key` applies inside the conflict callback, stated once for the
        postcondition check: reading the carrying side is what makes "the result does not hold the
        row the delta wrote" a fact about a row rather than about the marker.
        """

        side = self.old if self.old is not None else self.new
        if side is None:
            return ()
        return tuple(side[position] for position in self.primary_key_columns)

    def column_value(self, column: str, *, side: str) -> Any:
        """Return one column's value from the named side of the operation."""

        values = self.new if side == "new" else self.old
        if values is None:
            return NOT_SUPPLIED
        return values[self.columns.index(column)]


@dataclass(frozen=True)
class Delta:
    """One complete base-to-side delta: its changeset bytes and the fixed facts it proved."""

    side: str
    side_path: Path
    base_path: Path
    changeset: bytes
    operations: tuple[MaterializedChange, ...]
    changed_tables: tuple[str, ...]
    operation_counts: dict[str, int]

    @property
    def changeset_digest(self) -> str:
        """Return the sha256 of the exact changeset bytes."""

        return hashlib.sha256(self.changeset).hexdigest()

    @property
    def is_empty(self) -> bool:
        """Whether the changeset carries no operation at all."""

        return not self.operations


@dataclass(frozen=True)
class AppliedChangeset:
    """What one application attempt did: the conflict it hit, or nothing.

    ``conflict_key`` is the exact primary key of the row the engine could not apply, copied out of
    the change while the callback still held it. It is read from the **old** side first, because a
    changeset omits the columns an operation does not change and a key column is by definition
    unchanged by an ``UPDATE``: the new side of an update carries the not-supplied marker where the
    key is, so a key read from there would be a row identity that names no row.

    ``detail`` carries the engine's own message when the application failed without invoking the
    conflict callback at all -- a missing target table is that shape -- so a caller always has a
    reason even when there was no conflict to report.

    ``refusal`` carries a check the caller asked to run **inside the application's own
    transaction**: the operation was applied, the check refused it, and the application was rolled
    back, so the target holds exactly what it held before. It is a third outcome rather than a
    conflict because no change conflicted -- the engine applied everything it was given, and what
    the caller proved afterwards is that the whole application must not stand.

    ``resolved`` names every conflict an authored decision settled instead of aborting, so the
    caller's own postcondition check can tell "the engine dropped this operation" from "the caller
    decided this row". Without it the postcondition would refuse a candidate the caller explicitly
    asked for.
    """

    conflict_code: int | None = None
    conflict_table: str | None = None
    conflict_operation: str | None = None
    conflict_key: tuple[str, ...] | None = None
    detail: str = ""
    refusal: KnowledgeRefusal | None = None
    resolved: tuple[ResolvedConflict, ...] = ()

    @property
    def conflicted(self) -> bool:
        """Whether the application hit a conflict callback at all."""

        return self.conflict_code is not None


@dataclass(frozen=True)
class ResolvedConflict:
    """One conflict an authored decision settled, with the row identity the engine supplied.

    ``record_id`` is rendered the way the refusal renders it -- the primary key values joined with
    ``/`` -- so the record the caller decided and the record the postcondition check would have
    looked for are the same string rather than two spellings of one row.

    ``reason`` is empty for a conflict the callback attributed to a row, and names what was removed
    for a row the caller's referential decision retracted: SQLite reports that conflict without a
    row, so the row is only identified afterwards, by the violation it left behind.
    """

    table: str
    record_id: str
    decision: AuthoredDecision
    conflict_code: int
    reason: str = ""


def require_session_capability(operation: KnowledgeOperation) -> KnowledgeRefusal | None:
    """Refuse when the selected binding cannot produce or apply a changeset at all.

    The check reads the binding's own compile options rather than guessing from a version, because
    a wheel built without session support has the classes and not the capability.
    """

    missing = [
        name
        for name, present in (
            ("apsw.Session", hasattr(apsw, "Session")),
            ("apsw.Changeset", hasattr(apsw, "Changeset")),
            ("ENABLE_SESSION", "ENABLE_SESSION" in apsw.compile_options),
        )
        if not present
    ]
    if not missing:
        return None
    return session_unavailable_refusal(
        operation,
        f"the selected binding is missing {', '.join(missing)}",
        requirement=REQUIRED_SESSION_CAPABILITY,
    )


def build_delta(
    side_database: Path,
    base_database: Path,
    *,
    side: str,
    generation: SchemaGeneration,
) -> Delta:
    """Produce the complete base-to-side changeset and materialise every operation in it.

    The session runs on a connection whose main database is the *side* and which has the validated
    base attached as :data:`BASE_SCHEMA_NAME`; every table of **the selected generation** is attached
    and diffed before the changeset is read, so a table the session does not know about cannot be
    skipped silently. Attaching the running build's manifest instead is the failure requirement 6.2
    names: a v1/v1/v1 merge would attach generation 2's tables, and the version-1 tables' operations
    would never be diffed. The side connection is opened read-only: a diff reads both databases and
    never writes either.
    """

    connection = apsw.Connection(str(side_database), flags=apsw.SQLITE_OPEN_READONLY)
    connection.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MILLISECONDS}")
    try:
        connection.execute(f"ATTACH DATABASE ? AS {BASE_SCHEMA_NAME}", (str(base_database),))
        session = apsw.Session(connection, "main")
        try:
            for table in generation.tables:
                session.attach(table)
                session.diff(BASE_SCHEMA_NAME, table)
            changeset = session.changeset()
        finally:
            session.close()
    finally:
        connection.close()
    operations = tuple(_materialize(changeset, generation))
    counts: dict[str, int] = {table: 0 for table in generation.tables}
    for change in operations:
        counts[change.table] += 1
    return Delta(
        side=side,
        side_path=Path(side_database),
        base_path=Path(base_database),
        changeset=changeset,
        operations=operations,
        changed_tables=tuple(table for table in generation.tables if counts[table] > 0),
        operation_counts=counts,
    )


def apply_changeset(
    delta: Delta,
    target_path: Path,
    *,
    within_transaction: Callable[[apsw.Connection], KnowledgeRefusal | None] | None = None,
    reconciliations: Sequence[AuthoredReconciliation] = (),
) -> AppliedChangeset:
    """Apply one delta to one private target with a conflict callback that aborts.

    The callback copies the available facts -- the conflict code, the table, the operation and the
    **exact key of the row it could not apply** -- and returns ``SQLITE_CHANGESET_ABORT``. The first
    blocking conflict is captured and the whole application is rolled back, rather than continuing
    with ``OMIT`` to collect a cosmetically complete list.

    ``reconciliations`` are the caller's decisions rather than this operation's: when one names
    exactly the row the engine just refused, the callback applies that row's authored decision --
    ``keep-left`` retracts the arriving change, ``keep-right`` applies it over the stored value --
    records the decision in ``resolved``, and lets the application continue to the *next* conflict,
    which is refused exactly as before. A row no decision names is never touched, so nothing here
    becomes an automatic resolution policy.

    It is a **sequence** because one retained conflict is rarely the last one. A decision that
    settles the first conflict reveals the second, and the attempt that answers the second has to
    carry the first with it: without it the merge re-refuses the row the first decision already
    answered, the two conflicts alternate, and the caller is offered a decision it has already made
    and that has already had its effect. Each decision still answers only the row it named.

    The key is copied here rather than looked up afterwards for two reasons: an APSW ``TableChange``
    expires when the iterator advances, and this is the only place the engine names the row it
    actually refused. A later search of the changeset finds *an* operation on the table, which is a
    different fact from the operation that conflicted.

    ``within_transaction`` is the caller's own rule over the rows the application just wrote, and it
    runs on the same connection, inside the same transaction, after the changeset has been applied
    and **before the commit**. The application is therefore one atomic step with the check: a check
    that refuses rolls the whole application back and the target keeps exactly what it held, which
    is what a rule over the applied rows has to mean -- a rule proved after the commit would leave
    a committed state the caller then rejects, and a rule proved before the application would be a
    rule over the delta rather than over the result.
    """

    captured: dict[str, Any] = {}
    settled: list[ResolvedConflict] = []
    rowless: list[int] = []
    target = Path(target_path)

    def conflict(code: int, change: object) -> int:
        decision = _authored_decision(reconciliations, int(code), change)
        if decision is not None:
            if any(one.record_id is None for one in reconciliations):
                rowless.append(int(code))
            settled.append(
                ResolvedConflict(
                    table=str(getattr(change, "name", "") or ""),
                    record_id=_rendered_key(change),
                    decision=decision,
                    conflict_code=int(code),
                )
            )
            return _OMIT if decision == "keep-left" else _REPLACE
        if not captured:
            captured["code"] = int(code)
            captured["table"] = getattr(change, "name", None)
            captured["operation"] = getattr(change, "op", None)
            captured["key"] = _conflicting_key(change)
        return _ABORT

    connection = apsw.Connection(str(target))
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN")
        try:
            apsw.Changeset.apply(delta.changeset, connection, conflict=conflict, flags=0)
        except apsw.Error as error:
            _roll_back_if_open(connection)
            if not captured:
                return AppliedChangeset(
                    detail=f"{type(error).__name__}: {error}", resolved=tuple(settled)
                )
            return AppliedChangeset(
                conflict_code=int(captured["code"]),
                conflict_table=captured.get("table"),
                conflict_operation=captured.get("operation"),
                conflict_key=captured.get("key"),
                resolved=tuple(settled),
            )
        retracted = _retract_referential_rows(connection, delta) if rowless else ()
        if retracted is None:
            _roll_back_if_open(connection)
            return AppliedChangeset(
                conflict_code=_FOREIGN_KEY_CONFLICT,
                detail=(
                    "a declared reference is still broken by a row no arriving change wrote, so "
                    "the caller's retraction cannot settle this conflict"
                ),
                resolved=tuple(settled),
            )
        settled.extend(retracted)
        if within_transaction is not None:
            refusal = within_transaction(connection)
            if refusal is not None:
                _roll_back_if_open(connection)
                return AppliedChangeset(refusal=refusal)
        connection.execute("COMMIT")
    finally:
        connection.close()
    return AppliedChangeset(resolved=tuple(settled))


# How many retraction passes one application may take. Removing a child row cannot create another
# reference to it, so one pass is what a schema with no reference cycles needs; the bound exists so
# a cycle refuses instead of looping.
_MAX_RETRACTION_PASSES = 8


def _retract_referential_rows(
    connection: apsw.Connection, delta: Delta
) -> tuple[ResolvedConflict, ...] | None:
    """Remove the arriving rows that break a declared reference, and nothing else.

    SQLite reports a foreign-key conflict without handing the callback a change, so the row cannot be
    identified from the conflict itself -- and returning ``OMIT`` for that conflict does not remove
    it: measured on this schema, the application committed and the violating row was still there. The
    row is therefore identified by the violation it left behind, and retracted by the caller's
    decision rather than by the engine's.

    The bound is what keeps that from becoming a general licence to delete: only a row the *arriving*
    delta **inserted** can be retracted, and only while SQLite itself reports it as breaking a
    reference. An insertion is the one operation whose retraction removes exactly what arrived and
    nothing else. A violation no arriving insertion accounts for -- a row the left side authored,
    which is also the case where the reconciled answer is to restore the removed parent instead --
    returns ``None``, and the caller refuses the whole application with the conflict it started from.
    """

    inserted = _delta_inserted_keys(delta)
    retracted: list[ResolvedConflict] = []
    for _ in range(_MAX_RETRACTION_PASSES):
        violations = list(connection.execute("PRAGMA foreign_key_check"))
        if not violations:
            return tuple(retracted)
        row = _retractable_row(connection, violations[0], inserted)
        if row is None:
            return None
        table, record_id, rowid = row
        connection.execute(f'DELETE FROM "{table}" WHERE rowid = ?', (rowid,))
        retracted.append(
            ResolvedConflict(
                table=table,
                record_id=record_id,
                decision="keep-left",
                conflict_code=_FOREIGN_KEY_CONFLICT,
                reason="the arriving row broke a declared reference the left side no longer satisfies",
            )
        )
    return None


def _delta_inserted_keys(delta: Delta) -> dict[str, set[str]]:
    """Return the rendered keys of every row the delta *inserted*, by table.

    Only an insertion is a retraction candidate, and the narrowness is what keeps this from becoming
    destructive. Retracting an arriving ``UPDATE`` means restoring the value it overwrote -- which
    this layer cannot do, because the stored value it would restore is the left side's -- and
    deleting the row instead would remove content the left side authored. A ``DELETE`` is not a
    candidate either: the arriving side removed that row, so there is nothing left to retract.
    """

    inserted: dict[str, set[str]] = {}
    for change in delta.operations:
        if change.operation != "INSERT":
            continue
        key = change.primary_key()
        if not key or any(value is None for value in key):
            continue
        inserted.setdefault(change.table, set()).add("/".join(str(value) for value in key))
    return inserted


def _retractable_row(
    connection: apsw.Connection, violation: tuple[Any, ...], inserted: dict[str, set[str]]
) -> tuple[str, str, int] | None:
    """Return ``(table, record_id, rowid)`` for one violating row the delta inserted, or ``None``."""

    table = str(violation[0])
    rowid = int(violation[1])
    if not inserted.get(table):
        return None
    record_id = _row_record_id(connection, table, rowid)
    if record_id is None or record_id not in inserted[table]:
        return None
    return table, record_id, rowid


def _row_record_id(connection: apsw.Connection, table: str, rowid: int) -> str | None:
    """Render one row's primary key the way the delta renders it, read by rowid."""

    columns = [
        str(row[1])
        for row in connection.execute(f'PRAGMA table_info("{table}")')
        if int(row[5] or 0) > 0
    ]
    if not columns:
        return None
    selected = ", ".join(f'"{column}"' for column in columns)
    row = next(
        iter(connection.execute(f'SELECT {selected} FROM "{table}" WHERE rowid = ?', (rowid,))),
        None,
    )
    if row is None:
        return None
    return "/".join(str(value) for value in row)


def _authored_decision(
    reconciliations: Sequence[AuthoredReconciliation], code: int, change: object
) -> AuthoredDecision | None:
    """Return the caller's decision for exactly this conflict, or ``None``.

    Which shape of decision can match is a property of the conflict, not of the caller's intent. A
    conflict the engine attributed to a row is answered only by a decision naming that exact row --
    table and rendered key both -- and a conflict the engine reported without a change is answered
    only by the row-less decision. Nothing else matches, so a decision always applies to the conflict
    the caller read and never to a neighbouring one.

    ``reconciliations`` is a sequence because one retained conflict is rarely the last one, and the
    attempt that answers the second has to carry the first -- see
    :func:`~agents_remember.memory.knowledge.merge_changeset.apply_changeset`. It is still not a
    policy: each decision answers only the row it named, and every conflict no decision names is
    refused exactly as it was.
    """

    table = getattr(change, "name", None)
    for reconciliation in reconciliations:
        if reconciliation.record_id is None:
            if table is None and code == _FOREIGN_KEY_CONFLICT:
                return reconciliation.decision
            continue
        key = _conflicting_key(change)
        if table is None or key is None or str(table) != reconciliation.table:
            continue
        if "/".join(key) != reconciliation.record_id:
            continue
        return reconciliation.decision
    return None


def _rendered_key(change: object) -> str:
    """Render one change's exact key the way the refusals render it: values joined with ``/``."""

    key = _conflicting_key(change)
    return "" if key is None else "/".join(key)


def _roll_back_if_open(connection: apsw.Connection) -> None:
    """Undo the application's transaction when the engine has left one open.

    A failed application may already have ended the transaction by itself -- an I/O or full-disk
    error rolls the statement back at that level -- and ``ROLLBACK`` against a connection with no
    transaction is an error rather than a no-op. The connection is closed immediately afterwards,
    which is what undoes anything still open, so this asks the connection whether there is anything
    to undo instead of assuming there is.
    """

    if connection.in_transaction:
        connection.execute("ROLLBACK")


def _conflicting_key(change: object) -> tuple[str, ...] | None:
    """Copy the exact primary key of the conflicting row out of one callback change.

    Read from the **old** values whenever the operation carries them, because a changeset supplies
    only the columns an operation changes: for an ``UPDATE`` the key columns are unchanged and their
    new entries are the not-supplied marker, so a key read from the new side would name no row. An
    ``INSERT`` is the one operation with no old side, and it carries every column including the key.
    """

    positions = tuple(int(position) for position in getattr(change, "pk_columns", ()) or ())
    if not positions:
        return None
    values = getattr(change, "old", None)
    if values is None:
        values = getattr(change, "new", None)
    if values is None:
        return None
    rendered: list[str] = []
    for position in positions:
        value = values[position] if position < len(values) else apsw.no_change
        if value is apsw.no_change or value is None:
            return None
        rendered.append(str(value))
    return tuple(rendered)


def replay_delta(
    delta: Delta, target_path: Path, operation: KnowledgeOperation
) -> tuple[str | None, KnowledgeRefusal | None]:
    """Apply one delta to a fresh copy of the base and require the side's whole logical dataset.

    This is the complete-coverage check, and it is deliberately a replay rather than an inspection:
    a changeset that omitted a table applies cleanly and reports success, so only the resulting
    dataset can say whether every intended change was carried.

    Returns the replayed logical digest on success, or the typed refusal that replaced it. The
    target path must not exist: the copy is taken from the base's own bytes because a base reaching
    this point is a validated closed snapshot with no journal dependency of its own.
    """

    shutil.copyfile(delta.base_path, Path(target_path))
    applied = apply_changeset(delta, target_path)
    if applied.conflicted or applied.detail:
        reason = (
            applied.detail
            if not applied.conflicted
            else f"a conflict ({applied.conflict_code}) blocked the replay"
        )
        return None, changeset_incomplete_refusal(
            operation,
            f"the {delta.side} delta could not be replayed into a fresh copy of the base: {reason}",
            expected=str(logical.dataset_identity(delta.base_path).logical_digest),
            observed=reason,
        )
    replayed = logical.dataset_identity(Path(target_path))
    expected = _read_side_identity(delta, operation)
    if isinstance(expected, KnowledgeRefusal):
        return None, expected
    if replayed.logical_digest == expected.logical_digest:
        return replayed.logical_digest, None
    return None, changeset_incomplete_refusal(
        operation,
        f"the replayed {delta.side} dataset is not the side the delta was derived from",
        expected=expected.logical_digest,
        observed=replayed.logical_digest,
    )


def _read_side_identity(
    delta: Delta, operation: KnowledgeOperation
) -> SnapshotIdentity | KnowledgeRefusal:
    """Read the side's own identity, or return the typed refusal that replaced the failure.

    A side that was selected, diffed and then became unreadable is a reportable refusal rather than
    an escaping ``OSError``: the comparison this replay exists to make cannot be made, and the
    caller has to hear that in the vocabulary it branches on.
    """

    try:
        return logical.dataset_identity(delta.side_path)
    except (apsw.Error, OSError, ValueError) as error:
        return changeset_incomplete_refusal(
            operation,
            f"the {delta.side} dataset could not be re-read for the coverage comparison: {error}",
            observed=f"{type(error).__name__}: {error}",
        )


def _materialize(changeset: bytes, generation: SchemaGeneration) -> list[MaterializedChange]:
    """Copy every operation out of a changeset cursor before it advances.

    The generation is threaded through because a changed column is reported **by position**, so
    reading one back needs the selected generation's declared column order rather than the running
    build's -- which is what ``MaterializedChange.value_of`` uses too.
    """

    return [_copy_change(change, generation) for change in apsw.Changeset.iter(changeset)]


def _copy_change(change: object, generation: SchemaGeneration) -> MaterializedChange:
    """Copy one ``TableChange`` into an owned value with the not-supplied marker translated."""

    table = str(change.name)  # type: ignore[attr-defined]
    columns = generation.columns[table]
    old = _copy_side(change.old)  # type: ignore[attr-defined]
    new = _copy_side(change.new)  # type: ignore[attr-defined]
    carrying = new if new is not None else old
    return MaterializedChange(
        table=table,
        operation=str(change.op),  # type: ignore[attr-defined]
        primary_key_columns=tuple(
            int(position)
            for position in change.pk_columns  # type: ignore[attr-defined]
        ),
        old=old,
        new=new,
        columns=tuple(columns),
        supplied=frozenset(
            column
            for column, value in zip(columns, carrying or (), strict=True)
            if value is not NOT_SUPPLIED
        ),
    )


def _copy_side(side: object) -> tuple[Any, ...] | None:
    """Copy one side of a change, translating APSW's not-supplied marker."""

    if side is None:
        return None
    return tuple(NOT_SUPPLIED if value is apsw.no_change else value for value in side)  # type: ignore[union-attr]


def _unapplied_count(error: apsw.Error) -> int | None:
    """Return the count of rows SQLite reported it could not apply, when it reported one.

    ``apsw.SQLITE_CHANGESET_ABORT`` raises without a count, and some failures raise before any
    conflict is counted at all. The count is therefore optional, and "SQLite did not report one" is
    a different fact from zero.
    """

    for argument in getattr(error, "args", ()):
        if isinstance(argument, int) and not isinstance(argument, bool):
            return int(argument)
    return None
