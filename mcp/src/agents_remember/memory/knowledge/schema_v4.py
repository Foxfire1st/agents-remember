"""Generation 4's appended table: the recorded order of one detection run's signals.

This module owns **only what generation 4 appends**. Generation 1's ten tables, generation 2's six
and generation 3's four stay declared, verbatim, in :mod:`…schema`, :mod:`…schema_v2` and
:mod:`…schema_v3`; nothing here redeclares, reorders, renames, retypes or drops one of them, and no
``ALTER TABLE`` against an earlier generation's table appears anywhere in this package.
``KS-R10@v1`` §1.3 makes appending the only sanctioned way to add a table, and generation 4's own
case asserts ``GENERATION_4.columns[table] == GENERATION_3.columns[table]`` for every one of
generation 3's twenty names.

Why this leaf appends **one** table rather than a record group of its own, and why that table is
this one:

* A detection signal and a detection run are *typed records*, and ``KS-R10@v1`` already built the
  envelope they live in: ``knowledge_record`` carries the kind, the authority home, the lifecycle
  and the governing route, and ``record_revision`` carries the frozen payload and its content
  digest. Their payload shapes are registered in
  :data:`agents_remember.memory.knowledge.record_envelope.PAYLOAD_MODELS` under
  ``(detection_signal, detection-signal/v1)`` and ``(detection_run, detection-run/v1)``, exactly as
  the eight authored-judgment subtypes are, so the signal's own fields are declared once, in the
  generation-1/2 envelope, rather than restated as columns here.
* What the envelope **cannot** express is the *sequence*. Requirement 3.3 makes a run reproducible
  by comparing two ordered sequences rather than two sets, and requirement 3.4 refuses a
  re-execution silently overwriting a recorded run. A sequence stored as a payload tuple is a claim
  inside one revision; ``detection_run_signal`` makes it a row per position, `UNIQUE` over the
  signal, keyed by ordinal, and sealed against update and delete by triggers -- so "the recorded
  order" is a stored fact a later write cannot rewrite rather than a field a later write could
  replace.
* ``ordinal`` is part of the primary key, so one run cannot record two signals in one position, and
  the ``UNIQUE (repository_id, run_id, signal_id)`` key means one run cannot record one signal
  twice. Together they make the declared total order over signal identity a constraint of the table
  instead of a rule the write path remembers.
* Both endpoints are foreign keys to ``knowledge_record``, so a run's sequence can only name records
  the same store holds, and a signal cannot be attributed to a run by prose.

No detection row carries a content address, a logical digest or a fingerprint column, and none is
added here: requirement 3.7 gives the content digest to ``record_revision``, where ``Doc13:85``
already puts it -- the signal observed a digest and does not become one.
"""

from __future__ import annotations

from collections.abc import Mapping

# Every table this generation appends, in the order the encoder serializes them. Appended after
# generation 3's twenty, so generation 4's manifest begins with generation 3's, unchanged.
APPENDED_TABLES: tuple[str, ...] = ("detection_run_signal",)

APPENDED_COLUMNS: Mapping[str, tuple[str, ...]] = {
    "detection_run_signal": (
        "repository_id",
        "run_id",
        "ordinal",
        "signal_id",
    ),
}

# The declared primary key, as the DDL declares it. The encoder orders a table's rows by this tuple,
# so it is part of the generation's pinned structure rather than a derivation from the DDL text.
APPENDED_PRIMARY_KEYS: Mapping[str, tuple[str, ...]] = {
    "detection_run_signal": ("repository_id", "run_id", "ordinal"),
}

# No column of this table stores a typed JSON value. The sequence is two opaque identities and a
# position; every structured fact about a signal or a run lives in the registered payload on
# ``record_revision``, which is where the envelope's typed-JSON seam already is. Declared as an
# explicit empty mapping rather than omitted, so generation 4 states the same fact generation 3's
# declaration does and a reader does not infer "no JSON column" from an absence.
APPENDED_JSON_COLUMNS: Mapping[str, frozenset[str]] = {
    "detection_run_signal": frozenset(),
}

APPENDED_TABLE_DDL: Mapping[str, str] = {
    "detection_run_signal": """
CREATE TABLE detection_run_signal (
  repository_id TEXT NOT NULL,
  run_id TEXT NOT NULL,
  ordinal INTEGER NOT NULL,
  signal_id TEXT NOT NULL,
  PRIMARY KEY (repository_id, run_id, ordinal),
  UNIQUE (repository_id, run_id, signal_id),
  CHECK (ordinal >= 0),
  FOREIGN KEY (repository_id) REFERENCES repository(repository_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (repository_id, run_id)
    REFERENCES knowledge_record(repository_id, record_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (repository_id, signal_id)
    REFERENCES knowledge_record(repository_id, record_id)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED
) STRICT
""",
}

# Index names are a local choice; this one covers the reverse direction of a declared lookup -- "which
# runs recorded this signal" -- which is the same reason the earlier generations declare their own.
APPENDED_INDEX_DDL: tuple[str, ...] = (
    "CREATE INDEX detection_run_signal_signal ON detection_run_signal (repository_id, signal_id)",
)

# The same trigger idiom the earlier generations use: the operation's preconditions return a typed
# refusal, and these exist so a changeset, a repair script or a future code path that forgot the rule
# still cannot rewrite a recorded run's sequence or drop one of its signals. Requirement 3.4's "never
# overwrites the recorded one" is about the run, and a run's order is what a second write would have
# to move to present a re-execution as the recorded run.
APPENDED_TRIGGERS: Mapping[str, str] = {
    "detection_run_signal_no_reorder": """
CREATE TRIGGER detection_run_signal_no_reorder
BEFORE UPDATE OF run_id, ordinal, signal_id ON detection_run_signal
BEGIN SELECT RAISE(ABORT, 'immutable_revision: a recorded detection sequence cannot be reordered'); END
""",
    "detection_run_signal_no_delete": """
CREATE TRIGGER detection_run_signal_no_delete BEFORE DELETE ON detection_run_signal
BEGIN SELECT RAISE(ABORT, 'immutable_revision: a recorded detection sequence cannot be shortened'); END
""",
}

# Generation 4 needs no SQLite feature generation 3 does not already require: it adds one table with
# checked foreign-key groups, a composite unique key, an index and triggers, all of which
# ``strict_tables``, ``deferrable_foreign_keys``, ``trigger_raise_abort`` and ``json_functions``
# already cover. The tuple is declared (empty) rather than omitted so the composition of generation 4
# states the same fact generation 3's does.
APPENDED_FEATURES: tuple[str, ...] = ()
