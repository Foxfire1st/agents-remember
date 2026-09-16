"""The connection, pragma and schema-validation boundary of the knowledge store.

Every decision about *what a connection is* lives here: which pragmas are set and verified, how
an empty database becomes this schema generation, and how an existing one is checked against
the generation this code assumes. Keeping it separate from the mutation logic means the
mutation reads as a sequence of typed checks rather than a mix of SQLite plumbing and identity
rules.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import apsw

from agents_remember.memory.knowledge import schema
from agents_remember.memory.knowledge.refusals import KnowledgeStorageError
from agents_remember.models.knowledge.context import (
    KNOWLEDGE_SCHEMA_NAME,
    KnowledgeSchemaIdentity,
)

# The bounded lock policy: wait this long for a competing writer, then refuse as busy rather
# than block indefinitely. The operation never retries against a re-read snapshot.
BUSY_TIMEOUT_MILLISECONDS = 1000


def open_database(database_path: Path) -> apsw.Connection:
    """Open one connection and apply the pragmas the store depends on."""

    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = apsw.Connection(str(database_path))
    apply_connection_contract(connection)
    return connection


def apply_connection_contract(connection: apsw.Connection) -> None:
    """Set and verify the pragmas every read and write in this store depends on.

    ``foreign_keys`` is verified rather than assumed: without it a deferred constraint never
    fires, and every "the database refuses it" guarantee in this package would silently become
    an intention.
    """

    connection.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MILLISECONDS}")
    connection.execute("PRAGMA foreign_keys=ON")
    if next(iter(connection.execute("PRAGMA foreign_keys")))[0] != 1:
        raise KnowledgeStorageError(
            "this SQLite build did not accept PRAGMA foreign_keys=ON; the store refuses to "
            "operate without enforced referential integrity"
        )


def fetch_one(
    connection: apsw.Connection,
    statement: str,
    parameters: Sequence[str | int | float | bytes | None] = (),
) -> tuple[object, ...] | None:
    """Return the one row a query selects, or ``None`` when it selects nothing.

    Every reader in this package asks the same question -- the row, if there is one -- and one
    owner for it keeps the "iterate once, then check for a row" shape identical everywhere
    instead of repeated at each call site.
    """

    row = next(iter(connection.execute(statement, tuple(parameters))), None)
    return None if row is None else tuple(row)


def create_or_validate_schema(connection: apsw.Connection) -> KnowledgeSchemaIdentity:
    """Create the declared schema on an empty database, else validate what is there."""

    if next(iter(connection.execute("SELECT count(*) FROM sqlite_schema WHERE type = 'table'")))[0]:
        return inspect_schema(connection)
    with immediate_transaction(connection):
        for statement in schema.create_schema_statements():
            connection.execute(statement)
    # The version marker is written after the DDL commits. A crash between the two leaves a
    # complete but unversioned database, which the next open refuses instead of guessing -- the
    # opposite order could leave a database that claims a generation it does not have.
    connection.execute(f"PRAGMA user_version = {schema.SCHEMA_USER_VERSION}")
    return inspect_schema(connection)


def inspect_schema(connection: apsw.Connection) -> KnowledgeSchemaIdentity:
    """Validate an existing database against the declared generation."""

    user_version = int(next(iter(connection.execute("PRAGMA user_version")))[0])
    if user_version != schema.SCHEMA_USER_VERSION:
        raise KnowledgeStorageError(
            f"unsupported schema: user_version is {user_version}, expected "
            f"{schema.SCHEMA_USER_VERSION} ({KNOWLEDGE_SCHEMA_NAME})"
        )
    _require_declared_tables(connection)
    _require_declared_triggers(connection)
    return KnowledgeSchemaIdentity(
        schema_name=KNOWLEDGE_SCHEMA_NAME,
        user_version=user_version,
        fingerprint=schema.schema_fingerprint(),
    )


def immediate_transaction(connection: apsw.Connection) -> _ImmediateTransaction:
    """Open one immediate transaction: the write lock is taken before the first read."""

    return _ImmediateTransaction(connection)


class _ImmediateTransaction:
    """A single immediate transaction whose exit rolls back on any failure.

    ``__exit__`` never suppresses the exception: the rollback happens here and the caller still
    sees the failure it caused.
    """

    def __init__(self, connection: apsw.Connection) -> None:
        self._connection = connection

    def __enter__(self) -> None:
        self._connection.execute("BEGIN IMMEDIATE")

    def __exit__(self, *exception: object) -> None:
        if exception and exception[0] is not None:
            self._connection.execute("ROLLBACK")
            return
        self._connection.execute("COMMIT")


def discard_closed_wal_peers(database_path: Path) -> None:
    """Remove SQLite's scratch journal peers once no connection holds the database.

    This never runs while a connection is open, because deleting a live journal is how a
    crashed database loses committed content. With the last connection closed, the peer files
    are scratch that SQLite removes itself on a clean shutdown.
    """

    for suffix in ("-wal", "-shm"):
        peer = database_path.with_name(database_path.name + suffix)
        if peer.exists():
            peer.unlink()


def _require_declared_tables(connection: apsw.Connection) -> None:
    present = {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_schema WHERE type = 'table'")
    }
    missing = [name for name in schema.CANONICAL_TABLES if name not in present]
    if missing:
        raise KnowledgeStorageError(
            f"unsupported schema: missing canonical table(s) {', '.join(missing)}. Session "
            "changesets can silently omit a table that exists on only one side, so a partial "
            "schema is refused rather than written through."
        )
    for table, declared in schema.CANONICAL_COLUMNS.items():
        actual = tuple(str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})"))
        if actual != declared:
            raise KnowledgeStorageError(
                f"unsupported schema: table {table} declares columns {actual}, expected {declared}"
            )


def _require_declared_triggers(connection: apsw.Connection) -> None:
    present = {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_schema WHERE type = 'trigger'")
    }
    missing = sorted(set(schema.IMMUTABILITY_TRIGGERS) - present)
    if missing:
        raise KnowledgeStorageError(
            f"unsupported schema: missing immutability trigger(s) {', '.join(missing)}. A "
            "database without them can rewrite a sealed revision, so it is not this schema."
        )
