"""Generation 7's appended tables: the evidence claim and the verification observation.

This module owns **only what generation 7 appends**. Generations 1 to 6 stay declared, verbatim, in
:mod:`…schema`, :mod:`…schema_v2`, :mod:`…schema_v3`, :mod:`…schema_v4`, :mod:`…schema_v5` and
:mod:`…schema_v6`; nothing here redeclares,
reorders, renames, retypes or drops one of them, and no ``ALTER TABLE`` against an earlier
generation's table appears anywhere in this package. ``KS-R10@v1`` §1.3 makes appending the only
sanctioned way to add a table, and this generation's own case asserts
``GENERATION_7.columns[table] == GENERATION_6.columns[table]`` for every one of generation 6's
twenty-eight names.

Why five tables and not two:

* ``evidence_claim`` and ``verification_observation`` are **typed records**, and ``KS-R10@v1``
  already built the envelope they live in: ``knowledge_record`` carries the kind, the authority home,
  the lifecycle and the governing route, and ``record_revision`` carries the frozen payload and its
  content digest. Their payload shapes are registered in
  :data:`agents_remember.memory.knowledge.record_envelope.PAYLOAD_MODELS` under
  ``(evidence_claim, evidence-claim/v1)`` and
  ``(verification_observation, verification-observation/v1)``, so each record's own fields are
  declared once, in the envelope, rather than restated as columns.
* ``evidence_claim`` itself exists because a claim's subject and its claimed coverage are **resolved
  relations**, and a relation is a row. Putting them in the payload would make them unvalidated
  strings; putting them in one polymorphic column with an ``endpoint_id`` would make a misspelled
  kind, a dangling id and a wrong-kind target all store successfully -- which is the shape
  ``Doc13:102`` and ``KS-R12@v1`` §1.2 forbid for exactly the reason they forbid it as the foundation
  of a check.
* The three join tables are the structural half of the claim. ``evidence_claim_invariant_subject``
  and ``evidence_claim_facet_subject`` are one row per subject kind, so **the table IS the kind
  check**: a claim whose subject is an invariant revision cannot reach the facet table, because the
  only column that table declares references ``record_revision``. ``evidence_claim_coverage`` carries
  one checked foreign-key group per claimed-coverage kind, so the same structure holds for the list
  the author asserts.
* ``evidence_claim_coverage`` is the shipped ``facet_attachment`` idiom -- a stored ``coverage_kind``
  with one nullable foreign-key column per kind and a ``CHECK`` that the populated group matches the
  kind -- plus one column the attachment does not need, and the reason is the key. A claim's coverage
  is a *set*, not a list of independent rows: one endpoint is covered once. SQLite does not compare
  ``NULL`` s for equality in a key, so a key over the two nullable columns would let the same claim
  list the same endpoint twice; an ``''`` spelling in the unpopulated column would instead be a value
  its own foreign key rejects. ``covered_identity`` is therefore the endpoint's identity, carried
  once, ``NOT NULL``, with a second ``CHECK`` tying it to whichever endpoint column the kind
  populated -- so the primary key is the set membership itself and both foreign keys only ever see
  real identities. The duplicate the model refuses at construction is therefore unrepresentable
  rather than merely discouraged, and it is the *key* that makes it so.
  ``evidence_claim_coverage_once`` remains as a named restatement of the same rule over the same four
  columns, so a reader scanning the index set sees it without diffing a key; it is not what does the
  enforcing, and this paragraph is written so that the two cannot drift into claiming different rules.

Two absences are deliberate and are part of the record contract:

* **No digest column and no status column that could be read as an endorsement.** The claim's own
  seal is ``record_revision.content_digest`` on the envelope's aggregate; the only digest in these
  tables is ``artifact_sha256``, which identifies bytes that live outside the database. Nothing here
  can hold a verdict, a score, a grade or a sufficiency claim, and no member of the execution-result
  vocabulary is one.
* **No second content store.** There is no blob column, no bytes column and no artifact body: the
  reference is a confined repository-relative path plus the sha256 of the bytes that live at it, plus
  the size. Retention across enclosure cleanup is therefore an obligation with its own acceptance
  evidence -- the publication reference the observation record carries -- rather than an accident of
  where the run happened to write the file.
"""

