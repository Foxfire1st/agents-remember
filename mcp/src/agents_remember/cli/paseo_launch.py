"""The Paseo side of one role launch: the agent's identity, title and labels, and the launch call.

Agents Remember chooses the agent id before it calls the runtime and stores the whole call in the
execution receipt. Running the stored call again converges on the same agent: the runtime returns
the agent that already carries the id. The call is three bridge commands in a fixed order: archive
the agent of the execution this launch replaces (when there is one), obtain the workspace of the
role's folder, and create the agent in it.

The call also carries what the agent is given beside its first message: one tool-server
definition, the tool server of this build under a fixed name with the agent's binding in its
environment, and a short recovery note for the agent's system-level instructions. Both are the
same for every provider; the definition is left out only for a provider the runtime reports as
not accepting tool servers.
"""

from __future__ import annotations

import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import agents_remember
from agents_remember.application.agent_binding import TOOL_SERVER_NAME, AgentBinding
from agents_remember.application.orca_task_context import OrcaRoleContext
from agents_remember.cli.paseo_bridge import (
    BRIDGE_INVALID_REPLY,
    BRIDGE_REFUSED,
    PaseoBridgeFailure,
    bridge_call,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig

PASEO_AGENT_KIND = "paseo-agent"
# The labels every role agent carries; a task reference is `<repository>/<document path>`.
ROLE_LABEL = "ar.role"
REQUEST_LABEL = "ar.request-id"
SPRINT_LABEL = "ar.sprint-ref"
MASTER_LABEL = "ar.master-ref"
TASK_LABEL = "ar.task-ref"
# Paseo's limit for a title the caller sets.
_TITLE_LIMIT = 200
_TEXT_LIMIT = 800
# The module that starts this build's tool server.
_TOOL_SERVER_MODULE = "agents_remember.mcp"
RECOVERY_NOTE_LIMIT = 600
# Below this length a shortened task reference no longer identifies its document.
_SHORTEST_REFERENCE = 16
TOOL_SERVER_APPLIED = "tool server applied"
TOOL_SERVER_NOT_SUPPORTED = "tool server not applied: not supported by provider"


@dataclass(frozen=True, slots=True)
class LaunchOutcome:
    """What one run of a stored launch call established.

    ``created``: the agent exists in the runtime; ``execution`` and ``applied`` say where and with
    what. ``refused``: the runtime answered and no agent exists under the minted id.
    ``no-answer``: nothing usable came back, so the agent may or may not exist.
    """

    kind: Literal["created", "refused", "no-answer"]
    code: str | None = None
    message: str | None = None
    execution: dict[str, str] | None = None
    applied: dict[str, str] | None = None
    warning: str | None = None
    # False while the agent this launch replaces has not been archived in the runtime.
    predecessor_settled: bool = True


def mint_agent_id() -> str:
    """The id of the agent a launch creates; the runtime accepts a caller-chosen UUID."""

    return str(uuid.uuid4())


def agent_title(context: OrcaRoleContext) -> str:
    """``<Role> · <id of the most specific task document>``; ``<Role> · Projects`` when taskless."""

    role = context.role.replace("-", " ").capitalize()
    subject = context.effective_task.document.id if context.effective_task else "Projects"
    return f"{role} · {subject}"[:_TITLE_LIMIT]


def agent_labels(context: OrcaRoleContext, request_id: uuid.UUID) -> dict[str, str]:
    """The role, each task reference of the selection, and the request id."""

    references = (
        (SPRINT_LABEL, context.sprint),
        (MASTER_LABEL, context.master),
        (TASK_LABEL, context.task),
    )
    return {
        ROLE_LABEL: context.role,
        REQUEST_LABEL: str(request_id),
        **{label: document.ref.key for label, document in references if document is not None},
    }


@dataclass(frozen=True, slots=True)
class RoleLaunch:
    """One launch as the dispatch route prepared it."""

    agent_id: str
    request_id: uuid.UUID
    context: OrcaRoleContext
    folder: str
    provider: str
    session_options: dict[str, str]
    # The first message exactly as the agent receives it: the artifact line, then the content.
    prompt: str
    # The file the agent is told to write its report to.
    report_path: str
    # The reference of the handover artifact that holds the compiled first message.
    handover_artifact: dict[str, Any]
    # The settings file this build was started with; its tool server is started with the same.
    settings_file: Path
    # What the runtime reports about the provider.
    accepts_tool_servers: bool = True
    # The agent of the closed execution this launch replaces on its selection.
    replaces_agent_id: str | None = None


def agent_binding(launch: RoleLaunch) -> AgentBinding:
    """The agent id, role, task references, request id and report path of one launch."""

    context = launch.context
    return AgentBinding(
        agent_id=launch.agent_id,
        role=context.role,
        request_id=str(launch.request_id),
        report_path=launch.report_path,
        sprint_ref=context.sprint.ref if context.sprint else None,
        master_ref=context.master.ref if context.master else None,
        task_ref=context.task.ref if context.task else None,
    )


def tool_server_definition(settings_file: Path, binding: AgentBinding) -> dict[str, Any]:
    """The tool server of this build, started with this build's source and settings.

    It is the interpreter that runs this process, the tool server's module, and the settings file
    this process was started with. When this build's package is a source tree outside the
    interpreter's own installation, the definition names that tree, so that the server loads the
    same source as the process that launched the agent.
    """

    interpreter = sys.executable
    if not interpreter:
        raise ValueError("This process cannot name its Python interpreter for the tool server.")
    package = launching_source_root()
    source: dict[str, str] = {}
    if not package.is_relative_to(Path(sys.prefix).resolve()):
        source["PYTHONPATH"] = package.parent.as_posix()
    return {
        "type": "stdio",
        "command": interpreter,
        "args": ["-m", _TOOL_SERVER_MODULE, "--config", settings_file.as_posix()],
        "env": {**source, **binding.environment()},
    }


def launching_source_root() -> Path:
    """The package directory of this build, as its ``server_info`` reports it."""

    return Path(agents_remember.__file__).resolve().parent


def recovery_note(context: OrcaRoleContext, artifact: dict[str, Any]) -> str:
    """Role, task references, and where the complete first message is stored; no task content.

    The note never exceeds ``RECOVERY_NOTE_LIMIT`` characters. The artifact's path and SHA-256
    are always complete; task references that would not fit are shortened in the middle.
    """

    references = [
        (label, document.ref.key)
        for label, document in (
            ("Sprint", context.sprint),
            ("Master", context.master),
            ("Task", context.task),
        )
        if document is not None
    ]

    def note(keys: list[str]) -> str:
        named = [f"{label} {key}." for (label, _key), key in zip(references, keys, strict=True)]
        return " ".join(
            [
                f"AR role agent: {context.role}.",
                *(named or ["No task reference."]),
                f"Assignment file: {artifact['path']} (SHA-256 {artifact['sha256']}).",
                "Reload that file whenever your assignment is not in your context.",
            ]
        )

    keys = [key for _label, key in references]
    text = note(keys)
    if len(text) > RECOVERY_NOTE_LIMIT and keys:
        room = (RECOVERY_NOTE_LIMIT - len(note(["" for _key in keys]))) // len(keys)
        if room >= _SHORTEST_REFERENCE:
            text = note([_shortened(key, room) for key in keys])
    if len(text) > RECOVERY_NOTE_LIMIT:
        raise ValueError(
            f"The recovery note of this launch cannot name its handover artifact within "
            f"{RECOVERY_NOTE_LIMIT} characters; the artifact path is too long."
        )
    return text


def _shortened(reference: str, room: int) -> str:
    if len(reference) <= room:
        return reference
    head = (room - 1) // 3
    return f"{reference[:head]}…{reference[len(reference) - (room - 1 - head) :]}"


def build_launch_call(launch: RoleLaunch) -> dict[str, Any]:
    """Everything needed to make, and to repeat, the runtime calls of one launch.

    Model and effort go to the runtime as its model and thinking option; no permission mode is
    named, so the provider's default applies. A launch without a model leaves the choice to the
    provider. The first message, the recovery note and the tool-server definition are plain data
    in the call, so a repeat sends exactly what the first run sent.
    """

    agent: dict[str, Any] = {
        "agentId": launch.agent_id,
        "idempotencyKey": f"ar-role-launch:{launch.request_id}",
        "title": agent_title(launch.context),
        "labels": agent_labels(launch.context, launch.request_id),
        "provider": launch.provider,
        "prompt": launch.prompt,
        "systemPrompt": recovery_note(launch.context, launch.handover_artifact),
    }
    if launch.accepts_tool_servers:
        agent["mcpServers"] = {
            TOOL_SERVER_NAME: tool_server_definition(launch.settings_file, agent_binding(launch))
        }
    if launch.session_options.get("model"):
        agent["model"] = launch.session_options["model"]
    if launch.session_options.get("effort"):
        agent["thinkingOptionId"] = launch.session_options["effort"]
    return {
        **({"archiveAgentId": launch.replaces_agent_id} if launch.replaces_agent_id else {}),
        "workspace": {"cwd": launch.folder},
        "agent": agent,
    }


def applied_to_agent(call: dict[str, Any]) -> dict[str, Any]:
    """What a launch call gives the agent beside its first message, as the receipt records it."""

    agent = call["agent"]
    definition = (agent.get("mcpServers") or {}).get(TOOL_SERVER_NAME)
    if definition is None:
        tool_server = {
            "name": TOOL_SERVER_NAME,
            "applied": False,
            "detail": TOOL_SERVER_NOT_SUPPORTED,
        }
    else:
        tool_server = {
            "name": TOOL_SERVER_NAME,
            "applied": True,
            "detail": TOOL_SERVER_APPLIED,
            "command": [definition["command"], *definition["args"]],
            "environment": dict(definition["env"]),
            "sourceRoot": launching_source_root().as_posix(),
        }
    return {"toolServer": tool_server, "recoveryNote": agent["systemPrompt"]}


def run_launch_call(config: McpRuntimeConfig, call: dict[str, Any]) -> LaunchOutcome:
    """Run a stored launch call and classify what the runtime answered."""

    workspace = call.get("workspace")
    agent = call.get("agent")
    folder = workspace.get("cwd") if isinstance(workspace, dict) else None
    agent_id = agent.get("agentId") if isinstance(agent, dict) else None
    if not isinstance(agent, dict) or not isinstance(folder, str) or not isinstance(agent_id, str):
        raise ValueError("The saved launch call is incomplete and cannot be repeated.")
    predecessor = call.get("archiveAgentId")
    predecessor_settled = not predecessor
    try:
        if predecessor:
            bridge_call(config, "agent-archive", {"agentId": predecessor})
            predecessor_settled = True
        opened = bridge_call(config, "workspace-open", {"cwd": folder})
        workspace_id = _opened_workspace_id(opened, folder)
        created = bridge_call(config, "agent-create", {**agent, "workspaceId": workspace_id})
        return _created_outcome(created, agent_id, workspace_id)
    except PaseoBridgeFailure as error:
        return LaunchOutcome(
            kind="refused" if error.code == BRIDGE_REFUSED else "no-answer",
            code=error.code,
            message=str(error)[:_TEXT_LIMIT],
            predecessor_settled=predecessor_settled,
        )


def _opened_workspace_id(reply: dict[str, Any], folder: str) -> str:
    workspace = reply.get("workspace")
    workspace_id = workspace.get("id") if isinstance(workspace, dict) else None
    directory = workspace.get("directory") if isinstance(workspace, dict) else None
    if not _is_text(workspace_id) or not _is_text(directory):
        raise PaseoBridgeFailure(
            BRIDGE_INVALID_REPLY, "The Paseo bridge returned an unreadable workspace."
        )
    assert isinstance(workspace_id, str) and isinstance(directory, str)
    if Path(directory).resolve() != Path(folder).resolve():
        # An agent in this workspace would run in another folder than the role is entitled to.
        raise PaseoBridgeFailure(
            BRIDGE_REFUSED,
            f"The Paseo runtime answered with the workspace of {directory} for {folder}.",
        )
    return workspace_id


def _created_outcome(reply: dict[str, Any], agent_id: str, workspace_id: str) -> LaunchOutcome:
    agent = reply.get("agent")
    server_id = reply.get("serverId")
    provider = agent.get("provider") if isinstance(agent, dict) else None
    if (
        not isinstance(agent, dict)
        or agent.get("id") != agent_id
        or not isinstance(server_id, str)
        or not server_id
        or not isinstance(provider, str)
        or not provider
    ):
        raise PaseoBridgeFailure(
            BRIDGE_INVALID_REPLY, "The Paseo bridge returned an unreadable agent."
        )
    agent_workspace = agent.get("workspaceId")
    applied: dict[str, str] = {"id": provider}
    for key, source in (("model", "model"), ("effort", "thinkingOptionId")):
        value = agent.get(source)
        if isinstance(value, str) and value:
            applied[key] = value
    creation_error = reply.get("creationError")
    return LaunchOutcome(
        kind="created",
        execution={
            "kind": PASEO_AGENT_KIND,
            "serverId": server_id,
            "workspaceId": (
                agent_workspace
                if isinstance(agent_workspace, str) and agent_workspace
                else workspace_id
            ),
            "agentId": agent_id,
        },
        applied=applied,
        warning=(
            f"The Paseo runtime reported an error although the agent exists: {creation_error}"[
                :_TEXT_LIMIT
            ]
            if _is_text(creation_error)
            else None
        ),
    )


def _is_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value)
