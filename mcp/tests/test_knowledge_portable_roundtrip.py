"""Complete logical export and import: the round trip, its refusals and what it preserves.

This module holds the cases for `KS-R06@v1`. They live in the integration population, and that
population has a declared ceiling, so the cases are *consolidated by protected property* rather than
split one per assertion: each function below names the one failure it exists to catch, and groups the
checks that would each pass while that failure went unnoticed.

Three properties are what the cases are built to falsify, and each is the reason for a group below:

* **Completeness.** An export must hold every canonical collection including the empty ones, and an
  import must reproduce the exact logical digest. One case mutates an artifact four ways -- a dropped
  collection, a truncated table, an unknown table, a value the declared type cannot hold -- and
  asserts the check that catches each.
* **Refusal without partial acceptance.** A malformed, incomplete or unsupported artifact must not
  publish anything: the destination keeps its exact bytes, and no staging file survives beside it.
  The cases measure both -- the previous destination's file digest, and the destination directory's
  contents afterwards.
* **Preservation.** Every stored ID, provenance value and ``state_at_origin`` crosses as data.
  Importing a row that says ``accepted`` imports that value and grants no authority; the case holds
  the *value* to the round trip and asserts nothing promotes it.

Every refusal case names the exact node it protects, and each claimed guard has a mutation that
fails one of these nodes -- see the worker's evidence annex for the mutation results.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
from uuid import uuid4

import apsw
import pytest
from agents_remember.application.knowledge_export import (
    canonical_body_of_artifact,
    export_knowledge_artifact,
    import_knowledge_artifact,
    read_knowledge_artifact,
    validate_knowledge_artifact,
)
from agents_remember.application.knowledge_merge import (
    merge_resolved_knowledge_datasets,
    resolve_knowledge_merge_base,
)
from agents_remember.memory.knowledge import logical, records, schema
from agents_remember.memory.knowledge.candidate_workspace import create_candidate
from agents_remember.memory.knowledge.connection import open_read_only_database
from agents_remember.memory.knowledge.export_import import _verify_staged_dataset
from agents_remember.memory.knowledge.export_portable import (
    ENVELOPE_KEYS,
    EXPORT_FORMAT,
    canonical_document,
    encode_export,
)
from agents_remember.models.knowledge.candidate import CandidateResolution
from agents_remember.models.knowledge.context import KNOWLEDGE_SCHEMA_NAME
from agents_remember.models.knowledge.digest import sealed_revision
from agents_remember.models.knowledge.merge import (
    MergeBaseRequest,
    MergeRequest,
    ResolvedGitBase,
)
from agents_remember.models.knowledge.portable import ExportRequest, ImportRequest
from agents_remember.models.knowledge.repository import RepositoryIdentity
from agents_remember.models.knowledge.result import KnowledgeRefusal
from agents_remember.models.knowledge.snapshot import (
    AdmittedCandidateDestination,
    SnapshotDestinationRequest,
)
from knowledge_fixture_test_support import (
    BranchingKnowledgeFixture,
    build_branching_knowledge_fixture,
)
from merge_case_test_support import MergeCase, build_case, copy_closed, file_digest, set_label

pytestmark = pytest.mark.integration

TABLE_COUNT = len(schema.CANONICAL_TABLES)

# The fixture authors one claim whose recorded path resolves nowhere. It is the row the preservation
# case reads back, and it exists in the shared fixture precisely because storage never resolves a
# source.
ABSENT_PATH = "src/retired_adapter.py"
UNKNOWN_UUID = "00000000-0000-4000-8000-000000000000"


@pytest.fixture
def fixture(tmp_path: Path) -> BranchingKnowledgeFixture:
    return build_branching_knowledge_fixture(tmp_path / "source")


def identity_of(path: Path):
    return logical.dataset_identity(path)


def exported(source: BranchingKnowledgeFixture | Path):
    """Export one dataset, requiring that the export succeeded."""

    path = source.database_path if isinstance(source, BranchingKnowledgeFixture) else source
    result = export_knowledge_artifact(
        ExportRequest(database_path=path, expected_identity=identity_of(path))
    )
    assert result.state == "exported", result.refusal
    assert result.artifact is not None
    return result


def artifact_of(source: BranchingKnowledgeFixture | Path) -> str:
    return exported(source).artifact or ""


def envelope_of(artifact: str) -> dict:
    return json.loads(artifact)


def reencode(envelope: dict) -> str:
    """Render one envelope back to text exactly as a producer would.

    Re-rendering rather than string-editing is what keeps a mutated artifact a *valid document*: the
    case is then testing the check it names, not JSON syntax.
    """

    return encode_export(envelope)


def reseal(envelope: dict) -> str:
    """Recompute a mutated envelope's digest over its records, then render it.

    Used where the case is about content rather than about the seal: an artifact that honestly
    declares the digest of the records it carries has to be refused by the *next* check, and a case
    that forgot to reseal would be testing the seal twice and nothing else.
    """

    envelope["logicalDigest"] = logical.logical_digest_of_tables(
        KNOWLEDGE_SCHEMA_NAME, envelope["tables"]
    )
    return reencode(envelope)


def import_into(artifact: str, destination: Path, **kwargs):
    return import_knowledge_artifact(
        ImportRequest(artifact=artifact, destination_path=destination, **kwargs)
    )


def sqlite_entries(directory: Path) -> list[str]:
    """Return every database-like name in one directory: a published file, a stage or a peer."""

    return sorted(
        path.name
        for path in directory.iterdir()
        if path.name.endswith((".sqlite", "-wal", "-shm", "-journal"))
    )


def row_counts_of(path: Path) -> dict[str, int]:
    """Return one dataset's row count per canonical table, including the empty ones."""

    return {table: len(table_rows(path, table)) for table in schema.CANONICAL_TABLES}


def table_rows(path: Path, table: str) -> list[tuple]:
    connection = open_read_only_database(path)
    try:
        return [tuple(row) for row in connection.execute(f"SELECT * FROM {table}")]
    finally:
        connection.close()


def identifiers_of(path: Path, table: str) -> set[str]:
    """Return one table's declared primary-key identities, keyed as the schema declares them."""

    columns = schema.CANONICAL_COLUMNS[table]
    positions = [columns.index(key) for key in logical.PRIMARY_KEYS[table]]
    connection = open_read_only_database(path)
    try:
        return {
            "/".join(str(row[position]) for position in positions)
            for row in connection.execute(f"SELECT {', '.join(columns)} FROM {table}")
        }
    finally:
        connection.close()


