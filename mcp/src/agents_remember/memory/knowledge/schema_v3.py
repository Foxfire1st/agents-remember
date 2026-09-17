"""Generation 3's appended tables: the facet attachments, the supersession edge, the explanation pair.

This module owns **only what generation 3 appends**. Generation 1's ten tables and generation 2's
six stay declared, verbatim, in :mod:`agents_remember.memory.knowledge.schema` and
:mod:`agents_remember.memory.knowledge.schema_v2`; nothing here redeclares, reorders, renames,
retypes or drops one of them, and no ``ALTER TABLE`` against an earlier generation's table appears
anywhere in this package. ``KS-R10@v1`` §1.3 makes appending the only sanctioned way to add a table,
and a case asserts ``GENERATION_3.columns[table] == GENERATION_2.columns[table]`` for every one of
generation 2's sixteen names.

Why the tables are shaped this way:

* ``facet_attachment`` is one row per authored attachment, naming one **exact** endpoint revision
  (requirement 4.3). Endpoint-kind compatibility is structural rather than polymorphic: there is a
  foreign-key column per endpoint kind, a ``CHECK`` that the populated group matches the stored
  ``endpoint_kind`` and that the other groups are ``NULL``, and a real foreign key per group. The
  forbidden shape -- ``(facet_revision_id, endpoint_kind, endpoint_id)``, where a misspelled kind, a
  dangling id and a wrong-kind target all store successfully -- is not expressible here: there is no
  ``endpoint_id`` column for an unchecked identity to land in.
* ``facet_decision_supersession`` is the shipped predecessor idiom applied to the decision record
  kind: composite foreign keys to both exact revision endpoints, ``CHECK(child <> parent)``, and the
  graph walk over it is the shared lineage rule (:mod:`agents_remember.memory.knowledge.lineage`) --
  not a second cycle check (requirement 5.2).
* ``explanation`` is the separable record (requirement 6.1). Its subject is an exact statement
  revision of one of the two declared subject kinds, and it is expressed the same structural way the
  attachment is -- with one addition the attachment does not need: the subject's *identity* is part
  of the checked group too, so the foreign key is the three-column ``(repository_id, identity,
  revision_id)`` reference both revision tables already declare a ``UNIQUE`` key for. That makes
  "this revision really is a revision of the statement identity it claims" a constraint of the
  table rather than a check the write path remembers, and it means the stored row reproduces the
  whole subject without a denormalised copy that could disagree with the revision table.
  ``current_revision_id`` is the **recorded designation** (requirement 6.3) and is the one mutable
  field of this row.
* ``explanation_revision`` is the append-only body table (requirement 6.2). ``predecessor_revision_id``
  makes an edit a successor naming its exact predecessor, and the trigger set refuses an in-place
  rewrite of a sealed revision.
* The two tables reference each other (``explanation.current_revision_id`` ->
  ``explanation_revision`` and ``explanation_revision.explanation_id`` -> ``explanation``), which is
  exactly what ``NO ACTION ... DEFERRABLE INITIALLY DEFERRED`` is for: one transaction may insert the
  record, its first revision and its designation without depending on statement order.

No facet record carries a content address, a logical digest or a fingerprint column, and none is
added here: a facet's revision digest is ``record_revision.content_digest``, on the sealed revision
aggregate that owns it (requirement 7.2).
"""

from __future__ import annotations

from collections.abc import Mapping

# Every table this generation appends, in the order the encoder serializes them. Appended after
# generation 2's sixteen, so generation 3's manifest begins with generation 2's, unchanged.
APPENDED_TABLES: tuple[str, ...] = (
    "facet_attachment",
    "facet_decision_supersession",
    "explanation",
    "explanation_revision",
)

