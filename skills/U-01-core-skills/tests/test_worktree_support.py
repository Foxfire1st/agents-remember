from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from pathlib import Path


CORE_ROOT = Path(__file__).resolve().parents[1]
SHARED_ROOT = CORE_ROOT / "_shared"
sys.path.insert(0, str(SHARED_ROOT))

from agents_remember.memory_ledger import (  # noqa: E402
    LedgerError,
    create_initial_ledger,
    ledger_to_text,
    parse_ledger_text,
    prepend_mapping,
    write_ledger,
)
from agents_remember.worktree_contract import default_contract, load_contract, write_contract  # noqa: E402


RESOLVER_PATH = CORE_ROOT / "C-08-ar-management-resolver" / "scripts" / "ar_management_resolver.py"
RESOLVER_SPEC = importlib.util.spec_from_file_location("ar_management_resolver", RESOLVER_PATH)
assert RESOLVER_SPEC is not None and RESOLVER_SPEC.loader is not None
resolver = importlib.util.module_from_spec(RESOLVER_SPEC)
sys.modules[RESOLVER_SPEC.name] = resolver
RESOLVER_SPEC.loader.exec_module(resolver)

WORKTREE_MANAGER_PATH = CORE_ROOT / "C-09-git-worktree-manager" / "scripts" / "git_worktree_manager.py"
WORKTREE_MANAGER_SPEC = importlib.util.spec_from_file_location("git_worktree_manager", WORKTREE_MANAGER_PATH)
assert WORKTREE_MANAGER_SPEC is not None and WORKTREE_MANAGER_SPEC.loader is not None
worktree_manager = importlib.util.module_from_spec(WORKTREE_MANAGER_SPEC)
sys.modules[WORKTREE_MANAGER_SPEC.name] = worktree_manager
WORKTREE_MANAGER_SPEC.loader.exec_module(worktree_manager)

ADOPT_BASELINE_PATH = CORE_ROOT / "C-10-adopt-memory-baseline" / "scripts" / "adopt_memory_baseline.py"
ADOPT_BASELINE_SPEC = importlib.util.spec_from_file_location("adopt_memory_baseline", ADOPT_BASELINE_PATH)
assert ADOPT_BASELINE_SPEC is not None and ADOPT_BASELINE_SPEC.loader is not None
adopt_baseline = importlib.util.module_from_spec(ADOPT_BASELINE_SPEC)
sys.modules[ADOPT_BASELINE_SPEC.name] = adopt_baseline
ADOPT_BASELINE_SPEC.loader.exec_module(adopt_baseline)


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def init_repo(repo: Path, branch: str = "main") -> str:
    repo.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(["git", "init", "-b", branch], cwd=repo, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if result.returncode != 0:
        git(repo, "init")
        git(repo, "checkout", "-b", branch)
    git(repo, "config", "user.email", "agents-remember-tests@example.invalid")
    git(repo, "config", "user.name", "Agents Remember Tests")
    (repo / "README.md").write_text("# Test Repo\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-m", "Initial commit")
    return git(repo, "rev-parse", "HEAD")


def write_file_onboarding(onboarding_root: Path, repo_name: str, source_path: str, commit_hash: str) -> None:
    path = onboarding_root / f"{source_path}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                f"# {source_path}",
                "",
                "| Field | Value |",
                "| --- | --- |",
                f"| repository | {repo_name} |",
                f"| path | `{source_path}` |",
                "| doc_type | `file-level-onboarding` |",
                f"| lastVerifiedCommitHash | `{commit_hash}` |",
                "| lastVerifiedCommitDate | 2026-05-09T00:00:00+00:00 |",
                "",
            ]
        ),
        encoding="utf-8",
    )


