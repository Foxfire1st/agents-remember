"""Identity, binding and runtime refusals shared by role starts and messages."""

from __future__ import annotations

from collections.abc import Mapping

from agents_remember.application.agent_binding import AgentBinding, read_agent_binding
from agents_remember.cli.paseo_bridge import PaseoBridgeFailure, require_bridge_runtime
from agents_remember.cli.paseo_launch import StartingAgent
from agents_remember.cli.role_launch_preparation import ROLE_MESSAGE_TOOL, ROLE_START_TOOL
from agents_remember.cli.role_message_delivery import _Refused
from agents_remember.kernel.agentic_settings import AgenticSettingsError, load_agentic_settings
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.tasks.document_refs import TaskDocumentRefError, TaskDocumentTopology

_PROJECTS = "Projects"


def _caller(config: McpRuntimeConfig, environment: Mapping[str, str] | None) -> AgentBinding:
    """The binding of the calling agent and a configured runtime, or the refusal that names it."""

    try:
        binding = read_agent_binding(environment)
    except ValueError as error:
        raise _no_binding(f" {error}") from error
    if binding is None:
        raise _no_binding("")
    try:
        require_bridge_runtime(config)
    except PaseoBridgeFailure as error:
        raise _Refused(
            "no-paseo-runtime-configured",
            str(error),
            "Tell the developer in your own chat that the AR settings name no Paseo runtime.",
        ) from error
    return binding


def _no_binding(reason: str) -> _Refused:
    return _Refused(
        "caller-has-no-binding",
        "This tool server was not started by an AR role launch, so the caller has no binding "
        f"(agent id, role, task references).{reason}",
        f"A chat without a role cannot use {ROLE_MESSAGE_TOOL}: the developer speaks with the "
        f"agent in the dashboard. Such a chat starts a role with {ROLE_START_TOOL}.",
    )


def _binding_absent(environment: Mapping[str, str] | None) -> bool:
    """The caller carries no binding at all, rather than a malformed one.

    Only a genuinely absent binding is a chat without a role (MIK-R113 rule 6);
    a binding that names an agent but does not fit its role's class stays refused.
    """

    try:
        return read_agent_binding(environment) is None
    except ValueError:
        return False


def starting_agent(config: McpRuntimeConfig, binding: AgentBinding) -> StartingAgent:
    """The caller as the runtime and a recipient see it: agent id, role, and the work it serves.

    The work is named by the id of the most specific task document of the binding, or
    ``Projects`` for a taskless role.
    """

    reference = binding.task_ref or binding.master_ref or binding.sprint_ref
    if reference is None:
        return StartingAgent(
            binding.agent_id,
            binding.role,
            _PROJECTS,
            binding.sprint_ref,
            binding.master_ref,
            binding.task_ref,
        )
    try:
        subject = TaskDocumentTopology(config.coordination_root).resolve(reference).document.id
    except TaskDocumentRefError:
        # The document is gone or unreadable; its reference still says which work is meant.
        subject = reference.key
    return StartingAgent(
        binding.agent_id,
        binding.role,
        subject,
        binding.sprint_ref,
        binding.master_ref,
        binding.task_ref,
    )


def sender_line(sender: StartingAgent) -> str:
    """The line that opens every delivered message."""

    return f"From {sender.role} · {sender.subject} · agent {sender.agent_id}"


def role_message_limit(config: McpRuntimeConfig, binding: AgentBinding) -> int:
    """The bound on the characters of one message text, read per call (MIK-R117).

    The number in force is the one the settings file names at the call; where
    the settings name none it is :data:`DEFAULT_ROLE_MESSAGE_LIMIT`. A changed
    number holds for the next call without a restart of a tool server.
    """

    reference = binding.task_ref or binding.master_ref or binding.sprint_ref
    repo_root = None
    if reference is not None:
        scope = config.repositories.get(reference.repository)
        if scope is not None:
            repo_root = scope.path
    try:
        settings = load_agentic_settings(config.coordination_root, repo_root=repo_root)
    except AgenticSettingsError as error:
        raise _Refused(
            "settings-invalid",
            f"The settings cannot be loaded: {error}",
            "Fix the named field in the named settings file; the next call reads it again.",
        ) from error
    return settings.role_message_limit


def _host_unreachable(detail: str) -> _Refused:
    return _Refused(
        "host-unreachable",
        f"The Paseo runtime cannot be reached. {detail}",
        "Tell the developer in your own chat that the Paseo runtime cannot be reached; repeat "
        "the call after runtime_install (if not installed) and agents-remember dashboard --daemon.",
    )
