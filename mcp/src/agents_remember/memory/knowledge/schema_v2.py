"""Generation 2's appended tables: the record envelope, the route, and the governing joins.

This module owns **only what generation 2 appends**. Generation 1's ten tables stay declared,
verbatim, in :mod:`agents_remember.memory.knowledge.schema`; nothing here redeclares, reorders,
renames, retypes or drops one of them, and no ``ALTER TABLE`` against a generation-1 table
appears anywhere in this package. Requirement 1.3 makes that a hard rule rather than a
convention -- a generation that changed a column of an earlier generation is a schema
divergence, not a generation -- and Example 1 makes it checkable:
``GENERATION_2_TABLES[: len(GENERATION_1_TABLES)] == GENERATION_1_TABLES``.

Why the tables are shaped this way:

* ``route`` carries a repository-relative code-path scope and a self-referencing parent. The
  self-reference is ``DEFERRABLE INITIALLY DEFERRED`` like every shipped foreign key, so one
  transaction may insert a whole hierarchy without depending on insert order; the one-node cycle
  is a ``CHECK``, and the longer cycle is found by a recursive-CTE walk run inside the same
  transaction before commit (:mod:`agents_remember.memory.knowledge.routes`).
* ``knowledge_record`` is the envelope. It carries **no** content address, logical digest or
  fingerprint column, and that absence is what enforces requirement 3.3: a table with no
  identity-valued column cannot quietly become a second identity authority later.
  ``record_schema`` names the frozen payload shape its ``kind`` resolves to; it is not a
  fingerprint.
* ``record_revision`` is where the payload lives, because ``Doc13:85`` puts "revision
  payload/schema" on the revision rather than on the envelope. ``payload`` is the typed-JSON
  column -- ``TEXT NOT NULL`` plus ``json_valid``, which is what ``storage-design.md:97`` means
  by ``TEXT_JSON`` -- and generation 2 therefore adds ``json_functions`` to its required feature
  set.
* The three ``*_route`` tables associate a shipped generation-1 entity with the route that
  governs it. The association is expressed here, in generation-2 tables, and never as a new
  column on a generation-1 table (requirement 4.4). Each has the governed entity's key as its
  own primary key, which makes "at most one governing route per governed row" a constraint
  rather than a convention, and each foreign key names one exact table, which is the
  endpoint-kind compatibility ``Doc13:102`` requires.
"""

from __future__ import annotations

from collections.abc import Mapping

# Every table this generation appends, in the order the encoder serializes them. Appended after
# generation 1's ten, so generation 2's manifest begins with generation 1's, unchanged.
APPENDED_TABLES: tuple[str, ...] = (
    "route",
    "knowledge_record",
    "record_revision",
    "source_anchor_route",
    "invariant_route",
    "family_route",
)

APPENDED_COLUMNS: Mapping[str, tuple[str, ...]] = {
    "route": ("repository_id", "route_id", "parent_route_id", "path", "provenance"),
    "knowledge_record": (
        "repository_id",
        "record_id",
        "kind",
        "authority_home",
        "lifecycle",
        "governing_route_id",
        "record_schema",
        "provenance",
    ),
    "record_revision": (
        "repository_id",
        "revision_id",
        "record_id",
        "record_schema",
        "payload",
        "predecessor_revision_id",
        "content_digest",
        "provenance",
    ),
    "source_anchor_route": ("repository_id", "anchor_id", "route_id", "provenance"),
    "invariant_route": ("repository_id", "invariant_id", "route_id", "provenance"),
    "family_route": ("repository_id", "family_id", "route_id", "provenance"),
}

# The declared primary key of each appended table, as its DDL declares it. The encoder orders a
# table's rows by this tuple, so it is part of the generation's pinned structure rather than a
# derivation from the DDL text.
APPENDED_PRIMARY_KEYS: Mapping[str, tuple[str, ...]] = {
    "route": ("repository_id", "route_id"),
    "knowledge_record": ("repository_id", "record_id"),
    "record_revision": ("repository_id", "revision_id"),
    "source_anchor_route": ("repository_id", "anchor_id"),
    "invariant_route": ("repository_id", "invariant_id"),
    "family_route": ("repository_id", "family_id"),
}

