"""The structural preflight a common-base merge runs before any session exists.

This module answers one question about one database: *is its declared structure exactly the
supported schema generation?* It answers it from SQLite's own catalog -- tables and their options,
ordered columns with types, nullability and primary-key positions, foreign keys, indexes, triggers
and ``user_version`` -- rather than from a version string, because two databases can agree on
``user_version`` and disagree about a column type, a foreign key or a trigger.

**Why this runs before the session.** SQLite's changeset application can silently skip a table it
cannot match: the changeset carries no operation for a table that was never attached, and a table
that exists on only one side is exactly the schema disagreement this preflight refuses. A merge that
produced a green result over such a pair would have dropped a side's entire change set, so each input
is compared against the declared generation before a session exists.

One comparison, per input, against the declared generation -- not a second pass comparing inputs to
each other. Every accepted input is structurally identical to that one declaration, so the pairwise
pass would be unreachable: it could only fire for an input the per-input pass had already refused.
:func:`compare_structures` is public because it *is* that per-input comparison, and it is exposed so
a caller can read the difference it found.

**What it deliberately does not do.** It does not repair, migrate, add or drop anything, and it
does not ignore an unfamiliar table to obtain a green result: an input carrying a table outside the
declared manifest is not a dataset this schema wrote, so it is refused rather than merged around.

The declared structure is derived by creating the DDL in a private in-memory database and
introspecting the result, so the comparison runs against the same source of truth that creates a
database rather than against a second hand-written description of it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import apsw

from agents_remember.memory.knowledge.connection import open_read_only_database
from agents_remember.memory.knowledge.export_refusals import unsupported_schema_refusal
from agents_remember.memory.knowledge.merge_refusals import (
    missing_required_table_refusal,
    schema_mismatch_refusal,
)
from agents_remember.memory.knowledge.refusals import KnowledgeStorageError
from agents_remember.memory.knowledge.schema_generations import (
    SchemaGeneration,
    create_schema_statements,
    generation_of_database,
    registered_version_listing,
)
from agents_remember.models.knowledge.result import KnowledgeOperation, KnowledgeRefusal

# SQLite's own bookkeeping tables. They are not part of any dataset's declared structure and are
# excluded from the comparison by name; every other table is reported, so an extra one cannot hide.
_INTERNAL_TABLE_PREFIX = "sqlite_"


@dataclass(frozen=True)
class TableStructure:
    """One canonical table's declared structure, as the catalog reports it.

    ``columns`` carries ``(name, declared type, not-null, primary-key position)`` in declared
    order, so a reordered, renamed, retyped, weakened or re-keyed column is a difference here.
    """

    table: str
    columns: tuple[tuple[str, str, bool, int], ...]
    foreign_keys: frozenset[tuple[int, str, str, str, str, str, str, str]]
    indexes: frozenset[tuple[str, bool, str, bool, tuple[int, ...]]]
    triggers: frozenset[tuple[str, str, str]]
    strict: bool
    without_rowid: bool


@dataclass(frozen=True)
class DatabaseStructure:
    """One database's whole declared structure, plus what it declares outside the manifest.

    ``generation`` is the generation the structure was **read under**: the one the dataset itself
    declares, selected before anything was classified. It is carried rather than re-derived, because
    a structure that has already dropped the tables it did not recognise into ``extra_tables`` can no
    longer describe a foreign generation -- which is exactly the shape a partially threaded preflight
    produced: a version-1 input validated against generation 2 and then asked for generation 2's
    tables.
    """

    generation: SchemaGeneration
    user_version: int
    declared_tables: Mapping[str, TableStructure]
    extra_tables: tuple[str, ...]


def declared_generation(database_path: Path) -> SchemaGeneration:
    """Select the generation one input *declares*, without comparing anything yet.

    An open database declares its generation through ``PRAGMA user_version`` alone, so this is a
    registry lookup over that version and nothing else. A version the registry does not contain is
    refused here as an unsupported generation -- it is never re-read under another generation to
    obtain a green result.
    """

    connection = open_read_only_database(database_path)
    try:
        return generation_of_database(connection)
    finally:
        connection.close()


def selected_generation(
    databases: Mapping[str, Path], operation: KnowledgeOperation
) -> SchemaGeneration | KnowledgeRefusal:
    """Resolve every input's declared generation and require them to agree (requirement 6.1).

    The preflight reads each input's generation **first**, and when the inputs disagree it refuses
    before any session exists rather than picking one input's generation as the winner, migrating an
    input, or proceeding. When they agree, that agreed generation is the operation's selected
    generation: a v1/v1/v1 merge on the generation-2 build therefore proceeds under generation 1,
    which is what keeps requirement 5.1 true through the mechanism meant to serve it.
    """

    selected: SchemaGeneration | None = None
    first_role: str | None = None
    for role in ("base", "left", "right"):
        path = Path(databases[role])
        try:
            declared = declared_generation(path)
        except KnowledgeStorageError as error:
            return unsupported_schema_refusal(
                operation,
                f"the {role} input does not declare a schema generation this build supports: "
                f"{error}",
                expected=registered_version_listing(),
                observed=str(_observed_user_version(path)),
            )
        except (apsw.Error, OSError) as error:
            return schema_mismatch_refusal(
                operation, f"the {role} input could not be read: {error}"
            )
        if selected is None:
            selected, first_role = declared, role
            continue
        if declared != selected:
            return schema_mismatch_refusal(
                operation,
                f"the {role} input declares user_version {declared.user_version} while the "
                f"{first_role} input declares {selected.user_version}. Datasets of different "
                "generations are not merged: an input is never migrated and one input's generation "
                "is never chosen as the winner",
                expected=str(selected.user_version),
                observed=str(declared.user_version),
            )
    if selected is None:  # pragma: no cover - the three roles are always supplied
        raise KnowledgeStorageError("a merge names three inputs; none was supplied")
    return selected


def _observed_user_version(database_path: Path) -> object:
    """Return what one file records as its generation, for a refusal's ``observed`` fact."""

    try:
        connection = open_read_only_database(database_path)
    except (apsw.Error, OSError):
        return "<unreadable>"
    try:
        return int(next(iter(connection.execute("PRAGMA user_version")))[0])
    except (apsw.Error, StopIteration):  # pragma: no cover - a readable file always answers
        return "<unreadable>"
    finally:
        connection.close()


