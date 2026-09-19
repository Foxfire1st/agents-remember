"""Generation 5's appended table: the authored ``CitationBinding`` record.

This module owns **only what generation 5 appends**. Generation 1's ten tables, generation 2's six,
generation 3's four and generation 4's one stay declared, verbatim, in :mod:`…schema`,
:mod:`…schema_v2`, :mod:`…schema_v3` and :mod:`…schema_v4`; nothing here redeclares, reorders,
renames, retypes or drops one of them, and no ``ALTER TABLE`` against an earlier generation's table
appears anywhere in this package. ``KS-R10@v1`` §1.3 makes appending the only sanctioned way to add a
table, and generation 5's own case asserts ``GENERATION_5.columns[table] == GENERATION_4.columns[table]``
for every one of generation 4's twenty-one names.

Why one table, and why these columns:

* **The binding is one authored row.** ``citation_binding`` carries the prose owner revision (the
  memory repository, the confined document path, and the recorded blob identity of that document),
  the local citation key as written (its declared form and its exact text), the typed target
  reference (record id, declared kind, optional exact revision) and the locator, plus the recorded
  governing route. Every one of those is an authored fact; nothing on the row is derivable from
  anything else on it, which is why none of them is a computed column.

* **There is no digest, content address or fingerprint column here, and the absence is the point.**
  A citation binding is the most tempting place in this extension to add "just one digest" -- of the
  document, of the binding, of the target -- and ``KS-R18@v1`` §1.8 forbids it. The owner revision's
  blob identity is a *reference* to an identity the memory side already records, not a second one
  minted here. Ambiguity ("more than one binding claims one key in one owner revision") is therefore
  detected by equality over the **recorded key text**, which is exactly the fact at issue, instead
  of by a computed digest that would have to be kept in step with it.

* **The local key is stored as a form and its exact text.** Storing the text as written is what
  makes "a rewritten key is a different key" checkable by comparison rather than by re-parsing, and
  ``UNIQUE (repository_id, owner_document_path, owner_blob_object_id, local_key_form,
  local_key_text)`` makes "one owner revision records one key once" a constraint of the table rather
  than a rule the write path remembers. Two bindings that claim the same key cannot both be stored,
  so a claimed ambiguity is a fact an operator can only create deliberately -- which is what the
  ambiguity state reports about rather than resolving.

* **``target_locator_kind`` is checked against the shipped union.** The locator itself travels in the
  revision payload as the typed :data:`SourceLocator` union, and this column is the discriminator it
  must agree with. The ``CHECK`` names the union's three members and no fourth, so a binding-local
  locator spelling is inexpressible rather than merely unwritten -- the failure mode a parallel
  vocabulary would produce.

* **The governing route is a nullable foreign key on this row.** Requirement 1.6 puts the binding's
  governing-route association in the binding's own table, resolved against the existing ``route``
  entity: a single nullable column makes "at most one governing route per binding" a constraint
  rather than a convention, and a real foreign key makes "a named route that does not exist"
  unrepresentable. ``NULL`` is the explicit **ungoverned** state and is never defaulted to a
  repository root or to any other route.

* **The recorded facts are sealed by triggers.** A binding is a claim about what a sentence cited in
  one exact revision of one document; rewriting its key or re-pointing its target in place would
  silently re-bind it to prose that says something else. Generation 5's triggers refuse exactly
  that, while leaving ``lifecycle`` free -- a binding is a record whose lifecycle is its owner's,
  and the envelope's lifecycle column is where a record states it.
"""

from __future__ import annotations

from collections.abc import Mapping

# Every table this generation appends, in the order the encoder serializes them. Appended after
# generation 4's twenty-one, so generation 5's manifest begins with generation 4's, unchanged.
APPENDED_TABLES: tuple[str, ...] = ("citation_binding",)

APPENDED_COLUMNS: Mapping[str, tuple[str, ...]] = {
    "citation_binding": (
        "repository_id",
        "binding_id",
        "owner_document_path",
        "owner_blob_object_id",
        "local_key_form",
        "local_key_text",
        "target_record_id",
        "target_record_kind",
        "target_revision_id",
        "target_locator_kind",
        "governing_route_id",
        "provenance",
    ),
}

