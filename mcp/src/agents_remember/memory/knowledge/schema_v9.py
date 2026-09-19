"""Generation 9's appended tables: the truth-coverage census's record kinds and their relations.

This module owns **only what generation 9 appends**. Generation 1's ten tables, generation 2's six,
generation 3's four, generation 4's one, generation 5's one, generation 6's six, generation 7's five
and generation 8's one stay declared, verbatim, in :mod:`…schema`, :mod:`…schema_v2`,
:mod:`…schema_v3`, :mod:`…schema_v4`, :mod:`…schema_v5`, :mod:`…schema_v6`, :mod:`…schema_v7` and
:mod:`…schema_v8`; nothing here redeclares, reorders, renames, retypes or drops one of them, and no
``ALTER TABLE`` against an earlier generation's table appears anywhere in this package.
``KS-R10@v1`` §1.3 makes appending the only sanctioned way to add a table, and generation 9's own
case asserts ``GENERATION_9.columns[table] == GENERATION_8.columns[table]`` for every one of
generation 8's thirty-four names.

Why this leaf appends **three records and three relations** rather than a wide table:

* ``Doc12:49-55`` states the census's record list, and ``KS-R21@v1`` §6.2 settles how it is read:
  each entry is "a **census record kind with its own identity** -- not a column set on one wide
  table, and not a report section". A wide row would make the inventory surface, the claim and the
  migration disposition one record with three meanings, which is the shape the requirement refuses.
  Keeping the inventory row its own record is also what lets a surface **with no onboarding**
  (`:51`) exist in the census at all: it can have no claim.
* Three of the facts ``Doc12`` names are *relations* rather than fields: `:53`'s evidence reference,
  `:55`'s "links to the resulting new records, including claims that are split, combined, retired,
  or corrected", and the realization attribution the realization-coverage measure counts. The record
  envelope carries a kind, an authority home, a lifecycle, a governing route and one sealed payload,
  and it cannot express a relation -- so these are rows here, exactly as generation 8's succession
  edge is.
* ``knowledge_record`` already carries the kind, the governing route, the record schema and the
  authorship, and ``record_revision`` already carries the frozen payload and its content digest.
  Restating any of those as a column here would be a second declaration of one fact.

The census records carry **no content-address, no logical digest and no fingerprint column**, and
none is added here: "no second identity authority" is a property of the declared columns. The
content digest stays on ``record_revision``, where the envelope puts it.

Three columns are recorded facts rather than derivations, and each is deliberate:

* ``census_inventory_row.observed_doc_type`` is what the parser **read** off the artifact. The
  cardinality rule that decides which census claim a curator classifies reads it, so it is stored at
  import and the rule's input is auditable rather than re-derived from the artifact at read time.
* ``census_claim.claim_kind`` admits ``unclassified``. ``Doc12:65-70``'s taxonomy is closed at four
  kinds and ``KS-R21@v1`` §6.3 makes a claim outside them *unclassified*, "a reportable state, never
  a fifth kind invented by the importer" -- so the state is a value of the closed vocabulary rather
  than a NULL a reader could take for "not yet read".
* ``census_claim.applicability`` is the field that separates a claim in the cohort from one that is
  not. It is the field the eligibility rule reads, so that ``Doc12``'s Example 3 and requirement 5.2
  read alike: the accounting ``N = T + F + U + P`` is taken over the claims whose applicability is
  ``assessable``, and a piece recorded ``historical_non_applicable`` carries its disposition without
  entering ``N``.

Every table is sealed against update and delete by the same trigger pair the earlier generations
use, so "a census observation is never rewritten in place" is a property of the schema rather than a
rule the write path remembers.
"""

from __future__ import annotations

from collections.abc import Mapping

# Every table this generation appends, in the order the encoder serializes them. Appended after
# generation 8's thirty-four, so generation 9's manifest begins with generation 8's, unchanged.
APPENDED_TABLES: tuple[str, ...] = (
    "census_inventory_row",
    "census_claim",
    "census_disposition",
    "census_claim_evidence",
    "census_claim_realization",
    "census_disposition_link",
)

