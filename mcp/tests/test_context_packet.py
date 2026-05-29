from __future__ import annotations

import contextlib
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
MCP_TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(MCP_SRC))
sys.path.insert(0, str(MCP_TESTS))

from agents_remember.cli.context_packet import main as cli_main
from agents_remember.controllers.context_packet import (
    ContextPacketError,
    ContextPacketRequest,
    build_context_packet,
)
from agents_remember.mcp.config import load_config
from agents_remember.worktrees.git_worktree_manager import status_payload
from agents_remember.worktrees.worktree_contract import (
    default_contract,
    write_contract,
)
from test_config import settings_payload, write_json


class ContextPacketTests(unittest.TestCase):
    def test_builds_packet_from_allowed_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            initialize_context_fixture(root)
            config = write_and_load_config(root)

            packet = build_context_packet(
                config,
                ContextPacketRequest(repo_id="agents-remember-md"),
            )

            self.assertTrue(packet["ok"])
            self.assertEqual(packet["operation"], "context_packet")
            self.assertEqual(packet["contextPacketVersion"], 2)
            self.assertEqual(packet["repo"]["state"], "available")
            self.assertEqual(packet["repo"]["id"], "agents-remember-md")
            self.assertTrue(packet["repo"]["head"])
            self.assertFalse(packet["repo"]["dirty"])
            self.assertNotIn("repoId", packet)
            self.assertNotIn("storage", packet)
            self.assertNotIn("pathRules", packet)
            self.assertNotIn("crossRepo", packet)
            self.assertEqual(
                packet["paths"]["taskRoot"],
                (root / "ar-coordination" / "tasks" / "agents-remember-md").as_posix(),
            )
            self.assertEqual(packet["memory"]["mode"], "external")
            self.assertIn("pathRules", packet["memory"]["storage"])
            self.assertIn("crossRepo", packet["memory"])
            self.assertEqual(packet["worktree"], {"state": "inactive"})
            self.assertEqual(packet["providers"]["state"], "failed")
            self.assertTrue(Path(packet["providers"]["currentStateFile"]).exists())
            self.assertEqual(packet["providers"]["diagnosticsTool"], "provider_diagnostics")
            self.assertNotIn("currentState", packet["providers"])
            self.assertNotIn("rawStatus", packet["providers"])
            self.assertNotIn("rawStatus", packet["providers"]["items"][0])
            self.assertEqual(packet["drift"], {"status": "notChecked"})

    def test_builds_drift_summary_when_requested(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            initialize_context_fixture(root)
            config = write_and_load_config(root)

            packet = build_context_packet(
                config,
                ContextPacketRequest(repo_id="agents-remember-md", include_drift=True),
            )

            self.assertEqual(packet["drift"]["status"], "checked")
            self.assertEqual(packet["drift"]["count"], 0)
            self.assertEqual(packet["drift"]["actionableCount"], 0)
            self.assertTrue(packet["drift"]["reportPath"].endswith("_drift-report.md"))
            self.assertTrue(Path(packet["drift"]["reportPath"]).exists())

    def test_rejects_unknown_repo_before_filesystem_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            config = write_and_load_config(root)

            with self.assertRaisesRegex(ContextPacketError, "not allowed"):
                build_context_packet(config, ContextPacketRequest(repo_id="other-repo"))

    def test_reports_unavailable_git_facts_for_non_git_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            repo = root / "workspace" / "agents-remember-md"
            memory = root / "ar-coordination" / "memory-repos" / "ar-agents-remember-md"
            repo.mkdir(parents=True, exist_ok=True)
            (memory / "system").mkdir(parents=True, exist_ok=True)
            (memory / "onboarding").mkdir(parents=True, exist_ok=True)
            (memory / "system" / "settings.md").write_text("# Settings\n", encoding="utf-8")
            config = write_and_load_config(root)

            packet = build_context_packet(
                config,
                ContextPacketRequest(repo_id="agents-remember-md"),
            )

            self.assertFalse(packet["ok"])
            self.assertEqual(packet["repo"]["state"], "unavailable")
            self.assertIn("git", packet["repo"]["error"])

    def test_reports_active_worktree_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            initialize_context_fixture(root)
            repo = root / "workspace" / "agents-remember-md"
            coordination_root = root / "ar-coordination"
            contract = default_contract(
                task_name="Context Packet",
                repo_name="agents-remember-md",
                workflow_kind="light",
                memory_mode="external",
                coordination_root=coordination_root,
                code_repo_path=repo,
                code_source_branch=current_branch(repo),
                code_work_branch="ar/context-packet",
                code_base_commit=current_head(repo),
                worktree_name="context-packet",
                memory_repo_path=coordination_root / "memory-repos" / "ar-agents-remember-md",
                memory_source_branch="main",
                memory_work_branch="ar/context-packet-memory",
                memory_base_commit=current_head(repo),
            )
            write_contract(contract.contract_path, contract)
            payload = settings_payload(root)
            payload["repositories"]["agents-remember-md"]["contractPath"] = str(
                contract.contract_path
            )
            config_path = root / "mcp-settings.json"
            write_json(config_path, payload)
            config = load_config(config_path)

            packet = build_context_packet(
                config,
                ContextPacketRequest(repo_id="agents-remember-md"),
            )

            self.assertEqual(packet["worktree"]["state"], "active")
            self.assertEqual(packet["worktree"]["contractPath"], contract.contract_path.as_posix())
            self.assertEqual(packet["worktree"]["phase"], "worktree-started")
            self.assertEqual(packet["worktree"]["taskId"], status_payload(contract)["task_id"])
            self.assertNotIn("rawStatus", packet["worktree"])

    def test_cli_outputs_json_packet(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            initialize_context_fixture(root)
            config_path = root / "mcp-settings.json"
            write_json(config_path, settings_payload(root))

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = cli_main(["--config", str(config_path), "--repo", "agents-remember-md"])

            self.assertEqual(exit_code, 0)
            self.assertIn('"operation": "context_packet"', output.getvalue())


def write_and_load_config(root: Path):
    config_path = root / "mcp-settings.json"
    write_json(config_path, settings_payload(root))
    return load_config(config_path)


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


def current_branch(repo: Path) -> str:
    return git_output(repo, ["branch", "--show-current"]) or "master"


def current_head(repo: Path) -> str:
    return git_output(repo, ["rev-parse", "HEAD"])


def git_output(repo: Path, args: list[str]) -> str:
    result = subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return result.stdout.strip()


def run_git(repo: Path, args: list[str]) -> None:
    result = subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
