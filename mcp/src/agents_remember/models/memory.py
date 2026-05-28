"""Models for memory, onboarding, baseline, and carryover tools."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from agents_remember.models.base import FlexibleToolResponse, ToolResponse

DriftCheckStatus = Literal["notChecked", "checked", "error"]


class DriftCheckResponse(ToolResponse):
    operation: Literal["drift_check"] = "drift_check"
    status: DriftCheckStatus
    count: int | None = Field(default=None, ge=0)
    actionableCount: int | None = Field(default=None, ge=0)
    reportPath: str | None = None
    actionableSample: list[dict[str, Any]] | None = None
    error: str | None = None


class MemoryQualityCheckResponse(FlexibleToolResponse):
    operation: Literal["memory_quality_check"] = "memory_quality_check"
    repoId: str | None = None
    onboardingRoot: str | None = None
    checks: dict[str, Any] | list[dict[str, Any]] | None = None


class RouteIndexRefreshResponse(FlexibleToolResponse):
    operation: Literal["route_index_refresh"] = "route_index_refresh"
    repoId: str | None = None
    dryRun: bool | None = None


class MemoryInitResponse(FlexibleToolResponse):
    operation: Literal["memory_init"] = "memory_init"
    repoId: str | None = None
    dryRun: bool | None = None


class MemoryBaselineStatusResponse(FlexibleToolResponse):
    operation: Literal["memory_baseline_status"] = "memory_baseline_status"
    state: str | None = None


class MemoryBaselineAdoptResponse(FlexibleToolResponse):
    operation: Literal["memory_baseline_adopt"] = "memory_baseline_adopt"
    state: str | None = None
    dryRun: bool | None = None


class MemoryCarryoverPlanResponse(FlexibleToolResponse):
    operation: Literal["memory_carryover_plan"] = "memory_carryover_plan"
    state: str | None = None


class MemoryCarryoverApplyResponse(FlexibleToolResponse):
    operation: Literal["memory_carryover_apply"] = "memory_carryover_apply"
    state: str | None = None
