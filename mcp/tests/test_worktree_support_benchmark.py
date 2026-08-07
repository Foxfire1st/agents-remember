from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from agents_remember.benchmarks import runner as benchmark_runner
from agents_remember.mcp.config import load_config
from agents_remember.providers.identity import provider_instance_id
from agents_remember.worktrees.modules.models import OnboardingRefreshPlan, RouteOverviewRefreshPlan
from agents_remember.worktrees.modules.onboarding import (
    classify_route_overview_updates,
    require_updated_route_overview_content,
    require_updated_sidecar_content,
)
from test_worktree_support import (
    _benchmark_git_subcommands,
    commit_file,
    git,
    init_repo,
    write_file_onboarding,
    write_route_overview,
)


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

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_worktree_support_benchmark.py:59).
    def test_benchmark_safe_remove_deletes_readonly_tree(self) -> None:  # pragma: no cover
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

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_worktree_support_benchmark.py:74).
    def test_benchmark_safe_remove_deletes_directory_symlink_not_target(
        self,
    ) -> None:  # pragma: no cover
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

            # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_worktree_support_benchmark.py:97).
            def fake_which(command: str) -> str | None:  # pragma: no cover
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
                benchmark_runner.BenchmarkWorkspace(
                    case=case,
                    workspace_root=coordination_root.parent,
                    coordination_root=coordination_root,
                    source_repo_root=source_repo,
                    memory_repo=memory_repo,
                    provider_ids=("grepai-memory", "codegraphcontext-code"),
                )
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
                return {"ok": True}

            with mock.patch.object(
                benchmark_runner.provider_setup,
                "run_provider_setup",
                side_effect=fake_run_provider_setup,
            ) as run_provider_setup:
                benchmark_runner.prepare_configured_providers(
                    benchmark_runner.BenchmarkWorkspace(
                        case=case,
                        workspace_root=coordination_root.parent,
                        coordination_root=coordination_root,
                        source_repo_root=source_repo,
                        memory_repo=memory_repo,
                        provider_ids=("codegraphcontext-code",),
                    ),
                    dry_run=False,
                    provider_timeout=1,
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

    def test_benchmark_provider_setup_is_hermetic_no_seed(self) -> None:
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
                captured["grepai_seed_source"] = request.grepai_seed.source_coordination_root
                captured["cgc_seed_source"] = request.cgc_seed.source_coordination_root
                return {"ok": True}

            with mock.patch.object(
                benchmark_runner.provider_setup,
                "run_provider_setup",
                side_effect=fake_run_provider_setup,
            ):
                benchmark_runner.prepare_configured_providers(
                    benchmark_runner.BenchmarkWorkspace(
                        case=case,
                        workspace_root=coordination_root.parent,
                        coordination_root=coordination_root,
                        source_repo_root=source_repo,
                        memory_repo=memory_repo,
                        provider_ids=("grepai-memory", "codegraphcontext-code"),
                    ),
                    dry_run=False,
                    provider_timeout=1,
                )

            # Hermetic-cold: the benchmark wires no seed source for either provider.
            self.assertIsNone(captured["grepai_seed_source"])
            self.assertIsNone(captured["cgc_seed_source"])

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
                benchmark_runner.BenchmarkWorkspace(
                    case=case,
                    workspace_root=workspace_root,
                    coordination_root=coordination_root,
                    source_repo_root=source_repo,
                    memory_repo=memory_repo,
                    provider_ids=("grepai-memory", "codegraphcontext-code"),
                ),
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

    def test_codex_command_forwards_benchmark_mcp_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            codex_root = workspace / ".codex"
            codex_root.mkdir(parents=True)
            (codex_root / "config.toml").write_text(
                "\n".join(
                    [
                        "[mcp_servers.agents_remember_benchmark]",
                        'command = "/tmp/python"',
                        'args = ["-m", "agents_remember.mcp", "--config", "/tmp/settings.json"]',
                        "startup_timeout_sec = 120",
                        "",
                        "[mcp_servers.agents_remember_benchmark.env]",
                        'PYTHONIOENCODING = "utf-8"',
                        'PYTHONPATH = "/tmp/mcp/src"',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            with mock.patch.object(
                benchmark_runner.shutil, "which", return_value="C:/tools/codex.exe"
            ):
                command = benchmark_runner.codex_command(
                    workspace,
                    root / "final.md",
                    codex_sandbox=benchmark_runner.CODEX_SANDBOX_DANGER_FULL_ACCESS,
                )

        config_overrides = [
            command[index + 1] for index, value in enumerate(command) if value == "-c"
        ]
        self.assertIn(
            'mcp_servers.agents_remember_benchmark.command="/tmp/python"',
            config_overrides,
        )
        self.assertIn(
            'mcp_servers.agents_remember_benchmark.args=["-m","agents_remember.mcp","--config","/tmp/settings.json"]',
            config_overrides,
        )
        self.assertIn(
            "mcp_servers.agents_remember_benchmark.startup_timeout_sec=120",
            config_overrides,
        )
        self.assertIn(
            'mcp_servers.agents_remember_benchmark.env.PYTHONIOENCODING="utf-8"',
            config_overrides,
        )
        self.assertIn(
            'mcp_servers.agents_remember_benchmark.env.PYTHONPATH="/tmp/mcp/src"',
            config_overrides,
        )

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

            with (
                mock.patch.object(
                    benchmark_runner.shutil, "which", return_value="C:/tools/codex.exe"
                ),
                # The run itself is not under test here -- only the metadata the runner writes
                # around it -- so the child exits 0 and writes nothing to the JSONL it was given.
                mock.patch.object(
                    benchmark_runner.subprocess,
                    "run",
                    return_value=subprocess.CompletedProcess([], 0),
                ),
            ):
                benchmark_runner.run_one(
                    benchmark_runner.BenchmarkRun(
                        benchmarks_root=root,
                        case=case,
                        output_root=output_root,
                        dry_run=False,
                        codex_sandbox=benchmark_runner.CODEX_SANDBOX_DANGER_FULL_ACCESS,
                    ),
                    benchmark_runner.BenchmarkTask(
                        prompt=prompt,
                        variant=variant,
                        repetition=1,
                    ),
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
                benchmark_runner, "run_git_command", wraps=benchmark_runner.run_git_command
            ) as run_git_command:
                benchmark_runner.prepare_repo(repository, repo_root, dry_run=False)

            subcommands = _benchmark_git_subcommands(run_git_command)
            self.assertNotIn("clone", subcommands)
            self.assertNotIn("fetch", subcommands)
            self.assertIn("checkout", subcommands)
            self.assertIn("reset", subcommands)
            self.assertIn("clean", subcommands)

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
                benchmark_runner, "run_git_command", wraps=benchmark_runner.run_git_command
            ) as run_git_command:
                benchmark_runner.prepare_repo(
                    {"url": str(upstream), "commit": second_commit}, repo_root, dry_run=False
                )

            subcommands = _benchmark_git_subcommands(run_git_command)
            self.assertNotIn("clone", subcommands)
            self.assertIn("fetch", subcommands)
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
                    benchmark_runner, "run_git_command", wraps=benchmark_runner.run_git_command
                ) as run_git_command,
            ):
                benchmark_runner.prepare_repo(
                    repository, repo_root, dry_run=False, force_clone=True
                )

            self.assertTrue(remove_path.called)
            self.assertIn("clone", _benchmark_git_subcommands(run_git_command))
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
            self._append(sidecar, "\n## Update History\n\n- 2026-06-10T04:00+02:00 — Stamped.\n")
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
                "- 2026-06-10T04:00+02:00 — No content impact: version bump only; body verified.\n",
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
                "- 2026-06-10T04:00+02:00 — Documented the new retry contract.\n",
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
                "- 2026-06-12T18:00+02:00 — Documented the merged change.\n",
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
                "- 2026-06-10T04:00+02:00 — Documented the route change.\n",
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
                "- 2026-06-10T04:00+02:00 — No route impact: reviewed, file-local fix.\n",
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
