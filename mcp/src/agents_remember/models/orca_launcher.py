"""Wire models for the role launcher."""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from agents_remember.models.task_document_ref import TaskDocumentRef

OrcaRole = Literal[
    "architect",
    "system-specialist",
    "orchestrator",
    "manager",
    "worker",
    "reviewer",
    "curator",
]


class OrcaAgentOverride(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    agent_id: str = Field(alias="agentId", min_length=1, max_length=80)
    model_id: str | None = Field(default=None, alias="modelId", max_length=200)
    effort_id: str | None = Field(default=None, alias="effortId", max_length=80)


class OrcaSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    role: OrcaRole
    sprint_document_ref: TaskDocumentRef | None = Field(default=None, alias="sprintDocumentRef")
    master_document_ref: TaskDocumentRef | None = Field(default=None, alias="masterDocumentRef")
    task_document_ref: TaskDocumentRef | None = Field(default=None, alias="taskDocumentRef")


class OrcaLauncherOptionsRequest(OrcaSelection):
    agent_id: str | None = Field(default=None, alias="agentId", max_length=80)
    refresh_catalog: bool = Field(default=False, alias="refreshCatalog")


class OrcaDispatchRequest(OrcaSelection):
    request_id: uuid.UUID = Field(alias="requestId")
    action: Literal["start", "revive"] = "start"
    agent_override: OrcaAgentOverride | None = Field(default=None, alias="agentOverride")


class OrcaResultRequest(OrcaSelection):
    request_id: uuid.UUID | None = Field(default=None, alias="requestId")
