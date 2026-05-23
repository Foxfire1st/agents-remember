from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
MCP_TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(MCP_SRC))
sys.path.insert(0, str(MCP_TESTS))

from agents_remember.mcp.config import load_config  # noqa: E402
from agents_remember.mcp.server import create_server  # noqa: E402
from agents_remember.mcp.tools import (  # noqa: E402
    PUBLIC_TOOLS,
    cgc_query_payload,
    context_packet_payload,
    memory_init_payload,
    ping_payload,
    route_index_refresh_payload,
    runtime_install_payload,
    server_info_payload,
    skills_install_payload,
)
from test_config import settings_payload  # noqa: E402


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


class McpToolTests(unittest.TestCase):
    def test_ping_payload(self) -> None:
        self.assertEqual(
            ping_payload(),
            {
                "ok": True,
                "server": "agents-remember",
                "version": "0.1.0",
                "transport": "stdio",
            },
        )

    def test_server_info_payload_reports_safe_config_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            path = root / "mcp-settings.json"
            write_json(path, settings_payload(root))
            config = load_config(path)

            payload = server_info_payload(config)

            self.assertTrue(payload["ok"])
            self.assertEqual(payload["server"], "agents-remember")
            self.assertEqual(payload["transport"], "stdio")
            self.assertEqual(payload["configPath"], path.resolve().as_posix())
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
            path = root / "mcp-settings.json"
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
            self.assertEqual(payload["contextPacketVersion"], 1)
            self.assertEqual(payload["repoId"], "agents-remember-md")
            self.assertEqual(payload["providers"]["state"], "checked")
            self.assertEqual(payload["drift"], {"status": "notChecked"})

    def test_runtime_install_tool_uses_configured_coordination_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            path = root / "mcp-settings.json"
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

    def test_phase_04_tools_are_reported(self) -> None:
        expected = {
            "resolve_context",
            "drift_check",
            "route_index_refresh",
            "memory_init",
            "skills_install",
            "provider_status",
            "grepai_search",
            "grepai_trace",
            "cgc_query",
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
            "benchmark_prepare",
            "benchmark_run",
        }
        self.assertTrue(expected.issubset(set(PUBLIC_TOOLS)))

    def test_skills_install_payload_is_copy_only_and_dry_run_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            payload = skills_install_payload(str(Path(tmp_dir) / "skills"))

            self.assertTrue(payload["ok"])
            self.assertEqual(payload["operation"], "skills_install")
            self.assertTrue(payload["dryRun"])
            self.assertEqual(payload["layout"], "tree")
            self.assertTrue(payload["planned"])

    def test_memory_init_payload_uses_configured_memory_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            path = root / "mcp-settings.json"
            write_json(path, settings_payload(root))
            config = load_config(path)

            payload = memory_init_payload(config, "agents-remember-md")

            self.assertTrue(payload["ok"])
            self.assertEqual(payload["operation"], "memory_init")
            self.assertTrue(payload["dryRun"])
            self.assertEqual(
                payload["memoryRoot"],
                (root / "ar-coordination" / "memory-repos" / "ar-agents-remember-md").as_posix(),
            )

    def test_route_index_refresh_payload_runs_in_dry_run_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            initialize_context_fixture(root)
            path = root / "mcp-settings.json"
            write_json(path, settings_payload(root))
            config = load_config(path)

            payload = route_index_refresh_payload(config, "agents-remember-md")

            self.assertTrue(payload["ok"])
            self.assertEqual(payload["operation"], "route_index_refresh")
            self.assertTrue(payload["dryRun"])
            self.assertEqual(payload["routes"], 0)

    def test_cgc_query_payload_rejects_unknown_repo_before_provider_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            path = root / "mcp-settings.json"
            write_json(path, settings_payload(root))
            config = load_config(path)

            with self.assertRaisesRegex(ValueError, "not allowed"):
                cgc_query_payload(config, "other-repo", "deps")


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
