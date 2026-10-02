"""What a launch is expected to give an agent, for the dispatch-route cases of the launch tests."""

from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path
from typing import Any

from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.orca_launcher import OrcaDispatchRequest

# Label of a task reference -> the variable its tool server receives it in, and its name in the
# recovery note. The label's value is the reference as `<repository>/<document path>`.
REFERENCES: dict[str, tuple[str, str]] = {
    "ar.sprint-ref": ("AR_SPRINT_REF", "Sprint"),
    "ar.master-ref": ("AR_MASTER_REF", "Master"),
    "ar.task-ref": ("AR_TASK_REF", "Task"),
}


class GivenToAgentExpectations(unittest.TestCase):
    """Expected artifact, first message, tool server and recovery note of a launch.

    The launch test case supplies the fixture: its folder, settings, compiled first message, the
    source tree it presents as the launching build's, its fake runtime and its receipts.
    """

    root: Path
    config: McpRuntimeConfig
    prompt: str
    source: Path
    runtime: Any

    def receipt(self, request: OrcaDispatchRequest) -> dict[str, Any]:
        raise NotImplementedError

    def artifact(self, request: OrcaDispatchRequest) -> dict[str, Any]:
        """The reference of the handover artifact a launch of ``request`` writes."""

        path = (self.root / "reports" / f"{request.request_id}.handover.txt").as_posix()
        body = self.prompt.encode("utf-8")
        return {
            "path": path,
            "canonicalPath": path,
            "sha256": hashlib.sha256(body).hexdigest(),
            "bytes": len(body),
        }

    def first_message(self, request: OrcaDispatchRequest) -> str:
        """What the agent of ``request`` receives: one line naming the artifact, then its content."""

        artifact = self.artifact(request)
        return (
            f"AR handover artifact: {artifact['path']} (SHA-256 {artifact['sha256']}). It holds "
            "everything below this line; read that file again whenever your assignment is no "
            f"longer in your context.\n{self.prompt}"
        )

    def watch_launch(self, path: Path, observed: list[tuple[str, Any, Any, bool]]) -> None:
        """At every runtime call of a launch, record the receipt on disk and whether its artifact is."""

        def watch(command: str, payload: dict[str, Any]) -> None:
            saved = json.loads(path.read_text(encoding="utf-8"))
            stored = Path(saved["handoverArtifact"]["canonicalPath"]).is_file()
            observed.append((command, saved, payload, stored))

        self.runtime.observer = watch

    def given_to_agent(
        self, request: OrcaDispatchRequest, agent_id: str, task_labels: dict[str, str]
    ) -> tuple[dict[str, Any], str]:
        """The tool-server definition and the recovery note a launch passes to the runtime."""

        return (
            self.tool_server(request, agent_id, task_labels),
            self.recovery_note(request, task_labels),
        )

    def tool_server(
        self, request: OrcaDispatchRequest, agent_id: str, task_labels: dict[str, str]
    ) -> dict[str, Any]:
        """The one tool server a launch defines: the launching build's, with the agent's binding."""

        references = {
            REFERENCES[label][0]: json.dumps(
                dict(zip(("repository", "path"), key.split("/", 1), strict=True)),
                separators=(",", ":"),
            )
            for label, key in task_labels.items()
        }
        return {
            "type": "stdio",
            "command": sys.executable,
            "args": ["-m", "agents_remember.mcp", "--config", self.config.config_path.as_posix()],
            "env": {
                "PYTHONPATH": self.source.parent.as_posix(),
                "AR_PASEO_AGENT_ID": agent_id,
                "AR_ROLE": request.role,
                "AR_REQUEST_ID": str(request.request_id),
                "AR_REPORT_PATH": (self.root / "reports" / f"{request.request_id}.md").as_posix(),
                **references,
            },
        }

    def recovery_note(self, request: OrcaDispatchRequest, task_labels: dict[str, str]) -> str:
        artifact = self.artifact(request)
        named = [f"{REFERENCES[label][1]} {key}." for label, key in task_labels.items()]
        return " ".join(
            [
                f"AR role agent: {request.role}.",
                *(named or ["No task reference."]),
                f"Assignment file: {artifact['path']} (SHA-256 {artifact['sha256']}).",
                "Reload that file whenever your assignment is not in your context.",
            ]
        )

    def assert_applied_is_recorded(
        self, request: OrcaDispatchRequest, definition: dict[str, Any], note: str
    ) -> None:
        """The receipt says what the agent was given: tool server, recovery note, artifact."""

        receipt = self.receipt(request)
        artifact = self.artifact(request)
        self.assertEqual(
            receipt["toolServer"],
            {
                "name": "agents-remember-task",
                "applied": True,
                "detail": "tool server applied",
                "command": [definition["command"], *definition["args"]],
                "environment": definition["env"],
                "sourceRoot": self.source.as_posix(),
            },
        )
        self.assertEqual(receipt["recoveryNote"], note)
        self.assertEqual(receipt["handoverArtifact"], artifact)
        self.assertEqual(Path(artifact["path"]).read_text(encoding="utf-8"), self.prompt)