class WorktreeSupportTests(unittest.TestCase):
    def test_memory_ledger_roundtrip_and_prepend(self) -> None:
        ledger = create_initial_ledger("repo-a", "main", "main", "c1", "m1")
        parsed = parse_ledger_text(ledger_to_text(ledger))
        self.assertEqual(parsed.last_verified_code_commit, "c1")
        updated = prepend_mapping(parsed, "c2", "m2")
        reparsed = parse_ledger_text(ledger_to_text(updated))
        self.assertEqual(reparsed.rows[0].code_commit, "c2")
        self.assertEqual(reparsed.last_memory_content_commit, "m2")

    def test_memory_ledger_rejects_bad_top_row(self) -> None:
        text = """# Memory Branch Ledger

```json ar-memory-ledger
{
  "schema": "ar-memory-branch-ledger/v1",
  "repoName": "repo-a",
  "trackedCodeBranch": "main",
  "memoryBranch": "main",
  "baseCodeCommit": "c1",
  "baseMemoryCommit": "m1",
  "lastVerifiedCodeCommit": "c2",
  "lastMemoryContentCommit": "m2",
  "sortOrder": "newest-first"
}
```

| Code commit | Memory commit |
| ----------- | ------------- |
| c1 | m1 |
"""
        with self.assertRaises(LedgerError):
            parse_ledger_text(text)

    def test_memory_ledger_rejects_malformed_metadata(self) -> None:
        text = """# Memory Branch Ledger

```json ar-memory-ledger
{"schema": "ar-memory-branch-ledger/v1",
```

| Code commit | Memory commit |
| ----------- | ------------- |
| c1 | m1 |
"""
        with self.assertRaises(LedgerError):
            parse_ledger_text(text)

    def test_start_blocks_branch_mismatched_memory_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            memory_repo = root / "ar-management" / "memory-repos" / "ar-repo-a"
            memory_repo.mkdir(parents=True)
            write_ledger(memory_repo / "memory.md", create_initial_ledger("repo-a", "dev", "dev", "c1", "m1"))
            contract = default_contract(
                task_name="Fix Thing",
                repo_name="repo-a",
                workflow_kind="light-task",
                memory_mode="shared",
                coordination_root=root / "ar-management",
                code_repo_path=root / "repo-a",
                code_source_branch="main",
                code_work_branch="ar/fix-thing",
                code_base_commit="c1",
                worktree_name="fix-thing",
                memory_repo_path=memory_repo,
                memory_source_branch="main",
                memory_work_branch="ar/fix-thing",
                memory_base_commit="m1",
            )
            result = worktree_manager.prepare_memory_for_start(contract, Namespace(memory_choice=None, dry_run=True))
            self.assertEqual(result["state"], "blocked")
            self.assertIn("branch metadata", result["reason"])

    def test_start_reports_compatible_shared_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            memory_repo = root / "ar-management" / "memory-repos" / "ar-repo-a"
            memory_repo.mkdir(parents=True)
            write_ledger(memory_repo / "memory.md", create_initial_ledger("repo-a", "main", "main", "c1", "m1"))
            contract = default_contract(
                task_name="Fix Thing",
                repo_name="repo-a",
                workflow_kind="light-task",
                memory_mode="shared",
                coordination_root=root / "ar-management",
                code_repo_path=root / "repo-a",
                code_source_branch="main",
                code_work_branch="ar/fix-thing",
                code_base_commit="c1",
                worktree_name="fix-thing",
                memory_repo_path=memory_repo,
                memory_source_branch="main",
                memory_work_branch="ar/fix-thing",
                memory_base_commit="m1",
            )
            result = worktree_manager.prepare_memory_for_start(contract, Namespace(memory_choice=None, dry_run=True))
            self.assertEqual(result["state"], "compatible")
            self.assertEqual(result["worktree"], "would-create")

    def test_start_reports_internal_memory_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = default_contract(
                task_name="Fix Thing",
                repo_name="repo-a",
                workflow_kind="light-task",
                memory_mode="internal",
                coordination_root=root / "ar-management",
                code_repo_path=root / "repo-a",
                code_source_branch="main",
                code_work_branch="ar/fix-thing",
                code_base_commit="c1",
                worktree_name="fix-thing",
            )
            result = worktree_manager.prepare_memory_for_start(contract, Namespace(memory_choice=None, dry_run=True))
            self.assertEqual(result["state"], "internal")

    def test_worktree_contract_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = default_contract(
                task_name="Fix Platform Status",
                repo_name="device-management",
                workflow_kind="light-task",
                memory_mode="shared",
                coordination_root=root / "ar-management",
                code_repo_path=root / "device-management",
                code_source_branch="dev",
                code_work_branch="feature/fix-platform-status",
                code_base_commit="abc123",
                worktree_name="fix-platform-status",
                memory_repo_path=root / "ar-management" / "memory-repos" / "ar-device-management",
                memory_source_branch="dev",
                memory_work_branch="feature/fix-platform-status",
                memory_base_commit="def456",
            )
            write_contract(contract.contract_path, contract)
            loaded = load_contract(contract.contract_path)
            self.assertEqual(loaded.task_root, root / "ar-management" / "tasks" / "device-management" / "fix-platform-status-ar")
            self.assertEqual(loaded.memory_mode, "shared")
            self.assertEqual(loaded.ledger_path, loaded.memory_worktree / "memory.md")

    def test_resolver_internal_defaults_to_ar_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            repo = workspace / "my-app"
            repo.mkdir()
            context = resolver.resolve_management_context(
                repo_name="my-app",
                workspace_root=workspace,
                requested_topology="internal",
            )
            self.assertEqual(context.coordination_root, repo / "ar-management")
            self.assertEqual(context.memory_root, repo / "ar-memory")
            self.assertEqual(context.onboarding_root, repo / "ar-memory" / "onboarding")
            self.assertEqual(context.temp_root, repo / "ar-management" / "temp")

    def test_drift_report_paths_use_temp_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            repo = workspace / "repo-a"
            init_repo(repo, "main")
            coordination_root = workspace / "ar-management"
            temp_root = coordination_root / "temp"
            drift = adopt_baseline.drift
            default_report = drift.resolve_report_path(None, coordination_root, temp_root, repo)
            self.assertEqual(default_report, temp_root / "drift-reports" / "repo-a" / "repo-a_main_drift-report.md")
            self.assertEqual(
                drift.resolve_report_path(Path("custom/report.md"), coordination_root, temp_root, repo),
                temp_root / "custom" / "report.md",
            )
            self.assertEqual(
                drift.resolve_report_path(Path("../tasks/leak.md"), coordination_root, temp_root, repo),
                temp_root / "drift-reports" / "repo-a" / "leak.md",
            )
            inside_coordination = coordination_root / "tasks" / "manual.md"
            self.assertEqual(
                drift.resolve_report_path(inside_coordination, coordination_root, temp_root, repo),
                inside_coordination,
            )
            self.assertEqual(
                drift.resolve_report_path(workspace / "outside.md", coordination_root, temp_root, repo),
                temp_root / "drift-reports" / "repo-a" / "outside.md",
            )

    def test_cross_repo_legacy_string_is_excluded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings_dir = Path(tmp) / "system"
            settings_dir.mkdir(parents=True)
            settings_path = settings_dir / "settings.json"
            settings_path.write_text(
                json.dumps({"version": 2, "crossRepo": {"allow": ["repo-b"]}}),
                encoding="utf-8",
            )
            _storage, cross_repo = resolver.parse_json_settings(settings_path, "internal")
            self.assertEqual(cross_repo.allow[0].state, "excluded")
            self.assertIn("expectedBranch", cross_repo.allow[0].reason)

    def test_cross_repo_v2_code_only_include(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            init_repo(workspace / "repo-b", "main")
            settings = resolver.CrossRepoSettings(
                allow=[resolver.CrossRepoAllowEntry(repo="repo-b", expected_branch="main", include_code=True, include_memory=False)]
            )
            resolved = resolver.resolve_cross_repo_settings(settings, workspace, workspace / "ar-management")
            self.assertEqual(resolved.allow[0].state, "included-code-only")

    def test_cross_repo_v2_memory_include(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            code_head = init_repo(workspace / "repo-b", "main")
            memory_repo = workspace / "ar-management" / "memory-repos" / "ar-repo-b"
            memory_head = init_repo(memory_repo, "main")
            write_ledger(memory_repo / "memory.md", create_initial_ledger("repo-b", "main", "main", code_head, memory_head))
            git(memory_repo, "add", "memory.md")
            git(memory_repo, "commit", "-m", "Add memory ledger")
            settings = resolver.CrossRepoSettings(
                allow=[resolver.CrossRepoAllowEntry(repo="repo-b", expected_branch="main", include_code=True, include_memory=True)]
            )
            resolved = resolver.resolve_cross_repo_settings(settings, workspace, workspace / "ar-management")
            self.assertEqual(resolved.allow[0].state, "included")

    def test_adopt_memory_baseline_status_ready_without_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            code_head = init_repo(workspace / "repo-a", "main")
            memory_root = workspace / "ar-management" / "memory-repos" / "ar-repo-a"
            write_file_onboarding(memory_root / "onboarding", "repo-a", "README.md", code_head)
            args = Namespace(
                repo_name="repo-a",
                workspace_root=workspace,
                topology="shared",
                shared_root=workspace / "ar-management",
                repo=None,
                report=None,
            )
            context = adopt_baseline.resolve_context(args)
            rows, report = adopt_baseline.run_drift(context, None)
            payload = adopt_baseline.base_payload(context, rows, report)
            self.assertEqual(report, workspace / "ar-management" / "temp" / "drift-reports" / "repo-a" / "repo-a_main_drift-report.md")
            self.assertTrue(report.exists())
            self.assertFalse((workspace / "ar-management" / "tasks" / "repo-a" / "repo-a_main_drift-report.md").exists())
            self.assertEqual(payload["state"], "ready")
            self.assertEqual(payload["drift"]["actionable"], 0)
            self.assertFalse(payload["ledger"]["exists"])

    def test_adopt_memory_baseline_blocks_drift_without_acceptance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            repo = workspace / "repo-a"
            code_head = init_repo(repo, "main")
            (repo / "README.md").write_text("# Changed\n", encoding="utf-8")
            git(repo, "add", "README.md")
            git(repo, "commit", "-m", "Change README")
            memory_root = workspace / "ar-management" / "memory-repos" / "ar-repo-a"
            write_file_onboarding(memory_root / "onboarding", "repo-a", "README.md", code_head)
            args = Namespace(
                repo_name="repo-a",
                workspace_root=workspace,
                topology="shared",
                shared_root=workspace / "ar-management",
                repo=None,
                report=None,
                accept_drift=False,
                source_branch=None,
                work_branch=None,
                dry_run=True,
            )
            with redirect_stdout(io.StringIO()):
                self.assertEqual(adopt_baseline.command_adopt(args), 2)

    def test_adopt_memory_baseline_creates_initial_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            code_head = init_repo(workspace / "repo-a", "main")
            memory_root = workspace / "ar-management" / "memory-repos" / "ar-repo-a"
            write_file_onboarding(memory_root / "onboarding", "repo-a", "README.md", code_head)
            args = Namespace(
                repo_name="repo-a",
                workspace_root=workspace,
                topology="shared",
                shared_root=workspace / "ar-management",
                repo=None,
                report=None,
                accept_drift=False,
                source_branch=None,
                work_branch=None,
                dry_run=False,
            )
            with redirect_stdout(io.StringIO()):
                self.assertEqual(adopt_baseline.command_adopt(args), 0)
            ledger = parse_ledger_text((memory_root / "memory.md").read_text(encoding="utf-8"))
            self.assertEqual(ledger.last_verified_code_commit, code_head)
            self.assertTrue((memory_root / "docs" / ".gitkeep").exists())


if __name__ == "__main__":
    unittest.main()
