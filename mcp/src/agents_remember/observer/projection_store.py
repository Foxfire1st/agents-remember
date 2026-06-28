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
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from agents_remember.observer.events import Event
from agents_remember.observer.paths import observer_root
from agents_remember.observer.projection import (
    LedgerNode,
    RouteCoverageNode,
    SidecarStaleNode,
    WorkspaceProjection,
)
from agents_remember.observer.reducer import project_workspace
from agents_remember.observer.snapshots import (
    read_agent_pickups,
    read_drift_snapshots,
    read_enclosures,
    read_engine_process_facts,
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
from agents_remember.providers.status import refresh_current_provider_state

if TYPE_CHECKING:
    from agents_remember.mcp.config import McpRuntimeConfig

LATEST_STATE = "latest-state.json"
LATEST_METRICS = "latest-metrics.json"
PROVIDER_REFRESH_TTL_SECONDS = 10.0

logger = logging.getLogger(__name__)


def read_lifecycle_logs(root: Path) -> list[list[Event]]:
    """Every per-lifecycle log under ``lifecycles/<id>/events.jsonl``, validated."""
    store = EventStore(root)
    lifecycles_dir = root / "lifecycles"
    if not lifecycles_dir.is_dir():
        return []
    logs: list[list[Event]] = []
    for entry in sorted(lifecycles_dir.iterdir()):
        if not entry.is_dir():
            continue
        events = store.read(entry.name)
        if events:
            logs.append(events)
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
    sidecar_staleness, route_coverage, ledgers = _gather_repo_surfaces(config, moment)
    enclosures = read_enclosures(coordination_root)
    if provider_refresher is not None:
        provider_refresher.maybe_refresh(config, now=moment)
    projection = project_workspace(
        read_lifecycle_logs(root),
        enclosures=enclosures,
        providers=read_providers(config, now=moment),
        now=moment,
        drift_snapshots=read_drift_snapshots(coordination_root, now=moment),
        sidecar_staleness=sidecar_staleness,
        setup_summaries=read_setup_summaries(coordination_root, now=moment),
        setup_progress=read_setup_progress_nodes(coordination_root, now=moment),
        route_coverage=route_coverage,
        tool_reports=read_tool_reports(coordination_root, now=moment),
        agent_pickups=read_agent_pickups(coordination_root, now=moment),
        ledgers=ledgers,
        task_documents=read_task_documents(coordination_root, enclosures=enclosures, now=moment),
        series=read_series_documents(coordination_root, now=moment),
        engine_process_facts=read_engine_process_facts(coordination_root),
        engine_start_progress=read_start_progress_entries(coordination_root, now=moment),
        gates=read_gates(coordination_root, now=moment),
    )
    write_projection(root, projection)
    return projection


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


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{new_ulid()}.tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
