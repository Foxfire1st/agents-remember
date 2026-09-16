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
from agents_remember.models.knowledge.candidate import SnapshotIdentity
from agents_remember.models.knowledge.repository import RepositoryIdentity

# The declared primary key of each canonical table, as the DDL declares it. Row order inside a
# table is this key's order, so two databases compared here agree on ordering without either of
# them being asked how it happened to store its rows.
#
# These are the DDL's ``PRIMARY KEY`` column lists, which are not always the tables' leading
# columns: ``invariant_revision`` keys ``(repository_id, revision_id)`` while ``invariant_id`` sits
# between those two columns. A row is ordered by its key, not by the order its columns were
# declared in, and :func:`_require_declared_keys` is what keeps this table honest against the
# manifest rather than against an assumption about column order.
PRIMARY_KEYS: Mapping[str, tuple[str, ...]] = {
    "repository": ("repository_id",),
    "invariant": ("repository_id", "invariant_id"),
    "invariant_revision": ("repository_id", "revision_id"),
    "invariant_predecessor": (
        "repository_id",
        "invariant_id",
        "child_revision_id",
        "parent_revision_id",
    ),
    "family": ("repository_id", "family_id"),
    "family_revision": ("repository_id", "revision_id"),
    "family_predecessor": (
        "repository_id",
        "family_id",
        "child_revision_id",
        "parent_revision_id",
    ),
    "source_anchor": ("repository_id", "anchor_id"),
    "family_member": ("repository_id", "member_id"),
    "realization_claim": ("repository_id", "claim_id"),
}

# The columns whose stored text is a typed JSON value. They are decoded at this portable
# boundary; everything else is compared as the exact stored text.
JSON_COLUMNS: Mapping[str, frozenset[str]] = {
    "invariant": frozenset({"label_provenance"}),
    "invariant_revision": frozenset({"conditions", "exclusions", "provenance"}),
    "family": frozenset({"label_provenance"}),
    "family_revision": frozenset({"provenance"}),
    "source_anchor": frozenset({"source_identity", "locator", "provenance"}),
    "family_member": frozenset({"provenance"}),
    "realization_claim": frozenset({"provenance"}),
}

_BODY_VERSION = "ar-knowledge-logical-body/v1"


def logical_digest(connection: apsw.Connection, schema_name: str) -> str:
    """Return the canonical logical digest of the dataset this connection holds open."""

    return sha256_digest(logical_body(connection, schema_name))


def logical_body(connection: apsw.Connection, schema_name: str) -> dict[str, Any]:
    """Return the canonical logical body: the exact structure the digest seals.

    Reading it is a plain scan inside whatever transaction the caller holds, so a mutation can
    compare the dataset before and after its own writes inside one transaction rather than trusting
    a value it computed earlier.
    """

    _require_declared_keys()
    return {
        "body_version": _BODY_VERSION,
        "schema": schema_name,
        "user_version": schema.SCHEMA_USER_VERSION,
        "schema_fingerprint": schema.schema_fingerprint(),
        "tables": {table: _rows_of(connection, table) for table in schema.CANONICAL_TABLES},
    }


def snapshot_identity(
    connection: apsw.Connection, repository: RepositoryIdentity, schema_name: str
) -> SnapshotIdentity:
    """Return the logical identity of the dataset for one bound namespace."""

    return SnapshotIdentity(
        repository_id=repository.repository_id,
        schema_version=schema_name,
        logical_digest=logical_digest(connection, schema_name),
    )


def dataset_identity(database_path: Path) -> SnapshotIdentity:
    """Read one database file's logical identity through a connection that cannot write it.

    A file at a path is not a dataset until it is read as one: this opens it read-only, validates
    the declared schema generation and the bound namespace, and returns the identity those two
    facts scope. Every failure is a storage error rather than a returned value, so a caller that
    is comparing identities can name the path it could not read instead of treating it as empty.
    """

    connection = open_read_only_database(database_path)
    try:
        schema = inspect_schema(connection)
        repository = bound_repository(connection)
        if repository is None:
            raise KnowledgeStorageError(
                f"the database {database_path} holds no repository namespace row, so it is not a "
                "knowledge dataset this code can address"
            )
        return snapshot_identity(connection, repository, schema.schema_name)
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


def _rows_of(connection: apsw.Connection, table: str) -> list[dict[str, Any]]:
    """Return every row of ``table`` in primary-key order, keyed by declared column name."""

    columns = schema.CANONICAL_COLUMNS[table]
    keys = PRIMARY_KEYS[table]
    rows = [tuple(row) for row in connection.execute(f"SELECT {', '.join(columns)} FROM {table}")]
    rows.sort(key=lambda row: tuple(row[columns.index(key)] for key in keys))
    json_columns = JSON_COLUMNS.get(table, frozenset())
    return [
        {
            column: _cell(value, is_json=column in json_columns)
            for column, value in zip(columns, row, strict=True)
        }
        for row in rows
    ]


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


def _require_declared_keys() -> None:
    """Refuse a manifest whose declared key names a column the table does not have.

    The keys above and the DDL's ``PRIMARY KEY`` lists are two statements of one fact, and only the
    DDL can be checked from here. What this verifies is the weaker property the encoder actually
    depends on -- every key column exists in the table's declared column list -- so a future schema
    edit cannot leave this module sorting by a column the manifest no longer carries.
    """

    for table in schema.CANONICAL_TABLES:
        declared = schema.CANONICAL_COLUMNS[table]
        unknown = [key for key in PRIMARY_KEYS[table] if key not in declared]
        if unknown:
            raise KnowledgeStorageError(
                f"the row order declared for {table} names {unknown}, which its column manifest "
                f"{declared} does not carry"
            )
        if len(set(PRIMARY_KEYS[table])) != len(PRIMARY_KEYS[table]):
            raise KnowledgeStorageError(f"the row order declared for {table} repeats a column")