# The closed vocabularies the DDL constrains, declared once so the model and the schema cannot drift
# into two different sets of admitted values. ``CENSUS_*_VALUES`` are the names the payload models
# import; nothing here re-states one of them as a literal.
CENSUS_ARTIFACT_KINDS: tuple[str, ...] = (
    "file_level_onboarding",
    "route_local_overview",
    "other",
)
CENSUS_PARSE_OUTCOMES: tuple[str, ...] = (
    "parsed",
    "unparsed",
    "unsupported",
    "unreadable",
)
CENSUS_INVENTORY_STATES: tuple[str, ...] = ("present", "absent", "unreadable")
CENSUS_CLAIM_KINDS: tuple[str, ...] = (
    "unclassified",
    "current_behavior",
    "accepted_invariant",
    "historical_rationale",
    "realization_attribution",
)
CENSUS_APPLICABILITY: tuple[str, ...] = ("assessable", "non_claim", "historical_non_applicable")
CENSUS_DISPOSITION_KINDS: tuple[str, ...] = (
    "imported",
    "unmapped",
    "unsupported",
    "unreadable",
    "retired",
    "historical",
    "non_claim",
)
CENSUS_DISPOSITION_STATES: tuple[str, ...] = ("recorded", "applied")
CENSUS_EVIDENCE_STATES: tuple[str, ...] = ("unassessed", "assessed", "unresolved")
# The three dispositions a curator's authored assessment may record. They are ``Doc12:99-104``'s
# verified/contradicted/unresolved split as the assessment vocabulary spells it, and they are the ONLY
# source of the truth cells: nothing in the migration pipeline can produce one, which is why this
# column is nullable -- an absent value is the unassessed state rather than a fourth disposition.
CENSUS_ASSESSMENT_DISPOSITIONS: tuple[str, ...] = (
    "no_concern_found",
    "concern_found",
    "unresolved",
)
CENSUS_REALIZATION_STATES: tuple[str, ...] = ("unattributed", "attributed", "missing_realization")
CENSUS_LINK_KINDS: tuple[str, ...] = ("split_into", "combined_with", "retired", "corrected_by")
CENSUS_TARGET_STATES: tuple[str, ...] = ("resolved", "unresolved", "ambiguous")

APPENDED_COLUMNS: Mapping[str, tuple[str, ...]] = {
    "census_inventory_row": (
        "repository_id",
        "inventory_row_id",
        "artifact_path",
        "artifact_kind",
        "declared_source_path",
        "observed_doc_type",
        "observed_route_path",
        "outcome",
        "unparsed_content",
        "source_route_path",
        "inventory_state",
        "provenance",
    ),
    "census_claim": (
        "repository_id",
        "claim_id",
        "claim_text",
        "claim_location",
        "claim_kind",
        "applicability",
        "disposition",
        "provenance",
    ),
    "census_disposition": (
        "repository_id",
        "disposition_id",
        "disposition_kind",
        "disposition_state",
        "rationale",
        "provenance",
    ),
    "census_claim_evidence": (
        "repository_id",
        "claim_id",
        "evidence_ref",
        "evidence_state",
        "assessment_disposition",
        "provenance",
    ),
    "census_claim_realization": (
        "repository_id",
        "claim_id",
        "realization_ref",
        "attribution_state",
        "provenance",
    ),
    "census_disposition_link": (
        "repository_id",
        "disposition_id",
        "link_kind",
        "target_ref",
        "target_state",
        "provenance",
    ),
}

# The declared primary key, as the DDL declares it. Every census relation is keyed by its whole
# recorded content, so "the same fact recorded twice" is one row rather than two observations, and
# re-running an import at the same baseline is an idempotent insert instead of a duplicate append.
APPENDED_PRIMARY_KEYS: Mapping[str, tuple[str, ...]] = {
    "census_inventory_row": ("repository_id", "inventory_row_id"),
    "census_claim": ("repository_id", "claim_id"),
    "census_disposition": ("repository_id", "disposition_id"),
    "census_claim_evidence": ("repository_id", "claim_id", "evidence_ref"),
    "census_claim_realization": ("repository_id", "claim_id", "realization_ref"),
    "census_disposition_link": (
        "repository_id",
        "disposition_id",
        "link_kind",
        "target_ref",
    ),
}