from __future__ import annotations

from collections.abc import Mapping

# Every table this generation appends, in the order the encoder serializes them. Appended after
# generation 6's twenty-eight, so generation 7's manifest begins with generation 6's, unchanged.
APPENDED_TABLES: tuple[str, ...] = (
    "evidence_claim",
    "evidence_claim_invariant_subject",
    "evidence_claim_facet_subject",
    "evidence_claim_coverage",
    "verification_observation",
)

APPENDED_COLUMNS: Mapping[str, tuple[str, ...]] = {
    "evidence_claim": (
        "repository_id",
        "claim_id",
        "evidence_anchor_id",
        "provenance",
    ),
    "evidence_claim_invariant_subject": (
        "repository_id",
        "claim_id",
        "invariant_revision_id",
    ),
    "evidence_claim_facet_subject": (
        "repository_id",
        "claim_id",
        "facet_revision_id",
    ),
    "evidence_claim_coverage": (
        "repository_id",
        "claim_id",
        "coverage_kind",
        "covered_identity",
        "claim_id_endpoint",
        "anchor_id_endpoint",
    ),
    "verification_observation": (
        "repository_id",
        "observation_id",
        "knowledge_snapshot_repository_id",
        "knowledge_snapshot_schema_version",
        "knowledge_snapshot_logical_digest",
        "code_candidate_tree_id",
        "command_name",
        "command_identity",
        "artifact_path",
        "artifact_sha256",
        "artifact_size_bytes",
        "digest_checked_at_write",
        "execution_result",
        "environment_host",
        "environment_interpreter",
        "environment_toolchain",
        "publication_destination",
        "publication_sha256",
        "publication_recorded_at",
    ),
}

# The declared primary key of each appended table, as its DDL declares it. The encoder orders a
# table's rows by this tuple, so it is part of the generation's pinned structure rather than a
# derivation from the DDL text.
APPENDED_PRIMARY_KEYS: Mapping[str, tuple[str, ...]] = {
    "evidence_claim": ("repository_id", "claim_id"),
    "evidence_claim_invariant_subject": ("repository_id", "claim_id"),
    "evidence_claim_facet_subject": ("repository_id", "claim_id"),
    "evidence_claim_coverage": (
        "repository_id",
        "claim_id",
        "coverage_kind",
        "covered_identity",
        "claim_id_endpoint",
        "anchor_id_endpoint",
    ),
    "verification_observation": ("repository_id", "observation_id"),
}

# The columns whose stored text is a typed JSON value, decoded at the portable boundary. Every
# ``provenance`` column in this generation is one, on the shipped idiom, and so is the run's
# recorded toolchain: it is structured data rather than opaque text. The knowledge snapshot identity
# is *not* one -- it is three declared columns, because a subject that has to be decoded to be
# compared is a subject a constraint cannot reach.
APPENDED_JSON_COLUMNS: Mapping[str, frozenset[str]] = {
    "evidence_claim": frozenset({"provenance"}),
    "evidence_claim_invariant_subject": frozenset(),
    "evidence_claim_facet_subject": frozenset(),
    "evidence_claim_coverage": frozenset(),
    "verification_observation": frozenset({"environment_toolchain"}),
}

# The closed execution-result vocabulary, as the DDL's own CHECK list. The spellings are declared
# once in :mod:`agents_remember.models.knowledge.evidence`; this module states them a second time
# because a generation's DDL is pinned structure and cannot import the vocabulary that names it. The
# tuple is public for exactly that reason: a case asserts it equals the vocabulary's own list, so the
# stored CHECK and the typed field cannot drift.
EXECUTION_RESULT_MEMBERS: tuple[str, ...] = ("passed", "failed", "error", "skipped", "not_run")

