"""The one knowledge schema this build declares: the schema of a memory tree's derived index.

Knowledge is text in Git. The only SQLite dataset this build creates is the derived index of a
memory tree (MIK-R23), and the read code runs over that file's logical tables. The canonical
database is retired (MIK-R26).

This module owns the schema whole, as data: the eight logical tables the projection fills --
``repository``, ``invariant``, ``invariant_revision``, ``family``, ``family_revision``,
``family_member``, ``source_anchor`` and ``realization_claim`` -- with the columns the projection
writes and a surviving reader reads, the exact DDL text, the index set, the immutability trigger
set and the required feature set (MIK-R74). Every other table of the retired store is gone with
it: no definition, no reader, no registry of generations. A dataset that declares any other
version is refused as an unsupported schema, never migrated, repaired or read under another
declaration. A database of an earlier generation is legacy memory; it is read only by the
conversion command's own reader (``memory/conversion/legacy_db.py``), which does not use this
package.

Two decisions are load-bearing here and are stated once:

* **The index creates only what it fills.** Every table declared here is filled by the
  projection; a table nothing fills is not declared.
* **Immutability is enforced by the database, not only by the operation.** A trigger refuses
  the write even when it arrives from a repair script or a future code path that forgot the
  rule.

A record rather than module globals alone, because every reader takes the schema as a value:
the logical encoder orders rows by its ``tables``, ``columns`` and ``primary_keys`` and decodes
cells through its ``json_columns``; the connection contract creates and validates a file from
its DDL.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

import apsw

from agents_remember.kernel.canonical_json import sha256_digest
from agents_remember.memory.knowledge.refusals import KnowledgeStorageError

# SQLite's own schema counter for this shape. It changes only when the DDL changes.
SCHEMA_USER_VERSION = 1

# The declared manifest order. The logical encoder serializes the tables in exactly this order,
# including empty ones, so the order is part of the contract.
CANONICAL_TABLES: tuple[str, ...] = (
    "repository",
    "invariant",
    "invariant_revision",
    "family",
    "family_revision",
    "family_member",
    "source_anchor",
    "realization_claim",
)

# The declared column order per table. A reader that needs a column name for a positional
# row uses this rather than asking the database, so a reordered column is a manifest mismatch
# and not a silent re-map.
CANONICAL_COLUMNS: Mapping[str, tuple[str, ...]] = {
    "repository": ("repository_id", "authority_home"),
    "invariant": ("repository_id", "invariant_id", "display_label", "label_provenance"),
    "invariant_revision": (
        "repository_id",
        "invariant_id",
        "revision_id",
        "display_version",
        "statement",
        "applicability",
        "conditions",
        "exclusions",
        "state_at_origin",
        "acceptance_ref",
        "provenance",
        "payload_digest",
    ),
    "family": ("repository_id", "family_id", "display_label", "label_provenance"),
    "family_revision": (
        "repository_id",
        "family_id",
        "revision_id",
        "display_version",
        "joint_guarantee",
        "state_at_origin",
        "acceptance_ref",
        "provenance",
        "payload_digest",
    ),
    "source_anchor": (
        "repository_id",
        "anchor_id",
        "path",
        "source_identity",
        "locator",
        "provenance",
    ),
    "family_member": (
        "repository_id",
        "member_id",
        "family_revision_id",
        "invariant_revision_id",
        "provenance",
    ),
    "realization_claim": (
        "repository_id",
        "claim_id",
        "invariant_revision_id",
        "anchor_id",
        "role",
        "rationale",
        "provenance",
    ),
}

# The declared primary key of each table, as the DDL declares it. Row order inside a
# table is this key's order, so two indexes compared by the logical encoder agree on ordering
# without either of them being asked how it happened to store its rows.
#
# These are the DDL's ``PRIMARY KEY`` column lists, which are not always the tables' leading
# columns: ``invariant_revision`` keys ``(repository_id, revision_id)`` while ``invariant_id`` sits
# between those two columns. A row is ordered by its key, not by the order its columns were
# declared in.
PRIMARY_KEYS: Mapping[str, tuple[str, ...]] = {
    "repository": ("repository_id",),
    "invariant": ("repository_id", "invariant_id"),
    "invariant_revision": ("repository_id", "revision_id"),
    "family": ("repository_id", "family_id"),
    "family_revision": ("repository_id", "revision_id"),
    "source_anchor": ("repository_id", "anchor_id"),
    "family_member": ("repository_id", "member_id"),
    "realization_claim": ("repository_id", "claim_id"),
}

# The columns whose stored text is a typed JSON value. They are decoded at the portable boundary;
# everything else is compared as the exact stored text.
JSON_COLUMNS: Mapping[str, frozenset[str]] = {
    "invariant": frozenset({"label_provenance"}),
    "invariant_revision": frozenset({"conditions", "exclusions", "provenance"}),
    "family": frozenset({"label_provenance"}),
    "family_revision": frozenset({"provenance"}),
    "source_anchor": frozenset({"source_identity", "locator", "provenance"}),
    "family_member": frozenset({"provenance"}),
    "realization_claim": frozenset({"provenance"}),
}

# Every primary-key column is declared NOT NULL in SQLite because ``STRICT`` tables do not
# inherit rowid-key behaviour for a composite key, but a *nullable* key in an ordinary rowid
# table is a documented SQLite quirk and would silently defeat primary-key identity.
TABLE_DDL: Mapping[str, str] = {
    "repository": """
