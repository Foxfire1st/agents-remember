"""Domain-invalidated inputs for the shared workspace projection.

The filesystem watcher names which projection domain changed. This module retains one
bounded snapshot of each reader domain so a lifecycle write does not re-enumerate task
documents, drift snapshots, provider setup, and Engine Room facts. Each domain is replaced
as a whole when invalidated; deletion therefore reclaims retained rows on that same refresh.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agents_remember.controlplane.attention_dismissals import (
    AttentionDismissalRecord,
    AttentionDismissalStore,
)
from agents_remember.controlplane.records import GateRecord
from agents_remember.observer.event_retention import prune_expired_lifecycle_event_logs
from agents_remember.observer.events import Event
from agents_remember.observer.projection import (
    AgentPickupNode,
    CloseoutQueueNode,
    DriftSnapshotNode,
    EnclosureNode,
    EngineProcessFacts,
    ExpectationRowNode,
    LedgerNode,
    ProviderNode,
    RouteCoverageNode,
    SeriesNode,
    SetupProgressNode,
    SetupSummaryNode,
    SidecarStaleNode,
    TaskDocNode,
    ToolReportNode,
)
from agents_remember.observer.worktree_provider_admission import (
    active_enclosure_worktree_groups,
    admitted_worktree_groups,
    series_retained_lifecycle_ids,
)
from agents_remember.serving.projections.contract_snapshot import (
    ContractSnapshot,
    ContractSnapshotCache,
)
from agents_remember.serving.projections.drift_snapshots import prune_orphaned_drift_snapshots
from agents_remember.serving.projections.snapshots import (
    read_agent_pickups,
    read_drift_snapshots,
    read_enclosures,
    read_engine_process_facts,
    read_expectation_rows,
    read_gates,
    read_providers,
    read_series_documents,
    read_setup_progress_nodes,
    read_setup_summaries,
    read_start_progress_entries,
    read_task_documents,
    read_tool_reports,
    refresh_engine_process_landing,
)
from agents_remember.serving.projections.snapshots_impl._closeout_queue import (
    read_closeout_queues,
)
from pydantic import BaseModel

if TYPE_CHECKING:
    from agents_remember.kernel.primitives.runtime_config import (
        McpRuntimeConfig,
    )
    from agents_remember.serving.projections.landing_state import LandingStateReader


class ProjectionDomain(StrEnum):
    """Reader domains carried by watcher invalidations."""

    TASKS = "tasks"
    LIFECYCLES = "lifecycles"
    WORKSPACE = "workspace"
    DRIFT = "drift"
    PROVIDERS = "providers"
    START_PROGRESS = "start-progress"
    TOOL_REPORTS = "tool-reports"


ALL_PROJECTION_DOMAINS = frozenset(ProjectionDomain)


class ProjectionRefreshKind(StrEnum):
    FULL = "full"
    CHANGE = "change"
    HEARTBEAT = "heartbeat"


@dataclass(frozen=True)
class ProjectionRefresh:
    """Why one projection input snapshot is being refreshed."""

    kind: ProjectionRefreshKind
    domains: frozenset[ProjectionDomain] = frozenset()

    @classmethod
    def full(cls) -> ProjectionRefresh:
        return cls(ProjectionRefreshKind.FULL, ALL_PROJECTION_DOMAINS)

    @classmethod
    def change(cls, domains: frozenset[ProjectionDomain]) -> ProjectionRefresh:
        return cls(ProjectionRefreshKind.CHANGE, domains)

    @classmethod
    def heartbeat(cls) -> ProjectionRefresh:
        return cls(ProjectionRefreshKind.HEARTBEAT)

    @property
    def is_heartbeat(self) -> bool:
        return self.kind == ProjectionRefreshKind.HEARTBEAT

    def affects(self, domain: ProjectionDomain) -> bool:
        return self.kind == ProjectionRefreshKind.FULL or domain in self.domains


@dataclass(frozen=True)
class ProjectionInputs:
    lifecycle_logs: list[list[Event]]
    enclosures: list[EnclosureNode]
    providers: list[ProviderNode]
    drift_snapshots: list[DriftSnapshotNode]
    sidecar_staleness: list[SidecarStaleNode]
    setup_summaries: list[SetupSummaryNode]
    setup_progress: list[SetupProgressNode]
    route_coverage: list[RouteCoverageNode]
    tool_reports: list[ToolReportNode]
    agent_pickups: list[AgentPickupNode]
    expectation_rows: list[ExpectationRowNode]
    ledgers: list[LedgerNode]
    task_documents: list[TaskDocNode]
    series: list[SeriesNode]
    closeout_queues: list[CloseoutQueueNode]
    engine_process_facts: list[EngineProcessFacts]
    engine_start_progress: list[dict[str, Any]]
    gates: list[GateRecord]
    attention_dismissals: dict[str, AttentionDismissalRecord]
    active_worktree_groups: list[str]


@dataclass(frozen=True)
class RefreshPass:
    """One pass of the input reader: the clock every snapshot is aged against, which domains
    the pass was asked to refresh, and whether the task tree already changed under it.

    Every domain refresher decides from these three together -- ``tasks_changed`` alone flips
    lifecycles, engine facts and drift regardless of what the invalidation set said -- so the
    pass is the frame they all run in, not three parameters each of them re-receives.
    """

    now: datetime
    refresh: ProjectionRefresh
    tasks_changed: bool = False


@dataclass(frozen=True)
class ActiveGroups:
    """A worktree-group admission set for one pass, and whether it moved since the last one.

    A refresher needs both: the set says what to read, the moved flag says whether to read at
    all. Reading one without the other either re-reads every tick or never re-reads.
    """

    groups: set[str]
    changed: bool


LifecycleReader = Callable[[Path], list[list[Event]]]
RepoSurfaceReader = Callable[
    ["McpRuntimeConfig", datetime],
    tuple[list[SidecarStaleNode], list[RouteCoverageNode], list[LedgerNode]],
]


@dataclass(frozen=True)
class ProjectionReaders:
    """The three readers a projection pass reaches the outside world through: the lifecycle
    event logs, the cached repo-surface bundle, and the optional landing-state probe. Injected
    together as the pass's I/O seam so a test can replace the set coherently."""

    lifecycle: LifecycleReader
    repo_surfaces: RepoSurfaceReader
    landing_state: LandingStateReader | None = None


