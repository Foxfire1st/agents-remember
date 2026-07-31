"""Tests for the read-only change-set API (serving/changeset.py, L3 of operations-integration).

``changed_files_with_counts`` is covered over real git repos (modify/add/delete/binary,
plus a committed rename); ``task_changeset`` / ``file_diff`` / ``master_changeset`` are
covered over a code (+ memory) worktree pair driven by a written leaf contract, so the
counts, sidecar pairing, before/after content, and the master NET series diff are real.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.mcp.config import McpRuntimeConfig, RepositoryScope
from agents_remember.serving import files, scope
from agents_remember.serving.changeset import (
    file_diff,
    leaf_changeset,
    leaf_file_diff,
    master_changeset,
    master_file_diff,
    register_changeset_routes,
    task_changeset,
)
from agents_remember.serving.scope import FileScope
from agents_remember.worktrees.modules.git import changed_files_with_counts
from agents_remember.worktrees.worktree_contract import WorktreeContract, write_contract
from fastapi import FastAPI
from fastapi.testclient import TestClient


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


def _sum_counts(files: list[dict]) -> dict[str, int]:
    return {
        "files": len(files),
        "insertions": sum(int(f["insertions"] or 0) for f in files),
        "deletions": sum(int(f["deletions"] or 0) for f in files),
    }


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
        # The series code repo: main stays at the base while the in-flight series commits live
        # on the work branch. The NET is base -> work tip, not source-branch tip or a sum of
        # per-leaf diffs.
        self.code = self.tmp / "series-code"
        _init_repo(self.code)
        (self.code / "shared.py").write_text("base\n", encoding="utf-8")
        (self.code / "gone.py").write_text("x\n", encoding="utf-8")
        self.base = _commit_all(self.code, "series base")
        _git(self.code, "checkout", "-q", "-b", "work")
        (self.code / "shared.py").write_text("base\nl1\n", encoding="utf-8")  # +1
        (self.code / "added.py").write_text("a\n", encoding="utf-8")
        _commit_all(self.code, "c1")
        (self.code / "shared.py").write_text("base\nl1\nl2\n", encoding="utf-8")  # +1 (net +2)
        (self.code / "gone.py").unlink()
        self.code_work_tip = _commit_all(self.code, "c2")
        _git(self.code, "checkout", "-q", "main")

        self.mem = self.tmp / "series-memory"
        _init_repo(self.mem)
        mem_ob = self.mem / "onboarding" / "pkg"
        mem_ob.mkdir(parents=True)
        (mem_ob / "mod.py.md").write_text("# sidecar\nbase\n", encoding="utf-8")
        self.mem_base = _commit_all(self.mem, "memory base")
        _git(self.mem, "checkout", "-q", "-b", "work")
        (mem_ob / "mod.py.md").write_text("# sidecar\nbase\nwork\n", encoding="utf-8")
        (mem_ob / "new.py.md").write_text("# new\n", encoding="utf-8")
        self.mem_work_tip = _commit_all(self.mem, "memory work")
        _git(self.mem, "checkout", "-q", "main")

        # The master (series) root contract at tasks/<repo>/<master>/series-contract.md.
        self.master_contract = self.coord / "tasks" / "R" / "t" / "series-contract.md"
        write_contract(
            self.master_contract,
            WorktreeContract(
                task_id="T",
                task_name="t",
                repo_name="R",
                workflow_kind="light-task",
                memory_mode="external",
                coordination_root=self.coord,
                task_root=self.coord / "tasks" / "R" / "t",
                contract_path=self.master_contract,
                task_artifact=self.coord / "tasks" / "R" / "t" / "task.md",
                worktree_group=self.master_contract.parent,
                code_repo_path=self.code,
                code_source_branch="main",
                code_work_branch="work",
                code_base_commit=self.base,
                code_worktree=self.code,
                memory_repo_path=self.mem,
                memory_source_branch="main",
                memory_work_branch="work",
                memory_base_commit=self.mem_base,
                memory_worktree=self.mem,
                ledger_path=self.mem / "memory.md",
                kind="series",
            ),
        )
        # A leaf enclosure so the per-leaf breakdown is populated alongside the net diff.
        leaf_code = self.tmp / "l1"
        _init_repo(leaf_code)
        (leaf_code / "f.py").write_text("a\n", encoding="utf-8")
        leaf_base = _commit_all(leaf_code, "leaf base")
        (leaf_code / "f.py").write_text("a\nb\n", encoding="utf-8")  # +1 working
        _write_leaf_contract(
            self.coord / "tasks" / "R" / "t" / "enclosures" / "l1" / "series-contract.md",
            coord=self.coord,
            code=leaf_code,
            code_base=leaf_base,
            leaf_id="l1",
        )

    def tearDown(self) -> None:
        self._dir.cleanup()

    def test_master_net_diff_not_sum(self) -> None:
        body = master_changeset(self.config, "R", "t")
        self.assertEqual(
            body["counters"]["code"],
            _sum_counts(changed_files_with_counts(self.code, self.base, self.code_work_tip)),
        )
        self.assertEqual(
            body["counters"]["memory"],
            _sum_counts(changed_files_with_counts(self.mem, self.mem_base, self.mem_work_tip)),
        )
        code = _by_path(body["code"])
        # shared.py is +2 across the whole base..tip range (one coherent diff), added.py is an
        # add, gone.py a delete -- and net entries carry no per-leaf leafCount.
        self.assertEqual(code["shared.py"]["insertions"], 2)
        self.assertNotIn("leafCount", code["shared.py"])
        self.assertEqual(code["added.py"]["status"], "A")
        self.assertEqual(code["gone.py"]["status"], "D")
        self.assertEqual(body["counters"]["code"]["files"], 3)
        self.assertEqual(body["counters"]["code"]["insertions"], 3)  # shared +2, added +1
        self.assertIn("onboarding/pkg/mod.py.md", _by_path(body["memory"]))

    def test_master_file_diff_base_to_tip(self) -> None:
        self.assertEqual((self.code / "shared.py").read_text(encoding="utf-8"), "base\n")
        body = master_file_diff(self.config, "R", "t", "code", "shared.py")
        self.assertEqual(body["before"]["content"], "base\n")  # at the master base
        self.assertEqual(body["after"]["content"], "base\nl1\nl2\n")  # at the work tip
        self.assertEqual(body["kind"], "code")
        self.assertEqual(body["scope"], "t")
        memory = master_file_diff(self.config, "R", "t", "memory", "onboarding/pkg/mod.py.md")
        self.assertEqual(memory["before"]["content"], "# sidecar\nbase\n")
        self.assertEqual(memory["after"]["content"], "# sidecar\nbase\nwork\n")
        self.assertEqual(memory["kind"], "memory")

    def test_master_falls_back_to_source_tip_when_work_branch_absent(self) -> None:
        _git(self.code, "merge", "--ff-only", "work")
        _git(self.code, "branch", "-D", "work")
        _git(self.mem, "merge", "--ff-only", "work")
        _git(self.mem, "branch", "-D", "work")

        body = master_changeset(self.config, "R", "t")
        self.assertEqual(
            body["counters"]["code"],
            _sum_counts(changed_files_with_counts(self.code, self.base, self.code_work_tip)),
        )
        self.assertEqual(
            body["counters"]["memory"],
            _sum_counts(changed_files_with_counts(self.mem, self.mem_base, self.mem_work_tip)),
        )
        diff = master_file_diff(self.config, "R", "t", "code", "shared.py")
        self.assertEqual(diff["after"]["content"], "base\nl1\nl2\n")

    def test_master_keeps_per_leaf_breakdown(self) -> None:
        body = master_changeset(self.config, "R", "t")
        self.assertEqual({lf["leafId"] for lf in body["leaves"]}, {"l1"})

    def test_master_route_can_skip_per_leaf_breakdown(self) -> None:
        app = FastAPI()
        register_changeset_routes(app, self.config)
        with patch("agents_remember.serving.changeset._master_leaf_summaries") as summaries:
            response = TestClient(app).get(
                "/api/changeset/master",
                params={"repo": "R", "master": "t", "includeLeaves": "false"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["leaves"], [])
        summaries.assert_not_called()

    def test_unknown_master_degrades_to_empty(self) -> None:
        body = master_changeset(self.config, "R", "nope")
        self.assertEqual(body["code"], [])
        self.assertEqual(body["counters"]["code"]["files"], 0)


def _leaf_config(tmp: Path) -> McpRuntimeConfig:
    coord = tmp / "coord"
    return McpRuntimeConfig(
        config_path=tmp / "settings.json",
        coordination_root=coord,
        workspace_root=tmp / "ws",
        transcript_root=tmp / "logs",
        repositories={"R": RepositoryScope(repo_id="R", path=tmp / "ws" / "R")},
    )


def _write_leaf(
    config: McpRuntimeConfig,
    leaf_id: str,
    *,
    code: Path,
    code_base: str,
    code_worktree: Path,
    code_commit: str = "",
    parent_task_name: str = "t",
) -> Path:
    """A leaf enclosure contract under tasks/R/<master>/enclosures/<leaf>/ for the leaf views."""
    path = (
        config.coordination_root
        / "tasks"
        / "R"
        / parent_task_name
        / "enclosures"
        / leaf_id
        / "series-contract.md"
    )
    write_contract(
        path,
        WorktreeContract(
            task_id="T",
            task_name=parent_task_name,
            repo_name="R",
            workflow_kind="light-task",
            memory_mode="disabled",
            coordination_root=config.coordination_root,
            task_root=config.coordination_root / "tasks" / "R" / parent_task_name,
            contract_path=path,
            task_artifact=config.coordination_root / "tasks" / "R" / parent_task_name / "task.md",
            worktree_group=path.parent,
            code_repo_path=code,
            code_source_branch="main",
            code_work_branch="work",
            code_base_commit=code_base,
            code_commit=code_commit,
            code_worktree=code_worktree,
            kind="leaf",
            leaf_id=leaf_id,
            parent_task_name=parent_task_name,
        ),
    )
    return path


class LeafChangesetTests(unittest.TestCase):
    """The L4a leaf views: committed (base -> code_commit) and working (HEAD -> dirty worktree)."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)
        self.config = _leaf_config(self.tmp)

    def tearDown(self) -> None:
        self._dir.cleanup()

    def _committed_repo(self) -> tuple[Path, str, str]:
        code = self.tmp / "code"
        _init_repo(code)
        (code / "f.py").write_text("a\n", encoding="utf-8")
        base = _commit_all(code, "base")
        (code / "f.py").write_text("a\nb\n", encoding="utf-8")  # +1
        (code / "g.py").write_text("g\n", encoding="utf-8")  # add
        commit = _commit_all(code, "leaf work")
        return code, base, commit

    def test_committed_landed_delta_without_live_worktree(self) -> None:
        # A completed/cleaned leaf: the worktree is gone, but the contract's commits live on the repo,
        # so committed still diffs base -> code_commit. The persisted authored id is mixed-case while
        # the dashboard selector is normalized lowercase; both sides must use the same canonicalizer.
        code, base, commit = self._committed_repo()
        _write_leaf(
            self.config,
            "260707-HFX2-L15",
            code=code,
            code_base=base,
            code_commit=commit,
            code_worktree=self.tmp / "gone",
        )
        body = leaf_changeset(self.config, "R", "t", "260707-hfx2-l15", "committed")
        files = _by_path(body["code"])
        self.assertEqual(body["mode"], "committed")
        self.assertEqual(files["f.py"]["insertions"], 1)
        self.assertEqual(files["g.py"]["status"], "A")
        self.assertEqual(body["counters"]["code"]["files"], 2)

    def test_committed_falls_back_to_worktree_head_when_uncommitted(self) -> None:
        # No code_commit yet (in-flight leaf), but a live worktree: committed = base -> worktree HEAD,
        # NOT the dirty tree.
        code = self.tmp / "code"
        _init_repo(code)
        (code / "f.py").write_text("a\n", encoding="utf-8")
        base = _commit_all(code, "base")
        (code / "f.py").write_text("a\nb\n", encoding="utf-8")
        _commit_all(code, "committed in worktree")
        (code / "f.py").write_text("a\nb\nDIRTY\n", encoding="utf-8")  # dirty — must be excluded
        _write_leaf(self.config, "l2", code=code, code_base=base, code_worktree=code)
        files = _by_path(leaf_changeset(self.config, "R", "t", "l2", "committed")["code"])
        self.assertEqual(files["f.py"]["insertions"], 1)  # just the committed "b"

    def test_working_is_uncommitted_delta_only(self) -> None:
        code = self.tmp / "code"
        _init_repo(code)
        (code / "f.py").write_text("a\n", encoding="utf-8")
        base = _commit_all(code, "base")
        (code / "f.py").write_text("a\nb\n", encoding="utf-8")
        commit = _commit_all(code, "committed work")
        (code / "f.py").write_text("a\nb\nc\n", encoding="utf-8")  # dirty +1 over HEAD
        (code / "dirty_new.py").write_text("n\n", encoding="utf-8")  # untracked
        _write_leaf(
            self.config, "l1", code=code, code_base=base, code_commit=commit, code_worktree=code
        )
        body = leaf_changeset(self.config, "R", "t", "l1", "working")
        files = _by_path(body["code"])
        self.assertEqual(body["mode"], "working")
        self.assertEqual(files["f.py"]["insertions"], 1)  # only the uncommitted "c", not "b"
        self.assertEqual(files["dirty_new.py"]["status"], "A")

    def test_working_without_live_worktree_raises(self) -> None:
        code, base, commit = self._committed_repo()
        _write_leaf(
            self.config,
            "l3",
            code=code,
            code_base=base,
            code_commit=commit,
            code_worktree=self.tmp / "gone",
        )
        with self.assertRaises(FileNotFoundError):
            leaf_changeset(self.config, "R", "t", "l3", "working")

    def test_unknown_leaf_raises(self) -> None:
        with self.assertRaises(FileNotFoundError):
            leaf_changeset(self.config, "R", "t", "nope", "committed")

    def test_leaf_is_scoped_by_master(self) -> None:
        code, base, commit = self._committed_repo()
        _write_leaf(
            self.config, "l4", code=code, code_base=base, code_commit=commit, code_worktree=code
        )
        with self.assertRaises(FileNotFoundError):  # right leaf-id, wrong master
            leaf_changeset(self.config, "R", "other-master", "l4", "committed")

    def test_leaf_lookup_does_not_scan_contracts_outside_requested_master(self) -> None:
        code, base, _ = self._committed_repo()
        stray_path = (
            self.config.coordination_root
            / "tasks"
            / "R"
            / "other-master"
            / "enclosures"
            / "stray"
            / "series-contract.md"
        )
        _write_leaf_contract(
            stray_path,
            coord=self.config.coordination_root,
            code=code,
            code_base=base,
            leaf_id="stray",
        )
        with self.assertRaises(FileNotFoundError):
            leaf_changeset(self.config, "R", "t", "stray", "committed")

    def test_leaf_file_diff_committed_base_to_commit(self) -> None:
        code, base, commit = self._committed_repo()
        _write_leaf(
            self.config,
            "l5",
            code=code,
            code_base=base,
            code_commit=commit,
            code_worktree=self.tmp / "gone",
        )
        body = leaf_file_diff(self.config, "R", "t", "l5", "code", "f.py", "committed")
        self.assertEqual(body["before"]["content"], "a\n")
        self.assertEqual(body["after"]["content"], "a\nb\n")
        self.assertEqual(body["language"], "python")

    def test_leaf_file_diff_working_head_to_dirty(self) -> None:
        code = self.tmp / "code"
        _init_repo(code)
        (code / "f.py").write_text("a\n", encoding="utf-8")
        base = _commit_all(code, "base")
        (code / "f.py").write_text("a\nb\n", encoding="utf-8")
        commit = _commit_all(code, "committed")
        (code / "f.py").write_text("a\nb\nc\n", encoding="utf-8")  # dirty
        _write_leaf(
            self.config, "l6", code=code, code_base=base, code_commit=commit, code_worktree=code
        )
        body = leaf_file_diff(self.config, "R", "t", "l6", "code", "f.py", "working")
        self.assertEqual(body["before"]["content"], "a\nb\n")  # at worktree HEAD
        self.assertEqual(body["after"]["content"], "a\nb\nc\n")  # the dirty worktree