def _staging_directories() -> list[str]:
    """Return the private import staging directories currently under the system temporary root."""

    root = Path(tempfile.gettempdir())
    return sorted(path.name for path in root.glob("knowledge-import-*"))


def _cited_anchor_id(fixture: BranchingKnowledgeFixture) -> str:
    """Return the anchor one of the fixture's stored claims cites, read from the dataset."""

    connection = open_read_only_database(fixture.database_path)
    try:
        row = next(iter(connection.execute("SELECT anchor_id FROM realization_claim")))
    finally:
        connection.close()
    return str(row[0])


def _declared_digest(artifact: str) -> str:
    """Return one artifact's declared logical digest."""

    return str(envelope_of(artifact)["logicalDigest"])


def _stage_with_a_dangling_edge(
    directory: Path, fixture: BranchingKnowledgeFixture, anchor_id: str
) -> Path:
    """Stage one artifact, then write a dangling reference into the stage with enforcement off.

    This is the only way a malformed graph can exist in a database of this schema: the declared
    foreign keys refuse it at commit and the immutability triggers refuse any later edit, so the
    staged-verification read can only be exercised on a stage a test damaged on purpose. It is a
    probe of that read's policy, not a scenario the import can reach.
    """

    directory.mkdir(parents=True, exist_ok=True)
    stage = directory / "stage.sqlite"
    verified = import_into(artifact_of(fixture), directory / "published.sqlite")
    assert verified.state == "installed", verified.refusal
    copy_closed(directory / "published.sqlite", stage)
    # The anchor is deleted with enforcement off, so every claim that cites it is left dangling. No
    # trigger forbids a source-anchor deletion, and the reference survives because it is the *claim*
    # that names the anchor -- which is why this is the shape a damaged graph takes. The probe is of
    # the staged-verification read, not of the schema.
    connection = apsw.Connection(str(stage))
    try:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("DELETE FROM source_anchor WHERE anchor_id = ?", (anchor_id,))
    finally:
        connection.close()
    return stage


def labels_of(path: Path) -> list[str]:
    connection = open_read_only_database(path)
    try:
        return sorted(
            str(row[0]) for row in connection.execute("SELECT display_label FROM invariant")
        )
    finally:
        connection.close()


def _empty_dataset(directory: Path) -> Path:
    """Create one declared dataset with no rows, through the candidate lifecycle.

    This is the dataset a candidate starts as, so it is the honest producer of the "present and
    empty" collections: nothing is written into it but its namespace row, and it is closed so the
    export reads a file rather than a live handle.
    """

    directory.mkdir(parents=True, exist_ok=True)
    destination = AdmittedCandidateDestination(
        directory=directory / "admission",
        repository=RepositoryIdentity(repository_id=str(uuid4()), authority_home="agents-remember"),
        resolution=CandidateResolution(
            lane="draft-candidate",
            code_tree_id="c" * 40,
            memory_tree_id="d" * 40,
            snapshot_ref="candidate:portable-roundtrip",
            candidate_ref="draft:portable-roundtrip",
        ),
    )
    created = create_candidate(destination)
    assert created.state == "created", created.refusal
    return copy_closed(destination.database_path, directory / "empty.sqlite")


# -- the encoder, and the artifact it produces -------------------------------------------------


def test_the_artifact_is_one_deterministic_document_of_the_declared_shape(
    fixture: BranchingKnowledgeFixture,
) -> None:
    """The encoder is deterministic and the format is a document shape, not a convention.

    Five facts in one case because they are one property -- "one dataset has one artifact" -- and each
    could pass while another failed: the key set says the document *is* the format, the header says
    which schema generation it describes, and the byte comparison says re-encoding is stable. The
    digest's own identity claim is measured in the completeness case below.
    """

    first = exported(fixture)
    second = exported(fixture)
    body = envelope_of(first.artifact or "")

    assert tuple(body) == ENVELOPE_KEYS
    assert body["format"] == EXPORT_FORMAT
    assert body["schema"] == KNOWLEDGE_SCHEMA_NAME
    assert body["userVersion"] == schema.SCHEMA_USER_VERSION
    assert body["schemaFingerprint"] == schema.schema_fingerprint()
    assert len(body["tables"]) == TABLE_COUNT
    assert first.artifact == second.artifact
    assert first.artifact_digest == second.artifact_digest


def test_the_artifact_holds_every_collection_and_the_datasets_own_identity(
    fixture: BranchingKnowledgeFixture, tmp_path: Path
) -> None:
    """The digest is the dataset's logical identity, and every collection is present as itself.

    Completeness is measured twice on purpose: the shared fixture populates all ten collections, and a
    *fresh* dataset holds only its namespace row while still exporting the nine collections it left
    empty. A reader that dropped an empty collection passes the first half and fails the second,
    which is the case this pair exists for.
    """

    result = exported(fixture)
    body = envelope_of(result.artifact or "")
    fresh = envelope_of(artifact_of(_empty_dataset(tmp_path / "empty")))
    empty_collections = [table for table in schema.CANONICAL_TABLES if fresh["tables"][table] == []]

    assert body["logicalDigest"] == identity_of(fixture.database_path).logical_digest
    assert result.identity is not None and result.identity.logical_digest == body["logicalDigest"]
    assert set(body["tables"]) == set(schema.CANONICAL_TABLES)
    assert all(body["tables"][table] for table in schema.CANONICAL_TABLES)
    assert fresh["tables"]["repository"] != []
    assert len(empty_collections) == TABLE_COUNT - 1


def test_rows_and_strings_cross_the_boundary_exactly(
    fixture: BranchingKnowledgeFixture, tmp_path: Path
) -> None:
    """Columns keep their declared order, typed columns cross decoded, and text is not rewritten.

    The three are one property of the encoder -- the artifact carries the *value* rather than a
    rendering of it -- so they are measured together: a row in declared order, a typed JSON column as
    a JSON value, and a string whose right-to-left mark, CRLF and non-ASCII character a normalising
    encoder would change.
    """

    body = envelope_of(artifact_of(fixture))
    revision = body["tables"]["invariant_revision"][0]
    authored = "caf\u00e9 \u202eRTL line\r\nsecond line"
    case = build_case(tmp_path, diverging_revisions=False, diverging_identities=False)
    set_label(case.state_path("base"), authored)
    destination = tmp_path / "restored.sqlite"

    installed = import_into(artifact_of(case.state_path("base")), destination)

    assert isinstance(revision["conditions"], list)
    assert isinstance(revision["provenance"], dict)
    assert revision["provenance"]["actor_ref"] == "agent:fixture"
    assert installed.state == "installed", installed.refusal
    assert labels_of(destination) == [authored]


