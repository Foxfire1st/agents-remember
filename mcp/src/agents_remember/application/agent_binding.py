"""The binding of a launched role agent, as its tool server receives it.

A launch gives the runtime one tool-server definition for the agent, under the fixed name below,
and puts the agent's binding into that server's environment. The launch code writes these
variables and the tool server reads them; both import the names from this module, which is the one
place they are spelled.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from agents_remember.models.task_document_ref import TaskDocumentRef

# The name under which every launched agent is given the tool server of the launching build.
TOOL_SERVER_NAME = "agents-remember-task"

AGENT_ID_VARIABLE = "AR_PASEO_AGENT_ID"
ROLE_VARIABLE = "AR_ROLE"
REQUEST_ID_VARIABLE = "AR_REQUEST_ID"
REPORT_PATH_VARIABLE = "AR_REPORT_PATH"
# Each holds one task-document reference as JSON and is set only when the selection has it.
SPRINT_REF_VARIABLE = "AR_SPRINT_REF"
MASTER_REF_VARIABLE = "AR_MASTER_REF"
TASK_REF_VARIABLE = "AR_TASK_REF"


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
        """The binding as the environment of the agent's tool server."""

        variables = {
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
            if reference is not None:
                variables[name] = json.dumps(
                    reference.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")
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

    A process that carries an agent id but not the rest of a binding was started by something
    other than a launch of this build, and that is refused instead of half-read.
    """

    source = os.environ if environment is None else environment
    if not source.get(AGENT_ID_VARIABLE):
        return None
    return AgentBinding(
        agent_id=_text(source, AGENT_ID_VARIABLE),
        role=_text(source, ROLE_VARIABLE),
        request_id=_text(source, REQUEST_ID_VARIABLE),
        report_path=_text(source, REPORT_PATH_VARIABLE),
        sprint_ref=_reference(source, SPRINT_REF_VARIABLE),
        master_ref=_reference(source, MASTER_REF_VARIABLE),
        task_ref=_reference(source, TASK_REF_VARIABLE),
    )


def _text(source: Mapping[str, str], name: str) -> str:
    value = source.get(name)
    if not value:
        raise ValueError(f"The agent binding of this tool server has no {name}.")
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
