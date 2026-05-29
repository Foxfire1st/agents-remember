from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mcp.client.stdio import stdio_client
from mcp.types import TextContent

from mcp import ClientSession, StdioServerParameters

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
MCP_TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(MCP_SRC))
sys.path.insert(0, str(MCP_TESTS))

from agents_remember.benchmarks import runner as benchmark_runner
from agents_remember.mcp.config import load_config
from agents_remember.mcp.server import create_server
from agents_remember.mcp.tools import (
    PUBLIC_TOOLS,
    cgc_callees_payload,
    cgc_callers_payload,
    cgc_complexity_payload,
    cgc_dependencies_payload,
    cgc_symbol_search_payload,
    codex_benchmark_prepare_payload,
    codex_benchmark_run_payload,
    context_packet_payload,
    grepai_search_payload,
    grepai_trace_payload,
    memory_baseline_status_payload,
    memory_carryover_plan_payload,
    memory_init_payload,
    ping_payload,
    provider_diagnostics_payload,
    provider_watchers_payload,
    route_index_refresh_payload,
    runtime_install_payload,
    server_info_payload,
    skills_install_payload,
)
from agents_remember.providers.integrity import (
    check_provider_runner_integrity,
    manifest_path_for_config,
)
from agents_remember.providers.settings import lifecycle_settings_from_config
from test_config import settings_payload
from test_provider_current_state import ready_status_payload


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def planned_command(payload: dict) -> list[str]:
    return payload["command"]["command"]


def command_after(command: list[str], token: str) -> list[str]:
    return command[command.index(token) + 1 :]


def grepai_workspace(config) -> str:
    settings = lifecycle_settings_from_config(config)
    return settings["contextProviders"]["providers"]["grepai-memory"]["workspace"]