# -- the round trip ----------------------------------------------------------------------------


def test_a_populated_dataset_round_trips_to_an_equal_logical_dataset(
    fixture: BranchingKnowledgeFixture, tmp_path: Path
) -> None:
    """The whole dataset -- IDs, values, relations and digest -- is restored into an empty database.

    The byte comparison at the end is the strongest statement available about the round trip: a
    dataset restored from an artifact this encoder produced re-encodes to that artifact, which no
    regenerated identifier, lost row or re-spelled value could survive. It holds for *any* accepted
    artifact as well, because the reader accepts only the canonical form -- the canonical-key-order
    case below is the other half of that statement.
    """

    result = exported(fixture)
    destination = tmp_path / "restored.sqlite"
    export_path = tmp_path / "export.json"
    export_path.write_text(result.artifact or "", encoding="utf-8")
    stored = read_knowledge_artifact(export_path)
    assert isinstance(stored, str), stored

    installed = import_into(stored, destination)

    # The import's own outcome is asserted before anything reads the destination again: a case that
    # failed first on a follow-up open would name the wrong guard as the one that broke.
    assert installed.state == "installed", installed.refusal
    assert installed.publication == "published"

    again = exported(destination)
    connection = open_read_only_database(destination)
    try:
        repository = logical.bound_repository(connection)
        restored_body = logical.logical_body(connection, KNOWLEDGE_SCHEMA_NAME)
    finally:
        connection.close()
    recovered_body = canonical_body_of_artifact(result.artifact or "")
    stored_paths = sorted(str(row[2]) for row in table_rows(destination, "source_anchor"))

    assert read_knowledge_artifact(export_path) == result.artifact
    assert hashlib.sha256((result.artifact or "").encode("utf-8")).hexdigest() == (
        result.artifact_digest
    )
    assert (
        identity_of(destination).logical_digest == identity_of(fixture.database_path).logical_digest
    )
    assert row_counts_of(destination) == result.row_counts
    assert again.artifact == result.artifact
    # The read-only comparison path is the same knowledge as the dataset it installed: the body an
    # artifact carries and the body the restored file reads back are one value, which is what makes a
    # comparison across artifacts meaningful without restoring either of them.
    assert not isinstance(recovered_body, KnowledgeRefusal), recovered_body
    assert recovered_body == restored_body
    stored_keys = "\n".join(
        "\n".join(identifiers_of(destination, table)) for table in schema.CANONICAL_TABLES
    )
    for identifier in (
        fixture.invariant_id,
        fixture.base_revision_id,
        fixture.left_revision_id,
        fixture.right_revision_id,
        fixture.family.family_id,
        fixture.family.revision_id,
        fixture.integration.anchor_id,
        fixture.integration.claim_id,
        fixture.absent_source.claim_id,
    ):
        assert identifier in stored_keys, identifier
    # The restored file is usable knowledge rather than merely equal bytes: the store reopens it as
    # its typed aggregates and bound namespace, and the anchor whose recorded path resolves nowhere
    # is still recorded with that path, because import resolves no source and invents no record.
    assert repository is not None and repository.repository_id == fixture.repository_id
    assert ABSENT_PATH in stored_paths


def test_accepted_origin_state_crosses_as_data_and_is_not_promoted(tmp_path: Path) -> None:
    """An artifact carrying ``accepted`` imports that value; the import grants no authority."""

    case = build_case(tmp_path, diverging_revisions=False, diverging_identities=False)
    _set_origin_state(case.state_path("base"), "accepted", "acceptance:recorded-before-export")
    destination = tmp_path / "restored.sqlite"

    installed = import_into(artifact_of(case.state_path("base")), destination)

    assert installed.state == "installed", installed.refusal
    row = table_rows(destination, "invariant_revision")[0]
    columns = schema.CANONICAL_COLUMNS["invariant_revision"]
    assert row[columns.index("state_at_origin")] == "accepted"
    assert row[columns.index("acceptance_ref")] == "acceptance:recorded-before-export"


def test_a_merged_dataset_is_also_exportable_and_restorable(tmp_path: Path) -> None:
    """A merge result is an ordinary dataset: it exports and restores like any other."""

    case = build_case(tmp_path, diverging_revisions=False, diverging_identities=False)
    merged = _merge_case_to_destination(case, tmp_path / "merged.sqlite")
    destination = tmp_path / "restored.sqlite"

    installed = import_into(artifact_of(merged), destination)

    assert installed.state == "installed", installed.refusal
    assert identity_of(destination).logical_digest == identity_of(merged).logical_digest


def test_an_export_carries_no_git_ancestry_and_a_repeat_import_is_a_no_change(
    fixture: BranchingKnowledgeFixture, tmp_path: Path
) -> None:
    """The artifact is knowledge, not history; and identical knowledge is not rewritten.

    The two halves are one statement about what an import may do to a destination: it can restore the
    dataset the artifact names and nothing else -- no ancestry, and no bytes rewritten when the
    destination already holds exactly that dataset.
    """

    artifact = artifact_of(fixture)
    destination = tmp_path / "restored.sqlite"
    first = import_into(artifact, destination)
    before = file_digest(destination)

    second = import_into(artifact, destination, expected_destination=first.verified_identity)

    assert "commit" not in artifact.lower()
    assert "tree_id" not in artifact.lower()
    assert set(json.loads(artifact)) == set(ENVELOPE_KEYS)
    assert first.state == "installed"
    assert second.state == "no_change" and second.publication == "no_change"
    assert file_digest(destination) == before


# -- completeness refusals ---------------------------------------------------------------------


