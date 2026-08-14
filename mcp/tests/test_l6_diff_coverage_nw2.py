"""L6 closeout coverage tests for diff-coverage batch NW2.

Covers the remaining changed lines/branches in citation source-index storage,
citation provenance, terminal preflight validation, harness dispatch, finding
serialization, and operator-inbox consume.
"""

from __future__ import annotations

import sqlite3
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

import pytest
from packaging.version import InvalidVersion

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.application import operator_inbox_tools, terminal_tools
from agents_remember.errors import HarnessControlError
from agents_remember.kernel.primitives.runtime_config import (
    McpRuntimeConfig,
)
from agents_remember.memory_quality.style.citations import (
    extents,
    model,
    provenance,
    source_index_database,
)
from agents_remember.memory_quality.style.citations.provenance import (
    Histories,
    requirement_versions,
)
from agents_remember.memory_quality.style.citations.source_index_database import (
    Database,
    SourceIndexDatabaseError,
    _anchor_key,
    _generation_counters,
)
from agents_remember.memory_quality.style.citations.source_index_state import ReadyGeneration
from agents_remember.memory_quality.style.finding import QualityFinding
from agents_remember.worktrees.modules import terminal_validation
from agents_remember.worktrees.modules.terminal_validation import BranchTarget


def _ready(**over: object) -> ReadyGeneration:
    values: dict[str, object] = {
        "generation_id": "a" * 64,
        "snapshot_id": "b" * 64,
        "code_root": "/code",
        "memory_root": "/memory",
        "files_indexed": 1,
        "source_bytes": 10,
        "database_bytes": 100,
    }
    values.update(over)
    return ReadyGeneration(
        generation_id=cast(str, values["generation_id"]),
        snapshot_id=cast(str, values["snapshot_id"]),
        code_root=cast(str, values["code_root"]),
        memory_root=cast(str, values["memory_root"]),
        files_indexed=cast(int, values["files_indexed"]),
        source_bytes=cast(int, values["source_bytes"]),
        database_bytes=cast(int, values["database_bytes"]),
    )


def _target(**over: object) -> BranchTarget:
    base: dict[str, Any] = {
        "key": "code",
        "repo": Path("/repo"),
        "branch": "ar/leaf",
        "source": "ar/base",
        "optional": False,
        "remote": False,
    }
    base.update(over)
    return BranchTarget(**base)


class TestGenerationCounters:
    def test_counter_mismatch_raises(self) -> None:
        with pytest.raises(SourceIndexDatabaseError, match="counters do not match"):
            _generation_counters(
                {"files_indexed": "1", "source_bytes": "10"},
                _ready(files_indexed=2),
            )


class TestDatabaseOpen:
    def test_sqlite_error_closes_and_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "index.sqlite3"
        path.write_bytes(b"not a sqlite database")
        with pytest.raises(SourceIndexDatabaseError, match="database is corrupt"):
            Database.open(
                path,
                readiness=_ready(database_bytes=path.stat().st_size),
                verify_integrity=True,
            )

    def test_quick_check_corrupt_database(self, tmp_path: Path) -> None:
        path = tmp_path / "index.sqlite3"
        database = Database.create(path)
        line = "a uniquely searchable quotation with plenty of filler words here\n"
        for index in range(40):
            database.insert_file(f"file_{index}.py", [line])
        page_size = database.connection.execute("PRAGMA page_size").fetchone()
        assert page_size is not None
        readiness = _ready(files_indexed=40)
        database.write_snapshot(readiness)
        database.close()
        readiness = replace(readiness, database_bytes=path.stat().st_size)
        page_size = int(page_size[0])
        assert path.stat().st_size > page_size * 9
        with path.open("r+b") as handle:
            handle.seek(page_size * 8)
            handle.write(b"\x00" * page_size)
        with pytest.raises(SourceIndexDatabaseError, match="database is corrupt"):
            Database.open(path, readiness=readiness, verify_integrity=True)


