"""The Paseo side of one role launch: the agent's identity, title and labels, and the launch call.

Agents Remember chooses the agent id before it calls the runtime and stores the whole call in the
execution receipt. Running the stored call again converges on the same agent: the runtime returns
the agent that already carries the id. The call is three bridge commands in a fixed order: archive
the agent of the execution this launch replaces (when there is one), obtain the workspace of the
role's folder, and create the agent in it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

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
    prompt: str
    # The agent of the closed execution this launch replaces on its selection.
    replaces_agent_id: str | None = None


def build_launch_call(launch: RoleLaunch) -> dict[str, Any]:
    """Everything needed to make, and to repeat, the runtime calls of one launch.

    Model and effort go to the runtime as its model and thinking option; no permission mode is
    named, so the provider's default applies. A launch without a model leaves the choice to the
    provider. The first message is plain data in the call.
    """

    agent: dict[str, Any] = {
        "agentId": launch.agent_id,
        "idempotencyKey": f"ar-role-launch:{launch.request_id}",
        "title": agent_title(launch.context),
        "labels": agent_labels(launch.context, launch.request_id),
        "provider": launch.provider,
        "prompt": launch.prompt,
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
