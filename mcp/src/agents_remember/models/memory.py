"""Models for memory, onboarding, baseline, and carryover tools."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from agents_remember.models.base import FlexibleToolResponse, ToolResponse
from agents_remember.models.drift import DriftStatus
from agents_remember.models.lifecycles.memory_candidate import MemoryCandidatePairIdentity


class DriftCheckResponse(ToolResponse):
    operation: Literal["drift_check"] = "drift_check"
    # The last hand-copy of the drift vocabulary. `run_drift_summary` produces every member and
    # `models.drift.DriftSummary` already reads `DriftStatus` from its declaration; this model
    # kept a third, identical copy, which is one more place for the next member to not arrive.
    status: DriftStatus
    count: int | None = Field(default=None, ge=0)
    actionableCount: int | None = Field(default=None, ge=0)
    reportPath: str | None = None
    actionableSample: list[dict[str, Any]] | None = None
    error: str | None = None
    # WHICH tree was measured. `repo_id` alone means the official memory repo and a
    # `contract_path` means a leaf's memory worktree, and nothing else in this response
    # distinguishes them -- a caller that got the wrong one could not tell.
    onboardingRoot: str | None = None


class MemoryQualityCheckResponse(FlexibleToolResponse):
    operation: Literal["memory_quality_check"] = "memory_quality_check"
    repoId: str | None = None
    onboardingRoot: str | None = None
    # Async run envelope: start admits background work and poll reads it by runId;
    # the completed envelope carries the full result.
    status: (
        Literal[
            "started",
            "running",
            "completed",
            "failed",
            "run-not-found",
            "capacity-reached",
            "scope-refused",
        ]
        | None
    ) = None
    runId: str | None = None
    checks: dict[str, Any] | list[dict[str, Any]] | None = None
    reportPath: str | None = Field(
        default=None,
        description=(
            "For a full contract-scoped check, the single enclosure-local curator checklist "
            "that this invocation atomically replaced."
        ),
    )
    attestationPath: str | None = Field(
        default=None,
        description="Structured readiness attestation paired to the rendered curator checklist.",
    )
    guidance: str | None = Field(default=None, max_length=8192)
    checklistStatus: (
        Literal["action-required", "coherence-required", "ready-for-closeout"] | None
    ) = None
    qualityChecklistStatus: Literal["action-required", "ready-for-closeout"] | None = None
    coherenceStatus: str | None = Field(default=None, max_length=256)
    coherenceCanonicalPath: str | None = Field(default=None, max_length=8192)
    coherenceRecordDigest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    closeoutReady: bool | None = None
    curatorActionableCount: int | None = Field(default=None, ge=0)
    memoryRepairCount: int | None = Field(default=None, ge=0)
    missingOnboardingCount: int | None = Field(default=None, ge=0)
    staleRouteIndexCount: int | None = Field(default=None, ge=0)
    sourceChangeCandidateCount: int | None = Field(default=None, ge=0)
    closeoutOwnedFindingCount: int | None = Field(default=None, ge=0)
    noteworthyFindingCount: int | None = Field(default=None, ge=0)
    scopeAuthority: Literal["official-diagnostic", "leaf-candidate"] | None = None
    acceptanceEligible: bool | None = None
    contractPath: str | None = Field(default=None, max_length=8192)
    pairIdentity: MemoryCandidatePairIdentity | None = None
    pairStatus: str | None = Field(default=None, max_length=256)
    pairField: str | None = Field(default=None, max_length=256)
    detail: str | None = Field(default=None, max_length=8192)
    expected: dict[str, Any] | None = Field(default=None, max_length=32)
    observed: dict[str, Any] | None = Field(default=None, max_length=32)
    nextAction: str | None = Field(default=None, max_length=8192)
    nextArgs: dict[str, Any] | None = Field(default=None, max_length=32)


class _MemoryQualityExecutionRequest(BaseModel):
    """Fields shared by synchronous and asynchronous quality execution."""

    model_config = ConfigDict(extra="forbid")

    repo_id: str
    checks: list[str] | None = None
    detail_limit: int = 50
    contract_path: str | None = None


class MemoryQualitySyncRequest(_MemoryQualityExecutionRequest):
    """Run one memory-quality request synchronously."""

    mode: Literal["sync"]


class MemoryQualityStartRequest(_MemoryQualityExecutionRequest):
    """Start one bounded asynchronous memory-quality request."""

    mode: Literal["start"]


class MemoryQualityPollRequest(BaseModel):
    """Poll one repository-owned run through the same admitted scope."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["poll"]
    repo_id: str
    run_id: str
    contract_path: str | None = None


type MemoryQualityCheckRequest = Annotated[
    MemoryQualitySyncRequest | MemoryQualityStartRequest | MemoryQualityPollRequest,
    Field(discriminator="mode"),
]


class CitationFixResponse(FlexibleToolResponse):
    operation: Literal["citation_fix"] = "citation_fix"
    repoId: str | None = None
    dryRun: bool | None = None


class RouteIndexRefreshResponse(FlexibleToolResponse):
    operation: Literal["route_index_refresh"] = "route_index_refresh"
    repoId: str | None = None
    # The tree the indexes were WRITTEN into, declared because this tool mutates: a
    # caller has to be able to see whether it just wrote its leaf or the official repo.
    onboardingRoot: str | None = None
    dryRun: bool | None = None
    staleIndexes: list[str] | None = Field(
        default=None,
        description="Route-index paths whose rendered bytes differ from the onboarding census.",
    )


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
    decisions: dict[str, list[str]] | None = Field(
        default=None,
        description=(
            "Source paths grouped by carryover decision (auto-carry, "
            "review-required, reject); the per-record detail lives in reportPath."
        ),
    )
    reportPath: str | None = Field(
        default=None,
        description=(
            "Temp report file holding the full candidate records (derived "
            "onboarding paths, evidence, per-path reasons)."
        ),
    )


class MemoryCarryoverApplyResponse(FlexibleToolResponse):
    operation: Literal["memory_carryover_apply"] = "memory_carryover_apply"
    state: str | None = None
    decisions: dict[str, list[str]] | None = Field(
        default=None,
        description=(
            "Source paths grouped by carryover decision (auto-carry, "
            "review-required, reject); the per-record detail lives in reportPath."
        ),
    )
    carriedPaths: list[str] | None = Field(
        default=None,
        description="Source paths whose onboarding was actually carried over.",
    )
    reportPath: str | None = Field(
        default=None,
        description=("Temp report file holding the full candidate and carried records."),
    )