# ``provenance`` is the typed-JSON column on the shipped idiom, in every one of these six tables: a
# provenance record is structured data rather than opaque text. ``unparsed_content`` is the second
# one, and deliberately so -- it is the exact observed content the parser could not interpret, held
# as a document so that a reader can compare it byte for byte with the artifact instead of matching
# against a re-escaped string.
APPENDED_JSON_COLUMNS: Mapping[str, frozenset[str]] = {
    "census_inventory_row": frozenset({"provenance", "unparsed_content"}),
    "census_claim": frozenset({"provenance"}),
    "census_disposition": frozenset({"provenance"}),
    "census_claim_evidence": frozenset({"provenance"}),
    "census_claim_realization": frozenset({"provenance"}),
    "census_disposition_link": frozenset({"provenance"}),
}

# The closes, rendered from the one place each vocabulary is declared. A vocabulary that gained a
# value in the model would otherwise be a value the schema refuses, which is a drift this rendering
# makes unrepresentable.
_VOCABULARY_CHECKS: Mapping[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
    "census_inventory_row": (
        ("artifact_kind", CENSUS_ARTIFACT_KINDS),
        ("outcome", CENSUS_PARSE_OUTCOMES),
        ("inventory_state", CENSUS_INVENTORY_STATES),
    ),
    "census_claim": (
        ("claim_kind", CENSUS_CLAIM_KINDS),
        ("applicability", CENSUS_APPLICABILITY),
        ("disposition", CENSUS_DISPOSITION_KINDS),
    ),
    "census_disposition": (
        ("disposition_kind", CENSUS_DISPOSITION_KINDS),
        ("disposition_state", CENSUS_DISPOSITION_STATES),
    ),
    "census_claim_evidence": (
        ("evidence_state", CENSUS_EVIDENCE_STATES),
        ("assessment_disposition", CENSUS_ASSESSMENT_DISPOSITIONS),
    ),
    "census_claim_realization": (("attribution_state", CENSUS_REALIZATION_STATES),),
    "census_disposition_link": (
        ("link_kind", CENSUS_LINK_KINDS),
        ("target_state", CENSUS_TARGET_STATES),
    ),
}


def _vocabulary_closes(table: str) -> str:
    """Render one table's ``CHECK ... IN`` lines from its declared vocabularies."""

    return "\n".join(
        f"  CHECK ({column} IN ({', '.join(repr(value) for value in values)})),"
        for column, values in _VOCABULARY_CHECKS[table]
    )


def _record_table_ddl(table: str, key_columns: str, body: str, parents: str = "") -> str:
    """Return one census table's DDL: its key, its matched closes, its parents and its provenance.

    ``key_columns`` is the whole declared key and ``body`` is every column after the leading one, so
    the leading key column is written here exactly once whether the key is single or composite.
    """

    return (
        f"CREATE TABLE {table} (\n"
        f"  repository_id TEXT NOT NULL,\n"
        f"  {key_columns.split(',', maxsplit=1)[0].strip()} TEXT NOT NULL,\n"
        f"{body}\n"
        f"  provenance TEXT NOT NULL,\n"
        f"  PRIMARY KEY (repository_id, {key_columns}),\n"
        f"{_vocabulary_closes(table)}\n"
        f"  FOREIGN KEY (repository_id) REFERENCES repository(repository_id)\n"
        f"    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED"
        f"{parents}\n"
        f") STRICT\n"
    )


def _claim_parent() -> str:
    """Return the composite parent edge every census claim relation declares."""

    return (
        ",\n  FOREIGN KEY (repository_id, claim_id)"
        "\n    REFERENCES census_claim(repository_id, claim_id)"
        "\n    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED"
    )


