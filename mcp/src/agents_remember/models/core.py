"""Models for core MCP server tools."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from agents_remember.models.base import ResponseModel

Transport = Literal["stdio"]


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
