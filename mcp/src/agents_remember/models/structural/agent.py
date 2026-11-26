"""Requests and answers for seat retirement through the host."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import ConfigDict, field_validator

from agents_remember.models.base import StrictResponseModel, ToolResponse
from agents_remember.models.role_identity import canonical_role
from agents_remember.models.role_launcher import LauncherRole, RoleSelection
from agents_remember.models.task_document_ref import TaskDocumentRef


@dataclass(frozen=True)
class RetireChildRequest:
    agent_id: str
    role: LauncherRole
    reason: str
    interrupt_turn: bool = False
    sprint_document_ref: TaskDocumentRef | None = None
    master_document_ref: TaskDocumentRef | None = None
    task_document_ref: TaskDocumentRef | None = None


class RetirementIntent(StrictResponseModel):
    caller: str
    callerAgentId: str
    reason: str
    at: str


class RecordedSeatSelection(RoleSelection):
    model_config = ConfigDict(serialize_by_alias=True)


class OpenChildSeat(StrictResponseModel):
    agentId: str
    selection: RecordedSeatSelection


class RetireChildResponse(ToolResponse):
    """The outcome, recorded seat selection, and facts relevant to a retry."""

    operation: Literal["retire_child"] = "retire_child"
    status: str
    sprintDocumentRef: TaskDocumentRef | None = None
    masterDocumentRef: TaskDocumentRef | None = None
    taskDocumentRef: TaskDocumentRef | None = None
    role: str
    detail: str | None = None
    retireIntent: RetirementIntent | None = None
    openChildren: list[OpenChildSeat] | None = None

    @field_validator("role")
    @classmethod
    def _canonical_role(cls, value: str) -> str:
        return canonical_role(value)
