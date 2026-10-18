"""Models for core MCP server tools."""

from __future__ import annotations

from typing import Any, Literal

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


class OpenUserSettingsPayload(BaseModel):
    """The user's settings file or its folder found open to writing, with the user's commands."""

    model_config = ConfigDict(extra="forbid")

    userPath: str
    fileExists: bool
    fileOpen: bool
    folderOpen: bool
    closeCommand: str
    editCommand: str


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
    # Provider ids still present in settings, named once as no longer used: no tool
    # reports on the CGC or GrepAI providers anymore and nothing starts them.
    unusedProviderKeys: list[str] = Field(default_factory=list)
    # Settings keys the files hold that only removed parts read, as dotted names: named
    # here once as no longer used, and not read anywhere.
    unusedSettingsKeys: list[str] = Field(default_factory=list)
    # Present only while the user's settings file or its folder is open to writing: what is
    # open, the command that closes it and the command the user runs before an edit.
    openUserSettings: OpenUserSettingsPayload | None = None
    servingBuild: ServingBuildPayload
    # MIK-R83 rule 7: present only in a process loaded from a linked checkout
    # without a declared execution mode -- the disposable root it is bound to,
    # the worktree it was loaded from, and the configured root it did not use.
    checkoutBinding: dict[str, Any] | None = None
    # Both present only on a tool server that a role launch started for one agent: the name
    # under which the launch gave that agent this server, and the agent's binding.
    toolServer: str | None = None
    agentBinding: AgentBindingPayload | None = None