def test_a_row_is_the_declared_columns_in_declared_order_with_declared_types(
    fixture: BranchingKnowledgeFixture, tmp_path: Path
) -> None:
    """A row is exactly the declared columns, in declared order, holding declared-type values.

    Read from two directions in one case because they are one contract. The order claim says the
    artifact the encoder produces satisfies its own reader -- which is what makes the round trip a
    proof rather than a coincidence. The type claim holds the declared-TEXT half of the type rule to a
    column: a JSON object in a text column has to be refused, not re-encoded into a string that would
    then load, digest and round-trip as if it were the value the artifact declared.
    """

    body = envelope_of(artifact_of(fixture))
    columns = schema.CANONICAL_COLUMNS["invariant_revision"]
    text_column = "applicability"

    ordered_defect = envelope_of(artifact_of(fixture))
    ordered_defect["tables"]["invariant_revision"] = [
        {
            column: ordered_defect["tables"]["invariant_revision"][0][column]
            for column in reversed(columns)
        }
    ]
    text_defect = envelope_of(artifact_of(fixture))
    text_defect["tables"]["invariant_revision"] = [
        {
            **text_defect["tables"]["invariant_revision"][0],
            text_column: {"not": "text"},
        }
    ]
    outcomes = {
        "row_order": import_into(reencode(ordered_defect), tmp_path / "a.sqlite"),
        "text_column": import_into(reencode(text_defect), tmp_path / "b.sqlite"),
    }

    for table in schema.CANONICAL_TABLES:
        for row in body["tables"][table]:
            assert tuple(row) == schema.CANONICAL_COLUMNS[table], table
    assert [outcome.state for outcome in outcomes.values()] == ["refused"] * 2
    assert _refusal(outcomes["row_order"]).table == "invariant_revision"
    assert "declared columns in" in _refusal(outcomes["row_order"]).detail
    assert _refusal(outcomes["text_column"]).record_id == text_column
    assert sqlite_entries(tmp_path) == []


def _inconsistent_artifacts(fixture: BranchingKnowledgeFixture) -> dict[str, str]:
    """Return each malformed artifact one case refuses, keyed by the defect it carries.

    Building them is one cohesive step -- every entry is one edit to a faithful export -- and keeping
    it out of the case leaves the case's body as the measurements and the assertions that name which
    check caught which defect. Only the duplicated-key entry is resealed: it is about content, so its
    declared seal has to be honest about the records it carries for the *next* check to be the one
    that refuses it.
    """

    missing = envelope_of(artifact_of(fixture))
    del missing["tables"]["family_member"]
    truncated = envelope_of(artifact_of(fixture))
    truncated["tables"]["realization_claim"] = truncated["tables"]["realization_claim"][:-1]
    unknown_table = envelope_of(artifact_of(fixture))
    unknown_table["tables"]["assessment"] = []
    wrong_type = envelope_of(artifact_of(fixture))
    wrong_type["tables"]["repository"] = [
        {**wrong_type["tables"]["repository"][0], "authority_home": {"nested": True}}
    ]
    not_an_object = envelope_of(artifact_of(fixture))
    not_an_object["tables"]["family_member"] = ["this is not a row"]
    duplicated = envelope_of(artifact_of(fixture))
    rows = duplicated["tables"]["family_member"]
    duplicated["tables"]["family_member"] = [*rows, dict(rows[0])]
    reordered = envelope_of(artifact_of(fixture))
    columns = schema.CANONICAL_COLUMNS["repository"]
    reordered["tables"]["repository"] = [
        {column: reordered["tables"]["repository"][0][column] for column in reversed(columns)}
    ]
    renamed = envelope_of(artifact_of(fixture))
    row = dict(renamed["tables"]["repository"][0])
    row["authorityHome"] = row.pop("authority_home")
    renamed["tables"]["repository"] = [row]
    nulled = envelope_of(artifact_of(fixture))
    nulled["tables"]["repository"] = [{**nulled["tables"]["repository"][0], "repository_id": None}]
    unbound = envelope_of(artifact_of(fixture))
    unbound["tables"]["repository"] = []
    doubly_bound = envelope_of(artifact_of(fixture))
    doubly_bound["tables"]["repository"] = [
        dict(doubly_bound["tables"]["repository"][0]),
        {
            **doubly_bound["tables"]["repository"][0],
            "repository_id": "11111111-1111-4111-8111-111111111111",
        },
    ]
    bare_number = envelope_of(artifact_of(fixture))
    bare_number["tables"]["invariant_revision"] = [
        {**bare_number["tables"]["invariant_revision"][0], "conditions": 7}
    ]
    bare_boolean = envelope_of(artifact_of(fixture))
    bare_boolean["tables"]["invariant_revision"] = [
        {**bare_boolean["tables"]["invariant_revision"][0], "provenance": True}
    ]
    return {
        "missing_collection": reencode(missing),
        "truncated_table": reencode(truncated),
        "unknown_table": reencode(unknown_table),
        "wrong_type": reencode(wrong_type),
        "row_not_an_object": reencode(not_an_object),
        "duplicate_primary_key": reseal(duplicated),
        "reordered_row": reencode(reordered),
        "renamed_column": reencode(renamed),
        "null_primary_key": reencode(nulled),
        "bare_number": reencode(bare_number),
        "bare_boolean": reencode(bare_boolean),
        "unbound": reencode(unbound),
        "doubly_bound": reencode(doubly_bound),
    }


