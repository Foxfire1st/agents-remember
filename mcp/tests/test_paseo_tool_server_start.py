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


# What a harness that passes its own environment on could hand the tool server: another source
# tree, a seat identity, and task references of some other launch.
INHERITED = {
    "PYTHONPATH": "/another/ar/checkout/mcp/src",
    "AR_SPAWN_ROLE": "worker",
    "AR_HOSTED_SESSION_ID": "seat-of-another-launch",
    "AR_MASTER_REF": '{"repository":"sandbox-app","path":"other-master/task.json"}',
    "AR_TASK_REF": '{"repository":"sandbox-app","path":"other-master/01_other-leaf.json"}',
}
# Files an agent could leave in its working directory: a package named like the build's, and a
# module named like one the tool server imports. Loaded, either would end the server at once.
DECOYS = {
    "agents_remember/__init__.py": "raise SystemExit('the decoy package was loaded')\n",
    "agents_remember/mcp/__init__.py": "",
    "agents_remember/mcp/__main__.py": "raise SystemExit('the decoy module was run')\n",
    "json.py": "raise SystemExit('the loose json.py was loaded')\n",
}


async def _server_info(definition: dict[str, Any], cwd: Path) -> tuple[dict[str, Any], str | None]:
    """The server's ``server_info`` answer and the instructions it stated when it was opened."""

    # A harness adds the definition's environment to its own and starts the command in the
    # agent's working directory.
    server = StdioServerParameters(
        command=definition["command"],
        args=definition["args"],
        env={**os.environ, **INHERITED, **definition["env"]},
        cwd=cwd,
    )
    async with stdio_client(server) as (reader, writer), ClientSession(reader, writer) as session:
        opened = await session.initialize()
        result = await session.call_tool("server_info", {})
    (content,) = result.content
    assert isinstance(content, TextContent)
    return json.loads(content.text), opened.instructions


class ToolServerStartTests(unittest.TestCase):
    def test_the_definition_starts_this_builds_tool_server_which_reports_source_and_binding(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            for folder in ("coordination", "projects", "settings"):
                (root / folder).mkdir()
            for name, text in DECOYS.items():
                decoy = root / "projects" / name
                decoy.parent.mkdir(parents=True, exist_ok=True)
                decoy.write_text(text, encoding="utf-8")
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

            info, instructions = asyncio.run(
                asyncio.wait_for(
                    _server_info(definition, root / "projects"), timeout=START_TIMEOUT_SECONDS
                )
            )

        self.assertTrue(info["ok"])
        # The server that answers is this build's, although its working directory holds a package
        # and a module of the same names: same source tree, the settings it was given. Its
        # binding is the launch's alone, with nothing of what it inherited.
        self.assertEqual(info["servingBuild"]["packageRoot"], launching_source_root().as_posix())
        self.assertEqual(info["configPath"], settings.as_posix())
        self.assertEqual(info["coordinationRoot"], (root / "coordination").as_posix())
        # It states who it is when a harness opens it, in the spelling of the agent's assignment.
        self.assertEqual(
            instructions,
            "This server is agents-remember-task: the Agents Remember tool server of the AR "
            "build that launched this agent, and the one to use for this assignment.",
        )
        # It says under which name its agent was given it; ``server`` is the package's name.
        self.assertEqual(
            (info["server"], info["toolServer"]), ("agents-remember", "agents-remember-task")
        )
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
