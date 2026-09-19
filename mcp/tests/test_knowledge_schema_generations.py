"""The selectable schema generations: the pin, the gate, dispatch, and generation-relative identity.

Each case protects one consequential fact of `KS-R10` requirement 2 rather than a code path:

* the pinned generation-1 fingerprint recomputes to its recorded constant, and the gate **fails** on
  deliberately perturbed generation-1 data (2.5 -- a gate observed only in the passing direction is
  a comment with a test-shaped decoration);
* generation 2 is generation 1's manifest with tables appended, and never a reordered, renamed or
  retyped earlier table (1.3, Example 1);
* the artifact key lookup is type-strict, so the spellings ``1.0`` and ``true`` do not resolve to a
  generation (2.3);
* an unregistered version is refused rather than migrated, repaired or re-read (Failure And
  Recovery Behavior);
* a generation-2 table's rows and values enter the digest of a generation-2 dataset, and a
  generation-1 dataset's body is its own ten tables with its own pinned identity (2.4, 2.6).
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import apsw
import pytest
from agents_remember.application.knowledge_export import (
    export_knowledge_artifact,
    import_knowledge_artifact,
)
from agents_remember.kernel.canonical_json import sha256_digest as sha256_digest_of
from agents_remember.memory.knowledge import logical
from agents_remember.memory.knowledge.connection import (
    create_or_validate_schema,
    inspect_schema,
    open_read_only_database,
)
from agents_remember.memory.knowledge.export_import import (
    _STAGE_FILE_NAME,
    _private_stage_directory,
    _stage_imported_dataset,
)
from agents_remember.memory.knowledge.refusals import KnowledgeStorageError
from agents_remember.memory.knowledge.schema_generations import (
    CURRENT_GENERATION,
    GENERATION_1,
    GENERATION_1_FINGERPRINT,
    GENERATION_2,
    KnowledgeSchemaPinError,
    create_schema_statements,
    generation_for_key,
    generation_for_name,
    generation_for_version,
    generation_of_database,
    generation_of_new_store,
    require_pinned_generation_1_unchanged,
    require_pinned_generation_unchanged,
    structure_fingerprint,
)
from agents_remember.models.knowledge.portable import ExportRequest, ImportRequest
from agents_remember.models.knowledge.result import KnowledgeRefusal
from generation_test_support import create_generation_1_store, create_generation_2_store

pytestmark = pytest.mark.evidence_unit


def test_the_pinned_generation_1_fingerprint_recomputes_to_its_recorded_constant() -> None:
    """Requirement 2.1: the pin is checkable rather than trusted, and the gate passes."""

    assert GENERATION_1.fingerprint == GENERATION_1_FINGERPRINT
    assert structure_fingerprint(GENERATION_1) == GENERATION_1_FINGERPRINT
    require_pinned_generation_1_unchanged()


@pytest.mark.parametrize(
    "perturbation",
    [
        pytest.param(
            {"table_ddl": {**GENERATION_1.table_ddl, "repository": "CREATE TABLE repository (x)"}},
            id="one-table-ddl-string",
        ),
        pytest.param(
            {
                "triggers": {
                    **GENERATION_1.triggers,
                    "repository_no_delete": (
                        "CREATE TRIGGER repository_no_delete BEFORE DELETE ON repository "
                        "BEGIN SELECT (0) AND (1); END"
                    ),
                }
            },
            id="one-trigger-body",
        ),
        pytest.param(
            {
                "columns": {
                    **GENERATION_1.columns,
                    "repository": ("repository_id", "authority_home", "extra"),
                }
            },
            id="one-tables-column-tuple",
        ),
    ],
)
def test_the_pin_gate_fails_when_one_recorded_generation_1_field_is_perturbed(
    perturbation: dict,
) -> None:
    """Requirement 2.5, the failing direction: the gate raises, and it raises for the pin.

    The perturbation is applied to a *copy* of the recorded record rather than to the module's own
    value, so the case measures the gate rather than corrupting the registry the rest of the suite
    reads.
    """

    perturbed = replace(GENERATION_1, **perturbation)
    assert structure_fingerprint(perturbed) != GENERATION_1_FINGERPRINT
    with pytest.raises(KnowledgeSchemaPinError, match="no longer fingerprints as pinned"):
        require_pinned_generation_unchanged(perturbed)


def test_generation_2_appends_to_generation_1_without_touching_its_first_ten_tables() -> None:
    """Requirement 1.3 and Example 1, as the two comparisons the packet makes checkable."""

    assert GENERATION_2.tables[: len(GENERATION_1.tables)] == GENERATION_1.tables
    assert all(
        GENERATION_2.columns[table] == GENERATION_1.columns[table] for table in GENERATION_1.tables
    )
    assert len(GENERATION_2.tables) > len(GENERATION_1.tables)
    assert GENERATION_2.schema_name == "ar-knowledge-sqlite/v2"
    assert GENERATION_2.user_version == 2
    assert GENERATION_2.fingerprint != GENERATION_1.fingerprint


def test_the_artifact_key_lookup_is_type_strict_on_the_declared_version() -> None:
    """Requirement 2.3: ``1.0`` and ``true`` do not resolve, because Python equates all three."""

    assert generation_for_key(GENERATION_1.schema_name, 1) is GENERATION_1
    assert generation_for_key(GENERATION_2.schema_name, 2) is GENERATION_2
    assert generation_for_key(GENERATION_1.schema_name, 1.0) is None
    assert generation_for_key(GENERATION_1.schema_name, "1") is None
    assert generation_for_key(GENERATION_1.schema_name, None) is None
    assert generation_for_key(GENERATION_2.schema_name, 3) is None
    # Both spellings really are the hazard the strictness exists for.
    loose_boolean: object = True
    assert loose_boolean == 1
    assert generation_for_key(GENERATION_1.schema_name, GENERATION_1.user_version) is GENERATION_1


def test_an_unregistered_version_is_refused_rather_than_repaired_or_re_read(tmp_path: Path) -> None:
    """Failure And Recovery Behavior: an unknown generation is refused, never migrated."""

    path = tmp_path / "unregistered.sqlite"
    connection = apsw.Connection(str(path))
    try:
        connection.execute("CREATE TABLE t (x TEXT)")
        connection.execute("PRAGMA user_version = 99")
    finally:
        connection.close()
    reader = open_read_only_database(path)
    try:
        with pytest.raises(KnowledgeStorageError, match="unsupported schema"):
            generation_of_database(reader)
        with pytest.raises(KnowledgeStorageError, match="unsupported schema"):
            inspect_schema(reader)
    finally:
        reader.close()
    assert generation_for_version(99) is None


def test_creation_declares_the_newest_supported_generation_and_selection_reads_the_dataset() -> (
    None
):
    """Requirement 2.7: initialization declares; it does not select."""

    connection = apsw.Connection(":memory:")
    try:
        created = create_or_validate_schema(connection)
        assert created.schema_name == CURRENT_GENERATION.schema_name
        assert created.user_version == CURRENT_GENERATION.user_version
        assert created.fingerprint == CURRENT_GENERATION.fingerprint
        assert generation_of_new_store() is CURRENT_GENERATION
        assert generation_of_database(connection) is CURRENT_GENERATION
    finally:
        connection.close()


def test_a_generation_2_table_row_enters_the_generation_2_digest(tmp_path: Path) -> None:
    """Requirement 2.6's executable acceptance check.

    Adding and populating a **generation-2** table must change the logical digest. The shipped
    encoder built its table mapping from the live manifest, so a table that manifest did not name
    was invisible: adding and populating one left the digest byte-identical. A parameterised encoder
    that still read the global would reproduce exactly that.
    """

    path = tmp_path / "g2.db"
    connection = apsw.Connection(str(path))
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        create_or_validate_schema(connection)
        connection.execute(
            "INSERT INTO repository (repository_id, authority_home) VALUES (?, ?)",
            ("11111111-1111-4111-8111-111111111111", "agents-remember"),
        )
        before = logical.logical_digest(connection, GENERATION_2)
        assert logical.logical_body(connection, GENERATION_2)["tables"]["route"] == []
        connection.execute(
            "INSERT INTO route (repository_id, route_id, parent_route_id, path, provenance) "
            "VALUES (?, ?, NULL, ?, ?)",
            ("11111111-1111-4111-8111-111111111111", str(uuid4()), "src/pkg", "{}"),
        )
        after = logical.logical_digest(connection, GENERATION_2)
        assert logical.logical_body(connection, GENERATION_2)["tables"]["route"] != []
    finally:
        connection.close()
    assert before != after


def test_a_generation_1_dataset_keeps_its_own_body_under_generation_1(tmp_path: Path) -> None:
    """Requirement 2.4 and 5.1: an unchanged version-1 dataset reproduces its own identity.

    The body carries generation 1's schema name, its ``user_version`` and its pinned fingerprint,
    and its table mapping is generation 1's ten tables in generation 1's declared order -- not the
    running build's sixteen.
    """

    repository_id = str(uuid4())
    with create_generation_1_store(tmp_path / "v1.db", repository_id) as store:
        body = logical.logical_body(store.connection, GENERATION_1)
        assert body["schema"] == GENERATION_1.schema_name
        assert body["user_version"] == GENERATION_1.user_version
        assert body["schema_fingerprint"] == GENERATION_1_FINGERPRINT
        assert tuple(body["tables"]) == GENERATION_1.tables
        first = logical.logical_digest(store.connection, GENERATION_1)
        # The same rows under generation 2 are a **different dataset** with a different digest, and
        # are never reported as a continuation of the generation-1 identity. The comparison is made
        # through the one encoder over one table mapping rather than by reading a version-1 file
        # *as* generation 2, which the reader refuses -- a file that does not carry generation 2's
        # tables is not a generation-2 dataset, and pretending otherwise is the failure mode
        # requirement 6.2 exists to prevent.
        # A body that omits a declared table is refused, so a generation-2 body over the *same
        # rows* carries generation 2's own tables -- empty, because these rows do not populate them.
        with pytest.raises(KnowledgeStorageError, match="does not carry"):
            logical.logical_body_from_tables(GENERATION_2, body["tables"])
        same_rows = {
            **body["tables"],
            **{table: [] for table in GENERATION_2.tables if table not in body["tables"]},
        }
        over_the_same_rows = logical.logical_body_from_tables(GENERATION_2, same_rows)
        assert over_the_same_rows["schema"] == GENERATION_2.schema_name
        assert over_the_same_rows["user_version"] == GENERATION_2.user_version
        assert sha256_digest_of(over_the_same_rows) != first


def test_a_generation_1_dataset_is_still_validated_by_the_registry_it_declares(
    tmp_path: Path,
) -> None:
    """A version-1 file opened by generation-2 code validates, and reports generation 1."""

    repository_id = str(uuid4())
    path = tmp_path / "v1-open.db"
    with create_generation_1_store(path, repository_id):
        pass
    reader = open_read_only_database(path)
    try:
        assert generation_of_database(reader) is GENERATION_1
        identity = inspect_schema(reader)
        assert identity.schema_name == GENERATION_1.schema_name
        assert identity.user_version == GENERATION_1.user_version
        assert identity.fingerprint == GENERATION_1_FINGERPRINT
    finally:
        reader.close()


def test_a_version_1_artifact_is_imported_at_the_generation_it_declares(tmp_path: Path) -> None:
    """§Preservation Boundaries: importing a version-1 artifact keeps its version-1 result.

    The import stage has to be created at the generation the **artifact** declares (2.3), because
    the stage is what the artifact's logical identity is verified against. A stage created at the
    build's newest generation instead holds sixteen tables while the artifact carries generation 1's
    ten, so the digest it produces is the digest of a different dataset and the honest artifact is
    refused for not reproducing itself -- a version-1 dataset would stop being portable at exactly
    the moment the substrate gained a generation.

    Both legs run here. A case that only imported the artifact would pass again the day someone
    staged at the running build, which is the failure this case exists to catch.
    """

    repository_id = str(uuid4())
    source = tmp_path / "v1-artifact-source.db"
    with create_generation_1_store(source, repository_id):
        pass
    exported = export_knowledge_artifact(
        ExportRequest(database_path=source, expected_identity=logical.dataset_identity(source))
    )
    assert exported.state == "exported", exported.refusal
    assert exported.artifact is not None
    exported_identity = exported.identity
    assert exported_identity is not None
    envelope = json.loads(exported.artifact)
    assert (envelope["schema"], envelope["userVersion"]) == (
        GENERATION_1.schema_name,
        GENERATION_1.user_version,
    )

    destination = tmp_path / "v1-installed.db"
    installed = import_knowledge_artifact(
        ImportRequest(artifact=exported.artifact, destination_path=destination)
    )
    assert installed.state == "installed", installed.refusal
    reader = open_read_only_database(destination)
    try:
        assert generation_of_database(reader) is GENERATION_1
    finally:
        reader.close()
    assert logical.dataset_identity(destination).logical_digest == exported_identity.logical_digest

    # The same artifact staged at the running build: refused, and refused for the identity rather
    # than for anything the artifact did wrong.
    staged_at_the_build = _stage_imported_dataset(
        _private_stage_directory() / _STAGE_FILE_NAME,
        envelope["tables"],
        exported_identity,
        CURRENT_GENERATION,
    )
    assert isinstance(staged_at_the_build, KnowledgeRefusal)
    assert staged_at_the_build.code == "invalid_export"
    assert staged_at_the_build.expected == exported_identity.logical_digest


def test_a_generation_2_artifact_carries_its_appended_rows_across_an_import(tmp_path: Path) -> None:
    """The other half of the import's table set: what an artifact carries must actually be loaded.

    The version-1 case above pins *which generation the stage is created at*. This case pins the
    table set that generation's import loads: an import that created the right generation but then
    loaded a **pinned** table list would drop every appended row, and a version-2 artifact would
    install as a version-2 dataset with empty ``route`` and ``knowledge_record`` tables and a digest
    that does not match the one it declared. Both halves are needed because they fail differently —
    the first as a refusal, this one as a silent loss of data.
    """

    repository_id = str(uuid4())
    source = tmp_path / "v2-artifact-source.db"
    route_id = str(uuid4())
    record_id = str(uuid4())
    with create_generation_2_store(source, repository_id) as store:
        connection = store.connection
        connection.execute(
            "INSERT INTO route (repository_id, route_id, parent_route_id, path, provenance) "
            "VALUES (?, ?, ?, ?, ?)",
            (repository_id, route_id, None, "src/knowledge", "{}"),
        )
        connection.execute(
            "INSERT INTO knowledge_record (repository_id, record_id, kind, authority_home, "
            "lifecycle, governing_route_id, record_schema, provenance) VALUES (?, ?, ?, ?, ?, ?, "
            "?, ?)",
            (
                repository_id,
                record_id,
                "finding",
                "agents-remember",
                "draft",
                route_id,
                "f/v1",
                "{}",
            ),
        )
        exported = export_knowledge_artifact(
            ExportRequest(database_path=source, expected_identity=logical.dataset_identity(source))
        )
    assert exported.state == "exported", exported.refusal
    assert exported.artifact is not None
    exported_identity = exported.identity
    assert exported_identity is not None
    envelope = json.loads(exported.artifact)
    assert (envelope["schema"], envelope["userVersion"]) == (
        GENERATION_2.schema_name,
        GENERATION_2.user_version,
    )
    assert len(envelope["tables"]["route"]) == 1
    assert len(envelope["tables"]["knowledge_record"]) == 1

    destination = tmp_path / "v2-installed.db"
    installed = import_knowledge_artifact(
        ImportRequest(artifact=exported.artifact, destination_path=destination)
    )
    assert installed.state == "installed", installed.refusal
    reader = open_read_only_database(destination)
    try:
        assert generation_of_database(reader) is GENERATION_2
        # The appended rows crossed as data, not as empty tables the digest then disagreed about.
        assert [tuple(row) for row in reader.execute("SELECT route_id, path FROM route")] == [
            (route_id, "src/knowledge")
        ]
        assert [
            tuple(row)
            for row in reader.execute("SELECT record_id, governing_route_id FROM knowledge_record")
        ] == [(record_id, route_id)]
    finally:
        reader.close()
    assert logical.dataset_identity(destination).logical_digest == exported_identity.logical_digest


def test_every_supported_generation_declares_its_own_key_and_json_registries() -> None:
    """A record without its key and JSON-column registries cannot encode its own generation.

    Generation 1's registries are the shipped ones; generation 2's extend them, and every one of the
    first ten names keeps generation 1's. The typed-JSON columns have no DDL counterpart at all,
    which is why they are declared data rather than read out of the DDL.
    """

    assert set(GENERATION_2.primary_keys) == set(GENERATION_2.tables)
    assert set(GENERATION_1.primary_keys) == set(GENERATION_1.tables)
    assert GENERATION_2.primary_keys["record_revision"] == ("repository_id", "revision_id")
    assert GENERATION_2.json_columns["record_revision"] == frozenset({"payload", "provenance"})
    assert all(
        GENERATION_2.json_columns.get(table) == GENERATION_1.json_columns.get(table)
        for table in GENERATION_1.tables
    )
    assert generation_for_name(GENERATION_2.schema_name) is GENERATION_2
    assert generation_for_name("not-a-schema") is None
    assert "json_functions" in GENERATION_2.features
    assert "json_functions" not in GENERATION_1.features
    statements = create_schema_statements(GENERATION_2)
    assert any("json_valid" in statement for statement in statements)
    assert not any("ALTER TABLE" in statement for statement in statements)