APPENDED_TABLE_DDL: Mapping[str, str] = {
    "census_inventory_row": _record_table_ddl(
        "census_inventory_row",
        "inventory_row_id",
        "  artifact_path TEXT NOT NULL,\n"
        "  artifact_kind TEXT NOT NULL,\n"
        "  declared_source_path TEXT,\n"
        "  observed_doc_type TEXT,\n"
        "  observed_route_path TEXT,\n"
        "  outcome TEXT NOT NULL,\n"
        "  unparsed_content TEXT,\n"
        "  source_route_path TEXT,\n"
        "  inventory_state TEXT NOT NULL,",
    ),
    "census_claim": _record_table_ddl(
        "census_claim",
        "claim_id",
        "  claim_text TEXT NOT NULL,\n"
        "  claim_location TEXT NOT NULL,\n"
        "  claim_kind TEXT NOT NULL,\n"
        "  applicability TEXT NOT NULL,\n"
        "  disposition TEXT NOT NULL,",
    ),
    "census_disposition": _record_table_ddl(
        "census_disposition",
        "disposition_id",
        "  disposition_kind TEXT NOT NULL,\n  disposition_state TEXT NOT NULL,\n  rationale TEXT,",
    ),
    "census_claim_evidence": _record_table_ddl(
        "census_claim_evidence",
        "claim_id, evidence_ref",
        "  evidence_ref TEXT NOT NULL,\n"
        "  evidence_state TEXT NOT NULL,\n"
        "  assessment_disposition TEXT,",
        _claim_parent(),
    ),
    "census_claim_realization": _record_table_ddl(
        "census_claim_realization",
        "claim_id, realization_ref",
        "  realization_ref TEXT NOT NULL,\n  attribution_state TEXT NOT NULL,",
        _claim_parent(),
    ),
    "census_disposition_link": _record_table_ddl(
        "census_disposition_link",
        "disposition_id, link_kind, target_ref",
        "  link_kind TEXT NOT NULL,\n  target_ref TEXT NOT NULL,\n  target_state TEXT NOT NULL,",
        ",\n  FOREIGN KEY (repository_id, disposition_id)"
        "\n    REFERENCES census_disposition(repository_id, disposition_id)"
        "\n    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED",
    ),
}

# Index names are a local choice; each covers the reverse direction of a declared lookup, which is
# the same reason the earlier generations declare their own. The relation indexes are what "which
# claims cite this evidence" and "which dispositions point at this record" resolve through, and the
# outcome index is what the parse census reports through rather than walking the whole inventory.
APPENDED_INDEX_DDL: tuple[str, ...] = (
    "CREATE INDEX census_inventory_row_outcome ON census_inventory_row (repository_id, outcome)",
    "CREATE INDEX census_inventory_row_route "
    "ON census_inventory_row (repository_id, source_route_path)",
    "CREATE INDEX census_claim_kind ON census_claim (repository_id, claim_kind)",
    "CREATE INDEX census_claim_applicability ON census_claim (repository_id, applicability)",
    "CREATE INDEX census_claim_evidence_ref ON census_claim_evidence (repository_id, evidence_ref)",
    "CREATE INDEX census_claim_realization_ref "
    "ON census_claim_realization (repository_id, realization_ref)",
    "CREATE INDEX census_disposition_target ON census_disposition_link (repository_id, target_ref)",
)

# The same trigger idiom the earlier generations use: the write path returns a typed refusal, and
# these exist so a changeset, a repair script or a future code path that forgot the rule still cannot
# rewrite a census observation or drop one. Nothing is exempt from either guard -- unlike a
# citation binding, a census row has no lifecycle field a later operation is entitled to move.
APPENDED_TRIGGERS: Mapping[str, str] = {
    f"{table}_no_update": f"""
CREATE TRIGGER {table}_no_update BEFORE UPDATE ON {table}
BEGIN SELECT RAISE(ABORT, 'immutable_census_row: a recorded census observation cannot be rewritten'); END
"""
    for table in APPENDED_TABLES
} | {
    f"{table}_no_delete": f"""
CREATE TRIGGER {table}_no_delete BEFORE DELETE ON {table}
BEGIN SELECT RAISE(ABORT, 'immutable_census_row: a recorded census observation cannot be dropped'); END
"""
    for table in APPENDED_TABLES
}

# Generation 9 needs no SQLite feature generation 8 does not already require: it adds six tables with
# checked vocabularies, composite foreign-key groups, indexes and triggers, all of which
# ``strict_tables``, ``deferrable_foreign_keys``, ``trigger_raise_abort`` and ``json_functions``
# already cover. The tuple is declared (empty) rather than omitted so this generation states the same
# fact its predecessor does.
APPENDED_FEATURES: tuple[str, ...] = ()
