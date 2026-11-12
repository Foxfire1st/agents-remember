"""Pydantic response contracts for Agents Remember MCP tools."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agents_remember.models.tokens import (
        DEFAULT_TOKEN_COUNTER,
        ApproximateTokenCounter,
        ResponseTokenCounter,
        TiktokenTokenCounter,
        count_response_tokens,
        dump_with_token_count,
        finalize_payload_tokens,
        response_payload,
    )

from agents_remember.kernel.memory_mode import MemoryMode
from agents_remember.models.base import (
    FlexibleResponseModel,
    FlexibleToolResponse,
    ResponseModel,
    StrictResponseModel,
    ToolResponse,
)
from agents_remember.models.benchmarks import (
    CodexBenchmarkPrepareResponse,
    CodexBenchmarkRunResponse,
)
from agents_remember.models.context_packet import ContextPacketV2, SlimContextPacket
from agents_remember.models.core import ServerInfoResponse, Transport
from agents_remember.models.drift import DriftSummary
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
    CGCWatcherState,
    ContextProviderItem,
    GrepAISearchResponse,
    GrepAITraceResponse,
    GrepAIWatcherState,
    ProviderDiagnosticsItem,
    ProviderDiagnosticsResponse,
    ProviderIdentity,
    ProviderNativeToolResponse,
    ProviderRawStatus,
    ProviderStatusResponse,
    ProviderSummary,
    ProviderTargetRepo,
    ProviderWatcherAction,
    ProviderWatchersResponse,
    WatcherState,
)
from agents_remember.models.runtime import RuntimeInstallResponse
from agents_remember.models.task_document_ref import TaskScopedReaderContext
from agents_remember.models.tools.tool_registry import TOOL_RESPONSE_MODELS
from agents_remember.models.worktree import (
    CleanupStatus,
    HumanReviewStatus,
    IntegrationStatus,
    LifecycleStatus,
    NextOperation,
    NextTool,
    WorkflowKind,
    WorktreeAbandonResponse,
    WorktreeAttachResponse,
    WorktreeCleanupResponse,
    WorktreeCloseoutApplyResponse,
    WorktreeCloseoutPreviewResponse,
    WorktreeCommandResponse,
    WorktreeIntegrateResponse,
    WorktreePhase,
    WorktreeStartResponse,
    WorktreeState,
    WorktreeStatusResponse,
    WorktreeSummary,
)

__all__ = [
    "DEFAULT_TOKEN_COUNTER",
    "TOOL_RESPONSE_MODELS",
    "ApproximateTokenCounter",
    "CGCCalleesResponse",
    "CGCCallersResponse",
    "CGCComplexityResponse",
    "CGCDependenciesResponse",
    "CGCSymbolSearchResponse",
    "CGCVisualizeResponse",
    "CGCWatcherState",
    "CleanupStatus",
    "CodexBenchmarkPrepareResponse",
    "CodexBenchmarkRunResponse",
    "ContextPacketV2",
    "ContextProviderItem",
    "DriftCheckResponse",
    "DriftSummary",
    "FlexibleResponseModel",
    "FlexibleToolResponse",
    "GrepAISearchResponse",
    "GrepAITraceResponse",
    "GrepAIWatcherState",
    "HumanReviewStatus",
    "IntegrationStatus",
    "LifecycleStatus",
    "MemoryBaselineAdoptResponse",
    "MemoryBaselineStatusResponse",
    "MemoryCarryoverApplyResponse",
    "MemoryCarryoverPlanResponse",
    "MemoryInitResponse",
    "MemoryMode",
    "MemoryQualityCheckResponse",
    "NextOperation",
    "NextTool",
    "ProviderDiagnosticsItem",
    "ProviderDiagnosticsResponse",
    "ProviderIdentity",
    "ProviderNativeToolResponse",
    "ProviderRawStatus",
    "ProviderStatusResponse",
    "ProviderSummary",
    "ProviderTargetRepo",
    "ProviderWatcherAction",
    "ProviderWatchersResponse",
    "ResponseModel",
    "ResponseTokenCounter",
    "RouteIndexRefreshResponse",
    "RuntimeInstallResponse",
    "ServerInfoResponse",
    "SlimContextPacket",
    "StrictResponseModel",
    "TaskScopedReaderContext",
    "TiktokenTokenCounter",
    "ToolResponse",
    "Transport",
    "WatcherState",
    "WorkflowKind",
    "WorktreeAbandonResponse",
    "WorktreeAttachResponse",
    "WorktreeCleanupResponse",
    "WorktreeCloseoutApplyResponse",
    "WorktreeCloseoutPreviewResponse",
    "WorktreeCommandResponse",
    "WorktreeIntegrateResponse",
    "WorktreePhase",
    "WorktreeStartResponse",
    "WorktreeState",
    "WorktreeStatusResponse",
    "WorktreeSummary",
    "count_response_tokens",
    "dump_with_token_count",
    "finalize_payload_tokens",
    "response_payload",
]


# MIK-R204 @ v1 rule 1 (the resolver sits at the end of the module): the token counter imports `tiktoken`, which is a second of startup for a package
# that discovery never needs. The names stay importable exactly as before; they are resolved on first use.
_TOKEN_NAMES = frozenset(
    {
        "DEFAULT_TOKEN_COUNTER",
        "ApproximateTokenCounter",
        "ResponseTokenCounter",
        "TiktokenTokenCounter",
        "count_response_tokens",
        "dump_with_token_count",
        "finalize_payload_tokens",
        "response_payload",
    }
)


def __getattr__(name: str) -> object:
    """Resolve the token-counter vocabulary on first use instead of at import (PEP 562)."""

    if name in _TOKEN_NAMES:
        from agents_remember.models import tokens as module  # noqa: PLC0415

        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
