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
from agents_remember.models.knowledge.merge import ChangeOperationKind
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

# The one conflict action this package ever returns. ``OMIT`` and ``REPLACE`` are intentionally not
# named anywhere in the merge path: dropping a conflicting operation would publish a candidate that
# silently lost a side's change, and replacing one side's row with the other's is the automatic
# resolution this operation does not implement.
_ABORT = int(apsw.SQLITE_CHANGESET_ABORT)


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
        """Return the operation's key values, read from whichever side carries them."""

        side = self.new if self.new is not None else self.old
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
    """

    conflict_code: int | None = None
    conflict_table: str | None = None
    conflict_operation: str | None = None
    conflict_key: tuple[str, ...] | None = None
    detail: str = ""

    @property
    def conflicted(self) -> bool:
        """Whether the application hit a conflict callback at all."""

        return self.conflict_code is not None


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


def apply_changeset(delta: Delta, target_path: Path) -> AppliedChangeset:
    """Apply one delta to one private target with a conflict callback that always aborts.

    The callback copies the available facts -- the conflict code, the table, the operation and the
    **exact key of the row it could not apply** -- and returns ``SQLITE_CHANGESET_ABORT``
    unconditionally. The first blocking conflict is captured and the whole application is rolled
    back, rather than continuing with ``OMIT`` to collect a cosmetically complete list.

    The key is copied here rather than looked up afterwards for two reasons: an APSW ``TableChange``
    expires when the iterator advances, and this is the only place the engine names the row it
    actually refused. A later search of the changeset finds *an* operation on the table, which is a
    different fact from the operation that conflicted.
    """

    captured: dict[str, Any] = {}
    target = Path(target_path)

    def conflict(code: int, change: object) -> int:
        if not captured:
            captured["code"] = int(code)
            captured["table"] = getattr(change, "name", None)
            captured["operation"] = getattr(change, "op", None)
            captured["key"] = _conflicting_key(change)
        return _ABORT

    connection = apsw.Connection(str(target))
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        apsw.Changeset.apply(delta.changeset, connection, conflict=conflict, flags=0)
    except apsw.Error as error:
        if not captured:
            return AppliedChangeset(detail=f"{type(error).__name__}: {error}")
        return AppliedChangeset(
            conflict_code=int(captured["code"]),
            conflict_table=captured.get("table"),
            conflict_operation=captured.get("operation"),
            conflict_key=captured.get("key"),
        )
    finally:
        connection.close()
    return AppliedChangeset()


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
