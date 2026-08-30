from __future__ import annotations

import asyncio
import inspect
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest
from mcp.client.stdio import stdio_client
from mcp.types import TextContent

from mcp import ClientSession, StdioServerParameters

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
MCP_TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(MCP_SRC))
sys.path.insert(0, str(MCP_TESTS))

from agents_remember.application.benchmark_tools import CodexBenchmarkRun
from agents_remember.application.memory_tools import CarryoverSelection
from agents_remember.application.provider_tools import (
    GrepaiRepoScope,
    GrepaiSearchQuery,
    GrepaiTraceQuery,
    ProviderQueryScope,
)
from agents_remember.application.runtime.install import RuntimeInstallRequest
from agents_remember.benchmarks import runner as benchmark_runner
from agents_remember.errors import AuthorityError
from agents_remember.kernel import memory_init as memory_init_module
from agents_remember.kernel.primitives.runtime_config import (
    load_config,
)
from agents_remember.mcp import SERVER_VERSION
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
from agents_remember.mcp.tools import core as core_tools
from agents_remember.providers.settings import lifecycle_settings_from_config
from agents_remember.serving.build_info import ServingBuild
from test_config import settings_payload
from test_provider_current_state import ready_status_payload

DRY_RUN_SCOPE = ProviderQueryScope(dry_run=True)


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
        self.assertEqual(payload["version"], SERVER_VERSION)
        self.assertEqual(payload["transport"], "stdio")
        self.assertGreater(payload["tokens"], 0)
        self.assertEqual(payload["tokenizer"], "tiktoken:o200k_base")
        self.assertIs(payload["tokenCountExact"], True)

    def test_server_info_payload_reports_safe_config_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            path = root / ".codex" / "mcp" / "settings.json"
            write_json(path, settings_payload(root))
            config = load_config(path)

            build = ServingBuild(
                version=SERVER_VERSION,
                commit="abc1234",
                booted_at="2026-08-25T00:00:00Z",
                source_digest="sha256:" + "a" * 64,
                python_executable="/runtime/bin/python",
                package_root="/runtime/agents_remember",
            )
            payload = server_info_payload(config, build.payload())

            self.assertTrue(payload["ok"])
            self.assertEqual(payload["server"], "agents-remember")
            self.assertEqual(payload["transport"], "stdio")
            self.assertEqual(payload["configPath"], path.resolve().as_posix())
            self.assertEqual(
                payload["harnessSkillRoot"],
                (root / ".codex" / "skills").as_posix(),
            )
            self.assertEqual(payload["allowedRepoIds"], ["agents-remember"])
            self.assertEqual(
                payload["allowedProviderIds"],
                ["codegraphcontext-code", "grepai-memory"],
            )
            self.assertEqual(
                payload["tools"],
                list(PUBLIC_TOOLS),
            )
            self.assertEqual(payload["reservedTools"], [])
            self.assertEqual(
                payload["servingBuild"],
                {
                    "version": SERVER_VERSION,
                    "bootedAt": "2026-08-25T00:00:00Z",
                    "sourceDigest": "sha256:" + "a" * 64,
                    "pythonExecutable": "/runtime/bin/python",
                    "packageRoot": "/runtime/agents_remember",
                    "commit": "abc1234",
                },
            )

    def test_server_constructs_with_context_packet_tool(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            path = root / ".codex" / "mcp" / "settings.json"
            write_json(path, settings_payload(root))
            config = load_config(path)

            server = create_server(config)

            self.assertIsNotNone(server)

    def test_every_public_tool_has_a_description(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            path = root / ".codex" / "mcp" / "settings.json"
            write_json(path, settings_payload(root))
            config = load_config(path)

            server = create_server(config)
            tools = asyncio.run(server.list_tools())

            self.assertEqual({tool.name for tool in tools}, set(PUBLIC_TOOLS))
            missing = [
                tool.name for tool in tools if not (tool.description and tool.description.strip())
            ]
            self.assertEqual(missing, [], f"tools missing a description: {missing}")

    def test_agent_control_surface_exposes_only_structural_addresses(self) -> None:
        """L19 machine ban: models cannot request or retain private plane correlations."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            path = root / ".codex" / "mcp" / "settings.json"
            write_json(path, settings_payload(root))
            server = create_server(load_config(path))
            tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}

        self.assertTrue(
            {
                "dispatch_agent",
                "message_parent",
                "message_child",
                "retire_child",
                "rename_child",
                "rename_self",
                "lifecycle_gate",
                "gate_decide",
                "gate_list",
            }.issubset(tools)
        )
        self.assertTrue(
            {
                "spawn_agent_session",
                "attach_terminal_session_to_task",
                "hosted_session_readiness",
                "session_retire",
                "session_rename",
                "operator_inbox_post",
                "operator_inbox_poll",
                "operator_inbox_consume",
                "operator_inbox_supersede",
                "orchestration_nudge_manager",
            }.isdisjoint(tools)
        )

        forbidden_fragments = (
            "sessionid",
            "lifecycleid",
            "agentid",
            "inboxrowid",
            "adapterrequestid",
            "vendorcorrelationid",
            "gateid",
        )
        for name in (
            "dispatch_agent",
            "message_parent",
            "message_child",
            "retire_child",
            "rename_child",
            "rename_self",
            "lifecycle_gate",
            "gate_decide",
            "gate_list",
        ):
            schema_text = json.dumps(tools[name].inputSchema).lower().replace("_", "")
            with self.subTest(tool=name):
                self.assertFalse(
                    any(fragment in schema_text for fragment in forbidden_fragments),
                    schema_text,
                )
        message_schema = json.dumps(tools["message_parent"].inputSchema)
        self.assertNotIn("dispatch-brief", message_schema)
        self.assertNotIn("state-signal", message_schema)

    def test_closeout_tool_descriptions_pin_strict_quality_before_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            path = root / ".codex" / "mcp" / "settings.json"
            write_json(path, settings_payload(root))
            config = load_config(path)

            server = create_server(config)
            tools = {tool.name: tool.description or "" for tool in asyncio.run(server.list_tools())}

            self.assertIn("mandatory CRAP enforcement", tools["worktree_closeout_preview"])
            self.assertIn("before the code commit", tools["worktree_closeout_preview"])
            self.assertIn("mandatory CRAP enforcement", tools["worktree_closeout_apply"])
            self.assertIn("before any code", tools["worktree_closeout_apply"])
            self.assertIn("approval precede apply", tools["worktree_closeout_apply"])

    def test_context_packet_tool_delegates_to_application(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            initialize_context_fixture(root)
            path = root / "mcp-settings.json"
            write_json(path, settings_payload(root))
            config = load_config(path)

            payload = context_packet_payload(config, "agents-remember")

            self.assertTrue(payload["ok"])
            self.assertEqual(payload["operation"], "context_packet")
            self.assertEqual(payload["contextPacketVersion"], 2)
            self.assertEqual(payload["repo"]["id"], "agents-remember")
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
            # The raw status trees and the currentState body are filed, not
            # inlined (S4 response budgets); the report carries the detail.
            self.assertNotIn("rawStatus", payload)
            self.assertNotIn("currentState", payload)
            self.assertNotIn("rawStatus", payload["items"][0])
            self.assertTrue(Path(payload["currentStateFile"]).exists())
            report = json.loads(Path(payload["reportPath"]).read_text(encoding="utf-8"))
            self.assertIn("rawStatus", report)
            self.assertEqual(report["currentState"]["state"], "ready")

    def test_provider_watchers_status_reports_current_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            path = root / "mcp-settings.json"
            write_json(path, settings_payload(root))
            config = load_config(path)

            with patch(
                "agents_remember.providers.watcher_service.run_watchers_lifecycle",
                return_value=ready_status_payload(root),
            ) as run_watchers:
                payload = provider_watchers_payload(config, action="status")

            service_config = run_watchers.call_args.args[0]
            self.assertFalse(service_config.dry_run)
            self.assertEqual(payload["state"], "ready")
            # The currentState body is filed, not inlined (S4 response budgets).
            self.assertNotIn("currentState", payload)
            self.assertTrue(Path(payload["currentStateFile"]).exists())
            report = json.loads(Path(payload["reportPath"]).read_text(encoding="utf-8"))
            self.assertEqual(report["currentState"]["state"], "ready")

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

    def test_runtime_install_payload_carries_no_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            path = root / ".codex" / "mcp" / "settings.json"
            write_json(path, settings_payload(root))
            config = load_config(path)

            captured: dict[str, RuntimeInstallRequest] = {}

            def fake_run(cfg: object, request: RuntimeInstallRequest) -> dict[str, object]:
                captured["request"] = request
                return {"ok": True, "operation": "runtime_install"}

            with patch.object(core_tools, "run_runtime_install", side_effect=fake_run):
                runtime_install_payload(config, dry_run=True, no_cache=True)

            self.assertTrue(captured["request"].no_cache)

    def test_runtime_install_payload_exposes_no_cache_param(self) -> None:
        sig = inspect.signature(runtime_install_payload)
        self.assertIn("no_cache", sig.parameters)
        self.assertIs(sig.parameters["no_cache"].default, False)

    def test_phase_04_tools_are_reported(self) -> None:
        expected = {
            "resolve_context",
            "drift_check",
            "memory_quality_check",
            "citation_fix",
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
            "worktree_integrate",
            "worktree_cleanup",
            "memory_baseline_status",
            "memory_baseline_adopt",
            "memory_carryover_plan",
            "memory_carryover_apply",
            "codex_benchmark_prepare",
            "codex_benchmark_run",
            "closeout_queue",
            "lifecycle_gate",
            "gate_decide",
            "gate_list",
            "dispatch_agent",
            "retire_child",
            "rename_child",
            "rename_self",
            "message_parent",
            "message_child",
        }
        self.assertTrue(expected.issubset(set(PUBLIC_TOOLS)))
        for retired in (
            "lifecycle_block",
            "gate_create",
            "gate_wait",
            "gate_response_wait",
            "operator_inbox_post",
            "operator_inbox_poll",
            "operator_inbox_consume",
            "attach_terminal_session_to_task",
            "spawn_agent_session",
            "hosted_session_readiness",
            "session_retire",
            "session_rename",
        ):
            self.assertNotIn(retired, PUBLIC_TOOLS)
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
                "agents_remember.application.benchmark_tools.benchmark_runner.resolve_codex_executable",
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
            self.assertEqual(payload["codexExecutionPolicy"]["sandbox"], "default")
            self.assertEqual(payload["codexExecutionPolicy"]["sandboxArgument"], "omitted")
            self.assertTrue(payload["codexExecutionPolicy"]["benchmarkOnly"])

            with patch(
                "agents_remember.application.benchmark_tools.benchmark_runner.resolve_codex_executable",
                side_effect=benchmark_runner.CodexExecutableNotFound(
                    "codex executable was not found on PATH"
                ),
            ):
                danger_payload = codex_benchmark_run_payload(
                    config,
                    run=CodexBenchmarkRun(codex_sandbox="danger-full-access"),
                )

            self.assertEqual(
                danger_payload["codexExecutionPolicy"]["sandbox"], "danger-full-access"
            )
            self.assertEqual(
                danger_payload["codexExecutionPolicy"]["sandboxArgument"], "danger-full-access"
            )

    def test_codex_benchmark_tools_refuse_when_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            payload = settings_payload(root)
            payload["benchmarksEnabled"] = False
            path = root / "mcp-settings.json"
            write_json(path, payload)
            config = load_config(path)

            run = codex_benchmark_run_payload(config)
            self.assertFalse(run["ok"])
            self.assertEqual(run["operation"], "codex_benchmark_run")
            self.assertIn("disabled", run["error"])

            prepare = codex_benchmark_prepare_payload(config)
            self.assertFalse(prepare["ok"])
            self.assertEqual(prepare["operation"], "codex_benchmark_prepare")
            self.assertIn("disabled", prepare["error"])

    def test_command_style_artifacts_are_not_exposed_for_service_tools(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            path = root / "mcp-settings.json"
            write_json(path, settings_payload(root))
            config = load_config(path)

            with (
                patch(
                    "agents_remember.application.memory_tools.baseline.baseline_status",
                    return_value={"state": "ready"},
                ),
                patch(
                    "agents_remember.application.memory_tools.carryover.build_plan_for_request",
                    return_value={"state": "would-carryover"},
                ),
                patch(
                    "agents_remember.application.memory_tools._carryover_request",
                    return_value=object(),
                ),
                patch(
                    "agents_remember.application.memory_tools.carryover._require_carryover_authority"
                ),
                patch(
                    "agents_remember.application.benchmark_tools.benchmark_runner.prepare_benchmarks",
                    return_value={
                        "ok": True,
                        "operation": "codex_benchmark_prepare",
                        "messages": [],
                    },
                ),
            ):
                payloads = [
                    memory_baseline_status_payload(config, "agents-remember"),
                    memory_carryover_plan_payload(
                        config,
                        CarryoverSelection(
                            repo_id="agents-remember",
                            contract_path=(
                                root
                                / "ar-coordination/tasks/agents-remember/carryover/series-contract.md"
                            ).as_posix(),
                            source_memory=(
                                root / "ar-coordination" / "memory-repos" / "branch-memory"
                            ).as_posix(),
                            official_code_ref="main",
                            source_code_ref="feature",
                            old_base="base",
                        ),
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
                "agents_remember.application.benchmark_tools.benchmark_runner.prepare_benchmarks",
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
            self.assertEqual(
                payload["installRoot"],
                (root / ".codex" / "skills").as_posix(),
            )
            self.assertTrue(payload["installed"])
            # Skills install flat: one folder per skill, named by its frontmatter name.
            self.assertTrue(
                (root / ".codex" / "skills" / "c-09-git-worktree-manager" / "SKILL.md").exists()
            )

    def test_skills_install_payload_replaces_existing_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            path = root / ".codex" / "mcp" / "settings.json"
            write_json(path, settings_payload(root))
            config = load_config(path)
            install_root = root / ".codex" / "skills"
            install_root.mkdir(parents=True)
            old_target = root / "old-symlink-target"
            old_target.mkdir()
            destination = install_root / "c-09-git-worktree-manager"
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
            self.assertTrue((destination / "SKILL.md").exists())

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

            payload = memory_init_payload(config, "agents-remember")

            self.assertTrue(payload["ok"])
            self.assertEqual(payload["operation"], "memory_init")
            self.assertFalse(payload["dryRun"])
            self.assertEqual(
                payload["memoryRoot"],
                (root / "ar-coordination" / "memory-repos" / "ar-agents-remember").as_posix(),
            )
            memory_root = Path(payload["memoryRoot"])
            configured = subprocess.run(
                ["git", "config", "--get", "agents-remember.defaultBranch"],
                cwd=memory_root,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            symbolic = subprocess.run(
                ["git", "symbolic-ref", "--quiet", "HEAD"],
                cwd=memory_root,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            self.assertEqual((configured, symbolic), ("main", "refs/heads/main"))

    def test_memory_init_repairs_authority_after_config_write_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            path = root / "mcp-settings.json"
            write_json(path, settings_payload(root))
            config = load_config(path)
            real_run_git = memory_init_module.run_git

            def fail_authority(repo: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
                if args == [
                    "config",
                    "--local",
                    "agents-remember.defaultBranch",
                    "main",
                ]:
                    return subprocess.CompletedProcess(args, 1, "", "config locked")
                return real_run_git(repo, args)

            with patch.object(memory_init_module, "run_git", side_effect=fail_authority):
                failed = memory_init_payload(config, "agents-remember")
            self.assertFalse(failed["ok"])
            memory_root = Path(str(failed["memoryRoot"]))
            self.assertTrue((memory_root / ".git").exists())

            repaired = memory_init_payload(config, "agents-remember")

            self.assertTrue(repaired["ok"])
            self.assertTrue(repaired["git"]["repairAttempted"])
            configured = subprocess.run(
                ["git", "config", "--get", "agents-remember.defaultBranch"],
                cwd=memory_root,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            self.assertEqual(configured, "main")

    def test_memory_init_refuses_an_existing_unborn_nondefault_repository(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            path = root / "mcp-settings.json"
            write_json(path, settings_payload(root))
            config = load_config(path)
            memory_root = root / "ar-coordination" / "memory-repos" / "ar-agents-remember"
            memory_root.mkdir(parents=True)
            (memory_root / "user-owned.txt").write_text("preserve me\n", encoding="utf-8")
            subprocess.run(
                ["git", "init", "-b", "trunk"],
                cwd=memory_root,
                check=True,
                capture_output=True,
            )

            def user_tree() -> dict[str, bytes | None]:
                return {
                    item.relative_to(memory_root).as_posix(): (
                        None if item.is_dir() else item.read_bytes()
                    )
                    for item in memory_root.rglob("*")
                    if ".git" not in item.relative_to(memory_root).parts
                }

            before = user_tree()

            payload = memory_init_payload(config, "agents-remember")

            self.assertFalse(payload["ok"])
            self.assertIn("refs/heads/main", str(payload["git"]["stderr"]))
            self.assertEqual(payload["createdDirs"], [])
            self.assertEqual(payload["createdFiles"], [])
            self.assertEqual(user_tree(), before)
            configured = subprocess.run(
                ["git", "config", "--get", "agents-remember.defaultBranch"],
                cwd=memory_root,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(configured.returncode, 0)

    def test_route_index_refresh_payload_refuses_unscoped_apply(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            initialize_context_fixture(root)
            path = root / "mcp-settings.json"
            write_json(path, settings_payload(root))
            config = load_config(path)

            with self.assertRaisesRegex(AuthorityError, "requires a leaf contract_path"):
                route_index_refresh_payload(config, "agents-remember")

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
                            "agents-remember",
                            "resolve_context",
                            scope=DRY_RUN_SCOPE,
                        ),
                        ["find", "name", "resolve_context"],
                    ),
                    (
                        cgc_callers_payload(
                            config,
                            "agents-remember",
                            "resolve_context",
                            file="mcp/src/agents_remember/mcp/tools.py",
                            scope=DRY_RUN_SCOPE,
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
                            "agents-remember",
                            "resolve_context",
                            scope=DRY_RUN_SCOPE,
                        ),
                        ["analyze", "calls", "resolve_context"],
                    ),
                    (
                        cgc_dependencies_payload(
                            config,
                            "agents-remember",
                            "agents_remember.mcp",
                            scope=DRY_RUN_SCOPE,
                        ),
                        ["analyze", "deps", "agents_remember.mcp"],
                    ),
                    (
                        cgc_complexity_payload(
                            config,
                            "agents-remember",
                            function="resolve_context",
                            scope=DRY_RUN_SCOPE,
                        ),
                        ["analyze", "complexity", "resolve_context"],
                    ),
                    (
                        cgc_complexity_payload(config, "agents-remember", scope=DRY_RUN_SCOPE),
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
                cgc_callees_payload(config, "agents-remember", "")

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
                GrepaiSearchQuery(query="provider lifecycle"),
                scope=DRY_RUN_SCOPE,
            )
            scoped_payload = grepai_search_payload(
                config,
                GrepaiSearchQuery(query="provider lifecycle", limit=5, output_format="toon"),
                repos=GrepaiRepoScope(repo_ids=["agents-remember", "other-repo"]),
                scope=DRY_RUN_SCOPE,
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
                "agents-remember",
                "--project",
                "other-repo",
            ],
        )

    def test_grepai_search_resolves_uppercase_repo_id_to_normalized_project(self) -> None:
        # Regression: a configured repo id with uppercase (e.g. "Cobalt") is indexed
        # by the watcher under the stable_provider_id-normalized project ("cobalt").
        # The tool must emit "--project cobalt", and accept the repo id in any casing.
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            initialize_context_fixture(root)
            (root / "workspace" / "Cobalt").mkdir(parents=True)
            (root / "ar-coordination" / "memory-repos" / "ar-Cobalt").mkdir(parents=True)
            payload_data = settings_payload(root)
            payload_data["repositories"]["Cobalt"] = {}
            path = root / "mcp-settings.json"
            write_json(path, payload_data)
            config = load_config(path)

            configured = grepai_search_payload(
                config,
                GrepaiSearchQuery(query="automaton"),
                repos=GrepaiRepoScope(repo_ids=["Cobalt"]),
                scope=DRY_RUN_SCOPE,
            )
            lowercased = grepai_search_payload(
                config,
                GrepaiSearchQuery(query="automaton"),
                repos=GrepaiRepoScope(repo_ids=["cobalt"]),
                scope=DRY_RUN_SCOPE,
            )

        for payload in (configured, lowercased):
            self.assertTrue(payload["ok"])
            command = planned_command(payload)
            self.assertEqual(command[command.index("--project") + 1], "cobalt")
            self.assertNotIn("Cobalt", command)

    def test_grepai_payloads_reject_invalid_scope_and_trace_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            initialize_context_fixture(root)
            path = root / "mcp-settings.json"
            write_json(path, settings_payload(root))
            config = load_config(path)

            with self.assertRaisesRegex(ValueError, "unknown repo_ids"):
                grepai_search_payload(
                    config,
                    GrepaiSearchQuery(query="provider lifecycle"),
                    repos=GrepaiRepoScope(repo_ids=["unknown-repo"]),
                )
            with self.assertRaisesRegex(ValueError, "repo_ids is required"):
                grepai_search_payload(
                    config,
                    GrepaiSearchQuery(query="provider lifecycle"),
                    repos=GrepaiRepoScope(all_repos=False),
                )
            with self.assertRaisesRegex(ValueError, "trace_action"):
                grepai_trace_payload(
                    config, GrepaiTraceQuery(trace_action="neighbors", symbol="resolve_context")
                )
            with self.assertRaisesRegex(ValueError, "depth"):
                grepai_trace_payload(
                    config,
                    GrepaiTraceQuery(trace_action="callers", symbol="resolve_context", depth=2),
                )

    def test_grepai_trace_builds_explicit_action_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            initialize_context_fixture(root)
            path = root / "mcp-settings.json"
            write_json(path, settings_payload(root))
            config = load_config(path)

            payload = grepai_trace_payload(
                config,
                GrepaiTraceQuery(trace_action="graph", symbol="resolve_context", depth=3),
                repos=GrepaiRepoScope(repo_ids=["agents-remember"]),
                scope=DRY_RUN_SCOPE,
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
                "agents-remember",
            ],
        )


REAL_MCP_CONFIG = os.environ.get("AGENTS_REMEMBER_REAL_MCP_CONFIG")


@pytest.mark.agents_remember_real_mcp_config
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

        # The workspace name is derived from the settings file, not written here. It
        # used to be the literal "agents-remember-memory", which stopped being anyone's
        # workspace once provider instances became scoped -- `scoped_name` appends the
        # instance id, so the real name depends on the config the server was handed.
        # Nothing ran this suite, so the stale literal sat here unnoticed; asserting
        # against the same derivation the server uses is what keeps it a real check.
        self.assertTrue(payload["ok"], payload)
        expected_workspace = grepai_workspace(load_config(Path(str(REAL_MCP_CONFIG))))
        self.assertEqual(
            command_after(planned_command(payload), "grepai"),
            [
                "search",
                "provider lifecycle",
                "--workspace",
                expected_workspace,
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
                    "repo_ids": ["agents-remember"],
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
        self.assertIn("agents-remember", command)

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
    repo = root / "workspace" / "agents-remember"
    memory = root / "ar-coordination" / "memory-repos" / "ar-agents-remember"
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
