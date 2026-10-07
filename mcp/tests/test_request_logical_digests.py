"""Refutations of request-local identity reuse on real SQLite inputs."""

from __future__ import annotations

from contextvars import copy_context
from dataclasses import replace
from pathlib import Path
from shutil import copyfile
from unittest.mock import patch

import apsw
import pytest
from agents_remember.memory.knowledge import logical
from agents_remember.memory.knowledge.connection import open_read_only_database
from agents_remember.memory.knowledge.refusals import KnowledgeStorageError
from agents_remember.memory.knowledge.request_digests import same_request_digests
from agents_remember.memory.knowledge.schema_generations import SchemaGeneration

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
