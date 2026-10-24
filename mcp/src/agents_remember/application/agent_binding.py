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

from agents_remember.models.role_identity import (
    LEAF_ROLES,
    ROLE_LEVELS,
    TASKLESS_ROLES,
    canonical_role,
)
from agents_remember.models.task_document_ref import TaskDocumentRef

# The name under which every launched agent is given the tool server of the launching build.
TOOL_SERVER_NAME = "agents-remember-task"

# The tool server of the folder a role agent works in: the product's own other server. Rule 6
# (MIK-R112) keeps it out of a role agent at the launch, with what the harness or the host
# offers for one launch. No configuration file of the user or of the folder is changed, and no
# server of the user is kept out.
OTHER_PRODUCT_TOOL_SERVER = "agents-remember"


# What the role start hands the host for one launch so that the agent's harness does not load
# the tools of the product's other server (MIK-R132). The pinned host (0.11.1) offers this for
# Claude alone: it reads ``providerOptions.disallowedTools`` of the create request
# (``providers/claude/options.js``, ``providers/claude/agent.js``) and hands it to the harness
# as ``--disallowedTools``. The rule names the server and carries no star: the harness compares
# the server name of a rule ``mcp__<server>`` for equality, while a rule with a star is matched
# as a prefix of the whole tool name and would take the task server's tools with it.
CLAUDE_OTHER_SERVER_RULE = f"mcp__{OTHER_PRODUCT_TOOL_SERVER}"
EXCLUSION_MODE_CLAUDE = "claude-disallowed-tools"
# The mode an earlier build recorded. The host was handed nothing under it; the launch call of
# such a record had the other server's entry taken out of the map of servers it supplied, and
# a repeat of a saved call of that build still has (``cli/paseo_launch.py``).
EXCLUSION_MODE_SUPPLIED_MAP = "task-server-only"


def launch_server_exclusion(
    provider: str | None, *, servers: tuple[str, ...] = (TOOL_SERVER_NAME,)
) -> dict[str, Any] | None:
    """What this launch hands the host to keep the other server's tools out, if anything.

    The record is what the launch carries, and the role start's answer and the first message
    derive from it; the provider's name alone never decides. None means that nothing is handed
    over, so the agent's harness loads a second copy where its own configuration names the
    server. That holds for Codex (the host narrows tools only for a server that the same
    request names, and restating the user's entry would shadow it), for a provider that the
    host runs over ACP such as Hermes (a session is given its folder and the request's servers
    and nothing else), for Pi, and for every other provider the settings name. A launch that
    carries no task server keeps the other one: without it the agent had none of the
    product's tools.
    """
    if provider == "claude" and servers:
        return {
            "harness": provider,
            "mode": EXCLUSION_MODE_CLAUDE,
            "disallowedTools": [CLAUDE_OTHER_SERVER_RULE],
            "servers": list(servers),
        }
    return None


def exclusion_keeps_copy_out(record: object) -> bool:
    """Whether a stored exclusion record is one that the host read and the harness obeyed.

    A record of an earlier build named a mode under which the host was handed nothing; it
    kept no copy out and does not count.
    """
    return isinstance(record, dict) and record.get("mode") == EXCLUSION_MODE_CLAUDE


def exclusion_agent_options(
    exclusion: dict[str, Any] | None, *, host_denied: tuple[str, ...] = ()
) -> dict[str, Any]:
    """The keys of the host's agent definition that carry an exclusion record to the host.

    The host lays the provider options of the request over the ones its own configuration
    names for the provider, and a list of the request takes the place of the configured list
    (``provider-registry.js``, ``provider-options.js``: ``mergeProviderOptions``). The list
    handed over therefore repeats ``host_denied``, the rules the user's configuration of the
    host denies in the provider's options, and adds the product's rule to them: what the user
    denied stays denied for this agent. Nothing is handed over where there is no record, so
    the configured list then applies as it is.
    """
    if exclusion is None:
        return {}
    rules = dict.fromkeys((*host_denied, *exclusion["disallowedTools"]))
    return {"providerOptions": {"disallowedTools": list(rules)}}


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
# The seat identity of the earlier way of hosting. No part of this build reads it; an agent the
# host starts gets both emptied, so that a value inherited from the starting harness never
# reaches a reader of an earlier installation.
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
