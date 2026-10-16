"""The fake Paseo runtime of the launch tests, and what a launch is expected to give an agent."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
import unittest
from pathlib import Path
from typing import Any

from agents_remember.cli.paseo_bridge import PaseoBridgeFailure
from agents_remember.kernel.primitives.paseo_host_contract import PASEO_VERSION
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.role_launcher import RoleDispatchRequest
from paseo_runtime_test_support import runtime_settings, write_shared_runtime

SERVER_ID = "srv_configured"
CATALOG: dict[str, Any] = {
    "runtime": {"serverId": SERVER_ID, "version": PASEO_VERSION},
    "providers": [
        {
            "id": "codex",
            "label": "Codex",
            "modes": [],
            "models": [
                {
                    "id": "gpt-a",
                    "label": "GPT A",
                    "efforts": [{"id": "low", "label": "Low"}, {"id": "high", "label": "High"}],
                }
            ],
        },
        # A provider that reports no models is launched without one. The runtime reports this one
        # as taking no tool servers from its host.
        {"id": "eve", "label": "Eve", "models": [], "modes": [], "acceptsToolServers": False},
    ],
}
LAUNCH_COMMANDS = {"agent-archive", "workspace-open", "agent-create"}
# Label of a task reference -> the variable its tool server receives it in, and its name in the
# recovery note. The label's value is the reference as `<repository>/<document path>`.
REFERENCES: dict[str, tuple[str, str]] = {
    "ar.sprint-ref": ("AR_SPRINT_REF", "Sprint"),
    "ar.master-ref": ("AR_MASTER_REF", "Master"),
    "ar.task-ref": ("AR_TASK_REF", "Task"),
}


class FakeRuntime:
    """Stands in for ``bridge_call``: a small in-memory Paseo runtime that records every call.

    A queued failure is raised instead of the command's answer. With ``after_effect`` the command
    takes effect first, as when the runtime acted and its answer was lost.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.agents: dict[str, dict[str, Any]] = {}
        self.workspaces: dict[str, str] = {}
        self.failures: dict[str, list[tuple[PaseoBridgeFailure, bool]]] = {}
        self.observer: Any = None
        self.opened_directory: str | None = None
        self.creation_error: str | None = None
        # Agents a listing did not reach: with any, the listing reports itself as not ended.
        self.unlisted: set[str] = set()

    def fail(self, command: str, code: str, message: str = "", after_effect: bool = False) -> None:
        failure = PaseoBridgeFailure(code, message or f"{command} failed with {code}")
        self.failures.setdefault(command, []).append((failure, after_effect))

    def __call__(
        self, _config: McpRuntimeConfig, command: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        self.calls.append((command, copy.deepcopy(payload)))
        if self.observer is not None and command in LAUNCH_COMMANDS:
            self.observer(command, payload)
        queued = self.failures.get(command) or []
        failure, after_effect = queued.pop(0) if queued else (None, False)
        if failure is not None and not after_effect:
            raise failure
        reply = getattr(self, "_" + command.replace("-", "_"))(payload)
        if failure is not None:
            raise failure
        return copy.deepcopy(reply)

    def launch_calls(self) -> list[tuple[str, dict[str, Any]]]:
        return [call for call in self.calls if call[0] in LAUNCH_COMMANDS]

    def _catalog(self, _payload: dict[str, Any]) -> dict[str, Any]:
        return CATALOG

    def _workspace_open(self, payload: dict[str, Any]) -> dict[str, Any]:
        cwd = payload["cwd"]
        key = self.workspace_key(payload)
        preparation = (
            ("found" if key in self.workspaces else "created")
            if "masterProject" in payload
            else "opened"
        )
        workspace_id = self.workspaces.setdefault(key, f"wks_{len(self.workspaces) + 1}")
        return {
            "serverId": SERVER_ID,
            "preparation": preparation,
            "workspace": {"id": workspace_id, "directory": self.opened_directory or cwd},
        }

    @staticmethod
    def workspace_key(payload: dict[str, Any]) -> str:
        if "masterProject" not in payload:
            return payload["cwd"]
        return f"{payload['masterProject']['key']}:{payload['task']['key']}"

    def _agent_create(self, payload: dict[str, Any]) -> dict[str, Any]:
        agent_id = payload["agentId"]
        existing = agent_id in self.agents
        if not existing:
            self.agents[agent_id] = {
                "id": agent_id,
                "provider": payload["provider"],
                # What the runtime applied: the provider's own default when no model was given.
                "model": payload.get("model") or f"{payload['provider']}-default",
                "thinkingOptionId": payload.get("thinkingOptionId"),
                **(
                    {"serviceTier": payload["featureValues"]["service_tier"]}
                    if "service_tier" in payload.get("featureValues", {})
                    else {}
                ),
                **(
                    {"featureValues": dict(payload["featureValues"])}
                    if "featureValues" in payload
                    else {}
                ),
                **({"modeId": payload["modeId"]} if "modeId" in payload else {}),
                "title": payload["title"],
                "labels": payload["labels"],
                "workspaceId": payload["workspaceId"],
                "status": "running",
                "archivedAt": None,
            }
        return {
            "serverId": SERVER_ID,
            "existing": existing,
            "agent": self.agents[agent_id],
            **({"creationError": self.creation_error} if self.creation_error else {}),
        }

    def _agent_archive(self, payload: dict[str, Any]) -> dict[str, Any]:
        agent = self.agents.get(payload["agentId"])
        if agent is None:
            return {"serverId": SERVER_ID, "agentId": payload["agentId"], "found": False}
        agent["archivedAt"] = agent["archivedAt"] or "2026-10-02T00:00:00.000Z"
        agent["status"] = "closed"
        return {"serverId": SERVER_ID, "agentId": agent["id"], "found": True, "archived": True}

    def _agents_live(self, _payload: dict[str, Any]) -> dict[str, Any]:
        live = [
            agent["id"]
            for agent in self.agents.values()
            if not agent["archivedAt"] and agent["id"] not in self.unlisted
        ]
        return {"serverId": SERVER_ID, "agentIds": live, "complete": not self.unlisted}

    def _agent_state(self, payload: dict[str, Any]) -> dict[str, Any]:
        agent = self.agents.get(payload["agentId"])
        return {"serverId": SERVER_ID, "agent": self.state(agent) if agent else None}

    def _agent_resume(self, payload: dict[str, Any]) -> dict[str, Any]:
        agent = self.agents.get(payload["agentId"])
        resume: dict[str, Any] = {"attempted": False, "resumed": False}
        if agent and agent["status"] == "closed" and not agent["archivedAt"]:
            refusal = agent.get("resumeRefusal")
            if not refusal:
                agent["status"] = "idle"
            resume = {"attempted": True, "resumed": not refusal}
            if refusal:
                resume["error"] = refusal
        return {
            "serverId": SERVER_ID,
            "agent": self.state(agent) if agent else None,
            "resume": resume,
        }

    @staticmethod
    def state(agent: dict[str, Any]) -> dict[str, Any]:
        """The bridge's state of an agent; a test sets `status` and what goes with it."""

        permissions = agent.get("pendingPermissions", [])
        return {
            "id": agent["id"],
            "status": agent["status"],
            "archivedAt": agent["archivedAt"],
            # A pending permission request belongs to a turn that is still active.
            "turnActive": agent["status"] == "running",
            "pendingPermissions": permissions,
            "lastError": agent.get("lastError"),
            "attentionReason": agent.get("attentionReason"),
            # The last turn is readable only while the agent is idle with an open session.
            "lastTurn": agent.get("lastTurn", {"state": "none"})
            if agent["status"] == "idle"
            else None,
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

    def receipt(self, request: RoleDispatchRequest) -> dict[str, Any]:
        raise NotImplementedError

    def artifact(self, request: RoleDispatchRequest) -> dict[str, Any]:
        """The reference of the handover artifact a launch of ``request`` writes."""

        path = (self.root / "reports" / f"{request.request_id}.handover.txt").as_posix()
        body = self.prompt.encode("utf-8")
        return {
            "path": path,
            "canonicalPath": path,
            "sha256": hashlib.sha256(body).hexdigest(),
            "bytes": len(body),
        }

    def first_message(self, request: RoleDispatchRequest) -> str:
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
        self, request: RoleDispatchRequest, agent_id: str, task_labels: dict[str, str]
    ) -> tuple[dict[str, Any], str]:
        """The tool-server definition and the recovery note a launch passes to the runtime."""

        return (
            self.tool_server(request, agent_id, task_labels),
            self.recovery_note(request, task_labels),
        )

    def tool_server(
        self, request: RoleDispatchRequest, agent_id: str, task_labels: dict[str, str]
    ) -> dict[str, Any]:
        """The one tool server a launch defines: the launching build's, with the agent's binding."""

        # Every reference variable is set; one the selection does not have is empty.
        references = {variable: "" for variable, _name in REFERENCES.values()}
        for label, key in task_labels.items():
            references[REFERENCES[label][0]] = json.dumps(
                dict(zip(("repository", "path"), key.split("/", 1), strict=True)),
                separators=(",", ":"),
            )
        return {
            "type": "stdio",
            "command": sys.executable,
            # The working directory is kept off the module search path.
            "args": [
                "-P",
                "-m",
                "agents_remember.mcp",
                "--config",
                self.config.config_path.as_posix(),
            ],
            "env": {
                "PYTHONPATH": self.source.parent.as_posix(),
                # The fixture's Git/bytecode settings and its provided XDG homes survive the
                # role tool-server hop, the place of the user's settings among them; absent XDG
                # variables are not invented here.
                "GIT_OPTIONAL_LOCKS": "0",
                "PYTHONPYCACHEPREFIX": (self.root / "pycache").as_posix(),
                **{
                    name: os.environ[name]
                    for name in (
                        "XDG_CONFIG_HOME",
                        "XDG_DATA_HOME",
                        "XDG_STATE_HOME",
                        "XDG_CACHE_HOME",
                    )
                    if os.environ.get(name)
                },
                # No seat identity reaches the tool server from the harness that starts it.
                "AR_SPAWN_ROLE": "",
                "AR_HOSTED_SESSION_ID": "",
                "AR_PASEO_AGENT_ID": agent_id,
                "AR_ROLE": request.role,
                "AR_REQUEST_ID": str(request.request_id),
                "AR_REPORT_PATH": (self.root / "reports" / f"{request.request_id}.md").as_posix(),
                **references,
            },
        }

    def recovery_note(self, request: RoleDispatchRequest, task_labels: dict[str, str]) -> str:
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
        self, request: RoleDispatchRequest, definition: dict[str, Any], note: str
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


def prepare_host(root: Path, *, configured: bool) -> None:
    """One shared host authority for the installation a launch fixture represents."""
    settings = runtime_settings(root, listen="127.0.0.1:6835", providers={}, embed=[])
    write_shared_runtime(root, settings if configured else None)
