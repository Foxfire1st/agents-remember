"""Tests for the read-only files API (serving/files.py, L1 of operations-integration).

Pure tests build a :class:`FileScope` directly over a temp code+onboarding tree (so
onboarding behaviour is fully controlled); route tests drive ``/api/files/*`` through
the real scope resolver to cover the catalog, the allow-list, the traversal guard, and
the memory-less degrade path.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.errors import AuthorityError
from agents_remember.mcp.config import McpRuntimeConfig, RepositoryScope
from agents_remember.serving import scope as scope_module
from agents_remember.serving.app import create_app
from agents_remember.serving.files import (
    _MAX_FILE_BYTES,
    _REPO_CATALOG_TTL_SECONDS,
    FileScope,
    _resolve_within,
    list_dir,
    list_repos,
    read_file,
    resolve_onboarding,
    resolve_partner,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract, write_contract

_SIDECAR = (
    "# mod\n\n"
    "| Field | Value |\n| --- | --- |\n"
    "| lastVerifiedCommitHash | `abc1234` |\n"
    "| lastVerifiedCommitDate | `2026-06-28` |\n\n"
    "The module body.\n"
)


def _make_repo(code_root: Path, *, with_memory: bool) -> None:
    """A small code tree; one file has a sidecar, one does not."""
    (code_root / "pkg").mkdir(parents=True)
    (code_root / "pkg" / "mod.py").write_text("x = 1\n", encoding="utf-8")
    (code_root / "pkg" / "other.py").write_text("y = 2\n", encoding="utf-8")
    (code_root / "README.md").write_text("# readme\n", encoding="utf-8")
    if with_memory:
        onboarding = code_root / "ar-memory" / "onboarding"
        (onboarding / "pkg").mkdir(parents=True)
        (onboarding / "pkg" / "mod.py.md").write_text(_SIDECAR, encoding="utf-8")
        (onboarding / "overview.md").write_text("# repo overview\n", encoding="utf-8")


def _scope(code_root: Path) -> FileScope:
    return FileScope(
        scope_id="mainline",
        kind="mainline",
        repo_id="R",
        code_root=code_root,
        onboarding_root=code_root / "ar-memory" / "onboarding",
        memory_root=code_root / "ar-memory",
        branch=None,
        contract_path=None,
    )


class PathGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.root = Path(self._dir.name) / "code"
        self.root.mkdir(parents=True)
        (self.root / "pkg").mkdir()
        (self.root / "pkg" / "mod.py").write_text("x = 1\n", encoding="utf-8")

    def tearDown(self) -> None:
        self._dir.cleanup()

    def test_normal_nested_path_resolves(self) -> None:
        self.assertEqual(_resolve_within(self.root, "pkg/mod.py"), (self.root / "pkg" / "mod.py"))

    def test_empty_path_is_the_root(self) -> None:
        self.assertEqual(_resolve_within(self.root, ""), self.root)

    def test_absolute_path_rejected(self) -> None:
        with self.assertRaises(AuthorityError):
            _resolve_within(self.root, "/etc/passwd")

    def test_dotdot_traversal_rejected(self) -> None:
        with self.assertRaises(AuthorityError):
            _resolve_within(self.root, "../../etc/passwd")

    def test_symlink_escape_rejected(self) -> None:
        outside = Path(self._dir.name) / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("s", encoding="utf-8")
        os.symlink(outside, self.root / "link")
        with self.assertRaises(AuthorityError):
            _resolve_within(self.root, "link/secret.txt")


class ListAndReadTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.root = Path(self._dir.name) / "code"
        _make_repo(self.root, with_memory=True)
        self.scope = _scope(self.root)

    def tearDown(self) -> None:
        self._dir.cleanup()

    def test_list_dir_splits_code_and_sidecar_flag(self) -> None:
        body = list_dir(self.scope, "pkg")
        files = {e["name"]: e for e in body["code"] if e["kind"] == "file"}
        self.assertTrue(files["mod.py"]["hasSidecar"])
        self.assertFalse(files["other.py"]["hasSidecar"])
        self.assertEqual(files["mod.py"]["language"], "python")
        # onboarding children for that route come from the parallel onboarding tree
        self.assertIn("mod.py.md", {e["name"] for e in body["onboarding"]})

    def test_list_dir_lazy_one_level(self) -> None:
        body = list_dir(self.scope, "")
        names = {e["name"] for e in body["code"]}
        self.assertEqual(names, {"pkg", "README.md", "ar-memory"})  # no grandchildren

    def test_read_file_text_with_drift_metadata(self) -> None:
        body = read_file(self.scope, "pkg/mod.py")
        self.assertEqual(body["content"], "x = 1\n")
        self.assertEqual(body["language"], "python")
        self.assertFalse(body["truncated"])
        self.assertEqual(body["onboarding"]["status"], "found")
        self.assertEqual(body["onboarding"]["lastVerifiedCommitHash"], "abc1234")

    def test_read_file_missing_sidecar_is_not_an_error(self) -> None:
        body = read_file(self.scope, "pkg/other.py")
        self.assertEqual(body["content"], "y = 2\n")
        self.assertEqual(body["onboarding"]["status"], "missing")

    def test_read_file_oversize_truncates(self) -> None:
        big = self.root / "big.txt"
        big.write_text("a" * (_MAX_FILE_BYTES + 10), encoding="utf-8")
        body = read_file(self.scope, "big.txt")
        self.assertTrue(body["truncated"])
        self.assertEqual(len(body["content"]), _MAX_FILE_BYTES)
        self.assertEqual(body["size"], _MAX_FILE_BYTES + 10)

    def test_read_file_oversize_multibyte_boundary_returns_text_not_binary(self) -> None:
        # A multi-byte UTF-8 char straddling the 2-MiB cap must NOT make an
        # oversize text file misdecode into an empty "binary". "é" is two bytes; placed so its lead
        # byte is the last included byte and its continuation byte falls just past the cap, a naive
        # raw[:cap].decode() would raise -> the cut lands on the codepoint boundary instead.
        big = self.root / "big.txt"
        big.write_text("a" * (_MAX_FILE_BYTES - 1) + "é", encoding="utf-8")
        body = read_file(self.scope, "big.txt")
        self.assertEqual(body["language"], "text")  # NOT "binary"
        self.assertTrue(body["truncated"])
        self.assertNotEqual(body["content"], "")
        self.assertEqual(body["content"], "a" * (_MAX_FILE_BYTES - 1))
        self.assertEqual(body["size"], _MAX_FILE_BYTES + 1)

    def test_read_file_binary_is_marked_not_decoded(self) -> None:
        (self.root / "blob.bin").write_bytes(b"\x00\x01\xff\xfe")
        body = read_file(self.scope, "blob.bin")
        self.assertEqual(body["language"], "binary")
        self.assertEqual(body["content"], "")

    def test_read_file_absent_raises_not_found(self) -> None:
        with self.assertRaises(FileNotFoundError):
            read_file(self.scope, "pkg/ghost.py")


class PairingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.root = Path(self._dir.name) / "code"
        _make_repo(self.root, with_memory=True)
        self.scope = _scope(self.root)

    def tearDown(self) -> None:
        self._dir.cleanup()

    def test_forward_found_returns_sidecar_body(self) -> None:
        body = resolve_onboarding(self.scope, "pkg/mod.py")
        self.assertEqual(body["status"], "found")
        self.assertIn("The module body.", body["body"])

    def test_forward_missing_is_not_an_error(self) -> None:
        body = resolve_onboarding(self.scope, "pkg/other.py")
        self.assertEqual(body["status"], "missing")
        self.assertIsNone(body["body"])

    def test_reverse_sidecar_maps_to_code_partner(self) -> None:
        body = resolve_partner(self.scope, "pkg/mod.py.md")
        self.assertEqual(body["kind"], "sidecar")
        self.assertEqual(body["codePath"], "pkg/mod.py")
        self.assertTrue(body["exists"])

    def test_reverse_orphan_sidecar_reports_missing_partner(self) -> None:
        body = resolve_partner(self.scope, "pkg/ghost.py.md")
        self.assertEqual(body["kind"], "sidecar")
        self.assertFalse(body["exists"])

    def test_reverse_overview_has_no_code_partner(self) -> None:
        body = resolve_partner(self.scope, "overview.md")
        self.assertEqual(body["kind"], "overview")
        self.assertEqual(body["route"], "")
        # A partnerless overview carries its own markdown so the reader can render it (not a placeholder).
        self.assertEqual(body["body"], "# repo overview\n")


class CatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)

    def tearDown(self) -> None:
        self._dir.cleanup()

    def _config(self, repo_id: str = "R") -> McpRuntimeConfig:
        code_root = self.tmp / "ws" / repo_id
        _make_repo(code_root, with_memory=True)
        return McpRuntimeConfig(
            config_path=self.tmp / "settings.json",
            coordination_root=self.tmp / "coord",
            workspace_root=self.tmp / "ws",
            transcript_root=self.tmp / "logs",
            repositories={repo_id: RepositoryScope(repo_id=repo_id, path=code_root)},
        )

    def test_catalog_lists_mainline_per_allowed_repo(self) -> None:
        body = list_repos(self._config("R"))
        repos = {r["repo"]: r for r in body["repos"]}
        self.assertIn("R", repos)
        self.assertEqual(repos["R"]["mainline"]["scope"], "mainline")
        self.assertTrue(repos["R"]["mainline"]["exists"])
        self.assertEqual(repos["R"]["worktrees"], [])

    def test_malformed_enclosure_contract_is_skipped(self) -> None:
        config = self._config("R")
        leaf = config.coordination_root / "tasks" / "R" / "t" / "enclosures" / "l"
        leaf.mkdir(parents=True)
        (leaf / "series-contract.md").write_text("not a valid contract\n", encoding="utf-8")
        body = list_repos(config)  # must not raise
        self.assertEqual({r["repo"] for r in body["repos"]}, {"R"})


def _write_leaf_contract(
    tasks_root: Path,
    *,
    repo: str,
    task: str,
    leaf: str,
    code_worktree: Path,
    cleanup: str = "pending",
) -> None:
    """One leaf-enclosure contract on disk in the shape worktree_start writes it."""
    contract_path = tasks_root / repo / task / "enclosures" / leaf / "series-contract.md"
    write_contract(
        contract_path,
        WorktreeContract(
            task_id=task.upper(),
            task_name=task,
            repo_name=repo,
            workflow_kind="light-task",
            memory_mode="disabled",
            coordination_root=tasks_root.parent,
            task_root=tasks_root / repo / task,
            contract_path=contract_path,
            task_artifact=tasks_root / repo / task / "task.md",
            worktree_group=contract_path.parent,
            code_repo_path=code_worktree,
            code_source_branch="main",
            code_work_branch=f"branch-{leaf}",
            code_base_commit="abc1234",
            code_worktree=code_worktree,
            kind="leaf",
            leaf_id=leaf,
            cleanup=cleanup,
        ),
    )


class CatalogAssemblyTests(unittest.TestCase):
    """list_repos walks+parses ``tasks/`` ONCE and TTL-memos the result."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)

    def tearDown(self) -> None:
        self._dir.cleanup()

    def _config(self, *repo_ids: str) -> McpRuntimeConfig:
        repos = {}
        for repo_id in repo_ids:
            code = self.tmp / "ws" / repo_id
            code.mkdir(parents=True, exist_ok=True)
            repos[repo_id] = RepositoryScope(repo_id=repo_id, path=code)
        return McpRuntimeConfig(
            config_path=self.tmp / "settings.json",
            coordination_root=self.tmp / "coord",
            workspace_root=self.tmp / "ws",
            transcript_root=self.tmp / "logs",
            repositories=repos,
        )

    def _enclosure(self, tasks: Path, repo: str, leaf: str, *, cleanup: str = "pending") -> None:
        worktree = self.tmp / "wt" / repo / leaf
        worktree.mkdir(parents=True, exist_ok=True)
        _write_leaf_contract(
            tasks, repo=repo, task="t", leaf=leaf, code_worktree=worktree, cleanup=cleanup
        )

    def test_response_shape_and_filtering_are_unchanged(self) -> None:
        config = self._config("R1", "R2", "R3")
        tasks = config.coordination_root / "tasks"
        # Created out of order on purpose: per-repo rows must keep sorted-path order.
        self._enclosure(tasks, "R1", "z-leaf")
        self._enclosure(tasks, "R1", "a-leaf")
        self._enclosure(tasks, "R2", "only")
        # Filtered-out population (same semantics as the former per-repo iteration).
        self._enclosure(tasks, "R1", "gone-leaf", cleanup="abandoned")
        _write_leaf_contract(
            tasks,
            repo="R1",
            task="t",
            leaf="ghost",
            code_worktree=self.tmp / "wt-ghost",  # never created: worktree gone
        )
        self._enclosure(tasks, "other-repo", "foreign")  # not in the allow-list
        body = list_repos(config)
        self.assertEqual(
            body,
            {
                "repos": [
                    {
                        "repo": "R1",
                        "mainline": {"scope": "mainline", "label": "R1", "exists": True},
                        "worktrees": [
                            {
                                "scope": "a-leaf",
                                "label": "branch-a-leaf",
                                "branch": "branch-a-leaf",
                                "leafId": "a-leaf",
                                "taskName": "t",
                            },
                            {
                                "scope": "z-leaf",
                                "label": "branch-z-leaf",
                                "branch": "branch-z-leaf",
                                "leafId": "z-leaf",
                                "taskName": "t",
                            },
                        ],
                    },
                    {
                        "repo": "R2",
                        "mainline": {"scope": "mainline", "label": "R2", "exists": True},
                        "worktrees": [
                            {
                                "scope": "only",
                                "label": "branch-only",
                                "branch": "branch-only",
                                "leafId": "only",
                                "taskName": "t",
                            },
                        ],
                    },
                    {
                        "repo": "R3",
                        "mainline": {"scope": "mainline", "label": "R3", "exists": True},
                        "worktrees": [],
                    },
                ]
            },
        )

    def test_single_walk_and_one_parse_per_contract_for_all_repos(self) -> None:
        config = self._config("R1", "R2", "R3")
        tasks = config.coordination_root / "tasks"
        for repo in ("R1", "R2", "R3", "other-repo"):
            for leaf in ("l1", "l2"):
                self._enclosure(tasks, repo, leaf)
        with (
            mock.patch.object(
                scope_module,
                "iter_leaf_enclosure_contracts",
                wraps=scope_module.iter_leaf_enclosure_contracts,
            ) as walk_spy,
            mock.patch.object(
                scope_module, "load_contract", wraps=scope_module.load_contract
            ) as parse_spy,
        ):
            body = list_repos(config)
        self.assertEqual(walk_spy.call_count, 1)  # not one rglob per repo
        self.assertEqual(parse_spy.call_count, 8)  # every contract parsed once, not 3x
        worktrees = {r["repo"]: len(r["worktrees"]) for r in body["repos"]}
        self.assertEqual(worktrees, {"R1": 2, "R2": 2, "R3": 2})

    def test_ttl_memo_hit_then_expiry_rewalks_and_sees_new_enclosures(self) -> None:
        config = self._config("R1")
        tasks = config.coordination_root / "tasks"
        self._enclosure(tasks, "R1", "l1")
        clock = [1000.0]
        with mock.patch.object(
            scope_module,
            "iter_leaf_enclosure_contracts",
            wraps=scope_module.iter_leaf_enclosure_contracts,
        ) as walk_spy:
            first = list_repos(config, now=lambda: clock[0])
            self.assertEqual(walk_spy.call_count, 1)
            # A second enclosure starts right after the first poll.
            self._enclosure(tasks, "R1", "l2")
            repeat = list_repos(config, now=lambda: clock[0])
            self.assertIs(repeat, first)  # StrictMode repeat serves the memo, no re-walk
            self.assertEqual(walk_spy.call_count, 1)
            self.assertNotIn("l2", {w["leafId"] for w in repeat["repos"][0]["worktrees"]})
            # Past the TTL the catalog re-assembles and the new enclosure is visible.
            clock[0] += _REPO_CATALOG_TTL_SECONDS
            refreshed = list_repos(config, now=lambda: clock[0])
            self.assertEqual(walk_spy.call_count, 2)
            self.assertEqual(
                [w["leafId"] for w in refreshed["repos"][0]["worktrees"]], ["l1", "l2"]
            )
            # The memo refresh is sticky: the next repeat does not re-walk either.
            again = list_repos(config, now=lambda: clock[0])
            self.assertIs(again, refreshed)
            self.assertEqual(walk_spy.call_count, 2)


class RouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)

    def tearDown(self) -> None:
        self._dir.cleanup()

    def _client(self, *, with_memory: bool) -> TestClient:
        code_root = self.tmp / "ws" / "R"
        _make_repo(code_root, with_memory=with_memory)
        config = McpRuntimeConfig(
            config_path=self.tmp / "settings.json",
            coordination_root=self.tmp / "coord",
            workspace_root=self.tmp / "ws",
            transcript_root=self.tmp / "logs",
            repositories={"R": RepositoryScope(repo_id="R", path=code_root)},
        )
        return TestClient(create_app(config, interval=100))

    def test_repos_route_does_not_fall_through_to_static(self) -> None:
        with self._client(with_memory=True) as client:
            response = client.get("/api/files/repos")
        self.assertEqual(response.status_code, 200)
        self.assertIn("R", {r["repo"] for r in response.json()["repos"]})

    def test_unknown_repo_is_404(self) -> None:
        with self._client(with_memory=True) as client:
            response = client.get("/api/files/read", params={"repo": "nope", "path": "x"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["status"], "unknown-repo")

    def test_read_serves_code_content(self) -> None:
        with self._client(with_memory=True) as client:
            response = client.get("/api/files/read", params={"repo": "R", "path": "pkg/mod.py"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["content"], "x = 1\n")

    def test_traversal_is_400_bad_path(self) -> None:
        with self._client(with_memory=True) as client:
            response = client.get(
                "/api/files/read", params={"repo": "R", "path": "../../etc/passwd"}
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["status"], "bad-path")

    def test_null_byte_path_is_400_bad_path(self) -> None:
        with self._client(with_memory=True) as client:
            response = client.get("/api/files/read", params={"repo": "R", "path": "pkg/mod\x00.py"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["status"], "bad-path")

    def test_absent_file_is_404_not_found(self) -> None:
        with self._client(with_memory=True) as client:
            response = client.get("/api/files/read", params={"repo": "R", "path": "pkg/ghost.py"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["status"], "not-found")

    def test_list_route_returns_children(self) -> None:
        with self._client(with_memory=True) as client:
            response = client.get("/api/files/list", params={"repo": "R", "path": "pkg"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("mod.py", {e["name"] for e in response.json()["code"]})

    def test_memory_less_repo_still_browses_code(self) -> None:
        # No AR memory -> the scope degrades to code-only; reading code still works,
        # onboarding is uniformly "missing" rather than a 409 that blanks the repo.
        with self._client(with_memory=False) as client:
            response = client.get("/api/files/read", params={"repo": "R", "path": "pkg/mod.py"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["content"], "x = 1\n")
        self.assertEqual(response.json()["onboarding"]["status"], "missing")


if __name__ == "__main__":
    unittest.main()