def declared_structure(generation: SchemaGeneration) -> DatabaseStructure:
    """Return the structure one schema generation actually creates.

    The structure is derived by creating that generation's DDL in a private in-memory database and
    introspecting the result, so the comparison runs against the same source of truth that creates a
    database rather than against a second hand-written description of it.
    """

    connection = apsw.Connection(":memory:")
    try:
        for statement in create_schema_statements(generation):
            connection.execute(statement)
        connection.execute(f"PRAGMA user_version = {generation.user_version}")
        return read_structure(connection, generation)
    finally:
        connection.close()


def read_structure(connection: apsw.Connection, generation: SchemaGeneration) -> DatabaseStructure:
    """Return the declared structure of one open database, read under one generation.

    Every table is classified against **the selected generation's** manifest, not against the
    running build's. That is the whole of requirement 6.2's threading: a version-1 input read under
    generation 1 has no table it did not recognise, and a generation-2 input read under generation 1
    reports generation 2's tables as extra rather than silently accepting them.
    """

    user_version = int(next(iter(connection.execute("PRAGMA user_version")))[0])
    names = sorted(
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_schema WHERE type = 'table'")
        if not str(row[0]).startswith(_INTERNAL_TABLE_PREFIX)
    )
    declared = {name: _read_table(connection, name) for name in names if name in generation.tables}
    extra = tuple(name for name in names if name not in generation.tables)
    return DatabaseStructure(
        generation=generation,
        user_version=user_version,
        declared_tables=declared,
        extra_tables=extra,
    )


def read_database_structure(database_path: Path, generation: SchemaGeneration) -> DatabaseStructure:
    """Return the declared structure of one database file, read-only, under one generation."""

    connection = open_read_only_database(database_path)
    try:
        return read_structure(connection, generation)
    finally:
        connection.close()


def require_supported_structure(
    database_path: Path,
    operation: KnowledgeOperation,
    *,
    role: str,
    generation: SchemaGeneration | None = None,
) -> KnowledgeRefusal | None:
    """Return the refusal for one input whose structure is not the supported generation, or None.

    ``role`` names which of the merge's three positions the input occupies, so a refusal says which
    dataset disagreed rather than only that one did. ``generation`` is the operation's **selected**
    generation; when it is omitted the input's own declared generation is selected here, which is
    the same read requirement 6.1 performs, and keeps a single-input caller honest.
    """

    try:
        selected = generation if generation is not None else declared_generation(database_path)
    except KnowledgeStorageError as error:
        return unsupported_schema_refusal(
            operation,
            f"the {role} input does not declare a schema generation this build supports: {error}",
            expected=registered_version_listing(),
            observed=str(_observed_user_version(database_path)),
        )
    except (apsw.Error, OSError) as error:
        return schema_mismatch_refusal(operation, f"the {role} input could not be read: {error}")
    try:
        observed = read_database_structure(database_path, selected)
    except (apsw.Error, OSError) as error:
        return schema_mismatch_refusal(operation, f"the {role} input could not be read: {error}")
    return compare_structures(
        declared_structure(selected), observed, operation, role=role, generation=selected
    )