class McpToolTests(unittest.TestCase):
    def test_ping_payload(self) -> None:
        payload = ping_payload()

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["server"], "agents-remember")
        self.assertEqual(payload["version"], "0.2.0")
        self.assertEqual(payload["transport"], "stdio")
        self.assertEqual(payload["tokens"], 0)

    def test_server_info_payload_reports_safe_config_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            path = root / ".codex" / "mcp" / "settings.json"
            write_json(path, settings_payload(root))
            config = load_config(path)

            payload = server_info_payload(config)

            self.assertTrue(payload["ok"])
            self.assertEqual(payload["server"], "agents-remember")
            self.assertEqual(payload["transport"], "stdio")
            self.assertEqual(payload["configPath"], path.resolve().as_posix())
            self.assertEqual(
                payload["harnessSkillRoot"],
                (root / ".codex" / "skills").as_posix(),
            )
            self.assertEqual(payload["allowedRepoIds"], ["agents-remember-md"])
            self.assertEqual(
                payload["allowedProviderIds"],
                ["codegraphcontext-code", "grepai-memory"],
            )
            self.assertEqual(
                payload["tools"],
                list(PUBLIC_TOOLS),
            )
            self.assertEqual(payload["reservedTools"], [])

    def test_server_constructs_with_context_packet_tool(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            path = root / ".codex" / "mcp" / "settings.json"
            write_json(path, settings_payload(root))
            config = load_config(path)

            server = create_server(config)

            self.assertIsNotNone(server)

    def test_context_packet_tool_delegates_to_controller(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            initialize_context_fixture(root)
            path = root / "mcp-settings.json"
            write_json(path, settings_payload(root))
            config = load_config(path)

            payload = context_packet_payload(config, "agents-remember-md")

            self.assertTrue(payload["ok"])
            self.assertEqual(payload["operation"], "context_packet")
            self.assertEqual(payload["contextPacketVersion"], 2)
            self.assertEqual(payload["repo"]["id"], "agents-remember-md")
            self.assertNotIn("repoId", payload)
            self.assertEqual(payload["providers"]["state"], "failed")
            self.assertNotIn("currentState", payload["providers"])
            self.assertEqual(payload["drift"], {"status": "notChecked"})

    def test_provider_diagnostics_reports_raw_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            path = root / "mcp-settings.json"
            write_json(path, settings_payload(root))
            config = load_config(path)

            with patch(
                "agents_remember.providers.status._watchers_status",
                return_value=ready_status_payload(root),
            ):
                payload = provider_diagnostics_payload(config)

            self.assertTrue(payload["ok"])
            self.assertEqual(payload["operation"], "provider_diagnostics")
            self.assertEqual(payload["state"], "ready")
            self.assertEqual(payload["currentState"]["state"], "ready")
            self.assertIn("rawStatus", payload)
            self.assertIn("rawStatus", payload["items"][0])

    def test_provider_watchers_status_reports_current_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            path = root / "mcp-settings.json"
            write_json(path, settings_payload(root))
            config = load_config(path)

            with patch(
                "agents_remember.controllers.provider_tools.lifecycle_service.run_watchers_lifecycle",
                return_value=ready_status_payload(root),
            ) as run_watchers:
                payload = provider_watchers_payload(config, action="status")

            service_config = run_watchers.call_args.args[0]
            self.assertFalse(service_config.dry_run)
            self.assertEqual(payload["state"], "ready")
            self.assertEqual(payload["currentState"]["state"], "ready")
            self.assertTrue(Path(payload["currentStateFile"]).exists())

    def test_runtime_install_tool_uses_configured_coordination_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            path = root / ".codex" / "mcp" / "settings.json"
            write_json(path, settings_payload(root))
            config = load_config(path)

            payload = runtime_install_payload(
                config,
                dry_run=True,
                include_benchmarks=False,
                install_provider_deps=False,
            )

            self.assertTrue(payload["ok"])
            self.assertEqual(payload["operation"], "runtime_install")
            self.assertTrue(payload["dryRun"])
            self.assertEqual(
                payload["coordinationRoot"],
                (root / "ar-coordination").as_posix(),
            )
            self.assertFalse(payload["includeBenchmarks"])
            self.assertFalse(payload["installProviderDeps"])

    def test_runtime_install_can_dry_run_packaged_benchmarks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            path = root / ".codex" / "mcp" / "settings.json"
            write_json(path, settings_payload(root))
            config = load_config(path)

            payload = runtime_install_payload(
                config,
                dry_run=True,
                include_benchmarks=True,
                install_provider_deps=False,
            )

            self.assertTrue(payload["ok"])
            self.assertTrue(payload["includeBenchmarks"])
            self.assertGreater(payload["summary"]["copiedFiles"], 0)

    def test_phase_04_tools_are_reported(self) -> None:
        expected = {
            "resolve_context",
            "drift_check",
            "memory_quality_check",
            "route_index_refresh",
            "memory_init",
            "skills_install",
            "provider_status",
            "provider_diagnostics",
            "grepai_search",
            "grepai_trace",
            "cgc_symbol_search",
            "cgc_callers",
            "cgc_callees",
            "cgc_dependencies",
            "cgc_complexity",
            "provider_watchers",
            "cgc_visualize",
            "worktree_start",
            "worktree_attach",
            "worktree_status",
            "worktree_closeout_preview",
            "worktree_closeout_apply",
            "direct_closeout_preview",
            "direct_closeout_apply",
            "worktree_integrate",
            "worktree_cleanup",
            "memory_baseline_status",
            "memory_baseline_adopt",
            "memory_carryover_plan",
            "memory_carryover_apply",
            "codex_benchmark_prepare",
            "codex_benchmark_run",
        }
        self.assertTrue(expected.issubset(set(PUBLIC_TOOLS)))
        self.assertNotIn("cgc_query", PUBLIC_TOOLS)
        self.assertNotIn("benchmark_prepare", PUBLIC_TOOLS)
        self.assertNotIn("benchmark_run", PUBLIC_TOOLS)

    def test_codex_benchmark_run_reports_missing_codex_from_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            path = root / "mcp-settings.json"
            write_json(path, settings_payload(root))
            config = load_config(path)

            with patch(
                "agents_remember.controllers.benchmark_tools.benchmark_runner.resolve_codex_executable",
                side_effect=benchmark_runner.CodexExecutableNotFound(
                    "codex executable was not found on PATH"
                ),
            ):
                payload = codex_benchmark_run_payload(config)

            self.assertFalse(payload["ok"])
            self.assertEqual(payload["operation"], "codex_benchmark_run")
            self.assertEqual(payload["executable"], "codex")
            self.assertEqual(payload["resolution"], "PATH")
            self.assertEqual(payload["codexExecutionPolicy"]["resolution"], "PATH")
            self.assertEqual(payload["codexExecutionPolicy"]["sandbox"], "danger-full-access")
            self.assertEqual(
                payload["codexExecutionPolicy"]["sandboxArgument"], "danger-full-access"
            )
            self.assertTrue(payload["codexExecutionPolicy"]["benchmarkOnly"])

            with patch(
                "agents_remember.controllers.benchmark_tools.benchmark_runner.resolve_codex_executable",
                side_effect=benchmark_runner.CodexExecutableNotFound(
                    "codex executable was not found on PATH"
                ),
            ):
                default_payload = codex_benchmark_run_payload(
                    config,
                    codex_sandbox="default",
                )

            self.assertEqual(default_payload["codexExecutionPolicy"]["sandbox"], "default")
            self.assertEqual(default_payload["codexExecutionPolicy"]["sandboxArgument"], "omitted")

    def test_command_style_artifacts_are_not_exposed_for_service_tools(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            path = root / "mcp-settings.json"
            write_json(path, settings_payload(root))
            config = load_config(path)

            with (
                patch(
                    "agents_remember.controllers.memory_tools.baseline.baseline_status",
                    return_value={"state": "ready"},
                ),
                patch(
                    "agents_remember.controllers.memory_tools.carryover.build_plan_for_request",
                    return_value={"state": "would-carryover"},
                ),
                patch(
                    "agents_remember.controllers.benchmark_tools.benchmark_runner.prepare_benchmarks",
                    return_value={
                        "ok": True,
                        "operation": "codex_benchmark_prepare",
                        "messages": [],
                    },
                ),
            ):
                payloads = [
                    memory_baseline_status_payload(config, "agents-remember-md"),
                    memory_carryover_plan_payload(
                        config,
                        "agents-remember-md",
                        source_memory=(
                            root / "ar-coordination" / "memory-repos" / "branch-memory"
                        ).as_posix(),
                        official_code_ref="main",
                        source_code_ref="feature",
                        old_base="base",
                    ),
                    codex_benchmark_prepare_payload(config),
                ]

            for payload in payloads:
                self.assertNotIn("argv", payload)
                self.assertNotIn("stdout", payload)
                self.assertNotIn("stderr", payload)
                self.assertNotIn("payload", payload)

    def test_codex_benchmark_prepare_uses_packaged_cases_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            path = root / "mcp-settings.json"
            write_json(path, settings_payload(root))
            config = load_config(path)

            with patch(
                "agents_remember.controllers.benchmark_tools.benchmark_runner.prepare_benchmarks",
                return_value={
                    "ok": True,
                    "operation": "codex_benchmark_prepare",
                    "messages": [],
                },
            ) as prepare_benchmarks:
                payload = codex_benchmark_prepare_payload(config)

            self.assertTrue(payload["ok"])
            request = prepare_benchmarks.call_args.args[0]
            self.assertTrue((request.benchmarks_root / "cases").is_dir())
            self.assertIn("package_data", request.benchmarks_root.as_posix())

    def test_skills_install_payload_is_copy_only_and_applies_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            path = root / ".codex" / "mcp" / "settings.json"
            write_json(path, settings_payload(root))
            config = load_config(path)

            payload = skills_install_payload(config)

            self.assertTrue(payload["ok"])
            self.assertEqual(payload["operation"], "skills_install")
            self.assertFalse(payload["dryRun"])
            self.assertEqual(payload["layout"], "tree")
            self.assertEqual(
                payload["installRoot"],
                (root / ".codex" / "skills").as_posix(),
            )
            self.assertTrue(payload["installed"])

    def test_skills_install_payload_replaces_legacy_symlink_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            path = root / ".codex" / "mcp" / "settings.json"
            write_json(path, settings_payload(root))
            config = load_config(path)
            install_root = root / ".codex" / "skills"
            install_root.mkdir(parents=True)
            old_target = root / "old-symlink-target"
            old_target.mkdir()
            destination = install_root / "agents-remember-md"
            try:
                os.symlink(old_target, destination, target_is_directory=True)
            except OSError as error:
                raise unittest.SkipTest(f"directory symlinks unavailable: {error}") from error

            payload = skills_install_payload(
                config,
                dry_run=False,
                overwrite=True,
                archive_existing=False,
            )

            self.assertTrue(payload["ok"])
            self.assertIn(destination.as_posix(), payload["removed"])
            self.assertIn(destination.as_posix(), payload["installed"])
            self.assertTrue(destination.is_dir())
            self.assertFalse(destination.is_symlink() or os.path.islink(destination))
            self.assertTrue(old_target.exists())
            self.assertTrue(
                (
                    destination / "U-01-core-skills" / "C-00-initialize-memory-repo" / "SKILL.md"
                ).exists()
            )

    def test_skills_install_payload_requires_configured_harness_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            payload = settings_payload(root)
            path = root / "mcp-settings.json"
            write_json(path, payload)
            config = load_config(path)

            with self.assertRaisesRegex(ValueError, "harnessSkillRoot"):
                skills_install_payload(config)

    def test_memory_init_payload_uses_configured_memory_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            path = root / "mcp-settings.json"
            write_json(path, settings_payload(root))
            config = load_config(path)

            payload = memory_init_payload(config, "agents-remember-md")

            self.assertTrue(payload["ok"])
            self.assertEqual(payload["operation"], "memory_init")
            self.assertFalse(payload["dryRun"])
            self.assertEqual(
                payload["memoryRoot"],
                (root / "ar-coordination" / "memory-repos" / "ar-agents-remember-md").as_posix(),
            )

    def test_route_index_refresh_payload_applies_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            initialize_context_fixture(root)
            path = root / "mcp-settings.json"
            write_json(path, settings_payload(root))
            config = load_config(path)

            payload = route_index_refresh_payload(config, "agents-remember-md")

            self.assertTrue(payload["ok"])
            self.assertEqual(payload["operation"], "route_index_refresh")
            self.assertFalse(payload["dryRun"])
            self.assertEqual(payload["routes"], 0)

    def test_typed_cgc_payloads_build_fixed_native_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            initialize_context_fixture(root)
            path = root / "mcp-settings.json"
            write_json(path, settings_payload(root))
            config = load_config(path)

            with patch(
                "agents_remember.providers.lifecycle_service.lifecycle.main",
                side_effect=AssertionError("MCP provider tools must use lifecycle services"),
            ):
                # dry_run=True returns the planned provider command without
                # executing it — that command shape is exactly what this test pins.
                cases = [
                    (
                        cgc_symbol_search_payload(
                            config,
                            "agents-remember-md",
                            "resolve_context",
                            dry_run=True,
                        ),
                        ["find", "name", "resolve_context"],
                    ),
                    (
                        cgc_callers_payload(
                            config,
                            "agents-remember-md",
                            "resolve_context",
                            file="mcp/src/agents_remember/mcp/tools.py",
                            dry_run=True,
                        ),
                        [
                            "analyze",
                            "callers",
                            "resolve_context",
                            "--file",
                            "mcp/src/agents_remember/mcp/tools.py",
                        ],
                    ),
                    (
                        cgc_callees_payload(
                            config,
                            "agents-remember-md",
                            "resolve_context",
                            dry_run=True,
                        ),
                        ["analyze", "calls", "resolve_context"],
                    ),
                    (
                        cgc_dependencies_payload(
                            config,
                            "agents-remember-md",
                            "agents_remember.mcp",
                            dry_run=True,
                        ),
                        ["analyze", "dependencies", "agents_remember.mcp"],
                    ),
                    (
                        cgc_complexity_payload(
                            config,
                            "agents-remember-md",
                            function="resolve_context",
                            dry_run=True,
                        ),
                        ["analyze", "complexity", "resolve_context"],
                    ),
                    (
                        cgc_complexity_payload(config, "agents-remember-md", dry_run=True),
                        ["analyze", "complexity"],
                    ),
                ]

            for payload, expected_native_args in cases:
                with self.subTest(expected_native_args=expected_native_args):
                    self.assertTrue(payload["ok"])
                    self.assertEqual(
                        command_after(planned_command(payload), "runner"),
                        expected_native_args,
                    )
                    self.assertNotIn("argv", payload)

    def test_typed_cgc_payloads_reject_invalid_inputs_before_provider_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            path = root / "mcp-settings.json"
            write_json(path, settings_payload(root))
            config = load_config(path)

            with self.assertRaisesRegex(ValueError, "not allowed"):
                cgc_callers_payload(config, "other-repo", "resolve_context")
            with self.assertRaisesRegex(ValueError, "function"):
                cgc_callees_payload(config, "agents-remember-md", "")

    def test_grepai_search_builds_workspace_wide_and_multi_repo_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            initialize_context_fixture(root)
            (root / "workspace" / "other-repo").mkdir(parents=True)
            (root / "ar-coordination" / "memory-repos" / "ar-other-repo").mkdir(parents=True)
            payload_data = settings_payload(root)
            payload_data["repositories"]["other-repo"] = {}
            path = root / "mcp-settings.json"
            write_json(path, payload_data)
            config = load_config(path)

            workspace_payload = grepai_search_payload(
                config,
                "provider lifecycle",
                dry_run=True,
            )
            scoped_payload = grepai_search_payload(
                config,
                "provider lifecycle",
                repo_ids=["agents-remember-md", "other-repo"],
                limit=5,
                output_format="toon",
                dry_run=True,
            )
            workspace = grepai_workspace(config)

        self.assertTrue(workspace_payload["ok"])
        self.assertEqual(
            command_after(planned_command(workspace_payload), "grepai"),
            [
                "search",
                "provider lifecycle",
                "--workspace",
                workspace,
                "--limit",
                "10",
                "--json",
            ],
        )
        self.assertNotIn("--project", planned_command(workspace_payload))

        self.assertTrue(scoped_payload["ok"])
        self.assertEqual(
            command_after(planned_command(scoped_payload), "grepai"),
            [
                "search",
                "provider lifecycle",
                "--workspace",
                workspace,
                "--limit",
                "5",
                "--toon",
                "--project",
                "agents-remember-md",
                "--project",
                "other-repo",
            ],
        )

    def test_grepai_payloads_reject_invalid_scope_and_trace_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            initialize_context_fixture(root)
            path = root / "mcp-settings.json"
            write_json(path, settings_payload(root))
            config = load_config(path)

            with self.assertRaisesRegex(ValueError, "unknown repo_ids"):
                grepai_search_payload(config, "provider lifecycle", repo_ids=["unknown-repo"])
            with self.assertRaisesRegex(ValueError, "repo_ids is required"):
                grepai_search_payload(config, "provider lifecycle", all_repos=False)
            with self.assertRaisesRegex(ValueError, "trace_action"):
                grepai_trace_payload(config, "neighbors", "resolve_context")
            with self.assertRaisesRegex(ValueError, "depth"):
                grepai_trace_payload(config, "callers", "resolve_context", depth=2)

    def test_grepai_trace_builds_explicit_action_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            initialize_context_fixture(root)
            path = root / "mcp-settings.json"
            write_json(path, settings_payload(root))
            config = load_config(path)

            payload = grepai_trace_payload(
                config,
                "graph",
                "resolve_context",
                repo_ids=["agents-remember-md"],
                depth=3,
                dry_run=True,
            )
            workspace = grepai_workspace(config)

        self.assertTrue(payload["ok"])
        self.assertEqual(
            command_after(planned_command(payload), "grepai"),
            [
                "trace",
                "graph",
                "resolve_context",
                "--workspace",
                workspace,
                "--json",
                "--depth",
                "3",
                "--project",
                "agents-remember-md",
            ],
        )

    def test_provider_integrity_ignores_legacy_cgc_venv_in_docker_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            provider_venv = (
                root / "ar-coordination" / "providers" / "_venvs" / "codegraphcontext" / "bin"
            )
            provider_venv.mkdir(parents=True)
            (provider_venv / "cgc").write_text("changed", encoding="utf-8")
            path = root / "mcp-settings.json"
            write_json(path, settings_payload(root))
            config = load_config(path)

            payload = check_provider_runner_integrity(config)

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["state"], "notInstalled")
        self.assertEqual(payload["fileCount"], 0)

    def test_provider_integrity_ignores_legacy_grepai_binary_in_docker_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            provider_bin = root / "ar-coordination" / "providers" / "_bin"
            provider_bin.mkdir(parents=True)
            (provider_bin / "grepai").write_text("legacy host binary\n", encoding="utf-8")
            (provider_bin / "grepai.exe").write_text("legacy host binary\n", encoding="utf-8")
            path = root / "mcp-settings.json"
            write_json(path, settings_payload(root))
            config = load_config(path)

            payload = check_provider_runner_integrity(config)

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["state"], "notInstalled")
        self.assertEqual(payload["fileCount"], 0)

    def test_provider_integrity_ignores_recorded_legacy_grepai_binary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            path = root / "mcp-settings.json"
            write_json(path, settings_payload(root))
            config = load_config(path)
            write_json(
                manifest_path_for_config(config),
                {
                    "version": 1,
                    "coordinationRoot": config.coordination_root.as_posix(),
                    "files": {
                        "providers/_bin/grepai": "legacy",
                        "providers/_bin/grepai.exe": "legacy",
                    },
                },
            )

            payload = check_provider_runner_integrity(config)

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["state"], "checked")
        self.assertEqual(payload["fileCount"], 0)


