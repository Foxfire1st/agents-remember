"""The versioned SQL schema, its manifest and its structural fingerprint.

One module owns the canonical table list, the exact DDL text and the required feature set, so
a reader can compare its view of a database against this manifest instead of against prose.

Two decisions are load-bearing here and are stated once:

* **Every canonical table is created by this version, including the ones L1 does not write.**
  A session changeset can only carry operations for tables both sides already have, and a
  table that exists on one side only is exactly the schema-mismatch the merge preflight must
  refuse. Creating the shape once, here, means later leaves extend behaviour inside a stable
  schema instead of migrating it.
* **Immutability is enforced by the database, not only by the operation.** A trigger refuses
  the write even when it arrives from a changeset, a repair script or a future code path that
  forgot the rule. The operation's own preconditions exist to return a *typed refusal*; the
  triggers exist so that forgetting them still cannot rewrite a sealed revision.
"""

from __future__ import annotations

from collections.abc import Mapping

from agents_remember.kernel.canonical_json import sha256_digest
from agents_remember.models.knowledge.context import KNOWLEDGE_SCHEMA_NAME

# SQLite's own schema counter for this shape. It changes only when the DDL changes.
SCHEMA_USER_VERSION = 1

# The declared manifest order. The logical encoder of a later leaf serializes the tables in
# exactly this order, including empty ones, so the order is part of the contract.
CANONICAL_TABLES: tuple[str, ...] = (
    "repository",
    "invariant",
    "invariant_revision",
    "invariant_predecessor",
    "family",
    "family_revision",
    "family_predecessor",
    "source_anchor",
    "family_member",
    "realization_claim",
)

