"""Log/snapshot reading and atomic projection writes -- the reducer's I/O edge.

``read_lifecycle_logs`` enumerates the per-lifecycle truth; ``project_and_write``
ties reading, the pure reduction, and the write together (the entry the serving
layer drives on a tick, slice 04). Projections are written tmp-write +
``os.replace`` so a polling dashboard reader never sees a half-written
``latest-state.json`` -- stronger than the plain ``write_text`` of the
``setup_progress`` precedent, which a concurrent reader can tear.

The store root is resolved through :func:`agents_remember.observer.paths.observer_root`,
the single path abstraction shared with the write side.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from agents_remember.controlplane.attention_dismissals import AttentionDismissalStore
from agents_remember.observer.drift_snapshots import prune_orphaned_drift_snapshots
from agents_remember.observer.event_retention import prune_expired_lifecycle_event_logs
from agents_remember.observer.events import Event
from agents_remember.observer.lifecycle_state import TERMINAL_STATES
from agents_remember.observer.paths import observer_root
from agents_remember.observer.projection import (
    TASK_DOCUMENTS_PAYLOAD_BUDGET_BYTES,
    LedgerNode,
    RouteCoverageNode,
    SidecarStaleNode,
    WorkspaceProjection,
    task_documents_body_bytes,
)
from agents_remember.observer.reducer import project_workspace
from agents_remember.observer.snapshots import (
    read_agent_pickups,
    read_drift_snapshots,
    read_enclosures,
    read_engine_process_facts,
    read_expectation_rows,
    read_gates,
    read_ledger,
    read_providers,
    read_route_coverage,
    read_series_documents,
    read_setup_progress_nodes,
    read_setup_summaries,
    read_sidecar_staleness,
    read_start_progress_entries,
    read_task_documents,
    read_tool_reports,
)
from agents_remember.observer.store import EventStore
from agents_remember.observer.ulid import new_ulid
from agents_remember.observer.worktree_provider_admission import (
    active_enclosure_worktree_groups,
    admitted_worktree_groups,
    series_retained_lifecycle_ids,
)
from agents_remember.providers.status import refresh_current_provider_state

if TYPE_CHECKING:
    from agents_remember.mcp.config import McpRuntimeConfig

LATEST_STATE = "latest-state.json"
LATEST_METRICS = "latest-metrics.json"
PROVIDER_REFRESH_TTL_SECONDS = 10.0
REPO_SURFACE_REFRESH_TTL_SECONDS = 15.0
# F6 guardrail: the projection ticks every 1s, so a per-tick over-budget warning would be pure spam.
# Log at most this often -- enough to surface a body-heavy broadcast in the logs without flooding them.
TASK_DOCUMENTS_PAYLOAD_WARN_INTERVAL_SECONDS = 300.0

logger = logging.getLogger(__name__)

# F6: last time the over-budget task-doc payload warning fired, keyed "at" (an in-place-mutated dict --
# the codebase idiom for cross-tick module state, e.g. _task_doc_cache -- so no rebinding `global`).
_last_task_payload_warn: dict[str, datetime] = {}


@dataclass(frozen=True)
class _RepoSurfaceCacheEntry:
    at: datetime
    value: tuple[list[SidecarStaleNode], list[RouteCoverageNode], list[LedgerNode]]


_repo_surface_cache: dict[tuple[tuple[str, str, str], ...], _RepoSurfaceCacheEntry] = {}


@dataclass
class _LifecycleLogCacheEntry:
    mtime_ns: int
    size: int
    events: list[Event]


# 260707-HFX2-L12 F9/F7: the projection re-read + re-validated EVERY lifecycle's full events.jsonl
# each 1s tick. Heartbeats append only every 15s (and stop entirely once a leaf is parked past its
# inactivity cutoff), so most logs are byte-identical between ticks. This cache reuses the parsed
# event list when a log's (mtime_ns, size) is unchanged, bounding per-tick parse cost to the logs
# that actually changed. Keyed by log path; pruned to the live set each tick so it cannot leak.
_lifecycle_log_cache: dict[str, _LifecycleLogCacheEntry] = {}


def read_lifecycle_logs(root: Path) -> list[list[Event]]:
    """Every per-lifecycle log under ``lifecycles/<id>/events.jsonl``, validated (F9-cached)."""
    store = EventStore(root)
    lifecycles_dir = root / "lifecycles"
    if not lifecycles_dir.is_dir():
        return []
    logs: list[list[Event]] = []
    seen: set[str] = set()
    for entry in sorted(lifecycles_dir.iterdir()):
        if not entry.is_dir():
            continue
        path = entry / "events.jsonl"
        try:
            stat = path.stat()
        except OSError:
            continue
        key = str(path)
        seen.add(key)
        cached = _lifecycle_log_cache.get(key)
        if cached is not None and cached.mtime_ns == stat.st_mtime_ns and cached.size == stat.st_size:
            events = cached.events
        else:
            events = store.read(entry.name)
            _lifecycle_log_cache[key] = _LifecycleLogCacheEntry(
                mtime_ns=stat.st_mtime_ns, size=stat.st_size, events=events
            )
        if events:
            logs.append(events)
    for stale in [key for key in _lifecycle_log_cache if key not in seen]:
        _lifecycle_log_cache.pop(stale, None)
    return logs


def write_projection(root: Path, projection: WorkspaceProjection) -> None:
    """Atomically write ``latest-state.json`` + ``latest-metrics.json``."""
    root.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(root / LATEST_STATE, projection.model_dump(by_alias=True, exclude_none=True))
    _atomic_write_json(
        root / LATEST_METRICS,
        projection.metrics.model_dump(by_alias=True, exclude_none=True),
    )


class ProviderStateRefresher:
    """Refresh provider current-state snapshots before the observer reads them."""

    def __init__(
        self,
        *,
        ttl_seconds: float = PROVIDER_REFRESH_TTL_SECONDS,
        refresh: Any = refresh_current_provider_state,
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._refresh = refresh
        self._last_refresh: datetime | None = None

    def maybe_refresh(self, config: McpRuntimeConfig, *, now: datetime) -> None:
        if not config.providers:
            return
        if self._last_refresh is not None:
            age = (now - self._last_refresh).total_seconds()
            if age < self._ttl_seconds:
                return
        self._last_refresh = now
        try:
            self._refresh(config, checked_at=now)
        except Exception:
            logger.warning("provider current-state refresh failed; using last snapshot", exc_info=True)


class ProviderStateRefresh(Protocol):
    """Structural contract for projection pre-read provider refreshers."""

    def maybe_refresh(self, config: McpRuntimeConfig, *, now: datetime) -> None: ...


def project_and_write(
    config: McpRuntimeConfig,
    *,
    now: datetime | None = None,
    provider_refresher: ProviderStateRefresh | None = None,
) -> WorkspaceProjection:
    """Read logs + structural + analytical snapshots, reduce the tree, write it atomically."""
    moment = now or datetime.now(UTC)
    root = observer_root(config)
    coordination_root = config.coordination_root
    # Read the durable enclosures FIRST so retention is enclosure-aware: a not-yet-retired master
    # series protects every one of its leaves' event logs from the inactivity TTL (a running durable
    # task must never lose its history just because it has been a while since its last lifecycle event).
    enclosures = read_enclosures(coordination_root)
    prune_expired_lifecycle_event_logs(
        root,
        now=moment,
        protected_lifecycle_ids=series_retained_lifecycle_ids(enclosures, now=moment),
    )
    lifecycle_logs = read_lifecycle_logs(root)
    sidecar_staleness, route_coverage, ledgers = _gather_repo_surfaces_cached(config, moment)
    provider_groups = admitted_worktree_groups(enclosures, lifecycle_logs, now=moment)
    engine_groups = active_enclosure_worktree_groups(enclosures, lifecycle_logs, now=moment)
    prune_orphaned_drift_snapshots(config)
    if provider_refresher is not None:
        provider_refresher.maybe_refresh(config, now=moment)
    attention_store = AttentionDismissalStore(root)
    projection = project_workspace(
        lifecycle_logs,
        enclosures=enclosures,
        providers=read_providers(config, now=moment, active_worktree_groups=provider_groups),
        now=moment,
        drift_snapshots=read_drift_snapshots(coordination_root, now=moment),
        sidecar_staleness=sidecar_staleness,
        setup_summaries=read_setup_summaries(coordination_root, now=moment),
        setup_progress=read_setup_progress_nodes(
            coordination_root, now=moment, active_worktree_groups=provider_groups
        ),
        route_coverage=route_coverage,
        tool_reports=read_tool_reports(coordination_root, now=moment),
        agent_pickups=read_agent_pickups(coordination_root, now=moment),
        expectation_rows=read_expectation_rows(coordination_root, now=moment),
        ledgers=ledgers,
        task_documents=read_task_documents(coordination_root, enclosures=enclosures, now=moment),
        series=read_series_documents(coordination_root, now=moment),
        engine_process_facts=read_engine_process_facts(
            coordination_root, active_worktree_groups=engine_groups, now=moment
        ),
        engine_start_progress=read_start_progress_entries(coordination_root, now=moment),
        gates=read_gates(coordination_root, now=moment),
        attention_dismissals=attention_store.current(),
        # The Topology constellation filters its tree on this active set — the same
        # active-enclosure admission the Engine Room's engine_process_facts use, so both
        # views share one definition of "active" rather than diverging.
        active_worktree_groups=sorted(engine_groups),
    )
    attention_store.prune_lifecycles(
        {lifecycle.id for lifecycle in projection.lifecycles if lifecycle.state not in TERMINAL_STATES}
    )
    _warn_if_task_documents_payload_over_budget(projection, now=moment)
    write_projection(root, projection)
    return projection


def _warn_if_task_documents_payload_over_budget(
    projection: WorkspaceProjection, *, now: datetime
) -> int:
    """Flag (rate-limited) when the broadcast serialises a body-heavy task-doc payload (F6/CS-6 D1).

    Returns the measured body-byte cost so callers/tests can assert on it. This is observability only --
    no truncation, no contract change -- surfacing the unbounded ``analytics.taskDocuments`` payload that
    the escalated F6 windowing follow-up will bound, so the growth is visible in logs instead of silent.
    """
    payload_bytes = task_documents_body_bytes(projection.analytics.taskDocuments)
    if payload_bytes <= TASK_DOCUMENTS_PAYLOAD_BUDGET_BYTES:
        return payload_bytes
    last = _last_task_payload_warn.get("at")
    if last is not None:
        age = (now - last).total_seconds()
        if 0 <= age < TASK_DOCUMENTS_PAYLOAD_WARN_INTERVAL_SECONDS:
            return payload_bytes
    _last_task_payload_warn["at"] = now
    logger.warning(
        "analytics.taskDocuments broadcast body payload %d bytes exceeds budget %d across %d docs; "
        "task-doc bodies are serialised into every projection tick -- F6 windowing (bodies on-demand) "
        "is the follow-up that bounds this",
        payload_bytes,
        TASK_DOCUMENTS_PAYLOAD_BUDGET_BYTES,
        len(projection.analytics.taskDocuments),
    )
    return payload_bytes


def _gather_repo_surfaces(
    config: McpRuntimeConfig, moment: datetime
) -> tuple[list[SidecarStaleNode], list[RouteCoverageNode], list[LedgerNode]]:
    """Per-repo analytical surfaces (sidecar staleness, route coverage, ledger).

    These walk each managed repo's onboarding/memory roots, so they are gathered
    per ``RepositoryScope`` rather than once under the coordination root.
    """
    sidecar_staleness: list[SidecarStaleNode] = []
    route_coverage: list[RouteCoverageNode] = []
    ledgers: list[LedgerNode] = []
    for scope in config.repositories.values():
        if scope.memory_root is None:
            continue
        onboarding_root = scope.memory_root / "onboarding"
        sidecar_staleness.extend(
            read_sidecar_staleness(onboarding_root, repository=scope.repo_id, now=moment)
        )
        route_coverage.extend(read_route_coverage(onboarding_root, repository=scope.repo_id))
        ledger = read_ledger(scope.memory_root, code_root=scope.path)
        if ledger is not None:
            ledgers.append(ledger)
    return sidecar_staleness, route_coverage, ledgers


def _gather_repo_surfaces_cached(
    config: McpRuntimeConfig, moment: datetime
) -> tuple[list[SidecarStaleNode], list[RouteCoverageNode], list[LedgerNode]]:
    key = _repo_surface_cache_key(config)
    entry = _repo_surface_cache.get(key)
    if entry is not None:
        age = (moment - entry.at).total_seconds()
        if 0 <= age < REPO_SURFACE_REFRESH_TTL_SECONDS:
            return entry.value
    value = _gather_repo_surfaces(config, moment)
    _repo_surface_cache[key] = _RepoSurfaceCacheEntry(at=moment, value=value)
    return value


def _repo_surface_cache_key(config: McpRuntimeConfig) -> tuple[tuple[str, str, str], ...]:
    rows: list[tuple[str, str, str]] = []
    for repo_id, scope in sorted(config.repositories.items()):
        rows.append(
            (
                repo_id,
                scope.path.as_posix(),
                scope.memory_root.as_posix() if scope.memory_root is not None else "",
            )
        )
    return tuple(rows)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{new_ulid()}.tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
