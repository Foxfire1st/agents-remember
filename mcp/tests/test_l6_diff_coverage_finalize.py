"""L6 closeout coverage tests for task finalization helpers."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

import pytest

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.tasks import TaskDocument
from agents_remember.worktrees.modules import finalize
from agents_remember.worktrees.modules.finalize import (
    FinalizeArgs,
    FinalizeTaskDocumentError,
    FinalizeTaskTargets,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract


def _doc(**over: Any) -> TaskDocument:
    base: dict[str, Any] = {
        "id": "L1",
        "slug": "leaf",
        "title": "Leaf",
        "kind": "subTask",
        "repo": "agents-remember",
        "type": "Docs",
        "createdAt": "2026-01-01T00:00",
    }
    base.update(over)
    return TaskDocument.model_validate(base)


def _master(**over: Any) -> TaskDocument:
    base: dict[str, Any] = {
        "id": "series",
        "slug": "series",
        "title": "Series",
        "kind": "master",
        "repo": "agents-remember",
        "type": "Master (Code)",
        "createdAt": "2026-01-01T00:00",
        "subTasks": [{"number": "L1", "name": "Leaf", "file": "leaf.json", "status": "inProgress"}],
    }
    base.update(over)
    return TaskDocument.model_validate(base)


def _args(**over: Any) -> FinalizeArgs:
    base: dict[str, Any] = {"contract_path": Path("/c/series-contract.md")}
    base.update(over)
    return FinalizeArgs(**base)


def _contract(task_root: Path, *, kind: str = "leaf") -> WorktreeContract:
    return cast(
        WorktreeContract,
        SimpleNamespace(kind=kind, task_root=task_root, leaf_id="L1"),
    )


class TestResolveTaskTargets:
    def test_series_with_task_doc_asserted(self, tmp_path: Path) -> None:
        with pytest.raises(FinalizeTaskDocumentError, match="cannot assert a leaf"):
            finalize._resolve_task_targets(
                _contract(tmp_path, kind="series"), _args(task_doc_path=tmp_path / "x.json")
            )

    def test_series_delegates(self, tmp_path: Path) -> None:
        with mock.patch.object(
            finalize, "_resolve_parent_target", return_value=FinalizeTaskTargets()
        ) as parent:
            result = finalize._resolve_task_targets(_contract(tmp_path, kind="series"), _args())
        assert parent.called and result.leaf_path is None

    def test_leaf_with_doc(self, tmp_path: Path) -> None:
        leaf = _doc()
        with (
            mock.patch.object(
                finalize,
                "resolve_terminal_leaf_doc",
                return_value=(tmp_path / "leaf.json", leaf),
            ),
            mock.patch.object(
                finalize, "_resolve_parent_target", return_value=FinalizeTaskTargets()
            ),
        ):
            result = finalize._resolve_task_targets(_contract(tmp_path), _args())
        assert result.leaf_path is None  # delegated return

    def test_leaf_without_doc(self, tmp_path: Path) -> None:
        with (
            mock.patch.object(finalize, "resolve_terminal_leaf_doc", return_value=None),
            mock.patch.object(
                finalize, "_resolve_parent_target", return_value=FinalizeTaskTargets()
            ),
        ):
            result = finalize._resolve_task_targets(_contract(tmp_path), _args())
        assert result.leaf_path is None


class TestResolveParentTarget:
    def test_no_leaf_and_no_parent_args(self, tmp_path: Path) -> None:
        result = finalize._resolve_parent_target(_contract(tmp_path), _args(), None, None)
        assert result.leaf_path is None

    def test_no_leaf_with_parent_args(self, tmp_path: Path) -> None:
        with pytest.raises(FinalizeTaskDocumentError, match="without a contract-bound leaf"):
            finalize._resolve_parent_target(
                _contract(tmp_path), _args(subtask_number="L1"), None, None
            )

    def test_standalone_leaf(self, tmp_path: Path) -> None:
        leaf = _doc(master="")
        result = finalize._resolve_parent_target(
            _contract(tmp_path), _args(), tmp_path / "leaf.json", leaf
        )
        assert result.leaf_path == tmp_path / "leaf.json"
        assert result.completed_leaf is not None
        with pytest.raises(FinalizeTaskDocumentError, match="no immediate parent reference"):
            finalize._resolve_parent_target(
                _contract(tmp_path),
                _args(subtask_number="L1"),
                tmp_path / "leaf.json",
                leaf,
            )

    def test_leaf_with_master(self, tmp_path: Path) -> None:
        leaf = _doc(master="task.md")
        parent = _master()
        row = parent.subTasks[0]
        with (
            mock.patch.object(
                finalize, "_expected_parent_path", return_value=tmp_path / "task.json"
            ),
            mock.patch.object(finalize, "_read_parent", return_value=parent),
            mock.patch.object(finalize, "_exact_parent_row", return_value=row),
            mock.patch.object(finalize, "_check_parent_row_path", return_value=None),
            mock.patch.object(finalize, "_parent_completion_candidate", return_value=parent),
        ):
            result = finalize._resolve_parent_target(
                _contract(tmp_path), _args(), tmp_path / "leaf.json", leaf
            )
        assert result.parent_path == tmp_path / "task.json"
        assert result.parent_row is row


class TestAssertAndRead:
    def test_assert_parent_arguments(self, tmp_path: Path) -> None:
        expected = tmp_path / "task.json"
        with pytest.raises(FinalizeTaskDocumentError, match="not the leaf's immediate parent"):
            finalize._assert_parent_arguments(
                _args(master_doc_path=tmp_path / "other.json"), expected, "L1"
            )
        with pytest.raises(FinalizeTaskDocumentError, match="does not identify leaf"):
            finalize._assert_parent_arguments(_args(subtask_number="X"), expected, "L1")
        finalize._assert_parent_arguments(_args(master_doc_path=expected), expected, "L1")

    def test_read_parent(self, tmp_path: Path) -> None:
        path = tmp_path / "parent.json"
        path.write_text("{bad", encoding="utf-8")
        with pytest.raises(FinalizeTaskDocumentError, match="cannot read immediate parent"):
            finalize._read_parent(path)
        path.write_text(_doc().model_dump_json(), encoding="utf-8")
        with pytest.raises(FinalizeTaskDocumentError, match="not a master"):
            finalize._read_parent(path)
        path.write_text(_master().model_dump_json(), encoding="utf-8")
        assert finalize._read_parent(path).kind == "master"

    def test_exact_parent_row(self) -> None:
        parent = _master()
        row = finalize._exact_parent_row(parent, "L1")
        assert row.number == "L1"
        with pytest.raises(FinalizeTaskDocumentError, match="exactly one row"):
            finalize._exact_parent_row(_master(subTasks=[]), "L1")
        with pytest.raises(FinalizeTaskDocumentError, match="exactly one row"):
            finalize._exact_parent_row(
                _master(
                    subTasks=[
                        {"number": "L1", "name": "a", "file": "a.json", "status": "inProgress"},
                        {"number": "L1", "name": "b", "file": "b.json", "status": "inProgress"},
                    ]
                ),
                "L1",
            )

    def test_check_parent_row_path(self, tmp_path: Path) -> None:
        row = _master().subTasks[0]
        row = type(row)(number="L1", name="Leaf", file="", status="inProgress")
        finalize._check_parent_row_path(tmp_path / "task.json", row, tmp_path / "leaf.json")
        row = type(row)(number="L1", name="Leaf", file="other.json", status="inProgress")
        with pytest.raises(FinalizeTaskDocumentError, match="points at"):
            finalize._check_parent_row_path(tmp_path / "task.json", row, tmp_path / "leaf.json")
        row = type(row)(number="L1", name="Leaf", file="leaf.json", status="inProgress")
        finalize._check_parent_row_path(tmp_path / "task.json", row, tmp_path / "leaf.json")

    def test_expected_parent_path(self, tmp_path: Path) -> None:
        leaf = _doc(master="task.md")
        assert finalize._expected_parent_path(tmp_path, leaf) == (tmp_path / "task.json").resolve()
        leaf = _doc(master="../task.md")
        with pytest.raises(FinalizeTaskDocumentError, match="direct child"):
            finalize._expected_parent_path(tmp_path, leaf)


class TestReconcileAndCandidates:
    def test_reconcile_skips(self) -> None:
        updates = finalize._reconcile_task_documents(
            _contract(Path("/root")), FinalizeTaskTargets(), dry_run=True
        )
        assert updates["leaf"]["state"] == "skipped"
        assert updates["parent"]["state"] == "skipped"

    def test_reconcile_missing_candidates(self, tmp_path: Path) -> None:
        leaf = _doc()
        targets = FinalizeTaskTargets(leaf_path=tmp_path / "leaf.json", leaf=leaf)
        with pytest.raises(FinalizeTaskDocumentError, match="leaf completion candidate is missing"):
            finalize._reconcile_task_documents(_contract(tmp_path), targets, dry_run=False)
        targets = FinalizeTaskTargets(
            leaf_path=tmp_path / "leaf.json",
            leaf=leaf,
            completed_leaf=leaf,
            parent_path=tmp_path / "task.json",
            parent=_master(),
            parent_row=_master().subTasks[0],
        )
        with pytest.raises(
            FinalizeTaskDocumentError, match="parent completion candidate is missing"
        ):
            finalize._reconcile_task_documents(_contract(tmp_path), targets, dry_run=False)

    def test_reconcile_writes(self, tmp_path: Path) -> None:
        leaf = _doc()
        targets = FinalizeTaskTargets(
            leaf_path=tmp_path / "leaf.json",
            leaf=leaf,
            completed_leaf=leaf,
            parent_path=tmp_path / "task.json",
            parent=_master(),
            parent_row=_master().subTasks[0],
            completed_parent=_master(),
        )
        with (
            mock.patch.object(
                finalize,
                "publish_queue_bound_task_facts",
                side_effect=lambda _contract, publication, **_kwargs: publication(),
            ),
            mock.patch.object(finalize, "write_task_docs", return_value=[]),
        ):
            updates = finalize._reconcile_task_documents(
                _contract(tmp_path), targets, dry_run=False
            )
        assert updates["leaf"]["state"] == "updated"
        assert updates["parent"]["subtaskNumber"] == "L1"

    def test_candidates(self) -> None:
        leaf = _doc(status="inProgress")
        completed = finalize._leaf_completion_candidate(leaf)
        assert completed.status == "Completed"
        parent = _master()
        completed_parent = finalize._parent_completion_candidate(parent, "L1")
        assert completed_parent.subTasks[0].status == "Completed"
        with pytest.raises(FinalizeTaskDocumentError, match="disappeared before reconciliation"):
            finalize._parent_completion_candidate(parent, "MISSING")
        decisions = finalize._finalized_decisions({"decisions": []})
        assert decisions and decisions[0]["decision"] == "Finalize task lifecycle."
