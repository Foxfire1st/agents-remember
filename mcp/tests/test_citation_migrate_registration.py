"""`citation_migrate` lives on as an application operation and CLI subcommand.

Recorded defect (S0 of `260915-CAPS-L21`): ``application/memory_tools.citation_migrate_tool``
existed with exactly one caller, the ``memory-citations --migrate`` CLI subcommand, and no MCP
registration. The repair registered it as an MCP tool; MIK-R112 removes the tool again while
the function stays until MIK-R65 removes the legacy path, so the wiring pin below now covers
``citation_fix`` (which stays a tool) and the application-level migration reachability that
the CLI still needs.
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.application.memory_tools import (
    CitationOperationScope,
    citation_migrate_tool,
)
from agents_remember.errors import AuthorityError, ConfiguredContractAuthorityError
from agents_remember.kernel.primitives.runtime_config import (
    McpRuntimeConfig,
    RepositoryScope,
)
from agents_remember.mcp.registration.memory import register_memory_tools
from agents_remember.mcp.tools import PUBLIC_TOOLS, citation_fix_payload
from agents_remember.models.tools.tool_registry import (
    PUBLIC_TOOL_RESPONSE_MODELS,
)


def _config(tmp: Path, *, code_root: Path, memory_root: Path) -> McpRuntimeConfig:
    coordination = (tmp / "coord").resolve()
    coordination.mkdir(parents=True, exist_ok=True)
    return McpRuntimeConfig(
        config_path=coordination / "mcp.settings.json",
        coordination_root=coordination,
        workspace_root=(tmp / "ws").resolve(),
        transcript_root=coordination / "logs",
        repositories={
            "agents-remember": RepositoryScope(
                repo_id="agents-remember",
                path=code_root,
                memory_root=memory_root,
            )
        },
    )


class CitationMigrateRegistrationTests(unittest.TestCase):
    """The tool is gone; the sibling wiring pin stays."""

    def test_the_tool_is_not_registered_advertised_or_modelled(self) -> None:
        """MIK-R112 removed the `citation_migrate` tool; the function stays till MIK-R65."""
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            config = _config(tmp, code_root=tmp / "code", memory_root=tmp / "memory")
            server = register_memory_tools.__globals__["FastMCP"]("test")

            register_memory_tools(server, config)

            names = {tool.name for tool in asyncio.run(server.list_tools())}
        self.assertNotIn("citation_migrate", names)
        self.assertNotIn("citation_migrate", PUBLIC_TOOLS)
        self.assertNotIn("citation_migrate", PUBLIC_TOOL_RESPONSE_MODELS)

    def test_citation_tools_name_a_configured_contract_authority_refusal(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            config = _config(tmp, code_root=tmp / "code", memory_root=tmp / "memory")
            contract = "tasks/x/enclosures/leaf/series-contract.md"
            for tool, payload_builder in (("citation_fix", citation_fix_payload),):
                with (
                    self.subTest(tool=tool),
                    mock.patch(
                        f"agents_remember.mcp.tools.memory.{tool}_tool",
                        side_effect=ConfiguredContractAuthorityError(
                            side="memory", name="candidate"
                        ),
                    ),
                ):
                    payload = payload_builder(
                        config, "agents-remember", contract_path=contract, dry_run=True
                    )
                    self.assertFalse(payload["ok"])
                    # The delivered answer carries the envelope strip: no
                    # operation key rides to the agent (rule 2); the refusal
                    # proof reads the surviving identity fields.
                    self.assertNotIn("operation", payload)
                    self.assertEqual(payload["status"], "configured-contract-authority-invalid")
                    self.assertEqual(payload["repoId"], "agents-remember")
                    self.assertEqual(payload["contractPath"], contract)
                    self.assertIn("memory candidate", payload["detail"])
                    self.assertEqual(payload["nextAction"], "developer-decision")

    def test_citation_tools_propagate_other_authority_errors(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            config = _config(tmp, code_root=tmp / "code", memory_root=tmp / "memory")
            for tool, payload_builder in (("citation_fix", citation_fix_payload),):
                error = AuthorityError("unrelated authority failure")
                with (
                    self.subTest(tool=tool),
                    mock.patch(f"agents_remember.mcp.tools.memory.{tool}_tool", side_effect=error),
                ):
                    with self.assertRaises(AuthorityError) as raised:
                        payload_builder(
                            config,
                            "agents-remember",
                            contract_path="tasks/x/enclosures/leaf/series-contract.md",
                            dry_run=True,
                        )
                    self.assertIs(raised.exception, error)


class CitationMigrateReachesTheMigrationTests(unittest.TestCase):
    """One real call through the tool reaches ``migration.migrate_onboarding_root``."""

    def test_the_tool_invokes_the_migration_over_the_leaf_memory_onboarding_root(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw).resolve()
            coordination = tmp / "coord"
            memory_worktree = coordination / "worktrees" / "memory"
            code_worktree = coordination / "worktrees" / "code"
            (memory_worktree / "onboarding").mkdir(parents=True)
            code_worktree.mkdir(parents=True)
            contract_path = coordination / "tasks" / "enclosures" / "leaf" / "series-contract.md"
            contract_path.parent.mkdir(parents=True)
            contract_path.write_text("placeholder", encoding="utf-8")
            config = _config(tmp, code_root=code_worktree, memory_root=memory_worktree)

            scope = mock.Mock()
            scope.repo_id = "agents-remember"
            scope.onboarding_root = memory_worktree / "onboarding"
            scope.code_root = code_worktree
            scope.cache_authority = None

            with (
                mock.patch(
                    "agents_remember.application.memory_tools._leaf_memory_writer_scope",
                    return_value=scope,
                ) as guarded,
                mock.patch(
                    "agents_remember.application.memory_tools.migration.migrate_onboarding_root",
                    return_value={"operation": "citation_migrate", "declinedCount": 0},
                ) as migration,
            ):
                result = citation_migrate_tool(
                    config,
                    repo_id="agents-remember",
                    contract_path=str(contract_path),
                    dry_run=False,
                    operation_scope=CitationOperationScope(),
                )

            # The write guard runs FIRST and names the operation it is guarding, so the
            # migration can never be reached on a path that skipped the enclosure check.
            self.assertEqual(guarded.call_args.kwargs["operation"], "citation_migrate")
            migration.assert_called_once()
            args, kwargs = migration.call_args
            self.assertEqual(args[0], scope.onboarding_root)
            self.assertEqual(args[1].code_root, code_worktree)
            self.assertEqual(args[1].memory_root, memory_worktree)
            self.assertEqual(kwargs["only"], None)
            self.assertEqual(kwargs["dry_run"], False)
        self.assertEqual(result["repoId"], "agents-remember")


if __name__ == "__main__":
    unittest.main()
