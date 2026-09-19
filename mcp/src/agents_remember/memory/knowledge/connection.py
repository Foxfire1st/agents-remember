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

from agents_remember.memory.knowledge import schema_generations
from agents_remember.memory.knowledge.refusals import KnowledgeStorageError
from agents_remember.models.knowledge.context import KnowledgeSchemaIdentity

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


def open_read_only_database(database_path: Path) -> apsw.Connection:
    """Open one existing database through a connection that cannot write it.

    Verification uses this on purpose. A pass that could repair the file it is checking would
    prove only that the repair worked, and a snapshot whose identity is confirmed by a writable
    connection is not confirmed by a reader. ``foreign_keys`` is not set here because no
    statement this connection runs writes a row.
    """

    connection = apsw.Connection(str(database_path), flags=apsw.SQLITE_OPEN_READONLY)
    connection.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MILLISECONDS}")
    return connection


def journal_mode(connection: apsw.Connection) -> str:
    """Return the connection's current journal mode, lowercased."""

    return str(next(iter(connection.execute("PRAGMA journal_mode")))[0]).lower()


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
    """Create the newest supported generation on an empty database, else validate what is there.

    Initialization **declares** a generation; it does not select one. An empty database has no
    version to read, so "never from the running build" cannot govern this path: creating a store
    declares the newest generation this build supports, through one explicit constant. Validation
    is registry-driven thereafter, so an existing database of any registered generation is accepted
    and checked against *its own* tables, columns and triggers.
    """

    if next(iter(connection.execute("SELECT count(*) FROM sqlite_schema WHERE type = 'table'")))[0]:
        return inspect_schema(connection)
    declared = schema_generations.generation_of_new_store()
    with immediate_transaction(connection):
        for statement in schema_generations.create_schema_statements(declared):
            connection.execute(statement)
    # The version marker is written after the DDL commits. A crash between the two leaves a
    # complete but unversioned database, which the next open refuses instead of guessing -- the
    # opposite order could leave a database that claims a generation it does not have.
    connection.execute(f"PRAGMA user_version = {declared.user_version}")
    return inspect_schema(connection)


def inspect_schema(connection: apsw.Connection) -> KnowledgeSchemaIdentity:
    """Validate an existing database against the generation the database itself declares.

    The generation is selected from ``PRAGMA user_version`` and resolved against the registry --
    never against the running build. A dataset whose version is not registered is refused as an
    unsupported schema; it is not migrated, repaired or re-read under another generation. A dataset
    that resolves to a registered generation whose structure it does not match is refused by the
    structural checks below, not by a fallback to a different generation.
    """

    declared = schema_generations.generation_of_database(connection)
    _require_declared_tables(connection, declared)
    _require_declared_triggers(connection, declared)
    return KnowledgeSchemaIdentity(
        schema_name=declared.schema_name,
        user_version=declared.user_version,
        fingerprint=declared.fingerprint,
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
    """Remove the journal peers of one database that no connection holds.

    **No caller in this package may use this on close.** It cannot tell whether another
    connection -- in this process or another -- still has the database open, and it does not need
    to: SQLite checkpoints its WAL and removes both peer files itself when the last connection
    closes cleanly. What the unlink can do instead is destroy committed content. A reader holding
    a read transaction blocks that checkpoint, so a writer's committed frames are still only in
    the WAL when a closing writer unlinks it; the committed rows are then absent from the main
    file and the database is left unreadable until it is rebuilt. That is the failure this
    function's earlier docstring claimed was impossible, and it is reachable in the intended
    multi-consumer shape of this store (one consumer reading while another writes).

    It remains here, with that boundary stated, for a caller that has independently established
    that no connection holds the database -- an offline repair or an enclosure cleanup that owns
    the file exclusively. Every ordinary close relies on SQLite instead.
    """

    for suffix in ("-wal", "-shm"):
        peer = database_path.with_name(database_path.name + suffix)
        if peer.exists():
            peer.unlink()


def _require_declared_tables(
    connection: apsw.Connection, declared: schema_generations.SchemaGeneration
) -> None:
    present = {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_schema WHERE type = 'table'")
    }
    missing = [name for name in declared.tables if name not in present]
    if missing:
        raise KnowledgeStorageError(
            f"unsupported schema: missing canonical table(s) {', '.join(missing)}. Session "
            "changesets can silently omit a table that exists on only one side, so a partial "
            "schema is refused rather than written through."
        )
    for table, columns in declared.columns.items():
        actual = tuple(str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})"))
        if actual != columns:
            raise KnowledgeStorageError(
                f"unsupported schema: table {table} declares columns {actual}, expected {columns}"
            )


def _require_declared_triggers(
    connection: apsw.Connection, declared: schema_generations.SchemaGeneration
) -> None:
    present = {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_schema WHERE type = 'trigger'")
    }
    missing = sorted(set(declared.triggers) - present)
    if missing:
        raise KnowledgeStorageError(
            f"unsupported schema: missing immutability trigger(s) {', '.join(missing)}. A "
            "database without them can rewrite a sealed revision, so it is not this schema."
        )
