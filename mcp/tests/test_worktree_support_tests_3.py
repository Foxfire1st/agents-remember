from __future__ import annotations

import io
import json
import tempfile
from argparse import Namespace
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

from agents_remember.application.memory_tools import (
    CarryoverCommitMessages,
    CarryoverSelection,
    memory_carryover_apply_tool,
)
from agents_remember.kernel import coordination_context_resolver as resolver
from agents_remember.kernel.coordination_context.models import CoordinationRequest
from agents_remember.kernel.coordination_context_resolver import CoordinationHints
from agents_remember.kernel.memory_init import initialize_memory
from agents_remember.kernel.memory_ledger import (
    create_initial_ledger,
    find_mapping,
    load_ledger,
    parse_ledger_text,
    write_ledger,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, load_config
from agents_remember.memory import baseline as adopt_baseline
from agents_remember.memory import carryover as memory_carryover
from agents_remember.worktrees.integration.integration_ref_transaction import (
    IntegratedCommits,
    IntegrationSources,
    prepare_integration_ref_move,
)
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.contract_reader import WorktreeContractReader
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    LeafIdentity,
    RepoBranchPlan,
    default_contract,
    write_contract,
)
from test_worktree_support import (
    WorktreeSupportTests,
    commit_file,
    drift,
    git,
    init_repo,
    initialized_memory_repo,
    read_onboarding_field,
    write_entity_catalog,
    write_file_onboarding,
    write_route_overview,
)


def _carryover_config(workspace: Path) -> McpRuntimeConfig:
    config_path = workspace / "settings.json"
    config_path.write_text(
        json.dumps(
            {
                "version": 1,
                "coordinationRoot": (workspace / "ar-coordination").as_posix(),
                "workspaceRoot": workspace.as_posix(),
                "repositories": {"repo-a": {}},
            }
        ),
        encoding="utf-8",
    )
    return load_config(config_path)


def _apply_memory_carryover(
    config: McpRuntimeConfig,
    args: Namespace,
) -> dict[str, Any]:
    return memory_carryover_apply_tool(
        config,
        CarryoverSelection(
            repo_id=args.code_repository_name,
            contract_path=args.contract_path.as_posix(),
            source_memory=args.source_memory.as_posix(),
            official_code_ref=args.official_code_ref,
            source_code_ref=args.source_code_ref,
            old_base=args.old_base,
            replace_existing=args.replace_existing,
        ),
        intent_note=args.approval_note,
        include_review_required=args.include_review_required,
        messages=CarryoverCommitMessages(
            memory=args.memory_commit_message,
            ledger=args.ledger_commit_message,
        ),
    )


def _carryover_target(
    workspace: Path,
    code_repo: Path,
    memory_repo: Path,
    code_tip: str,
):
    contract = default_contract(
        ContractTask(
            name="carryover-recovery",
            repo_name="repo-a",
            coordination_root=workspace / "ar-coordination",
            workflow_kind="light-task",
            memory_mode="external",
        ),
        leaf=LeafIdentity(worktree_name="carryover-recovery", leaf_id="CARRYOVER-RECOVERY"),
        code=RepoBranchPlan(
            repo_path=code_repo,
            source_branch="main",
            work_branch="carryover-recovery",
            base_commit=code_tip,
        ),
        memory=RepoBranchPlan(
            repo_path=memory_repo,
            source_branch="main",
            work_branch="carryover-recovery",
            base_commit=git(memory_repo, "rev-parse", "main"),
        ),
    )
    git(
        code_repo,
        "worktree",
        "add",
        "-b",
        contract.code_work_branch,
        str(contract.code_worktree),
        "main",
    )
    assert contract.memory_worktree is not None
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


