"""Refutations of request-local identity reuse on real SQLite inputs."""

from __future__ import annotations

import os
from contextvars import copy_context
from dataclasses import replace
from pathlib import Path
from shutil import copyfile
from unittest.mock import patch
from uuid import UUID

import apsw
import pytest
from agents_remember.memory.knowledge import dataset_identity_memo as identity_memo
from agents_remember.memory.knowledge import logical
from agents_remember.memory.knowledge.connection import (
    open_read_only_database,
)
from agents_remember.memory.knowledge.dataset_identity_memo import DatasetIdentityMemo
from agents_remember.memory.knowledge.refusals import KnowledgeStorageError
from agents_remember.memory.knowledge.request_digests import same_request_digests
from agents_remember.memory.knowledge.schema import SchemaGeneration
from agents_remember.memory.knowledge_index.projection import INDEX_REPOSITORY_ID
from agents_remember.models.knowledge.authorship import Authorship
from knowledge_rows_test_support import InvariantRequest, open_knowledge_store

GENERATION = SchemaGeneration(
    schema_name="request-equality-test",
    user_version=1,
    tables=("example",),
    columns={"example": ("id", "value")},
    primary_keys={"example": ("id",)},
    json_columns={"example": frozenset()},
    features=(),
    table_ddl={"example": "CREATE TABLE example(id INTEGER PRIMARY KEY, value TEXT)"},
    index_ddl=(),
    triggers={},
    fingerprint="one-test-generation",
)


def database(path: Path, value: str) -> apsw.Connection:
    connection = apsw.Connection(str(path))
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute(GENERATION.table_ddl["example"])
    connection.execute("INSERT INTO example VALUES(1,?)", (value,))
    return connection


def test_equal_endpoint_groups_share_two_scans_and_never_cross_request(tmp_path: Path) -> None:
    writers = [
        database(tmp_path / "before.sqlite", "before"),
        database(tmp_path / "after.sqlite", "after"),
    ]
    readers = [
        open_read_only_database(tmp_path / name) for name in ("before.sqlite", "after.sqlite")
    ]
    try:
        with patch.object(logical, "logical_body", wraps=logical.logical_body) as scan:
            with same_request_digests():
                before = [logical.logical_digest(readers[0], GENERATION) for _ in range(7)]
                after = [logical.logical_digest(readers[1], GENERATION) for _ in range(10)]
                copied = copy_context()
            assert len(set(before)) == len(set(after)) == 1 and before[0] != after[0]
            assert scan.call_count == 2
            assert copied.run(logical.logical_digest, readers[0], GENERATION) == before[0]
            assert scan.call_count == 3  # the copied context retained no completed request answer
            with same_request_digests():
                assert logical.logical_digest(readers[1], GENERATION) == after[0]
            assert scan.call_count == 4
    finally:
        for connection in readers + writers:
            connection.close()


def test_wal_commit_changes_visible_inputs_even_when_main_file_does_not(tmp_path: Path) -> None:
    path = tmp_path / "wal.sqlite"
    writer = database(path, "A")
    reader = open_read_only_database(path)
    try:
        with (
            patch.object(logical, "logical_body", wraps=logical.logical_body) as scan,
            same_request_digests(),
        ):
            a = logical.logical_digest(reader, GENERATION)
            main_bytes = path.read_bytes()
            writer.execute("UPDATE example SET value='B'")
            assert path.read_bytes() == main_bytes
            b = logical.logical_digest(reader, GENERATION)
            assert a != b and scan.call_count == 2
            assert logical.logical_digest(reader, GENERATION) == b and scan.call_count == 2
    finally:
        reader.close()
        writer.close()


def test_identical_endpoint_bytes_still_do_not_share_a_scan(tmp_path: Path) -> None:
    before_path = tmp_path / "before.sqlite"
    after_path = tmp_path / "after.sqlite"
    writer = database(before_path, "same bytes")
    writer.wal_checkpoint()
    copyfile(before_path, after_path)
    readers = [open_read_only_database(path) for path in (before_path, after_path)]
    try:
        assert readers[0].serialize("main") == readers[1].serialize("main")
        with (
            same_request_digests(),
            patch.object(logical, "logical_body", wraps=logical.logical_body) as scan,
        ):
            before = [logical.logical_digest(readers[0], GENERATION) for _ in range(7)]
            after = [logical.logical_digest(readers[1], GENERATION) for _ in range(10)]
            assert len(set(before + after)) == 1
            assert scan.call_count == 2
    finally:
        for connection in readers:
            connection.close()
        writer.close()