def compare_structures(
    expected: DatabaseStructure,
    observed: DatabaseStructure,
    operation: KnowledgeOperation,
    *,
    role: str,
    generation: SchemaGeneration | None = None,
) -> KnowledgeRefusal | None:
    """Return the first structural difference between two declared structures, or None.

    The checks run most-structural first: a missing canonical table, then a table this schema
    generation does not declare, then the per-table column, key, index and trigger comparison. The
    first difference is returned rather than a list, because a caller acts on the reason the merge
    did not start and a later difference in the same input would not change that.

    The manifest and the column map come from the **selected generation** -- the one both structures
    were read under -- rather than from the running build.
    """

    selected = generation if generation is not None else expected.generation
    if observed.user_version != expected.user_version:
        return schema_mismatch_refusal(
            operation,
            f"the {role} input declares user_version {observed.user_version}",
            expected=str(expected.user_version),
            observed=str(observed.user_version),
        )
    missing = [table for table in selected.tables if table not in observed.declared_tables]
    if missing:
        return missing_required_table_refusal(
            operation, missing[0], observed=f"absent from the {role} input"
        )
    for table in observed.extra_tables:
        return schema_mismatch_refusal(
            operation,
            f"the {role} input carries a table the supported schema does not declare",
            table=table,
            expected="|".join(selected.tables),
            observed=table,
        )
    for table in selected.tables:
        difference = _compare_table(operation, role, table, (expected, observed), selected)
        if difference is not None:
            return difference
    return None


def _compare_table(
    operation: KnowledgeOperation,
    role: str,
    table: str,
    pair: tuple[DatabaseStructure, DatabaseStructure],
    generation: SchemaGeneration,
) -> KnowledgeRefusal | None:
    """Return the first difference inside one canonical table, or None.

    ``pair`` is ``(expected, observed)``. They travel together because every check below compares
    one against the other, and passing them as one value keeps this function inside the argument
    limit the structural rules enforce.
    """

    expected, observed = pair
    want = expected.declared_tables[table]
    have = observed.declared_tables[table]
    expected_columns = generation.columns[table]
    observed_columns = tuple(column[0] for column in have.columns)
    if observed_columns != expected_columns:
        return schema_mismatch_refusal(
            operation,
            f"the {role} input declares different columns for {table}",
            table=table,
            expected=", ".join(expected_columns),
            observed=", ".join(observed_columns),
        )
    return (
        _compare_column_details(operation, role, table, want, have)
        or _compare_foreign_keys(operation, role, table, want, have)
        or _compare_indexes(operation, role, table, want, have)
        or _compare_triggers(operation, role, table, want, have)
        or _compare_options(operation, role, table, want, have)
    )


def _compare_column_details(
    operation: KnowledgeOperation,
    role: str,
    table: str,
    want: TableStructure,
    have: TableStructure,
) -> KnowledgeRefusal | None:
    """Compare ordered column types, nullability and primary-key positions."""

    for expected_column, observed_column in zip(want.columns, have.columns, strict=True):
        if observed_column == expected_column:
            continue
        return schema_mismatch_refusal(
            operation,
            f"column {expected_column[0]} of {table} differs in the {role} input",
            table=table,
            expected=_render_column(expected_column),
            observed=_render_column(observed_column),
        )
    return None


def _compare_foreign_keys(
    operation: KnowledgeOperation,
    role: str,
    table: str,
    want: TableStructure,
    have: TableStructure,
) -> KnowledgeRefusal | None:
    """Compare declared foreign keys, including their deferral and delete actions."""

    if have.foreign_keys == want.foreign_keys:
        return None
    return schema_mismatch_refusal(
        operation,
        f"the {role} input declares different foreign keys for {table}",
        table=table,
        expected=_render_foreign_keys(want.foreign_keys),
        observed=_render_foreign_keys(have.foreign_keys),
    )


def _compare_indexes(
    operation: KnowledgeOperation,
    role: str,
    table: str,
    want: TableStructure,
    have: TableStructure,
) -> KnowledgeRefusal | None:
    """Compare unique indexes and named indexes, by name and by structure."""

    if have.indexes == want.indexes:
        return None
    return schema_mismatch_refusal(
        operation,
        f"the {role} input declares different indexes for {table}",
        table=table,
        expected=_render_indexes(want.indexes),
        observed=_render_indexes(have.indexes),
    )


