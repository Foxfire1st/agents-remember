"""Models for core MCP server tools."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from agents_remember.models.base import ResponseModel
from agents_remember.models.task_document_ref import TaskDocumentRef

Transport = Literal["stdio"]


class ServingBuildPayload(BaseModel):
    """Boot-bound runtime identity shared by dashboard and MCP server surfaces."""

    model_config = ConfigDict(extra="forbid")

    version: str
    bootedAt: str
    sourceDigest: str | None = None
    pythonExecutable: str | None = None
    packageRoot: str | None = None
    commit: str | None = None
    dashboardBuild: str | None = None
    # Only ever True or absent -- see ``ServingBuild.payload``.
    dirty: bool | None = None


class AgentBindingPayload(BaseModel):
    """The agent a tool server was started for by a role launch, and the AR work it is bound to."""

    model_config = ConfigDict(extra="forbid")

    agentId: str
    role: str
    requestId: str
    reportPath: str
    sprintDocumentRef: TaskDocumentRef | None = None
    masterDocumentRef: TaskDocumentRef | None = None
    taskDocumentRef: TaskDocumentRef | None = None


class PingResponse(ResponseModel):
    server: str
    version: str
    transport: Transport


class ServerInfoResponse(ResponseModel):
    server: str
    version: str
    transport: Transport
    configPath: str
    coordinationRoot: str
    workspaceRoot: str
    transcriptRoot: str
    harnessSkillRoot: str | None = None
    allowedRepoIds: list[str] = Field(default_factory=list)
    allowedProviderIds: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    reservedTools: list[str] = Field(default_factory=list)
    servingBuild: ServingBuildPayload
    # Present only on a tool server that a role launch started for one agent.
    agentBinding: AgentBindingPayload | None = None
