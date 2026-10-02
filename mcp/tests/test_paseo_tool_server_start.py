"""The tool-server definition of a launch, started for real over stdio as a harness starts it."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any

from agents_remember.application.agent_binding import AgentBinding
from agents_remember.cli.paseo_launch import launching_source_root, tool_server_definition
from agents_remember.models.task_document_ref import TaskDocumentRef
from mcp.client.stdio import stdio_client
from mcp.types import TextContent

from mcp import ClientSession, StdioServerParameters

START_TIMEOUT_SECONDS = 120


async def _server_info(definition: dict[str, Any], cwd: Path) -> dict[str, Any]:
    # A harness adds the definition's environment to its own and starts the command.
    server = StdioServerParameters(
        command=definition["command"],
        args=definition["args"],
        env={**os.environ, **definition["env"]},
        cwd=cwd,
    )
    async with stdio_client(server) as (reader, writer), ClientSession(reader, writer) as session:
        await session.initialize()
        result = await session.call_tool("server_info", {})
    (content,) = result.content
    assert isinstance(content, TextContent)
    return json.loads(content.text)


class ToolServerStartTests(unittest.TestCase):
    def test_the_definition_starts_this_builds_tool_server_which_reports_source_and_binding(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            for folder in ("coordination", "projects", "settings"):
                (root / folder).mkdir()
            settings = root / "settings" / "agents-remember-settings.json"
            settings.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "coordinationRoot": (root / "coordination").as_posix(),
                        "workspaceRoot": (root / "projects").as_posix(),
                        "repositories": {},
                        "providers": {},
                        "dashboard": {"autoStart": False},
                    }
                ),
                encoding="utf-8",
            )
            binding = AgentBinding(
                agent_id="f3c1a2b4-5d6e-4f70-8a91-b2c3d4e5f607",
                role="orchestrator",
                request_id="0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d",
                report_path=(root / "projects" / "report.md").as_posix(),
                sprint_ref=TaskDocumentRef(repository="sandbox-app", path="sbx-sprint/task.json"),
            )
            definition = tool_server_definition(settings, binding)

            info = asyncio.run(
                asyncio.wait_for(
                    _server_info(definition, root / "projects"), timeout=START_TIMEOUT_SECONDS
                )
            )

        self.assertTrue(info["ok"])
        # The server that answers is this build's: same source tree, the settings it was given.
        self.assertEqual(info["servingBuild"]["packageRoot"], launching_source_root().as_posix())
        self.assertEqual(info["configPath"], settings.as_posix())
        self.assertEqual(info["coordinationRoot"], (root / "coordination").as_posix())
        self.assertEqual(
            info["agentBinding"],
            {
                "agentId": binding.agent_id,
                "role": "orchestrator",
                "requestId": binding.request_id,
                "reportPath": binding.report_path,
                "sprintDocumentRef": {"repository": "sandbox-app", "path": "sbx-sprint/task.json"},
            },
        )


if __name__ == "__main__":
    unittest.main()