def test_an_incomplete_or_inconsistent_artifact_is_refused(
    fixture: BranchingKnowledgeFixture, tmp_path: Path
) -> None:
    """Thirteen ways an artifact can be incomplete or inconsistent, each refused before publication.

    One case, thirteen mutations, because they are thirteen instances of one property -- the artifact
    does not describe a complete, bound, typed logical dataset of this schema generation -- and the
    assertions name which check caught which, so a regression that lost one check fails here rather
    than passing on the strength of the other twelve. Four are document-level (a dropped collection, a
    truncated table, an undeclared table, a text column holding an object), five are row-level (a row
    that is not an object, a duplicated key, a reordered row, a renamed column, a nulled key), two are
    typed-column-level (a bare number and a bare boolean where a JSON value belongs) and two are
    binding-level (no namespace row at all, and two). The nullable column is read in the same case,
    because "null is a value where the schema allows one" is the other half of the same declared-type
    rule.
    """

    artifacts = _inconsistent_artifacts(fixture)
    outcomes = {
        name: import_into(text, tmp_path / f"{position}.sqlite")
        for position, (name, text) in enumerate(artifacts.items())
    }

    assert [outcome.state for outcome in outcomes.values()] == ["refused"] * 13
    assert all(
        outcome.refusal is not None and outcome.refusal.code == "invalid_export"
        for outcome in outcomes.values()
    )
    # Document-level: which collection, and which declared seal.
    assert _refusal(outcomes["missing_collection"]).table == "family_member"
    assert _refusal(outcomes["truncated_table"]).expected == _declared_digest(
        artifacts["truncated_table"]
    )
    assert _refusal(outcomes["unknown_table"]).table == "assessment"
    assert _refusal(outcomes["wrong_type"]).record_id == "authority_home"
    # Row-level: a row that is not an object at all, the identity claimed twice, the reordered row,
    # the renamed and the nulled column.
    assert _refusal(outcomes["row_not_an_object"]).table == "family_member"
    assert "not a JSON object" in _refusal(outcomes["row_not_an_object"]).detail
    assert _refusal(outcomes["duplicate_primary_key"]).table == "family_member"
    assert "declared columns in" in _refusal(outcomes["reordered_row"]).detail
    assert _refusal(outcomes["renamed_column"]).table == "repository"
    assert "primary key" in _refusal(outcomes["null_primary_key"]).detail
    # A typed JSON column holds a JSON value: a bare number and a bare boolean are not ones.
    assert _refusal(outcomes["bare_number"]).record_id == "conditions"
    assert _refusal(outcomes["bare_boolean"]).record_id == "provenance"
    # A dataset is bound to exactly one namespace: neither zero repository rows nor two is a
    # dataset this store can address, and an index or a later ambiguity is not the refusal.
    assert _refusal(outcomes["unbound"]).table == "repository"
    assert "0 repository row(s)" in _refusal(outcomes["unbound"]).detail
    assert _refusal(outcomes["doubly_bound"]).table == "repository"
    assert "2 repository row(s)" in _refusal(outcomes["doubly_bound"]).detail
    assert all(
        row["acceptance_ref"] is None
        for row in envelope_of(artifact_of(fixture))["tables"]["invariant_revision"]
    )
    assert sqlite_entries(tmp_path) == []


def _refusal(outcome) -> KnowledgeRefusal:
    """Return one refused import's refusal, failing the case if it carried none."""

    assert outcome.refusal is not None, outcome
    return outcome.refusal


def test_a_filtered_read_response_cannot_validate_as_a_complete_export(
    fixture: BranchingKnowledgeFixture, tmp_path: Path
) -> None:
    """A partial projection is not a backup, whatever it holds.

    Two shapes are refused, and both are the failure this requirement exists to prevent: a bare table
    projection with no envelope at all, and an envelope whose records were narrowed to a subset of the
    dataset, which the declared seal catches. The counterpart case below keeps this one honest -- a
    reader that refused every projection would look identical to one that checks completeness.
    """

    body = envelope_of(artifact_of(fixture))
    projection = json.dumps({"realization_claim": body["tables"]["realization_claim"][:1]})
    narrowed = {**body, "tables": {**body["tables"], "realization_claim": []}}

    bare = validate_knowledge_artifact(projection)
    partial = import_into(reencode(narrowed), tmp_path / "refused.sqlite")
    bodied = canonical_body_of_artifact(reencode(narrowed))

    assert bare.state == "refused" and bare.refusal is not None
    assert bare.refusal.code == "invalid_export"
    assert partial.state == "refused" and partial.refusal is not None
    assert partial.refusal.code == "invalid_export"
    # The narrowed envelope *parses* as a document, so a comparison path that skipped validation
    # would hand back a body for records that are not a complete dataset. It refuses instead.
    assert isinstance(bodied, KnowledgeRefusal)
    assert bodied.code == "invalid_export"
    assert not (tmp_path / "refused.sqlite").exists()


def test_a_complete_projection_of_the_source_tables_still_validates(
    fixture: BranchingKnowledgeFixture,
) -> None:
    """The counterpart of the filtered-response case: a complete projection *is* a complete export.

    Without this case the previous one would pass for the wrong reason -- a reader that refused every
    projection would look identical to one that checks completeness.
    """

    body = envelope_of(artifact_of(fixture))
    rebuilt = {key: body[key] for key in ENVELOPE_KEYS}
    rebuilt["tables"] = {table: body["tables"][table] for table in schema.CANONICAL_TABLES}

    verdict = validate_knowledge_artifact(reencode(rebuilt))

    assert verdict.state == "validated", verdict.refusal
    assert verdict.logical_digest == body["logicalDigest"]


