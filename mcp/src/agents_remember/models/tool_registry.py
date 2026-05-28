"""Registry that maps public MCP tools to declared response models."""

from __future__ import annotations

from pydantic import BaseModel

from agents_remember.models.benchmarks import (
    CodexBenchmarkPrepareResponse,
    CodexBenchmarkRunResponse,
)
from agents_remember.models.context_packet import ContextPacketV2
from agents_remember.models.core import PingResponse, ServerInfoResponse
from agents_remember.models.memory import (
    DriftCheckResponse,
    MemoryBaselineAdoptResponse,
    MemoryBaselineStatusResponse,
    MemoryCarryoverApplyResponse,
    MemoryCarryoverPlanResponse,
    MemoryInitResponse,
    MemoryQualityCheckResponse,
    RouteIndexRefreshResponse,
)
from agents_remember.models.providers import (
    CGCCalleesResponse,
    CGCCallersResponse,
    CGCComplexityResponse,
    CGCDependenciesResponse,
    CGCSymbolSearchResponse,
    CGCVisualizeResponse,
    GrepAISearchResponse,
    GrepAITraceResponse,
    ProviderDiagnosticsResponse,
    ProviderStatusResponse,
    ProviderWatchersResponse,
)
from agents_remember.models.runtime import ResolveContextResponse, RuntimeInstallResponse
from agents_remember.models.skills import SkillsInstallResponse
from agents_remember.models.worktree import (
    DirectCloseoutApplyResponse,
    DirectCloseoutPreviewResponse,
    WorktreeAttachResponse,
    WorktreeCleanupResponse,
    WorktreeCloseoutApplyResponse,
    WorktreeCloseoutPreviewResponse,
    WorktreeIntegrateResponse,
    WorktreeStartResponse,
    WorktreeStatusResponse,
)

PUBLIC_TOOL_RESPONSE_MODELS: dict[str, type[BaseModel]] = {
    "ping": PingResponse,
    "server_info": ServerInfoResponse,
    "context_packet": ContextPacketV2,
    "runtime_install": RuntimeInstallResponse,
    "resolve_context": ResolveContextResponse,
    "drift_check": DriftCheckResponse,
    "memory_quality_check": MemoryQualityCheckResponse,
    "route_index_refresh": RouteIndexRefreshResponse,
    "memory_init": MemoryInitResponse,
    "skills_install": SkillsInstallResponse,
    "provider_status": ProviderStatusResponse,
    "provider_diagnostics": ProviderDiagnosticsResponse,
    "grepai_search": GrepAISearchResponse,
    "grepai_trace": GrepAITraceResponse,
    "cgc_symbol_search": CGCSymbolSearchResponse,
    "cgc_callers": CGCCallersResponse,
    "cgc_callees": CGCCalleesResponse,
    "cgc_dependencies": CGCDependenciesResponse,
    "cgc_complexity": CGCComplexityResponse,
    "provider_watchers": ProviderWatchersResponse,
    "cgc_visualize": CGCVisualizeResponse,
    "worktree_start": WorktreeStartResponse,
    "worktree_attach": WorktreeAttachResponse,
    "worktree_status": WorktreeStatusResponse,
    "worktree_closeout_preview": WorktreeCloseoutPreviewResponse,
    "worktree_closeout_apply": WorktreeCloseoutApplyResponse,
    "direct_closeout_preview": DirectCloseoutPreviewResponse,
    "direct_closeout_apply": DirectCloseoutApplyResponse,
    "worktree_integrate": WorktreeIntegrateResponse,
    "worktree_cleanup": WorktreeCleanupResponse,
    "memory_baseline_status": MemoryBaselineStatusResponse,
    "memory_baseline_adopt": MemoryBaselineAdoptResponse,
    "memory_carryover_plan": MemoryCarryoverPlanResponse,
    "memory_carryover_apply": MemoryCarryoverApplyResponse,
    "codex_benchmark_prepare": CodexBenchmarkPrepareResponse,
    "codex_benchmark_run": CodexBenchmarkRunResponse,
}