# The columns whose stored text is a typed JSON value, decoded at the portable boundary. The
# shipped generation declares every ``provenance`` column this way, and this generation keeps the
# idiom: a provenance record is structured data, not opaque text. ``payload`` is here because it
# is the typed-JSON column the envelope exists to carry.
APPENDED_JSON_COLUMNS: Mapping[str, frozenset[str]] = {
    "route": frozenset({"provenance"}),
    "knowledge_record": frozenset({"provenance"}),
    "record_revision": frozenset({"payload", "provenance"}),
    "source_anchor_route": frozenset({"provenance"}),
    "invariant_route": frozenset({"provenance"}),
    "family_route": frozenset({"provenance"}),
}

APPENDED_TABLE_DDL: Mapping[str, str] = {
    "route": """
CREATE TABLE route (
  repository_id TEXT NOT NULL,
  route_id TEXT NOT NULL,
  parent_route_id TEXT,
  path TEXT NOT NULL,
  provenance TEXT NOT NULL,
  PRIMARY KEY (repository_id, route_id),
  CHECK (parent_route_id IS NULL OR parent_route_id <> route_id),
  FOREIGN KEY (repository_id) REFERENCES repository(repository_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (repository_id, parent_route_id)
    REFERENCES route(repository_id, route_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED
) STRICT
""",
    "knowledge_record": """
CREATE TABLE knowledge_record (
  repository_id TEXT NOT NULL,
  record_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  authority_home TEXT NOT NULL,
  lifecycle TEXT NOT NULL,
  governing_route_id TEXT,
  record_schema TEXT NOT NULL,
  provenance TEXT NOT NULL,
  PRIMARY KEY (repository_id, record_id),
  FOREIGN KEY (repository_id) REFERENCES repository(repository_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (repository_id, governing_route_id)
    REFERENCES route(repository_id, route_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED
) STRICT
""",
    "record_revision": """
CREATE TABLE record_revision (
  repository_id TEXT NOT NULL,
  revision_id TEXT NOT NULL,
  record_id TEXT NOT NULL,
  record_schema TEXT NOT NULL,
  payload TEXT NOT NULL CHECK (json_valid(payload)),
  predecessor_revision_id TEXT,
  content_digest TEXT NOT NULL,
  provenance TEXT NOT NULL,
  PRIMARY KEY (repository_id, revision_id),
  FOREIGN KEY (repository_id) REFERENCES repository(repository_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (repository_id, record_id)
    REFERENCES knowledge_record(repository_id, record_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED
) STRICT
""",
    "source_anchor_route": """
CREATE TABLE source_anchor_route (
  repository_id TEXT NOT NULL,
  anchor_id TEXT NOT NULL,
  route_id TEXT NOT NULL,
  provenance TEXT NOT NULL,
  PRIMARY KEY (repository_id, anchor_id),
  FOREIGN KEY (repository_id, anchor_id)
    REFERENCES source_anchor(repository_id, anchor_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (repository_id, route_id)
    REFERENCES route(repository_id, route_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED
) STRICT
""",
    "invariant_route": """
CREATE TABLE invariant_route (
  repository_id TEXT NOT NULL,
  invariant_id TEXT NOT NULL,
  route_id TEXT NOT NULL,
  provenance TEXT NOT NULL,
  PRIMARY KEY (repository_id, invariant_id),
  FOREIGN KEY (repository_id, invariant_id)
    REFERENCES invariant(repository_id, invariant_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (repository_id, route_id)
    REFERENCES route(repository_id, route_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED
) STRICT
""",
    "family_route": """
CREATE TABLE family_route (
  repository_id TEXT NOT NULL,
  family_id TEXT NOT NULL,
  route_id TEXT NOT NULL,
  provenance TEXT NOT NULL,
  PRIMARY KEY (repository_id, family_id),
  FOREIGN KEY (repository_id, family_id)
    REFERENCES family(repository_id, family_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (repository_id, route_id)
    REFERENCES route(repository_id, route_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED
) STRICT
""",
}