_EXECUTION_RESULT_CHECK = (
    "execution_result IN (" + ", ".join(f"'{member}'" for member in EXECUTION_RESULT_MEMBERS) + ")"
)

APPENDED_TABLE_DDL: Mapping[str, str] = {
    "evidence_claim": """
CREATE TABLE evidence_claim (
  repository_id TEXT NOT NULL,
  claim_id TEXT NOT NULL,
  evidence_anchor_id TEXT NOT NULL,
  provenance TEXT NOT NULL,
  PRIMARY KEY (repository_id, claim_id),
  FOREIGN KEY (repository_id) REFERENCES repository(repository_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (repository_id, evidence_anchor_id)
    REFERENCES source_anchor(repository_id, anchor_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED
) STRICT
""",
    "evidence_claim_invariant_subject": """
CREATE TABLE evidence_claim_invariant_subject (
  repository_id TEXT NOT NULL,
  claim_id TEXT NOT NULL,
  invariant_revision_id TEXT NOT NULL,
  PRIMARY KEY (repository_id, claim_id),
  FOREIGN KEY (repository_id) REFERENCES repository(repository_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (repository_id, claim_id)
    REFERENCES evidence_claim(repository_id, claim_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (repository_id, invariant_revision_id)
    REFERENCES invariant_revision(repository_id, revision_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED
) STRICT
""",
    "evidence_claim_facet_subject": """
CREATE TABLE evidence_claim_facet_subject (
  repository_id TEXT NOT NULL,
  claim_id TEXT NOT NULL,
  facet_revision_id TEXT NOT NULL,
  PRIMARY KEY (repository_id, claim_id),
  FOREIGN KEY (repository_id) REFERENCES repository(repository_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (repository_id, claim_id)
    REFERENCES evidence_claim(repository_id, claim_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (repository_id, facet_revision_id)
    REFERENCES record_revision(repository_id, revision_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED
) STRICT
""",
    "evidence_claim_coverage": """
CREATE TABLE evidence_claim_coverage (
  repository_id TEXT NOT NULL,
  claim_id TEXT NOT NULL,
  coverage_kind TEXT NOT NULL,
  covered_identity TEXT NOT NULL,
  claim_id_endpoint TEXT,
  anchor_id_endpoint TEXT,
  PRIMARY KEY (repository_id, claim_id, coverage_kind, covered_identity),
  CHECK (
    (coverage_kind = 'realization_claim' AND claim_id_endpoint IS NOT NULL
      AND anchor_id_endpoint IS NULL)
    OR (coverage_kind = 'source_anchor' AND anchor_id_endpoint IS NOT NULL
      AND claim_id_endpoint IS NULL)
  ),
  CHECK (covered_identity = claim_id_endpoint OR covered_identity = anchor_id_endpoint),
  FOREIGN KEY (repository_id) REFERENCES repository(repository_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (repository_id, claim_id)
    REFERENCES evidence_claim(repository_id, claim_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (repository_id, claim_id_endpoint)
    REFERENCES realization_claim(repository_id, claim_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (repository_id, anchor_id_endpoint)
    REFERENCES source_anchor(repository_id, anchor_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED
) STRICT
""",
    "verification_observation": f"""
CREATE TABLE verification_observation (
  repository_id TEXT NOT NULL,
  observation_id TEXT NOT NULL,
  knowledge_snapshot_repository_id TEXT,
  knowledge_snapshot_schema_version TEXT,
  knowledge_snapshot_logical_digest TEXT,
  code_candidate_tree_id TEXT,
  command_name TEXT NOT NULL,
  command_identity TEXT NOT NULL,
  artifact_path TEXT,
  artifact_sha256 TEXT,
  artifact_size_bytes INTEGER,
  digest_checked_at_write INTEGER NOT NULL,
  execution_result TEXT NOT NULL,
  environment_host TEXT NOT NULL,
  environment_interpreter TEXT NOT NULL,
  environment_toolchain TEXT NOT NULL,
  publication_destination TEXT,
  publication_sha256 TEXT,
  publication_recorded_at TEXT,
  PRIMARY KEY (repository_id, observation_id),
  CHECK ({_EXECUTION_RESULT_CHECK}),
  CHECK (digest_checked_at_write IN (0, 1)),
  CHECK (
    knowledge_snapshot_repository_id IS NULL
    OR (knowledge_snapshot_schema_version IS NOT NULL
      AND knowledge_snapshot_logical_digest IS NOT NULL)
  ),
  CHECK (
    knowledge_snapshot_repository_id IS NOT NULL
    OR code_candidate_tree_id IS NOT NULL
  ),
  CHECK (
    (artifact_path IS NULL AND artifact_sha256 IS NULL AND artifact_size_bytes IS NULL)
    OR (artifact_path IS NOT NULL AND artifact_sha256 IS NOT NULL
      AND artifact_size_bytes IS NOT NULL)
  ),
  CHECK (
    digest_checked_at_write = 0
    OR (artifact_sha256 IS NOT NULL AND artifact_size_bytes IS NOT NULL)
  ),
  CHECK (
    (publication_destination IS NULL AND publication_sha256 IS NULL
      AND publication_recorded_at IS NULL)
    OR (publication_destination IS NOT NULL AND publication_sha256 IS NOT NULL
      AND publication_recorded_at IS NOT NULL)
  ),
  FOREIGN KEY (repository_id) REFERENCES repository(repository_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (repository_id, observation_id)
    REFERENCES knowledge_record(repository_id, record_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED
) STRICT
""",
}

