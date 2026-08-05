"""Read-only provider watcher status for context packets."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from agents_remember.mcp.config import (
    DEFAULT_DOCKER_CONTROL_SECONDS,
    McpRuntimeConfig,
    ProviderScope,
)
from agents_remember.models.providers import (
    CGCWatcherState,
    ContextProviderItem,
    GrepAIWatcherState,
    ProviderCapability,
    ProviderDiagnosticsItem,
    ProviderDiagnosticsResponse,
    ProviderIdentity,
    ProviderRawStatus,
    ProviderRuntime,
    ProviderState,
    ProviderStatusResponse,
    ProviderSummary,
    ProviderTargetRepo,
    WatcherState,
)
from agents_remember.providers import lifecycle
from agents_remember.providers.current_state import write_current_provider_state
from agents_remember.providers.metrics import ProviderMetricsStore
from agents_remember.providers.recovery import PROVIDER_WATCHER_RESTART_RECOVERY
from agents_remember.providers.settings import write_lifecycle_settings


@dataclass(frozen=True)
class ProviderStatusProjection:
    configured: bool
    enabled: bool
    state: str
    ok: bool | None
    partial: bool
    settings_file: str | None = None
    current_state_file: str | None = None
    current_state: dict[str, Any] | None = None
    process_namespace: dict[str, Any] | None = None
    recovery_actions: list[dict[str, Any]] | None = None
    raw_status: dict[str, Any] | None = None


def provider_status_packet(
    config: McpRuntimeConfig,
    *,
    include_providers: bool = True,
    detail_limit: int = 20,
    target_repo_id: str | None = None,
) -> dict[str, Any]:
    summary = provider_summary(
        config,
        include_providers=include_providers,
        detail_limit=detail_limit,
        target_repo_id=target_repo_id,
    )
    # Containment R4 (260707-HFX-L1): the daemon-sampled containment metrics ride
    # the status packet even when providers are disabled — leftover stacks from a
    # dead session are exactly what must stay observable. Read-only; None until
    # the serving daemon's first sample lands.
    # 260707-HFX-L2: index staleness is a reportable STATE — the newest
    # index-lifecycle rows (seed catch-up, staleIndex, watcher readiness)
    # surface here so an operator sees behind-ness without reading logs.
    # Both are set on the MODEL and serialized by the single dump below. They used to be
    # stamped onto the dumped dict, which `ProviderStatusResponse` (extra="forbid") then
    # rejected at the `mcp/tools/base.py::_tool_payload` re-validation — so a sampled
    # metrics row or a non-empty index-state list turned `provider_status` into a
    # ValidationError instead of a response. `exclude_none=True` keeps the empty case
    # byte-identical to before: an unsampled metric and an empty row list emit no key.
    store = ProviderMetricsStore(config.coordination_root)
    index_states = store.read_recent_index_states(limit=10)
    response = ProviderStatusResponse(
        ok=True,
        providers=summary,
        metrics=store.read_current(),
        indexState=index_states or None,
    )
    return response.model_dump(mode="json", exclude_none=True)


def provider_summary_packet(
    config: McpRuntimeConfig,
    *,
    include_providers: bool = True,
    detail_limit: int = 20,
    target_repo_id: str | None = None,
) -> dict[str, Any]:
    return provider_summary(
        config,
        include_providers=include_providers,
        detail_limit=detail_limit,
        target_repo_id=target_repo_id,
    ).model_dump(mode="json", exclude_none=True)


def provider_diagnostics_packet(
    config: McpRuntimeConfig,
    *,
    detail_limit: int = 20,
) -> dict[str, Any]:
    projection = _provider_status_projection(config, include_providers=True)
    diagnostics = ProviderDiagnosticsResponse(
        ok=True,
        configured=projection.configured,
        enabled=projection.enabled,
        state=projection.state,
        partial=projection.partial,
        settingsFile=projection.settings_file,
        currentStateFile=projection.current_state_file,
        currentState=projection.current_state,
        processNamespace=projection.process_namespace,
        items=_provider_diagnostics_items(config, projection)[:detail_limit],
        recoveryActions=_provider_recovery_actions(projection),
        rawStatus=ProviderRawStatus.model_validate(projection.raw_status)
        if projection.raw_status
        else None,
    )
    return diagnostics.model_dump(mode="json", exclude_none=True)


def provider_summary(
    config: McpRuntimeConfig,
    *,
    include_providers: bool = True,
    detail_limit: int = 20,
    target_repo_id: str | None = None,
) -> ProviderSummary:
    projection = _provider_status_projection(config, include_providers=include_providers)
    return ProviderSummary(
        configured=projection.configured,
        enabled=projection.enabled,
        state=_provider_state(projection.state),
        ok=projection.ok,
        partial=projection.partial,
        currentStateFile=projection.current_state_file,
        processNamespace=projection.process_namespace,
        items=_provider_summary_items(
            config,
            projection,
            detail_limit=detail_limit,
            target_repo_id=target_repo_id,
        ),
        indexing=_provider_indexing_targets(projection),
        recoveryActions=_provider_recovery_actions(projection),
    )


def refresh_current_provider_state(
    config: McpRuntimeConfig,
    *,
    checked_at: datetime | None = None,
) -> dict[str, Any] | None:
    projection = _provider_status_projection(
        config,
        include_providers=True,
        checked_at=checked_at,
    )
    return projection.current_state


def _provider_status_projection(
    config: McpRuntimeConfig,
    *,
    include_providers: bool,
    checked_at: datetime | None = None,
) -> ProviderStatusProjection:
    configured = bool(config.providers)
    if not include_providers:
        return ProviderStatusProjection(
            configured=configured,
            enabled=configured,
            state="skipped",
            ok=None,
            partial=False,
        )
    if not configured:
        return ProviderStatusProjection(
            configured=False,
            enabled=False,
            state="noProviders",
            ok=True,
            partial=False,
        )

    status = _watchers_status(config)
    current_state = write_current_provider_state(config, status, checked_at=checked_at)
    aggregated = current_state["state"]
    # The raw watchers ok only proves containers; the aggregated current-state
    # ok also reflects graph/workspace content, so both must hold for a green
    # global summary.
    raw_ok = status.get("ok")
    ok = (
        bool(aggregated.get("ok"))
        if raw_ok is None
        else bool(raw_ok) and bool(aggregated.get("ok"))
    )
    providers_map = aggregated.get("providers", {})
    partial = status.get("partial", False) or (
        not ok
        and any(
            isinstance(provider, dict) and provider.get("state") == "ready"
            for provider in providers_map.values()
        )
    )
    return ProviderStatusProjection(
        configured=True,
        enabled=any(status.get("enabled", {}).values()),
        state=aggregated["state"],
        ok=ok,
        partial=partial,
        settings_file=status.get("settingsFile", ""),
        current_state_file=current_state["path"],
        current_state=current_state["state"],
        process_namespace=status.get("processNamespace"),
        recovery_actions=status.get("recoveryActions", []),
        raw_status=status,
    )


def _watchers_status(config: McpRuntimeConfig) -> dict[str, Any]:
    settings_path = write_lifecycle_settings(config)
    try:
        args = argparse.Namespace(
            coordination_root=config.coordination_root,
            from_settings=settings_path,
            dry_run=False,
            timeout=DEFAULT_DOCKER_CONTROL_SECONDS,
            json=True,
        )
        return lifecycle.watchers_run(args, "status")
    finally:
        settings_path.unlink(missing_ok=True)


def _provider_summary_items(
    config: McpRuntimeConfig,
    projection: ProviderStatusProjection,
    *,
    detail_limit: int,
    target_repo_id: str | None,
) -> list[ContextProviderItem]:
    if projection.state == "skipped":
        return []
    states = _current_provider_states(projection)
    items: list[ContextProviderItem] = []
    for provider_id in ("codegraphcontext-code", "grepai-memory"):
        provider = config.providers.get(provider_id)
        if provider is not None:
            items.append(
                _provider_summary_item(
                    provider,
                    states.get(provider_id, {}),
                    target_repo_id=target_repo_id,
                    detail_limit=detail_limit,
                )
            )
    return items[:detail_limit]


def _provider_recovery_actions(projection: ProviderStatusProjection) -> list[dict[str, Any]]:
    actions = list(projection.recovery_actions or [])
    states = _current_provider_states(projection)
    grepai_state = states.get("grepai-memory", {})
    if grepai_state.get("indexingState") == "noWorkspace":
        actions.append(
            {
                "provider": "grepai-memory",
                "action": "restart",
                "recoveryAction": PROVIDER_WATCHER_RESTART_RECOVERY,
            }
        )
    for repo_id, watcher in _cgc_watchers_map(states.get("codegraphcontext-code", {})).items():
        if watcher.get("indexingState") in {"empty", "backend-unreachable"}:
            actions.append(
                {
                    "provider": "codegraphcontext-code",
                    "repoId": repo_id,
                    "action": "restart",
                    "recoveryAction": PROVIDER_WATCHER_RESTART_RECOVERY,
                }
            )
    return actions


def _cgc_watchers_map(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    resources = state.get("resources")
    resources = resources if isinstance(resources, dict) else {}
    watchers = resources.get("watchers")
    if not isinstance(watchers, dict):
        return {}
    return {
        str(repo_id): watcher for repo_id, watcher in watchers.items() if isinstance(watcher, dict)
    }


def _provider_indexing_targets(projection: ProviderStatusProjection) -> list[str]:
    """Busy "<provider-id>:<repo-id>" targets with an initial scan in progress.

    Indexing is healthy-but-busy: it must not degrade state/ok, but agents
    reading only the compact summary still need to know results are partial."""
    states = _current_provider_states(projection)
    targets = [
        f"codegraphcontext-code:{repo_id}"
        for repo_id, watcher in sorted(
            _cgc_watchers_map(states.get("codegraphcontext-code", {})).items()
        )
        if watcher.get("indexingState") == "indexing"
    ]
    grepai_state = states.get("grepai-memory", {})
    if grepai_state.get("indexingState") == "indexing":
        targets.append("grepai-memory")
    return targets


def _provider_summary_item(
    provider: ProviderScope,
    state: dict[str, Any],
    *,
    target_repo_id: str | None,
    detail_limit: int,
) -> ContextProviderItem:
    return ContextProviderItem(
        id=provider.provider_id,
        capability=_provider_capability(provider.provider_id),
        state=_provider_state(state.get("state")),
        ok=state.get("ok"),
        runtime=_provider_runtime(provider.provider_id),
        identity=_provider_identity(provider),
        runtimeRoot=state.get("runtimeRoot") or provider.runtime_root.as_posix(),
        logRoot=state.get("logRoot") or provider.log_root.as_posix(),
        watchers=_provider_watchers(provider.provider_id, state, target_repo_id, detail_limit),
        targetRepo=_provider_target_repo(provider.provider_id, state, target_repo_id),
    )


def _provider_diagnostics_items(
    config: McpRuntimeConfig,
    projection: ProviderStatusProjection,
) -> list[ProviderDiagnosticsItem]:
    results = _raw_results_by_provider(projection)
    states = _current_provider_states(projection)
    items: list[ProviderDiagnosticsItem] = []
    for provider_id, raw_provider in (
        ("codegraphcontext-code", "codegraphcontext"),
        ("grepai-memory", "grepai"),
    ):
        provider = config.providers.get(provider_id)
        if provider is not None:
            items.append(
                _provider_diagnostics_item(
                    provider_id,
                    provider,
                    state=states.get(provider_id, {}),
                    raw_status=results.get(raw_provider),
                )
            )
    return items


def _provider_diagnostics_item(
    provider_id: str,
    provider: ProviderScope,
    *,
    state: dict[str, Any],
    raw_status: dict[str, Any] | None,
) -> ProviderDiagnosticsItem:
    return ProviderDiagnosticsItem(
        id=provider_id,
        state=str(state.get("state") or "unknown"),
        ok=state.get("ok"),
        identity=_provider_identity(provider),
        runtimeRoot=state.get("runtimeRoot") or provider.runtime_root.as_posix(),
        logRoot=state.get("logRoot") or provider.log_root.as_posix(),
        rawStatus=ProviderRawStatus.model_validate(raw_status) if raw_status else None,
    )


def _provider_watchers(
    provider_id: str,
    state: dict[str, Any],
    target_repo_id: str | None,
    detail_limit: int,
) -> GrepAIWatcherState | list[CGCWatcherState] | None:
    if provider_id == "grepai-memory":
        return GrepAIWatcherState(
            state=_watcher_state_from_up(state.get("watcherUp")),
            watcherUp=state.get("watcherUp"),
            indexingState=str(state.get("indexingState") or "unknown"),
        )
    if provider_id != "codegraphcontext-code" or target_repo_id is not None:
        return None
    watchers = _cgc_watcher_states(state)
    return watchers[:detail_limit]


def _provider_target_repo(
    provider_id: str,
    state: dict[str, Any],
    target_repo_id: str | None,
) -> ProviderTargetRepo | None:
    if target_repo_id is None:
        return None
    if provider_id == "codegraphcontext-code":
        watcher = _cgc_watcher_by_repo(state, target_repo_id)
        if watcher is None:
            return ProviderTargetRepo(repoId=target_repo_id, state="unknown")
        return ProviderTargetRepo(
            repoId=target_repo_id,
            state=watcher.state,
            ok=watcher.ok,
            watcherUp=watcher.watcherUp,
            indexingState=watcher.indexingState,
            lastRefresh=watcher.lastRefresh,
        )
    return ProviderTargetRepo(
        repoId=target_repo_id,
        state=_provider_state(state.get("state")),
        ok=state.get("ok"),
        watcherUp=state.get("watcherUp"),
        indexingState=str(state.get("indexingState") or "unknown"),
    )


def _cgc_watcher_by_repo(state: dict[str, Any], repo_id: str) -> CGCWatcherState | None:
    for watcher in _cgc_watcher_states(state):
        if watcher.repoId == repo_id:
            return watcher
    return None


def _cgc_watcher_states(state: dict[str, Any]) -> list[CGCWatcherState]:
    resources = state.get("resources")
    resources = resources if isinstance(resources, dict) else {}
    watchers = resources.get("watchers")
    if not isinstance(watchers, dict):
        return []
    items: list[CGCWatcherState] = []
    for repo_id, watcher in sorted(watchers.items()):
        if isinstance(watcher, dict):
            items.append(_cgc_watcher_state(str(repo_id), watcher))
    return items


def _cgc_watcher_state(repo_id: str, watcher: dict[str, Any]) -> CGCWatcherState:
    return CGCWatcherState(
        repoId=str(watcher.get("repoId") or repo_id),
        state=_provider_state(watcher.get("state")),
        ok=watcher.get("ok"),
        watcherUp=watcher.get("watcherUp"),
        indexingState=str(watcher.get("indexingState") or "unknown"),
        lastRefresh=_last_refresh_summary(watcher.get("lastRefresh")),
    )


def _last_refresh_summary(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return str(value)

    updated_at = value.get("updatedAt")
    parts: list[str] = [str(updated_at)] if updated_at else []
    if "returncode" in value:
        parts.append(f"returncode={value['returncode']}")
    if "durationSeconds" in value:
        parts.append(f"durationSeconds={value['durationSeconds']}")
    return " ".join(parts) if parts else None


def _watcher_state_from_up(watcher_up: Any) -> WatcherState:
    if watcher_up is True:
        return "running"
    if watcher_up is False:
        return "stopped"
    return "unknown"


def _current_provider_states(projection: ProviderStatusProjection) -> dict[str, dict[str, Any]]:
    current_state = projection.current_state or {}
    states = current_state.get("providers")
    if not isinstance(states, dict):
        return {}
    return {key: value for key, value in states.items() if isinstance(value, dict)}


def _raw_results_by_provider(projection: ProviderStatusProjection) -> dict[str, dict[str, Any]]:
    raw_status = projection.raw_status or {}
    results = raw_status.get("results")
    if not isinstance(results, list):
        return {}
    return {str(result.get("provider")): result for result in results if isinstance(result, dict)}


def _provider_identity(provider: ProviderScope) -> ProviderIdentity:
    return ProviderIdentity(
        scope=provider.scope,
        instanceId=provider.instance_id,
    )


def _provider_state(value: Any) -> ProviderState:
    state = str(value or "unknown")
    if state not in {
        "ready",
        "degraded",
        "failed",
        "disabled",
        "unknown",
        "noProviders",
        "skipped",
    }:
        state = "unknown"
    return cast(ProviderState, state)


def _provider_capability(provider_id: str) -> ProviderCapability:
    if provider_id == "grepai-memory":
        return "semantic-memory-search"
    return "code-relationship-search"


def _provider_runtime(provider_id: str) -> ProviderRuntime:
    if provider_id in {"grepai-memory", "codegraphcontext-code"}:
        return "docker"
    return "unknown"
