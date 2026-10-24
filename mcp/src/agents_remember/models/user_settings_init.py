"""Response model for the user-settings initializer (MIK-R196 rule 11)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from agents_remember.models.base import ToolResponse


class UserSettingsInitResponse(ToolResponse):
    """One preview or write of the one user settings file.

    ``state`` names what happened: ``preview`` for an unwritten plan, ``created`` and
    ``completed`` for the two writes this surface makes, ``already-configured`` and
    ``already-initialized`` for a destination it read and left alone, and ``refused`` for a call
    that wrote nothing (``code``, ``detail`` and ``nextAction`` then carry the refusal).
    ``paseoRuntime`` and ``roleHarnesses`` are the values the write carries; ``readBack`` is the
    state read from the destination after the write.
    """

    operation: Literal["user_settings_init"] = "user_settings_init"
    state: str
    path: str
    target: Literal["host-authority", "role-settings"] = "role-settings"
    changed: bool = False
    code: str | None = None
    detail: str | None = Field(default=None, max_length=8192)
    nextAction: str | None = None
    paseoRuntime: dict[str, Any] | None = None
    roleHarnesses: dict[str, str] = Field(default_factory=dict)
    preservedKeys: list[str] = Field(default_factory=list)
    writtenKeys: list[str] = Field(default_factory=list)
    readBack: dict[str, Any] | None = None
    dryRun: bool = False
    settingsSources: list[str] = Field(default_factory=list)
