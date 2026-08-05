"""L6 closeout coverage tests for task-document helpers."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from pydantic import ValidationError

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.tasks import TaskDocument
from agents_remember.tasks import store as task_store
from agents_remember.tasks.document import StepDisposition
from agents_remember.tasks.leaf_doc import (
    TerminalLeafResolutionError,
    _assert_terminal_path,
    resolve_terminal_leaf_doc,
)
from agents_remember.tasks.master_sync import derived_master_status
from agents_remember.tasks.store import write_task_docs


def _doc(**over: Any) -> TaskDocument:
    base: dict[str, Any] = {
        "id": "T1",
        "slug": "task",
        "title": "Hello",
        "kind": "light",
        "repo": "r",
        "type": "Docs",
        "createdAt": "2026-01-01T00:00",
    }
    base.update(over)
    return TaskDocument.model_validate(base)


class TestStepDispositionAndMasterSync:
    def test_blank_skip_reason_rejected(self) -> None:
        with pytest.raises(ValidationError, match="must not be blank"):
            StepDisposition(reason="   ", recordedAt="2026-01-01T00:00")

    def test_collapse_leaf_step_status(self) -> None:
        leaf = _doc(status="planning", steps=[])
        assert derived_master_status(leaf) == "planning"
        leaf = _doc(
            status="planning",
            steps=[{"id": "S1", "title": "One", "status": "done"}],
        )
        assert derived_master_status(leaf) == "Completed"
        leaf = _doc(
            status="planning",
            steps=[{"id": "S1", "title": "One", "status": "inProgress"}],
        )
        assert derived_master_status(leaf) == "inProgress"
        leaf = _doc(
            status="Completed",
            steps=[{"id": "S1", "title": "One", "status": "pending"}],
        )
        assert derived_master_status(leaf) == "inProgress"


class TestWriteTaskDocs:
    def test_write_success(self, tmp_path: Path) -> None:
        paths = write_task_docs(tmp_path, [_doc()])
        assert paths[0][0].exists() and paths[0][1].exists()

    def test_write_rollback(self, tmp_path: Path) -> None:
        with (
            mock.patch.object(task_store, "atomic_write_text", side_effect=OSError("boom")),
            pytest.raises(OSError),
        ):
            write_task_docs(tmp_path, [_doc()])

    def test_write_rollback_both_failed(self, tmp_path: Path) -> None:
        with (
            mock.patch.object(task_store, "atomic_write_text", side_effect=OSError("boom")),
            mock.patch.object(
                task_store, "_restore_task_doc_batch", side_effect=OSError("rollback")
            ),
            pytest.raises(RuntimeError, match="publication and rollback both failed"),
        ):
            write_task_docs(tmp_path, [_doc()])


class TestTerminalLeafResolution:
    def test_blank_leaf_id(self, tmp_path: Path) -> None:
        with pytest.raises(TerminalLeafResolutionError, match="nonblank leaf id"):
            resolve_terminal_leaf_doc(tmp_path, "  ")

    def test_asserted_path_validation(self, tmp_path: Path) -> None:
        with pytest.raises(TerminalLeafResolutionError, match="direct JSON child"):
            resolve_terminal_leaf_doc(tmp_path, "L1", asserted_path=tmp_path / "sub" / "x.json")
        with pytest.raises(TerminalLeafResolutionError, match="direct JSON child"):
            resolve_terminal_leaf_doc(tmp_path, "L1", asserted_path=tmp_path / "x.md")
        with pytest.raises(TerminalLeafResolutionError, match="does not exist"):
            resolve_terminal_leaf_doc(tmp_path, "L1", asserted_path=tmp_path / "x.json")

    def test_ambiguous_and_mismatch(self, tmp_path: Path) -> None:
        write_task_docs(
            tmp_path,
            [
                _doc(id="L1", slug="a", kind="subTask"),
                _doc(id="L1", slug="b", kind="subTask"),
            ],
        )
        with pytest.raises(TerminalLeafResolutionError, match="ambiguous"):
            resolve_terminal_leaf_doc(tmp_path, "L1")
        asserted = tmp_path / "a.json"
        with pytest.raises(TerminalLeafResolutionError, match="not bound to contract leaf"):
            resolve_terminal_leaf_doc(tmp_path, "OTHER", asserted_path=asserted)

    def test_success_and_unreadable_candidate(self, tmp_path: Path) -> None:
        write_task_docs(tmp_path, [_doc(id="L1", slug="a", kind="subTask")])
        result = resolve_terminal_leaf_doc(tmp_path, "L1")
        assert result is not None and result[0].stem == "a"
        asserted = tmp_path / "a.json"
        other = tmp_path / "other.json"
        with pytest.raises(TerminalLeafResolutionError, match="does not equal contract-bound"):
            _assert_terminal_path(
                asserted, "L1", [(other, _doc(id="L1", slug="b", kind="subTask"))]
            )
        (tmp_path / "bad.json").write_text("{invalid", encoding="utf-8")
        with pytest.raises(TerminalLeafResolutionError, match="cannot read terminal leaf"):
            resolve_terminal_leaf_doc(tmp_path, "bad")
