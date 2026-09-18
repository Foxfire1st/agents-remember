"""`citation_migrate` is reachable as an MCP tool, not only as a CLI subcommand.

Recorded defect (S0 of `260915-CAPS-L21`): ``application/memory_tools.citation_migrate_tool``
existed with exactly one caller, the ``memory-citations --migrate`` CLI subcommand, and no MCP
registration. A running server could therefore not reach it at all, and the CLI could not reach
a live leaf either: from the primary checkout it is refused outright, and from a linked worktree
it is confined to a disposable coordination root that cannot contain a leaf contract. The result
was 481 superseded citation tables that no governed route could convert for six weeks.

These tests pin the two halves of the repair -- the tool is registered and advertised, and the
registered handler actually reaches ``migrate_onboarding_root`` -- so the wiring cannot be
dropped again without a failure that names it.
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
from agents_remember.kernel.primitives.runtime_config import (
    McpRuntimeConfig,
    RepositoryScope,
)
from agents_remember.mcp.registration.memory import register_memory_tools
from agents_remember.mcp.tools import PUBLIC_TOOLS, citation_migrate_payload
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
    """The server advertises the tool, and the handler is the application entry point."""

    def test_the_tool_is_registered_advertised_and_has_a_response_model(self) -> None:
        """All three surfaces together: a name in one and not another is the recorded defect."""
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            config = _config(tmp, code_root=tmp / "code", memory_root=tmp / "memory")
            server = register_memory_tools.__globals__["FastMCP"]("test")

            register_memory_tools(server, config)

            names = {tool.name for tool in asyncio.run(server.list_tools())}
        self.assertIn("citation_migrate", names)
        self.assertIn("citation_migrate", PUBLIC_TOOLS)
        self.assertIn("citation_migrate", PUBLIC_TOOL_RESPONSE_MODELS)

    def test_the_payload_builder_reaches_the_application_tool(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            config = _config(tmp, code_root=tmp / "code", memory_root=tmp / "memory")
            with mock.patch(
                "agents_remember.mcp.tools.memory.citation_migrate_tool",
                # The real tool validates its scope before resolving anything, so the double
                # answers the same shape the application entry point returns.
                return_value={
                    "ok": False,
                    "repoId": "agents-remember",
                    "operation": "citation_migrate",
                    "declinedCount": 0,
                },
            ) as reached:
                payload = citation_migrate_payload(
                    config,
                    "agents-remember",
                    contract_path="tasks/x/enclosures/leaf/series-contract.md",
                    dry_run=True,
                )
        reached.assert_called_once()
        self.assertEqual(reached.call_args.kwargs["dry_run"], True)
        self.assertEqual(payload["operation"], "citation_migrate")


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
