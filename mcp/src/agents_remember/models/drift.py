"""Models for onboarding drift summaries exposed in tool responses."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from agents_remember.models.base import StrictResponseModel

DriftStatus = Literal["notChecked", "checked", "error"]


class DriftSummary(StrictResponseModel):
    status: DriftStatus
    count: int | None = Field(default=None, ge=0)
    actionableCount: int | None = Field(default=None, ge=0)
    reportPath: str | None = None
    actionableSample: list[dict[str, Any]] | None = None
    # `run_drift_summary` reports errors for a non-directory onboarding root, legacy memory,
    # or a failed converted-tree measurement. An absent onboarding root is valid and yields
    # a checked summary with zero cards.
    error: str | None = None
    # Converted-tree measurement (MIK-R44): what was really read. Absent on legacy results;
    # legacy fields are never invented for a converted card, and vice versa.
    basis: str | None = None
    memoryRoot: str | None = None
    codeRoot: str | None = None
    sidecarCount: int | None = Field(default=None, ge=0)
    states: dict[str, int] | None = None
    violationCount: int | None = Field(default=None, ge=0)
    violationSample: list[dict[str, Any]] | None = None
    reportOnlyCount: int | None = Field(default=None, ge=0)
    unreadableSidecars: list[str] | None = None
