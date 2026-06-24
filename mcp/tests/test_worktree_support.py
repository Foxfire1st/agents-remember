from __future__ import annotations

import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from contextlib import contextmanager, redirect_stdout
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest import mock

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.benchmarks import runner as benchmark_runner
from agents_remember.kernel import coordination_context_resolver as resolver
from agents_remember.kernel import filesystem
from agents_remember.kernel.memory_ledger import (
    LedgerError,
    create_initial_ledger,
    find_mapping,
    ledger_to_text,
    load_ledger,
    parse_ledger_text,
    prepend_mapping,
    write_ledger,
)
from agents_remember.mcp.config import load_config
from agents_remember.memory import baseline as adopt_baseline
from agents_remember.memory import carryover as memory_carryover
from agents_remember.providers.identity import provider_instance_id
from agents_remember.tasks import TaskDocument, write_task_doc
from agents_remember.worktrees import git_worktree_manager as worktree_manager
from agents_remember.worktrees.modules import start as worktree_start
from agents_remember.worktrees.modules.integrate import _merge_integrated_commits
from agents_remember.worktrees.modules.models import (
    PATH_SAMPLE_LIMIT,
    OnboardingRefreshPlan,
    RouteOverviewRefreshPlan,
)
from agents_remember.worktrees.modules.onboarding import (
    classify_route_overview_updates,
    require_updated_route_overview_content,
    require_updated_sidecar_content,
)
from agents_remember.worktrees.task_resolver import (
    leaf_enclosure_path,
    series_contract_path,
    task_root_candidates,
)
from agents_remember.worktrees.worktree_contract import (
    default_contract,
    load_contract,
    write_contract,
)

drift = adopt_baseline.drift


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def init_repo(repo: Path, branch: str = "main") -> str:
    repo.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["git", "init", "-b", branch],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        git(repo, "init")
        git(repo, "checkout", "-b", branch)
    git(repo, "config", "user.email", "agents-remember-tests@example.invalid")
    git(repo, "config", "user.name", "Agents Remember Tests")
    (repo / "README.md").write_text("# Test Repo\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-m", "Initial commit")
    return git(repo, "rev-parse", "HEAD")


def write_file_onboarding(
    onboarding_root: Path, repo_name: str, source_path: str, commit_hash: str
) -> None:
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


def write_route_overview(
    onboarding_root: Path, repo_name: str, source_route: str, commit_hash: str
) -> Path:
    path = (
        onboarding_root / source_route / "overview.md"
        if source_route != "."
        else onboarding_root / "overview.md"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                f"# {source_route} Overview",
                "",
                "| Field | Value |",
                "| --- | --- |",
                f"| repository | {repo_name} |",
                "| doc_type | `route-local-overview` |",
                f"| sourceRoute | `{source_route}` |",
                f"| lastVerifiedCommitHash | `{commit_hash}` |",
                "| lastVerifiedCommitDate | 2026-05-09T00:00:00+00:00 |",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


def write_entity_catalog(
    onboarding_root: Path,
    repo_name: str,
    rows: list[tuple[str, str, str, list[str]]],
    inventory_entities: list[str] | None = None,
    include_fingerprints: bool = True,
) -> Path:
    path = onboarding_root / "entities.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    if inventory_entities is None:
        inventory_entities = [entity for entity, _algorithm, _fingerprint, _evidence_paths in rows]
    lines = [
        "# Entities",
        "",
        "| Field | Value |",
        "| --- | --- |",
        f"| repository | {repo_name} |",
        "| doc_type | `repo-entity-catalog` |",
        "| lastUpdated | 2026-05-09T00:00:00+00:00 |",
        "| status | active |",
        "",
    ]
    if include_fingerprints:
        lines.extend(
            [
                "## Entity Fingerprints",
                "",
                "| Entity | Algorithm | Fingerprint | Evidence Paths |",
                "| --- | --- | --- | --- |",
            ]
        )
        for entity, algorithm, fingerprint, evidence_paths in rows:
            evidence = "; ".join(f"`{source_path}`" for source_path in evidence_paths)
            lines.append(f"| {entity} | `{algorithm}` | `{fingerprint}` | {evidence} |")
        lines.append("")
    lines.extend(["## Entity Inventory", ""])
    for entity in inventory_entities:
        lines.extend([f"### {entity}", ""])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def read_onboarding_field(path: Path, field: str) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"| {field} |"):
            return line.split("|", 3)[2].strip().strip("`")
    raise AssertionError(f"{field} was not found in {path}")


def commit_file(repo: Path, path: str, content: str, message: str) -> str:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    git(repo, "add", path)
    git(repo, "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def commit_memory_ledger(
    memory_repo: Path, code_commit: str, memory_content_commit: str, message: str
) -> str:
    ledger_path = memory_repo / "memory.md"
    ledger = parse_ledger_text(ledger_path.read_text(encoding="utf-8"))
    write_ledger(ledger_path, prepend_mapping(ledger, code_commit, memory_content_commit))
    git(memory_repo, "add", "memory.md")
    git(memory_repo, "commit", "-m", message)
    return git(memory_repo, "rev-parse", "HEAD")


def initialized_memory_repo(
    memory_repo: Path, repo_name: str, _code_branch: str, memory_branch: str, code_commit: str
) -> str:
    memory_content = init_repo(memory_repo, memory_branch)
    write_ledger(
        memory_repo / "memory.md", create_initial_ledger(repo_name, code_commit, memory_content)
    )
    git(memory_repo, "add", "memory.md")
    git(memory_repo, "commit", "-m", "Add memory ledger")
    return git(memory_repo, "rev-parse", "HEAD")


def open_external_contract_fixture(root: Path):
    code_repo = root / "repo-a"
    code_base = init_repo(code_repo, "main")
    memory_repo = root / "ar-coordination" / "memory-repos" / "ar-repo-a"
    memory_seed = init_repo(memory_repo, "main")
    write_ledger(memory_repo / "memory.md", create_initial_ledger("repo-a", code_base, memory_seed))
    git(memory_repo, "add", "memory.md")
    git(memory_repo, "commit", "-m", "Add memory ledger")
    memory_base = git(memory_repo, "rev-parse", "HEAD")
    contract = default_contract(
        task_name="Commit Approval Thing",
        repo_name="repo-a",
        workflow_kind="chat",
        memory_mode="external",
        coordination_root=root / "ar-coordination",
        code_repo_path=code_repo,
        code_source_branch="main",
        code_work_branch="ar/commit-approval-thing",
        code_base_commit=code_base,
        worktree_name="commit-approval-thing",
        memory_repo_path=memory_repo,
        memory_source_branch="main",
        memory_work_branch="ar/commit-approval-thing",
        memory_base_commit=memory_base,
    )
    assert contract.memory_worktree is not None
    git(
        code_repo,
        "worktree",
        "add",
        "-b",
        contract.code_work_branch,
        str(contract.code_worktree),
        "main",
    )
    git(
        memory_repo,
        "worktree",
        "add",
        "-b",
        contract.memory_work_branch,
        str(contract.memory_worktree),
        "main",
    )
    write_contract(contract.contract_path, contract)
    return contract


def dirty_open_external_contract_fixture(root: Path):
    contract = open_external_contract_fixture(root)
    (contract.code_worktree / "feature.txt").write_text("feature\n", encoding="utf-8")
    assert contract.memory_worktree is not None
    write_file_onboarding(
        contract.memory_worktree / "onboarding",
        contract.repo_name,
        "feature.txt",
        contract.code_base_commit,
    )
    return contract


def committed_range_external_contract_fixture(root: Path):
    """Open external contract whose changes are already committed on the work branch.

    The feature.txt sidecar exists at the memory baseline so the body gate can
    classify it, feature.txt and raw.txt arrive via work-branch commits
    (raw.txt has no onboarding), and the working tree is clean.
    """
    code_repo = root / "repo-a"
    code_base = init_repo(code_repo, "main")
    memory_repo = root / "ar-coordination" / "memory-repos" / "ar-repo-a"
    memory_seed = init_repo(memory_repo, "main")
    write_ledger(memory_repo / "memory.md", create_initial_ledger("repo-a", code_base, memory_seed))
    write_file_onboarding(memory_repo / "onboarding", "repo-a", "feature.txt", code_base)
    git(memory_repo, "add", "-A")
    git(memory_repo, "commit", "-m", "Add memory baseline")
    memory_base = git(memory_repo, "rev-parse", "HEAD")
    contract = default_contract(
        task_name="Committed Range Thing",
        repo_name="repo-a",
        workflow_kind="chat",
        memory_mode="external",
        coordination_root=root / "ar-coordination",
        code_repo_path=code_repo,
        code_source_branch="main",
        code_work_branch="ar/committed-range-thing",
        code_base_commit=code_base,
        worktree_name="committed-range-thing",
        memory_repo_path=memory_repo,
        memory_source_branch="main",
        memory_work_branch="ar/committed-range-thing",
        memory_base_commit=memory_base,
    )
    assert contract.memory_worktree is not None
    git(
        code_repo,
        "worktree",
        "add",
        "-b",
        contract.code_work_branch,
        str(contract.code_worktree),
        "main",
    )
    git(
        memory_repo,
        "worktree",
        "add",
        "-b",
        contract.memory_work_branch,
        str(contract.memory_worktree),
        "main",
    )
    commit_file(contract.code_worktree, "feature.txt", "feature v2\n", "Add feature")
    commit_file(contract.code_worktree, "raw.txt", "raw\n", "Add raw transport")
    write_contract(contract.contract_path, contract)
    return contract


def long_source_path() -> str:
    segments = [f"segment-{index:02d}" for index in range(30)]
    return "/".join(["mcp", "src", *segments, "deep_file.py"])


@contextmanager
def long_path_tempdir():
    path = Path(tempfile.mkdtemp())
    try:
        yield path
    finally:
        shutil.rmtree(filesystem.extended_path(path), ignore_errors=True)


def closed_external_contract_fixture(
    root: Path, code_path: str = "feature.txt", code_content: str = "feature\n"
):
    code_repo = root / "repo-a"
    code_base = init_repo(code_repo, "main")
    memory_repo = root / "ar-coordination" / "memory-repos" / "ar-repo-a"
    memory_seed = init_repo(memory_repo, "main")
    write_ledger(memory_repo / "memory.md", create_initial_ledger("repo-a", code_base, memory_seed))
    git(memory_repo, "add", "memory.md")
    git(memory_repo, "commit", "-m", "Add memory ledger")
    memory_base = git(memory_repo, "rev-parse", "HEAD")
    contract = default_contract(
        task_name="Integrate Thing",
        repo_name="repo-a",
        workflow_kind="chat",
        memory_mode="external",
        coordination_root=root / "ar-coordination",
        code_repo_path=code_repo,
        code_source_branch="main",
        code_work_branch="ar/integrate-thing",
        code_base_commit=code_base,
        worktree_name="integrate-thing",
        memory_repo_path=memory_repo,
        memory_source_branch="main",
        memory_work_branch="ar/integrate-thing",
        memory_base_commit=memory_base,
    )
    assert contract.memory_worktree is not None
    git(
        code_repo,
        "worktree",
        "add",
        "-b",
        contract.code_work_branch,
        str(contract.code_worktree),
        "main",
    )
    git(
        memory_repo,
        "worktree",
        "add",
        "-b",
        contract.memory_work_branch,
        str(contract.memory_worktree),
        "main",
    )
    code_commit = commit_file(contract.code_worktree, code_path, code_content, "Add feature")
    memory_content_commit = commit_file(
        contract.memory_worktree, "onboarding/feature.txt.md", "# feature\n", "Document feature"
    )
    ledger_commit = commit_memory_ledger(
        contract.memory_worktree, code_commit, memory_content_commit, "Sync ledger"
    )
    closed = replace(
        contract,
        human_review_status="approved",
        approved_for_commit=True,
        closeout_status="completed",
        code_commit=code_commit,
        memory_content_commit=memory_content_commit,
        ledger_commit=ledger_commit,
    )
    write_contract(closed.contract_path, closed)
    return closed