APPENDED_COLUMNS: Mapping[str, tuple[str, ...]] = {
    "facet_attachment": (
        "repository_id",
        "attachment_id",
        "facet_revision_id",
        "endpoint_kind",
        "invariant_revision_id",
        "family_revision_id",
        "anchor_id",
        "claim_id",
        "provenance",
    ),
    "facet_decision_supersession": (
        "repository_id",
        "superseding_revision_id",
        "superseded_revision_id",
        "provenance",
    ),
    "explanation": (
        "repository_id",
        "explanation_id",
        "subject_kind",
        "subject_invariant_id",
        "subject_invariant_revision_id",
        "subject_family_id",
        "subject_family_revision_id",
        "current_revision_id",
        "provenance",
    ),
    "explanation_revision": (
        "repository_id",
        "explanation_id",
        "revision_id",
        "predecessor_revision_id",
        "body",
        "payload_digest",
        "provenance",
    ),
}

# The declared primary key of each appended table, as its DDL declares it. The encoder orders a
# table's rows by this tuple, so it is part of the generation's pinned structure rather than a
# derivation from the DDL text.
APPENDED_PRIMARY_KEYS: Mapping[str, tuple[str, ...]] = {
    "facet_attachment": ("repository_id", "attachment_id"),
    "facet_decision_supersession": (
        "repository_id",
        "superseding_revision_id",
        "superseded_revision_id",
    ),
    "explanation": ("repository_id", "explanation_id"),
    "explanation_revision": ("repository_id", "revision_id"),
}

# The columns whose stored text is a typed JSON value, decoded at the portable boundary. Every
# ``provenance`` column in this generation is one, on the shipped idiom; ``body`` and
# ``payload_digest`` are not, because an explanation body is authored prose rather than structured
# data and its digest is a single hex string.
APPENDED_JSON_COLUMNS: Mapping[str, frozenset[str]] = {
    "facet_attachment": frozenset({"provenance"}),
    "facet_decision_supersession": frozenset({"provenance"}),
    "explanation": frozenset({"provenance"}),
    "explanation_revision": frozenset({"provenance"}),
}