def test_change_and_restore_during_scan_never_stores_result_for_old_inputs(tmp_path: Path) -> None:
    path = tmp_path / "aba.sqlite"
    writer = database(path, "A")
    reader = open_read_only_database(path)
    original = logical.logical_body
    calls = 0

    def changed(connection: apsw.Connection, generation: SchemaGeneration | str):
        nonlocal calls
        calls += 1
        if calls == 1:
            writer.execute("UPDATE example SET value='B'")
            body = original(connection, generation)
            writer.execute("UPDATE example SET value='A'")
            return body
        return original(connection, generation)

    try:
        with same_request_digests(), patch.object(logical, "logical_body", side_effect=changed):
            b = logical.logical_digest(reader, GENERATION)
            a = logical.logical_digest(reader, GENERATION)
            assert b != a and calls == 2
            assert logical.logical_digest(reader, GENERATION) == a and calls == 2
    finally:
        reader.close()
        writer.close()


def test_full_custom_generation_is_an_input_not_its_name_or_fingerprint(tmp_path: Path) -> None:
    path = tmp_path / "generation.sqlite"
    writer = database(path, '{"meaning":"recorded"}')
    reader = open_read_only_database(path)
    decoded = replace(GENERATION, json_columns={"example": frozenset({"value"})})
    try:
        with (
            same_request_digests(),
            patch.object(logical, "logical_body", wraps=logical.logical_body) as scan,
        ):
            text = logical.logical_digest(reader, GENERATION)
            json_value = logical.logical_digest(reader, decoded)
            assert text != json_value and scan.call_count == 2
            assert logical.logical_digest(reader, decoded) == json_value and scan.call_count == 2
    finally:
        reader.close()
        writer.close()


def test_nested_requests_and_failed_computations_are_not_shared(tmp_path: Path) -> None:
    path = tmp_path / "nested.sqlite"
    writer = database(path, "A")
    reader = open_read_only_database(path)
    try:
        with (
            same_request_digests(),
            patch.object(logical, "logical_body", wraps=logical.logical_body) as scan,
        ):
            a = logical.logical_digest(reader, GENERATION)
            with same_request_digests():
                assert logical.logical_digest(reader, GENERATION) == a
            assert logical.logical_digest(reader, GENERATION) == a and scan.call_count == 2
        with (
            same_request_digests(),
            patch.object(
                logical,
                "logical_body",
                side_effect=KnowledgeStorageError("original domain refusal"),
            ) as scan,
        ):
            for _ in range(2):
                with pytest.raises(KnowledgeStorageError, match="original domain refusal"):
                    logical.logical_digest(reader, GENERATION)
            assert scan.call_count == 2
    finally:
        reader.close()
        writer.close()


def test_readonly_main_does_not_allow_a_temp_table_to_reuse_main_identity(tmp_path: Path) -> None:
    path = tmp_path / "temp.sqlite"
    writer = database(path, "A")
    reader = open_read_only_database(path)
    try:
        with (
            same_request_digests(),
            patch.object(logical, "logical_body", wraps=logical.logical_body) as scan,
        ):
            a = logical.logical_digest(reader, GENERATION)
            reader.execute("CREATE TEMP TABLE example(id INTEGER PRIMARY KEY, value TEXT)")
            reader.execute("INSERT INTO temp.example VALUES(1,'temporary')")
            b = logical.logical_digest(reader, GENERATION)
            assert a != b and scan.call_count == 2
    finally:
        reader.close()
        writer.close()


_NAMESPACE = "11111111-1111-4111-8111-111111111111"

_AUTHORSHIP = Authorship(
    actor_ref="dataset-identity-test",
    authorization_ref="MIK-R74 rule 3: datasets are built, never staged",
    operation_id=UUID(_NAMESPACE),
    recorded_at="2026-10-25T00:00:00+00:00",
)


def _identity_database(path: Path) -> apsw.Connection:
    """One knowledge dataset, built the one way a dataset is built (MIK-R74 rule 3).

    This fixture used to hand-stage its own rows -- an insert statement of its own writing
    into the repository table and another into the invariant table -- which rule 3 forbids:
    a dataset is produced by writing a text memory tree and calling :func:`build_index`, and
    the logical tables are filled in exactly one place,
    ``memory/knowledge_index/projection.py``. The support builder does precisely that here,
    so the file carries a real repository row and one real invariant row that no test wrote
    itself.
    """
    store = open_knowledge_store(path, INDEX_REPOSITORY_ID)
    try:
        store.create_invariant(
            InvariantRequest(
                repository_id=INDEX_REPOSITORY_ID,
                invariant_id=_NAMESPACE,
                display_label="initial",
                provenance=_AUTHORSHIP,
            )
        )
    finally:
        store.close()
    return apsw.Connection(str(path))


