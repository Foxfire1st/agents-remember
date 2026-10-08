"""The one knowledge schema this build declares: the schema of a memory tree's derived index.

Knowledge is text in Git. The only SQLite dataset this build creates is the derived index of a
memory tree (MIK-R23), and the read code runs over that file's logical tables. The canonical
database is retired (MIK-R26), and with it the registry of nine openable schema generations: there
is **one** schema, :data:`CURRENT_GENERATION`, and a dataset that declares any other version is
refused as an unsupported schema, never migrated, repaired or read under another declaration. A
database of an earlier generation is legacy memory; it is read only by the conversion command's
own reader (``memory/conversion/legacy_db.py``), which does not use this package.

**The table definitions are kept.** The schema is declared as data in :mod:`.schema` (the base
tables) and in :mod:`.schema_v2` to :mod:`.schema_v9` (the tables each appended); this module
composes them, in that order, into the one :class:`SchemaGeneration` record. Its name, version and
structural fingerprint are exactly those the last generation of the registry had, so an index built
before this change and one built after it are the same schema, and the recorded fingerprint below
is checked against the composition rather than trusted (:func:`require_pinned_schema_unchanged`).

A record rather than module globals, because every reader takes the schema as a value: the logical
encoder orders rows by its ``tables``, ``columns`` and ``primary_keys`` and decodes cells through
its ``json_columns``; the connection contract creates and validates a file from its DDL.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Protocol

import apsw

from agents_remember.kernel.canonical_json import sha256_digest
from agents_remember.memory.knowledge import (
    schema,
    schema_v2,
    schema_v3,
    schema_v4,
    schema_v5,
    schema_v6,
    schema_v7,
    schema_v8,
    schema_v9,
)
from agents_remember.memory.knowledge.refusals import KnowledgeStorageError


class _AppendedTables(Protocol):
    """The declarations one appended-table module exposes for the schema to compose it.

    A protocol rather than a base class, so each of ``schema_v2`` … ``schema_v9`` stays a module of
    plain declared data that publishes exactly these names.
    """

    APPENDED_TABLES: tuple[str, ...]
    APPENDED_COLUMNS: Mapping[str, tuple[str, ...]]
    APPENDED_PRIMARY_KEYS: Mapping[str, tuple[str, ...]]
    APPENDED_JSON_COLUMNS: Mapping[str, frozenset[str]]
    APPENDED_FEATURES: tuple[str, ...]
    APPENDED_TABLE_DDL: Mapping[str, str]
    APPENDED_INDEX_DDL: tuple[str, ...]
    APPENDED_TRIGGERS: Mapping[str, str]


class KnowledgeSchemaPinError(KnowledgeStorageError):
    """The declared schema no longer fingerprints as its recorded constant.

    This is a defect report rather than an expected outcome: a table definition was edited. The
    recovery is to correct the change or to declare a new schema version deliberately, never to
    re-pin the constant to the new value.
    """


@dataclass(frozen=True)
class SchemaGeneration:
    """The declared ``(schema name, user_version)`` schema, as data.

    Every field exists because some reader needs it and none is derivable from the others:

    * ``tables``, ``columns``, ``primary_keys`` and ``json_columns`` are what the encoder orders
      rows by and decodes cells through. They are not recoverable from ``table_ddl``: the typed-JSON
      columns have no DDL counterpart, and a primary-key tuple is declared data that the encoder
      checks *against* the column list.
    * ``features`` is an input to the manifest and therefore to the fingerprint.
    * ``fingerprint`` is the structural identity a reader compares.
    """

    schema_name: str
    user_version: int
    tables: tuple[str, ...]
    columns: Mapping[str, tuple[str, ...]]
    primary_keys: Mapping[str, tuple[str, ...]]
    json_columns: Mapping[str, frozenset[str]]
    features: tuple[str, ...]
    table_ddl: Mapping[str, str]
    index_ddl: tuple[str, ...]
    triggers: Mapping[str, str]
    fingerprint: str


def structure_manifest(generation: SchemaGeneration) -> dict[str, object]:
    """Return the schema's structural manifest."""

    return {
        "schema": generation.schema_name,
        "user_version": generation.user_version,
        "tables": {name: list(generation.columns[name]) for name in generation.tables},
        "triggers": sorted(generation.triggers),
        "indexes": sorted(index_name(statement) for statement in generation.index_ddl),
        "features": list(generation.features),
    }


def structure_fingerprint(generation: SchemaGeneration) -> str:
    """Return the fingerprint of a schema's exact structure.

    The fingerprint covers the declared manifest, the DDL text, the trigger set and the index set.
    Two databases with the same fingerprint can be compared row by row; two with different
    fingerprints are different schemas whatever ``user_version`` says.
    """

    return sha256_digest(
        {
            "manifest": structure_manifest(generation),
            "table_ddl": {name: generation.table_ddl[name] for name in generation.tables},
            "trigger_ddl": {
                name: generation.triggers[name] for name in sorted(generation.triggers)
            },
            "index_ddl": list(generation.index_ddl),
        }
    )


def index_name(statement: str) -> str:
    """Return the name one ``CREATE INDEX`` statement declares."""

    return statement.split(" ON ", 1)[0].removeprefix("CREATE INDEX ").strip()