# Index names are a local choice; each covers the reverse direction of a declared lookup -- "which
# claims are anchored here", "which claims cover this realisation", "which observations recorded
# this snapshot" -- which is the same reason the earlier generations declare their own.
APPENDED_INDEX_DDL: tuple[str, ...] = (
    "CREATE INDEX evidence_claim_anchor ON evidence_claim (repository_id, evidence_anchor_id)",
    # One endpoint is covered once, named. The primary key already enforces it: ``covered_identity``
    # is NOT NULL and part of the key, and SQLite does not compare NULLs for equality in a key, which
    # is why the endpoint identity is carried in its own column rather than read out of the two
    # nullable ones (see this module's docstring). This index therefore restates a rule the key
    # already holds -- it is over the carried column, not over a COALESCE of the nullable ones, so the
    # index and the key cannot describe different rules.
    "CREATE UNIQUE INDEX evidence_claim_coverage_once "
    "ON evidence_claim_coverage (repository_id, claim_id, coverage_kind, covered_identity)",
    "CREATE INDEX evidence_claim_invariant_subject_revision "
    "ON evidence_claim_invariant_subject (repository_id, invariant_revision_id)",
    "CREATE INDEX evidence_claim_facet_subject_revision "
    "ON evidence_claim_facet_subject (repository_id, facet_revision_id)",
    "CREATE INDEX evidence_claim_coverage_claim "
    "ON evidence_claim_coverage (repository_id, claim_id_endpoint)",
    "CREATE INDEX evidence_claim_coverage_anchor "
    "ON evidence_claim_coverage (repository_id, anchor_id_endpoint)",
    "CREATE INDEX verification_observation_snapshot "
    "ON verification_observation "
    "(repository_id, knowledge_snapshot_logical_digest, observation_id)",
    "CREATE INDEX verification_observation_command "
    "ON verification_observation (repository_id, command_name, observation_id)",
)