def _compare_triggers(
    operation: KnowledgeOperation,
    role: str,
    table: str,
    want: TableStructure,
    have: TableStructure,
) -> KnowledgeRefusal | None:
    """Compare the immutability triggers by name and by body."""

    if have.triggers == want.triggers:
        return None
    return schema_mismatch_refusal(
        operation,
        f"the {role} input declares different triggers for {table}",
        table=table,
        expected=_render_triggers(want.triggers),
        observed=_render_triggers(have.triggers),
    )


def _compare_options(
    operation: KnowledgeOperation,
    role: str,
    table: str,
    want: TableStructure,
    have: TableStructure,
) -> KnowledgeRefusal | None:
    """Compare the table's declared options: STRICT and WITHOUT ROWID."""

    if (have.strict, have.without_rowid) == (want.strict, want.without_rowid):
        return None
    return schema_mismatch_refusal(
        operation,
        f"the {role} input declares different table options for {table}",
        table=table,
        expected=f"strict={want.strict} without_rowid={want.without_rowid}",
        observed=f"strict={have.strict} without_rowid={have.without_rowid}",
    )


def _read_table(connection: apsw.Connection, table: str) -> TableStructure:
    """Read one table's declared structure from the catalog."""

    options = _table_options(connection, table)
    return TableStructure(
        table=table,
        columns=tuple(
            (str(row[1]), str(row[2]), bool(row[3]), int(row[5]))
            for row in connection.execute(f"PRAGMA table_xinfo({table})")
        ),
        foreign_keys=frozenset(
            tuple(row)  # type: ignore[arg-type]
            for row in connection.execute(f"PRAGMA foreign_key_list({table})")
        ),
        indexes=frozenset(
            (
                str(row[1]),
                bool(row[2]),
                str(row[3]),
                bool(row[4]),
                _index_columns(connection, str(row[1])),
            )
            for row in connection.execute(f"PRAGMA index_list({table})")
        ),
        triggers=frozenset(
            (str(row[0]), str(row[1]), _normalized_sql(row[2]))
            for row in connection.execute(
                "SELECT name, tbl_name, sql FROM sqlite_schema "
                "WHERE type = 'trigger' AND tbl_name = ?",
                (table,),
            )
        ),
        strict=bool(options.get("strict", 0)),
        without_rowid=bool(options.get("wr", 0)),
    )


def _table_options(connection: apsw.Connection, table: str) -> dict[str, Any]:
    """Return one table's option row from ``pragma_table_list``, keyed by column name."""

    cursor = connection.execute(f"PRAGMA table_list({table})")
    try:
        names = [str(description[0]) for description in cursor.get_description()]
        row = next(iter(cursor), None)
    finally:
        cursor.close()
    if row is None:
        return {}
    return {name: value for name, value in zip(names, tuple(row), strict=True)}


def _index_columns(connection: apsw.Connection, index_name: str) -> tuple[int, ...]:
    """Return the ordered key columns one index covers.

    The index *name* is local naming rather than dataset structure, so the compared fact is the
    shape: which declared columns the index covers, in order. A named index is separately compared
    by name inside :func:`_read_table`'s index tuple, because a dropped or renamed declared index is
    a real schema difference while an auto-index's generated name is not. ``key=1`` marks the key
    columns; the trailing rowid entries SQLite appends are not part of that shape.
    """

    return tuple(
        int(row[1])
        for row in connection.execute(f"PRAGMA index_xinfo({index_name})")
        if int(row[5]) == 1
    )


def _normalized_sql(statement: object) -> str:
    """Return one catalog SQL body with whitespace collapsed, for a stable comparison."""

    return " ".join(str(statement).split())


def _render_column(column: tuple[str, str, bool, int]) -> str:
    """Render one column's compared facts as one line."""

    name, declared_type, not_null, key_position = column
    return f"{name} {declared_type} not_null={not_null} pk={key_position}"


def _render_foreign_keys(keys: frozenset[tuple[int, str, str, str, str, str, str, str]]) -> str:
    """Render a table's foreign keys in a stable order."""

    return "; ".join(
        " ".join(str(part) for part in key)
        for key in sorted(keys, key=lambda item: tuple(str(part) for part in item))
    )


def _render_indexes(indexes: frozenset[tuple[str, bool, str, bool, tuple[int, ...]]]) -> str:
    """Render a table's indexes in a stable order."""

    return "; ".join(
        " ".join(str(part) for part in index)
        for index in sorted(indexes, key=lambda item: (item[0], item[1]))
    )


def _render_triggers(triggers: frozenset[tuple[str, str, str]]) -> str:
    """Render a table's triggers in a stable order."""

    return "; ".join(
        f"{name} ({body})" for name, _table, body in sorted(triggers, key=lambda item: item[0])
    )
