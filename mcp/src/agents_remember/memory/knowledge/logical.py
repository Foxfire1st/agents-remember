"""The canonical logical identity of one knowledge dataset.

Two databases are the same knowledge exactly when their canonical logical bodies are equal, and
this module owns that body. It serializes the supported schema version and every canonical table
-- including empty ones -- in the declared manifest order, each row ordered by its declared primary
key and each field in the declared column order. Typed JSON columns are decoded to JSON values, so
a difference that exists only in the stored text (key order, whitespace, Unicode escaping) is not
a difference in knowledge.

What the digest deliberately excludes is as load-bearing as what it includes: SQLite page order,
file paths, journal state, header counters, mtimes, Git commits, ledger rows, caches and rendered
views are all outside it. A candidate therefore has a content identity that survives being backed
up, copied and reopened anywhere.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import apsw

from agents_remember.kernel.canonical_json import decoded_json, sha256_digest
from agents_remember.memory.knowledge import schema
from agents_remember.memory.knowledge.connection import (
    fetch_one,
    inspect_schema,
    open_read_only_database,
)
from agents_remember.memory.knowledge.refusals import KnowledgeStorageError
from agents_remember.memory.knowledge.schema_generations import (
    SchemaGeneration,
    generation_for_name,
    generation_of_database,
)
from agents_remember.models.knowledge.candidate import SnapshotIdentity
from agents_remember.models.knowledge.repository import RepositoryIdentity

# Generation 1's declared primary keys and typed-JSON columns, kept importable under this module's
# shipped names so existing readers do not break. They are **generation 1's** data, re-exported
# from the module that owns generation 1's pinned structure; a generation-aware reader reads
# ``generation.primary_keys`` / ``generation.json_columns`` instead, which is what the encoder now
# does. The distinction matters: a site that keeps reading these globals for a table generation 2
# added would find no entry at all, and a site that reads them for one of the first ten names gets
# the right answer only because generation 2 appends without altering them.
PRIMARY_KEYS: Mapping[str, tuple[str, ...]] = schema.PRIMARY_KEYS
JSON_COLUMNS: Mapping[str, frozenset[str]] = schema.JSON_COLUMNS

_BODY_VERSION = "ar-knowledge-logical-body/v1"


def resolve_generation(generation: SchemaGeneration | str) -> SchemaGeneration:
    """Return the generation record an encoder caller selected, however it spelled it.

    Dispatch -- deciding *which* generation a dataset is -- happens in
    :mod:`…schema_generations` and reads the dataset. This function does not decide anything: it
    resolves a caller's already-selected generation to the record the encoder needs. A name that
    no registered generation declares is refused rather than approximated, so a caller cannot
    obtain a digest under a generation the registry does not contain.
    """

    if isinstance(generation, SchemaGeneration):
        return generation
    resolved = generation_for_name(generation)
    if resolved is None:
        raise KnowledgeStorageError(
            f"{generation!r} is not a schema generation this build supports, so no logical body "
            "can be assembled under it"
        )
    return resolved


def logical_digest(connection: apsw.Connection, generation: SchemaGeneration | str) -> str:
    """Return the canonical logical digest of the dataset this connection holds open."""

    return sha256_digest(logical_body(connection, generation))


def logical_body(connection: apsw.Connection, generation: SchemaGeneration | str) -> dict[str, Any]:
    """Return the canonical logical body: the exact structure the digest seals.

    Reading it is a plain scan inside whatever transaction the caller holds, so a mutation can
    compare the dataset before and after its own writes inside one transaction rather than trusting
    a value it computed earlier.
    """

    selected = resolve_generation(generation)
    _require_declared_keys(selected)
    return logical_body_from_tables(
        selected, {table: _rows_of(connection, selected, table) for table in selected.tables}
    )


def logical_body_from_tables(
    generation: SchemaGeneration | str, tables: Mapping[str, Any]
) -> dict[str, Any]:
    """Wrap an already-encoded table mapping in the canonical logical body of ITS OWN generation.

    The body is one structure with one encoder, and this is that structure stated once. The
    portable export needs to seal a table mapping it decoded from an artifact rather than scanned
    from a database, and the only acceptable way for it to obtain the same digest is to build the
    same body through the same function -- a second assembly of ``body_version``/``schema``/
    ``user_version``/``schema_fingerprint`` is a second digest definition, and a difference between
    the two would make an export that cannot be re-imported.

    Every field that used to come from this module's build-time globals now comes from the selected
    generation, and the table mapping is projected into **that** generation's declared table order.
    That projection is what keeps a version-1 dataset's ten-table body -- including its empty
    tables -- byte-identical after a generation 2 exists, and what makes a generation-2 dataset
    serialize generation 2's own tables instead of silently omitting them.
    """

    selected = resolve_generation(generation)
    _require_declared_keys(selected)
    absent = [table for table in selected.tables if table not in tables]
    if absent:
        raise KnowledgeStorageError(
            f"the table mapping does not carry {', '.join(absent)}, which generation "
            f"{selected.schema_name} declares. A body that omits part of its own manifest is a "
            "weaker check wearing this one's name, so it is refused rather than digested."
        )
    return {
        "body_version": _BODY_VERSION,
        "schema": selected.schema_name,
        "user_version": selected.user_version,
        "schema_fingerprint": selected.fingerprint,
        "tables": {table: tables[table] for table in selected.tables},
    }


def logical_digest_of_tables(generation: SchemaGeneration | str, tables: Mapping[str, Any]) -> str:
    """Return the canonical logical digest of an already-encoded table mapping."""

    return sha256_digest(logical_body_from_tables(generation, tables))


def snapshot_identity(
    connection: apsw.Connection,
    repository: RepositoryIdentity,
    generation: SchemaGeneration | str,
) -> SnapshotIdentity:
    """Return the logical identity of the dataset for one bound namespace."""

    selected = resolve_generation(generation)
    return SnapshotIdentity(
        repository_id=repository.repository_id,
        schema_version=selected.schema_name,
        logical_digest=logical_digest(connection, selected),
    )


def dataset_identity(database_path: Path) -> SnapshotIdentity:
    """Read one database file's logical identity through a connection that cannot write it.

    A file at a path is not a dataset until it is read as one: this opens it read-only, selects the
    declared schema generation *from the dataset*, validates the bound namespace, and returns the
    identity those two facts scope. Every failure is a storage error rather than a returned value,
    so a caller that is comparing identities can name the path it could not read instead of treating
    it as empty.
    """

    connection = open_read_only_database(database_path)
    try:
        generation = generation_of_database(connection)
        inspect_schema(connection)
        repository = bound_repository(connection)
        if repository is None:
            raise KnowledgeStorageError(
                f"the database {database_path} holds no repository namespace row, so it is not a "
                "knowledge dataset this code can address"
            )
        return snapshot_identity(connection, repository, generation)
    finally:
        connection.close()


def bound_repository(connection: apsw.Connection) -> RepositoryIdentity | None:
    """Return the one repository row a knowledge dataset is bound to, or ``None``.

    A dataset addressed by this package is bound to exactly one namespace: the store's own
    initialization refuses a rebind, so more than one row means the file is not a dataset this
    code wrote. That ambiguity is refused rather than resolved by picking a row.
    """

    rows = [
        tuple(row)
        for row in connection.execute("SELECT repository_id, authority_home FROM repository")
    ]
    if not rows:
        return None
    if len(rows) > 1:
        raise KnowledgeStorageError(
            f"a knowledge dataset must be bound to exactly one repository namespace; this one "
            f"holds {len(rows)} rows"
        )
    return RepositoryIdentity(repository_id=str(rows[0][0]), authority_home=str(rows[0][1]))


def require_bound_repository(connection: apsw.Connection, repository_id: str) -> RepositoryIdentity:
    """Return the stored namespace row, refusing a database that is not bound to it.

    A candidate database with no repository row, or one bound elsewhere, is not the destination the
    admission described. The refusal is a storage error rather than a returned refusal code because
    the caller reaches this only after `change_candidate` has already checked the same fact for its
    typed receipt; this is the second, defensive read.
    """

    row = fetch_one(
        connection,
        "SELECT repository_id, authority_home FROM repository WHERE repository_id = ?",
        (repository_id,),
    )
    if row is None:
        raise KnowledgeStorageError(
            f"the candidate database is not bound to repository namespace {repository_id}"
        )
    return RepositoryIdentity(repository_id=str(row[0]), authority_home=str(row[1]))


def _rows_of(
    connection: apsw.Connection, generation: SchemaGeneration, table: str
) -> list[dict[str, Any]]:
    """Return every row of ``table`` in primary-key order, keyed by declared column name."""

    columns = generation.columns[table]
    keys = generation.primary_keys[table]
    rows = [tuple(row) for row in connection.execute(f"SELECT {', '.join(columns)} FROM {table}")]
    rows.sort(key=lambda row: tuple(row[columns.index(key)] for key in keys))
    json_columns = generation.json_columns.get(table, frozenset())
    return [
        {
            column: _cell(value, is_json=column in json_columns)
            for column, value in zip(columns, row, strict=True)
        }
        for row in rows
    ]


def cell_value(value: Any, *, is_json: bool) -> Any:
    """Return one stored cell's canonical logical value, decoding a typed JSON column.

    This is the public spelling of the decoder every reader of a stored row uses, so a read page
    and this module's digest cannot disagree about what a cell means: there is one function that
    decides it, and a second reader calls it rather than reimplementing it.
    """

    return _cell(value, is_json=is_json)


def _cell(value: Any, *, is_json: bool) -> Any:
    """Return one cell's canonical logical value, decoding a typed JSON column."""

    if value is None:
        return None
    if not is_json:
        return str(value)
    try:
        return decoded_json(str(value))
    except ValueError as error:
        raise KnowledgeStorageError(
            f"a typed JSON column does not hold unambiguous JSON: {error}"
        ) from error


def _require_declared_keys(generation: SchemaGeneration) -> None:
    """Refuse a manifest whose declared key names a column the table does not have.

    The generation's key tuples and its DDL's ``PRIMARY KEY`` lists are two statements of one fact,
    and only the DDL can be checked from here. What this verifies is the weaker property the encoder
    actually depends on -- every key column exists in the table's declared column list -- so a
    future schema edit cannot leave this module sorting by a column the manifest no longer carries.
    It is checked against the **selected** generation, so a generation whose own key registry
    disagrees with its own columns is refused rather than silently encoded.
    """

    for table in generation.tables:
        declared = generation.columns[table]
        unknown = [key for key in generation.primary_keys[table] if key not in declared]
        if unknown:
            raise KnowledgeStorageError(
                f"the row order declared for {table} names {unknown}, which its column manifest "
                f"{declared} does not carry"
            )
        if len(set(generation.primary_keys[table])) != len(generation.primary_keys[table]):
            raise KnowledgeStorageError(f"the row order declared for {table} repeats a column")