class WorktreeSupport3(WorktreeSupportTests):
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
                    request=CoordinationRequest(
                        hints=CoordinationHints(coordination_root=coordination_root),
                        contract_reader=WorktreeContractReader(),
                    ),
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
                    request=CoordinationRequest(
                        hints=CoordinationHints(coordination_root=workspace / "ar-coordination"),
                        contract_reader=WorktreeContractReader(),
                    ),
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

    def test_memory_init_unborn_default_adopts_the_first_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            code_head = init_repo(workspace / "repo-a", "main")
            config = _carryover_config(workspace)
            initialized = initialize_memory(config, repo_id="repo-a")
            self.assertTrue(initialized["ok"])
            memory_root = config.repositories["repo-a"].memory_root
            assert memory_root is not None
            write_file_onboarding(
                memory_root / "onboarding",
                "repo-a",
                "README.md",
                code_head,
            )
            context = adopt_baseline.resolve_request_context(
                adopt_baseline.BaselineRequest(
                    code_repository_name="repo-a",
                    workspace_root=workspace,
                    coordination_root=config.coordination_root,
                )
            )

            result = adopt_baseline.adopt_initial_baseline(context, "main", "main")

            self.assertEqual(result["state"], "adopted-baseline")
            ledger = parse_ledger_text((memory_root / "memory.md").read_text(encoding="utf-8"))
            self.assertEqual(ledger.last_verified_code_commit, code_head)
            self.assertEqual(git(memory_root, "branch", "--show-current"), "main")

    def test_unborn_baseline_refuses_missing_or_mismatched_init_authority(self) -> None:
        for mode in ("missing", "mismatched"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                workspace = Path(tmp)
                code_head = init_repo(workspace / "repo-a", "main")
                config = _carryover_config(workspace)
                initialized = initialize_memory(config, repo_id="repo-a")
                self.assertTrue(initialized["ok"])
                memory_root = config.repositories["repo-a"].memory_root
                assert memory_root is not None
                write_file_onboarding(
                    memory_root / "onboarding",
                    "repo-a",
                    "README.md",
                    code_head,
                )
                if mode == "missing":
                    git(memory_root, "config", "--unset", "agents-remember.defaultBranch")
                else:
                    git(memory_root, "config", "agents-remember.defaultBranch", "trunk")
                before = {
                    path.relative_to(memory_root).as_posix(): path.read_bytes()
                    for path in sorted(memory_root.rglob("*"))
                    if path.is_file() and ".git" not in path.parts
                }
                context = adopt_baseline.resolve_request_context(
                    adopt_baseline.BaselineRequest(
                        code_repository_name="repo-a",
                        workspace_root=workspace,
                        coordination_root=config.coordination_root,
                    )
                )

                with self.assertRaisesRegex(
                    RuntimeError,
                    "explicit default-branch authority|exact unborn default branch",
                ):
                    adopt_baseline.adopt_initial_baseline(context, "main", "main")

                after = {
                    path.relative_to(memory_root).as_posix(): path.read_bytes()
                    for path in sorted(memory_root.rglob("*"))
                    if path.is_file() and ".git" not in path.parts
                }
                self.assertEqual(after, before)
                self.assertFalse((memory_root / "memory.md").exists())

    def test_adopt_memory_baseline_cannot_commit_an_integration_branch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            init_repo(workspace / "repo-a", "main")
            memory_root = workspace / "ar-coordination" / "memory-repos" / "ar-repo-a"
            init_repo(memory_root, "main")
            (memory_root / "docs").mkdir()
            (memory_root / "docs" / ".gitkeep").write_text("", encoding="utf-8")
            git(memory_root, "checkout", "-b", "super")
            request = adopt_baseline.BaselineRequest(
                code_repository_name="repo-a",
                workspace_root=workspace,
                coordination_root=workspace / "ar-coordination",
            )
            context = adopt_baseline.resolve_request_context(request)

            with self.assertRaisesRegex(RuntimeError, "bootstrap-only exception"):
                adopt_baseline.adopt_initial_baseline(context, "main", "super")

            self.assertFalse((memory_root / "memory.md").exists())
            self.assertEqual(git(memory_root, "branch", "--show-current"), "super")

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
            config = _carryover_config(workspace)
            target = _carryover_target(workspace, code_repo, official_memory, official_head)
            assert target.memory_worktree is not None
            official_memory = target.memory_worktree

            args = Namespace(
                config_path=config.config_path,
                code_repository_root=code_repo,
                official_code_ref="main",
                source_code_ref="workbench/reado/v1.2",
                old_base=old_base,
                target_memory=official_memory,
                source_memory=source_memory,
                code_repository_name="repo-a",
                contract_path=target.contract_path,
                replace_existing=False,
                approved=True,
                approval_note="developer approved c-11-memory-carryover-from-branch carryover",
                include_review_required=[],
                memory_commit_message="Carry over landed memory",
                ledger_commit_message="Record carryover ledger",
            )
            plan: dict[str, Any] = memory_carryover.build_plan(args)
            self.assertEqual(plan["counts"], {"auto-carry": 1})

            payload = _apply_memory_carryover(config, args)
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
            config = _carryover_config(workspace)
            target = _carryover_target(
                workspace,
                code_repo,
                official_memory,
                git(code_repo, "rev-parse", "main"),
            )
            assert target.memory_worktree is not None
            official_memory = target.memory_worktree

            args = Namespace(
                config_path=config.config_path,
                code_repository_root=code_repo,
                official_code_ref="main",
                source_code_ref="workbench/reado/v1.2",
                old_base=old_base,
                target_memory=official_memory,
                source_memory=source_memory,
                code_repository_name="repo-a",
                contract_path=target.contract_path,
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
            payload = _apply_memory_carryover(config, args)
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
            config = _carryover_config(workspace)
            target = _carryover_target(workspace, code_repo, official_memory, official_head)
            assert target.memory_worktree is not None
            official_memory = target.memory_worktree

            ledger_before = load_ledger(official_memory / "memory.md")
            self.assertIsNone(find_mapping(ledger_before, official_head))

            args = Namespace(
                config_path=config.config_path,
                code_repository_root=code_repo,
                official_code_ref="main",
                source_code_ref="workbench/reado/v1",
                old_base=old_base,
                target_memory=official_memory,
                source_memory=source_memory,
                code_repository_name="repo-a",
                contract_path=target.contract_path,
                replace_existing=False,
                approved=True,
                approval_note="developer approved c-11-memory-carryover-from-branch carryover",
                include_review_required=[],
                memory_commit_message="Carry over landed memory",
                ledger_commit_message="Record carryover ledger",
            )
            payload = _apply_memory_carryover(config, args)
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
                config_path=workspace / "settings.json",
                contract_path=workspace / "series-contract.md",
                code_repository_root=code_repo,
                official_code_ref="main",
                source_code_ref="workbench/reado/v1.2",
                old_base=old_base,
                target_memory=official_memory,
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
                config_path=workspace / "settings.json",
                contract_path=workspace / "series-contract.md",
                code_repository_root=code_repo,
                official_code_ref="main",
                source_code_ref="workbench/reado/v1.2",
                old_base=old_base,
                target_memory=official_memory,
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
                ContractTask(
                    name="Atomic Integrate",
                    repo_name="repo-a",
                    coordination_root=root / "ar-coordination",
                    workflow_kind="chat-task",
                    memory_mode="disabled",
                ),
                leaf=LeafIdentity(worktree_name="atomic-integrate"),
                code=RepoBranchPlan(
                    repo_path=code_repo,
                    source_branch="main",
                    work_branch="ar/atomic-integrate",
                    base_commit=base,
                ),
            )
            with (
                mock.patch(
                    "agents_remember.worktrees.integration.integration_ref_transaction."
                    "require_authorized_integration_commits"
                ),
                mock.patch(
                    "agents_remember.worktrees.integration.integration_ref_transaction.integration_targets",
                    return_value=(SimpleNamespace(side="code", branch="main"),),
                ),
                self.assertRaisesRegex(RuntimeError, "not a fast-forward"),
            ):
                prepare_integration_ref_move(
                    contract,
                    IntegratedCommits(code=divergent, memory_content="", ledger=""),
                    WorktreeArgs(),
                    IntegrationSources(
                        current_code_source=head_before,
                        current_memory_source="",
                        code_replay_required=True,
                        memory_replay_required=False,
                    ),
                )
            self.assertEqual(git(code_repo, "rev-parse", "HEAD"), head_before)
