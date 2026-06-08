"""Models for provider summaries and dedicated provider diagnostics."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from agents_remember.models.base import (
    FlexibleResponseModel,
    FlexibleToolResponse,
    StrictResponseModel,
    ToolResponse,
)

ProviderCapability = Literal["semantic-memory-search", "code-relationship-search"]
ProviderRuntime = Literal["docker", "local", "unknown"]
WatcherState = Literal["running", "partial", "stopped", "unknown"]
ProviderState = Literal[
    "ready",
    "degraded",
    "failed",
    "disabled",
    "unknown",
    "noProviders",
    "skipped",
]


class ProviderIdentity(StrictResponseModel):
    scope: str
    instanceId: str
    coordinationRoot: str | None = None


class ProviderTargetRepo(StrictResponseModel):
    repoId: str
    state: ProviderState
    ok: bool | None = None
    watcherUp: bool | None = None
    indexingState: str = "unknown"
    lastRefresh: str | None = None


class GrepAIWatcherState(StrictResponseModel):
    kind: Literal["grepai"] = "grepai"
    state: WatcherState
    watcherUp: bool | None = None
    indexingState: str = "unknown"


class CGCWatcherState(StrictResponseModel):
    kind: Literal["codegraphcontext"] = "codegraphcontext"
    repoId: str
    state: ProviderState
    ok: bool | None = None
    watcherUp: bool | None = None
    indexingState: str = "unknown"
    lastRefresh: str | None = None


class ContextProviderItem(StrictResponseModel):
    id: str
    capability: ProviderCapability
    state: ProviderState
    ok: bool | None = None
    runtime: ProviderRuntime = "unknown"
    identity: ProviderIdentity
    runtimeRoot: str | None = None
    logRoot: str | None = None
    watchers: GrepAIWatcherState | list[CGCWatcherState] | None = None
    targetRepo: ProviderTargetRepo | None = None


class ProviderSummary(StrictResponseModel):
    configured: bool
    enabled: bool
    state: ProviderState
    ok: bool | None = None
    partial: bool = False
    currentStateFile: str | None = None
    processNamespace: dict[str, Any] | None = None
    items: list[ContextProviderItem] = Field(default_factory=list)
    recoveryActions: list[dict[str, Any]] = Field(default_factory=list)
    diagnosticsTool: str = "provider_diagnostics"


class ProviderStatusResponse(ToolResponse):
    operation: Literal["provider_status"] = "provider_status"
    providers: ProviderSummary


class ProviderRawStatus(FlexibleResponseModel):
    provider: str | None = None
    action: str | None = None
    ok: bool | None = None


class ProviderDiagnosticsItem(StrictResponseModel):
    id: str
    state: ProviderState | str = "unknown"
    ok: bool | None = None
    identity: ProviderIdentity | None = None
    runtimeRoot: str | None = None
    logRoot: str | None = None
    rawStatus: ProviderRawStatus | None = None


class ProviderDiagnosticsResponse(ToolResponse):
    operation: Literal["provider_diagnostics"] = "provider_diagnostics"
    configured: bool
    enabled: bool
    state: ProviderState | str
    partial: bool = False
    settingsFile: str | None = None
    currentStateFile: str | None = None
    currentState: dict[str, Any] | None = None
    processNamespace: dict[str, Any] | None = None
    items: list[ProviderDiagnosticsItem] = Field(default_factory=list)
    recoveryActions: list[dict[str, Any]] = Field(default_factory=list)
    rawStatus: ProviderRawStatus | None = None


ProviderWatcherAction = Literal["status", "start", "stop", "restart", "refresh", "shutdown-all"]


class ProviderWatchersResponse(FlexibleToolResponse):
    operation: Literal["provider_watchers"] = "provider_watchers"
    action: ProviderWatcherAction | str | None = None
    state: ProviderState | str | None = None
    partial: bool | None = None
    currentStateFile: str | None = None
    currentState: dict[str, Any] | None = None
    steps: list[dict[str, Any]] = Field(default_factory=list)
    recoveryActions: list[dict[str, Any]] = Field(default_factory=list)


class ProviderNativeToolResponse(FlexibleToolResponse):
    provider: str | None = None
    action: str | None = None
    dryRun: bool | None = None
    settingsFile: str | None = None
    state: str | None = None
    error: str | None = None


class GrepAISearchResponse(ProviderNativeToolResponse):
    operation: Literal["grepai_search"] = "grepai_search"


class GrepAITraceResponse(ProviderNativeToolResponse):
    operation: Literal["grepai_trace"] = "grepai_trace"


class CGCSymbolSearchResponse(ProviderNativeToolResponse):
    operation: Literal["cgc_symbol_search"] = "cgc_symbol_search"


class CGCCallersResponse(ProviderNativeToolResponse):
    operation: Literal["cgc_callers"] = "cgc_callers"


class CGCCalleesResponse(ProviderNativeToolResponse):
    operation: Literal["cgc_callees"] = "cgc_callees"


class CGCDependenciesResponse(ProviderNativeToolResponse):
    operation: Literal["cgc_dependencies"] = "cgc_dependencies"


class CGCComplexityResponse(ProviderNativeToolResponse):
    operation: Literal["cgc_complexity"] = "cgc_complexity"


class CGCVisualizeResponse(ProviderNativeToolResponse):
    operation: Literal["cgc_visualize"] = "cgc_visualize"
    port: int | None = None
    context: str | None = None
