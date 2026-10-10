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
    # `run_drift_summary` returns `{"status": "error", "error": ...}` when the onboarding root
    # is missing. Both halves were absent here, so `include_drift=true` against a repo without
    # onboarding raised out of the tool instead of reporting why -- the strict model rejected
    # the status *and* the key. `DriftCheckResponse` has carried both all along.
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
