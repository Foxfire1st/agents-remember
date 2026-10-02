"""The Paseo side of one role launch: the agent's identity, title and labels, and the launch call.

Agents Remember chooses the agent id before it calls the runtime and stores the whole call in the
execution receipt. Running the stored call again converges on the same agent: the runtime returns
the agent that already carries the id. The call is three bridge commands in a fixed order: archive
the agent of the execution this launch replaces (when there is one), obtain the workspace of the
role's folder, and create the agent in it. The third command also sends the first message, once
the agent exists and its tool servers have had time to start, and a repeat sends it to an agent
that never received it; a first message the runtime did not take leaves the launch unresolved.
An agent whose closed session the runtime cannot open again before it ever got that message is
lost: the launch is closed as refused and names the agent, which the next launch archives.

The call also carries what the agent is given beside its first message: one tool-server
definition, the tool server of this build under a fixed name with the agent's binding in its
environment, and a short recovery note for the agent's system-level instructions. Both are the
same for every provider; the definition is left out only for a provider the runtime reports as
not accepting tool servers.

A role that another role agent starts carries that agent as its parent: the call names it to the
runtime, and the receipt records it.
"""

from __future__ import annotations

import os
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import agents_remember
from agents_remember.application.agent_binding import TOOL_SERVER_NAME, AgentBinding
from agents_remember.application.orca_task_context import OrcaRoleContext
from agents_remember.cli.paseo_bridge import (
    AGENT_WITHOUT_MESSAGE_LOST,
    BRIDGE_INVALID_REPLY,
    BRIDGE_REFUSED,
    PaseoBridgeFailure,
    bridge_call,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.worktrees.modules.quality.dagger_authority import HOST_REGISTRY_ROOT_ENV

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
# A shortened part of a task reference keeps its leading id, at most this many characters of it.
_LONGEST_LEADING_ID = 12
# Variables that say where a process of this build may write: no index lock and no bytecode in
# the checkout it runs from, its own terminal multiplexer, its own registry of quality tools. A
# harness need not pass its environment on, so the definition carries each of them when the
# launching process has it, and never a value of its own.
_CARRIED_VARIABLES = (
    "GIT_OPTIONAL_LOCKS",
    "PYTHONPYCACHEPREFIX",
    "TMUX_TMPDIR",
    HOST_REGISTRY_ROOT_ENV,
)
TOOL_SERVER_APPLIED = "tool server applied"
TOOL_SERVER_NOT_SUPPORTED = "tool server not applied: not supported by provider"


@dataclass(frozen=True, slots=True)
class LaunchOutcome:
    """What one run of a stored launch call established.

    ``created``: the agent exists in the runtime; ``execution`` and ``applied`` say where and with
    what. ``refused``: the runtime answered and no agent exists under the minted id, or the one
    that exists never got its first message and cannot be opened again (``lost_agent_id``).
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
    # The agent of a refused launch that exists in the runtime and is still to be archived.
    lost_agent_id: str | None = None


@dataclass(frozen=True, slots=True)
class StartingAgent:
    """The role agent that starts another one: its agent id, its role, and the work it serves.

    ``subject`` is the id of the most specific task document of its binding, or ``Projects``.
    """

    agent_id: str
    role: str
    subject: str


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
    # The agent that started this role, which the runtime records as the new agent's parent.
    parent_agent_id: str | None = None


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
    this process was started with. The interpreter is told not to put the working directory on
    its module search path: the server starts in the agent's folder, and nothing in that folder
    may stand in for the build or for a module the build imports. The source path variable is
    always set: to this build's source tree when the package is a checkout outside the
    interpreter's own installation, and to nothing when the interpreter's own installation holds
    it, so that no inherited value puts another source tree in front.
    """

    interpreter = sys.executable
    if not interpreter:
        raise ValueError("This process cannot name its Python interpreter for the tool server.")
    package = launching_source_root()
    installed = package.is_relative_to(Path(sys.prefix).resolve())
    kept = {name: os.environ[name] for name in _CARRIED_VARIABLES if os.environ.get(name)}
    return {
        "type": "stdio",
        "command": interpreter,
        "args": ["-P", "-m", _TOOL_SERVER_MODULE, "--config", settings_file.as_posix()],
        "env": {
            "PYTHONPATH": "" if installed else package.parent.as_posix(),
            **kept,
            **binding.environment(),
        },
    }


def launching_source_root() -> Path:
    """The package directory of this build, as its ``server_info`` reports it."""

    return Path(agents_remember.__file__).resolve().parent


def recovery_note(context: OrcaRoleContext, artifact: dict[str, Any]) -> str:
    """Role, task references, and where the complete first message is stored; no task content.

    The note never exceeds ``RECOVERY_NOTE_LIMIT`` characters. The artifact's path and SHA-256
    are always complete; task references that would not fit are shortened (``_fitted``).
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
    text = note(_fitted(keys, RECOVERY_NOTE_LIMIT - len(note(["" for _key in keys]))))
    if len(text) > RECOVERY_NOTE_LIMIT:
        raise ValueError(
            f"The recovery note of this launch cannot name its handover artifact within "
            f"{RECOVERY_NOTE_LIMIT} characters; the artifact path is too long."
        )
    return text


def _fitted(references: list[str], room: int) -> list[str]:
    """The references as the note names them in ``room`` characters: whole, or shortened.

    The others are shortened before the most specific reference, the last one: it stays whole
    while the others can still be named in their shortest form, and is cut only by what is then
    still missing. A less specific reference may end at its task folder, whose document it
    names: its document name is given up before any folder's slug is cut, and comes back only
    when every folder is whole and the name fits whole.
    """

    lengths = [len(reference) for reference in references]
    if not references or sum(lengths) <= room:
        return references
    others, last = references[:-1], references[-1]
    floors = [len(_shortened(reference, 0, ends_at_folder=True)) for reference in others]
    folders = [
        len(_shortened(reference, len(reference) - 1, ends_at_folder=True)) for reference in others
    ]
    shortest = len(_shortened(last, 0, ends_at_folder=False))
    specific = min(len(last), max(room - sum(floors), shortest))
    widths = _shares(room - specific, floors, folders)
    spare = room - specific - sum(widths)
    for index in reversed(range(len(others))):
        missing = lengths[index] - widths[index]
        if widths[index] == folders[index] and 0 < missing <= spare:
            widths[index], spare = lengths[index], spare - missing
    return [
        *(
            _shortened(reference, width, ends_at_folder=True)
            for reference, width in zip(others, widths, strict=True)
        ),
        _shortened(last, specific, ends_at_folder=False),
    ]


def _shares(room: int, floors: list[int], caps: list[int]) -> list[int]:
    """Divide ``room`` among parts that each take at least their floor and at most their cap.

    The parts that need least are served first, so what they leave over goes to the others.
    """

    widths = list(floors)
    spare = room - sum(floors)
    wanting = sorted(
        (index for index in range(len(caps)) if caps[index] > floors[index]),
        key=lambda index: caps[index] - floors[index],
    )
    for served, index in enumerate(wanting):
        grant = min(caps[index] - floors[index], max(spare, 0) // (len(wanting) - served))
        widths[index] += grant
        spare -= grant
    return widths


def _shortened(reference: str, width: int, *, ends_at_folder: bool) -> str:
    """``reference`` in at most ``width`` characters, or in its shortest form if that is longer.

    The repository name stays whole. A part with a slug, a task folder or a numbered document,
    keeps at least its leading id and ends in an ellipsis where its slug was cut; the slugs get
    the room first. A part without a slug is never cut inside: it is whole, or at its shortest,
    or, as the document of a reference that may end at its folder, left out; that document is
    named only when every other part is whole.
    """

    if len(reference) <= width:
        return reference
    repository, *parts = reference.split("/")
    shortest = [
        ""
        if ends_at_folder and index == len(parts) - 1 and index > 0 and "_" not in part
        else part
        if len(_leading_id(part)) + 1 >= len(part)
        else f"{_leading_id(part)}…"
        for index, part in enumerate(parts)
    ]
    slugs = [index for index, part in enumerate(parts) if "_" in part]
    plain = sum(len(shortest[index]) + 1 for index in range(len(parts)) if shortest[index])
    slug_floor = sum(len(shortest[index]) for index in slugs)
    granted = _shares(
        width - len(repository) - plain + slug_floor,
        [len(shortest[index]) for index in slugs],
        [len(parts[index]) for index in slugs],
    )
    texts = list(shortest)
    for index, size in zip(slugs, granted, strict=True):
        texts[index] = parts[index] if size >= len(parts[index]) else f"{parts[index][: size - 1]}…"
    spare = width - len(repository) - sum(len(text) + 1 for text in texts if text)
    # A part that was left out, the document of a reference that ends at its folder, comes back
    # last, and only to a reference whose other parts are all whole.
    for index in sorted(range(len(parts)), key=lambda index: not shortest[index]):
        part = parts[index]
        others_whole = all(
            texts[other] == parts[other] for other in range(len(parts)) if other != index
        )
        missing = len(part) - len(texts[index]) + (0 if texts[index] else 1)
        if index not in slugs and 0 < missing <= spare and (shortest[index] or others_whole):
            texts[index], spare = part, spare - missing
    return "/".join([repository, *(text for text in texts if text)])


def _leading_id(part: str) -> str:
    """What identifies a folder or document among its siblings: the text before its slug."""

    head = part.split("_", 1)[0] if "_" in part else part.split(".", 1)[0]
    return head[:_LONGEST_LEADING_ID]


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
    if launch.parent_agent_id:
        agent["parentAgentId"] = launch.parent_agent_id
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
        lost = error.code == AGENT_WITHOUT_MESSAGE_LOST
        return LaunchOutcome(
            kind="refused" if lost or error.code == BRIDGE_REFUSED else "no-answer",
            code=error.code,
            message=str(error)[:_TEXT_LIMIT],
            predecessor_settled=predecessor_settled,
            lost_agent_id=agent_id if lost else None,
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
