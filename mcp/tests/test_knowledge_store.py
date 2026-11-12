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
from agents_remember.memory.knowledge import schema
from agents_remember.memory.knowledge.records import decode_revision_row
from agents_remember.memory.knowledge.refusals import KnowledgeStorageError
from agents_remember.memory.knowledge.schema import (
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
    BASE_STATEMENT,
    SECOND_INVARIANT_STATEMENT,
    THIRD_INVARIANT_STATEMENT,
    BranchingKnowledgeFixture,
    build_branching_knowledge_fixture,
    make_authorship,
)
from knowledge_rows_test_support import open_knowledge_store


@pytest.fixture
def fixture(tmp_path: Path) -> BranchingKnowledgeFixture:
    return build_branching_knowledge_fixture(tmp_path / "candidate")


def test_three_separately_recorded_revisions_stay_independently_addressable(
    fixture: BranchingKnowledgeFixture,
) -> None:
    """Three single-revision records stay independently addressable with their exact statements.

    The failure this catches is the one the requirement exists for: a store that treats a
    display version as identity, or that keeps only the newest revision, would answer several
    identities with one statement. A text record holds exactly one revision (MIK-R74 rule 3),
    so the three revisions are three records that share nothing but their derived display.
    """

    with fixture.reopen() as store:
        base = store.get_revision(fixture.base_revision_id)
        second = store.get_revision(fixture.second_revision_id)
        third = store.get_revision(fixture.third_revision_id)
    assert base is not None and second is not None and third is not None
    # Display versions are derived from the text revision number (``rN``), never authored.
    assert base.revision.display_version == "r1"
    assert second.revision.display_version == "r1"
    assert third.revision.display_version == "r1"
    assert base.revision.statement == BASE_STATEMENT
    assert second.revision.statement == SECOND_INVARIANT_STATEMENT
    assert third.revision.statement == THIRD_INVARIANT_STATEMENT
    assert (
        len({base.revision.revision_id, second.revision.revision_id, third.revision.revision_id})
        == 3
    )
    # The index holds no predecessor edge (MIK-R74 rule 1): every revision decodes with no
    # predecessors.
    assert (
        base.revision.predecessors
        == second.revision.predecessors
        == ()
        == third.revision.predecessors
    )
    assert base.revision.provenance.actor_ref == "260928-MIK/260928-MIK-L74"
    assert second.revision.provenance.actor_ref == "260928-MIK/260928-MIK-L74"


def test_reopening_the_same_path_keeps_identity_and_schema(
    fixture: BranchingKnowledgeFixture,
) -> None:
    """A reopen resolves the same identities, digests and the one declared schema."""

    with fixture.reopen() as first:
        digests = {
            revision_id: first.get_revision(revision_id).revision.payload_digest  # type: ignore[union-attr]
            for revision_id in (
                fixture.base_revision_id,
                fixture.second_revision_id,
                fixture.third_revision_id,
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
                    "UPDATE invariant_revision SET statement = ? WHERE revision_id = ?",
                    ("written-through-a-reader", fixture.base_revision_id),
                )
    with pytest.raises(KnowledgeStorageError, match="does not exist"):
        open_existing_knowledge_store(fixture.database_path.with_name("absent.db"), "x")
    assert not fixture.database_path.with_name("absent.db").exists()  # an open never creates


def test_stored_revision_rows_refuse_update_and_delete(
    fixture: BranchingKnowledgeFixture,
) -> None:
    """The database itself refuses an in-place rewrite from any code path.

    The corruption is written through a writable handle -- the read handles below cannot write
    by construction -- and the file's own triggers refuse it there.
    """

    tamperer = apsw.Connection(str(fixture.database_path))
    try:
        with pytest.raises(apsw.ConstraintError, match="immutable_revision"):
            tamperer.execute(
                "UPDATE invariant_revision SET statement = ? WHERE revision_id = ?",
                ("A rewrite attempted behind the identity.", fixture.second_revision_id),
            )
        with pytest.raises(apsw.ConstraintError, match="immutable_revision"):
            tamperer.execute(
                "DELETE FROM invariant_revision WHERE revision_id = ?",
                (fixture.second_revision_id,),
            )
    finally:
        tamperer.close()
    with fixture.reopen() as store:
        # The predecessor tables are gone with the schema (MIK-R74 rule 1): there is no
        # predecessor row left to refuse a rewrite of. The revision-row refusals above are the
        # whole immutability surface now.
        after = store.get_revision(fixture.second_revision_id)
    assert after is not None
    assert after.revision.statement == SECOND_INVARIANT_STATEMENT


def test_payload_digest_seals_more_than_the_statement(
    fixture: BranchingKnowledgeFixture,
) -> None:
    """A stored payload altered outside the operation is detected when it is read back.

    The trigger is dropped first because the point of the case is the *second* defence: even a
    database edited by something that bypassed this process must not be served as if the
    revision behind the identity were unchanged.
    """

    substitute = make_authorship(actor_ref="agent:substituted").model_dump_json()
    tamperer = apsw.Connection(str(fixture.database_path))
    try:
        tamperer.execute("DROP TRIGGER invariant_revision_no_update")
        tamperer.execute(
            "UPDATE invariant_revision SET provenance = ? WHERE revision_id = ?",
            (substitute, fixture.second_revision_id),
        )
        row = list(
            next(
                tamperer.execute(
                    "SELECT repository_id, invariant_id, revision_id, display_version, statement, "
                    "applicability, conditions, exclusions, state_at_origin, acceptance_ref, "
                    "provenance, payload_digest FROM invariant_revision WHERE revision_id = ?",
                    (fixture.second_revision_id,),
                )
            )
        )
    finally:
        tamperer.close()
    with pytest.raises(KnowledgeStorageError, match="payload digest"):
        decode_revision_row(row, ())


def test_the_created_file_carries_the_one_declared_schema(tmp_path: Path) -> None:
    """The created database carries the declared schema's own manifest and column order.

    The file is a real derived index: the eight logical tables the projection fills beside the
    ``ix_*`` tables the index builder owns (MIK-R74 rule 1's conforming example -- a newly built
    index file lists the eight logical tables and the ``ix_*`` tables in ``sqlite_schema``).
    """

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
    assert (
        set(declared.tables)
        | {
            "ix_meta",
            "ix_problem",
            "ix_record",
            "ix_entry",
            "ix_member",
            "ix_route",
            "ix_link",
            "ix_history_row",
            "ix_uuid",
        }
        == tables
    )
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
    assert len(CURRENT_GENERATION.tables) == 8
    assert schema.generation_for_name("ar-knowledge-sqlite/v8") is None
    assert not hasattr(schema, "GENERATIONS")  # no registry of openable generations

    edited = dict(CURRENT_GENERATION.columns)
    edited["invariant"] = (*edited["invariant"], "a_new_column")
    changed = replace(CURRENT_GENERATION, columns=edited, fingerprint="")
    changed = replace(changed, fingerprint=schema.structure_fingerprint(changed))
    monkeypatch.setattr(schema, "CURRENT_GENERATION", changed)
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