# Index names are a local choice; each covers the reverse direction of a declared lookup, which is
# the same reason generation 1 declares its own.
APPENDED_INDEX_DDL: tuple[str, ...] = (
    "CREATE INDEX route_parent ON route (repository_id, parent_route_id)",
    "CREATE INDEX route_path ON route (repository_id, path)",
    "CREATE INDEX knowledge_record_kind ON knowledge_record (repository_id, kind)",
    "CREATE INDEX knowledge_record_governing_route "
    "ON knowledge_record (repository_id, governing_route_id)",
    "CREATE INDEX record_revision_record ON record_revision (repository_id, record_id)",
    "CREATE INDEX record_revision_predecessor "
    "ON record_revision (repository_id, predecessor_revision_id)",
    "CREATE INDEX source_anchor_route_route ON source_anchor_route (repository_id, route_id)",
    "CREATE INDEX invariant_route_route ON invariant_route (repository_id, route_id)",
    "CREATE INDEX family_route_route ON family_route (repository_id, route_id)",
)

# The same trigger idiom generation 1 uses: the operation's preconditions return a typed refusal,
# and these exist so a changeset, a repair script or a future code path that forgot the rule still
# cannot rewrite a sealed revision or repoint a sealed association.
APPENDED_TRIGGERS: Mapping[str, str] = {
    "record_revision_no_rewrite": """
CREATE TRIGGER record_revision_no_rewrite
BEFORE UPDATE OF record_schema, payload, predecessor_revision_id, content_digest, provenance
ON record_revision
BEGIN SELECT RAISE(ABORT, 'immutable_revision: a record revision cannot be rewritten in place'); END
""",
    "record_revision_no_delete": """
CREATE TRIGGER record_revision_no_delete BEFORE DELETE ON record_revision
BEGIN SELECT RAISE(ABORT, 'immutable_revision: a sealed record revision cannot be deleted'); END
""",
    "knowledge_record_no_rebind": """
CREATE TRIGGER knowledge_record_no_rebind
BEFORE UPDATE OF repository_id, record_id, kind, record_schema ON knowledge_record
BEGIN SELECT RAISE(ABORT, 'immutable_revision: a record envelope cannot be rebound'); END
""",
    "route_no_rebind": """
CREATE TRIGGER route_no_rebind BEFORE UPDATE OF repository_id, route_id ON route
BEGIN SELECT RAISE(ABORT, 'immutable_revision: a route identity cannot be rebound'); END
""",
    "route_no_delete": """
CREATE TRIGGER route_no_delete BEFORE DELETE ON route
BEGIN SELECT RAISE(ABORT, 'immutable_revision: a route cannot be deleted while it may govern rows'); END
""",
    "source_anchor_route_no_repoint": """
CREATE TRIGGER source_anchor_route_no_repoint BEFORE UPDATE OF route_id ON source_anchor_route
BEGIN SELECT RAISE(ABORT, 'immutable_revision: a governing route cannot be repointed in place'); END
""",
    "invariant_route_no_repoint": """
CREATE TRIGGER invariant_route_no_repoint BEFORE UPDATE OF route_id ON invariant_route
BEGIN SELECT RAISE(ABORT, 'immutable_revision: a governing route cannot be repointed in place'); END
""",
    "family_route_no_repoint": """
CREATE TRIGGER family_route_no_repoint BEFORE UPDATE OF route_id ON family_route
BEGIN SELECT RAISE(ABORT, 'immutable_revision: a governing route cannot be repointed in place'); END
""",
}

# Generation 1's features plus the one generation 2 needs: ``json_valid`` in a ``CHECK``. It is
# part of the fingerprint because it is part of the manifest, so it is declared rather than
# assumed.
APPENDED_FEATURES: tuple[str, ...] = ("json_functions",)
