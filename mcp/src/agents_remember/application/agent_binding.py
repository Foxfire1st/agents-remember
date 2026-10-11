"""The binding of a launched role agent, as its tool server receives it.

A launch gives the runtime one tool-server definition for the agent, under the fixed name below,
and puts the agent's binding into that server's environment. The launch code writes these
variables and the tool server reads them; both import the names from this module, which is the one
place they are spelled.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from agents_remember.application.role_launch_context import LEAF_ROLES, ROLE_LEVELS, TASKLESS_ROLES
from agents_remember.models.role_identity import canonical_role
from agents_remember.models.task_document_ref import TaskDocumentRef

# The name under which every launched agent is given the tool server of the launching build.
TOOL_SERVER_NAME = "agents-remember-task"

# The tool server of the folder a role agent works in: the product's own other server. Rule 6
# (MIK-R112) keeps it out of a role agent at the launch, with what the harness or the host
# offers for one launch. No configuration file of the user or of the folder is changed, and no
# server of the user is kept out.
OTHER_PRODUCT_TOOL_SERVER = "agents-remember"

# Harness ids the registry knows (kernel/harnesses.py): "claude", "codex", "pi", "eve".
# A settings file may name more; their launch surface is unknown. The known set is
# spelled out in launch_server_exclusion (mode per harness), not here.

# The decoy command for the codex override below: never spawned (the server is disabled in
# the same override set), exits cleanly if it ever were, POSIX like the rest of this leaf.
_CODEX_DISABLED_DECOY_COMMAND = "/bin/true"


def harness_tool_exclusion_argv(harness_id: str | None) -> tuple[str, ...]:
    """One-launch argv that keeps the product's other server out of a role agent.

    ``pi`` filters tools (``--exclude-tools`` names both spellings of the other server's
    tool prefix) and ``claude`` denies them (``--disallowedTools``, verified in
    ``claude --help`` 2.1.294; both spellings). The task server's ``...-task__`` /
    ``..._task__`` tools match neither pattern. ``codex`` disables the one server with
    the unquoted dotted key (codex splits ``-c`` keys at the dots; a quoted segment
    never merges). The second ``-c`` supplies a never-spawned command so the override
    is a complete table: with the server configured it merges to disabled, without it
    the decoy starts cleanly instead of failing bootstrap (both probed model-free).
    ``eve`` is AR-owned: the host serves the task tools and no folder server loads, so
    no argv is needed. Unknown harnesses offer nothing verified: empty, and the brief
    keeps the server-choice sentence for them.
    """
    if harness_id == "pi":
        return ("--exclude-tools", "mcp__agents-remember__*,mcp__agents_remember__*")
    if harness_id == "claude":
        return ("--disallowedTools", "mcp__agents-remember__*,mcp__agents_remember__*")
    if harness_id == "codex":
        return (
            "-c",
            "mcp_servers.agents-remember.enabled=false",
            "-c",
            f'mcp_servers.agents-remember.command="{_CODEX_DISABLED_DECOY_COMMAND}"',
        )
    return ()


def harness_carries_one_server(harness_id: str | None) -> bool:
    """Whether a role agent on this harness carries the product's tools once.

    True for ``pi``, ``claude`` and ``codex`` (argv exclusion, merged with
    caller filters) and ``eve`` (host-owned runtime). False for
    settings-defined harnesses with an unknown surface and an unresolved id:
    for those the brief keeps the server-choice sentence and the role-start
    answer says a second copy is loaded.
    """
    return launch_server_exclusion(harness_id) is not None


def launch_server_exclusion(
    harness_id: str | None, *, servers: tuple[str, ...] = (TOOL_SERVER_NAME,)
) -> dict[str, Any] | None:
    """The applied one-launch server exclusion for a native role start, if any.

    The record is what the launch carries, not the harness name: ``mode`` is
    ``host-owned`` for ``eve`` (no folder server loads in the host runtime),
    ``task-server-only`` for ``pi``/``claude``/``codex`` (the host payload
    lists only ``servers``), and there is no record for unknown harnesses or
    launches that list no servers. Brief wording and the role-start answer
    derive from this record; the harness name alone never decides.

    Where the exclusion is actually applied as mechanism is the terminal
    seat-spawn argv (application/terminal_tools.py merges the product patterns
    into the native harness command per harness). The bridge agent-create call
    forwards only workspace/agent and carries no exclusion consumer; whether a
    bridge-launched agent sees a second copy is not measurable leaf-side. That
    question is returned for owning authority disposition -- it is not answered
    by this record (the exact host consumer is named in the L112-FX-F07
    disposition, not here, so this prose stays out of the paseo single path).
    """
    if harness_id == "eve":
        return {"harness": harness_id, "mode": "host-owned", "servers": list(servers)}
    if harness_id in ("pi", "claude", "codex") and servers:
        return {"harness": harness_id, "mode": "task-server-only", "servers": list(servers)}
    return None


# The product-server tool patterns merged into caller tool filters: both
# spellings of the other server's tool prefix. The task server's
# ``...-task__`` / ``..._task__`` tools match neither pattern.
_PRODUCT_TOOL_PATTERNS = ("mcp__agents-remember__*", "mcp__agents_remember__*")


def _merge_tool_filter(
    args: list[str], flags: tuple[str, ...], patterns: tuple[str, ...]
) -> list[str] | None:
    """Caller args with the patterns merged into its filter value, if it has one.

    Finds the first known flag: an ``--flag=value`` form merges into the suffix,
    a bare flag merges into the following argument unless that starts another
    flag. Returns None when the caller passes no such flag (the caller adds a
    fresh one) — never drops or reorders caller arguments.
    """
    merged = list(args)
    for index, arg in enumerate(merged):
        for flag in flags:
            if arg == flag:
                if index + 1 < len(merged) and not merged[index + 1].startswith("-"):
                    merged[index + 1] = _merge_pattern_list(merged[index + 1], patterns)
                    return merged
                return None
            if arg.startswith(flag + "="):
                merged[index] = flag + "=" + _merge_pattern_list(arg[len(flag) + 1 :], patterns)
                return merged
    return None


def _merge_pattern_list(value: str, patterns: tuple[str, ...]) -> str:
    present = value.split(",") if value else []
    for pattern in patterns:
        if pattern not in present:
            present.append(pattern)
    return ",".join(present)


def with_server_exclusion(
    harness_id: str | None, launch_args: list[str] | None
) -> list[str] | None:
    """Append the one-launch exclusion argv for a role-agent spawn, if any.

    Never drops caller-supplied arguments: when the caller already passes the same
    tool filter (``--exclude-tools``/``-xt``, ``--disallowedTools``/``--disallowed-tools``),
    the product patterns merge into its value instead of riding a second flag, so an
    unrelated caller filter (``ask_question``, ``Bash``) cannot bypass the exclusion
    while the launch still reports single-server. ``-c`` overrides merge by key, so
    the codex pair always rides along.
    """
    extra = harness_tool_exclusion_argv(harness_id)
    if not extra:
        return launch_args
    existing = list(launch_args) if launch_args else []
    if harness_id == "pi":
        merged = _merge_tool_filter(existing, ("--exclude-tools", "-xt"), _PRODUCT_TOOL_PATTERNS)
        if merged is not None:
            return merged
    if harness_id == "claude":
        merged = _merge_tool_filter(
            existing, ("--disallowedTools", "--disallowed-tools"), _PRODUCT_TOOL_PATTERNS
        )
        if merged is not None:
            return merged
    return [*existing, *extra]


AGENT_ID_VARIABLE = "AR_PASEO_AGENT_ID"
ROLE_VARIABLE = "AR_ROLE"
REQUEST_ID_VARIABLE = "AR_REQUEST_ID"
REPORT_PATH_VARIABLE = "AR_REPORT_PATH"
# Each holds one task-document reference as JSON, or nothing when the selection has none. All
# three are always set, so that a value the tool server would inherit from the harness that starts
# it never takes the place of one the launch did not give.
SPRINT_REF_VARIABLE = "AR_SPRINT_REF"
MASTER_REF_VARIABLE = "AR_MASTER_REF"
TASK_REF_VARIABLE = "AR_TASK_REF"
# The seat identity other parts of the tool server read. A launched agent's tool server has none;
# the variables are emptied for the same reason.
CLEARED_SEAT_VARIABLES = ("AR_SPAWN_ROLE", "AR_HOSTED_SESSION_ID")


@dataclass(frozen=True, slots=True)
class AgentBinding:
    """The facts that tie one agent to AR work."""

    agent_id: str
    role: str
    request_id: str
    report_path: str
    sprint_ref: TaskDocumentRef | None = None
    master_ref: TaskDocumentRef | None = None
    task_ref: TaskDocumentRef | None = None

    def environment(self) -> dict[str, str]:
        """The binding as the environment of the agent's tool server.

        Every variable is present: a reference the selection does not have is empty, which the
        reader takes as absent.
        """

        variables: dict[str, str] = {
            **dict.fromkeys(CLEARED_SEAT_VARIABLES, ""),
            AGENT_ID_VARIABLE: self.agent_id,
            ROLE_VARIABLE: self.role,
            REQUEST_ID_VARIABLE: self.request_id,
            REPORT_PATH_VARIABLE: self.report_path,
        }
        for name, reference in (
            (SPRINT_REF_VARIABLE, self.sprint_ref),
            (MASTER_REF_VARIABLE, self.master_ref),
            (TASK_REF_VARIABLE, self.task_ref),
        ):
            variables[name] = (
                json.dumps(
                    reference.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")
                )
                if reference is not None
                else ""
            )
        return variables

    def as_report(self) -> dict[str, Any]:
        """The binding as a tool reports it."""

        report: dict[str, Any] = {
            "agentId": self.agent_id,
            "role": self.role,
            "requestId": self.request_id,
            "reportPath": self.report_path,
        }
        for key, reference in (
            ("sprintDocumentRef", self.sprint_ref),
            ("masterDocumentRef", self.master_ref),
            ("taskDocumentRef", self.task_ref),
        ):
            if reference is not None:
                report[key] = reference.model_dump(mode="json")
        return report


def read_agent_binding(environment: Mapping[str, str] | None = None) -> AgentBinding | None:
    """The binding this process was started with; ``None`` when it was not started for an agent.

    A process that carries an agent id but not a binding as a launch of this build makes it was
    started by something else, and that is refused instead of half-read: every text must be
    present, both ids must have the shape in which they are minted, the role must be an AR role,
    the report path absolute, and the references exactly those of the role's class.
    """

    source = os.environ if environment is None else environment
    if not source.get(AGENT_ID_VARIABLE):
        return None
    binding = AgentBinding(
        agent_id=_minted_id(source, AGENT_ID_VARIABLE),
        role=canonical_role(_text(source, ROLE_VARIABLE)),
        request_id=_minted_id(source, REQUEST_ID_VARIABLE),
        report_path=_text(source, REPORT_PATH_VARIABLE),
        sprint_ref=_reference(source, SPRINT_REF_VARIABLE),
        master_ref=_reference(source, MASTER_REF_VARIABLE),
        task_ref=_reference(source, TASK_REF_VARIABLE),
    )
    if binding.role not in ROLE_LEVELS:
        raise ValueError(
            f"The agent binding of this tool server names no AR role in {ROLE_VARIABLE}."
        )
    if not os.path.isabs(binding.report_path):
        raise ValueError(
            f"The agent binding of this tool server has no absolute path in {REPORT_PATH_VARIABLE}."
        )
    given = (
        binding.sprint_ref is not None,
        binding.master_ref is not None,
        binding.task_ref is not None,
    )
    valid = (
        given in {(False, False, False), (True, False, False), (True, True, False)}
        if binding.role == "investigator"
        else given == _references_of(binding.role)
    )
    if not valid:
        raise ValueError(
            f"The agent binding of this tool server does not carry the task references of "
            f"a {binding.role}: {SPRINT_REF_VARIABLE}, {MASTER_REF_VARIABLE}, {TASK_REF_VARIABLE}."
        )
    return binding


def _references_of(role: str) -> tuple[bool, bool, bool]:
    """Which of sprint, master and task reference a role's class carries."""

    role = canonical_role(role)
    return (role not in TASKLESS_ROLES, role == "manager" or role in LEAF_ROLES, role in LEAF_ROLES)


def _text(source: Mapping[str, str], name: str) -> str:
    value = source.get(name)
    if not value:
        raise ValueError(f"The agent binding of this tool server has no {name}.")
    return value


def _minted_id(source: Mapping[str, str], name: str) -> str:
    value = _text(source, name)
    try:
        minted = str(uuid.UUID(value)) == value
    except ValueError:
        minted = False
    if not minted:
        raise ValueError(f"The agent binding of this tool server has no minted id in {name}.")
    return value


def _reference(source: Mapping[str, str], name: str) -> TaskDocumentRef | None:
    raw = source.get(name)
    if not raw:
        return None
    try:
        return TaskDocumentRef.model_validate(json.loads(raw))
    except ValueError as error:
        raise ValueError(
            f"The agent binding of this tool server has no readable {name}."
        ) from error