def test_a_document_that_is_not_a_well_formed_artifact_is_refused(
    fixture: BranchingKnowledgeFixture, tmp_path: Path
) -> None:
    """Five document-level defects, each refused before the artifact is read as a dataset.

    Not JSON at all, a repeated object key, a document missing a required envelope field, a document
    carrying a field this format does not declare, and -- the fifth, and the only one that is a defect
    of a *value* rather than of the document's shape -- a complete, well-shaped document carrying a
    number the canonical encoding has no spelling for. They are one property -- the *document* is not
    this format -- and the assertions name the wording or the field each refusal reports, so a
    regression that collapsed two checks into one still fails here.

    The unrenderable-value half exists because ``json.dumps(..., allow_nan=False)`` raises a raw
    ``ValueError`` for such a value, and Python's own decoder hands it one for three spellings a JSON
    producer really emits: the non-JSON constants ``NaN`` and ``Infinity``, and a number that
    overflows to an infinity (``1e400``). ``import_knowledge_dataset`` publishes "every failure
    returns a typed refusal with the destination untouched", so the ``ValueError`` is caught here and
    turned into a failure of *this case* rather than being allowed to escape as an exception -- a
    mutant that lets it out dies on the ``escaped == {}`` assertion below -- and the three spellings
    are driven together because a fix for one of them leaves the other two open.
    """

    unparsable = validate_knowledge_artifact("not a document at all")
    duplicated = validate_knowledge_artifact(
        '{"format":"ar-knowledge-export/v1","format":"ar-knowledge-export/v1",'
        '"schema":"ar-knowledge-sqlite/v1"}'
    )
    incomplete = validate_knowledge_artifact('{"format":"ar-knowledge-export/v1"}')
    alien = validate_knowledge_artifact(
        '{"format":"ar-knowledge-export/v1","generatedBy":"some other tool"}'
    )

    assert unparsable.state == "refused" and unparsable.refusal is not None
    assert "not valid JSON" in unparsable.refusal.detail
    assert duplicated.state == "refused" and duplicated.refusal is not None
    assert "duplicate JSON key" in duplicated.refusal.detail
    assert incomplete.state == "refused" and incomplete.refusal is not None
    assert "logicalDigest" in incomplete.refusal.detail
    assert alien.state == "refused" and alien.refusal is not None
    assert alien.refusal.record_id == "generatedBy"
    codes = {
        verdict.refusal.code
        for verdict in (unparsable, duplicated, incomplete, alien)
        if verdict.refusal is not None
    }
    assert codes == {"invalid_export"}

    artifact = artifact_of(fixture)
    assert '"userVersion":1,' in artifact
    unrenderable = {
        name: artifact.replace('"userVersion":1,', f'"userVersion":{spelling},', 1)
        for name, spelling in (
            ("not_a_json_constant_nan", "NaN"),
            ("not_a_json_constant_infinity", "Infinity"),
            ("a_number_that_overflows_to_an_infinity", "1e400"),
        )
    }
    assert all(text != artifact for text in unrenderable.values())
    escaped: dict[str, str] = {}
    value_verdicts = []
    imported = []
    canonical_forms = []
    for name, text in unrenderable.items():
        try:
            value_verdicts.append(validate_knowledge_artifact(text))
            imported.append(import_into(text, tmp_path / f"{name}.sqlite"))
            canonical_forms.append(canonical_document(text))
        except ValueError as error:
            escaped[name] = str(error)
    assert escaped == {}, f"a raw ValueError escaped the typed boundary: {escaped}"
    assert [verdict.state for verdict in value_verdicts] == ["refused"] * len(unrenderable)
    assert [outcome.state for outcome in imported] == ["refused"] * len(unrenderable)
    assert canonical_forms == [None] * len(unrenderable)
    assert {
        verdict.refusal.record_id for verdict in value_verdicts if verdict.refusal is not None
    } == {"<unrenderable>"}
    assert all(
        verdict.refusal is not None and verdict.refusal.code == "invalid_export"
        for verdict in value_verdicts
    )
    assert all(
        verdict.refusal is not None and "cannot spell" in verdict.refusal.detail
        for verdict in value_verdicts
    )
    assert sqlite_entries(tmp_path) == []


def test_an_artifact_this_build_or_this_namespace_cannot_accept_is_refused(
    fixture: BranchingKnowledgeFixture, tmp_path: Path
) -> None:
    """Five admissions an artifact can fail: version, fingerprint, format, namespace, namespace type.

    Five instances of one property -- this artifact is not one this build may accept *here* -- and the
    assertions keep the two codes apart, so a regression that widened ``unsupported_schema`` over a
    format or namespace mismatch fails rather than passing as a stricter refusal. The namespace case is
    the only one that is not a defect of the artifact at all: the same bytes are valid knowledge
    somewhere else, and the admitted namespace is what refuses them.

    The fifth is the *type* of the declared namespace, and it is asserted by refusal *identity* rather
    than by verdict, because a non-string namespace is still refused without the header guard that owns
    it -- and the difference is larger than the identity: the deeper namespace comparison cannot even
    build its refusal, because ``KnowledgeRefusal`` requires strings and that comparison hands it the
    integer, so a raw pydantic ``ValidationError`` crosses the public import path. Both facts are
    asserted here, and the import is wrapped so that the mutant fails on an assertion of this case
    rather than being reported as an exception-death: nothing may escape the boundary, and what comes
    back must be the guard's own refusal.
    """

    future = envelope_of(artifact_of(fixture))
    future["userVersion"] = schema.SCHEMA_USER_VERSION + 1
    other_schema = envelope_of(artifact_of(fixture))
    other_schema["schemaFingerprint"] = "a" * 64
    other_format = envelope_of(artifact_of(fixture))
    other_format["format"] = "ar-knowledge-export/v2"
    other_namespace = envelope_of(artifact_of(fixture))
    other_namespace["repositoryId"] = 12345

    unsupported = [
        import_into(reencode(future), tmp_path / "a.sqlite"),
        import_into(reencode(other_schema), tmp_path / "b.sqlite"),
    ]
    unknown_format = import_into(reencode(other_format), tmp_path / "c.sqlite")
    wrong_namespace = import_into(
        artifact_of(fixture),
        tmp_path / "d.sqlite",
        expected_repository_id="11111111-1111-4111-8111-111111111111",
    )
    escaped: str | None = None
    mistyped_namespace = None
    try:
        mistyped_namespace = import_into(reencode(other_namespace), tmp_path / "e.sqlite")
    except ValueError as error:
        escaped = f"{type(error).__name__}: {error}"

    assert escaped is None, f"a raw exception escaped the typed boundary: {escaped}"
    assert mistyped_namespace is not None
    assert all(
        outcome.state == "refused"
        for outcome in [*unsupported, unknown_format, wrong_namespace, mistyped_namespace]
    )
    codes = {outcome.refusal.code for outcome in unsupported if outcome.refusal is not None}
    assert codes == {"unsupported_schema"}
    assert unknown_format.refusal is not None
    assert unknown_format.refusal.code == "invalid_export"
    assert wrong_namespace.refusal is not None
    assert wrong_namespace.refusal.code == "invalid_export"
    assert wrong_namespace.refusal.table == "repository"
    # The fifth admission, and the one whose *identity* is the property rather than its verdict: a
    # `repositoryId` that is not a string is refused by the header's own namespace guard, and without
    # that guard the failure is not merely a different refusal -- the deeper namespace comparison
    # cannot build its refusal at all, and a raw pydantic `ValidationError` crosses the public import
    # path. The assertion names the guard's own refusal, which is what makes deleting the guard
    # visible; the wrapped call above is what makes the *mutant* fail on an assertion of this case
    # instead of being reported as an exception-death.
    assert mistyped_namespace.refusal is not None
    assert mistyped_namespace.refusal.code == "invalid_export"
    assert mistyped_namespace.refusal.record_id == "12345"
    assert mistyped_namespace.refusal.detail == "the artifact declares no repository namespace"
    assert sqlite_entries(tmp_path) == []


