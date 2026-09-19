"""Generation 8's appended table: the edge by which one semantic change set supersedes another.

This module owns **only what generation 8 appends**. Generation 1's ten tables, generation 2's six,
generation 3's four, generation 4's one, generation 5's one, generation 6's six and generation 7's
five stay declared, verbatim, in :mod:`…schema`, :mod:`…schema_v2`, :mod:`…schema_v3`,
:mod:`…schema_v4`, :mod:`…schema_v5`, :mod:`…schema_v6` and :mod:`…schema_v7`; nothing here
redeclares, reorders, renames, retypes or drops one of them, and no ``ALTER TABLE`` against an
earlier generation's table appears anywhere in this package. ``KS-R10@v1`` §1.3 makes appending the
only sanctioned way to add a table, and generation 8's own case asserts
``GENERATION_8.columns[table] == GENERATION_7.columns[table]`` for every one of generation 7's
thirty-three names.

Why this leaf appends **one** table rather than a record group of its own:

* The change set, the effect claim, the preservation claim and the unresolved question are *typed
  records*, and ``KS-R10@v1`` already built the envelope they live in: ``knowledge_record`` carries
  the kind, the authority home, the lifecycle and the governing route, and ``record_revision``
  carries the frozen payload and its content digest. Their payload shapes are registered in
  :data:`agents_remember.memory.knowledge.record_envelope.PAYLOAD_MODELS` under
  ``(invariant_effect_claim, invariant-effect-claim/v1)``, ``(preservation_claim,
  preservation-claim/v1)``, ``(unresolved_question, unresolved-question/v1)`` and
  ``(semantic_change_set, semantic-change-set/v1)``, exactly as the requirement record group's is, so
  each record's own required field set and closed vocabularies are declared once rather than
  restated as columns here.
* What the envelope **cannot** express is a **record-to-record lineage edge**. Its
  ``record_revision.predecessor_revision_id`` is a revision-to-revision edge *inside* one record, and
  the shipped envelope has no record-level predecessor at all. Requirement 4.8 makes the change-set
  succession exactly that: a revised change set "is a new record with a predecessor edge -- not an
  edited row, and not a distinct record kind", where "the successor is a new ``SemanticChangeSet``
  naming its exact predecessor, and the superseded row stays addressable", and where the edge "is
  inserted inside the successor's own creation batch". The shipped technique it names is the one the
  invariant and family graphs already use, so the edge is a row here rather than a field on the
  successor.
* ``(repository_id, successor_change_set_id, predecessor_change_set_id)`` is the primary key, so one
  successor cannot record the same predecessor twice, and ``CHECK (successor_change_set_id <>
  predecessor_change_set_id)`` refuses the one-node cycle in the schema rather than only in the write
  path. The longer cycle is found by the shared acyclic walk over this table, run inside the same
  transaction (the same rule and the same scan the two predecessor graphs and the decision
  supersession edge use).
* Both endpoints are foreign keys to ``knowledge_record``, so an edge can only name records the same
  store holds. That the named records are change sets -- and not some other kind of envelope record
  -- is the write path's check, exactly as the decision supersession edge's endpoint kind is, because
  a foreign key addresses a table and not a kind.
* The table is sealed against update and delete by triggers, so "a predecessor change set is never
  rewritten by the arrival of its successor" is a property of the schema rather than a rule the write
  path remembers.

No effect claim, preservation claim, unresolved question or change-set row carries a content address,
a logical digest or a fingerprint column, and none is added here: requirement 3.7 of the envelope's
own contract puts the content digest on ``record_revision``, where ``Doc13:85`` already puts it.
"""

from __future__ import annotations

from collections.abc import Mapping

# Every table this generation appends, in the order the encoder serializes them. Appended after
# generation 7's thirty-three, so generation 8's manifest begins with generation 7's, unchanged.
APPENDED_TABLES: tuple[str, ...] = ("change_set_predecessor",)

APPENDED_COLUMNS: Mapping[str, tuple[str, ...]] = {
    "change_set_predecessor": (
        "repository_id",
        "successor_change_set_id",
        "predecessor_change_set_id",
        "provenance",
    ),
}

# The declared primary key, as the DDL declares it. The encoder orders a table's rows by this tuple,
# so it is part of the generation's pinned structure rather than a derivation from the DDL text.
APPENDED_PRIMARY_KEYS: Mapping[str, tuple[str, ...]] = {
    "change_set_predecessor": (
        "repository_id",
        "successor_change_set_id",
        "predecessor_change_set_id",
    ),
}

# ``provenance`` is a typed JSON value like every other shipped provenance column, so it is declared
# here rather than left for a reader to infer from an absence.
APPENDED_JSON_COLUMNS: Mapping[str, frozenset[str]] = {
    "change_set_predecessor": frozenset({"provenance"}),
}

APPENDED_TABLE_DDL: Mapping[str, str] = {
    "change_set_predecessor": """
CREATE TABLE change_set_predecessor (
  repository_id TEXT NOT NULL,
  successor_change_set_id TEXT NOT NULL,
  predecessor_change_set_id TEXT NOT NULL,
  provenance TEXT NOT NULL,
  PRIMARY KEY (repository_id, successor_change_set_id, predecessor_change_set_id),
  CHECK (successor_change_set_id <> predecessor_change_set_id),
  FOREIGN KEY (repository_id) REFERENCES repository(repository_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (repository_id, successor_change_set_id)
    REFERENCES knowledge_record(repository_id, record_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (repository_id, predecessor_change_set_id)
    REFERENCES knowledge_record(repository_id, record_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED
) STRICT
""",
}

# Index names are a local choice; this one covers the reverse direction of a declared lookup -- "which
# change sets supersede this one" -- which is the same reason the earlier generations declare theirs.
APPENDED_INDEX_DDL: tuple[str, ...] = (
    "CREATE INDEX change_set_predecessor_parent ON change_set_predecessor "
    "(repository_id, predecessor_change_set_id)",
)

# The same trigger idiom the earlier generations use: the operation's preconditions return a typed
# refusal, and these exist so a changeset, a repair script or a future code path that forgot the rule
# still cannot rewrite or drop a recorded succession edge.
APPENDED_TRIGGERS: Mapping[str, str] = {
    "change_set_predecessor_no_update": """
CREATE TRIGGER change_set_predecessor_no_update
BEFORE UPDATE ON change_set_predecessor
BEGIN SELECT RAISE(ABORT, 'immutable_revision: a recorded change-set succession cannot be updated'); END
""",
    "change_set_predecessor_no_delete": """
CREATE TRIGGER change_set_predecessor_no_delete BEFORE DELETE ON change_set_predecessor
BEGIN SELECT RAISE(ABORT, 'immutable_revision: a recorded change-set succession cannot be deleted'); END
""",
}

# Generation 8 needs no SQLite feature generation 7 does not already require: it adds one table with
# checked foreign-key groups, a composite key, an index and triggers, all of which ``strict_tables``,
# ``deferrable_foreign_keys``, ``trigger_raise_abort`` and ``json_functions`` already cover. The tuple
# is declared (empty) rather than omitted so the composition of generation 8 states the same fact
# generation 7's does.
APPENDED_FEATURES: tuple[str, ...] = ()