APPENDED_TABLE_DDL: Mapping[str, str] = {
    "facet_attachment": """
CREATE TABLE facet_attachment (
  repository_id TEXT NOT NULL,
  attachment_id TEXT NOT NULL,
  facet_revision_id TEXT NOT NULL,
  endpoint_kind TEXT NOT NULL,
  invariant_revision_id TEXT,
  family_revision_id TEXT,
  anchor_id TEXT,
  claim_id TEXT,
  provenance TEXT NOT NULL,
  PRIMARY KEY (repository_id, attachment_id),
  CHECK (
    (endpoint_kind = 'invariant_revision' AND invariant_revision_id IS NOT NULL
      AND family_revision_id IS NULL AND anchor_id IS NULL AND claim_id IS NULL)
    OR (endpoint_kind = 'family_revision' AND family_revision_id IS NOT NULL
      AND invariant_revision_id IS NULL AND anchor_id IS NULL AND claim_id IS NULL)
    OR (endpoint_kind = 'source_anchor' AND anchor_id IS NOT NULL
      AND invariant_revision_id IS NULL AND family_revision_id IS NULL AND claim_id IS NULL)
    OR (endpoint_kind = 'realization_claim' AND claim_id IS NOT NULL
      AND invariant_revision_id IS NULL AND family_revision_id IS NULL AND anchor_id IS NULL)
  ),
  FOREIGN KEY (repository_id) REFERENCES repository(repository_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (repository_id, facet_revision_id)
    REFERENCES record_revision(repository_id, revision_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (repository_id, invariant_revision_id)
    REFERENCES invariant_revision(repository_id, revision_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (repository_id, family_revision_id)
    REFERENCES family_revision(repository_id, revision_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (repository_id, anchor_id)
    REFERENCES source_anchor(repository_id, anchor_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (repository_id, claim_id)
    REFERENCES realization_claim(repository_id, claim_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED
) STRICT
""",
    "facet_decision_supersession": """
CREATE TABLE facet_decision_supersession (
  repository_id TEXT NOT NULL,
  superseding_revision_id TEXT NOT NULL,
  superseded_revision_id TEXT NOT NULL,
  provenance TEXT NOT NULL,
  PRIMARY KEY (repository_id, superseding_revision_id, superseded_revision_id),
  CHECK (superseding_revision_id <> superseded_revision_id),
  FOREIGN KEY (repository_id) REFERENCES repository(repository_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (repository_id, superseding_revision_id)
    REFERENCES record_revision(repository_id, revision_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (repository_id, superseded_revision_id)
    REFERENCES record_revision(repository_id, revision_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED
) STRICT
""",
    "explanation": """
CREATE TABLE explanation (
  repository_id TEXT NOT NULL,
  explanation_id TEXT NOT NULL,
  subject_kind TEXT NOT NULL,
  subject_invariant_id TEXT,
  subject_invariant_revision_id TEXT,
  subject_family_id TEXT,
  subject_family_revision_id TEXT,
  current_revision_id TEXT,
  provenance TEXT NOT NULL,
  PRIMARY KEY (repository_id, explanation_id),
  CHECK (
    (subject_kind = 'invariant_revision' AND subject_invariant_id IS NOT NULL
      AND subject_invariant_revision_id IS NOT NULL AND subject_family_id IS NULL
      AND subject_family_revision_id IS NULL)
    OR (subject_kind = 'family_revision' AND subject_family_id IS NOT NULL
      AND subject_family_revision_id IS NOT NULL AND subject_invariant_id IS NULL
      AND subject_invariant_revision_id IS NULL)
  ),
  FOREIGN KEY (repository_id) REFERENCES repository(repository_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (repository_id, subject_invariant_id, subject_invariant_revision_id)
    REFERENCES invariant_revision(repository_id, invariant_id, revision_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (repository_id, subject_family_id, subject_family_revision_id)
    REFERENCES family_revision(repository_id, family_id, revision_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (repository_id, current_revision_id)
    REFERENCES explanation_revision(repository_id, revision_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED
) STRICT
""",
    "explanation_revision": """
CREATE TABLE explanation_revision (
  repository_id TEXT NOT NULL,
  explanation_id TEXT NOT NULL,
  revision_id TEXT NOT NULL,
  predecessor_revision_id TEXT,
  body TEXT NOT NULL,
  payload_digest TEXT NOT NULL,
  provenance TEXT NOT NULL,
  PRIMARY KEY (repository_id, revision_id),
  CHECK (predecessor_revision_id IS NULL OR predecessor_revision_id <> revision_id),
  FOREIGN KEY (repository_id) REFERENCES repository(repository_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (repository_id, explanation_id)
    REFERENCES explanation(repository_id, explanation_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (repository_id, predecessor_revision_id)
    REFERENCES explanation_revision(repository_id, revision_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED
) STRICT
""",
}

# Index names are a local choice; each covers the reverse direction of a declared lookup, which is
# the same reason the earlier generations declare their own.
APPENDED_INDEX_DDL: tuple[str, ...] = (
    "CREATE INDEX facet_attachment_facet_revision "
    "ON facet_attachment (repository_id, facet_revision_id)",
    "CREATE INDEX facet_attachment_invariant_revision "
    "ON facet_attachment (repository_id, invariant_revision_id)",
    "CREATE INDEX facet_attachment_family_revision "
    "ON facet_attachment (repository_id, family_revision_id)",
    "CREATE INDEX facet_attachment_anchor ON facet_attachment (repository_id, anchor_id)",
    "CREATE INDEX facet_attachment_claim ON facet_attachment (repository_id, claim_id)",
    "CREATE INDEX facet_decision_supersession_superseded "
    "ON facet_decision_supersession (repository_id, superseded_revision_id)",
    "CREATE INDEX explanation_subject_invariant "
    "ON explanation (repository_id, subject_invariant_id, subject_invariant_revision_id)",
    "CREATE INDEX explanation_subject_family "
    "ON explanation (repository_id, subject_family_id, subject_family_revision_id)",
    "CREATE INDEX explanation_current_revision ON explanation (repository_id, current_revision_id)",
    "CREATE INDEX explanation_revision_explanation "
    "ON explanation_revision (repository_id, explanation_id)",
    "CREATE INDEX explanation_revision_predecessor "
    "ON explanation_revision (repository_id, predecessor_revision_id)",
)