def test_an_artifact_declaring_another_namespace_than_its_own_rows_is_refused(
    fixture: BranchingKnowledgeFixture, tmp_path: Path
) -> None:
    """The declared namespace must be the one the artifact's own repository row holds.

    Every other namespace admission compares the artifact against the *destination*; this one
    compares the artifact against **itself**, and it is the only check that can see the defect:
    the artifact's rows are a dataset of one namespace, its header declares another, and there is
    no destination namespace to disagree with. Ablating it therefore does not change a refusal
    identity -- it lets a foreign artifact **validate and install**, which is cross-namespace
    contamination through the one path whose whole purpose is safe installation.

    **``repositoryId`` is a header field, and the declared digest covers ``tables`` only**, so
    changing the namespace leaves the seal valid: a resealed spelling would be byte-identical to
    the plain one and would measure the same artifact twice. One artifact is therefore measured
    once, and the assertion below states which check refuses it rather than implying a second one
    that cannot exist. The ablation is the control: with this guard removed the artifact validates
    and installs, so no earlier check was standing in its way.
    """

    other = "22222222-2222-4222-8222-222222222222"
    envelope = envelope_of(artifact_of(fixture))
    envelope["repositoryId"] = other
    text = reseal(envelope)

    assert text == reencode(envelope), (
        "the namespace sits outside the seal, so resealing must not move a byte: this keeps the "
        "node honest about measuring one artifact rather than two"
    )
    directory = tmp_path / "declared_other"
    directory.mkdir()
    outcome = import_into(text, directory / "destination.sqlite")
    assert outcome.state == "refused", (
        "the declared digest seals exactly the records the artifact carries, so the namespace "
        "disagreeing with its own repository row is the only check left to refuse it"
    )
    assert _refusal(outcome).code == "invalid_export"
    assert _refusal(outcome).table == "repository"
    assert _refusal(outcome).expected == other
    assert _refusal(outcome).observed == fixture.repository_id
    assert "namespace" in _refusal(outcome).detail
    assert sqlite_entries(directory) == []


def test_a_dangling_reference_is_refused_at_commit(
    fixture: BranchingKnowledgeFixture, tmp_path: Path
) -> None:
    """A missing endpoint is refused: the declared foreign keys are deferred, not dropped.

    The artifact is *resealed* after the endpoint is removed, so the declared digest is honest about
    the records it carries. What refuses it is therefore the loaded graph, not the seal -- which is the
    check the requirement names when it asks for validation of missing references.
    """

    body = envelope_of(artifact_of(fixture))
    claim = dict(body["tables"]["realization_claim"][0])
    claim["anchor_id"] = UNKNOWN_UUID
    body["tables"]["realization_claim"] = [claim]
    artifact = reseal(body)

    refused = import_into(artifact, tmp_path / "refused.sqlite")

    assert refused.state == "refused"
    assert refused.refusal is not None
    assert refused.refusal.code == "relationship_constraint"
    assert sqlite_entries(tmp_path) == []

    # The refusal composes two guards, and the end-to-end case above reaches only the first: the
    # declared foreign keys are deferred to commit, so the load itself refuses a dangling edge and
    # the staged-verification read is never consulted. That read is exercised here directly, on a
    # stage built the only way a malformed graph can exist at all in this schema -- written with
    # enforcement off -- rather than being left as an assumed guard.
    malformed = _stage_with_a_dangling_edge(
        tmp_path / "malformed", fixture, _cited_anchor_id(fixture)
    )
    staged_refusal = _verify_staged_dataset(
        malformed,
        logical.SnapshotIdentity(
            repository_id=fixture.repository_id,
            schema_version=KNOWLEDGE_SCHEMA_NAME,
            logical_digest=_declared_digest(artifact),
        ),
    )

    assert staged_refusal is not None
    assert staged_refusal.code == "relationship_constraint"
    assert "holds 1 foreign-key violation(s)" in staged_refusal.detail
    assert "realization_claim" in staged_refusal.detail


# -- destination behaviour ---------------------------------------------------------------------


def test_a_destination_is_replaced_only_for_the_admitted_identity(
    fixture: BranchingKnowledgeFixture, tmp_path: Path
) -> None:
    """An import never replaces a dataset the request admitted no identity for.

    Three destination states, one rule: an occupied destination with no admitted identity is refused
    and keeps its bytes; an occupied destination whose identity *was* admitted is replaced atomically;
    and an admitted destination that is absent is refused rather than silently created, because a
    caller that named the identity it expected to replace asked a different question than "install
    this somewhere".
    """

    destination = tmp_path / "occupied.sqlite"
    first = import_into(artifact_of(fixture), destination)
    assert first.state == "installed"
    before = file_digest(destination)
    other = build_branching_knowledge_fixture(tmp_path / "other")
    absent = tmp_path / "absent.sqlite"

    before_staging = _staging_directories()
    unadmitted = import_into(artifact_of(other), destination)
    after_staging = _staging_directories()
    # The refusal is measured before the admitted replace below runs: this is the state the
    # destination is left in by the import that was refused, byte for byte. The staging comparison is
    # the other half -- an occupied destination is refused *before* a stage is built, so the refusal
    # costs no database work and leaves no private directory behind for a later step to find.
    refused_bytes = file_digest(destination)
    replaced = import_into(
        artifact_of(other), destination, expected_destination=first.verified_identity
    )
    vanished = import_into(
        artifact_of(fixture),
        absent,
        expected_destination=logical.SnapshotIdentity(
            repository_id=fixture.repository_id,
            schema_version=KNOWLEDGE_SCHEMA_NAME,
            logical_digest="0" * 64,
        ),
    )

    assert unadmitted.state == "refused"
    assert unadmitted.refusal is not None
    # ``destination_occupied`` rather than the later ``destination_stale``: the occupied destination
    # is refused by the import's own admission check, before a stage exists. The publication would
    # also refuse it, one step later and under a code about the destination's *identity* rather than
    # about the request having admitted none.
    assert unadmitted.refusal.code == "destination_occupied"
    assert refused_bytes == before
    assert after_staging == before_staging
    assert replaced.state == "installed", replaced.refusal
    assert replaced.publication == "published"
    assert file_digest(destination) != before
    assert (
        identity_of(destination).logical_digest == identity_of(other.database_path).logical_digest
    )
    assert vanished.state == "refused"
    assert vanished.refusal is not None and vanished.refusal.code == "destination_stale"
    assert not absent.exists()


