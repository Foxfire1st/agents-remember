"""End-to-end stdio transport tests for hang-prone MCP tools.

Spawns the real server over real pipes — the same topology as a harness —
and asserts that tool calls complete within a bound. Born from the 2026-06-09
incident where `memory_carryover_plan` hung for 6-8 minutes via MCP while the
identical function returned in 2.6s when called directly (GitHub #49).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
MCP_TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(MCP_SRC))
sys.path.insert(0, str(MCP_TESTS))

from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    LeafIdentity,
    RepoBranchPlan,
    default_contract,
    write_contract,
)
from mcp.client.stdio import stdio_client
from mcp.types import TextContent
from test_config import write_json
from test_worktree_support import (
    commit_file,
    git,
    init_repo,
    initialized_memory_repo,
    write_file_onboarding,
)

from mcp import ClientSession, StdioServerParameters

INITIALIZE_TIMEOUT = 60
CALL_TIMEOUT = 120


def harness_settings_payload(root: Path) -> dict:
    return {
        "version": 1,
        "coordinationRoot": str(root / "ar-coordination"),
        "workspaceRoot": str(root / "workspace"),
        "repositories": {"repo-a": {}},
        "providers": {},
        "timeoutCaps": {"toolSeconds": 30, "providerSetupSeconds": 60},
        "benchmarksEnabled": False,
    }


def build_carryover_fixture(root: Path) -> dict:
    """Code repo + official memory + in-coordination source memory, with a
    landed branch so the plan has real candidates to classify."""
    workspace = root / "workspace"
    code_repo = workspace / "repo-a"
    old_base = init_repo(code_repo, "main")
    git(code_repo, "checkout", "-b", "feat/branch")
    source_head = commit_file(
        code_repo, "feature.py", "def feature():\n    return 'landed'\n", "Add feature"
    )
    git(code_repo, "checkout", "main")
    git(code_repo, "merge", "--ff-only", "feat/branch")

    coordination = root / "ar-coordination"
    official_memory = coordination / "memory-repos" / "ar-repo-a"
    initialized_memory_repo(official_memory, "repo-a", "main", "main", old_base)
    git(official_memory, "checkout", "-b", "carryover-recovery")
    git(code_repo, "checkout", "feat/branch")
    contract = default_contract(
        ContractTask(
            name="carryover-recovery",
            repo_name="repo-a",
            coordination_root=coordination,
            workflow_kind="light-task",
            memory_mode="external",
        ),
        leaf=LeafIdentity(
            worktree_name="carryover-recovery",
            leaf_id="CARRYOVER-RECOVERY",
        ),
        code=RepoBranchPlan(
            repo_path=code_repo,
            source_branch="main",
            work_branch="feat/branch",
            base_commit=source_head,
        ),
        memory=RepoBranchPlan(
            repo_path=official_memory,
            source_branch="main",
            work_branch="carryover-recovery",
            base_commit=git(official_memory, "rev-parse", "HEAD"),
        ),
    )
    contract = replace(
        contract,
        code_worktree=code_repo,
        memory_worktree=official_memory,
        ledger_path=official_memory / "memory.md",
    )
    write_contract(contract.contract_path, contract)

    source_memory = coordination / "worktrees" / "repo-a" / "task-ar" / "memory-task"
    write_file_onboarding(source_memory / "onboarding", "repo-a", "feature.py", source_head)

    return {
        "contract_path": contract.contract_path.as_posix(),
        "source_memory": source_memory.as_posix(),
        "official_code_ref": "main",
        "source_code_ref": "feat/branch",
        "old_base": old_base,
    }


async def call_tool_over_stdio(
    settings_path: Path, tool: str, arguments: dict, timeout: float
) -> dict:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "agents_remember.mcp", "--config", str(settings_path)],
        env=dict(os.environ),
    )
    async with (
        stdio_client(params) as (read_stream, write_stream),
        ClientSession(read_stream, write_stream) as session,
    ):
        await asyncio.wait_for(session.initialize(), timeout=INITIALIZE_TIMEOUT)
        result = await asyncio.wait_for(session.call_tool(tool, arguments), timeout=timeout)
    text = "".join(block.text for block in result.content if isinstance(block, TextContent))
    return json.loads(text)


class StdioTransportTests(unittest.TestCase):
    def test_memory_carryover_plan_returns_over_stdio(self) -> None:
        """Regression for GitHub #49: the plan must come back within the bound,
        with the protocol pipes intact afterwards."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = build_carryover_fixture(root)
            settings_path = root / "harness" / "mcp-settings.json"
            write_json(settings_path, harness_settings_payload(root))

            payload = asyncio.run(
                call_tool_over_stdio(
                    settings_path,
                    "memory_carryover_plan",
                    {"repo_id": "repo-a", **fixture},
                    timeout=CALL_TIMEOUT,
                )
            )

            self.assertTrue(payload["ok"], msg=payload)
            self.assertEqual(payload["operation"], "memory_carryover_plan")
            self.assertEqual(payload["state"], "would-carryover")
            self.assertEqual(payload["counts"], {"auto-carry": 1})

    def test_ping_returns_over_stdio(self) -> None:
        """Baseline: a trivial tool proves the harness itself is sound."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "workspace" / "repo-a").mkdir(parents=True)
            init_repo(root / "workspace" / "repo-a", "main")
            settings_path = root / "harness" / "mcp-settings.json"
            write_json(settings_path, harness_settings_payload(root))

            payload = asyncio.run(
                call_tool_over_stdio(settings_path, "ping", {}, timeout=CALL_TIMEOUT)
            )

            self.assertTrue(payload["ok"], msg=payload)


if __name__ == "__main__":
    unittest.main()