class LeafChangesetRouteTests(unittest.TestCase):
    """The leaf+mode selector validation on /api/changeset/task (precedence leaf > master > scope)."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)
        self.config = _leaf_config(self.tmp)
        code = self.tmp / "code"
        _init_repo(code)
        (code / "f.py").write_text("a\n", encoding="utf-8")
        base = _commit_all(code, "base")
        (code / "f.py").write_text("a\nb\n", encoding="utf-8")
        commit = _commit_all(code, "work")
        _write_leaf(
            self.config, "ok", code=code, code_base=base, code_commit=commit, code_worktree=code
        )
        _write_leaf(
            self.config,
            "gonewt",
            code=code,
            code_base=base,
            code_commit=commit,
            code_worktree=self.tmp / "gone",
        )
        app = FastAPI()
        register_changeset_routes(app, self.config)
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self._dir.cleanup()

    def test_leaf_without_master_is_400(self) -> None:
        r = self.client.get(
            "/api/changeset/task", params={"repo": "R", "leaf": "ok", "mode": "committed"}
        )
        self.assertEqual(r.status_code, 400)

    def test_leaf_with_bad_mode_is_400(self) -> None:
        r = self.client.get(
            "/api/changeset/task",
            params={"repo": "R", "master": "t", "leaf": "ok", "mode": "bogus"},
        )
        self.assertEqual(r.status_code, 400)

    def test_leaf_committed_is_200(self) -> None:
        r = self.client.get(
            "/api/changeset/task",
            params={"repo": "R", "master": "t", "leaf": "ok", "mode": "committed"},
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["mode"], "committed")

    def test_working_without_worktree_is_404(self) -> None:
        r = self.client.get(
            "/api/changeset/task",
            params={"repo": "R", "master": "t", "leaf": "gonewt", "mode": "working"},
        )
        self.assertEqual(r.status_code, 404)

    def test_file_diff_leaf_committed_is_200(self) -> None:
        r = self.client.get(
            "/api/changeset/file-diff",
            params={
                "repo": "R",
                "master": "t",
                "leaf": "ok",
                "kind": "code",
                "path": "f.py",
                "mode": "committed",
            },
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["after"]["content"], "a\nb\n")


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