class WorktreeSupportTests(unittest.TestCase):
    def test_master_start_creates_integration_contract_and_leaf_enclosure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            code_repo = workspace / "repo-a"
            init_repo(code_repo, "main")
            coordination_root = workspace / "ar-coordination"
            (coordination_root / "memory-repos" / "ar-repo-a" / "system").mkdir(parents=True)
            (coordination_root / "memory-repos" / "ar-repo-a" / "onboarding").mkdir()
            task_root = coordination_root / "tasks" / "repo-a" / "260624_master"
            write_task_doc(
                task_root,
                TaskDocument.model_validate(
                    {
                        "id": "master",
                        "slug": "task",
                        "title": "Master Series",
                        "kind": "master",
                        "status": "inProgress",
                        "repo": "repo-a",
                        "createdAt": "2026-06-24T02:00",
                        "subTasks": [
                            {
                                "number": "15",
                                "name": "Leaf task",
                                "file": "15_leaf.md",
                                "status": "inProgress",
                            }
                        ],
                    }
                ),
            )

            result = worktree_manager.start_result(
                worktree_manager.WorktreeArgs(
                    code_repository_name="repo-a",
                    workspace_root=workspace,
                    coordination_root=coordination_root,
                    code_repository_root=code_repo,
                    topology="external",
                    task_name="260624_master",
                    worktree_name="15_leaf",
                    leaf_id="15_leaf",
                    workflow_kind="light-task",
                    memory_mode="disabled",
                    skip_provider_setup=True,
                    lifecycle_id="LC-LEAF",
                )
            )

            self.assertEqual(result.returncode, 0)
            root_contract = load_contract(series_contract_path(task_root))
            leaf_contract = load_contract(leaf_enclosure_path(task_root, "15_leaf"))
            self.assertEqual(
                (root_contract.kind, root_contract.code_source_branch), ("series", "main")
            )
            self.assertEqual(root_contract.code_work_branch, "ar/260624_master")
            self.assertEqual(root_contract.code_worktree, code_repo)
            self.assertEqual((leaf_contract.kind, leaf_contract.leaf_id), ("leaf", "15_leaf"))
            self.assertEqual(leaf_contract.code_source_branch, "ar/260624_master")
            self.assertEqual(leaf_contract.code_work_branch, "ar/15_leaf")
            self.assertEqual(leaf_contract.parent_contract_path, root_contract.contract_path)
            self.assertEqual(
                result.payload["enclosure_path"], leaf_contract.contract_path.as_posix()
            )
            self.assertIn(
                "ar/260624_master", git(code_repo, "branch", "--list", "ar/260624_master")
            )
            self.assertIn("ar/15_leaf", git(code_repo, "branch", "--list", "ar/15_leaf"))

    def test_memory_ledger_roundtrip_and_prepend(self) -> None:
        ledger = create_initial_ledger("repo-a", "c1", "m1")
        text = ledger_to_text(ledger)
        self.assertIn("# Memory Ledger", text)
        self.assertNotIn("trackedCodeBranch", text)
        self.assertNotIn("memoryBranch", text)
        parsed = parse_ledger_text(text)
        self.assertEqual(parsed.last_verified_code_commit, "c1")
        updated = prepend_mapping(parsed, "c2", "m2")
        reparsed = parse_ledger_text(ledger_to_text(updated))
        self.assertEqual(reparsed.rows[0].code_commit, "c2")
        self.assertEqual(reparsed.last_memory_content_commit, "m2")

    def test_memory_ledger_rejects_bad_top_row(self) -> None:
        text = "\n".join(
            [
                "# Memory Ledger",
                "",
                "```json ar-memory-ledger",
                "{",
                '  "schema": "ar-memory-ledger/v1",',
                '  "repoName": "repo-a",',
                '  "baseCodeCommit": "c1",',
                '  "baseMemoryCommit": "m1",',
                '  "lastVerifiedCodeCommit": "c2",',
                '  "lastMemoryContentCommit": "m2",',
                '  "sortOrder": "newest-first"',
                "}",
                "```",
                "",
                "| Code commit | Memory commit |",
                "| ----------- | ------------- |",
                "| c1 | m1 |",
            ]
        )
        with self.assertRaises(LedgerError):
            parse_ledger_text(text)

    def test_memory_ledger_rejects_malformed_metadata(self) -> None:
        text = "\n".join(
            [
                "# Memory Ledger",
                "",
                "```json ar-memory-ledger",
                '{"schema": "ar-memory-ledger/v1",',
                "```",
                "",
                "| Code commit | Memory commit |",
                "| ----------- | ------------- |",
                "| c1 | m1 |",
            ]
        )
        with self.assertRaises(LedgerError):
            parse_ledger_text(text)

    def test_resolver_returns_repo_task_root_without_task_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code_repo = root / "repo-a"
            code_repo.mkdir()
            memory_repo = root / "ar-coordination" / "memory-repos" / "ar-repo-a"
            (memory_repo / "system").mkdir(parents=True)
            (memory_repo / "onboarding").mkdir()
            (memory_repo / "system" / "settings.md").write_text("# Settings\n", encoding="utf-8")

            context = resolver.resolve_coordination_context(
                code_repository_root=code_repo,
                requested_topology="external",
                coordination_root=root / "ar-coordination",
            )

            self.assertEqual(context.task_root, root / "ar-coordination" / "tasks" / "repo-a")

    def test_worktree_provider_start_passes_grepai_worktree_memory_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            coordination_root = root / "ar-coordination"
            code_repo = root / "repo-a"
            memory_repo = coordination_root / "memory-repos" / "ar-repo-a"
            settings_path = root / "provider-settings.json"
            code_repo.mkdir(parents=True)
            memory_repo.mkdir(parents=True)
            settings_path.write_text(
                json.dumps(
                    {
                        "contextProviders": {
                            "enabled": True,
                            "providers": {
                                "grepai-memory": {
                                    "enabled": True,
                                    "roots": [
                                        {
                                            "projectId": "repo-a",
                                            "path": memory_repo.as_posix(),
                                        }
                                    ],
                                }
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )
            contract = default_contract(
                task_name="Provider task",
                repo_name="repo-a",
                workflow_kind="light-task",
                memory_mode="external",
                coordination_root=coordination_root,
                code_repo_path=code_repo,
                code_source_branch="main",
                code_work_branch="ar/provider-task",
                code_base_commit="abc123",
                worktree_name="provider-task",
                memory_repo_path=memory_repo,
                memory_source_branch="main",
                memory_work_branch="ar/provider-task",
                memory_base_commit="def456",
            )
            context = Namespace(
                code_repository_name="repo-a",
                code_repository_root=code_repo,
                coordination_root=coordination_root,
                memory_root=memory_repo,
            )
            args = worktree_manager.WorktreeArgs(
                dry_run=True,
                skip_provider_setup=False,
                provider_timeout=1,
                provider_setup_config=worktree_manager.WorktreeProviderSetupConfig(
                    coordination_root=coordination_root,
                    settings_path=settings_path,
                    seed_source_coordination_root=coordination_root,
                ),
            )
            captured: dict[str, Any] = {}

            def fake_run_provider_setup(request):
                captured["request"] = request
                return {"ok": True, "results": []}

            with mock.patch.object(
                worktree_start.provider_setup,
                "run_provider_setup",
                side_effect=fake_run_provider_setup,
            ):
                payload = worktree_manager.prepare_providers_for_start(context, contract, args)

            self.assertEqual(payload["state"], "planned")
            request = captured["request"]
            self.assertEqual(request.skip_grepai, False)
            self.assertEqual(request.grepai_seed.project_id, "repo-a")
            self.assertEqual(request.grepai_seed.source_coordination_root, coordination_root)
            self.assertEqual(request.grepai_seed.target_memory_root, contract.memory_worktree)
            self.assertEqual(
                request.grepai_isolated.runtime_root, contract.worktree_group / "provider-runtime"
            )
            self.assertEqual(request.grepai_isolated.target_memory_root, contract.memory_worktree)

    def test_start_ignores_legacy_ledger_branch_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            memory_repo = root / "ar-coordination" / "memory-repos" / "ar-repo-a"
            memory_repo.mkdir(parents=True)
            (memory_repo / "memory.md").write_text(
                "\n".join(
                    [
                        "# Memory Branch Ledger",
                        "",
                        "```json ar-memory-ledger",
                        "{",
                        '  "schema": "ar-memory-branch-ledger/v1",',
                        '  "repoName": "repo-a",',
                        '  "trackedCodeBranch": "dev",',
                        '  "memoryBranch": "dev",',
                        '  "baseCodeCommit": "c1",',
                        '  "baseMemoryCommit": "m1",',
                        '  "lastVerifiedCodeCommit": "c1",',
                        '  "lastMemoryContentCommit": "m1",',
                        '  "sortOrder": "newest-first"',
                        "}",
                        "```",
                        "",
                        "Newest entries are always inserted at the top.",
                        "",
                        "| Code commit | Memory commit |",
                        "| ----------- | ------------- |",
                        "| c1 | m1 |",
                    ]
                ),
                encoding="utf-8",
            )
            contract = default_contract(
                task_name="Fix Thing",
                repo_name="repo-a",
                workflow_kind="light-task",
                memory_mode="external",
                coordination_root=root / "ar-coordination",
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
            result: dict[str, Any] = worktree_manager.prepare_memory_for_start(
                contract, worktree_manager.WorktreeArgs(memory_choice=None, dry_run=True)
            )
            self.assertEqual(result["state"], "compatible")
            self.assertEqual(result["worktree"], "would-create")

    def test_start_blocks_dirty_external_memory_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            memory_repo = root / "ar-coordination" / "memory-repos" / "ar-repo-a"
            memory_seed = init_repo(memory_repo, "main")
            write_ledger(
                memory_repo / "memory.md", create_initial_ledger("repo-a", "c1", memory_seed)
            )
            git(memory_repo, "add", "memory.md")
            git(memory_repo, "commit", "-m", "Add memory ledger")
            (memory_repo / "onboarding" / "fresh.md").parent.mkdir(parents=True, exist_ok=True)
            (memory_repo / "onboarding" / "fresh.md").write_text("# fresh\n", encoding="utf-8")
            contract = default_contract(
                task_name="Fix Thing",
                repo_name="repo-a",
                workflow_kind="light-task",
                memory_mode="external",
                coordination_root=root / "ar-coordination",
                code_repo_path=root / "repo-a",
                code_source_branch="main",
                code_work_branch="ar/fix-thing",
                code_base_commit="c1",
                worktree_name="fix-thing",
                memory_repo_path=memory_repo,
                memory_source_branch="main",
                memory_work_branch="ar/fix-thing",
                memory_base_commit=memory_seed,
            )
            result: dict[str, Any] = worktree_manager.prepare_memory_for_start(
                contract, worktree_manager.WorktreeArgs(memory_choice=None, dry_run=True)
            )
            self.assertEqual(result["state"], "blocked")
            self.assertIn("commit refreshed onboarding and ledger", result["reason"])
            self.assertIn("commit-memory-and-ledger-first", result["choices"])

    def test_start_reports_compatible_external_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            memory_repo = root / "ar-coordination" / "memory-repos" / "ar-repo-a"
            memory_repo.mkdir(parents=True)
            write_ledger(memory_repo / "memory.md", create_initial_ledger("repo-a", "c1", "m1"))
            contract = default_contract(
                task_name="Fix Thing",
                repo_name="repo-a",
                workflow_kind="light-task",
                memory_mode="external",
                coordination_root=root / "ar-coordination",
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
            result: dict[str, Any] = worktree_manager.prepare_memory_for_start(
                contract, worktree_manager.WorktreeArgs(memory_choice=None, dry_run=True)
            )
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
                coordination_root=root / "ar-coordination",
                code_repo_path=root / "repo-a",
                code_source_branch="main",
                code_work_branch="ar/fix-thing",
                code_base_commit="c1",
                worktree_name="fix-thing",
            )
            result: dict[str, Any] = worktree_manager.prepare_memory_for_start(
                contract, worktree_manager.WorktreeArgs(memory_choice=None, dry_run=True)
            )
            self.assertEqual(result["state"], "internal")

    def test_worktree_contract_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = default_contract(
                task_name="Fix Platform Status",
                repo_name="device-management",
                workflow_kind="light-task",
                memory_mode="external",
                coordination_root=root / "ar-coordination",
                code_repo_path=root / "device-management",
                code_source_branch="dev",
                code_work_branch="feature/fix-platform-status",
                code_base_commit="abc123",
                worktree_name="fix-platform-status",
                memory_repo_path=root / "ar-coordination" / "memory-repos" / "ar-device-management",
                memory_source_branch="dev",
                memory_work_branch="feature/fix-platform-status",
                memory_base_commit="def456",
            )
            write_contract(contract.contract_path, contract)
            loaded = load_contract(contract.contract_path)
            assert loaded.memory_worktree is not None
            self.assertEqual(
                loaded.task_root,
                root / "ar-coordination" / "tasks" / "device-management" / "fix-platform-status",
            )
            self.assertEqual(loaded.task_artifact, loaded.task_root / "task.md")
            self.assertEqual(
                loaded.worktree_group,
                root
                / "ar-coordination"
                / "worktrees"
                / "device-management"
                / "fix-platform-status-ar",
            )
            self.assertEqual(loaded.memory_mode, "external")
            self.assertEqual(loaded.ledger_path, loaded.memory_worktree / "memory.md")
            self.assertEqual(
                task_root_candidates(
                    root / "ar-coordination", "device-management", "Fix Platform Status"
                ),
                [
                    root
                    / "ar-coordination"
                    / "tasks"
                    / "device-management"
                    / "fix-platform-status",
                    root
                    / "ar-coordination"
                    / "tasks"
                    / "device-management"
                    / "fix-platform-status-ar",
                ],
            )
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(
                    worktree_manager.command_status(
                        Namespace(contract_path=contract.contract_path)
                    ),
                    0,
                )
            self.assertEqual(
                json.loads(output.getvalue())["contract_path"], contract.contract_path.as_posix()
            )

    def test_closeout_dry_run_without_approval_reports_commit_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = dirty_open_external_contract_fixture(root)
            args = Namespace(
                contract_path=contract.contract_path,
                approved=False,
                approval_note="",
                code_commit_message="Add feature",
                memory_commit_message="Document feature",
                ledger_commit_message="Sync ledger",
                dry_run=True,
            )
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(worktree_manager.command_closeout(args), 0)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["state"], "would-closeout")
            self.assertEqual(payload["phase"], "commit-approval-pending")
            self.assertEqual(payload["nextOperation"], "request_commit_approval")
            self.assertEqual(payload["nextTool"], "worktree_closeout_apply")
            self.assertEqual(
                payload["nextArgs"]["contract_path"], contract.contract_path.as_posix()
            )
            self.assertNotIn("next_command", payload)
            self.assertTrue(payload["commit_approval_required"])
            self.assertIn(
                "refresh-onboarding-metadata-and-entity-fingerprints", payload["closeout_order"]
            )
            self.assertEqual(payload["changed_code_paths"], {"count": 1, "sample": ["feature.txt"]})
            self.assertEqual(payload["changed_code_paths_committed"], {"count": 0, "sample": []})
            self.assertEqual(payload["onboarding_metadata_refresh"]["missing"], [])
            self.assertEqual(
                payload["onboarding_metadata_refresh"]["required"],
                {"count": 1, "sample": ["feature.txt"]},
            )
            self.assertEqual(
                payload["onboarding_metadata_refresh"]["unonboarded"],
                {"count": 0, "sample": []},
            )
            self.assertEqual(payload["entity_fingerprint_refresh"]["required"], [])
            self.assertTrue(payload["proposed_commits"]["code"]["would_commit"])
            self.assertTrue(
                payload["proposed_commits"]["memory"]["metadata_refresh_after_code_commit"]
            )
            self.assertTrue(
                payload["proposed_commits"]["memory"][
                    "entity_fingerprint_refresh_after_code_commit"
                ]
            )
            self.assertEqual(
                git(contract.code_worktree, "rev-parse", "HEAD"), contract.code_base_commit
            )
            self.assertEqual(load_contract(contract.contract_path).closeout_status, "not-started")

    def test_closeout_plan_uses_memory_worktree_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = dirty_open_external_contract_fixture(root)
            assert contract.memory_worktree is not None
            (contract.memory_worktree / "onboarding" / "feature.txt.md").unlink()
            system_root = contract.memory_worktree / "system"
            system_root.mkdir(parents=True)
            (system_root / "settings.md").write_text("# Settings\n", encoding="utf-8")
            (system_root / "settings.json").write_text(
                json.dumps(
                    {
                        "version": 2,
                        "onboarding": {
                            "storage": {"mode": "memory-repo"},
                            "pathRules": {
                                "include": {"paths": ["feature.txt"], "fileTypes": [".txt"]},
                                "exclude": {"paths": ["feature.txt"], "fileTypes": []},
                            },
                        },
                        "crossRepo": {"allow": []},
                    }
                ),
                encoding="utf-8",
            )

            plan = worktree_manager.onboarding_refresh_plan(contract, ["feature.txt"])

            self.assertEqual(plan["required"], [])
            self.assertEqual(plan["missing"], [])
            self.assertEqual(plan["unsupported"], [])

    def test_changed_worktree_paths_includes_long_files(self) -> None:
        with long_path_tempdir() as root:
            repo = root / "repo-a"
            init_repo(repo, "main")
            git(repo, "config", "core.longpaths", "true")
            source_path = long_source_path()
            source_file = repo / source_path
            filesystem.mkdir(source_file.parent, parents=True, exist_ok=True)
            filesystem.write_text(source_file, "value = 1\n", encoding="utf-8")
            self.assertGreater(len(str(source_file)), 260)

            paths = worktree_manager.changed_worktree_paths(repo)

            self.assertIn(source_path, paths)

    def test_committed_changed_paths_intersects_base_and_verified(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo-a"
            base = init_repo(repo, "main")
            first = commit_file(repo, "a.txt", "a\n", "Add a")
            second = commit_file(repo, "b.txt", "b\n", "Add b")

            self.assertEqual(
                worktree_manager.committed_changed_paths(repo, base, ""), ["a.txt", "b.txt"]
            )
            self.assertEqual(worktree_manager.committed_changed_paths(repo, base, first), ["b.txt"])
            self.assertEqual(worktree_manager.committed_changed_paths(repo, base, second), [])

            git(repo, "rm", "-q", "a.txt")
            git(repo, "commit", "-m", "Delete a")
            self.assertEqual(worktree_manager.committed_changed_paths(repo, base, ""), ["b.txt"])

    def test_onboarding_refresh_plan_detects_long_sidecar_paths(self) -> None:
        with long_path_tempdir() as root:
            source_path = long_source_path()
            onboarding_root = root / "memory" / "onboarding"
            onboarding_file = onboarding_root / f"{source_path}.md"
            filesystem.mkdir(onboarding_file.parent, parents=True, exist_ok=True)
            filesystem.write_text(onboarding_file, "# Long path\n", encoding="utf-8")
            self.assertGreater(len(str(onboarding_file)), 260)
            context = Namespace(
                storage=resolver.StorageSettings(mode="memory-repo", default="memory-repo"),
                code_repository_name="repo-a",
                onboarding_root=onboarding_root,
            )

            plan = worktree_manager.onboarding_refresh_plan_for_context(context, [source_path])

            self.assertEqual(plan["required"][0]["source_path"], source_path)
            self.assertEqual(plan["missing"], [])
            self.assertEqual(plan["unsupported"], [])

    def test_closeout_requires_approval_note_for_real_commits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = dirty_open_external_contract_fixture(root)
            args = Namespace(
                contract_path=contract.contract_path,
                approved=True,
                approval_note="",
                code_commit_message="Add feature",
                memory_commit_message="Document feature",
                ledger_commit_message="Sync ledger",
                dry_run=False,
            )
            with self.assertRaises(RuntimeError):
                worktree_manager.command_closeout(args)
            self.assertTrue(worktree_manager.worktree_dirty(contract.code_worktree))
            self.assertEqual(load_contract(contract.contract_path).closeout_status, "not-started")

    def test_closeout_records_commit_approval_note(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = dirty_open_external_contract_fixture(root)
            args = Namespace(
                contract_path=contract.contract_path,
                approved=True,
                approval_note="developer approved commit preview",
                code_commit_message="Add feature",
                memory_commit_message="Document feature",
                ledger_commit_message="Sync ledger",
                dry_run=False,
            )
            with redirect_stdout(io.StringIO()):
                self.assertEqual(worktree_manager.command_closeout(args), 0)
            loaded = load_contract(contract.contract_path)
            self.assertEqual(loaded.closeout_status, "completed")
            self.assertTrue(loaded.approved_for_commit)
            self.assertEqual(loaded.commit_approval_note, "developer approved commit preview")

    def test_closeout_refreshes_onboarding_metadata_to_new_code_commit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = dirty_open_external_contract_fixture(root)
            assert contract.memory_worktree is not None
            onboarding_file = contract.memory_worktree / "onboarding" / "feature.txt.md"
            args = Namespace(
                contract_path=contract.contract_path,
                approved=True,
                approval_note="developer approved commit preview",
                code_commit_message="Add feature",
                memory_commit_message="Document feature",
                ledger_commit_message="Sync ledger",
                dry_run=False,
            )
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(worktree_manager.command_closeout(args), 0)
            payload = json.loads(output.getvalue())
            loaded = load_contract(contract.contract_path)
            self.assertEqual(
                read_onboarding_field(onboarding_file, "lastVerifiedCommitHash"), loaded.code_commit
            )
            self.assertEqual(
                read_onboarding_field(onboarding_file, "lastVerifiedCommitHash"),
                payload["code_commit"],
            )
            ledger = parse_ledger_text(
                (contract.memory_worktree / "memory.md").read_text(encoding="utf-8")
            )
            self.assertEqual(ledger.last_verified_code_commit, payload["code_commit"])
            self.assertEqual(ledger.last_memory_content_commit, payload["memory_content_commit"])
            self.assertIn(
                "feature.txt.md",
                git(
                    contract.memory_worktree,
                    "show",
                    "--name-only",
                    "--format=",
                    payload["memory_content_commit"],
                ),
            )

    def test_closeout_blocks_missing_onboarding_for_changed_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = open_external_contract_fixture(root)
            (contract.code_worktree / "feature.txt").write_text("feature\n", encoding="utf-8")
            args = Namespace(
                contract_path=contract.contract_path,
                approved=True,
                approval_note="developer approved commit preview",
                code_commit_message="Add feature",
                memory_commit_message="Document feature",
                ledger_commit_message="Sync ledger",
                dry_run=False,
            )
            with self.assertRaisesRegex(
                RuntimeError, "Run the c-05-create-or-update-onboarding-files skill"
            ):
                worktree_manager.command_closeout(args)
            self.assertTrue(worktree_manager.worktree_dirty(contract.code_worktree))
            self.assertEqual(load_contract(contract.contract_path).closeout_status, "not-started")

    def test_closeout_preview_covers_committed_range(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = committed_range_external_contract_fixture(root)
            args = Namespace(
                contract_path=contract.contract_path,
                approved=False,
                approval_note="",
                code_commit_message="Add feature",
                memory_commit_message="Document feature",
                ledger_commit_message="Sync ledger",
                dry_run=True,
            )
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(worktree_manager.command_closeout(args), 0)
            payload = json.loads(output.getvalue())
            self.assertEqual(
                payload["changed_code_paths"],
                {"count": 2, "sample": ["feature.txt", "raw.txt"]},
            )
            self.assertEqual(
                payload["changed_code_paths_committed"],
                {"count": 2, "sample": ["feature.txt", "raw.txt"]},
            )
            self.assertEqual(payload["onboarding_metadata_refresh"]["missing"], [])
            self.assertEqual(
                payload["onboarding_metadata_refresh"]["unonboarded"],
                {"count": 1, "sample": ["raw.txt"]},
            )
            self.assertEqual(
                payload["sidecar_body_gate"]["stale"],
                {"count": 1, "sample": ["feature.txt"]},
            )
            self.assertFalse(payload["proposed_commits"]["code"]["would_commit"])
            self.assertTrue(payload["proposed_commits"]["memory"]["would_commit"])

    def test_closeout_apply_stamps_committed_range_to_existing_head(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = committed_range_external_contract_fixture(root)
            assert contract.memory_worktree is not None
            sidecar = contract.memory_worktree / "onboarding" / "feature.txt.md"
            sidecar.write_text(
                sidecar.read_text(encoding="utf-8")
                + "\nDocumented the transported change.\n\n## Update History\n\n"
                + "- 2026-06-12T18:00 — Reviewed the merged feature change.\n",
                encoding="utf-8",
            )
            code_head = git(contract.code_worktree, "rev-parse", "HEAD")
            args = Namespace(
                contract_path=contract.contract_path,
                approved=True,
                approval_note="developer approved commit preview",
                code_commit_message="Add feature",
                memory_commit_message="Document feature",
                ledger_commit_message="Sync ledger",
                dry_run=False,
            )
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(worktree_manager.command_closeout(args), 0)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["code_commit"], code_head)
            self.assertEqual(read_onboarding_field(sidecar, "lastVerifiedCommitHash"), code_head)
            self.assertEqual(
                payload["refreshed_onboarding"], {"count": 1, "sample": ["feature.txt"]}
            )
            self.assertEqual(
                payload["unonboarded_changed_paths"], {"count": 1, "sample": ["raw.txt"]}
            )
            ledger = parse_ledger_text(
                (contract.memory_worktree / "memory.md").read_text(encoding="utf-8")
            )
            self.assertEqual(ledger.last_verified_code_commit, code_head)
            self.assertEqual(load_contract(contract.contract_path).closeout_status, "completed")

    def test_closeout_blocks_stale_sidecar_for_committed_range_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = committed_range_external_contract_fixture(root)
            args = Namespace(
                contract_path=contract.contract_path,
                approved=True,
                approval_note="developer approved commit preview",
                code_commit_message="Add feature",
                memory_commit_message="Document feature",
                ledger_commit_message="Sync ledger",
                dry_run=False,
            )
            with self.assertRaisesRegex(RuntimeError, "feature.txt"):
                worktree_manager.command_closeout(args)
            self.assertEqual(load_contract(contract.contract_path).closeout_status, "not-started")

    def test_second_closeout_does_not_regate_prior_closeout_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = closed_external_contract_fixture(root)
            assert contract.memory_worktree is not None
            commit_file(contract.code_worktree, "second.txt", "second\n", "Add second slice")
            write_file_onboarding(
                contract.memory_worktree / "onboarding",
                contract.repo_name,
                "second.txt",
                contract.code_commit,
            )
            args = Namespace(
                contract_path=contract.contract_path,
                approved=False,
                approval_note="",
                code_commit_message="Add second slice",
                memory_commit_message="Document second slice",
                ledger_commit_message="Sync ledger",
                dry_run=True,
            )
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(worktree_manager.command_closeout(args), 0)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["changed_code_paths"], {"count": 1, "sample": ["second.txt"]})
            self.assertEqual(payload["sidecar_body_gate"]["stale"], {"count": 0, "sample": []})

    def test_closeout_excludes_sync_transported_committed_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = closed_external_contract_fixture(root)
            commit_file(contract.code_repo_path, "other.txt", "other\n", "Parallel landing")
            new_main = git(contract.code_repo_path, "rev-parse", "HEAD")
            git(contract.code_worktree, "merge", "--no-ff", "-m", "Sync main", "main")
            synced = replace(contract, code_base_commit=new_main)
            write_contract(synced.contract_path, synced)
            args = Namespace(
                contract_path=contract.contract_path,
                approved=False,
                approval_note="",
                code_commit_message="No-op",
                memory_commit_message="No-op",
                ledger_commit_message="No-op",
                dry_run=True,
            )
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(worktree_manager.command_closeout(args), 0)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["changed_code_paths"], {"count": 0, "sample": []})
            self.assertEqual(payload["changed_code_paths_committed"], {"count": 0, "sample": []})

    def test_closeout_preview_bounds_committed_path_lists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = committed_range_external_contract_fixture(root)
            for index in range(PATH_SAMPLE_LIMIT + 5):
                (contract.code_worktree / f"bulk-{index:02d}.txt").write_text(
                    "x\n", encoding="utf-8"
                )
            git(contract.code_worktree, "add", "-A")
            git(contract.code_worktree, "commit", "-m", "Bulk transport")
            args = Namespace(
                contract_path=contract.contract_path,
                approved=False,
                approval_note="",
                code_commit_message="Bulk",
                memory_commit_message="Bulk",
                ledger_commit_message="Bulk",
                dry_run=True,
            )
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(worktree_manager.command_closeout(args), 0)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["changed_code_paths"]["count"], PATH_SAMPLE_LIMIT + 7)
            self.assertEqual(len(payload["changed_code_paths"]["sample"]), PATH_SAMPLE_LIMIT)
            unonboarded = payload["onboarding_metadata_refresh"]["unonboarded"]
            self.assertEqual(unonboarded["count"], PATH_SAMPLE_LIMIT + 6)
            self.assertEqual(len(unonboarded["sample"]), PATH_SAMPLE_LIMIT)

    def test_closeout_blocks_memory_commit_when_memory_quality_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = dirty_open_external_contract_fixture(root)
            assert contract.memory_worktree is not None
            memory_head = git(contract.memory_worktree, "rev-parse", "HEAD")
            args = Namespace(
                contract_path=contract.contract_path,
                approved=True,
                approval_note="developer approved commit preview",
                code_commit_message="Add feature",
                memory_commit_message="Document feature",
                ledger_commit_message="Sync ledger",
                dry_run=False,
            )
            failed_quality = {
                "ok": False,
                "findingCount": 1,
                "findings": [
                    {
                        "code": "onboarding_drift_test",
                        "path": "feature.txt.md",
                        "message": "test drift",
                    }
                ],
            }
            with (
                mock.patch(
                    "agents_remember.worktrees.modules.closeout.run_memory_quality_check",
                    return_value=failed_quality,
                ),
                self.assertRaisesRegex(RuntimeError, "clean memory_quality_check"),
            ):
                worktree_manager.command_closeout(args)
            self.assertEqual(git(contract.memory_worktree, "rev-parse", "HEAD"), memory_head)
            self.assertEqual(load_contract(contract.contract_path).closeout_status, "not-started")

    def test_closeout_refreshes_entity_fingerprint_after_code_commit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = open_external_contract_fixture(root)
            assert contract.memory_worktree is not None
            (contract.code_worktree / "feature.txt").write_text("old\n", encoding="utf-8")
            git(contract.code_worktree, "add", "feature.txt")
            git(contract.code_worktree, "commit", "-m", "Add feature baseline")
            baseline_commit = git(contract.code_worktree, "rev-parse", "HEAD")
            write_file_onboarding(
                contract.memory_worktree / "onboarding",
                contract.repo_name,
                "feature.txt",
                baseline_commit,
            )
            seed_fingerprint = drift.compute_git_blob_set_fingerprint(
                contract.code_worktree, ["feature.txt"]
            )
            catalog = write_entity_catalog(
                contract.memory_worktree / "onboarding",
                contract.repo_name,
                [("Feature", drift.GIT_BLOB_SET_ALGORITHM, seed_fingerprint, ["feature.txt"])],
            )
            (contract.code_worktree / "feature.txt").write_text("new\n", encoding="utf-8")
            args = Namespace(
                contract_path=contract.contract_path,
                approved=True,
                approval_note="developer approved commit preview",
                code_commit_message="Update feature",
                memory_commit_message="Document feature update",
                ledger_commit_message="Sync ledger",
                dry_run=False,
            )
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(worktree_manager.command_closeout(args), 0)
            payload = json.loads(output.getvalue())
            expected = drift.compute_git_blob_set_fingerprint(
                contract.code_worktree, ["feature.txt"]
            )
            self.assertNotEqual(expected, seed_fingerprint)
            self.assertEqual(payload["refreshed_entities"][0]["entity"], "Feature")
            self.assertIn(expected, catalog.read_text(encoding="utf-8"))
            self.assertIn(
                "entities.md",
                git(
                    contract.memory_worktree,
                    "show",
                    "--name-only",
                    "--format=",
                    payload["memory_content_commit"],
                ),
            )

    def test_status_reports_integration_pending_for_dirty_closed_contract(self) -> None:
        # slice 09: a dirty tree is no longer read as a commit-approval gate. A closed-out contract reports
        # its honest lifecycle position (integration-pending) even when the worktree is dirty;
        # commit-approval-pending is owned by the closeout preview / a raised gate, not `git status`.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = closed_external_contract_fixture(root)
            (contract.code_worktree / "followup.txt").write_text("follow-up\n", encoding="utf-8")
            payload = worktree_manager.status_payload(contract)
            self.assertEqual(payload["phase"], "integration-pending")
            self.assertEqual(payload["nextOperation"], "request_integration_decision")
            self.assertEqual(payload["nextTool"], "worktree_integrate")
            self.assertNotIn("next_command", payload)

    def test_integrate_ff_only_fast_forwards_code_and_memory_main(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = closed_external_contract_fixture(root)
            assert contract.memory_worktree is not None
            assert contract.memory_repo_path is not None
            args = Namespace(
                contract_path=contract.contract_path,
                approved=True,
                strategy="ff-only",
                ledger_commit_message="",
                dry_run=False,
            )
            with redirect_stdout(io.StringIO()):
                self.assertEqual(worktree_manager.command_integrate(args), 0)
            self.assertEqual(
                git(contract.code_repo_path, "rev-parse", "main"), contract.code_commit
            )
            self.assertEqual(
                git(contract.memory_repo_path, "rev-parse", "main"), contract.ledger_commit
            )
            loaded = load_contract(contract.contract_path)
            self.assertEqual(loaded.integration_status, "completed")
            self.assertEqual(loaded.integrated_code_commit, contract.code_commit)
            self.assertEqual(
                loaded.integrated_memory_content_commit, contract.memory_content_commit
            )
            self.assertEqual(loaded.integrated_ledger_commit, contract.ledger_commit)
            self.assertEqual(worktree_manager.status_payload(loaded)["phase"], "cleanup-pending")

            cleanup_args = Namespace(
                contract_path=contract.contract_path, approved=True, dry_run=False
            )
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(worktree_manager.command_cleanup(cleanup_args), 0)
            cleanup_payload = json.loads(output.getvalue())
            self.assertEqual(cleanup_payload["state"], "cleanup-completed")
            self.assertEqual(cleanup_payload["nextOperation"], "done")
            self.assertNotIn("next_command", cleanup_payload)
            self.assertFalse(contract.code_worktree.exists())
            self.assertFalse(contract.memory_worktree.exists())
            self.assertFalse(
                git(contract.code_repo_path, "branch", "--list", contract.code_work_branch)
            )
            self.assertFalse(
                git(contract.memory_repo_path, "branch", "--list", contract.memory_work_branch)
            )
            loaded = load_contract(contract.contract_path)
            self.assertEqual(loaded.cleanup, "completed")
            self.assertEqual(worktree_manager.status_payload(loaded)["phase"], "cleanup-completed")

            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(worktree_manager.command_cleanup(cleanup_args), 0)
            self.assertEqual(json.loads(output.getvalue())["state"], "already-clean")

    def test_cleanup_blocks_before_integration_completed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = closed_external_contract_fixture(root)
            args = Namespace(contract_path=contract.contract_path, approved=True, dry_run=False)
            with self.assertRaises(RuntimeError):
                worktree_manager.command_cleanup(args)

    def test_integrate_replay_handles_parallel_non_overlapping_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = closed_external_contract_fixture(root)
            assert contract.memory_repo_path is not None
            parallel_code = commit_file(
                contract.code_repo_path, "parallel.txt", "parallel\n", "Parallel code change"
            )
            parallel_memory_content = commit_file(
                contract.memory_repo_path,
                "onboarding/parallel.txt.md",
                "# parallel\n",
                "Document parallel change",
            )
            commit_memory_ledger(
                contract.memory_repo_path,
                parallel_code,
                parallel_memory_content,
                "Sync parallel ledger",
            )
            args = Namespace(
                contract_path=contract.contract_path,
                approved=True,
                strategy="replay",
                ledger_commit_message="Replay integration ledger",
                dry_run=False,
            )
            with redirect_stdout(io.StringIO()):
                self.assertEqual(worktree_manager.command_integrate(args), 0)
            loaded = load_contract(contract.contract_path)
            self.assertEqual(loaded.integration_status, "completed")
            self.assertNotEqual(loaded.integrated_code_commit, contract.code_commit)
            self.assertNotEqual(loaded.integrated_ledger_commit, contract.ledger_commit)
            self.assertEqual(
                git(contract.code_repo_path, "rev-parse", "main"), loaded.integrated_code_commit
            )
            self.assertEqual(
                git(contract.memory_repo_path, "rev-parse", "main"), loaded.integrated_ledger_commit
            )
            self.assertTrue((contract.code_repo_path / "feature.txt").exists())
            self.assertTrue((contract.code_repo_path / "parallel.txt").exists())
            ledger = parse_ledger_text(
                (contract.memory_repo_path / "memory.md").read_text(encoding="utf-8")
            )
            self.assertEqual(ledger.rows[0].code_commit, loaded.integrated_code_commit)
            self.assertEqual(ledger.rows[0].memory_commit, loaded.integrated_memory_content_commit)

    def test_integrate_replay_blocks_code_conflicts_before_main_moves(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = closed_external_contract_fixture(
                root, code_path="README.md", code_content="# Task\n"
            )
            assert contract.memory_repo_path is not None
            parallel_code = commit_file(
                contract.code_repo_path, "README.md", "# Parallel\n", "Parallel conflicting change"
            )
            args = Namespace(
                contract_path=contract.contract_path,
                approved=True,
                strategy="replay",
                ledger_commit_message="Replay integration ledger",
                dry_run=False,
            )
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(worktree_manager.command_integrate(args), 2)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["state"], "blocked-code-conflict")
            self.assertTrue(payload["developer_decision_required"])
            self.assertEqual(git(contract.code_repo_path, "rev-parse", "main"), parallel_code)
            self.assertEqual(
                git(contract.memory_repo_path, "rev-parse", "main"), contract.memory_base_commit
            )
            loaded = load_contract(contract.contract_path)
            self.assertEqual(loaded.integration_status, "blocked")
            self.assertEqual(
                worktree_manager.status_payload(loaded)["phase"], "integration-blocked"
            )

    def test_resolver_explicit_internal_uses_existing_ar_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            repo = workspace / "my-app"
            repo.mkdir()
            (repo / "ar-memory").mkdir()
            context = resolver.resolve_coordination_context(
                code_repository_name="my-app",
                workspace_root=workspace,
                requested_topology="internal",
            )
            self.assertEqual(context.coordination_root, repo / "ar-coordination")
            self.assertEqual(context.memory_root, repo / "ar-memory")
            self.assertEqual(context.onboarding_root, repo / "ar-memory" / "onboarding")
            self.assertEqual(context.temp_root, repo / "ar-coordination" / "temp")

    def test_resolver_prefers_existing_internal_memory_over_external_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            repo = workspace / "repo-a"
            repo.mkdir()
            (repo / "ar-memory").mkdir()
            external_memory = workspace / "ar-coordination" / "memory-repos" / "ar-repo-a"
            external_memory.mkdir(parents=True)
            context = resolver.resolve_coordination_context(
                code_repository_name="repo-a",
                workspace_root=workspace,
                coordination_root=workspace / "ar-coordination",
            )
            self.assertEqual(context.topology, "internal")
            self.assertEqual(context.memory_root, repo / "ar-memory")
            self.assertEqual(context.coordination_root, repo / "ar-coordination")

    def test_resolver_uses_external_memory_repo_when_internal_memory_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            repo = workspace / "repo-a"
            repo.mkdir()
            external_memory = workspace / "ar-coordination" / "memory-repos" / "ar-repo-a"
            external_memory.mkdir(parents=True)
            context = resolver.resolve_coordination_context(
                code_repository_name="repo-a",
                workspace_root=workspace,
                coordination_root=workspace / "ar-coordination",
            )
            self.assertEqual(context.topology, "external")
            self.assertEqual(context.coordination_root, workspace / "ar-coordination")
            self.assertEqual(context.memory_root, external_memory)
            self.assertEqual(context.onboarding_root, external_memory / "onboarding")

    def test_resolver_ignores_dot_env_override_for_coordination_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            repo = workspace / "repo-a"
            repo.mkdir()
            agents_repo = workspace / "agents-remember"
            agents_repo.mkdir()
            (agents_repo / ".env").write_text(
                "AR_COORDINATION_ROOT=custom-coordination\n", encoding="utf-8"
            )
            (agents_repo / "custom-coordination" / "memory-repos" / "ar-repo-a").mkdir(parents=True)
            external_memory = workspace / "ar-coordination" / "memory-repos" / "ar-repo-a"
            external_memory.mkdir(parents=True)
            with mock.patch.object(resolver, "agents_repo_from_script", return_value=agents_repo):
                context = resolver.resolve_coordination_context(
                    code_repository_name="repo-a",
                    workspace_root=workspace,
                )
            self.assertEqual(context.topology, "external")
            self.assertEqual(context.coordination_root, workspace / "ar-coordination")
            self.assertEqual(context.memory_root, external_memory)

    def test_resolver_uses_installed_runtime_root_as_coordination_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            repo = workspace / "repo-a"
            repo.mkdir()
            coordination_root = workspace / "runtime-home"
            (coordination_root / "skills").mkdir(parents=True)
            (coordination_root / "system").mkdir()
            (coordination_root / "tasks").mkdir()
            external_memory = coordination_root / "memory-repos" / "ar-repo-a"
            external_memory.mkdir(parents=True)
            with mock.patch.object(
                resolver, "agents_repo_from_script", return_value=coordination_root
            ):
                context = resolver.resolve_coordination_context(
                    code_repository_name="repo-a",
                    workspace_root=workspace,
                )
            self.assertEqual(context.topology, "external")
            self.assertEqual(context.coordination_root, coordination_root)
            self.assertEqual(context.memory_root, external_memory)

    def test_resolver_ignores_dot_env_example_at_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            repo = workspace / "repo-a"
            repo.mkdir()
            agents_repo = workspace / "agents-remember"
            agents_repo.mkdir()
            (agents_repo / ".env.example").write_text(
                "AR_COORDINATION_ROOT=example-coordination\n", encoding="utf-8"
            )
            (agents_repo / "example-coordination" / "memory-repos" / "ar-repo-a").mkdir(
                parents=True
            )
            with (
                mock.patch.object(resolver, "agents_repo_from_script", return_value=agents_repo),
                self.assertRaises(resolver.MissingMemoryError) as raised,
            ):
                resolver.resolve_coordination_context(
                    code_repository_name="repo-a",
                    workspace_root=workspace,
                )
            self.assertEqual(raised.exception.coordination_root, workspace / "ar-coordination")

    def test_resolver_does_not_select_legacy_external_onboarding_without_memory_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            repo = workspace / "repo-a"
            repo.mkdir()
            coordination_root = workspace / "ar-coordination"
            (coordination_root / "onboarding" / "repo-a").mkdir(parents=True)
            with self.assertRaises(resolver.MissingMemoryError):
                resolver.resolve_coordination_context(
                    code_repository_name="repo-a",
                    workspace_root=workspace,
                    coordination_root=coordination_root,
                )

    def test_resolver_does_not_select_external_from_path_rules_without_memory_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            repo = workspace / "repo-a"
            repo.mkdir()
            coordination_root = workspace / "ar-coordination"
            system_root = coordination_root / "system"
            system_root.mkdir(parents=True)
            (system_root / "settings.json").write_text(
                json.dumps({"version": 2, "onboarding": {"pathRules": {"path": "repo-a"}}}),
                encoding="utf-8",
            )
            with self.assertRaises(resolver.MissingMemoryError):
                resolver.resolve_coordination_context(
                    code_repository_name="repo-a",
                    workspace_root=workspace,
                    coordination_root=coordination_root,
                )

    def test_resolver_errors_when_memory_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            repo = workspace / "repo-a"
            repo.mkdir()
            agents_repo = workspace / "agents-remember"
            agents_repo.mkdir()
            with self.assertRaises(resolver.MissingMemoryError) as raised:
                resolver.resolve_coordination_context(
                    code_repository_name="repo-a",
                    workspace_root=workspace,
                    coordination_root=workspace / "ar-coordination",
                )
            self.assertEqual(raised.exception.internal_root, repo / "ar-memory")
            self.assertEqual(
                raised.exception.external_memory,
                workspace / "ar-coordination" / "memory-repos" / "ar-repo-a",
            )
            self.assertIn(
                "initialize memory with c-00-initialize-memory-repo", str(raised.exception)
            )

    def test_drift_report_paths_use_temp_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            repo = workspace / "repo-a"
            init_repo(repo, "main")
            coordination_root = workspace / "ar-coordination"
            temp_root = coordination_root / "temp"
            memory_root = coordination_root / "memory-repos" / "ar-repo-a"
            drift = adopt_baseline.drift
            default_report = drift.resolve_report_path(None, coordination_root, temp_root, repo)
            self.assertEqual(
                default_report,
                temp_root / "drift-reports" / "repo-a" / "repo-a_main_drift-report.md",
            )
            self.assertEqual(
                drift.resolve_report_path(
                    Path("custom/report.md"), coordination_root, temp_root, repo
                ),
                temp_root / "custom" / "report.md",
            )
            self.assertEqual(
                drift.resolve_report_path(
                    Path("../tasks/leak.md"), coordination_root, temp_root, repo
                ),
                temp_root / "drift-reports" / "repo-a" / "leak.md",
            )
            inside_coordination = coordination_root / "tasks" / "manual.md"
            self.assertEqual(
                drift.resolve_report_path(inside_coordination, coordination_root, temp_root, repo),
                inside_coordination,
            )
            memory_report = memory_root / "reports" / "manual-drift-report.md"
            self.assertEqual(
                drift.resolve_report_path(
                    memory_report, coordination_root, temp_root, repo, memory_root
                ),
                temp_root / "drift-reports" / "repo-a" / "manual-drift-report.md",
            )
            self.assertEqual(
                drift.resolve_report_path(
                    workspace / "outside.md", coordination_root, temp_root, repo
                ),
                temp_root / "drift-reports" / "repo-a" / "outside.md",
            )

    def test_drift_detects_clean_route_local_overview(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            repo = workspace / "repo-a"
            init_repo(repo, "main")
            verified_commit = commit_file(repo, "src/service.py", "print('ok')\n", "Add service")
            onboarding_root = workspace / "memory" / "onboarding"
            overview = write_route_overview(onboarding_root, "repo-a", "src", verified_commit)

            rows = drift.classify_sidecar_onboarding_units(
                overview,
                repo,
                onboarding_root,
                resolver.StorageSettings(mode="memory-repo", default="memory-repo"),
            )

            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].classification, "up to date")
            self.assertEqual(rows[0].source_file, "src")

    def test_drift_detects_changed_route_local_overview_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            repo = workspace / "repo-a"
            init_repo(repo, "main")
            verified_commit = commit_file(repo, "src/service.py", "print('old')\n", "Add service")
            onboarding_root = workspace / "memory" / "onboarding"
            overview = write_route_overview(onboarding_root, "repo-a", "src", verified_commit)
            commit_file(repo, "src/service.py", "print('new')\n", "Change service")

            rows = drift.classify_sidecar_onboarding_units(
                overview,
                repo,
                onboarding_root,
                resolver.StorageSettings(mode="memory-repo", default="memory-repo"),
            )

            self.assertEqual(rows[0].classification, "drifted")
            self.assertIn("Source route changed", rows[0].note)

    def test_drift_detects_clean_entity_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            repo = workspace / "repo-a"
            init_repo(repo, "main")
            commit_file(repo, "src/entity.py", "class Entity:\n    pass\n", "Add entity")
            fingerprint = drift.compute_git_blob_set_fingerprint(repo, ["src/entity.py"])
            onboarding_root = workspace / "memory" / "onboarding"
            catalog = write_entity_catalog(
                onboarding_root,
                "repo-a",
                [("Entity", drift.GIT_BLOB_SET_ALGORITHM, fingerprint, ["src/entity.py"])],
            )

            rows = drift.classify_sidecar_onboarding_units(
                catalog,
                repo,
                onboarding_root,
                resolver.StorageSettings(mode="memory-repo", default="memory-repo"),
            )

            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].source_file, "entity:Entity")
            self.assertEqual(rows[0].classification, "up to date")

    def test_drift_detects_entity_fingerprint_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            repo = workspace / "repo-a"
            init_repo(repo, "main")
            commit_file(repo, "src/entity.py", "class Entity:\n    pass\n", "Add entity")
            fingerprint = drift.compute_git_blob_set_fingerprint(repo, ["src/entity.py"])
            onboarding_root = workspace / "memory" / "onboarding"
            catalog = write_entity_catalog(
                onboarding_root,
                "repo-a",
                [("Entity", drift.GIT_BLOB_SET_ALGORITHM, fingerprint, ["src/entity.py"])],
            )
            commit_file(
                repo, "src/entity.py", "class Entity:\n    name = 'changed'\n", "Change entity"
            )

            rows = drift.classify_sidecar_onboarding_units(
                catalog,
                repo,
                onboarding_root,
                resolver.StorageSettings(mode="memory-repo", default="memory-repo"),
            )

            self.assertEqual(rows[0].classification, "drifted")
            self.assertIn("fingerprint changed", rows[0].note)

    def test_drift_detects_missing_entity_evidence_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            repo = workspace / "repo-a"
            init_repo(repo, "main")
            onboarding_root = workspace / "memory" / "onboarding"
            catalog = write_entity_catalog(
                onboarding_root,
                "repo-a",
                [("Entity", drift.GIT_BLOB_SET_ALGORITHM, "sha256:abc", ["src/missing.py"])],
            )

            rows = drift.classify_sidecar_onboarding_units(
                catalog,
                repo,
                onboarding_root,
                resolver.StorageSettings(mode="memory-repo", default="memory-repo"),
            )

            self.assertEqual(rows[0].classification, "drifted")
            self.assertIn("Entity evidence path missing", rows[0].note)
            self.assertIn("removed, renamed, or moved", rows[0].note)

    def test_drift_detects_entity_inventory_without_fingerprint_table(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            repo = workspace / "repo-a"
            init_repo(repo, "main")
            onboarding_root = workspace / "memory" / "onboarding"
            catalog = write_entity_catalog(
                onboarding_root,
                "repo-a",
                [],
                inventory_entities=["Entity"],
                include_fingerprints=False,
            )

            rows = drift.classify_sidecar_onboarding_units(
                catalog,
                repo,
                onboarding_root,
                resolver.StorageSettings(mode="memory-repo", default="memory-repo"),
            )

            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].source_file, "entity:Entity")
            self.assertEqual(rows[0].classification, "missing verification")
            self.assertIn("no parseable Entity Fingerprints table", rows[0].note)

    def test_drift_detects_entity_inventory_entry_missing_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            repo = workspace / "repo-a"
            init_repo(repo, "main")
            commit_file(repo, "src/entity.py", "class Entity:\n    pass\n", "Add entity")
            fingerprint = drift.compute_git_blob_set_fingerprint(repo, ["src/entity.py"])
            onboarding_root = workspace / "memory" / "onboarding"
            catalog = write_entity_catalog(
                onboarding_root,
                "repo-a",
                [("Entity", drift.GIT_BLOB_SET_ALGORITHM, fingerprint, ["src/entity.py"])],
                inventory_entities=["Entity", "Other"],
            )

            rows = drift.classify_sidecar_onboarding_units(
                catalog,
                repo,
                onboarding_root,
                resolver.StorageSettings(mode="memory-repo", default="memory-repo"),
            )

            by_source = {row.source_file: row for row in rows}
            self.assertEqual(by_source["entity:Entity"].classification, "up to date")
            self.assertEqual(by_source["entity:Other"].classification, "missing verification")
            self.assertIn("no matching fingerprint row", by_source["entity:Other"].note)

    def test_drift_detects_orphaned_entity_fingerprint_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            repo = workspace / "repo-a"
            init_repo(repo, "main")
            commit_file(repo, "src/entity.py", "class Entity:\n    pass\n", "Add entity")
            fingerprint = drift.compute_git_blob_set_fingerprint(repo, ["src/entity.py"])
            onboarding_root = workspace / "memory" / "onboarding"
            catalog = write_entity_catalog(
                onboarding_root,
                "repo-a",
                [
                    ("Entity", drift.GIT_BLOB_SET_ALGORITHM, fingerprint, ["src/entity.py"]),
                    ("Removed", drift.GIT_BLOB_SET_ALGORITHM, fingerprint, ["src/entity.py"]),
                ],
                inventory_entities=["Entity"],
            )

            rows = drift.classify_sidecar_onboarding_units(
                catalog,
                repo,
                onboarding_root,
                resolver.StorageSettings(mode="memory-repo", default="memory-repo"),
            )

            by_source = {row.source_file: row for row in rows}
            self.assertEqual(by_source["entity:Entity"].classification, "up to date")
            self.assertEqual(by_source["entity:Removed"].classification, "orphaned")
            self.assertIn("removed, renamed, or moved", by_source["entity:Removed"].note)

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
                allow=[
                    resolver.CrossRepoAllowEntry(
                        repo="repo-b",
                        expected_branch="main",
                        include_code=True,
                        include_memory=False,
                    )
                ]
            )
            resolved = resolver.resolve_cross_repo_settings(
                settings, workspace, workspace / "ar-coordination"
            )
            self.assertEqual(resolved.allow[0].state, "included-code-only")

    def test_cross_repo_v2_memory_include(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            code_head = init_repo(workspace / "repo-b", "main")
            memory_repo = workspace / "ar-coordination" / "memory-repos" / "ar-repo-b"
            memory_head = init_repo(memory_repo, "main")
            write_ledger(
                memory_repo / "memory.md", create_initial_ledger("repo-b", code_head, memory_head)
            )
            git(memory_repo, "add", "memory.md")
            git(memory_repo, "commit", "-m", "Add memory ledger")
            settings = resolver.CrossRepoSettings(
                allow=[
                    resolver.CrossRepoAllowEntry(
                        repo="repo-b",
                        expected_branch="main",
                        include_code=True,
                        include_memory=True,
                    )
                ]
            )
            resolved = resolver.resolve_cross_repo_settings(
                settings, workspace, workspace / "ar-coordination"
            )
            self.assertEqual(resolved.allow[0].state, "included")

    def test_adopt_memory_baseline_status_ready_without_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            code_head = init_repo(workspace / "repo-a", "main")
            memory_root = workspace / "ar-coordination" / "memory-repos" / "ar-repo-a"
            init_repo(memory_root, "main")
            (memory_root / "docs").mkdir()
            (memory_root / "docs" / ".gitkeep").write_text("", encoding="utf-8")
            (memory_root / "system").mkdir()
            (memory_root / "system" / "settings.md").write_text("# Settings\n", encoding="utf-8")
            write_file_onboarding(memory_root / "onboarding", "repo-a", "README.md", code_head)
            args = Namespace(
                code_repository_name="repo-a",
                workspace_root=workspace,
                topology="external",
                coordination_root=workspace / "ar-coordination",
                code_repository_root=None,
                report=None,
            )
            context = adopt_baseline.resolve_baseline_context(args)
            rows, report = adopt_baseline.run_drift(context, None)
            payload: dict[str, Any] = adopt_baseline.base_payload(context, rows, report)
            self.assertEqual(
                report,
                workspace
                / "ar-coordination"
                / "temp"
                / "drift-reports"
                / "repo-a"
                / "repo-a_main_drift-report.md",
            )
            self.assertTrue(report.exists())
            self.assertFalse(
                (
                    workspace
                    / "ar-coordination"
                    / "tasks"
                    / "repo-a"
                    / "repo-a_main_drift-report.md"
                ).exists()
            )
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
            memory_root = workspace / "ar-coordination" / "memory-repos" / "ar-repo-a"
            write_file_onboarding(memory_root / "onboarding", "repo-a", "README.md", code_head)
            args = Namespace(
                code_repository_name="repo-a",
                workspace_root=workspace,
                topology="external",
                coordination_root=workspace / "ar-coordination",
                code_repository_root=None,
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
            memory_root = workspace / "ar-coordination" / "memory-repos" / "ar-repo-a"
            init_repo(memory_root, "main")
            (memory_root / "docs").mkdir()
            (memory_root / "docs" / ".gitkeep").write_text("", encoding="utf-8")
            (memory_root / "system").mkdir()
            (memory_root / "system" / "settings.md").write_text("# Settings\n", encoding="utf-8")
            write_file_onboarding(memory_root / "onboarding", "repo-a", "README.md", code_head)
            args = Namespace(
                code_repository_name="repo-a",
                workspace_root=workspace,
                topology="external",
                coordination_root=workspace / "ar-coordination",
                code_repository_root=None,
                report=None,
                accept_drift=False,
                source_branch=None,
                work_branch=None,
                dry_run=False,
            )
            with redirect_stdout(io.StringIO()):
                self.assertEqual(adopt_baseline.command_adopt(args), 0)
            ledger = parse_ledger_text((memory_root / "memory.md").read_text(encoding="utf-8"))
            ledger_text = (memory_root / "memory.md").read_text(encoding="utf-8")
            self.assertEqual(ledger.last_verified_code_commit, code_head)
            self.assertNotIn("trackedCodeBranch", ledger_text)
            self.assertNotIn("memoryBranch", ledger_text)
            self.assertTrue((memory_root / "docs" / ".gitkeep").exists())

    def test_memory_carryover_applies_landed_branch_onboarding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            code_repo = workspace / "repo-a"
            old_base = init_repo(code_repo, "main")
            git(code_repo, "checkout", "-b", "workbench/reado/v1.2")
            source_head = commit_file(
                code_repo, "feature.py", "def feature():\n    return 'landed'\n", "Add feature"
            )
            git(code_repo, "checkout", "main")
            git(code_repo, "merge", "--ff-only", "workbench/reado/v1.2")
            official_head = git(code_repo, "rev-parse", "main")
            self.assertEqual(official_head, source_head)

            official_memory = workspace / "ar-coordination" / "memory-repos" / "ar-repo-a"
            initialized_memory_repo(official_memory, "repo-a", "main", "main", old_base)
            source_memory = workspace / "ar-coordination" / "memory-source-branch" / "ar-repo-a"
            write_file_onboarding(source_memory / "onboarding", "repo-a", "feature.py", source_head)
            onboarding_file = source_memory / "onboarding" / "feature.py.md"
            onboarding_file.write_text(
                onboarding_file.read_text(encoding="utf-8") + "Branch-learned behavior.\n",
                encoding="utf-8",
            )

            args = Namespace(
                code_repository_root=code_repo,
                official_code_ref="main",
                source_code_ref="workbench/reado/v1.2",
                old_base=old_base,
                official_memory=official_memory,
                source_memory=source_memory,
                code_repository_name="repo-a",
                replace_existing=False,
                approved=True,
                approval_note="developer approved c-11-memory-carryover-from-branch carryover",
                include_review_required=[],
                memory_commit_message="Carry over landed memory",
                ledger_commit_message="Record carryover ledger",
            )
            plan: dict[str, Any] = memory_carryover.build_plan(args)
            self.assertEqual(plan["counts"], {"auto-carry": 1})

            payload: dict[str, Any] = memory_carryover.apply_carryover(args)
            official_onboarding = official_memory / "onboarding" / "feature.py.md"
            ledger = parse_ledger_text((official_memory / "memory.md").read_text(encoding="utf-8"))
            self.assertEqual(payload["state"], "carried-over")
            self.assertEqual(payload["carried"][0]["source_path"], "feature.py")
            self.assertIn(
                "Branch-learned behavior.", official_onboarding.read_text(encoding="utf-8")
            )
            self.assertEqual(
                read_onboarding_field(official_onboarding, "lastVerifiedCommitHash"), official_head
            )
            self.assertEqual(ledger.rows[0].code_commit, official_head)
            self.assertEqual(ledger.rows[0].memory_commit, payload["memory_content_commit"])

    def test_memory_carryover_requires_review_for_same_path_ambiguity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            code_repo = workspace / "repo-a"
            init_repo(code_repo, "main")
            old_base = commit_file(code_repo, "feature.py", "value = 'base'\n", "Add feature base")
            git(code_repo, "checkout", "-b", "workbench/reado/v1.2")
            source_head = commit_file(
                code_repo,
                "feature.py",
                "value = 'source branch'\n",
                "Update feature on source branch",
            )
            git(code_repo, "checkout", "main")
            commit_file(
                code_repo, "feature.py", "value = 'official'\n", "Update feature differently"
            )

            official_memory = workspace / "ar-coordination" / "memory-repos" / "ar-repo-a"
            initialized_memory_repo(official_memory, "repo-a", "main", "main", old_base)
            source_memory = workspace / "ar-coordination" / "memory-source-branch" / "ar-repo-a"
            write_file_onboarding(source_memory / "onboarding", "repo-a", "feature.py", source_head)

            args = Namespace(
                code_repository_root=code_repo,
                official_code_ref="main",
                source_code_ref="workbench/reado/v1.2",
                old_base=old_base,
                official_memory=official_memory,
                source_memory=source_memory,
                code_repository_name="repo-a",
                replace_existing=False,
                approved=True,
                approval_note="developer approved c-11-memory-carryover-from-branch carryover",
                include_review_required=[],
                memory_commit_message="Carry over landed memory",
                ledger_commit_message="Record carryover ledger",
            )
            plan: dict[str, Any] = memory_carryover.build_plan(args)
            self.assertEqual(plan["candidates"][0]["decision"], "review-required")
            self.assertEqual(plan["candidates"][0]["evidence"], "same-path-changed")
            payload: dict[str, Any] = memory_carryover.apply_carryover(args)
            self.assertEqual(payload["state"], "nothing-to-carryover")
            self.assertFalse((official_memory / "onboarding" / "feature.py.md").exists())

    def test_memory_carryover_maps_unmapped_official_head_when_nothing_to_carry(self) -> None:
        # Regression for the PR-merge-commit ledger gap: when nothing is actionable
        # to carry but the official code HEAD is not in the ledger (e.g. a merge
        # commit landed on top of the verified tip), carryover maps it to the
        # current memory content so the next worktree can base off the merged branch
        # without a manual reconciliation.
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            code_repo = workspace / "repo-a"
            old_base = init_repo(code_repo, "main")
            # A source branch with no commits of its own: nothing to carry.
            git(code_repo, "checkout", "-b", "workbench/reado/v1")
            git(code_repo, "checkout", "main")
            # main advances to a new HEAD (stands in for a PR merge commit on top).
            official_head = commit_file(
                code_repo, "feature.py", "value = 'merged'\n", "Merge PR #1 into main"
            )
            self.assertNotEqual(official_head, old_base)

            official_memory = workspace / "ar-coordination" / "memory-repos" / "ar-repo-a"
            initialized_memory_repo(official_memory, "repo-a", "main", "main", old_base)
            source_memory = workspace / "ar-coordination" / "memory-source-branch" / "ar-repo-a"
            (source_memory / "onboarding").mkdir(parents=True, exist_ok=True)

            ledger_before = load_ledger(official_memory / "memory.md")
            self.assertIsNone(find_mapping(ledger_before, official_head))

            args = Namespace(
                code_repository_root=code_repo,
                official_code_ref="main",
                source_code_ref="workbench/reado/v1",
                old_base=old_base,
                official_memory=official_memory,
                source_memory=source_memory,
                code_repository_name="repo-a",
                replace_existing=False,
                approved=True,
                approval_note="developer approved c-11-memory-carryover-from-branch carryover",
                include_review_required=[],
                memory_commit_message="Carry over landed memory",
                ledger_commit_message="Record carryover ledger",
            )
            payload: dict[str, Any] = memory_carryover.apply_carryover(args)
            self.assertEqual(payload["state"], "ledger-mapped-head")

            ledger_after = load_ledger(official_memory / "memory.md")
            self.assertEqual(ledger_after.rows[0].code_commit, official_head)
            self.assertEqual(
                ledger_after.rows[0].memory_commit, ledger_before.last_memory_content_commit
            )
            self.assertEqual(ledger_after.last_verified_code_commit, official_head)

    def test_memory_carryover_requires_review_when_only_earlier_path_commit_landed(self) -> None:
        # Regression: a single landed path-commit must NOT count as exact-landed
        # when a later commit to the same path has not landed on the official ref.
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            code_repo = workspace / "repo-a"
            old_base = init_repo(code_repo, "main")
            git(code_repo, "checkout", "-b", "workbench/reado/v1.2")
            landed_head = commit_file(code_repo, "feature.py", "value = 'v1'\n", "Add feature v1")
            # Land ONLY the first source-branch commit on main.
            git(code_repo, "checkout", "main")
            git(code_repo, "merge", "--ff-only", "workbench/reado/v1.2")
            self.assertEqual(git(code_repo, "rev-parse", "main"), landed_head)
            # The source branch keeps going with a second, never-landed commit to the same path.
            git(code_repo, "checkout", "workbench/reado/v1.2")
            source_head = commit_file(
                code_repo, "feature.py", "value = 'v2 unlanded'\n", "Update feature v2"
            )

            official_memory = workspace / "ar-coordination" / "memory-repos" / "ar-repo-a"
            initialized_memory_repo(official_memory, "repo-a", "main", "main", old_base)
            source_memory = workspace / "ar-coordination" / "memory-source-branch" / "ar-repo-a"
            write_file_onboarding(source_memory / "onboarding", "repo-a", "feature.py", source_head)

            args = Namespace(
                code_repository_root=code_repo,
                official_code_ref="main",
                source_code_ref="workbench/reado/v1.2",
                old_base=old_base,
                official_memory=official_memory,
                source_memory=source_memory,
                code_repository_name="repo-a",
                replace_existing=False,
                approved=True,
                approval_note="developer approved c-11-memory-carryover-from-branch carryover",
                include_review_required=[],
                memory_commit_message="Carry over landed memory",
                ledger_commit_message="Record carryover ledger",
            )
            plan: dict[str, Any] = memory_carryover.build_plan(args)
            self.assertNotEqual(plan["candidates"][0]["evidence"], "exact-landed-commit")
            self.assertEqual(plan["candidates"][0]["evidence"], "same-path-changed")
            self.assertEqual(plan["candidates"][0]["decision"], "review-required")

    def test_memory_carryover_rejects_branch_memory_when_code_did_not_land(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            code_repo = workspace / "repo-a"
            old_base = init_repo(code_repo, "main")
            git(code_repo, "checkout", "-b", "workbench/reado/v1.2")
            source_head = commit_file(
                code_repo, "feature.py", "value = 'pending'\n", "Add pending feature"
            )
            git(code_repo, "checkout", "main")

            official_memory = workspace / "ar-coordination" / "memory-repos" / "ar-repo-a"
            initialized_memory_repo(official_memory, "repo-a", "main", "main", old_base)
            source_memory = workspace / "ar-coordination" / "memory-source-branch" / "ar-repo-a"
            write_file_onboarding(source_memory / "onboarding", "repo-a", "feature.py", source_head)

            args = Namespace(
                code_repository_root=code_repo,
                official_code_ref="main",
                source_code_ref="workbench/reado/v1.2",
                old_base=old_base,
                official_memory=official_memory,
                source_memory=source_memory,
                code_repository_name="repo-a",
                replace_existing=False,
            )
            plan: dict[str, Any] = memory_carryover.build_plan(args)
            self.assertEqual(plan["candidates"][0]["decision"], "reject")
            self.assertEqual(plan["candidates"][0]["evidence"], "not-landed")

    def test_integrate_refuses_non_fast_forward_code_without_mutating(self) -> None:
        # Atomicity: when the code fast-forward is impossible, integration must
        # raise BEFORE advancing the code branch (no half-integrated state).
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code_repo = root / "repo-a"
            base = init_repo(code_repo, "main")
            commit_file(code_repo, "main.py", "value = 1\n", "advance main")
            head_before = git(code_repo, "rev-parse", "HEAD")
            # A divergent commit whose ancestry does NOT include main's HEAD.
            git(code_repo, "checkout", "-b", "other", base)
            divergent = commit_file(code_repo, "other.py", "value = 2\n", "divergent")
            git(code_repo, "checkout", "main")

            contract = default_contract(
                task_name="Atomic Integrate",
                repo_name="repo-a",
                workflow_kind="chat",
                memory_mode="disabled",
                coordination_root=root / "ar-coordination",
                code_repo_path=code_repo,
                code_source_branch="main",
                code_work_branch="ar/atomic-integrate",
                code_base_commit=base,
                worktree_name="atomic-integrate",
            )
            with self.assertRaisesRegex(RuntimeError, "not a fast-forward"):
                _merge_integrated_commits(contract, divergent, "", "")
            self.assertEqual(git(code_repo, "rev-parse", "HEAD"), head_before)


class BenchmarkRunnerPortabilityTests(unittest.TestCase):
    def test_manifest_relative_path_rejects_absolute_and_parent_escape(self) -> None:
        self.assertEqual(
            benchmark_runner.manifest_relative_path("workspaces/case-a", "fixturePath"),
            Path("workspaces") / "case-a",
        )
        for value in (
            "../outside",
            "workspaces/../outside",
            "/tmp/outside",
            "C:/outside",
            "C:outside",
            r"..\outside",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                benchmark_runner.manifest_relative_path(value, "fixturePath")
        with self.assertRaises(ValueError):
            benchmark_runner.manifest_relative_path(None, "fixturePath")

    def test_manifest_path_component_rejects_nested_names(self) -> None:
        self.assertEqual(
            benchmark_runner.manifest_path_component("ar-repo-a", "memoryRepository.name"),
            "ar-repo-a",
        )
        with self.assertRaises(ValueError):
            benchmark_runner.manifest_path_component("nested/repo", "memoryRepository.name")

    def test_benchmark_safe_remove_deletes_readonly_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "workspace"
            nested = root / "nested"
            nested.mkdir(parents=True)
            locked = nested / "pack-file"
            locked.write_text("content\n", encoding="utf-8")
            os.chmod(locked, stat.S_IREAD)
            try:
                benchmark_runner.remove_path(root)
            finally:
                if locked.exists():
                    os.chmod(locked, stat.S_IWRITE)
            self.assertFalse(root.exists())

    def test_benchmark_safe_remove_deletes_directory_symlink_not_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target"
            target.mkdir()
            (target / "SKILL.md").write_text("content\n", encoding="utf-8")
            link = root / "link"
            try:
                os.symlink(target, link, target_is_directory=True)
            except (NotImplementedError, OSError) as error:
                raise unittest.SkipTest(f"directory symlinks unavailable: {error}") from error

            benchmark_runner.remove_path(link)

            self.assertFalse(link.exists() or link.is_symlink())
            self.assertTrue((target / "SKILL.md").is_file())

    def test_codex_executable_resolves_from_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cmd = root / "codex.cmd"
            cmd.write_text("@echo off\n", encoding="utf-8")

            def fake_which(command: str) -> str | None:
                if command == "codex":
                    return str(cmd)
                return None

            with mock.patch.object(benchmark_runner.shutil, "which", side_effect=fake_which):
                self.assertEqual(benchmark_runner.resolve_codex_executable(), str(cmd))

    def test_benchmark_provider_ids_follow_selected_variants(self) -> None:
        case = benchmark_runner.BenchmarkCase(
            Path("case.json"),
            {
                "id": "case-a",
                "repository": {"name": "repo-a"},
                "workspace": {"fixturePath": "workspaces/case-a"},
                "prompts": [
                    {
                        "id": "prompt-a",
                        "variants": [
                            {
                                "id": "no-onboarding",
                                "promptPath": "prompts/no.md",
                                "cwd": "workspaces/case-a/source-only",
                                "allowMemory": False,
                            },
                            {
                                "id": "with-onboarding",
                                "promptPath": "prompts/memory.md",
                                "cwd": "workspaces/case-a/with-memory",
                                "allowMemory": True,
                            },
                            {
                                "id": "with-onboarding-warm",
                                "promptPath": "prompts/warm.md",
                                "cwd": "workspaces/case-a/with-memory",
                                "allowMemory": True,
                                "providers": [
                                    "grepai-memory",
                                    "codegraphcontext-code",
                                ],
                            },
                        ],
                    }
                ],
            },
        )

        self.assertEqual(
            benchmark_runner.selected_provider_ids(
                case,
                prompt_id="prompt-a",
                variant_id="with-onboarding",
            ),
            (),
        )
        self.assertEqual(
            benchmark_runner.selected_provider_ids(
                case,
                prompt_id="prompt-a",
                variant_id="with-onboarding-warm",
            ),
            ("grepai-memory", "codegraphcontext-code"),
        )
        self.assertEqual(
            benchmark_runner.selected_provider_ids(case),
            ("grepai-memory", "codegraphcontext-code"),
        )

    def test_benchmark_provider_settings_are_generated_without_system_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            coordination_root = root / "ar-coordination"
            source_repo = root / "repos" / "repo-a"
            memory_repo = coordination_root / "memory-repos" / "ar-repo-a"
            source_repo.mkdir(parents=True)
            memory_repo.mkdir(parents=True)
            case = benchmark_runner.BenchmarkCase(
                Path("case.json"),
                {
                    "id": "case-a",
                    "repository": {"name": "repo-a"},
                    "workspace": {"fixturePath": "workspaces/case-a"},
                },
            )

            settings = benchmark_runner.benchmark_lifecycle_settings(
                case=case,
                coordination_root=coordination_root,
                source_repo_root=source_repo,
                memory_repo=memory_repo,
                provider_ids=("grepai-memory", "codegraphcontext-code"),
            )

            self.assertFalse((coordination_root / "system" / "settings.json").exists())
            providers = settings["contextProviders"]["providers"]
            self.assertEqual(
                providers["grepai-memory"]["roots"],
                [{"projectId": "repo-a", "path": memory_repo.resolve().as_posix()}],
            )
            self.assertEqual(
                providers["codegraphcontext-code"]["roots"],
                [{"repoId": "repo-a", "path": source_repo.resolve().as_posix()}],
            )
            instance_id = provider_instance_id("benchmark", coordination_root.parent)
            self.assertEqual(providers["grepai-memory"]["instance"]["id"], instance_id)
            self.assertEqual(providers["grepai-memory"]["instance"]["scope"], "benchmark")
            self.assertEqual(
                providers["codegraphcontext-code"]["instance"]["id"],
                instance_id,
            )
            self.assertEqual(
                providers["codegraphcontext-code"]["instance"]["scope"],
                "benchmark",
            )
            self.assertEqual(
                providers["grepai-memory"]["backend"]["containerName"],
                f"ar-grepai-postgres-{instance_id}",
            )
            self.assertEqual(
                providers["grepai-memory"]["watch"]["logDir"],
                (coordination_root / "logs" / "providers" / "grepai" / instance_id).as_posix(),
            )
            self.assertEqual(
                providers["codegraphcontext-code"]["backend"]["containerName"],
                f"ar-cgc-falkordb-{instance_id}",
            )
            self.assertEqual(
                providers["codegraphcontext-code"]["watch"]["logFileTemplate"],
                (
                    coordination_root
                    / "logs"
                    / "providers"
                    / "codegraphcontext"
                    / instance_id
                    / "<repoId>"
                    / "watch.log"
                ).as_posix(),
            )

    def test_benchmark_provider_setup_uses_generated_settings_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            coordination_root = root / "ar-coordination"
            source_repo = root / "repos" / "repo-a"
            memory_repo = coordination_root / "memory-repos" / "ar-repo-a"
            source_repo.mkdir(parents=True)
            memory_repo.mkdir(parents=True)
            case = benchmark_runner.BenchmarkCase(
                Path("case.json"),
                {
                    "id": "case-a",
                    "repository": {"name": "repo-a"},
                    "workspace": {"fixturePath": "workspaces/case-a"},
                },
            )
            captured: dict[str, Any] = {}

            def fake_run_provider_setup(
                request: benchmark_runner.provider_setup.ProviderSetupRequest,
            ) -> dict[str, object]:
                captured["settings_path"] = request.settings_path
                captured["settings"] = json.loads(request.settings_path.read_text(encoding="utf-8"))
                captured["cgc_seed_repo_id"] = request.cgc_seed.repo_id
                return {"ok": True}

            with mock.patch.object(
                benchmark_runner.provider_setup,
                "run_provider_setup",
                side_effect=fake_run_provider_setup,
            ) as run_provider_setup:
                benchmark_runner.prepare_configured_providers(
                    case,
                    coordination_root,
                    source_repo,
                    memory_repo,
                    dry_run=False,
                    provider_timeout=1,
                    provider_ids=("codegraphcontext-code",),
                    cgc_seed_source_coordination_root=root / "source-coordination",
                    cgc_seed_repo_id="repo-a",
                )

            self.assertEqual(run_provider_setup.call_count, 1)
            settings_path = captured["settings_path"]
            self.assertIsInstance(settings_path, Path)
            self.assertFalse(settings_path.exists())
            settings = captured["settings"]
            self.assertIsInstance(settings, dict)
            self.assertIn(
                "codegraphcontext-code",
                settings["contextProviders"]["providers"],
            )
            self.assertEqual(captured["cgc_seed_repo_id"], "repo-a")

    def test_benchmark_provider_setup_seeds_grepai_from_source_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            coordination_root = root / "ar-coordination"
            source_coordination_root = root / "source-coordination"
            source_settings_path = root / "source-provider-settings.json"
            source_repo = root / "repos" / "repo-a"
            memory_repo = coordination_root / "memory-repos" / "ar-repo-a"
            source_repo.mkdir(parents=True)
            memory_repo.mkdir(parents=True)
            case = benchmark_runner.BenchmarkCase(
                Path("case.json"),
                {
                    "id": "case-a",
                    "repository": {"name": "repo-a"},
                    "workspace": {"fixturePath": "workspaces/case-a"},
                },
            )
            captured: dict[str, Any] = {}

            def fake_run_provider_setup(
                request: benchmark_runner.provider_setup.ProviderSetupRequest,
            ) -> dict[str, object]:
                captured["grepai_seed_source"] = request.grepai_seed.source_coordination_root
                captured["grepai_seed_source_settings"] = request.grepai_seed.source_settings_path
                captured["grepai_seed_project"] = request.grepai_seed.project_id
                captured["grepai_target_memory"] = request.grepai_seed.target_memory_root
                return {"ok": True}

            with mock.patch.object(
                benchmark_runner.provider_setup,
                "run_provider_setup",
                side_effect=fake_run_provider_setup,
            ):
                benchmark_runner.prepare_configured_providers(
                    case,
                    coordination_root,
                    source_repo,
                    memory_repo,
                    dry_run=False,
                    provider_timeout=1,
                    provider_ids=("grepai-memory",),
                    cgc_seed_source_coordination_root=source_coordination_root,
                    cgc_seed_repo_id="repo-a",
                    provider_seed_source_settings_path=source_settings_path,
                )

            self.assertEqual(captured["grepai_seed_source"], source_coordination_root)
            self.assertEqual(captured["grepai_seed_source_settings"], source_settings_path)
            self.assertEqual(captured["grepai_seed_project"], "repo-a")
            self.assertEqual(captured["grepai_target_memory"], memory_repo)

    def test_benchmark_prepare_writes_workspace_mcp_registration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace_root = root / "with-memory"
            coordination_root = workspace_root / "ar-coordination"
            source_repo = workspace_root / "repos" / "repo-a"
            memory_repo = coordination_root / "memory-repos" / "ar-repo-a"
            source_repo.mkdir(parents=True)
            memory_repo.mkdir(parents=True)
            case = benchmark_runner.BenchmarkCase(
                Path("case.json"),
                {
                    "id": "case-a",
                    "repository": {"name": "repo-a"},
                    "workspace": {"fixturePath": "workspaces/case-a"},
                },
            )

            settings_path, config_path = benchmark_runner.write_benchmark_mcp_registration(
                case=case,
                workspace_root=workspace_root,
                coordination_root=coordination_root,
                source_repo_root=source_repo,
                memory_repo=memory_repo,
                provider_ids=("grepai-memory", "codegraphcontext-code"),
                provider_timeout=123,
                dry_run=False,
            )

            self.assertEqual(settings_path.parent, workspace_root / ".codex" / "mcp")
            self.assertEqual(config_path, workspace_root / ".codex" / "config.toml")
            config = load_config(settings_path)
            self.assertEqual(config.coordination_root, coordination_root.resolve())
            self.assertEqual(config.workspace_root, source_repo.parent.resolve())
            self.assertEqual(config.repositories["repo-a"].path, source_repo.resolve())
            self.assertEqual(config.repositories["repo-a"].memory_root, memory_repo.resolve())
            self.assertEqual(
                config.harness_skill_root,
                (workspace_root / ".codex" / "skills").resolve(),
            )
            self.assertEqual(
                config.allowed_provider_ids,
                ("codegraphcontext-code", "grepai-memory"),
            )
            self.assertEqual(config.timeout_caps["providerSetupSeconds"], 123)
            self.assertEqual(config.providers["grepai-memory"].scope, "benchmark")
            self.assertEqual(
                config.providers["grepai-memory"].instance_id,
                provider_instance_id("benchmark", coordination_root.parent),
            )
            config_text = config_path.read_text(encoding="utf-8")
            self.assertIn("[mcp_servers.agents_remember_benchmark]", config_text)
            self.assertIn("agents_remember.mcp", config_text)
            self.assertIn(str(settings_path.resolve()).replace("\\", "\\\\"), config_text)

    def test_codex_command_resolves_from_path_and_declares_benchmark_policy(self) -> None:
        with mock.patch.object(benchmark_runner.shutil, "which", return_value="C:/tools/codex.exe"):
            command = benchmark_runner.codex_command(
                Path("workspace"),
                Path("final.md"),
                codex_sandbox=benchmark_runner.CODEX_SANDBOX_DANGER_FULL_ACCESS,
            )

        self.assertEqual(command[0], "C:/tools/codex.exe")
        self.assertEqual(command[1], "exec")
        self.assertEqual(
            command[command.index("--sandbox") + 1],
            benchmark_runner.CODEX_SANDBOX_DANGER_FULL_ACCESS,
        )
        policy = benchmark_runner.codex_execution_policy(command[0])
        self.assertEqual(policy["scope"], "codex-benchmark-only")
        self.assertEqual(policy["resolution"], "PATH")
        self.assertEqual(policy["pathVariable"], "PATH")
        self.assertEqual(policy["resolvedExecutable"], "C:/tools/codex.exe")
        self.assertTrue(policy["benchmarkOnly"])
        self.assertFalse(policy["genericExecutableOverride"])
        self.assertFalse(policy["arbitraryShell"])

    def test_codex_command_default_sandbox_omits_sandbox_argument(self) -> None:
        with mock.patch.object(benchmark_runner.shutil, "which", return_value="C:/tools/codex.exe"):
            command = benchmark_runner.codex_command(
                Path("workspace"),
                Path("final.md"),
                codex_sandbox=benchmark_runner.CODEX_SANDBOX_DEFAULT,
            )

        self.assertNotIn("--sandbox", command)
        policy = benchmark_runner.codex_execution_policy(
            command[0],
            codex_sandbox=benchmark_runner.CODEX_SANDBOX_DEFAULT,
        )
        self.assertEqual(policy["sandbox"], "default")
        self.assertEqual(policy["sandboxArgument"], "omitted")

    def test_codex_run_metadata_records_benchmark_host_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            case_root = root / "case"
            case_root.mkdir()
            (case_root / "prompt.md").write_text("benchmark prompt\n", encoding="utf-8")
            (case_root / "workspace").mkdir()
            case = benchmark_runner.BenchmarkCase(
                case_root / "case.json",
                {
                    "id": "case-a",
                    "repository": {"name": "repo-a"},
                    "workspace": {"fixturePath": "workspaces/case-a"},
                },
            )
            prompt = {"id": "triage"}
            variant = {"id": "with-onboarding", "promptPath": "prompt.md", "cwd": "workspace"}
            output_root = root / "runs"

            def fake_run(
                command: list[str],
                *,
                input: str,
                stdout: Any,
                stderr: object,
                text: bool,
                check: bool,
            ) -> subprocess.CompletedProcess[str]:
                stdout.write("{}\n")
                return subprocess.CompletedProcess(command, 0)

            with (
                mock.patch.object(
                    benchmark_runner.shutil, "which", return_value="C:/tools/codex.exe"
                ),
                mock.patch.object(benchmark_runner.subprocess, "run", side_effect=fake_run),
            ):
                benchmark_runner.run_one(
                    benchmarks_root=root,
                    case=case,
                    prompt=prompt,
                    variant=variant,
                    repetition=1,
                    output_root=output_root,
                    dry_run=False,
                    codex_sandbox=benchmark_runner.CODEX_SANDBOX_DANGER_FULL_ACCESS,
                )

            metadata = json.loads(
                (output_root / "triage" / "with-onboarding" / "run-001.metadata.json").read_text(
                    encoding="utf-8"
                )
            )
            policy = metadata["codexExecutionPolicy"]
            self.assertEqual(policy["scope"], "codex-benchmark-only")
            self.assertEqual(policy["resolution"], "PATH")
            self.assertEqual(policy["resolvedExecutable"], "C:/tools/codex.exe")
            self.assertEqual(policy["sandbox"], "danger-full-access")
            self.assertTrue(policy["benchmarkOnly"])

    def test_prepare_repo_reuses_cached_commit_without_fetch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            upstream = root / "upstream"
            commit = init_repo(upstream)
            repo_root = root / "workspace" / "repo-a"
            repository = {"url": str(upstream), "commit": commit}
            benchmark_runner.prepare_repo(repository, repo_root, dry_run=False)

            with mock.patch.object(
                benchmark_runner, "run_command", wraps=benchmark_runner.run_command
            ) as run_command:
                benchmark_runner.prepare_repo(repository, repo_root, dry_run=False)

            commands = [call.args[0] for call in run_command.call_args_list]
            command_text = [" ".join(command) for command in commands]
            self.assertFalse(any(" clone " in f" {text} " for text in command_text))
            self.assertFalse(any(" fetch " in f" {text} " for text in command_text))
            self.assertTrue(any(" checkout " in f" {text} " for text in command_text))
            self.assertTrue(any(" reset " in f" {text} " for text in command_text))
            self.assertTrue(any(" clean " in f" {text} " for text in command_text))

    def test_prepare_repo_fetches_when_cached_checkout_lacks_commit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            upstream = root / "upstream"
            first_commit = init_repo(upstream)
            repo_root = root / "workspace" / "repo-a"
            benchmark_runner.prepare_repo(
                {"url": str(upstream), "commit": first_commit}, repo_root, dry_run=False
            )
            second_commit = commit_file(upstream, "feature.txt", "feature\n", "Add feature")

            with mock.patch.object(
                benchmark_runner, "run_command", wraps=benchmark_runner.run_command
            ) as run_command:
                benchmark_runner.prepare_repo(
                    {"url": str(upstream), "commit": second_commit}, repo_root, dry_run=False
                )

            commands = [call.args[0] for call in run_command.call_args_list]
            command_text = [" ".join(command) for command in commands]
            self.assertFalse(any(" clone " in f" {text} " for text in command_text))
            self.assertTrue(any(" fetch " in f" {text} " for text in command_text))
            self.assertEqual(git(repo_root, "rev-parse", "HEAD"), second_commit)

    def test_prepare_repo_force_clone_discards_cached_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            upstream = root / "upstream"
            commit = init_repo(upstream)
            repo_root = root / "workspace" / "repo-a"
            repository = {"url": str(upstream), "commit": commit}
            benchmark_runner.prepare_repo(repository, repo_root, dry_run=False)

            with (
                mock.patch.object(
                    benchmark_runner, "remove_path", wraps=benchmark_runner.remove_path
                ) as remove_path,
                mock.patch.object(
                    benchmark_runner, "run_command", wraps=benchmark_runner.run_command
                ) as run_command,
            ):
                benchmark_runner.prepare_repo(
                    repository, repo_root, dry_run=False, force_clone=True
                )

            commands = [call.args[0] for call in run_command.call_args_list]
            command_text = [" ".join(command) for command in commands]
            self.assertTrue(remove_path.called)
            self.assertTrue(any(" clone " in f" {text} " for text in command_text))
            self.assertTrue((repo_root / ".git").exists())

    def test_skill_exposure_copy_mode_copies_skill_tree_without_bash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            coordination_root = workspace / "ar-coordination"
            skill_dir = coordination_root / "skills" / "U-01-core-skills" / "C-00-test-skill"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                "---\nname: c-00-test-skill\n---\n", encoding="utf-8"
            )

            benchmark_runner.sync_workspace_skill_exposure(
                workspace,
                coordination_root,
                dry_run=False,
                mode="copy",
            )

            exposed = workspace / ".codex" / "skills" / benchmark_runner.SKILLS_EXPOSURE_NAMESPACE
            self.assertTrue(
                (exposed / "U-01-core-skills" / "C-00-test-skill" / "SKILL.md").is_file()
            )

    def test_skill_exposure_default_copies_skill_tree_without_script(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            coordination_root = workspace / "ar-coordination"
            skill_dir = coordination_root / "skills" / "U-01-core-skills" / "C-00-test-skill"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                "---\nname: c-00-test-skill\n---\n", encoding="utf-8"
            )

            benchmark_runner.sync_workspace_skill_exposure(
                workspace,
                coordination_root,
                dry_run=False,
            )

            exposed = workspace / ".codex" / "skills" / benchmark_runner.SKILLS_EXPOSURE_NAMESPACE
            self.assertTrue(
                (exposed / "U-01-core-skills" / "C-00-test-skill" / "SKILL.md").is_file()
            )


class RequireUpdatedSidecarContentTests(unittest.TestCase):
    def _setup(self, tmp: Path) -> tuple[Path, Path, OnboardingRefreshPlan]:
        memory_repo = tmp / "memory"
        init_repo(memory_repo)
        onboarding_root = memory_repo / "onboarding"
        write_file_onboarding(onboarding_root, "demo-repo", "src/app.py", "0" * 40)
        git(memory_repo, "add", "-A")
        git(memory_repo, "commit", "-m", "Add sidecar")
        sidecar = onboarding_root / "src" / "app.py.md"
        plan: OnboardingRefreshPlan = {
            "required": [{"source_path": "src/app.py", "onboarding_file": sidecar.as_posix()}],
            "missing": [],
            "unsupported": [],
            "unonboarded": [],
        }
        return memory_repo, sidecar, plan

    @staticmethod
    def _append(sidecar: Path, text: str) -> None:
        sidecar.write_text(sidecar.read_text(encoding="utf-8") + text, encoding="utf-8")

    def test_blocks_when_changed_source_sidecar_not_updated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            memory_repo, _sidecar, plan = self._setup(Path(tmp_dir))
            # Sidecar is committed and untouched: a metadata-only refresh would be stale.
            with self.assertRaises(RuntimeError) as caught:
                require_updated_sidecar_content(None, plan, memory_tree=memory_repo)
            self.assertIn("src/app.py", str(caught.exception))

    def test_blocks_metadata_only_edit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            memory_repo, sidecar, plan = self._setup(Path(tmp_dir))
            sidecar.write_text(
                sidecar.read_text(encoding="utf-8").replace("0" * 40, "1" * 40),
                encoding="utf-8",
            )
            with self.assertRaises(RuntimeError) as caught:
                require_updated_sidecar_content(None, plan, memory_tree=memory_repo)
            self.assertIn("metadata/history-only", str(caught.exception))

    def test_blocks_history_only_edit_without_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            memory_repo, sidecar, plan = self._setup(Path(tmp_dir))
            self._append(sidecar, "\n## Update History\n\n- 2026-06-10T04:00 — Stamped.\n")
            with self.assertRaises(RuntimeError) as caught:
                require_updated_sidecar_content(None, plan, memory_tree=memory_repo)
            self.assertIn("metadata/history-only", str(caught.exception))
            self.assertIn("No content impact:", str(caught.exception))

    def test_passes_history_only_edit_with_no_impact_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            memory_repo, sidecar, plan = self._setup(Path(tmp_dir))
            self._append(
                sidecar,
                "\n## Update History\n\n"
                "- 2026-06-10T04:00 — No content impact: version bump only; body verified.\n",
            )
            attested = require_updated_sidecar_content(None, plan, memory_tree=memory_repo)
            self.assertEqual(attested, ["src/app.py"])

    def test_blocks_body_update_without_history_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            memory_repo, sidecar, plan = self._setup(Path(tmp_dir))
            self._append(sidecar, "\nUpdated body.\n")
            with self.assertRaises(RuntimeError) as caught:
                require_updated_sidecar_content(None, plan, memory_tree=memory_repo)
            self.assertIn("without a new Update History entry", str(caught.exception))

    def test_passes_when_body_and_history_updated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            memory_repo, sidecar, plan = self._setup(Path(tmp_dir))
            self._append(
                sidecar,
                "\nUpdated body.\n\n## Update History\n\n"
                "- 2026-06-10T04:00 — Documented the new retry contract.\n",
            )
            attested = require_updated_sidecar_content(None, plan, memory_tree=memory_repo)
            self.assertEqual(attested, [])

    def test_passes_new_untracked_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            memory_repo, _sidecar, _plan = self._setup(Path(tmp_dir))
            onboarding_root = memory_repo / "onboarding"
            write_file_onboarding(onboarding_root, "demo-repo", "src/new.py", "0" * 40)
            new_sidecar = onboarding_root / "src" / "new.py.md"
            plan: OnboardingRefreshPlan = {
                "required": [
                    {"source_path": "src/new.py", "onboarding_file": new_sidecar.as_posix()}
                ],
                "missing": [],
                "unsupported": [],
                "unonboarded": [],
            }
            attested = require_updated_sidecar_content(None, plan, memory_tree=memory_repo)
            self.assertEqual(attested, [])

    def test_passes_committed_sidecar_update_against_verified_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            memory_repo, sidecar, plan = self._setup(Path(tmp_dir))
            verified = git(memory_repo, "rev-parse", "HEAD")
            self._append(
                sidecar,
                "\nUpdated body.\n\n## Update History\n\n"
                "- 2026-06-12T18:00 — Documented the merged change.\n",
            )
            git(memory_repo, "add", "-A")
            git(memory_repo, "commit", "-m", "Update sidecar before closeout")
            attested = require_updated_sidecar_content(
                None, plan, memory_tree=memory_repo, memory_verified_commit=verified
            )
            self.assertEqual(attested, [])

    def test_blocks_committed_sidecar_unchanged_since_verified_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            memory_repo, _sidecar, plan = self._setup(Path(tmp_dir))
            verified = git(memory_repo, "rev-parse", "HEAD")
            (memory_repo / "unrelated.md").write_text("# Unrelated\n", encoding="utf-8")
            git(memory_repo, "add", "-A")
            git(memory_repo, "commit", "-m", "Unrelated memory change")
            with self.assertRaises(RuntimeError) as caught:
                require_updated_sidecar_content(
                    None, plan, memory_tree=memory_repo, memory_verified_commit=verified
                )
            self.assertIn("src/app.py", str(caught.exception))

    def test_passes_new_sidecar_committed_after_verified_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            memory_repo, _sidecar, _plan = self._setup(Path(tmp_dir))
            verified = git(memory_repo, "rev-parse", "HEAD")
            onboarding_root = memory_repo / "onboarding"
            write_file_onboarding(onboarding_root, "demo-repo", "src/new.py", "0" * 40)
            git(memory_repo, "add", "-A")
            git(memory_repo, "commit", "-m", "Add sidecar before closeout")
            new_sidecar = onboarding_root / "src" / "new.py.md"
            plan: OnboardingRefreshPlan = {
                "required": [
                    {"source_path": "src/new.py", "onboarding_file": new_sidecar.as_posix()}
                ],
                "missing": [],
                "unsupported": [],
                "unonboarded": [],
            }
            attested = require_updated_sidecar_content(
                None, plan, memory_tree=memory_repo, memory_verified_commit=verified
            )
            self.assertEqual(attested, [])

    def test_noop_when_no_required_sidecars(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            memory_repo, _sidecar, _plan = self._setup(Path(tmp_dir))
            attested = require_updated_sidecar_content(
                None,
                {"required": [], "missing": [], "unsupported": [], "unonboarded": []},
                memory_tree=memory_repo,
            )
            self.assertEqual(attested, [])


class RequireUpdatedRouteOverviewContentTests(unittest.TestCase):
    """The route-overview body gate: nearest-governing routes are domain-evident."""

    CHANGED = ("src/app/feature.py",)

    def _setup(self, tmp: Path) -> tuple[Path, dict[str, Path]]:
        memory_repo = tmp / "memory"
        init_repo(memory_repo)
        onboarding_root = memory_repo / "onboarding"
        overviews = {
            ".": write_route_overview(onboarding_root, "demo-repo", ".", "0" * 40),
            "src/app": write_route_overview(onboarding_root, "demo-repo", "src/app", "0" * 40),
        }
        git(memory_repo, "add", "-A")
        git(memory_repo, "commit", "-m", "Add route overviews")
        return memory_repo, overviews

    def _plan(self, overviews: dict[str, Path]) -> RouteOverviewRefreshPlan:
        return {
            "required": [
                {"source_route": route, "onboarding_file": path.as_posix()}
                for route, path in overviews.items()
            ],
            "missing_metadata": [],
        }

    @staticmethod
    def _append(path: Path, text: str) -> None:
        path.write_text(path.read_text(encoding="utf-8") + text, encoding="utf-8")

    def test_blocks_stale_nearest_governing_overview(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            memory_repo, overviews = self._setup(Path(tmp_dir))
            with self.assertRaises(RuntimeError) as caught:
                require_updated_route_overview_content(
                    None, self._plan(overviews), list(self.CHANGED), memory_tree=memory_repo
                )
            self.assertIn("src/app", str(caught.exception))
            self.assertIn("No route impact:", str(caught.exception))

    def test_ancestor_overview_is_reported_not_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            memory_repo, overviews = self._setup(Path(tmp_dir))
            self._append(
                overviews["src/app"],
                "\nRoute behavior notes.\n\n## Update History\n\n"
                "- 2026-06-10T04:00 — Documented the route change.\n",
            )
            attested = require_updated_route_overview_content(
                None, self._plan(overviews), list(self.CHANGED), memory_tree=memory_repo
            )
            self.assertEqual(attested, [])
            gate = classify_route_overview_updates(
                None, self._plan(overviews), list(self.CHANGED), memory_tree=memory_repo
            )
            self.assertEqual(gate["stamped_without_body_review"], ["."])
            self.assertEqual(gate["stale"], [])

    def test_marker_attests_nearest_governing_overview(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            memory_repo, overviews = self._setup(Path(tmp_dir))
            self._append(
                overviews["src/app"],
                "\n## Update History\n\n"
                "- 2026-06-10T04:00 — No route impact: reviewed, file-local fix.\n",
            )
            attested = require_updated_route_overview_content(
                None, self._plan(overviews), list(self.CHANGED), memory_tree=memory_repo
            )
            self.assertEqual(attested, ["src/app"])

    def test_blocks_overview_body_update_without_history_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            memory_repo, overviews = self._setup(Path(tmp_dir))
            self._append(overviews["src/app"], "\nRoute behavior notes.\n")
            with self.assertRaises(RuntimeError) as caught:
                require_updated_route_overview_content(
                    None, self._plan(overviews), list(self.CHANGED), memory_tree=memory_repo
                )
            self.assertIn("without a new Update History entry", str(caught.exception))

    def test_root_overview_gates_when_it_is_the_nearest_governor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            memory_repo, overviews = self._setup(Path(tmp_dir))
            with self.assertRaises(RuntimeError) as caught:
                require_updated_route_overview_content(
                    None, self._plan(overviews), ["README.md"], memory_tree=memory_repo
                )
            self.assertIn("have an unmodified", str(caught.exception))

    def test_noop_when_no_required_overviews(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            memory_repo, _overviews = self._setup(Path(tmp_dir))
            attested = require_updated_route_overview_content(
                None,
                {"required": [], "missing_metadata": []},
                list(self.CHANGED),
                memory_tree=memory_repo,
            )
            self.assertEqual(attested, [])


if __name__ == "__main__":
    unittest.main()
