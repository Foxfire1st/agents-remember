"""Tests for the FastMCP compact-content shim.

The shim re-serializes the unstructured JSON ``TextContent`` mirror of a tool
result without FastMCP's hardcoded ``indent=2`` whitespace, while leaving the
``structuredContent`` half untouched.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import anyio
from mcp.server.fastmcp.utilities import func_metadata
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import TextContent

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
MCP_TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(MCP_SRC))
sys.path.insert(0, str(MCP_TESTS))

from agents_remember.kernel.primitives.runtime_config import (
    load_config,
)
from agents_remember.mcp.compact_content import install_compact_content
from agents_remember.mcp.server import create_server
from test_config import settings_payload


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


class CompactContentShimTests(unittest.TestCase):
    def test_install_is_idempotent(self) -> None:
        install_compact_content()
        first = func_metadata._convert_to_content
        install_compact_content()
        self.assertIs(func_metadata._convert_to_content, first)

    def test_non_json_text_blocks_pass_through(self) -> None:
        install_compact_content()
        [block] = func_metadata._convert_to_content("not json")
        assert isinstance(block, TextContent)
        self.assertEqual(block.text, "not json")

    def test_tool_call_text_block_is_compact_and_matches_structured(self) -> None:
        # create_server installs the shim; constructing it here exercises that path.
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            path = root / ".codex" / "mcp" / "settings.json"
            _write_json(path, settings_payload(root))
            config = load_config(path)
            server = create_server(config)

            result = anyio.run(self._call_ping, server)

        text_blocks = [b for b in result.content if isinstance(b, TextContent)]
        self.assertTrue(text_blocks, "expected a text content block")
        text = text_blocks[0].text

        # The shim strips FastMCP's pretty-printing: valid JSON, no newlines.
        self.assertNotIn("\n", text)
        parsed = json.loads(text)

        # The compacted text mirrors the untouched structuredContent.
        self.assertIsNotNone(result.structuredContent)
        self.assertEqual(parsed, result.structuredContent)
        self.assertTrue(parsed["ok"])

    @staticmethod
    async def _call_ping(server):
        async with create_connected_server_and_client_session(server._mcp_server) as client:
            return await client.call_tool("ping", {})


if __name__ == "__main__":
    unittest.main()