REAL_MCP_CONFIG = os.environ.get("AGENTS_REMEMBER_REAL_MCP_CONFIG")


@unittest.skipUnless(
    REAL_MCP_CONFIG,
    "set AGENTS_REMEMBER_REAL_MCP_CONFIG to run real MCP integration tests",
)
class RealMcpIntegrationTests(unittest.TestCase):
    def test_real_mcp_grepai_search_dry_run_uses_workspace_scope(self) -> None:
        payload = asyncio.run(
            self.call_tool(
                "grepai_search",
                {
                    "query": "provider lifecycle",
                    "limit": 1,
                    "output_format": "json",
                    "dry_run": True,
                },
            )
        )

        self.assertTrue(payload["ok"], payload)
        self.assertEqual(
            command_after(planned_command(payload), "grepai"),
            [
                "search",
                "provider lifecycle",
                "--workspace",
                "agents-remember-memory",
                "--limit",
                "1",
                "--json",
            ],
        )
        self.assertNotIn("--project", planned_command(payload))

    def test_real_mcp_grepai_search_runs_with_project_filter(self) -> None:
        payload = asyncio.run(
            self.call_tool(
                "grepai_search",
                {
                    "query": "provider lifecycle",
                    "repo_ids": ["agents-remember-md"],
                    "limit": 1,
                    "output_format": "json",
                    "dry_run": False,
                    "timeout": 60,
                },
            )
        )

        self.assertTrue(payload["ok"], payload)
        command = payload["command"]["command"]
        self.assertIn("--workspace", command)
        self.assertIn("agents-remember-memory", command)
        self.assertIn("--project", command)
        self.assertIn("agents-remember-md", command)

    async def call_tool(self, name: str, arguments: dict[str, object]) -> dict:
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "agents_remember.mcp", "--config", str(REAL_MCP_CONFIG)],
            env={**os.environ, "PYTHONPATH": str(MCP_SRC)},
        )
        async with (
            stdio_client(params) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            result = await session.call_tool(name, arguments)
        if result.structuredContent is not None:
            return dict(result.structuredContent)
        content = result.content[0]
        if not isinstance(content, TextContent):
            raise AssertionError(f"expected text content, got {type(content).__name__}")
        return json.loads(content.text)


def initialize_context_fixture(root: Path) -> None:
    repo = root / "workspace" / "agents-remember-md"
    memory = root / "ar-coordination" / "memory-repos" / "ar-agents-remember-md"
    (memory / "system").mkdir(parents=True, exist_ok=True)
    (memory / "onboarding").mkdir(parents=True, exist_ok=True)
    (memory / "system" / "settings.md").write_text("# Settings\n", encoding="utf-8")
    repo.mkdir(parents=True, exist_ok=True)
    run_git(repo, ["init"])
    run_git(repo, ["config", "user.email", "agents-remember@example.invalid"])
    run_git(repo, ["config", "user.name", "Agents Remember"])
    (repo / "README.md").write_text("# Fixture\n", encoding="utf-8")
    run_git(repo, ["add", "README.md"])
    run_git(repo, ["commit", "-m", "init"])


def run_git(repo: Path, args: list[str]) -> None:
    result = subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
