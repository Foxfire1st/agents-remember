"""Log/snapshot reading and atomic projection writes -- the reducer's I/O edge.

``read_lifecycle_logs`` enumerates the per-lifecycle truth; ``project_and_write``
ties reading, the pure reduction, and the write together (the entry the serving
layer drives on a tick). Projections are published through
:mod:`agents_remember.kernel.atomic_write` so a polling dashboard reader never sees a
half-written ``latest-state.json`` -- stronger than the plain ``write_text`` of the
``setup_progress`` precedent, which a concurrent reader can tear.

The store root is resolved through :func:`agents_remember.observer.paths.observer_root`,
the single path abstraction shared with the write side.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from agents_remember.controlplane.attention_dismissals import AttentionDismissalStore
from agents_remember.kernel.atomic_write import atomic_write_text
from agents_remember.observer.events import Event
from agents_remember.observer.lifecycle_state import TERMINAL_STATES
from agents_remember.observer.projection import (
    TASK_DOCUMENTS_PAYLOAD_BUDGET_BYTES,
    LedgerNode,
    RouteCoverageNode,
    SidecarStaleNode,
    WorkspaceProjection,
    task_documents_body_bytes,
)
from agents_remember.observer.reducer import (
    AnalyticalInputs,
    WorkspaceStructure,
    project_workspace,
)
from agents_remember.observer.store import EventStore
from agents_remember.providers.status import refresh_current_provider_state
from agents_remember.serving.projections.contract_snapshot import ContractSnapshotCache
from agents_remember.serving.projections.paths import observer_root
from agents_remember.serving.projections.projection_inputs import (
    ProjectionInputState,
    ProjectionReaders,
    ProjectionRefresh,
    RefreshPass,
)
from agents_remember.serving.projections.snapshots import (
    read_ledger,
    read_route_coverage,
    read_sidecar_staleness,
)

if TYPE_CHECKING:
    from agents_remember.kernel.primitives.runtime_config import (
        McpRuntimeConfig,
    )
    from agents_remember.serving.projections.landing_state import LandingStateReader

LATEST_STATE = "latest-state.json"
LATEST_METRICS = "latest-metrics.json"
PROVIDER_REFRESH_TTL_SECONDS = 10.0
# Repo-surface TTL: this walk opens+parses every onboarding document across EVERY managed
# repo (8 memory repos, thousands of markdown files) — py-spy measured it as the dominant slice
# of the projection tick at the old 15 s TTL. Staleness/coverage/ledger panels tolerate minutes
# of lag; the tick budget does not tolerate the walk.
REPO_SURFACE_REFRESH_TTL_SECONDS = 120.0
# Guardrail: the projection ticks every 1s, so a per-tick over-budget warning would be pure spam.
# Log at most this often -- enough to surface a body-heavy broadcast in the logs without flooding them.
TASK_DOCUMENTS_PAYLOAD_WARN_INTERVAL_SECONDS = 300.0

logger = logging.getLogger(__name__)

# Last time the over-budget task-doc payload warning fired, keyed "at" (an in-place-mutated dict --
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
    log_events: list[Event]


# ONE leaf-enclosure-contract enumeration + parse pass per tick, shared by
# read_enclosures, read_engine_process_facts, and drift-snapshot pruning (each previously ran its
# own walk + load_contract pass = 3x per tick). The cache reuses a parsed contract while its
# (mtime_ns, size, ctime_ns) is unchanged and prunes to the live enumeration each build. Mutated only on the
# projection worker thread (ticks are serialized by the projector's awaited asyncio.to_thread),
# matching the module-level cache discipline of _lifecycle_log_cache below.
_contract_snapshot_cache = ContractSnapshotCache()

# The projection re-read + re-validated EVERY lifecycle's full events.jsonl
# each 1s tick. Heartbeats append only every 15s (and stop entirely once a leaf is parked past its
# inactivity cutoff), so most logs are byte-identical between ticks. This cache reuses the parsed
# event list when a log's (mtime_ns, size) is unchanged, bounding per-tick parse cost to the logs
# that actually changed. Keyed by log path; pruned to the live set each tick so it cannot leak.
_lifecycle_log_cache: dict[str, _LifecycleLogCacheEntry] = {}


def read_lifecycle_logs(root: Path) -> list[list[Event]]:
    """Every per-lifecycle log under ``lifecycles/<id>/events.jsonl``, validated (cached).

    Heartbeats are coalesced into ``heartbeat.json`` sidecars, so a heartbeat tick changes one tiny
    JSON file instead of invalidating the cached parse of the full event log.
    """
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
        if (
            cached is not None
            and cached.mtime_ns == stat.st_mtime_ns
            and cached.size == stat.st_size
        ):
            log_events = cached.log_events
        else:
            log_events = store.read_log(entry.name)
            _lifecycle_log_cache[key] = _LifecycleLogCacheEntry(
                mtime_ns=stat.st_mtime_ns, size=stat.st_size, log_events=log_events
            )
        events = list(log_events)
        heartbeat = store.read_heartbeat(entry.name)
        if heartbeat is not None and (not events or heartbeat.ts >= events[-1].ts):
            events.append(heartbeat)
        if events:
            logs.append(events)
    for stale in [key for key in _lifecycle_log_cache if key not in seen]:
        _lifecycle_log_cache.pop(stale, None)
    return logs


def write_projection(root: Path, projection: WorkspaceProjection) -> None:
    """Atomically write ``latest-state.json`` + ``latest-metrics.json``."""
    root.mkdir(parents=True, exist_ok=True)
    _write_json(root / LATEST_STATE, projection.model_dump(by_alias=True, exclude_none=True))
    _write_json(
        root / LATEST_METRICS, projection.metrics.model_dump(by_alias=True, exclude_none=True)
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
            logger.warning(
                "provider current-state refresh failed; using last snapshot", exc_info=True
            )


class ProviderStateRefresh(Protocol):
    """Structural contract for projection pre-read provider refreshers."""

    def maybe_refresh(self, config: McpRuntimeConfig, *, now: datetime) -> None: ...


@dataclass(frozen=True)
class ProjectionTickState:
    """What a projector carries between ticks: the retained input snapshot it re-reads
    incrementally, and the two pre-read refreshers (provider current state, landing state) it
    drives before reading. All three are the projector's own long-lived collaborators; a tick
    given some of them and not others silently reverts to a cold read."""

    input_state: ProjectionInputState | None = None
    provider_refresher: ProviderStateRefresh | None = None
    landing_state: LandingStateReader | None = None


def project_and_write(
    config: McpRuntimeConfig,
    *,
    now: datetime | None = None,
    refresh: ProjectionRefresh | None = None,
    tick: ProjectionTickState | None = None,
) -> WorkspaceProjection:
    """Read logs + structural + analytical snapshots, reduce the tree, write it atomically."""
    tick = tick or ProjectionTickState()
    moment = now or datetime.now(UTC)
    root = observer_root(config)
    if tick.provider_refresher is not None:
        tick.provider_refresher.maybe_refresh(config, now=moment)
    state = tick.input_state or ProjectionInputState(contract_cache=_contract_snapshot_cache)
    inputs = state.read(
        config,
        ProjectionReaders(
            lifecycle=read_lifecycle_logs,
            repo_surfaces=_gather_repo_surfaces_cached,
            landing_state=tick.landing_state,
        ),
        observer_root=root,
        pass_=RefreshPass(now=moment, refresh=refresh or ProjectionRefresh.full()),
    )
    attention_store = AttentionDismissalStore(root)
    projection = project_workspace(
        inputs.lifecycle_logs,
        structure=WorkspaceStructure(
            enclosures=inputs.enclosures,
            providers=inputs.providers,
            # The Topology constellation filters its tree on this active set — the same
            # active-enclosure admission the Engine Room's engine_process_facts use, so both
            # views share one definition of "active" rather than diverging.
            active_worktree_groups=inputs.active_worktree_groups,
        ),
        now=moment,
        given=AnalyticalInputs(
            drift_snapshots=inputs.drift_snapshots,
            sidecar_staleness=inputs.sidecar_staleness,
            setup_summaries=inputs.setup_summaries,
            setup_progress=inputs.setup_progress,
            route_coverage=inputs.route_coverage,
            tool_reports=inputs.tool_reports,
            agent_pickups=inputs.agent_pickups,
            expectation_rows=inputs.expectation_rows,
            ledgers=inputs.ledgers,
            task_documents=inputs.task_documents,
            series=inputs.series,
            closeout_queues=inputs.closeout_queues,
            engine_process_facts=inputs.engine_process_facts,
            engine_start_progress=inputs.engine_start_progress,
            gates=inputs.gates,
            attention_dismissals=inputs.attention_dismissals,
        ),
    )
    attention_store.prune_lifecycles(
        {
            lifecycle.id
            for lifecycle in projection.lifecycles
            if lifecycle.state not in TERMINAL_STATES
        }
    )
    _warn_if_task_documents_payload_over_budget(projection, now=moment)
    write_projection(root, projection)
    return projection


def _warn_if_task_documents_payload_over_budget(
    projection: WorkspaceProjection, *, now: datetime
) -> int:
    """Flag (rate-limited) when the broadcast serialises a body-heavy task-doc payload.

    Returns the measured body-byte cost so callers/tests can assert on it. This is observability only --
    no truncation, no contract change -- surfacing the unbounded ``analytics.taskDocuments`` payload that
    a planned windowing follow-up will bound, so the growth is visible in logs instead of silent.
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


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    """Serialise, then hand the bytes to the package's one atomic publish.

    This used to be ``_atomic_write_json`` and owned its own temp-and-replace -- one of the
    two named copies of that primitive. What is left is the JSON rendering, which is all
    that was ever local to the projection store.
    """
    atomic_write_text(path, json.dumps(payload, indent=2) + "\n")
