"""The supported schema generations, as pinned data rather than build-time globals.

A schema generation used to be a singleton: ``SCHEMA_USER_VERSION``, ``CANONICAL_TABLES`` and
``schema_fingerprint()`` described the running build, and every reader compared a dataset against
*that*. The moment a second generation exists that becomes false in a way that is worse than
merely incomplete -- the logical encoder builds its table mapping from the live manifest, so a
dataset read by newer code would digest over tables it does not have, and an unchanged version-1
dataset would silently change identity.

This module makes a generation one frozen record, and makes *selection* a read of the dataset:

* :data:`GENERATION_1` is pinned as the exact generation this branch ships today, including the
  structural fingerprint recorded as a constant. Its declarations stay verbatim in
  :mod:`agents_remember.memory.knowledge.schema`; nothing here restates them.
* :data:`GENERATION_2` is generation 1 plus the tables :mod:`…schema_v2` appends.
* :data:`GENERATION_3` is generation 2 plus the tables :mod:`…schema_v3` appends -- the facet
  attachments, the decision supersession edge and the explanation pair. It is the created
  generation, so a *new* store declares version 3 while a generation-2 dataset that already exists
  keeps declaring version 2 and is read through generation 2's own record.
* :data:`GENERATION_4` is generation 3 plus the table :mod:`…schema_v4` appends -- the recorded
  order of one detection run's signals. It is the created generation now, so a *new* store declares
  version 4 while a generation-3 dataset that already exists keeps declaring version 3 and is read
  through generation 3's own record. The detection record group's own payload shapes are registered
  in the record envelope rather than appended as columns, which is why this generation adds one
  table and not a record group.
* :func:`require_pinned_generation_1_unchanged` is the gate that fails -- not warns -- when the
  pinned generation no longer recomputes to its constant. Without it the pin is a comment.
* :func:`generation_of_database` and :func:`generation_of_artifact` select a generation from what
  the *dataset* declares. :func:`generation_of_new_store` is the one place a build's own
  generation decides anything, and it decides only what brand-new data declares (requirement 2.7).

The two key spaces are not interchangeable and that is deliberate. An open SQLite file declares its
generation through ``PRAGMA user_version`` alone -- the application schema name is not stored in
the file, and the name :func:`…connection.inspect_schema` reports is the *build's* -- so the open
path resolves by version. A portable artifact carries ``schema`` as well, so the artifact path
resolves by pair. Versions are distinct per supported generation precisely so the open path's
single-key lookup is total over what it can see.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

import apsw

from agents_remember.kernel.canonical_json import sha256_digest
from agents_remember.memory.knowledge import schema, schema_v2, schema_v3, schema_v4
from agents_remember.memory.knowledge.export_refusals import unsupported_schema_refusal
from agents_remember.memory.knowledge.refusals import KnowledgeStorageError
from agents_remember.models.knowledge.context import KNOWLEDGE_SCHEMA_NAME
from agents_remember.models.knowledge.result import KnowledgeOperation, KnowledgeRefusal


class KnowledgeSchemaPinError(KnowledgeStorageError):
    """A pinned generation no longer describes itself.

    This is a defect report rather than an expected outcome: generation 1's recorded data was
    edited, or the encoder changed what it covers. The recovery is to correct the change, never to
    re-pin the constant to the new value.
    """


@dataclass(frozen=True)
class SchemaGeneration:
    """One supported ``(schema name, user_version)`` generation, as data.

    Every field exists because some reader needs it and none is derivable from the others:

    * ``tables``, ``columns``, ``primary_keys`` and ``json_columns`` are what the encoder orders
      rows by and decodes cells through. They are not recoverable from ``table_ddl``: the typed-JSON
      columns have no DDL counterpart at all (``json_valid`` appears nowhere in generation 1's DDL),
      and a primary-key tuple is declared data that the encoder checks *against* the column list.
    * ``features`` is an input to the manifest and therefore to the fingerprint.
    * ``fingerprint`` is the structural identity a reader compares. For generation 1 it is a
      recorded constant, checked by :func:`require_pinned_generation_unchanged`.
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
    """Return one generation's structural manifest, in the shipped shape."""

    return {
        "schema": generation.schema_name,
        "user_version": generation.user_version,
        "tables": {name: list(generation.columns[name]) for name in generation.tables},
        "triggers": sorted(generation.triggers),
        "indexes": sorted(index_name(statement) for statement in generation.index_ddl),
        "features": list(generation.features),
    }


def structure_fingerprint(generation: SchemaGeneration) -> str:
    """Return the fingerprint of one generation's exact structure.

    The fingerprint covers the declared manifest, the DDL text, the trigger set and the index set.
    Two databases with the same fingerprint can be compared row by row; two with different
    fingerprints are different schemas whatever ``user_version`` says.

    This is the **one** fingerprint definition. It is a function of a generation record rather
    than of the running build, which is what lets generation 1's recorded data be recomputed
    against its constant instead of against the code that produced it.
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
    """Return the exact ordered DDL statements that create one generation."""

    statements: list[str] = [generation.table_ddl[name] for name in generation.tables]
    statements.extend(generation.index_ddl)
    statements.extend(generation.triggers[name] for name in sorted(generation.triggers))
    return tuple(statements)


# Exactly what ``schema_fingerprint()`` returned for generation 1, read at revision
# ``420669c459aab3650cdaa5b3e5271e71d7d94c0e`` -- the pre-refactor branch head -- by running,
# from ``mcp/``:
#
#   PYTHONPATH=mcp/src mcp/.venv/bin/python -c \
#     "from agents_remember.memory.knowledge import schema; print(schema.schema_fingerprint())"
#
# It is recorded as measured data, not as an assertion, and it is non-circular by construction:
# the constant comes from a revision older than this refactor, while
# :func:`require_pinned_generation_unchanged` recomputes it from the recorded generation-1 record.
# A value that disagrees with any recorded attestation is a finding, never a re-pin.
GENERATION_1_FINGERPRINT = "bae805d6443a42ca65f733149cb3dc694ea89d3e40554c56480f17cb51edab0b"

GENERATION_1_SCHEMA_NAME = KNOWLEDGE_SCHEMA_NAME
GENERATION_2_SCHEMA_NAME = "ar-knowledge-sqlite/v2"
GENERATION_3_SCHEMA_NAME = "ar-knowledge-sqlite/v3"
GENERATION_4_SCHEMA_NAME = "ar-knowledge-sqlite/v4"

GENERATION_1 = SchemaGeneration(
    schema_name=GENERATION_1_SCHEMA_NAME,
    user_version=schema.SCHEMA_USER_VERSION,
    tables=schema.CANONICAL_TABLES,
    columns=schema.CANONICAL_COLUMNS,
    primary_keys=schema.PRIMARY_KEYS,
    json_columns=schema.JSON_COLUMNS,
    features=schema.REQUIRED_SQLITE_FEATURES,
    table_ddl=schema.TABLE_DDL,
    index_ddl=schema.INDEX_DDL,
    triggers=schema.IMMUTABILITY_TRIGGERS,
    fingerprint=GENERATION_1_FINGERPRINT,
)


# Generation 2 is generation 1, unchanged, plus the appended tables. The composition is written as
# an explicit append so ``GENERATION_2.tables[: len(GENERATION_1.tables)] == GENERATION_1.tables``
# and generation 2's columns for each of the first ten names are generation 1's -- the two
# comparisons Example 1 makes checkable, and the reason the governing-route association lives in
# generation-2 tables rather than as a column appended to a generation-1 table.
def _compose_generation_2() -> SchemaGeneration:
    """Return generation 2: generation 1's declarations, unchanged, with this leaf's tables appended."""

    composed = SchemaGeneration(
        schema_name=GENERATION_2_SCHEMA_NAME,
        user_version=2,
        tables=GENERATION_1.tables + schema_v2.APPENDED_TABLES,
        columns={**GENERATION_1.columns, **schema_v2.APPENDED_COLUMNS},
        primary_keys={**GENERATION_1.primary_keys, **schema_v2.APPENDED_PRIMARY_KEYS},
        json_columns={**GENERATION_1.json_columns, **schema_v2.APPENDED_JSON_COLUMNS},
        features=GENERATION_1.features + schema_v2.APPENDED_FEATURES,
        table_ddl={**GENERATION_1.table_ddl, **schema_v2.APPENDED_TABLE_DDL},
        index_ddl=GENERATION_1.index_ddl + schema_v2.APPENDED_INDEX_DDL,
        triggers={**GENERATION_1.triggers, **schema_v2.APPENDED_TRIGGERS},
        fingerprint="",
    )
    return replace(composed, fingerprint=structure_fingerprint(composed))


GENERATION_2 = _compose_generation_2()


# Generation 3 is generation 2, unchanged, plus the tables :mod:`…schema_v3` appends -- the facet
# attachments, the decision supersession edge and the explanation pair. The composition is written
# as the same explicit append generation 2's is, so
# ``GENERATION_3.tables[: len(GENERATION_2.tables)] == GENERATION_2.tables`` and generation 3's
# columns for each of the first sixteen names are generation 2's. That prefix equality is the whole
# of ``KS-R10@v1`` §1.3's additive rule: a generation appends tables and never retypes, reorders or
# drops an earlier generation's.
def _compose_generation_3() -> SchemaGeneration:
    """Return generation 3: generation 2's declarations, unchanged, with this leaf's tables appended."""

    composed = SchemaGeneration(
        schema_name=GENERATION_3_SCHEMA_NAME,
        user_version=3,
        tables=GENERATION_2.tables + schema_v3.APPENDED_TABLES,
        columns={**GENERATION_2.columns, **schema_v3.APPENDED_COLUMNS},
        primary_keys={**GENERATION_2.primary_keys, **schema_v3.APPENDED_PRIMARY_KEYS},
        json_columns={**GENERATION_2.json_columns, **schema_v3.APPENDED_JSON_COLUMNS},
        features=GENERATION_2.features + schema_v3.APPENDED_FEATURES,
        table_ddl={**GENERATION_2.table_ddl, **schema_v3.APPENDED_TABLE_DDL},
        index_ddl=GENERATION_2.index_ddl + schema_v3.APPENDED_INDEX_DDL,
        triggers={**GENERATION_2.triggers, **schema_v3.APPENDED_TRIGGERS},
        fingerprint="",
    )
    return replace(composed, fingerprint=structure_fingerprint(composed))


GENERATION_3 = _compose_generation_3()


# Generation 4 is generation 3, unchanged, plus the table :mod:`…schema_v4` appends -- the recorded
# order of one detection run's signals. The composition is written as the same explicit append
# generations 2 and 3 are, so ``GENERATION_4.tables[: len(GENERATION_3.tables)] ==
# GENERATION_3.tables`` and generation 4's columns for each of the first twenty names are generation
# 3's. That prefix equality is the whole of ``KS-R10@v1`` §1.3's additive rule: a generation appends
# tables and never retypes, reorders or drops an earlier generation's.
def _compose_generation_4() -> SchemaGeneration:
    """Return generation 4: generation 3's declarations, unchanged, with this leaf's table appended."""

    composed = SchemaGeneration(
        schema_name=GENERATION_4_SCHEMA_NAME,
        user_version=4,
        tables=GENERATION_3.tables + schema_v4.APPENDED_TABLES,
        columns={**GENERATION_3.columns, **schema_v4.APPENDED_COLUMNS},
        primary_keys={**GENERATION_3.primary_keys, **schema_v4.APPENDED_PRIMARY_KEYS},
        json_columns={**GENERATION_3.json_columns, **schema_v4.APPENDED_JSON_COLUMNS},
        features=GENERATION_3.features + schema_v4.APPENDED_FEATURES,
        table_ddl={**GENERATION_3.table_ddl, **schema_v4.APPENDED_TABLE_DDL},
        index_ddl=GENERATION_3.index_ddl + schema_v4.APPENDED_INDEX_DDL,
        triggers={**GENERATION_3.triggers, **schema_v4.APPENDED_TRIGGERS},
        fingerprint="",
    )
    return replace(composed, fingerprint=structure_fingerprint(composed))


GENERATION_4 = _compose_generation_4()

# The registry. Ordered oldest first, so "the newest generation this build supports" is the last
# entry rather than a second literal that could drift from the tuple -- and so
# ``generation_of_new_store()`` declares generation 4 while a generation-3 dataset stays
# generation 3 (``KS-R10@v1`` §5.1).
GENERATIONS: tuple[SchemaGeneration, ...] = (
    GENERATION_1,
    GENERATION_2,
    GENERATION_3,
    GENERATION_4,
)

GENERATIONS_BY_VERSION: Mapping[int, SchemaGeneration] = {
    generation.user_version: generation for generation in GENERATIONS
}

GENERATIONS_BY_KEY: Mapping[tuple[str, int], SchemaGeneration] = {
    (generation.schema_name, generation.user_version): generation for generation in GENERATIONS
}

# What a *created* store declares. This is the only place a build's own generation decides
# anything, and it decides only what new data says about itself: an empty database has no version
# to read, so "never from the running build" cannot govern initialization (requirement 2.7).
CURRENT_GENERATION = GENERATIONS[-1]


def require_pinned_generation_unchanged(generation: SchemaGeneration) -> None:
    """Fail loudly when a pinned generation no longer fingerprints as its recorded constant."""

    recomputed = structure_fingerprint(generation)
    if recomputed != generation.fingerprint:
        raise KnowledgeSchemaPinError(
            f"schema generation {generation.schema_name} no longer fingerprints as pinned: the "
            f"recorded generation data or the encoder was changed. Pinned "
            f"{generation.fingerprint}, recomputed {recomputed}. Correct the change; do not "
            "re-pin the constant to the new value."
        )


def require_pinned_generation_1_unchanged() -> None:
    """Fail loudly when the pinned generation-1 structure no longer describes itself."""

    require_pinned_generation_unchanged(GENERATION_1)


def generation_of_new_store() -> SchemaGeneration:
    """Declare the generation a store is *created* as. Not a selection: there is no dataset."""

    return CURRENT_GENERATION


def generation_of_database(connection: apsw.Connection) -> SchemaGeneration:
    """Select the generation a dataset declares, from the dataset itself.

    An open database declares its generation through ``PRAGMA user_version`` alone -- the
    application schema name is not in the file -- so this resolves by version. A dataset whose
    version is not registered is refused as an unsupported schema: it is not migrated, repaired,
    re-created, or re-read under a different generation to obtain a green result.
    """

    declared = int(next(iter(connection.execute("PRAGMA user_version")))[0])
    generation = generation_for_version(declared)
    if generation is None:
        raise KnowledgeStorageError(
            f"unsupported schema: user_version is {declared}. This build supports "
            f"{registered_version_listing()}. An unknown generation is refused, never migrated or "
            "guessed at."
        )
    return generation


def generation_for_version(user_version: int) -> SchemaGeneration | None:
    """Return the generation one open-database version resolves to, or ``None``."""

    return GENERATIONS_BY_VERSION.get(user_version)


def declared_columns_for(table: str) -> tuple[str, ...] | None:
    """Return the declared column order one table carries anywhere in the registry, or ``None``.

    This exists for exactly one caller: the portable reader's canonical-form gate, which renders an
    artifact **before** it has decided whether the artifact's declared generation is one this build
    supports. The gate is about the *spelling* of a document rather than about which generation it
    is, so it must be able to render a table the document carries even when the header is one the
    reader is about to refuse -- otherwise a differently-spelled document with an unsupported header
    would be refused for the wrong reason (or, worse, not refused for its spelling at all).

    Requirement 1.3's additive rule is what makes a single answer well defined: a table declared by
    more than one generation declares the same columns in each, and a disagreement here is a
    registry defect rather than something to resolve by picking one.
    """

    frames = {
        generation.columns[table] for generation in GENERATIONS if table in generation.columns
    }
    if not frames:
        return None
    if len(frames) > 1:
        raise KnowledgeSchemaPinError(
            f"the registry declares more than one column order for {table}, which requirement 1.3 "
            "forbids: a later generation appends tables and never retypes or reorders an earlier "
            "generation's"
        )
    return next(iter(frames))


def declared_json_columns_for(table: str) -> frozenset[str] | None:
    """Return the typed-JSON column set one table carries anywhere in the registry, or ``None``."""

    frames = {
        generation.json_columns.get(table, frozenset())
        for generation in GENERATIONS
        if table in generation.columns
    }
    if not frames:
        return None
    if len(frames) > 1:
        raise KnowledgeSchemaPinError(
            f"the registry declares more than one typed-JSON column set for {table}, which "
            "requirement 1.3 forbids for an earlier generation's table"
        )
    return next(iter(frames))


def generation_for_name(schema_name: object) -> SchemaGeneration | None:
    """Return the generation one application schema name names, or ``None``.

    This is not dispatch: it does not decide which generation a dataset is. It resolves a caller's
    already-selected generation, spelled by name, to the record an encoder needs -- and it refuses
    a name no registered generation declares rather than approximating one.
    """

    if not isinstance(schema_name, str):
        return None
    for generation in GENERATIONS:
        if generation.schema_name == schema_name:
            return generation
    return None


def generation_for_key(schema_name: object, user_version: object) -> SchemaGeneration | None:
    """Return the generation one artifact ``(schema, userVersion)`` pair resolves to, or ``None``.

    The lookup is **type-strict on the version** before it is a lookup. In Python ``1 == 1.0 ==
    True`` and all three hash alike, so an equality- or dict-key-based lookup would resolve the
    spellings ``1.0`` and ``true`` to generation 1 -- which the shipped reader deliberately
    refuses. A non-integer spelling is therefore refused by the caller as an unsupported schema,
    with the observed spelling rendered, exactly as today.
    """

    if type(user_version) is not int:
        return None
    if not isinstance(schema_name, str):
        return None
    return GENERATIONS_BY_KEY.get((schema_name, user_version))


def registered_version_listing() -> str:
    """Render every registered generation's own version string, for a refusal's ``expected``."""

    return " | ".join(str(generation.user_version) for generation in GENERATIONS)


def registered_key_listing() -> str:
    """Render every registered ``schema/version`` pair, for an artifact refusal's ``expected``."""

    return " | ".join(
        f"{generation.schema_name}/{generation.user_version}" for generation in GENERATIONS
    )


def declared_version_string(schema_name: object) -> str | None:
    """Return the version string of the generation one artifact schema name names, or ``None``.

    This preserves the shipped rendering for a type-strict refusal: a generation-1 artifact's
    ``expected`` stays that generation's own version string (``"1"``), not the joined registry
    listing -- the two are different facts. The joined listing belongs to the unregistered-key
    refusal, which says this build supports no such pair.
    """

    for generation in GENERATIONS:
        if generation.schema_name == schema_name:
            return str(generation.user_version)
    return None


def generation_of_artifact(
    envelope: Mapping[str, object], operation: KnowledgeOperation
) -> SchemaGeneration | KnowledgeRefusal:
    """Select the generation a portable artifact declares, from the artifact itself.

    A portable artifact *does* carry its application schema name, unlike an open file, so this
    resolves the pair rather than the version -- and it is type-strict on ``userVersion`` before it
    is a lookup, so the spellings ``1.0`` and ``true`` keep the shipped refusal instead of
    resolving to generation 1.
    """

    declared_schema = envelope.get("schema")
    declared_version = envelope.get("userVersion")
    if type(declared_version) is not int:
        # The type-strict path keeps the SHIPPED rendering: for a generation-1 artifact
        # ``expected`` is that generation's own version string ("1"). It is not the joined
        # registry listing -- that belongs to the unregistered-key refusal below, which is a
        # different fact (this build supports no such pair) from a mis-spelled version.
        return unsupported_schema_refusal(
            operation,
            f"the artifact declares user version {declared_version!r}; the supported generation is "
            f"{declared_version_string(declared_schema)}, spelled as a JSON integer",
            expected=declared_version_string(declared_schema) or registered_version_listing(),
            observed=repr(declared_version),
        )
    generation = generation_for_key(declared_schema, declared_version)
    if generation is None:
        return unsupported_schema_refusal(
            operation,
            "the artifact declares a schema generation this build does not support",
            expected=registered_key_listing(),
            observed=f"{declared_schema!r}/{declared_version!r}",
        )
    return generation