class TestInsertFile:
    def test_quote_stream_id_not_assigned(self) -> None:
        connection = SimpleNamespace(
            execute=lambda sql, *args: (
                SimpleNamespace(lastrowid=1)
                if sql.startswith("INSERT INTO files")
                else SimpleNamespace(lastrowid=None)
            ),
            executemany=lambda *args, **kwargs: None,
        )
        database = Database(cast(sqlite3.Connection, connection))
        with pytest.raises(SourceIndexDatabaseError, match="quote stream id was not assigned"):
            database.insert_file("a.py", ["x = 1\n"])


class TestQuoteBuffers:
    def test_short_gram_buffer_flushes_at_threshold(self) -> None:
        connection = SimpleNamespace(
            execute=lambda *args, **kwargs: SimpleNamespace(lastrowid=1),
            executemany=lambda *args, **kwargs: None,
        )
        database = Database(cast(sqlite3.Connection, connection))
        with mock.patch.object(source_index_database, "_POSTING_BUFFER_KEYS", 0):
            database._buffer_short_grams(1, "ab")
        assert not database.short_quote_buffer
        assert database.short_quote_buffer_bytes == 0


class TestDirectQueries:
    def test_anchor_key_collision_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "index.sqlite3"
        database = Database.create(path)
        database.insert_file("a.py", ["x = 1\n"])
        key = _anchor_key(model.SYMBOL, "x")
        database.connection.execute(
            "UPDATE anchor_names SET anchor_kind = ?, anchor_text = ? WHERE anchor_key = ?",
            ("heading", "other", key),
        )
        with pytest.raises(SourceIndexDatabaseError, match="anchor key collision"):
            database._direct((model.Anchor(model.SYMBOL, "x"),))
        database.close()


class TestQuoteQueries:
    def test_empty_normalised_quote_is_skipped(self, tmp_path: Path) -> None:
        path = tmp_path / "index.sqlite3"
        database = Database.create(path)
        anchor = model.Anchor(model.QUOTE, "   ")
        assert database._quotes((anchor,)) == {anchor: ()}
        database.close()

    def test_candidate_stream_without_target_is_skipped(self, tmp_path: Path) -> None:
        path = tmp_path / "index.sqlite3"
        database = Database.create(path)
        database.insert_file("a.py", ["abcx\n"])
        database.insert_file("b.py", ["xbcd\n"])
        anchor = model.Anchor(model.QUOTE, "abcd")
        assert database.locations((anchor,))[anchor] == ()
        database.close()

    def test_quote_files_iterates_every_matching_path(self, tmp_path: Path) -> None:
        path = tmp_path / "index.sqlite3"
        database = Database.create(path)
        database.insert_file("a.py", ["shared quote\n"])
        database.insert_file("b.py", ["shared quote\n"])
        anchor = model.Anchor(model.QUOTE, "shared quote")
        found = database.locations((anchor,))[anchor]
        assert [one.path for one in found] == ["a.py", "b.py"]
        database.close()

    def test_quote_files_skips_path_without_matches(self, tmp_path: Path) -> None:
        path = tmp_path / "index.sqlite3"
        database = Database.create(path)
        anchor = model.Anchor(model.QUOTE, "quoted")
        match = extents.QuoteMatch(start=1, end=1, source_byte_start=0, source_byte_end=6)
        found = database._quote_files(anchor, {"a.py": [], "b.py": [match]})
        assert [one.path for one in found] == ["b.py"]
        database.close()