def test_a_successful_import_leaves_no_stage_journal_or_peer_behind(
    fixture: BranchingKnowledgeFixture, tmp_path: Path
) -> None:
    """A successful import publishes one database file and leaves no stage, journal or peer.

    The one other entry the publication creates beside the file is L4's lock resource
    (``.<name>.lock``), which is asserted here rather than left implicit so the directory's contents
    are the claim. Whether a *capture* of the destination directory enumerates files or the
    directory is L4's open question and not decided here.
    """

    installed = import_into(artifact_of(fixture), tmp_path / "restored.sqlite")

    assert installed.state == "installed"
    assert sqlite_entries(tmp_path) == ["restored.sqlite"]
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        ".restored.sqlite.lock",
        "restored.sqlite",
        "source",
    ]


def test_a_refused_import_preserves_the_destination_bytes_and_leaves_no_stage(
    fixture: BranchingKnowledgeFixture, tmp_path: Path
) -> None:
    """The refusal-inertness evidence class: before and after are one file, and nothing new."""

    destination = tmp_path / "occupied.sqlite"
    assert import_into(artifact_of(fixture), destination).state == "installed"
    before = file_digest(destination)
    body = envelope_of(artifact_of(fixture))
    del body["tables"]["source_anchor"]

    refused = import_into(
        reencode(body), destination, expected_destination=identity_of(destination)
    )

    assert refused.state == "refused"
    assert refused.refusal is not None and refused.refusal.code == "invalid_export"
    assert file_digest(destination) == before
    assert sqlite_entries(tmp_path) == ["occupied.sqlite"]


# -- export guards -----------------------------------------------------------------------------


def test_exporting_a_moved_or_absent_dataset_is_refused(tmp_path: Path) -> None:
    """An export is addressed at a dataset, and a missing input is never an empty one."""

    case = build_case(tmp_path, diverging_revisions=False, diverging_identities=False)
    admitted = identity_of(case.state_path("base"))
    set_label(case.state_path("base"), "a label written after the admission")

    moved = export_knowledge_artifact(
        ExportRequest(database_path=case.state_path("base"), expected_identity=admitted)
    )
    absent = export_knowledge_artifact(
        ExportRequest(
            database_path=tmp_path / "absent.sqlite",
            expected_identity=logical.SnapshotIdentity(
                repository_id="11111111-1111-4111-8111-111111111111",
                schema_version=KNOWLEDGE_SCHEMA_NAME,
                logical_digest="0" * 64,
            ),
        )
    )

    assert moved.state == "refused"
    assert moved.refusal is not None and moved.refusal.code == "stale_precondition"
    assert moved.refusal.expected == admitted.logical_digest
    assert moved.artifact is None
    assert absent.state == "refused"
    assert absent.refusal is not None
    assert absent.refusal.code == "selected_input_unavailable"


# -- helpers -----------------------------------------------------------------------------------


def _set_origin_state(path: Path, state: str, acceptance_ref: str) -> None:
    """Write one revision's origin state as a dataset that recorded it earlier would hold.

    The write path only authors ``proposed`` rows, so an ``accepted`` origin state is produced here by
    editing the stored row and restoring the trigger afterwards -- the "historical data this store did
    not author" the round trip has to preserve. The row is **resealed** as part of the edit, through
    the package's own decoder and sealer: ``state_at_origin`` is part of the payload the digest
    covers, so a row whose state was rewritten without recomputing its seal is not a dataset that
    recorded an acceptance, it is a row altered behind its identity -- the damaged shape the import
    refuses, and the shape this case would otherwise have been measuring instead of preservation.
    """

    connection = apsw.Connection(str(path))
    try:
        connection.execute("DROP TRIGGER invariant_revision_no_update")
        for revision in _resealed_revisions(connection, state, acceptance_ref):
            connection.execute(
                "UPDATE invariant_revision SET state_at_origin = ?, acceptance_ref = ?, "
                "payload_digest = ? WHERE repository_id = ? AND revision_id = ?",
                (
                    state,
                    acceptance_ref,
                    revision.payload_digest,
                    revision.repository_id,
                    revision.revision_id,
                ),
            )
    finally:
        connection.close()
    connection = apsw.Connection(str(path))
    try:
        connection.execute(
            "CREATE TRIGGER invariant_revision_no_update BEFORE UPDATE ON invariant_revision "
            "BEGIN SELECT RAISE(ABORT, 'immutable_revision: revision rows cannot be updated'); END"
        )
    finally:
        connection.close()


def _resealed_revisions(connection: apsw.Connection, state: str, acceptance_ref: str):
    """Yield every stored revision with that origin state recorded and its own seal recomputed."""

    columns = ", ".join(schema.CANONICAL_COLUMNS["invariant_revision"])
    for row in connection.execute(f"SELECT {columns} FROM invariant_revision"):
        stored = records.decode_revision_row(
            tuple(row),
            records.decode_predecessor_rows(
                list(
                    connection.execute(
                        "SELECT parent_revision_id FROM invariant_predecessor "
                        "WHERE child_revision_id = ?",
                        (str(row[2]),),
                    )
                )
            ),
        )
        yield sealed_revision(
            stored.revision.model_copy(
                update={"state_at_origin": state, "acceptance_ref": acceptance_ref}
            )
        )


def _merge_case_to_destination(case: MergeCase, destination: Path) -> Path:
    """Merge one case's three datasets and publish the result at ``destination``."""

    world = case.world
    assert world is not None
    resolution = resolve_knowledge_merge_base(
        MergeBaseRequest(
            repository=case.repository,
            git_base=ResolvedGitBase(
                repository_root=world.root,
                base_commit_id=world.base_commit,
                left_commit_id=world.left_commit,
                right_commit_id=world.right_commit,
            ),
            inputs=case.merge_inputs(),
        )
    )
    assert not isinstance(resolution, KnowledgeRefusal), resolution
    outcome = merge_resolved_knowledge_datasets(
        MergeRequest(
            resolution=resolution,
            databases=case.databases_by_role(),
            destination=SnapshotDestinationRequest(destination_path=destination),
        )
    )
    assert outcome.state == "structurally_merged", outcome.refusal
    return destination