# The same trigger idiom the earlier generations use: the operation's preconditions return a typed
# refusal, and these exist so a changeset, a repair script or a future code path that forgot the rule
# still cannot rewrite a sealed row or rebind a sealed association.
#
# Two absences are deliberate and are the point of the record kind they belong to:
# * ``facet_attachment`` has no delete trigger, because requirement 4.6's removal is an explicit
#   deletion of that attachment row and nothing else; it refuses an in-place repoint instead.
# * ``explanation`` has no whole-row update trigger, because ``current_revision_id`` is the one
#   mutable field (requirement 6.3). Its trigger names the columns that may not move, so the guard
#   is on everything except the recorded designation.
APPENDED_TRIGGERS: Mapping[str, str] = {
    "facet_attachment_no_repoint": """
CREATE TRIGGER facet_attachment_no_repoint
BEFORE UPDATE OF facet_revision_id, endpoint_kind, invariant_revision_id, family_revision_id,
  anchor_id, claim_id, provenance ON facet_attachment
BEGIN SELECT RAISE(ABORT, 'immutable_revision: an attachment cannot be repointed in place'); END
""",
    "facet_decision_supersession_no_update": """
CREATE TRIGGER facet_decision_supersession_no_update
BEFORE UPDATE ON facet_decision_supersession
BEGIN SELECT RAISE(ABORT, 'immutable_revision: a recorded supersession edge cannot be updated'); END
""",
    "facet_decision_supersession_no_delete": """
CREATE TRIGGER facet_decision_supersession_no_delete
BEFORE DELETE ON facet_decision_supersession
BEGIN SELECT RAISE(ABORT, 'immutable_revision: a recorded supersession edge cannot be deleted'); END
""",
    "explanation_no_rebind": """
CREATE TRIGGER explanation_no_rebind
BEFORE UPDATE OF repository_id, explanation_id, subject_kind, subject_invariant_id,
  subject_invariant_revision_id, subject_family_id, subject_family_revision_id ON explanation
BEGIN SELECT RAISE(ABORT, 'immutable_revision: an explanation subject cannot be rebound'); END
""",
    "explanation_no_delete": """
CREATE TRIGGER explanation_no_delete BEFORE DELETE ON explanation
BEGIN SELECT RAISE(ABORT, 'immutable_revision: an explanation record cannot be deleted'); END
""",
    "explanation_revision_no_rewrite": """
CREATE TRIGGER explanation_revision_no_rewrite
BEFORE UPDATE OF explanation_id, predecessor_revision_id, body, payload_digest, provenance
ON explanation_revision
BEGIN SELECT RAISE(ABORT, 'immutable_revision: a sealed explanation revision cannot be rewritten'); END
""",
    "explanation_revision_no_delete": """
CREATE TRIGGER explanation_revision_no_delete BEFORE DELETE ON explanation_revision
BEGIN SELECT RAISE(ABORT, 'immutable_revision: a sealed explanation revision cannot be deleted'); END
""",
}

# Generation 3 needs no SQLite feature generation 2 does not already require: it adds tables, checked
# foreign-key groups, indexes and triggers, all of which ``strict_tables``,
# ``deferrable_foreign_keys``, ``trigger_raise_abort`` and ``json_functions`` already cover. The
# tuple is declared (empty) rather than omitted so the composition of generation 3 states the same
# fact generation 2's does, and a reader does not have to infer "no new feature" from an absence.
APPENDED_FEATURES: tuple[str, ...] = ()