class TestProvenanceBranches:
    def test_locked_version_npm_parse_error(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        (repo / "dashboard").mkdir(parents=True)
        (repo / "dashboard" / "package-lock.json").write_text("not json", encoding="utf-8")
        read = Histories(repo, repo)._locked_version("missing", "npm", None)
        assert read.version is None
        assert read.error is not None and "invalid" in read.error

    def test_manifest_read_exception(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        (repo / "mcp").mkdir(parents=True)
        (repo / "mcp" / "requirements.txt").write_bytes(b"\xff\xfe")
        read = Histories(repo, repo)._manifest("mcp/requirements.txt", None)
        assert read.text is None
        assert read.error is not None and "could not read" in read.error

    def test_invalid_exact_version_is_permissive(self) -> None:
        with mock.patch.object(provenance, "Version", side_effect=InvalidVersion("boom")):
            exact, permissive = requirement_versions("pkg==1.0.post-\n")
        assert exact == {} and permissive == {"pkg"}


class TestTerminalValidationBranches:
    def test_local_absent_remote_preflight_blocked(self) -> None:
        with mock.patch.object(
            terminal_validation,
            "_remote_branch_preflight",
            return_value={"remote_deleted": False, "reason": "remote-unreachable"},
        ):
            result = terminal_validation._local_absent_remote_preflight(
                _target(remote=True), {"branch": "ar/leaf"}
            )
        assert result["reason"] == "remote: remote-unreachable"

    def test_local_absent_remote_preflight_would_delete(self) -> None:
        with mock.patch.object(
            terminal_validation,
            "_remote_branch_preflight",
            return_value={"remote_deleted": False, "would_delete": True},
        ):
            result = terminal_validation._local_absent_remote_preflight(
                _target(remote=True), {"branch": "ar/leaf"}
            )
        assert result["would_delete"] is True

    def test_provider_blockers_iterates_every_item(self) -> None:
        blockers = terminal_validation._provider_blockers(
            {
                "containers": [
                    {"deleted": False, "reason": "busy-1"},
                    {"deleted": False, "reason": "busy-2"},
                ],
                "networks": [],
            }
        )
        assert blockers == [
            {"provider": "containers[0]", "reason": "busy-1"},
            {"provider": "containers[1]", "reason": "busy-2"},
        ]

    def test_provider_blockers_skips_benign_then_blocks(self) -> None:
        blockers = terminal_validation._provider_blockers(
            {
                "containers": [
                    {"deleted": False, "reason": "already-absent"},
                    {"deleted": False, "reason": "busy-1"},
                ],
                "networks": [],
            }
        )
        assert blockers == [{"provider": "containers[1]", "reason": "busy-1"}]


class TestHarnessDispatch:
    def test_launch_selection_error_refused(self) -> None:
        settings = SimpleNamespace(
            resolved_role_knobs=lambda role, level: SimpleNamespace(
                model="m",
                effort="e",
                harness="codex",
                launch_args=None,
                prompt_keywords=None,
                session_commands=[],
            ),
            harnesses=(),
        )
        config = SimpleNamespace(coordination_root=Path("/x"), workspace_root=Path("/w"))
        with (
            mock.patch.object(terminal_tools, "load_agentic_settings", return_value=settings),
            mock.patch.object(
                terminal_tools,
                "_resolve_spawn_harness",
                return_value=(SimpleNamespace(id="codex"), None),
            ),
            mock.patch.object(
                terminal_tools,
                "resolve_settings_launch",
                side_effect=HarnessControlError("launch selection boom"),
            ),
        ):
            dispatch, refusal = terminal_tools._resolve_harness_dispatch(
                cast(McpRuntimeConfig, config),
                task_document_ref=None,
                level=None,
                env={"AR_SPAWN_ROLE": "worker"},
                which=None,
            )
        assert dispatch is None
        assert refusal is not None
        assert refusal["status"] == "launch-selection-invalid"
        assert "launch selection boom" in str(refusal["detail"])


class TestFindingSerialization:
    def test_report_only_finding_is_serialized(self) -> None:
        finding = QualityFinding(
            check="c",
            path="p",
            line=1,
            severity="warn",
            code="C",
            message="m",
            report_only=True,
        )
        assert finding.to_dict()["reportOnly"] is True


class TestOperatorInboxConsume:
    def test_consume_without_expectation_row(self) -> None:
        """N16: consume is attribution-only -- no expectation lookup rides the call."""
        entry = SimpleNamespace(id="e", state="consumed", consumedAt="2026-08-05T00:00:00+00:00")
        store = SimpleNamespace(consume=lambda *args, **kwargs: (entry, True))
        with (
            mock.patch.object(operator_inbox_tools, "_store", return_value=store),
        ):
            result = operator_inbox_tools.operator_inbox_consume_tool(
                cast(McpRuntimeConfig, SimpleNamespace()),
                entry_id="e",
                consumed_by="root",
                consumed_via="cli",
            )
        assert result["ok"] is True and result["consumedNow"] is True