class ProjectionInputState:
    """One fixed-slot retained input snapshot with explicit domain invalidation."""

    def __init__(self, *, contract_cache: ContractSnapshotCache | None = None) -> None:
        self._contract_cache = contract_cache or ContractSnapshotCache()
        self._contracts: ContractSnapshot | None = None
        self._task_refresh_pending = False
        self._enclosures: list[EnclosureNode] = []
        self._lifecycle_logs: list[list[Event]] = []
        self._providers: list[ProviderNode] = []
        self._drift_snapshots: list[DriftSnapshotNode] = []
        self._setup_summaries: list[SetupSummaryNode] = []
        self._setup_progress: list[SetupProgressNode] = []
        self._tool_reports: list[ToolReportNode] = []
        self._agent_pickups: list[AgentPickupNode] = []
        self._expectation_rows: list[ExpectationRowNode] = []
        self._task_documents: list[TaskDocNode] = []
        self._series: list[SeriesNode] = []
        self._closeout_queues: list[CloseoutQueueNode] = []
        self._engine_process_facts: list[EngineProcessFacts] = []
        self._engine_start_progress: list[dict[str, Any]] = []
        self._gates: list[GateRecord] = []
        self._attention_dismissals: dict[str, AttentionDismissalRecord] = {}
        self._provider_groups: set[str] = set()
        self._engine_groups: set[str] = set()
        self._ages_at: datetime | None = None

    def read(
        self,
        config: McpRuntimeConfig,
        readers: ProjectionReaders,
        *,
        observer_root: Path,
        pass_: RefreshPass,
    ) -> ProjectionInputs:
        """Refresh invalidated domains, then return the complete current input snapshot."""
        now = pass_.now
        self._advance_ages(now)
        pass_ = replace(pass_, tasks_changed=self._refresh_tasks(config, pass_))
        self._closeout_queues = read_closeout_queues(config.coordination_root, now=pass_.now)
        self._refresh_lifecycles(observer_root, pass_, lifecycle_reader=readers.lifecycle)
        provider_groups = admitted_worktree_groups(self._enclosures, self._lifecycle_logs, now=now)
        engine_groups = active_enclosure_worktree_groups(
            self._enclosures, self._lifecycle_logs, now=now
        )
        providers = ActiveGroups(provider_groups, provider_groups != self._provider_groups)
        engines = ActiveGroups(engine_groups, engine_groups != self._engine_groups)
        self._provider_groups = provider_groups
        self._engine_groups = engine_groups

        self._refresh_providers(config, pass_, providers)
        self._refresh_engine_facts(config, pass_, engines, landing_state=readers.landing_state)
        self._refresh_drift(config, pass_)
        self._refresh_workspace(config, pass_, observer_root=observer_root)
        self._refresh_progress(config, pass_)

        sidecars, routes, ledgers = readers.repo_surfaces(config, now)
        self._ages_at = now
        return ProjectionInputs(
            lifecycle_logs=self._lifecycle_logs,
            enclosures=self._enclosures,
            providers=self._providers,
            drift_snapshots=self._drift_snapshots,
            sidecar_staleness=sidecars,
            setup_summaries=self._setup_summaries,
            setup_progress=self._setup_progress,
            route_coverage=routes,
            tool_reports=self._tool_reports,
            agent_pickups=self._agent_pickups,
            expectation_rows=self._expectation_rows,
            ledgers=ledgers,
            task_documents=self._task_documents,
            series=self._series,
            closeout_queues=self._closeout_queues,
            engine_process_facts=self._engine_process_facts,
            engine_start_progress=self._engine_start_progress,
            gates=self._gates,
            attention_dismissals=self._attention_dismissals,
            active_worktree_groups=sorted(engine_groups),
        )

    def _refresh_tasks(self, config: McpRuntimeConfig, pass_: RefreshPass) -> bool:
        if (
            not pass_.refresh.affects(ProjectionDomain.TASKS)
            and self._contracts is not None
            and not self._task_refresh_pending
        ):
            return False
        self._task_refresh_pending = True
        contracts = self._contract_cache.build(config.coordination_root / "tasks")
        enclosures = read_enclosures(config.coordination_root, contracts=contracts)
        task_documents = read_task_documents(
            config.coordination_root, enclosures=enclosures, now=pass_.now
        )
        series = read_series_documents(config.coordination_root, now=pass_.now)
        self._contracts = contracts
        self._enclosures = enclosures
        self._task_documents = task_documents
        self._series = series
        self._task_refresh_pending = False
        return True

    def _refresh_lifecycles(
        self,
        observer_root: Path,
        pass_: RefreshPass,
        *,
        lifecycle_reader: LifecycleReader,
    ) -> None:
        if not (
            pass_.refresh.is_heartbeat
            or pass_.tasks_changed
            or pass_.refresh.affects(ProjectionDomain.LIFECYCLES)
        ):
            return
        prune_expired_lifecycle_event_logs(
            observer_root,
            now=pass_.now,
            protected_lifecycle_ids=series_retained_lifecycle_ids(self._enclosures, now=pass_.now),
        )
        self._lifecycle_logs = lifecycle_reader(observer_root)

    def _refresh_providers(
        self,
        config: McpRuntimeConfig,
        pass_: RefreshPass,
        groups: ActiveGroups,
    ) -> None:
        now = pass_.now
        providers_changed = pass_.refresh.is_heartbeat or pass_.refresh.affects(
            ProjectionDomain.PROVIDERS
        )
        if providers_changed:
            self._setup_summaries = read_setup_summaries(config.coordination_root, now=now)
        if not providers_changed and not groups.changed:
            return
        self._providers = read_providers(config, now=now, active_worktree_groups=groups.groups)
        self._setup_progress = read_setup_progress_nodes(
            config.coordination_root,
            now=now,
            active_worktree_groups=groups.groups,
        )

    def _refresh_engine_facts(
        self,
        config: McpRuntimeConfig,
        pass_: RefreshPass,
        groups: ActiveGroups,
        *,
        landing_state: LandingStateReader | None,
    ) -> None:
        if not (pass_.refresh.is_heartbeat or pass_.tasks_changed or groups.changed):
            return
        assert self._contracts is not None
        if pass_.tasks_changed or groups.changed:
            self._engine_process_facts = read_engine_process_facts(
                config.coordination_root,
                active_worktree_groups=groups.groups,
                now=pass_.now,
                landing_state=landing_state,
                contracts=self._contracts,
            )
        else:
            self._engine_process_facts = refresh_engine_process_landing(
                self._engine_process_facts,
                now=pass_.now,
                landing_state=landing_state,
                contracts=self._contracts,
            )

    def _refresh_drift(self, config: McpRuntimeConfig, pass_: RefreshPass) -> None:
        if not pass_.tasks_changed and not pass_.refresh.affects(ProjectionDomain.DRIFT):
            return
        assert self._contracts is not None
        prune_orphaned_drift_snapshots(config, contracts=self._contracts)
        self._drift_snapshots = read_drift_snapshots(config.coordination_root, now=pass_.now)

    def _refresh_workspace(
        self,
        config: McpRuntimeConfig,
        pass_: RefreshPass,
        *,
        observer_root: Path,
    ) -> None:
        if not (
            pass_.refresh.is_heartbeat
            or pass_.refresh.affects(ProjectionDomain.WORKSPACE)
            or pass_.refresh.affects(ProjectionDomain.LIFECYCLES)
        ):
            return
        now = pass_.now
        self._gates = read_gates(config.coordination_root, now=now)
        self._agent_pickups = read_agent_pickups(config.coordination_root, now=now)
        self._expectation_rows = read_expectation_rows(config.coordination_root, now=now)
        self._attention_dismissals = AttentionDismissalStore(observer_root).current()

    def _refresh_progress(self, config: McpRuntimeConfig, pass_: RefreshPass) -> None:
        if pass_.refresh.affects(ProjectionDomain.START_PROGRESS):
            self._engine_start_progress = read_start_progress_entries(
                config.coordination_root, now=pass_.now
            )
        if pass_.refresh.affects(ProjectionDomain.TOOL_REPORTS):
            self._tool_reports = read_tool_reports(config.coordination_root, now=pass_.now)

    def _advance_ages(self, now: datetime) -> None:
        if self._ages_at is None:
            return
        elapsed = max(0.0, (now - self._ages_at).total_seconds())
        if elapsed == 0:
            return
        self._providers = _advance_model_age(self._providers, "snapshotStaleSeconds", elapsed)
        self._drift_snapshots = _advance_model_age(
            self._drift_snapshots, "snapshotStaleSeconds", elapsed
        )
        self._setup_summaries = _advance_model_age(
            self._setup_summaries, "snapshotStaleSeconds", elapsed
        )
        self._setup_progress = _advance_model_age(
            self._setup_progress, "heartbeatAgeSeconds", elapsed
        )
        self._tool_reports = _advance_model_age(self._tool_reports, "ageSeconds", elapsed)
        self._agent_pickups = _advance_model_age(self._agent_pickups, "ageSeconds", elapsed)
        self._task_documents = _advance_model_age(self._task_documents, "ageSeconds", elapsed)
        self._series = _advance_model_age(self._series, "ageSeconds", elapsed)
        self._engine_start_progress = [
            {
                **entry,
                "ageSeconds": float(entry["ageSeconds"]) + elapsed,
            }
            if isinstance(entry.get("ageSeconds"), (float, int))
            else entry
            for entry in self._engine_start_progress
        ]


def _advance_model_age[NodeT: BaseModel](
    nodes: list[NodeT], field: str, elapsed: float
) -> list[NodeT]:
    advanced: list[NodeT] = []
    for node in nodes:
        value = getattr(node, field)
        advanced.append(
            node.model_copy(update={field: value + elapsed}) if value is not None else node
        )
    return advanced