def create_schema_statements(generation: SchemaGeneration) -> tuple[str, ...]:
    """Return the exact ordered DDL statements that create the schema."""

    statements: list[str] = [generation.table_ddl[name] for name in generation.tables]
    statements.extend(generation.index_ddl)
    statements.extend(generation.triggers[name] for name in sorted(generation.triggers))
    return tuple(statements)


# The schema's name and version: those of the registry's last generation, so the files this build
# creates and the files an earlier build created declare the same schema.
SCHEMA_NAME = "ar-knowledge-sqlite/v9"
SCHEMA_USER_VERSION = 9


# The structural fingerprint of the declared schema, as the registry's last generation computed it
# at ``d0855daa5242f36aab1f97c11849fe6c0d6e933c`` (the base of the retirement leaf). It is recorded
# as measured data and checked against the composition below by
# :func:`require_pinned_schema_unchanged`: a table definition that changes changes the fingerprint,
# and that is a finding, never a re-pin.
SCHEMA_FINGERPRINT = "eeeaa02d871bd7b911d2d470c70f4cd869b55fb06d750d69966cac7c169c6d4f"

# The modules whose tables follow the base tables, in the order they are appended. The order is
# part of the schema: it is the table order the logical encoder serializes.
_APPENDED: tuple[_AppendedTables, ...] = (
    schema_v2,
    schema_v3,
    schema_v4,
    schema_v5,
    schema_v6,
    schema_v7,
    schema_v8,
    schema_v9,
)


def _compose() -> SchemaGeneration:
    """Return the one schema: the base tables with each appended module's declarations after them."""

    composed = SchemaGeneration(
        schema_name=SCHEMA_NAME,
        user_version=SCHEMA_USER_VERSION,
        tables=schema.CANONICAL_TABLES,
        columns=schema.CANONICAL_COLUMNS,
        primary_keys=schema.PRIMARY_KEYS,
        json_columns=schema.JSON_COLUMNS,
        features=schema.REQUIRED_SQLITE_FEATURES,
        table_ddl=schema.TABLE_DDL,
        index_ddl=schema.INDEX_DDL,
        triggers=schema.IMMUTABILITY_TRIGGERS,
        fingerprint="",
    )
    for appended in _APPENDED:
        composed = replace(
            composed,
            tables=composed.tables + appended.APPENDED_TABLES,
            columns={**composed.columns, **appended.APPENDED_COLUMNS},
            primary_keys={**composed.primary_keys, **appended.APPENDED_PRIMARY_KEYS},
            json_columns={**composed.json_columns, **appended.APPENDED_JSON_COLUMNS},
            features=composed.features + appended.APPENDED_FEATURES,
            table_ddl={**composed.table_ddl, **appended.APPENDED_TABLE_DDL},
            index_ddl=composed.index_ddl + appended.APPENDED_INDEX_DDL,
            triggers={**composed.triggers, **appended.APPENDED_TRIGGERS},
        )
    return replace(composed, fingerprint=structure_fingerprint(composed))


# The one schema. What a created file declares, and the only version an opened file may declare.
CURRENT_GENERATION = _compose()


def require_pinned_schema_unchanged() -> None:
    """Fail loudly when the composed schema no longer fingerprints as its recorded constant."""

    if CURRENT_GENERATION.fingerprint != SCHEMA_FINGERPRINT:
        raise KnowledgeSchemaPinError(
            f"schema {SCHEMA_NAME} no longer fingerprints as pinned: a table definition was "
            f"changed. Pinned {SCHEMA_FINGERPRINT}, composed {CURRENT_GENERATION.fingerprint}. "
            "Correct the change; do not re-pin the constant to the new value."
        )


def generation_of_new_store() -> SchemaGeneration:
    """Declare the schema a file is *created* with."""

    return CURRENT_GENERATION


def generation_of_database(connection: apsw.Connection) -> SchemaGeneration:
    """Return the schema an opened file declares, which must be the one this build declares.

    An open database declares its schema through ``PRAGMA user_version`` alone. A file that
    declares any other version is refused as an unsupported schema: it is not migrated, repaired,
    re-created, or read as if it declared this one. A database of an earlier generation is the
    legacy format, which only the conversion command reads.
    """

    declared = int(next(iter(connection.execute("PRAGMA user_version")))[0])
    if declared != CURRENT_GENERATION.user_version:
        raise KnowledgeStorageError(
            f"unsupported schema: user_version is {declared}. This build reads one schema, "
            f"{SCHEMA_NAME} (user_version {SCHEMA_USER_VERSION}), the schema of a memory tree's "
            "derived index. A database of another generation is the legacy format: it is never "
            "migrated or guessed at, and its knowledge is read after conversion to text "
            "(agents-remember knowledge-convert, MIK-R24)."
        )
    return CURRENT_GENERATION


def generation_for_name(schema_name: object) -> SchemaGeneration | None:
    """Return the declared schema when ``schema_name`` names it, else ``None``.

    This is not dispatch: it resolves a caller's already-selected schema, spelled by name, to the
    record an encoder needs, and it refuses any other name rather than approximating one.
    """

    return CURRENT_GENERATION if schema_name == SCHEMA_NAME else None
