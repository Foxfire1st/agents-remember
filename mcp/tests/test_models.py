from __future__ import annotations

import sys
import unittest
from pathlib import Path

from pydantic import BaseModel

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.mcp.tools import PUBLIC_TOOLS
from agents_remember.models.tools.tool_registry import PUBLIC_TOOL_RESPONSE_MODELS


class PublicToolResponseModelTests(unittest.TestCase):
    def test_every_public_tool_has_a_response_model(self) -> None:
        self.assertEqual(set(PUBLIC_TOOLS), set(PUBLIC_TOOL_RESPONSE_MODELS))

    def test_every_public_tool_response_model_generates_json_schema(self) -> None:
        for tool_name, model in PUBLIC_TOOL_RESPONSE_MODELS.items():
            with self.subTest(tool=tool_name):
                self.assertTrue(issubclass(model, BaseModel))
                schema = model.model_json_schema()
                self.assertEqual(schema["type"], "object")
                self.assertIn("properties", schema)


if __name__ == "__main__":
    unittest.main()
