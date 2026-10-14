"""The seat retirement through the host: its request and its answer (MIK-R100 rule 8)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import field_validator

from agents_remember.models.base import ToolResponse
from agents_remember.models.role_identity import canonical_role
from agents_remember.models.task_document_ref import TaskDocumentRef

StructuralRole = Literal[
    "architect",
    "orchestrator",
    "strategist",
    "designer",
    "investigator",
    "system-specialist",
    "manager",
    "worker",
    "reviewer",
    "curator",
]
"""Roles that occupy canonical task-document seats."""


@dataclass(frozen=True)
class RetireChildRequest:
    task_document_ref: TaskDocumentRef
    role: StructuralRole
    reason: str
    # The receipt-tracked agent to retire (MIK-R100 rule 8): the starter retires the
    # agent in its seat without giving it a turn. An empty id is refused by name.
    agent_id: str | None = None
    # The retiring call may interrupt the agent's turn; without it an agent in a turn
    # is not retired.
    interrupt_turn: bool = False


class RetireChildResponse(ToolResponse):
    """An answer that identifies work and role but never a runtime occupant."""

    operation: Literal["retire_child"] = "retire_child"
    status: str
    taskDocumentRef: TaskDocumentRef | None = None
    role: str
    detail: str | None = None

    @field_validator("role")
    @classmethod
    def _canonical_role(cls, value: str) -> str:
        return canonical_role(value)
