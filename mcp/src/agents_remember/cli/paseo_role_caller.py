"""Identity, binding and runtime refusals shared by role starts and messages."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from importlib import import_module
from pathlib import Path

from agents_remember.application.agent_binding import AgentBinding, read_agent_binding
from agents_remember.canvas.explainer_binding import (
    EXPLAINER_BINDING_ENV,
    validate_explainer_binding,
)
from agents_remember.canvas.explainer_state import AGENT_MARKER
from agents_remember.cli.paseo_bridge import PaseoBridgeFailure, require_bridge_runtime
from agents_remember.cli.paseo_launch import StartingAgent
from agents_remember.cli.role_launch_preparation import ROLE_MESSAGE_TOOL, ROLE_START_TOOL
from agents_remember.cli.role_message_delivery import _Refused
from agents_remember.kernel.agentic_settings import AgenticSettingsError, load_agentic_settings
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.tasks import document_refs
from agents_remember.tasks.document_refs import TaskDocumentRefError, TaskDocumentTopology

_PROJECTS = "Projects"
#: The sender word of a leaf explainer: it is no AR role (it holds no receipt and starts nothing),
#: and a message names it by this word, its leaf and its seat (MIK-R211 v5 rule 3, D607).
EXPLAINER_SENDER = "explainer"


def _caller(config: McpRuntimeConfig, environment: Mapping[str, str] | None) -> AgentBinding:
    """The binding of the calling agent and a configured runtime, or the refusal that names it."""

    try:
        binding = read_agent_binding(environment)
    except ValueError as error:
        raise _no_binding(f" {error}") from error
    if binding is None:
        raise _no_binding("")
    _require_runtime(config)
    return binding


def _message_caller(
    config: McpRuntimeConfig, environment: Mapping[str, str] | None
) -> AgentBinding:
    """The sender of one role message: a role agent's binding, or a leaf explainer's (D607).

    A leaf explainer reports to its master's architect with ``role_message``, as every agent does
    (MIK-R211 v5 rule 3). It carries no AR role binding, but its launch binding says who it is: its
    seat, the master and the leaf. That is its sender identity and nothing more -- it has no role
    receipt, no report collection and no start authority. A process with an AR role binding, a
    malformed one, or neither binding is answered exactly as :func:`_caller` answers it.
    """

    source = os.environ if environment is None else environment
    if source.get(EXPLAINER_BINDING_ENV) is None or not _binding_absent(environment):
        return _caller(config, environment)
    binding = _explainer_sender(config, source)
    _require_runtime(config)
    return binding


def _explainer_sender(config: McpRuntimeConfig, source: Mapping[str, str]) -> AgentBinding:
    """The leaf explainer's launch binding as a sender, or the no-binding refusal naming why."""

    try:
        given = json.loads(source.get(EXPLAINER_BINDING_ENV) or "")
        launch = validate_explainer_binding(given if isinstance(given, dict) else {}, config=config)
    except ValueError as error:  # malformed JSON and every ExplainerRefused of the validator
        raise _no_binding(f" The explainer launch binding does not hold: {error}") from error
    process = (source.get(AGENT_MARKER) or "").strip()
    if process and process != launch.agent:
        raise _no_binding(
            f" The explainer launch binding names seat {launch.agent} and this process is "
            f"{process}; no marker widens a binding."
        )
    # The module-qualified topology: the leaf and its parents are read from the task documents.
    topology = document_refs.TaskDocumentTopology(config.coordination_root)
    try:
        leaf = topology.ref_for_id(launch.repository, launch.task, launch.leaf)
        master = topology.parent(leaf)
        sprint = topology.parent(master) if master is not None else None
    except TaskDocumentRefError as error:
        raise _no_binding(
            f" The explainer's leaf {launch.leaf} in {launch.task} has no readable task "
            f"document: {error}"
        ) from error
    return AgentBinding(
        agent_id=launch.agent,
        role=EXPLAINER_SENDER,
        request_id=launch.request,
        report_path=launch.workspace.as_posix(),
        sprint_ref=sprint,
        master_ref=master,
        task_ref=leaf,
    )


def _require_runtime(config: McpRuntimeConfig) -> None:
    try:
        require_bridge_runtime(config)
    except PaseoBridgeFailure as error:
        raise _Refused(
            "no-paseo-runtime-configured",
            str(error),
            "Tell the developer in your own chat that the AR settings name no Paseo runtime.",
        ) from error


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
        _explainer_identity(config, binding) if binding.role == EXPLAINER_SENDER else "",
    )


def _explainer_identity(config: McpRuntimeConfig, binding: AgentBinding) -> str:
    """The binding and the revision a leaf explainer speaks from, for its sender line (D607).

    A message from an explainer arrives as that leaf's seat, at its environment's binding (the label
    and digest of ``binding.json``) and at the leaf's latest published revision, so the architect
    knows which page the message is about. An environment with no readable binding sends nothing.
    The canvas modules are read on the call, so a role message's discovery loads none of them.
    """

    access = import_module("agents_remember.canvas.explainer_access")
    workspace = Path(binding.report_path)
    try:
        held = access.require_binding(workspace)
    except ValueError as error:  # ExplainerRefused: no binding.json, or an unreadable one
        raise _no_binding(
            f" The explainer's environment has no readable binding: {error}"
        ) from error
    frame = held.binding
    label = "planned" if held.planned is not None else f"#{frame.comparison_number}"
    digest = import_module("agents_remember.canvas.explainer_state").binding_digest(held)
    paths = import_module("agents_remember.canvas.explainer_paths")
    store = import_module("agents_remember.application.review_frame_store")
    # The workspace is <coordination>/explainer/<repository>/<task>/<leaf>; the page's revisions are
    # published into the task itself, <coordination>/tasks/<repository>/<task>, as the seat's render
    # publishes them (register_explainer_surface).
    task_root = (
        Path(config.coordination_root)
        / "tasks"
        / workspace.parent.parent.name
        / workspace.parent.name
    )
    opening = store.reopen_frame(store.frame_scope(task_root, frame.leaf_id, paths.EXPLAINER_FRAME))
    revision = (
        "no published revision" if opening.revision is None else f"revision {opening.revision}"
    )
    return f"binding {label} ({digest}) · {revision}"


def sender_line(sender: StartingAgent) -> str:
    """The line that opens every delivered message; an explainer's also names its binding and revision."""

    identity = f" · {sender.identity}" if sender.identity else ""
    return f"From {sender.role} · {sender.subject}{identity} · agent {sender.agent_id}"


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
