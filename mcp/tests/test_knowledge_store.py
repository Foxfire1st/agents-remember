"""Focused behaviour of the opened knowledge dataset: its reads, its schema and its opens.

The dataset a reader opens is the derived index of a memory tree. Each case protects one
consequential read or schema guarantee: a divergent-successor read, a database-level immutability
guarantee, a seal verified on decode, the one declared schema, and an open that cannot write. The
rows are inserted by the test-only builder (``knowledge_rows_test_support``); the write rules of the
retired canonical database have no cases here because they have no code (MIK-R26).
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import apsw
import pytest
from agents_remember.memory.knowledge import schema_generations
from agents_remember.memory.knowledge.refusals import KnowledgeStorageError
from agents_remember.memory.knowledge.schema_generations import (
    CURRENT_GENERATION,
    SCHEMA_FINGERPRINT,
    KnowledgeSchemaPinError,
    require_pinned_schema_unchanged,
)
from agents_remember.memory.knowledge.store import (
    open_existing_knowledge_store,
    open_read_only_store,
)
from knowledge_fixture_test_support import (
    BASE_DISPLAY_VERSION,
    BASE_STATEMENT,
    BRANCH_A_STATEMENT,
    BRANCH_B_STATEMENT,
    SHARED_SUCCESSOR_DISPLAY_VERSION,
    BranchingKnowledgeFixture,
    build_branching_knowledge_fixture,
    make_authorship,
)
from knowledge_rows_test_support import open_knowledge_store


@pytest.fixture
def fixture(tmp_path: Path) -> BranchingKnowledgeFixture:
    return build_branching_knowledge_fixture(tmp_path / "candidate")


def test_two_same_label_successors_reopen_as_separate_revisions(
    fixture: BranchingKnowledgeFixture,
) -> None:
    """Both v2 successors stay independently addressable with their exact statements.

    The failure this catches is the one the requirement exists for: a store that treats a
    display version as identity, or that keeps only the newest revision, would answer both
    identities with one statement.
    """

    with fixture.reopen() as store:
        base = store.get_revision(fixture.base_revision_id)
        left = store.get_revision(fixture.left_revision_id)
        right = store.get_revision(fixture.right_revision_id)
    assert base is not None and left is not None and right is not None
    assert left.revision.display_version == SHARED_SUCCESSOR_DISPLAY_VERSION
    assert right.revision.display_version == SHARED_SUCCESSOR_DISPLAY_VERSION
    assert left.revision.statement == BRANCH_A_STATEMENT
    assert right.revision.statement == BRANCH_B_STATEMENT
    assert left.revision.revision_id != right.revision.revision_id
    assert left.revision.predecessors == right.revision.predecessors == (fixture.base_revision_id,)
    assert base.revision.display_version == BASE_DISPLAY_VERSION
    assert base.revision.statement == BASE_STATEMENT
    assert base.revision.provenance == fixture.authorship
    assert left.revision.provenance == fixture.authorship


def test_reopening_the_same_path_keeps_identity_and_schema(
    fixture: BranchingKnowledgeFixture,
) -> None:
    """A reopen resolves the same identities, digests and the one declared schema."""

    with fixture.reopen() as first:
        digests = {
            revision_id: first.get_revision(revision_id).revision.payload_digest  # type: ignore[union-attr]
            for revision_id in (
                fixture.base_revision_id,
                fixture.left_revision_id,
                fixture.right_revision_id,
            )
        }
        fingerprint = first.schema.fingerprint
    with open_existing_knowledge_store(fixture.database_path, fixture.repository_id) as second:
        assert second.schema.fingerprint == fingerprint
        assert second.schema.schema_name == CURRENT_GENERATION.schema_name
        assert second.schema.user_version == CURRENT_GENERATION.user_version
        assert second.schema.fingerprint == CURRENT_GENERATION.fingerprint
        for revision_id, digest in digests.items():
            stored = second.get_revision(revision_id)
            assert stored is not None
            assert stored.revision.payload_digest == digest


def test_a_reader_opens_a_dataset_through_a_connection_that_cannot_write(
    fixture: BranchingKnowledgeFixture,
) -> None:
    """Both read opens refuse a write at the connection, whatever the caller then does.

    The reviewer opens index files through ``open_existing_knowledge_store``. Catches an open that
    hands a reader a writable connection to a file nothing but the index builder may write.
    """

    for opener in (open_existing_knowledge_store, open_read_only_store):
        with opener(fixture.database_path, fixture.repository_id) as store:
            assert store.get_revision(fixture.base_revision_id) is not None
            with pytest.raises(apsw.ReadOnlyError):
                store.connection.execute(
                    "INSERT INTO repository (repository_id, authority_home) VALUES (?, ?)",
                    (str(uuid4()), "written-through-a-reader"),
                )
    with pytest.raises(KnowledgeStorageError, match="does not exist"):
        open_existing_knowledge_store(fixture.database_path.with_name("absent.db"), "x")
    assert not fixture.database_path.with_name("absent.db").exists()  # an open never creates


def test_stored_revision_rows_refuse_update_and_delete(
    fixture: BranchingKnowledgeFixture,
) -> None:
    """The database itself refuses an in-place rewrite from any code path."""

    with fixture.reopen() as store:
        before = store.get_revision(fixture.left_revision_id)
        with pytest.raises(apsw.ConstraintError, match="immutable_revision"):
            store.connection.execute(
                "UPDATE invariant_revision SET statement = ? WHERE revision_id = ?",
                ("A rewrite attempted behind the identity.", fixture.left_revision_id),
            )
        with pytest.raises(apsw.ConstraintError, match="immutable_revision"):
            store.connection.execute(
                "DELETE FROM invariant_revision WHERE revision_id = ?",
                (fixture.left_revision_id,),
            )
        with pytest.raises(apsw.ConstraintError, match="immutable_revision"):
            store.connection.execute(
                "UPDATE invariant_predecessor SET parent_revision_id = ? "
                "WHERE child_revision_id = ?",
                (fixture.right_revision_id, fixture.left_revision_id),
            )
        after = store.get_revision(fixture.left_revision_id)
    assert before is not None and after is not None
    assert after == before
    assert after.revision.statement == BRANCH_A_STATEMENT


def test_payload_digest_seals_more_than_the_statement(
    fixture: BranchingKnowledgeFixture,
) -> None:
    """A stored payload altered outside the operation is detected when it is read back.

    The trigger is dropped first because the point of the case is the *second* defence: even a
    database edited by something that bypassed this process must not be served as if the
    revision behind the identity were unchanged.
    """

    substitute = make_authorship(actor_ref="agent:substituted").model_dump_json()
    with fixture.reopen() as store:
        store.connection.execute("DROP TRIGGER invariant_revision_no_update")
        store.connection.execute(
            "UPDATE invariant_revision SET provenance = ? WHERE revision_id = ?",
            (substitute, fixture.left_revision_id),
        )
        with pytest.raises(KnowledgeStorageError, match="payload digest"):
            store.get_revision(fixture.left_revision_id)


def test_the_created_file_carries_the_one_declared_schema(tmp_path: Path) -> None:
    """The created database carries the declared schema's own manifest and column order."""

    declared = CURRENT_GENERATION
    path = tmp_path / "manifest.db"
    with open_knowledge_store(path, str(uuid4())) as store:
        tables = {
            str(row[0])
            for row in store.connection.execute(
                "SELECT name FROM sqlite_schema WHERE type = 'table'"
            )
        }
        user_version = int(next(iter(store.connection.execute("PRAGMA user_version")))[0])
        columns = {
            table: tuple(
                str(row[1]) for row in store.connection.execute(f"PRAGMA table_info({table})")
            )
            for table in declared.tables
        }
    assert set(declared.tables) == tables
    assert user_version == declared.user_version
    assert columns == {table: tuple(declared.columns[table]) for table in declared.tables}


