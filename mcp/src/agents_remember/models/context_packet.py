"""Versioned context packet response models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from agents_remember.models.base import StrictResponseModel, ToolResponse
from agents_remember.models.drift import DriftSummary
from agents_remember.models.providers import ProviderSummary
from agents_remember.models.worktree import WorktreeSummary


class RepoSummary(StrictResponseModel):
    id: str
    root: str
    branch: str
    head: str
    dirty: bool
    state: Literal["available", "detached", "unavailable"]
    error: str | None = None


class ContextPaths(StrictResponseModel):
    coordinationRoot: str
    taskRoot: str
    tempRoot: str
    memoryRoot: str
    onboardingRoot: str
    ledgerPath: str | None = None
    settingsPath: str
    pathSettingsPath: str | None = None
    systemRoot: str
    sourcesPath: str
    toolsPath: str
    docsRoot: str


class PathMatchRules(StrictResponseModel):
    paths: list[str] = Field(default_factory=list)
    fileTypes: list[str] = Field(default_factory=list)


class StoragePathRule(StrictResponseModel):
    path: str = ""
    storage: str = ""
    include: PathMatchRules = Field(default_factory=PathMatchRules)
    exclude: PathMatchRules = Field(default_factory=PathMatchRules)


class StorageSummary(StrictResponseModel):
    mode: str
    default: str
    pathRules: list[StoragePathRule] = Field(default_factory=list)


class CrossRepoAllowEntry(StrictResponseModel):
    repo: str
    expectedBranch: str | None = None
    includeCode: bool = False
    includeMemory: bool = False
    state: str | None = None
    reason: str | None = None
    code: dict[str, Any] | None = None
    memory: dict[str, Any] | None = None


class CrossRepoSummary(StrictResponseModel):
    allow: list[CrossRepoAllowEntry] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class MemorySummary(StrictResponseModel):
    mode: Literal["internal", "external"]
    storage: StorageSummary
    crossRepo: CrossRepoSummary = Field(default_factory=CrossRepoSummary)


class BranchFreshness(StrictResponseModel):
    branch: str
    upstream: str | None = None
    fetched: bool = False
    ahead: int | None = None
    behind: int | None = None
    state: Literal[
        "current",
        "behind",
        "ahead",
        "diverged",
        "no-upstream",
        "no-branch",
        "unknown",
        "unavailable",
    ]
    error: str | None = None


class FreshnessSummary(StrictResponseModel):
    status: Literal["checked", "not-checked"] = "not-checked"
    code: BranchFreshness | None = None
    memory: BranchFreshness | None = None
    ledgerMapsCodeHead: bool | None = None
    ledgerError: str | None = None


class ContextDiagnosticsHint(StrictResponseModel):
    providerDiagnosticsTool: str = "provider_diagnostics"


class ContextPacketV2(ToolResponse):
    operation: Literal["context_packet"] = "context_packet"
    contextPacketVersion: Literal[2] = 2
    repo: RepoSummary
    paths: ContextPaths
    memory: MemorySummary
    worktree: WorktreeSummary
    providers: ProviderSummary
    drift: DriftSummary
    freshness: FreshnessSummary = Field(default_factory=FreshnessSummary)
    diagnostics: ContextDiagnosticsHint = Field(default_factory=ContextDiagnosticsHint)