# The declared primary key, as the DDL declares it. The encoder orders a table's rows by this tuple,
# so it is part of the generation's pinned structure rather than a derivation from the DDL text.
APPENDED_PRIMARY_KEYS: Mapping[str, tuple[str, ...]] = {
    "citation_binding": ("repository_id", "binding_id"),
}

# ``provenance`` is the typed-JSON column, on the shipped idiom: a provenance record is structured
# data rather than opaque text. Nothing else here is typed JSON -- the locator travels inside the
# sealed revision payload, which is generation 2's typed-JSON column, and the local key's exact text
# is an opaque authored string whose whole meaning is that it is byte-for-byte what the prose wrote.
APPENDED_JSON_COLUMNS: Mapping[str, frozenset[str]] = {
    "citation_binding": frozenset({"provenance"}),
}

APPENDED_TABLE_DDL: Mapping[str, str] = {
    "citation_binding": """
CREATE TABLE citation_binding (
  repository_id TEXT NOT NULL,
  binding_id TEXT NOT NULL,
  owner_document_path TEXT NOT NULL,
  owner_blob_object_id TEXT NOT NULL,
  local_key_form TEXT NOT NULL,
  local_key_text TEXT NOT NULL,
  target_record_id TEXT NOT NULL,
  target_record_kind TEXT NOT NULL,
  target_revision_id TEXT,
  target_locator_kind TEXT NOT NULL,
  governing_route_id TEXT,
  provenance TEXT NOT NULL,
  PRIMARY KEY (repository_id, binding_id),
  UNIQUE (
    repository_id, owner_document_path, owner_blob_object_id, local_key_form, local_key_text
  ),
  CHECK (local_key_form IN ('prose_cit_body', 'table_row_anchor_source')),
  CHECK (target_locator_kind IN ('file', 'line_range', 'symbol')),
  CHECK (length(owner_document_path) > 0),
  CHECK (length(local_key_text) > 0),
  FOREIGN KEY (repository_id) REFERENCES repository(repository_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (repository_id, target_record_id)
    REFERENCES knowledge_record(repository_id, record_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (repository_id, governing_route_id)
    REFERENCES route(repository_id, route_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED
) STRICT
""",
}

# Index names are a local choice; each covers the reverse direction of a declared lookup, which is
# the same reason the earlier generations declare their own. The owner-revision index is the one the
# closure read's declared selected set is assembled through; the target index is the one "which
# prose cites this record" resolves through.
APPENDED_INDEX_DDL: tuple[str, ...] = (
    "CREATE INDEX citation_binding_owner_revision "
    "ON citation_binding (repository_id, owner_document_path, owner_blob_object_id)",
    "CREATE INDEX citation_binding_target_record "
    "ON citation_binding (repository_id, target_record_id)",
    "CREATE INDEX citation_binding_governing_route "
    "ON citation_binding (repository_id, governing_route_id)",
)

# The same trigger idiom the earlier generations use: the operation's preconditions return a typed
# refusal, and these exist so a changeset, a repair script or a future code path that forgot the
# rule still cannot re-bind a recorded citation or drop one. ``lifecycle`` is deliberately outside
# both guards' column lists -- a record's lifecycle is the envelope's own field and this leaf does
# not make a binding's lifecycle immutable.
APPENDED_TRIGGERS: Mapping[str, str] = {
    "citation_binding_no_rebind": """
CREATE TRIGGER citation_binding_no_rebind
BEFORE UPDATE OF owner_document_path, owner_blob_object_id, local_key_form, local_key_text,
  target_record_id, target_record_kind, target_revision_id, target_locator_kind
ON citation_binding
BEGIN SELECT RAISE(ABORT, 'immutable_revision: a recorded citation binding cannot be re-bound'); END
""",
    "citation_binding_no_delete": """
CREATE TRIGGER citation_binding_no_delete BEFORE DELETE ON citation_binding
BEGIN SELECT RAISE(ABORT, 'immutable_revision: a recorded citation binding cannot be dropped'); END
""",
}

# Generation 5 needs no SQLite feature generation 4 does not already require: it adds one table with
# checked foreign-key groups, a composite unique key, indexes and triggers, all of which
# ``strict_tables``, ``deferrable_foreign_keys``, ``trigger_raise_abort`` and ``json_functions``
# already cover. The tuple is declared (empty) rather than omitted so the composition of generation 5
# states the same fact generation 4's does.
APPENDED_FEATURES: tuple[str, ...] = ()
