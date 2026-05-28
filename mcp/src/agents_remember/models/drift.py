"""Models for onboarding drift summaries exposed in tool responses."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from agents_remember.models.base import StrictResponseModel

DriftStatus = Literal["notChecked", "checked"]


class DriftSummary(StrictResponseModel):
    status: DriftStatus
    count: int | None = Field(default=None, ge=0)
    actionableCount: int | None = Field(default=None, ge=0)
    reportPath: str | None = None
    actionableSample: list[dict[str, Any]] | None = None