def test_the_declared_schema_is_pinned_and_a_changed_table_definition_fails_the_pin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MIK-R26 (ruling Q1): one schema, the index's, with the table definitions kept.

    The composed schema fingerprints as the recorded constant, which is the fingerprint the last
    generation of the retired registry had -- so an index built before the registry was removed
    and one built after it declare the same schema. Catches a table definition edited without a
    deliberate new schema version.
    """

    require_pinned_schema_unchanged()
    assert CURRENT_GENERATION.fingerprint == SCHEMA_FINGERPRINT
    assert (CURRENT_GENERATION.schema_name, CURRENT_GENERATION.user_version) == (
        "ar-knowledge-sqlite/v9",
        9,
    )
    assert len(CURRENT_GENERATION.tables) == 40
    assert schema_generations.generation_for_name("ar-knowledge-sqlite/v8") is None
    assert not hasattr(schema_generations, "GENERATIONS")  # no registry of openable generations

    edited = dict(CURRENT_GENERATION.columns)
    edited["invariant"] = (*edited["invariant"], "a_new_column")
    changed = replace(CURRENT_GENERATION, columns=edited, fingerprint="")
    changed = replace(changed, fingerprint=schema_generations.structure_fingerprint(changed))
    monkeypatch.setattr(schema_generations, "CURRENT_GENERATION", changed)
    with pytest.raises(KnowledgeSchemaPinError, match="no longer fingerprints as pinned"):
        require_pinned_schema_unchanged()


def test_a_database_that_is_not_this_schema_refuses_to_open(tmp_path: Path) -> None:
    """Three ways a stored database is not this schema, and each is refused by name.

    A missing table, a dropped immutability trigger and another generation's version are one
    question -- is this file the schema this build declares -- asked by the same opening guard.
    A database of an earlier generation is the legacy format: it is never read as if it declared
    this schema, and the refusal names the conversion.
    """

    def built(name: str) -> Path:
        path = tmp_path / name
        open_knowledge_store(path, str(uuid4())).close()
        return path

    partial = built("partial.db")
    connection = apsw.Connection(str(partial))
    connection.execute("DROP TABLE realization_claim")
    connection.close()
    with pytest.raises(KnowledgeStorageError, match="missing canonical table"):
        open_existing_knowledge_store(partial, str(uuid4()))

    untriggered = built("untriggered.db")
    connection = apsw.Connection(str(untriggered))
    connection.execute("DROP TRIGGER invariant_revision_no_delete")
    connection.close()
    with pytest.raises(KnowledgeStorageError, match="missing immutability trigger"):
        open_existing_knowledge_store(untriggered, str(uuid4()))

    for version in (1, 8, 10):
        legacy = built(f"generation-{version}.db")
        connection = apsw.Connection(str(legacy))
        connection.execute(f"PRAGMA user_version = {version}")
        connection.close()
        with pytest.raises(KnowledgeStorageError, match="unsupported schema") as refused:
            open_existing_knowledge_store(legacy, str(uuid4()))
        assert f"user_version is {version}" in str(refused.value)
        assert "agents-remember knowledge-convert" in str(refused.value)


def test_lower_ranked_owners_do_not_import_the_memory_domain() -> None:
    """The layer contract's direction holds for the new storage home.

    Storage ranks above the worktree and memory-quality owners, so an import from either into
    this package would be a cycle with a type annotation on it. The check is the reverse-import
    direction only; every other package may import the shared vocabulary freely.
    """

    package_root = Path(__file__).resolve().parents[1] / "src" / "agents_remember"
    offenders: list[str] = []
    for owner in ("worktrees", "memory_quality"):
        for module in sorted((package_root / owner).rglob("*.py")):
            for line in module.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped.startswith(
                    ("from agents_remember.memory ", "from agents_remember.memory.")
                ) or (stripped.startswith("import agents_remember.memory")):
                    offenders.append(f"{module.relative_to(package_root)}: {stripped}")
    assert offenders == []