CREATE TABLE repository (
  repository_id TEXT PRIMARY KEY NOT NULL,
  authority_home TEXT NOT NULL
) STRICT
""",
    "invariant": """
CREATE TABLE invariant (
  repository_id TEXT NOT NULL,
  invariant_id TEXT NOT NULL,
  display_label TEXT NOT NULL,
  label_provenance TEXT NOT NULL,
  PRIMARY KEY (repository_id, invariant_id),
  FOREIGN KEY (repository_id) REFERENCES repository(repository_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED
) STRICT
""",
    "invariant_revision": """
CREATE TABLE invariant_revision (
  repository_id TEXT NOT NULL,
  invariant_id TEXT NOT NULL,
  revision_id TEXT NOT NULL,
  display_version TEXT NOT NULL,
  statement TEXT NOT NULL,
  applicability TEXT NOT NULL,
  conditions TEXT NOT NULL,
  exclusions TEXT NOT NULL,
  state_at_origin TEXT NOT NULL,
  acceptance_ref TEXT,
  provenance TEXT NOT NULL,
  payload_digest TEXT NOT NULL,
  PRIMARY KEY (repository_id, revision_id),
  UNIQUE (repository_id, invariant_id, revision_id),
  FOREIGN KEY (repository_id, invariant_id)
    REFERENCES invariant(repository_id, invariant_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED
) STRICT
""",
    "family": """
CREATE TABLE family (
  repository_id TEXT NOT NULL,
  family_id TEXT NOT NULL,
  display_label TEXT NOT NULL,
  label_provenance TEXT NOT NULL,
  PRIMARY KEY (repository_id, family_id),
  FOREIGN KEY (repository_id) REFERENCES repository(repository_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED
) STRICT
""",
    "family_revision": """
CREATE TABLE family_revision (
  repository_id TEXT NOT NULL,
  family_id TEXT NOT NULL,
  revision_id TEXT NOT NULL,
  display_version TEXT NOT NULL,
  joint_guarantee TEXT NOT NULL,
  state_at_origin TEXT NOT NULL,
  acceptance_ref TEXT,
  provenance TEXT NOT NULL,
  payload_digest TEXT NOT NULL,
  PRIMARY KEY (repository_id, revision_id),
  UNIQUE (repository_id, family_id, revision_id),
  FOREIGN KEY (repository_id, family_id)
    REFERENCES family(repository_id, family_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED
) STRICT
""",
    "source_anchor": """
CREATE TABLE source_anchor (
  repository_id TEXT NOT NULL,
  anchor_id TEXT NOT NULL,
  path TEXT NOT NULL,
  source_identity TEXT NOT NULL,
  locator TEXT NOT NULL,
  provenance TEXT NOT NULL,
  PRIMARY KEY (repository_id, anchor_id),
  FOREIGN KEY (repository_id) REFERENCES repository(repository_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED
) STRICT
""",
    "family_member": """
CREATE TABLE family_member (
  repository_id TEXT NOT NULL,
  member_id TEXT NOT NULL,
  family_revision_id TEXT NOT NULL,
  invariant_revision_id TEXT NOT NULL,
  provenance TEXT NOT NULL,
  PRIMARY KEY (repository_id, member_id),
  UNIQUE (repository_id, family_revision_id, invariant_revision_id),
  FOREIGN KEY (repository_id, family_revision_id)
    REFERENCES family_revision(repository_id, revision_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (repository_id, invariant_revision_id)
    REFERENCES invariant_revision(repository_id, revision_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED
) STRICT
""",
    "realization_claim": """
CREATE TABLE realization_claim (
  repository_id TEXT NOT NULL,
  claim_id TEXT NOT NULL,
  invariant_revision_id TEXT NOT NULL,
  anchor_id TEXT NOT NULL,
  role TEXT NOT NULL,
  rationale TEXT NOT NULL,
  provenance TEXT NOT NULL,
  PRIMARY KEY (repository_id, claim_id),
  UNIQUE (repository_id, invariant_revision_id, anchor_id),
  FOREIGN KEY (repository_id, invariant_revision_id)
    REFERENCES invariant_revision(repository_id, revision_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (repository_id, anchor_id)
    REFERENCES source_anchor(repository_id, anchor_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED
) STRICT
""",
}

# Index names are a local choice; each covers the reverse direction of a declared lookup.
INDEX_DDL: tuple[str, ...] = (
    "CREATE INDEX invariant_revision_invariant ON invariant_revision (repository_id, invariant_id)",
    "CREATE INDEX family_revision_family ON family_revision (repository_id, family_id)",
    "CREATE INDEX source_anchor_path ON source_anchor (repository_id, path)",
    "CREATE INDEX family_member_invariant_revision "
    "ON family_member (repository_id, invariant_revision_id)",
    "CREATE INDEX realization_claim_invariant_revision "
    "ON realization_claim (repository_id, invariant_revision_id)",
    "CREATE INDEX realization_claim_anchor ON realization_claim (repository_id, anchor_id)",
)

# The exact trigger set. Names are part of the contract: a reader compares them, because a
# dataset whose constraints were quietly dropped is not the same schema.
IMMUTABILITY_TRIGGERS: Mapping[str, str] = {
    "repository_no_update": """
CREATE TRIGGER repository_no_update BEFORE UPDATE ON repository
BEGIN SELECT RAISE(ABORT, 'immutable_revision: a repository namespace cannot be rebound'); END
""",
    "repository_no_delete": """
CREATE TRIGGER repository_no_delete BEFORE DELETE ON repository
BEGIN SELECT RAISE(ABORT, 'immutable_revision: a repository namespace cannot be deleted'); END
""",
    "invariant_no_rebind": """
CREATE TRIGGER invariant_no_rebind BEFORE UPDATE OF repository_id, invariant_id ON invariant
BEGIN SELECT RAISE(ABORT, 'immutable_revision: an invariant identity cannot be rebound'); END
""",
    "invariant_no_delete": """
CREATE TRIGGER invariant_no_delete BEFORE DELETE ON invariant
BEGIN SELECT RAISE(ABORT, 'immutable_revision: an invariant identity cannot be deleted'); END
""",
    "invariant_revision_no_update": """
CREATE TRIGGER invariant_revision_no_update BEFORE UPDATE ON invariant_revision
BEGIN SELECT RAISE(ABORT, 'immutable_revision: revision rows cannot be updated'); END
""",
    "invariant_revision_no_delete": """
CREATE TRIGGER invariant_revision_no_delete BEFORE DELETE ON invariant_revision
BEGIN SELECT RAISE(ABORT, 'immutable_revision: revision rows cannot be deleted'); END
""",
    "family_revision_no_update": """
CREATE TRIGGER family_revision_no_update BEFORE UPDATE ON family_revision
BEGIN SELECT RAISE(ABORT, 'immutable_revision: family revision rows cannot be updated'); END
""",
    "family_revision_no_delete": """
CREATE TRIGGER family_revision_no_delete BEFORE DELETE ON family_revision
BEGIN SELECT RAISE(ABORT, 'immutable_revision: family revision rows cannot be deleted'); END
""",
    "source_anchor_no_rewrite": """
CREATE TRIGGER source_anchor_no_rewrite BEFORE UPDATE OF path, source_identity, locator, provenance
ON source_anchor
BEGIN SELECT RAISE(ABORT, 'immutable_revision: an anchor payload cannot be rewritten in place'); END
""",
    "family_member_no_repoint": """
CREATE TRIGGER family_member_no_repoint
BEFORE UPDATE OF family_revision_id, invariant_revision_id, provenance ON family_member
BEGIN SELECT RAISE(ABORT, 'immutable_revision: a membership cannot be repointed in place'); END
""",
    "realization_claim_no_rewrite": """
CREATE TRIGGER realization_claim_no_rewrite
BEFORE UPDATE OF invariant_revision_id, anchor_id, role, rationale, provenance ON realization_claim
BEGIN SELECT RAISE(ABORT, 'immutable_revision: a realization claim cannot be rewritten in place'); END
""",
}

# The minimum SQLite feature set this schema requires. A binding that lacks one of these
# cannot store the shape at all, so the store refuses before creating anything.
REQUIRED_SQLITE_FEATURES: tuple[str, ...] = (
    "strict_tables",
    "deferrable_foreign_keys",
    "trigger_raise_abort",
    "recursive_cte",
)


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
    Two datasets with the same fingerprint can be compared row by row; two with different
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


# The schema's name and version: those the registry's last generation declared, so the files this
# build creates and the files an earlier build created declare the same schema.
SCHEMA_NAME = "ar-knowledge-sqlite/v9"
SCHEMA_USER_VERSION = 9


# The structural fingerprint of the declared schema, as measured for the eight-table shape
# (MIK-R74). It is recorded as measured data and checked against the composition below by
# :func:`require_pinned_schema_unchanged`: a table definition that changes changes the
# fingerprint, and that is a finding, never a re-pin.
SCHEMA_FINGERPRINT = "4b8e3d8024215fde21c6700ab2846bdb1d7ddb350dfca2794560b67c665d34e5"


def _compose() -> SchemaGeneration:
    """Return the one schema: the eight logical tables the projection fills."""

    composed = SchemaGeneration(
        schema_name=SCHEMA_NAME,
        user_version=SCHEMA_USER_VERSION,
        tables=CANONICAL_TABLES,
        columns=CANONICAL_COLUMNS,
        primary_keys=PRIMARY_KEYS,
        json_columns=JSON_COLUMNS,
        features=REQUIRED_SQLITE_FEATURES,
        table_ddl=TABLE_DDL,
        index_ddl=INDEX_DDL,
        triggers=IMMUTABILITY_TRIGGERS,
        fingerprint="",
    )
    return replace(composed, fingerprint=structure_fingerprint(composed))


# The one schema. What a created file declares, and the only version an opened file may declare.
CURRENT_GENERATION = _compose()


def require_pinned_schema_unchanged() -> None:
    """Fail loudly when the declared schema no longer fingerprints as its recorded constant."""

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
