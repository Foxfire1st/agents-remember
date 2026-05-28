"""Models for skill installation tools."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from agents_remember.models.base import FlexibleToolResponse


class SkillsInstallResponse(FlexibleToolResponse):
    operation: Literal["skills_install"] = "skills_install"
    dryRun: bool | None = None
    layout: str | None = None
    installRoot: str | None = None
    planned: list[str] = Field(default_factory=list)
    installed: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    archived: list[str] = Field(default_factory=list)