# The same trigger idiom the earlier generations use: the operation's preconditions return a typed
# refusal, and these exist so a changeset, a repair script or a future code path that forgot the rule
# still cannot rewrite a sealed record or rebind a sealed association.
#
# The four claim-side triggers are the shipped rule applied to this leaf's own relation rows. A
# claim's subject and its claimed coverage are what the author *asserted*, so repointing or removing
# one would silently change the claim without changing its identity -- the exact silent promotion the
# record kinds are separated to prevent. Nothing here refuses the delete of a ``knowledge_record`` or
# a ``record_revision``; those are already sealed by generation 2's own triggers.
APPENDED_TRIGGERS: Mapping[str, str] = {
    "evidence_claim_no_update": """
CREATE TRIGGER evidence_claim_no_update BEFORE UPDATE ON evidence_claim
BEGIN SELECT RAISE(ABORT, 'immutable_revision: a recorded evidence claim cannot be updated'); END
""",
    "evidence_claim_no_delete": """
CREATE TRIGGER evidence_claim_no_delete BEFORE DELETE ON evidence_claim
BEGIN SELECT RAISE(ABORT, 'immutable_revision: a recorded evidence claim cannot be deleted'); END
""",
    "evidence_claim_invariant_subject_no_rebind": """
CREATE TRIGGER evidence_claim_invariant_subject_no_rebind
BEFORE UPDATE ON evidence_claim_invariant_subject
BEGIN SELECT RAISE(ABORT, 'immutable_revision: an evidence claim subject cannot be rebound'); END
""",
    "evidence_claim_invariant_subject_no_delete": """
CREATE TRIGGER evidence_claim_invariant_subject_no_delete
BEFORE DELETE ON evidence_claim_invariant_subject
BEGIN SELECT RAISE(ABORT, 'immutable_revision: an evidence claim subject cannot be removed'); END
""",
    "evidence_claim_facet_subject_no_rebind": """
CREATE TRIGGER evidence_claim_facet_subject_no_rebind
BEFORE UPDATE ON evidence_claim_facet_subject
BEGIN SELECT RAISE(ABORT, 'immutable_revision: an evidence claim subject cannot be rebound'); END
""",
    "evidence_claim_facet_subject_no_delete": """
CREATE TRIGGER evidence_claim_facet_subject_no_delete
BEFORE DELETE ON evidence_claim_facet_subject
BEGIN SELECT RAISE(ABORT, 'immutable_revision: an evidence claim subject cannot be removed'); END
""",
    "evidence_claim_coverage_no_rebind": """
CREATE TRIGGER evidence_claim_coverage_no_rebind BEFORE UPDATE ON evidence_claim_coverage
BEGIN SELECT RAISE(ABORT, 'immutable_revision: claimed coverage cannot be rebound'); END
""",
    "evidence_claim_coverage_no_delete": """
CREATE TRIGGER evidence_claim_coverage_no_delete BEFORE DELETE ON evidence_claim_coverage
BEGIN SELECT RAISE(ABORT, 'immutable_revision: claimed coverage cannot be narrowed'); END
""",
    "verification_observation_no_rewrite": """
CREATE TRIGGER verification_observation_no_rewrite BEFORE UPDATE ON verification_observation
BEGIN SELECT RAISE(ABORT, 'immutable_revision: a recorded verification observation cannot be updated'); END
""",
    "verification_observation_no_delete": """
CREATE TRIGGER verification_observation_no_delete BEFORE DELETE ON verification_observation
BEGIN SELECT RAISE(ABORT, 'immutable_revision: a recorded verification observation cannot be deleted'); END
""",
}

# Generation 7 needs no SQLite feature generation 6 does not already require: it adds tables with
# checked foreign-key groups, a composite primary key, indexes and triggers, all of which
# ``strict_tables``, ``deferrable_foreign_keys``, ``trigger_raise_abort`` and ``json_functions``
# already cover. The tuple is declared (empty) rather than omitted so the composition of generation 7
# states the same fact generation 6's does.
APPENDED_FEATURES: tuple[str, ...] = ()
