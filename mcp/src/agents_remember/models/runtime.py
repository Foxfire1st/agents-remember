"""Models for runtime installation tools."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from agents_remember.models.base import FlexibleToolResponse


class RuntimeInstallResponse(FlexibleToolResponse):
    operation: Literal["runtime_install"] = "runtime_install"
    dryRun: bool | None = None
    coordinationRoot: str | None = None
    includeBenchmarks: bool | None = None
    installProviderDeps: bool | None = None
    summary: dict[str, Any] | None = None
    host: dict[str, Any] | None = None
    messages: list[str] = Field(default_factory=list)
    reportPath: str | None = Field(
        default=None,
        description=(
            "Temp report file with the full install detail (provider watcher "
            "rebind runs, compose renders, command transcripts); the inline "
            "payload keeps counts and a compact rebind digest."
        ),
    )
