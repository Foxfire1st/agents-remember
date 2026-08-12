"""Transitive source-line enforcement from canonical task identity."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.models.worktree import SourceLineageProjection
from agents_remember.tasks import TaskDocument, write_task_doc
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.modules.start import _preflighted_contract, attach_result
from agents_remember.worktrees.source_lineage import (
    lineage_block_payload,
    lineage_refusal,
    parent_source_lineage,
    source_lineage_for_contract,
    source_lineage_for_task,
)
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    LeafIdentity,
    RepoBranchPlan,
    default_contract,
    default_series_contract,
    write_contract,
)


class SourceLineageTests(unittest.TestCase):
    def test_sprint_roles_have_no_single_master_lineage_edge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _fixture(Path(tmp))

            projection = source_lineage_for_task(
                fixture.coordination,
                TaskDocumentRef(repository="repo", path="sprint/task.json"),
            )

            self.assertIsNone(projection)

    def test_leaf_identity_proves_code_and_memory_transitively(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _fixture(Path(tmp), external_memory=True)

            projection = source_lineage_for_task(fixture.coordination, fixture.leaf_ref)

            assert projection is not None
            self.assertEqual(projection.state, "current")
            self.assertEqual(
                [(edge.relation, edge.side) for edge in projection.edges],
                [
                    ("super-to-master", "code"),
                    ("super-to-master", "memory"),
                    ("master-to-leaf", "code"),
                    ("master-to-leaf", "memory"),
                ],
            )
            self.assertIsNone(lineage_refusal(projection))

    def test_super_move_blocks_before_leaf_start_even_with_stale_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _fixture(Path(tmp))
            _commit_on(fixture.code_repo, "super", "super.txt")

            result = _preflighted_contract(
                SimpleNamespace(code_repository_name="repo"),
                fixture.leaf_contract,
                WorktreeArgs(dry_run=True, stale_base_choice="proceed-stale"),
            )

            self.assertIsInstance(result, WorktreeCommandResult)
            result = cast("WorktreeCommandResult", result)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.payload["nextOperation"], "sync_source_lineage")
            lineage = SourceLineageProjection.model_validate(result.payload["source_lineage"])
            self.assertEqual(lineage.state, "blocked")
            self.assertEqual(lineage.edges[0].relation, "super-to-master")
            next_args = cast("dict[str, object]", result.payload["nextArgs"])
            self.assertEqual(
                next_args["contract_path"],
                fixture.master_contract.contract_path.as_posix(),
            )

    def test_master_move_blocks_leaf_dispatch_with_leaf_sync_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _fixture(Path(tmp))
            _commit_on(fixture.code_repo, "master", "master.txt")

            projection = source_lineage_for_task(fixture.coordination, fixture.leaf_ref)

            assert projection is not None
            self.assertEqual(projection.state, "blocked")
            stale = [edge for edge in projection.edges if edge.state != "current"]
            self.assertEqual(
                [(edge.relation, edge.side) for edge in stale], [("master-to-leaf", "code")]
            )
            self.assertEqual(
                projection.recoveries[0].contractPath,
                fixture.leaf_contract.contract_path.as_posix(),
            )

    def test_attach_refuses_before_stale_task_context_is_resumed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _fixture(Path(tmp))
            _commit_on(fixture.code_repo, "super", "super.txt")

            result = attach_result(WorktreeArgs(contract_path=fixture.leaf_contract.contract_path))

            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.payload["state"], "blocked")
            self.assertIn(
                "Attach refused before stale task context",
                cast(str, result.payload["summary"]),
            )

    def test_missing_leaf_contract_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _fixture(Path(tmp))
            fixture.leaf_contract.contract_path.unlink()

            projection = source_lineage_for_task(fixture.coordination, fixture.leaf_ref)

            assert projection is not None
            self.assertEqual(projection.state, "unavailable")
            self.assertEqual(projection.edges[0].relation, "master-to-leaf")
            refusal = lineage_refusal(projection)
            assert refusal is not None
            self.assertEqual(refusal[0], "source-lineage-unavailable")

    def test_missing_master_contract_names_the_super_to_master_edge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _fixture(Path(tmp))
            fixture.master_contract.contract_path.unlink()

            projection = source_lineage_for_task(
                fixture.coordination,
                TaskDocumentRef(repository="repo", path="master/task.json"),
            )

            assert projection is not None
            self.assertEqual(projection.state, "unavailable")
            self.assertEqual(projection.edges[0].relation, "super-to-master")

    def test_malformed_master_contract_fails_closed_for_task_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _fixture(Path(tmp))
            fixture.master_contract.contract_path.write_text("not a contract\n", encoding="utf-8")

            projection = source_lineage_for_task(
                fixture.coordination,
                TaskDocumentRef(repository="repo", path="master/task.json"),
            )

            assert projection is not None
            self.assertEqual(projection.state, "unavailable")

    def test_parent_only_preflight_fails_closed_for_every_missing_parent_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _fixture(Path(tmp))
            self.assertIsNone(parent_source_lineage(fixture.master_contract))

            no_parent = replace(fixture.leaf_contract, parent_contract_path=None)
            self.assertEqual(parent_source_lineage(no_parent).state, "unavailable")  # type: ignore[union-attr]

            absent = replace(
                fixture.leaf_contract,
                parent_contract_path=fixture.coordination / "absent-series-contract.md",
            )
            self.assertEqual(parent_source_lineage(absent).state, "unavailable")  # type: ignore[union-attr]

            fixture.master_contract.contract_path.write_text("invalid\n", encoding="utf-8")
            self.assertEqual(
                parent_source_lineage(fixture.leaf_contract).state,  # type: ignore[union-attr]
                "unavailable",
            )

    def test_contract_resolution_fails_closed_for_non_task_and_missing_parent_shapes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _fixture(Path(tmp))
            other = replace(fixture.leaf_contract, kind="other")
            self.assertIsNone(source_lineage_for_contract(other))

            no_parent = replace(fixture.leaf_contract, parent_contract_path=None)
            self.assertEqual(source_lineage_for_contract(no_parent).state, "unavailable")  # type: ignore[union-attr]

            fixture.master_contract.contract_path.write_text("invalid\n", encoding="utf-8")
            self.assertEqual(
                source_lineage_for_contract(fixture.leaf_contract).state,  # type: ignore[union-attr]
                "unavailable",
            )

    def test_unavailable_lineage_has_no_sync_recovery_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _fixture(Path(tmp))
            projection = source_lineage_for_contract(
                replace(fixture.master_contract, code_repo_path=Path(tmp) / "absent")
            )

            assert projection is not None
            payload = lineage_block_payload(projection)
            self.assertNotIn("nextTool", payload)

    def test_contract_branch_mismatch_and_git_failures_are_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _fixture(Path(tmp))
            cases: tuple[tuple[str, Any], ...] = (
                (
                    "leaf-parent-branch-mismatch",
                    replace(fixture.leaf_contract, code_source_branch="not-master"),
                ),
                (
                    "repository-absent",
                    replace(fixture.master_contract, code_repo_path=Path(tmp) / "absent"),
                ),
                ("branch-name-absent", replace(fixture.master_contract, code_source_branch="")),
                (
                    "branch-ref-absent",
                    replace(fixture.master_contract, code_work_branch="not-a-ref"),
                ),
            )
            for name, contract in cases:
                with self.subTest(name=name):
                    projection = source_lineage_for_contract(contract)
                    assert projection is not None
                    self.assertEqual(projection.state, "unavailable")

            with mock.patch(
                "agents_remember.worktrees.source_lineage.ahead_behind", return_value=None
            ):
                projection = source_lineage_for_contract(fixture.master_contract)
            assert projection is not None
            self.assertEqual(projection.state, "unavailable")

    def test_diverged_master_reports_divergence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _fixture(Path(tmp))
            _commit_on(fixture.code_repo, "master", "master.txt")
            _commit_on(fixture.code_repo, "super", "super.txt")

            projection = source_lineage_for_contract(fixture.master_contract)

            assert projection is not None
            self.assertEqual(projection.state, "blocked")
            self.assertEqual(projection.edges[0].state, "diverged")
            self.assertEqual((projection.edges[0].ahead, projection.edges[0].behind), (1, 1))


class _Fixture:
    def __init__(
        self,
        coordination: Path,
        code_repo: Path,
        master_contract,
        leaf_contract,
        leaf_ref: TaskDocumentRef,
    ) -> None:
        self.coordination = coordination
        self.code_repo = code_repo
        self.master_contract = master_contract
        self.leaf_contract = leaf_contract
        self.leaf_ref = leaf_ref


def _fixture(root: Path, *, external_memory: bool = False) -> _Fixture:
    coordination = root / "coordination"
    code_repo = _repo(root / "code")
    memory_repo = _repo(root / "memory") if external_memory else None
    task_root = coordination / "tasks" / "repo" / "master"
    _write_task_tree(coordination)
    memory_mode = "external" if external_memory else "disabled"
    memory_plan = (
        RepoBranchPlan(memory_repo, "super", "master", _git(memory_repo, "rev-parse", "super"))
        if memory_repo is not None
        else None
    )
    master = default_series_contract(
        ContractTask("master", "repo", coordination, "light-task", memory_mode),
        code=RepoBranchPlan(code_repo, "super", "master", _git(code_repo, "rev-parse", "super")),
        memory=memory_plan,
        task_root=task_root,
    )
    write_contract(master.contract_path, master)
    leaf_memory = (
        RepoBranchPlan(memory_repo, "master", "leaf", _git(memory_repo, "rev-parse", "master"))
        if memory_repo is not None
        else None
    )
    leaf = default_contract(
        ContractTask(
            "master",
            "repo",
            coordination,
            "light-task",
            memory_mode,
            parent_contract_path=master.contract_path,
        ),
        leaf=LeafIdentity("leaf", leaf_id="leaf-1"),
        code=RepoBranchPlan(code_repo, "master", "leaf", _git(code_repo, "rev-parse", "master")),
        memory=leaf_memory,
    )
    write_contract(leaf.contract_path, leaf)
    return _Fixture(
        coordination,
        code_repo,
        master,
        leaf,
        TaskDocumentRef(repository="repo", path="master/leaf-1.json"),
    )


def _write_task_tree(coordination: Path) -> None:
    task_root = coordination / "tasks" / "repo"
    write_task_doc(
        task_root / "sprint",
        _doc(id="SPRINT", slug="sprint", title="Sprint", kind="master", orchestrates=["master"]),
    )
    write_task_doc(
        task_root / "master",
        _doc(
            id="MASTER",
            slug="master",
            title="Master",
            kind="master",
            subTasks=[
                {"number": "leaf-1", "name": "Leaf", "file": "leaf-1.md", "status": "inProgress"}
            ],
        ),
    )
    write_task_doc(
        task_root / "master",
        _doc(id="leaf-1", slug="leaf-1", title="Leaf", kind="subTask", master="task.md"),
    )


def _doc(**values: object) -> TaskDocument:
    return TaskDocument.model_validate(
        {
            "repo": "repo",
            "createdAt": "2026-08-12T00:00",
            **values,
        }
    )


def _repo(path: Path) -> Path:
    path.mkdir(parents=True)
    _git(path, "init", "-b", "super")
    _git(path, "config", "user.email", "test@example.invalid")
    _git(path, "config", "user.name", "Test")
    (path / "base.txt").write_text("base\n", encoding="utf-8")
    _git(path, "add", "base.txt")
    _git(path, "commit", "-m", "base")
    _git(path, "branch", "master")
    _git(path, "branch", "leaf", "master")
    return path


def _commit_on(repo: Path, branch: str, name: str) -> None:
    _git(repo, "switch", branch)
    (repo / name).write_text(name + "\n", encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "commit", "-m", name)


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


if __name__ == "__main__":
    unittest.main()
