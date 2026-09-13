from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import cast
from unittest.mock import patch

from mcp.server.fastmcp import FastMCP

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
MCP_TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(MCP_SRC))
sys.path.insert(0, str(MCP_TESTS))

from agents_remember.application.provider_tools import (
    GrepaiRepoScope,
    GrepaiSearchQuery,
    GrepaiTraceQuery,
    ProviderQueryScope,
)
from agents_remember.kernel import memory_init as memory_init_module
from agents_remember.kernel.primitives.runtime_config import (
    McpRuntimeConfig,
    load_config,
)
from agents_remember.mcp import SERVER_VERSION
from agents_remember.mcp.registration import TOOL_REGISTRARS
from agents_remember.mcp.tools import (
    PUBLIC_TOOLS,
    cgc_callees_payload,
    cgc_callers_payload,
    grepai_search_payload,
    grepai_trace_payload,
    memory_init_payload,
    ping_payload,
    server_info_payload,
)
from agents_remember.models.tools.tool_registry import PUBLIC_TOOL_RESPONSE_MODELS
from agents_remember.models.tools.tool_response import finalize_tool_response
from agents_remember.serving.build_info import ServingBuild
from test_config import settings_payload

DRY_RUN_SCOPE = ProviderQueryScope(dry_run=True)


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


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


REAL_MCP_CONFIG = os.environ.get("AGENTS_REMEMBER_REAL_MCP_CONFIG")


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


class PublicSurfaceInventoryTests(unittest.TestCase):
    """The advertised inventory and the live registration must agree, in order.

    The ``server_info`` payload reports ``PUBLIC_TOOLS`` itself, so its own assertion is
    self-referential and cannot catch a tool that is registered but missing from the inventory.
    That is exactly how ``worktree_record_landing`` shipped advertised-but-unusable: absent from
    ``PUBLIC_TOOLS`` and absent from ``TOOL_RESPONSE_MODELS``, where ``finalize_tool_response``
    indexes by tool name -- so the tool was listed and could not return a payload at all.
    """

    def test_live_registration_matches_the_public_inventory_in_order(self) -> None:
        server = FastMCP("inventory-probe")
        for register_tools in TOOL_REGISTRARS:
            register_tools(server, _permissive_registration_config())

        live = tuple(tool.name for tool in asyncio.run(server.list_tools()))

        # FastMCP publishes in registration order, so this comparison is ordered on purpose:
        # a tool registered into the wrong group is a reordering bug, not just a missing row.
        self.assertEqual(live, PUBLIC_TOOLS)

    def test_worktree_record_landing_has_a_response_model_that_validates(self) -> None:
        self.assertEqual(set(PUBLIC_TOOL_RESPONSE_MODELS), set(PUBLIC_TOOLS))
        # A name in the inventory but missing from the registry raises KeyError here, which the
        # surface comparison above cannot see on its own.
        envelope = finalize_tool_response(
            "worktree_record_landing",
            {
                "ok": True,
                "state": "recorded",
                "taskId": "T",
                "taskName": "task",
                "contractPath": "/tmp/contract.md",
                "integrationStrategy": "pr",
                "landedCodeCommit": "a" * 40,
                "landingTargets": ["main"],
                "summary": "recorded",
            },
        )
        self.assertEqual(envelope["operation"], "worktree_record_landing")

    def test_worktree_checkpoint_landing_has_a_response_model_that_validates(self) -> None:
        # The set comparison above proves the name is registered, but not which model it points
        # at: the two landing tools sit next to each other in the registry and their payloads
        # differ only in the operation literal, so a swap between them still validates as a set.
        envelope = finalize_tool_response(
            "worktree_checkpoint_landing",
            {
                "ok": True,
                "state": "checkpointed",
                "taskId": "T",
                "taskName": "master",
                "contractPath": "/tmp/contract.md",
                "integrationStrategy": "ff-only",
                "integratedCodeCommit": "a" * 40,
                "integratedMemoryContentCommit": "",
                "integratedLedgerCommit": "",
                "summary": "checkpointed",
            },
        )
        self.assertEqual(envelope["operation"], "worktree_checkpoint_landing")

    def test_the_checkpoint_description_publishes_rather_than_pausing(self) -> None:
        """The agent-facing text must not route an ordinary stop into a publication.

        ``worktree_checkpoint_landing`` moves the master's committed refs onto its super branch,
        where every other master sees them. Describing it as the way to pause a master invites an
        agent to publish unfinished work for an ordinary stop request -- changes the developer never
        asked to publish. A pause publishes nothing and moves no ref, so the description has to
        present a partial publication and say the pause is a separate matter.
        """

        server = FastMCP("description-probe")
        for register_tools in TOOL_REGISTRARS:
            register_tools(server, _permissive_registration_config())
        descriptions = {
            tool.name: (tool.description or "") for tool in asyncio.run(server.list_tools())
        }

        description = descriptions["worktree_checkpoint_landing"]

        self.assertNotIn("Use this to pause", description)
        self.assertIn("PUBLISH", description)
        self.assertIn("not a pause", description)
        self.assertIn("separate matter and is NOT this call", description)


def _permissive_registration_config() -> McpRuntimeConfig:
    """A registration-time config stub.

    Every registrar only closes over the config; none validates it while registering. A
    permissive chain keeps this test about the inventory rather than about building a runtime.
    """

    class _Stub:
        def __getattr__(self, name: str) -> object:
            return _Stub()

    return cast(McpRuntimeConfig, _Stub())


if __name__ == "__main__":
    unittest.main()