# The declared column order per table. A reader that needs a column name for a positional
# changeset row (SQLite sessions report changed columns by position) uses this rather than
# asking the database, so a reordered column is a manifest mismatch and not a silent re-map.
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
    "invariant_predecessor": (
        "repository_id",
        "invariant_id",
        "child_revision_id",
        "parent_revision_id",
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
    "family_predecessor": (
        "repository_id",
        "family_id",
        "child_revision_id",
        "parent_revision_id",
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

# The declared primary key of each canonical table, as the DDL declares it. Row order inside a
# table is this key's order, so two databases compared by the logical encoder agree on ordering
# without either of them being asked how it happened to store its rows.
#
# These are the DDL's ``PRIMARY KEY`` column lists, which are not always the tables' leading
# columns: ``invariant_revision`` keys ``(repository_id, revision_id)`` while ``invariant_id`` sits
# between those two columns. A row is ordered by its key, not by the order its columns were
# declared in, and ``logical._require_declared_keys`` is what keeps this table honest against the
# column manifest rather than against an assumption about column order.
#
# They are declared here, beside the rest of generation 1's pinned structure, because they are
# part of what a generation *is*: a generation record carries its own key tuple per table, and a
# record cannot be assembled from a module that has to import the registry back to exist.
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

# The columns whose stored text is a typed JSON value. They are decoded at the portable boundary;
# everything else is compared as the exact stored text. ``json_valid`` appears nowhere in the
# generation-1 DDL, so this registry is declared data rather than something the DDL could be read
# for -- which is why a generation record carries it and a reader cannot derive it.
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
    "invariant_predecessor": """
CREATE TABLE invariant_predecessor (
  repository_id TEXT NOT NULL,
  invariant_id TEXT NOT NULL,
  child_revision_id TEXT NOT NULL,
  parent_revision_id TEXT NOT NULL,
  PRIMARY KEY (repository_id, invariant_id, child_revision_id, parent_revision_id),
  CHECK (child_revision_id <> parent_revision_id),
  FOREIGN KEY (repository_id, invariant_id, child_revision_id)
    REFERENCES invariant_revision(repository_id, invariant_id, revision_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (repository_id, invariant_id, parent_revision_id)
    REFERENCES invariant_revision(repository_id, invariant_id, revision_id)
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
    "family_predecessor": """
CREATE TABLE family_predecessor (
  repository_id TEXT NOT NULL,
  family_id TEXT NOT NULL,
  child_revision_id TEXT NOT NULL,
  parent_revision_id TEXT NOT NULL,
  PRIMARY KEY (repository_id, family_id, child_revision_id, parent_revision_id),
  CHECK (child_revision_id <> parent_revision_id),
  FOREIGN KEY (repository_id, family_id, child_revision_id)
    REFERENCES family_revision(repository_id, family_id, revision_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (repository_id, family_id, parent_revision_id)
    REFERENCES family_revision(repository_id, family_id, revision_id)
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
    "CREATE INDEX invariant_predecessor_parent_endpoint "
    "ON invariant_predecessor (repository_id, invariant_id, parent_revision_id)",
    "CREATE INDEX invariant_revision_invariant ON invariant_revision (repository_id, invariant_id)",
    "CREATE INDEX family_predecessor_parent_endpoint "
    "ON family_predecessor (repository_id, family_id, parent_revision_id)",
    "CREATE INDEX family_revision_family ON family_revision (repository_id, family_id)",
    "CREATE INDEX source_anchor_path ON source_anchor (repository_id, path)",
    "CREATE INDEX family_member_invariant_revision "
    "ON family_member (repository_id, invariant_revision_id)",
    "CREATE INDEX realization_claim_invariant_revision "
    "ON realization_claim (repository_id, invariant_revision_id)",
    "CREATE INDEX realization_claim_anchor ON realization_claim (repository_id, anchor_id)",
)

# The exact trigger set. Names are part of the contract: the merge preflight compares them,
# because a database whose constraints were quietly dropped is not the same schema.
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
    "invariant_predecessor_no_update": """
CREATE TRIGGER invariant_predecessor_no_update BEFORE UPDATE ON invariant_predecessor
BEGIN SELECT RAISE(ABORT, 'immutable_revision: a sealed predecessor edge cannot be updated'); END
""",
    "invariant_predecessor_no_delete": """
CREATE TRIGGER invariant_predecessor_no_delete BEFORE DELETE ON invariant_predecessor
BEGIN SELECT RAISE(ABORT, 'immutable_revision: a sealed predecessor edge cannot be deleted'); END
""",
    "family_revision_no_update": """
CREATE TRIGGER family_revision_no_update BEFORE UPDATE ON family_revision
BEGIN SELECT RAISE(ABORT, 'immutable_revision: family revision rows cannot be updated'); END
""",
    "family_revision_no_delete": """
CREATE TRIGGER family_revision_no_delete BEFORE DELETE ON family_revision
BEGIN SELECT RAISE(ABORT, 'immutable_revision: family revision rows cannot be deleted'); END
""",
    "family_predecessor_no_update": """
CREATE TRIGGER family_predecessor_no_update BEFORE UPDATE ON family_predecessor
BEGIN SELECT RAISE(ABORT, 'immutable_revision: a sealed family predecessor edge cannot be updated'); END
""",
    "family_predecessor_no_delete": """
CREATE TRIGGER family_predecessor_no_delete BEFORE DELETE ON family_predecessor
BEGIN SELECT RAISE(ABORT, 'immutable_revision: a sealed family predecessor edge cannot be deleted'); END
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


def schema_manifest() -> dict[str, object]:
    """Return the canonical structural manifest compared by readers and the merge preflight."""

    return {
        "schema": KNOWLEDGE_SCHEMA_NAME,
        "user_version": SCHEMA_USER_VERSION,
        "tables": {name: list(CANONICAL_COLUMNS[name]) for name in CANONICAL_TABLES},
        "triggers": sorted(IMMUTABILITY_TRIGGERS),
        "indexes": sorted(_index_name(statement) for statement in INDEX_DDL),
        "features": list(REQUIRED_SQLITE_FEATURES),
    }


def schema_fingerprint() -> str:
    """Return the fingerprint of this exact schema generation.

    The fingerprint covers the declared manifest, the DDL text, the trigger set and the index
    set. Two databases with the same fingerprint can be compared row by row; two with
    different fingerprints are different schemas whatever ``user_version`` says.
    """

    return sha256_digest(
        {
            "manifest": schema_manifest(),
            "table_ddl": {name: TABLE_DDL[name] for name in CANONICAL_TABLES},
            "trigger_ddl": {
                name: IMMUTABILITY_TRIGGERS[name] for name in sorted(IMMUTABILITY_TRIGGERS)
            },
            "index_ddl": list(INDEX_DDL),
        }
    )


def _index_name(statement: str) -> str:
    return statement.split(" ON ", 1)[0].removeprefix("CREATE INDEX ").strip()


def create_schema_statements() -> tuple[str, ...]:
    """Return the exact ordered DDL statements that create this schema generation."""

    statements: list[str] = [TABLE_DDL[name] for name in CANONICAL_TABLES]
    statements.extend(INDEX_DDL)
    statements.extend(IMMUTABILITY_TRIGGERS[name] for name in sorted(IMMUTABILITY_TRIGGERS))
    return tuple(statements)