def _relabel(writer: apsw.Connection, label: str) -> None:
    """One external writer's edit of the row the builder wrote, seen only through SQLite.

    The subject of every test below is whether the identity memo observes a change made
    behind its back: a row edit in an open writer, in WAL mode where the main file's
    bytes do not move. The dataset's content is builder-produced (rule 3); this only
    moves an existing row's authored label, which is a mutation for the digest to see,
    not a row the test stages.
    """
    writer.execute("UPDATE invariant SET display_label = ?", (label,))


def test_dataset_identity_retains_one_scan_and_observes_wal_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "identity.sqlite"
    writer = _identity_database(path)
    writer.execute("PRAGMA journal_mode=WAL")
    _relabel(writer, "initial")
    memo = DatasetIdentityMemo()
    monkeypatch.setattr(logical, "DATASET_IDENTITIES", memo)
    try:
        with patch.object(logical, "logical_body", wraps=logical.logical_body) as scans:
            first = logical.dataset_identity(path)
            assert logical.dataset_identity(path) == first
            assert scans.call_count == 1
            before = path.read_bytes()
            _relabel(writer, "one")
            assert path.read_bytes() == before
            second = logical.dataset_identity(path)
            assert second.logical_digest != first.logical_digest
            assert logical.dataset_identity(path) == second
            assert scans.call_count == 2
    finally:
        memo.clear()
        writer.close()


def test_dataset_identity_rejects_corruption_even_when_mtime_is_restored(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "corrupt.sqlite"
    _identity_database(path).close()
    memo = DatasetIdentityMemo()
    monkeypatch.setattr(logical, "DATASET_IDENTITIES", memo)
    try:
        logical.dataset_identity(path)
        before = path.stat()
        data = path.read_bytes()
        path.write_bytes(b"?" * len(data))
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        with pytest.raises((apsw.Error, KnowledgeStorageError)):
            logical.dataset_identity(path)
    finally:
        memo.clear()


def test_dataset_identity_replacement_deletion_and_schema_changes_invalidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "replace.sqlite"
    _identity_database(path).close()
    memo = DatasetIdentityMemo()
    monkeypatch.setattr(logical, "DATASET_IDENTITIES", memo)
    try:
        first = logical.dataset_identity(path)
        replacement = tmp_path / "other.sqlite"
        writer = _identity_database(replacement)
        _relabel(writer, "two")
        writer.close()
        replacement.replace(path)
        assert logical.dataset_identity(path).logical_digest != first.logical_digest
        writer = apsw.Connection(str(path))
        writer.execute(
            "DROP TRIGGER "
            + next(iter(writer.execute("SELECT name FROM sqlite_schema WHERE type='trigger'")))[0]
        )
        writer.close()
        with pytest.raises(KnowledgeStorageError, match="trigger"):
            logical.dataset_identity(path)
        path.unlink()
        with pytest.raises(apsw.Error):
            logical.dataset_identity(path)
    finally:
        memo.clear()


def test_dataset_identity_eviction_closes_retained_readers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    memo = DatasetIdentityMemo(capacity=1)
    monkeypatch.setattr(logical, "DATASET_IDENTITIES", memo)
    paths = [tmp_path / name for name in ("one.sqlite", "two.sqlite")]
    for path in paths:
        _identity_database(path).close()
    try:
        logical.dataset_identity(paths[0])
        first = memo._readers[paths[0]].connection
        assert first is not None and first.readonly("main")
        logical.dataset_identity(paths[1])
        with pytest.raises(apsw.ConnectionClosedError):
            first.execute("SELECT 1")
    finally:
        memo.clear()


@pytest.mark.parametrize("warm", [False, True])
def test_dataset_identity_never_admits_old_open_handle_under_replaced_file_witness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    warm: bool,
) -> None:
    path, replacement = tmp_path / "opened.sqlite", tmp_path / "replacement.sqlite"
    _identity_database(path).close()
    writer = _identity_database(replacement)
    _relabel(writer, "new")
    writer.close()
    memo = DatasetIdentityMemo()
    monkeypatch.setattr(logical, "DATASET_IDENTITIES", memo)
    if warm:
        logical.dataset_identity(path)
    real_generation = identity_memo.generation_of_database

    def swap_after_open(connection: apsw.Connection):
        generation = real_generation(connection)
        if replacement.exists():
            replacement.replace(path)
        return generation

    try:
        with patch.object(identity_memo, "generation_of_database", swap_after_open):
            before = logical.dataset_identity(path)
        after = logical.dataset_identity(path)
        assert before.logical_digest != after.logical_digest
        assert logical.dataset_identity(path) == after
    finally:
        memo.clear()
