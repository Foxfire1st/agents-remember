"""Tests for the read-only change-set API (serving/changeset.py, L3 of operations-integration).

``changed_files_with_counts`` is covered over real git repos (modify/add/delete/binary,
plus a committed rename); ``task_changeset`` / ``file_diff`` / ``master_changeset`` are
covered over a code (+ memory) worktree pair driven by a written leaf contract, so the
counts, sidecar pairing, before/after content, and master accumulation are real.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.mcp.config import McpRuntimeConfig, RepositoryScope
from agents_remember.serving import files, scope
from agents_remember.serving.changeset import file_diff, master_changeset, task_changeset
from agents_remember.serving.scope import FileScope
from agents_remember.worktrees.modules.git import changed_files_with_counts
from agents_remember.worktrees.worktree_contract import WorktreeContract, write_contract


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", f"safe.directory={repo.as_posix()}", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@e.invalid")
    _git(repo, "config", "user.name", "T")


def _commit_all(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    result = subprocess.run(
        ["git", "-c", f"safe.directory={repo.as_posix()}", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _by_path(files: list[dict]) -> dict[str, dict]:
    return {f["path"]: f for f in files}


class ChangedCountsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.repo = Path(self._dir.name) / "repo"
        _init_repo(self.repo)
        (self.repo / "a.py").write_text("1\n2\n3\n", encoding="utf-8")
        (self.repo / "del.py").write_text("d\n", encoding="utf-8")
        (self.repo / "img.bin").write_bytes(b"\x00\x01\x02\x03")
        self.base = _commit_all(self.repo, "base")

    def tearDown(self) -> None:
        self._dir.cleanup()

    def test_worktree_counts_modify_add_delete_binary(self) -> None:
        (self.repo / "a.py").write_text("1\n2\n3\n4\n", encoding="utf-8")  # +1
        (self.repo / "del.py").unlink()  # delete
        (self.repo / "new.py").write_text("n\n", encoding="utf-8")  # untracked add
        (self.repo / "img.bin").write_bytes(b"\x00\x01\x02\x03\x04")  # binary modify
        files = _by_path(changed_files_with_counts(self.repo, self.base, None))
        self.assertEqual(
            files["a.py"], {"path": "a.py", "insertions": 1, "deletions": 0, "status": "M"}
        )
        self.assertEqual(files["del.py"]["status"], "D")
        self.assertEqual(files["del.py"]["deletions"], 1)
        self.assertEqual(files["new.py"]["status"], "A")
        self.assertEqual(files["new.py"]["insertions"], 1)
        self.assertIsNone(files["img.bin"]["insertions"])  # binary -> null counts
        self.assertIsNone(files["img.bin"]["deletions"])
        self.assertEqual(files["img.bin"]["status"], "M")

    def test_committed_rename_is_detected(self) -> None:
        (self.repo / "old.py").write_text("line1\nline2\n", encoding="utf-8")
        c1 = _commit_all(self.repo, "add old")
        _git(self.repo, "mv", "old.py", "moved.py")
        c2 = _commit_all(self.repo, "rename")
        files = _by_path(changed_files_with_counts(self.repo, c1, c2))
        self.assertIn("moved.py", files)
        self.assertEqual(files["moved.py"]["status"], "R")
        self.assertNotIn("old.py", files)


def _write_leaf_contract(
    contract_path: Path,
    *,
    coord: Path,
    code: Path,
    code_base: str,
    memory: Path | None = None,
    memory_base: str = "",
    leaf_id: str = "l1",
) -> None:
    external = memory is not None
    contract = WorktreeContract(
        task_id="T",
        task_name="t",
        repo_name="R",
        workflow_kind="light-task",
        memory_mode="external" if external else "disabled",
        coordination_root=coord,
        task_root=coord / "tasks" / "R" / "t",
        contract_path=contract_path,
        task_artifact=coord / "tasks" / "R" / "t" / "task.md",
        worktree_group=contract_path.parent,
        code_repo_path=code,
        code_source_branch="main",
        code_work_branch="work",
        code_base_commit=code_base,
        code_worktree=code,
        memory_repo_path=memory,
        memory_source_branch="main" if external else "",
        memory_work_branch="work" if external else "",
        memory_base_commit=memory_base,
        memory_worktree=memory,
        ledger_path=(memory / "memory.md") if memory is not None else None,
        kind="leaf",
        leaf_id=leaf_id,
        parent_task_name="t",
    )
    write_contract(contract_path, contract)


class TaskChangesetTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)
        self.code = self.tmp / "code"
        _init_repo(self.code)
        (self.code / "pkg").mkdir()
        (self.code / "pkg" / "mod.py").write_text("a\nb\n", encoding="utf-8")
        self.code_base = _commit_all(self.code, "code base")
        (self.code / "pkg" / "mod.py").write_text("a\nb\nc\n", encoding="utf-8")  # +1
        (self.code / "pkg" / "new.py").write_text("n\n", encoding="utf-8")  # untracked add

        self.mem = self.tmp / "mem"
        _init_repo(self.mem)
        ob = self.mem / "onboarding" / "pkg"
        ob.mkdir(parents=True)
        (ob / "mod.py.md").write_text("# sidecar\nbody\n", encoding="utf-8")
        self.mem_base = _commit_all(self.mem, "mem base")
        (ob / "mod.py.md").write_text("# sidecar\nbody\nmore\n", encoding="utf-8")  # +1

        self.contract_path = self.tmp / "enc" / "l1" / "series-contract.md"
        _write_leaf_contract(
            self.contract_path,
            coord=self.tmp / "coord",
            code=self.code,
            code_base=self.code_base,
            memory=self.mem,
            memory_base=self.mem_base,
        )
        self.scope = FileScope(
            scope_id="l1",
            kind="worktree",
            repo_id="R",
            code_root=self.code,
            onboarding_root=self.mem / "onboarding",
            memory_root=self.mem,
            branch="work",
            contract_path=self.contract_path,
        )

    def tearDown(self) -> None:
        self._dir.cleanup()

    def test_task_changeset_code_memory_counts_and_sidecar(self) -> None:
        body = task_changeset(self.scope)
        code = _by_path(body["code"])
        self.assertEqual(code["pkg/mod.py"]["insertions"], 1)
        self.assertTrue(code["pkg/mod.py"]["hasSidecar"])
        self.assertEqual(code["pkg/new.py"]["status"], "A")
        self.assertFalse(code["pkg/new.py"]["hasSidecar"])
        self.assertIn("onboarding/pkg/mod.py.md", _by_path(body["memory"]))
        self.assertEqual(body["counters"]["code"]["files"], 2)
        self.assertEqual(body["counters"]["code"]["insertions"], 2)  # mod +1, new +1
        self.assertEqual(body["counters"]["memory"]["files"], 1)

    def test_file_diff_modified_has_before_and_after(self) -> None:
        body = file_diff(self.scope, "code", "pkg/mod.py")
        self.assertEqual(body["before"]["content"], "a\nb\n")
        self.assertEqual(body["after"]["content"], "a\nb\nc\n")
        self.assertEqual(body["language"], "python")
        self.assertEqual(body["kind"], "code")

    def test_file_diff_added_has_no_before(self) -> None:
        body = file_diff(self.scope, "code", "pkg/new.py")
        self.assertIsNone(body["before"])
        self.assertEqual(body["after"]["content"], "n\n")

    def test_file_diff_deleted_has_no_after(self) -> None:
        (self.code / "pkg" / "mod.py").unlink()
        body = file_diff(self.scope, "code", "pkg/mod.py")
        self.assertEqual(body["before"]["content"], "a\nb\n")
        self.assertIsNone(body["after"])

    def test_file_diff_memory_side(self) -> None:
        body = file_diff(self.scope, "memory", "onboarding/pkg/mod.py.md")
        self.assertEqual(body["kind"], "memory")
        self.assertEqual(body["before"]["content"], "# sidecar\nbody\n")
        self.assertEqual(body["after"]["content"], "# sidecar\nbody\nmore\n")

    def test_mainline_scope_has_no_changeset(self) -> None:
        mainline = FileScope("mainline", "mainline", "R", self.code, None, None, None, None)
        with self.assertRaises(FileNotFoundError):
            task_changeset(mainline)


class MasterChangesetTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)
        self.coord = self.tmp / "coord"
        self.config = McpRuntimeConfig(
            config_path=self.tmp / "settings.json",
            coordination_root=self.coord,
            workspace_root=self.tmp / "ws",
            transcript_root=self.tmp / "logs",
            repositories={"R": RepositoryScope(repo_id="R", path=self.tmp / "ws" / "R")},
        )

    def tearDown(self) -> None:
        self._dir.cleanup()

    def _leaf(self, leaf_id: str, shared_extra: str) -> None:
        code = self.tmp / leaf_id
        _init_repo(code)
        (code / "shared.py").write_text("base\n", encoding="utf-8")
        base = _commit_all(code, "base")
        (code / "shared.py").write_text("base\n" + shared_extra, encoding="utf-8")
        (code / f"{leaf_id}.py").write_text("x\n", encoding="utf-8")  # unique untracked add
        contract_path = (
            self.coord / "tasks" / "R" / "t" / "enclosures" / leaf_id / "series-contract.md"
        )
        _write_leaf_contract(
            contract_path, coord=self.coord, code=code, code_base=base, leaf_id=leaf_id
        )

    def test_accumulates_and_dedups_shared_path(self) -> None:
        self._leaf("l1", "one\n")  # shared.py +1
        self._leaf("l2", "two\nthree\n")  # shared.py +2
        body = master_changeset(self.config, "R", "t")
        code = _by_path(body["code"])
        self.assertEqual(code["shared.py"]["insertions"], 3)  # 1 + 2 summed
        self.assertEqual(code["shared.py"]["leafCount"], 2)  # touched by both leaves
        self.assertIn("l1.py", code)
        self.assertIn("l2.py", code)
        self.assertEqual(body["counters"]["code"]["files"], 3)  # shared + l1 + l2
        self.assertEqual(body["counters"]["code"]["insertions"], 5)  # 3 + 1 + 1
        self.assertEqual({lf["leafId"] for lf in body["leaves"]}, {"l1", "l2"})


class ScopeExtractionTests(unittest.TestCase):
    def test_scope_module_exposes_resolver_and_runner(self) -> None:
        self.assertTrue(callable(scope.resolve_scope))
        self.assertTrue(callable(scope.run_scoped))
        self.assertTrue(callable(scope.language_for))
        # files.py re-exports FileScope + _resolve_within for existing callers (L1 tests).
        self.assertIs(files.FileScope, scope.FileScope)
        self.assertIs(files._resolve_within, scope._resolve_within)


if __name__ == "__main__":
    unittest.main()
