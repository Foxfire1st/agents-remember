"""Models for runtime installation and coordination context tools."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from agents_remember.models.base import FlexibleToolResponse, ToolResponse


class RuntimeInstallResponse(FlexibleToolResponse):
    operation: Literal["runtime_install"] = "runtime_install"
    dryRun: bool | None = None
    coordinationRoot: str | None = None
    includeBenchmarks: bool | None = None
    installProviderDeps: bool | None = None
    summary: dict[str, Any] | None = None
    messages: list[str] = Field(default_factory=list)


class ResolveContextResponse(ToolResponse):
    operation: Literal["resolve_context"] = "resolve_context"
    repoId: str
    context: dict[str, Any]
