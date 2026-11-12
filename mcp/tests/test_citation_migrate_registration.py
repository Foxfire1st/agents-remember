"""Retired citation migration stays absent; converted fixer wiring stays intact."""

from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.application import memory_tools
from agents_remember.cli import memory_citations
from agents_remember.errors import AuthorityError, ConfiguredContractAuthorityError
from agents_remember.kernel.primitives.runtime_config import (
    McpRuntimeConfig,
    RepositoryScope,
)
from agents_remember.mcp.registration.memory import register_memory_tools
from agents_remember.mcp.tools import PUBLIC_TOOLS, citation_fix_payload
from agents_remember.memory_quality import check, converted_check
from agents_remember.models.tools.tool_registry import (
    TOOL_RESPONSE_MODELS,
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
        """MIK-R112 removed the tool; MIK-R65 also removes its legacy application path."""
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            config = _config(tmp, code_root=tmp / "code", memory_root=tmp / "memory")
            server = register_memory_tools.__globals__["FastMCP"]("test")

            register_memory_tools(server, config)

            names = {tool.name for tool in asyncio.run(server.list_tools())}
        self.assertNotIn("citation_migrate", names)
        self.assertNotIn("citation_migrate", PUBLIC_TOOLS)
        self.assertNotIn("citation_migrate", TOOL_RESPONSE_MODELS)
        self.assertFalse(hasattr(memory_tools, "citation_migrate_tool"))
        self.assertFalse(hasattr(memory_tools, "citation_source_index_build_tool"))
        self.assertFalse(hasattr(converted_check, "LEGACY_FORMAT_CHECKS"))
        self.assertFalse(hasattr(converted_check, "not_applicable_on_converted"))
        retired = (
            "changed_lines.py",
            "update_history",
            "citations/candidate/git_source.py",
            "citations/claim_change_router.py",
            "citations/claim_reopen.py",
            "citations/deterministic_projection.py",
            "citations/documents/transaction.py",
            "citations/drafts.py",
            "citations/editing.py",
            "citations/exclusion_register.py",
            "citations/citation_index_settings.py",
            "citations/fixer.py",
            "citations/migration.py",
            "citations/old_form.py",
            "citations/prose.py",
            "citations/provenance.py",
            "citations/range_resolution.py",
            "citations/repair.py",
            "citations/resolution.py",
            "citations/source_index.py",
            "citations/source_index_database.py",
            "citations/source_index_state.py",
            "citations/structures.py",
            "citations/symbol_index.py",
            "citations/work_order.py",
        )
        style = MCP_SRC / "agents_remember/memory_quality/style"
        for relative in retired:
            if relative == "update_history":
                self.assertEqual(list((style / relative).rglob("*.py")), [], relative)
            else:
                self.assertFalse((style / relative).exists(), relative)
        self.assertEqual(len(check.STYLE_CHECKS), 3)
        self.assertTrue(all("citations" not in name for name in check.AVAILABLE_CHECKS))
        schema = next(
            tool for tool in asyncio.run(server.list_tools()) if tool.name == "citation_fix"
        )
        self.assertNotIn("expected_snapshot", schema.inputSchema["properties"])
        self.assertNotIn("exclude", schema.inputSchema["properties"])
        parser = argparse.ArgumentParser()
        memory_citations.add_arguments(parser)
        options = {option for action in parser._actions for option in action.option_strings}
        self.assertFalse({"--build-index", "--expected-snapshot", "--exclude"} & options)

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


if __name__ == "__main__":
    unittest.main()
