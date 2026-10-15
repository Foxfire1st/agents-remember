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
from typing import TYPE_CHECKING, Any, TypeVar

from agents_remember.controlplane.attention_dismissals import (
    AttentionDismissalRecord,
    AttentionDismissalStore,
)
from agents_remember.observer.event_retention import (
    prune_expired_lifecycle_event_logs,
    prune_one_lifecycle_log,
)
from agents_remember.observer.events import Event
from agents_remember.observer.projection import (
    CloseoutQueueNode,
    DriftSnapshotNode,
    EnclosureNode,
    EngineProcessFacts,
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
from agents_remember.observer.store import EventStore
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
    read_drift_snapshots,
    read_enclosures,
    read_engine_process_facts,
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
    CloseoutQueueCache,
    refresh_closeout_queues,
)
from agents_remember.serving.projections.snapshots_impl._common import (
    _file_age_seconds,
    _read_json,
    _task_doc_cache,
)
from agents_remember.serving.projections.snapshots_impl._lifecycle_logs import (
    read_lifecycle_logs,
    read_one_lifecycle_log,
)
from agents_remember.serving.projections.snapshots_impl._task_document_closure import (
    doc_projection_closure,
)
from agents_remember.serving.projections.snapshots_impl._task_documents import (
    _master_docs_by_ref,
    _task_document_lifecycle_maps,
    _TaskDocProjectionOptions,
    project_series_nodes,
    project_task_doc_nodes,
)
from agents_remember.tasks import TASK_DOCUMENT_SCHEMA
from agents_remember.tasks.task_paths import (
    ARCHIVE_DIR,
    ENCLOSURES_DIR,
    SERIES_CONTRACT_FILENAME,
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
    # The watcher's accepted waking paths behind a change refresh, with whether the
    # sample is complete (F09): incremental readers may trust them only when complete
    # and non-empty; anything else re-reads the full affected domains. Full and
    # heartbeat refreshes carry no paths and always reconcile fully.
    paths: tuple[str, ...] = ()
    paths_complete: bool = False

    @classmethod
    def full(cls) -> ProjectionRefresh:
        return cls(ProjectionRefreshKind.FULL, ALL_PROJECTION_DOMAINS)

    @classmethod
    def change(
        cls,
        domains: frozenset[ProjectionDomain],
        paths: tuple[str, ...] = (),
        paths_complete: bool = False,
    ) -> ProjectionRefresh:
        return cls(ProjectionRefreshKind.CHANGE, domains, paths, paths_complete)

    @classmethod
    def heartbeat(cls) -> ProjectionRefresh:
        return cls(ProjectionRefreshKind.HEARTBEAT)

    @property
    def is_heartbeat(self) -> bool:
        return self.kind == ProjectionRefreshKind.HEARTBEAT

    def affects(self, domain: ProjectionDomain) -> bool:
        return self.kind == ProjectionRefreshKind.FULL or domain in self.domains


def incremental_paths(refresh: ProjectionRefresh) -> tuple[str, ...] | None:
    """The trusted waking paths for path-directed incremental readers (F09).

    Only a change refresh with a complete non-empty path sample qualifies: full and
    heartbeat refreshes reconcile fully, and an incomplete sample (more distinct
    paths than the wake carries, or drops at the pacer cap) falls back to the full
    domain re-read instead of certifying a partial one.
    """
    if refresh.kind != ProjectionRefreshKind.CHANGE:
        return None
    if not refresh.paths_complete or not refresh.paths:
        return None
    return refresh.paths


def _subtree_paths(root: Path, paths: tuple[str, ...]) -> list[Path]:
    """Wake paths below ``root``, in root-joined form, skipping outsiders (F09).

    Both the raw and the symlink-resolved form are tried once each; wake samples
    are small, so the extra resolve costs nothing, while retained merge keys keep
    the unresolved root-joined form the enumeration uses.
    """
    try:
        resolved_root = root.resolve()
    except OSError:
        resolved_root = root
    out: list[Path] = []
    for raw in paths:
        candidate = Path(raw)
        try:
            out.append(root / candidate.relative_to(root))
            continue
        except ValueError:
            pass
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        try:
            out.append(root / resolved.relative_to(resolved_root))
        except ValueError:
            continue
    return out


def _is_enclosure_contract(tasks_root: Path, path: Path) -> bool:
    """Whether ``path`` is a live leaf enclosure contract candidate (F09).

    Mirrors the enumerator's shape rule (``enclosures/<leaf>/series-contract.md``)
    without walking: the scoped contract build applies the same archive/notes
    exclusions per path.
    """
    try:
        relative = path.relative_to(tasks_root)
    except ValueError:
        return False
    return (
        len(relative.parts) == 3
        and relative.parts[0] == ENCLOSURES_DIR
        and relative.parts[2] == SERIES_CONTRACT_FILENAME
    )


def _is_canonical_task_doc(tasks_root: Path, path: Path) -> bool:
    """Whether ``path`` is a canonical task document the doc readers project (F09).

    Mirrors the enumerator's shape rule (``tasks/<repository>/<task>/*.json`` outside
    archive/enclosure folders) without walking: only named documents re-project.
    """
    if not path.name.endswith(".json"):
        return False
    try:
        relative = path.relative_to(tasks_root)
    except ValueError:
        return False
    return (
        len(relative.parts) == 3
        and ARCHIVE_DIR not in relative.parts
        and ENCLOSURES_DIR not in relative.parts
    )


def _merge_doc_nodes(
    retained: list[_DocNodeT],
    new: list[_DocNodeT],
    dropped: set[str],
    now: datetime,
) -> list[_DocNodeT]:
    """Merge re-projected document nodes into retained ones, in full-read order (F09).

    Fresh nodes replace retained ones by ``docPath`` and dropped paths leave; the
    merged list is sorted by ``docPath`` exactly like the enumerating full reference.
    Retained nodes keep their bytes except ``ageSeconds``, which is recomputed at the
    pass clock with one stat per retained document -- a stat is not a read, so the
    merge stays byte-equal to a full projection at the same clock while content
    reads stay scoped to the re-projected closure.
    """
    fresh = {node.docPath: node for node in new}
    retained_paths = {node.docPath for node in retained}
    merged = [fresh.get(node.docPath, node) for node in retained if node.docPath not in dropped]
    for doc_path, node in fresh.items():
        if doc_path not in retained_paths:
            merged.append(node)
    merged_by_path = {node.docPath: node for node in merged}
    merged = [merged_by_path[key] for key in sorted(merged_by_path)]
    return [
        node
        if node.docPath in fresh
        else node.model_copy(update={"ageSeconds": _file_age_seconds(Path(node.docPath), now)})
        for node in merged
    ]


_DocNodeT = TypeVar("_DocNodeT", TaskDocNode, SeriesNode)


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
    ledgers: list[LedgerNode]
    task_documents: list[TaskDocNode]
    series: list[SeriesNode]
    closeout_queues: list[CloseoutQueueNode]
    engine_process_facts: list[EngineProcessFacts]
    engine_start_progress: list[dict[str, Any]]
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

#: How long retained repo surfaces stay fresh without a full pass (F09): mirrors
#: ``REPO_SURFACE_REFRESH_TTL_SECONDS`` in ``projection_store`` (kept as a local
#: constant because that module imports this one). Memory edits stay visible within
#: the same bound as before; noop ticks inside it reach no surface reader.
_REPO_SURFACES_TTL_SECONDS = 120.0

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
        self._task_documents: list[TaskDocNode] = []
        self._series: list[SeriesNode] = []
        # lifecycle id -> retained events (F09): incremental lifecycle refresh merges
        # re-read ids into this map and rebuilds the sorted log list from it.
        self._lifecycle_logs_by_id: dict[str, list[Event]] = {}
        # Whether the id map is attributed (F09): only a production full read through
        # read_lifecycle_logs (sorted-id enumeration) can attribute rows to ids, so an
        # injected test reader leaves the map untrusted and incremental stays off.
        self._lifecycle_ids_trusted = False
        self._closeout_queues: list[CloseoutQueueNode] = []
        self._closeout_cache: CloseoutQueueCache | None = None
        self._engine_process_facts: list[EngineProcessFacts] = []
        self._engine_start_progress: list[dict[str, Any]] = []
        self._attention_dismissals: dict[str, AttentionDismissalRecord] = {}
        self._sidecar_staleness: list[SidecarStaleNode] = []
        self._route_coverage: list[RouteCoverageNode] = []
        self._ledgers: list[LedgerNode] = []
        self._surfaces_at: datetime | None = None
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
        retrying = self._task_refresh_pending
        if retrying:
            pass_ = replace(pass_, refresh=ProjectionRefresh.full(), tasks_changed=True)
        try:
            inputs = self._read_inputs(config, readers, observer_root=observer_root, pass_=pass_)
        except BaseException:
            self._task_refresh_pending = True
            raise
        self._task_refresh_pending = False
        return inputs

    def _read_inputs(
        self,
        config: McpRuntimeConfig,
        readers: ProjectionReaders,
        *,
        observer_root: Path,
        pass_: RefreshPass,
    ) -> ProjectionInputs:
        """Complete one pass; a failure leaves reconciliation pending in the owner."""
        now = pass_.now
        self._advance_ages(now)
        tasks_changed = self._refresh_tasks(config, pass_)
        pass_ = replace(pass_, tasks_changed=pass_.tasks_changed or tasks_changed)
        # The closeout queues read task documents, enclosure contracts, store states and live
        # worktree/branch states (MIK-R81 rule 3; F13): each sprint recomputes only when its
        # own inputs moved, so heartbeats and unrelated domains retain every queue byte for
        # byte -- invalid-empty responses included -- while a code/memory edit or a tip
        # advance recomputes exactly the sprints it touches.
        # MIK-R81 rule 2 (F09): heartbeats name no paths (the watcher saw nothing), so
        # the digest hashes the retained stat map with no walk and no stat at all; a
        # change sample scopes the digest and the re-list to its files, and anything
        # else walks and reconciles fully.
        closeout_paths = (
            ()
            if pass_.refresh.kind == ProjectionRefreshKind.HEARTBEAT
            else incremental_paths(pass_.refresh)
        )
        self._closeout_queues, self._closeout_cache = refresh_closeout_queues(
            config.coordination_root,
            now=pass_.now,
            cache=self._closeout_cache,
            paths=closeout_paths,
        )
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
        self._refresh_workspace(pass_, observer_root=observer_root)
        self._refresh_progress(config, pass_)

        # MIK-R81 rule 2 (F09): the repo surfaces (memory onboarding/code roots) have no
        # watch root, so no change tick can name them; they refresh on full passes and
        # when their TTL lapses, and every other pass retains them unread. A noop tick
        # therefore reaches no surface reader at all.
        if pass_.refresh.kind == ProjectionRefreshKind.FULL or self._surfaces_stale(now):
            sidecars, routes, ledgers = readers.repo_surfaces(config, now)
            self._sidecar_staleness = sidecars
            self._route_coverage = routes
            self._ledgers = ledgers
            self._surfaces_at = now
        else:
            sidecars, routes, ledgers = (
                self._sidecar_staleness,
                self._route_coverage,
                self._ledgers,
            )
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
            ledgers=ledgers,
            task_documents=self._task_documents,
            series=self._series,
            closeout_queues=self._closeout_queues,
            engine_process_facts=self._engine_process_facts,
            engine_start_progress=self._engine_start_progress,
            attention_dismissals=self._attention_dismissals,
            active_worktree_groups=sorted(engine_groups),
        )

    def _surfaces_stale(self, now: datetime) -> bool:
        """Whether the retained repo surfaces outlived their freshness bound."""
        if self._surfaces_at is None:
            return True
        return (now - self._surfaces_at).total_seconds() >= _REPO_SURFACES_TTL_SECONDS

    def _refresh_tasks(self, config: McpRuntimeConfig, pass_: RefreshPass) -> bool:
        if (
            not pass_.refresh.affects(ProjectionDomain.TASKS)
            and self._contracts is not None
            and not self._task_refresh_pending
        ):
            return False
        retrying = self._task_refresh_pending
        self._task_refresh_pending = True
        if retrying:
            self._refresh_tasks_full(config, pass_)
            return True
        paths = incremental_paths(pass_.refresh)
        if paths is not None and self._contracts is not None:
            return self._refresh_tasks_incremental(config, pass_, paths)
        return self._refresh_tasks_full(config, pass_)

    def _refresh_tasks_full(self, config: McpRuntimeConfig, pass_: RefreshPass) -> bool:
        contracts = self._contract_cache.build(config.coordination_root / "tasks")
        enclosures = read_enclosures(config.coordination_root, contracts=contracts)
        task_documents = read_task_documents(
            config.coordination_root, enclosures=enclosures, now=pass_.now
        )
        series = read_series_documents(config.coordination_root, now=pass_.now)
        # MIK-R81 rule 2 (F09): a TASKS invalidation re-reads the task tree, but the
        # downstream cascade (lifecycles, engine facts, drift) flips only when the
        # task STRUCTURE moved -- contracts or enclosures. Documentary changes (a
        # one-keystroke draft: new bodyRevision, same structure) update the retained
        # documents the projection reads directly, and reach closeout through its own
        # per-sprint gate, without re-deriving every downstream domain for them.
        # (Payload reads stay bounded either way: the shared payload cache re-parses
        # only stat-changed files.)
        structural = (
            self._contracts is None
            or contracts.contracts != self._contracts.contracts
            or contracts.skipped != self._contracts.skipped
            or enclosures != self._enclosures
        )
        self._contracts = contracts
        self._enclosures = enclosures
        self._task_documents = task_documents
        self._series = series
        return structural

    def _refresh_tasks_incremental(
        self, config: McpRuntimeConfig, pass_: RefreshPass, paths: tuple[str, ...]
    ) -> bool:
        """Re-read only the task inputs a complete wake path sample named (F09).

        Contracts re-parse only for named contract paths, documents re-project only
        for named document paths plus their dependency closure, and everything else
        merges from retained state with volatile fields refreshed at the pass clock.
        Structural moves still flip the downstream cascade exactly like the full
        refresh; a sample naming no document or contract input changes nothing.
        """
        tasks_root = config.coordination_root / "tasks"
        named = _subtree_paths(tasks_root, paths)
        contract_paths = [path for path in named if _is_enclosure_contract(tasks_root, path)]
        doc_paths = [path for path in named if _is_canonical_task_doc(tasks_root, path)]
        if not contract_paths and not doc_paths:
            return False
        contracts = self._contract_cache.build(tasks_root, only=contract_paths)
        enclosures = read_enclosures(config.coordination_root, contracts=contracts)
        assert self._contracts is not None
        structural = (
            contracts.contracts != self._contracts.contracts
            or contracts.skipped != self._contracts.skipped
            or enclosures != self._enclosures
        )
        self._contracts = contracts
        self._enclosures = enclosures
        if doc_paths or structural:
            affected = set(doc_paths)
            if structural:
                affected.update(path for path, _ in _task_doc_cache.cached(tasks_root))
            self._reproject_task_documents(config, tasks_root, pass_, affected)
        return structural

    def _reproject_task_documents(
        self,
        config: McpRuntimeConfig,
        tasks_root: Path,
        pass_: RefreshPass,
        affected: set[Path],
    ) -> None:
        """Re-project the affected documents plus closure, merging retained nodes (F09).

        Payload reads touch only affected files (the stat-gated cache serves the
        rest from memory for dependency closure); deleted or unreadable documents
        drop their nodes exactly like the full reference, which omits them too.
        """
        fresh = _task_doc_cache.payloads(
            tasks_root, sorted(affected), read_payload=_read_json, prune=False
        )
        fresh_paths = {path for path, _ in fresh}
        dropped = {path for path in affected if path not in fresh_paths}
        retained = _task_doc_cache.cached(tasks_root)
        by_path = dict(retained)
        for path, payload in fresh:
            by_path[path] = payload
        for path in dropped:
            by_path.pop(path, None)
        all_docs = [
            (path, payload)
            for path, payload in by_path.items()
            if payload.get("schema") == TASK_DOCUMENT_SCHEMA
        ]
        closure = doc_projection_closure(all_docs, affected, tasks_root)
        closure_docs = [item for item in all_docs if item[0] in closure]
        lifecycle_maps = _task_document_lifecycle_maps(self._enclosures)
        options = _TaskDocProjectionOptions(
            include_body=False,
            master_docs=_master_docs_by_ref(closure_docs),
            coordination_root=config.coordination_root,
        )
        new_doc_nodes = project_task_doc_nodes(
            closure_docs, lifecycle_maps=lifecycle_maps, now=pass_.now, options=options
        )
        closure_folders = {path.parent for path in closure}
        series_docs = [
            item for item in all_docs if item[0] in closure or item[0].parent in closure_folders
        ]
        new_series_nodes = project_series_nodes(
            series_docs, coordination_root=config.coordination_root, now=pass_.now
        )
        dropped_keys = {path.as_posix() for path in dropped}
        self._task_documents = _merge_doc_nodes(
            self._task_documents, new_doc_nodes, dropped_keys, pass_.now
        )
        self._series = _merge_doc_nodes(self._series, new_series_nodes, dropped_keys, pass_.now)

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
        paths = incremental_paths(pass_.refresh)
        if paths is not None and not pass_.tasks_changed and self._lifecycle_ids_trusted:
            self._refresh_lifecycles_incremental(observer_root, pass_, paths, lifecycle_reader)
            return
        prune_expired_lifecycle_event_logs(
            observer_root,
            now=pass_.now,
            protected_lifecycle_ids=series_retained_lifecycle_ids(self._enclosures, now=pass_.now),
            note_own=True,
        )
        # Our own prune never wakes our own projector (MIK-R81 rule 3): the
        # delete events of directories this tick reclaimed are our echo. The
        # prune returns the reclaimed events.jsonl paths; the directory is noted
        # so its heartbeat and gates sidecars read as our echo too.
        self._read_lifecycles_full(lifecycle_reader, observer_root)

    def _read_lifecycles_full(self, lifecycle_reader: LifecycleReader, observer_root: Path) -> None:
        """Full lifecycle re-read with the retained id map rebuilt when possible (F09)."""
        if lifecycle_reader is read_lifecycle_logs:
            # The production reader enumerates sorted ids: attribute its rows by id
            # in the same call, so the next incremental merge has a trusted base.
            # Injected test readers keep the legacy unattributed contract, and the
            # map stays untrusted until a production full read rebuilds it.
            by_id: dict[str, list[Event]] = {}
            self._lifecycle_logs = read_lifecycle_logs(observer_root, by_id=by_id)
            self._lifecycle_logs_by_id = by_id
            self._lifecycle_ids_trusted = True
        else:
            self._lifecycle_logs = lifecycle_reader(observer_root)
            self._lifecycle_logs_by_id = {}
            self._lifecycle_ids_trusted = False

    def _refresh_lifecycles_incremental(
        self,
        observer_root: Path,
        pass_: RefreshPass,
        paths: tuple[str, ...],
        lifecycle_reader: LifecycleReader,
    ) -> None:
        """Re-read only the lifecycles a complete wake path sample named (F09).

        Prune and log reads touch only named lifecycle ids and merge into the
        retained id map; expiry of unnamed lifecycles waits for the next
        heartbeat/full reconciliation. A sample naming no lifecycle id falls back
        to the full read rather than certifying a partial one.
        """
        named = _subtree_paths(observer_root, paths)
        ids = {
            path.relative_to(observer_root).parts[1]
            for path in named
            if len(path.relative_to(observer_root).parts) >= 2
            and path.relative_to(observer_root).parts[0] == "lifecycles"
        }
        if not ids:
            self._read_lifecycles_full(lifecycle_reader, observer_root)
            return
        protected = series_retained_lifecycle_ids(self._enclosures, now=pass_.now)
        lifecycles_dir = observer_root / "lifecycles"
        for lifecycle_id in sorted(ids):
            pruned = prune_one_lifecycle_log(
                lifecycles_dir,
                lifecycle_id,
                now=pass_.now,
                protected_lifecycle_ids=protected,
                note_own=True,
            )
            if pruned is not None:
                self._lifecycle_logs_by_id.pop(lifecycle_id, None)
        store = EventStore(observer_root)
        for lifecycle_id in sorted(ids):
            if (
                lifecycle_id in self._lifecycle_logs_by_id
                and not (lifecycles_dir / lifecycle_id).is_dir()
            ):
                self._lifecycle_logs_by_id.pop(lifecycle_id, None)
                continue
            events = read_one_lifecycle_log(store, lifecycles_dir, lifecycle_id)
            if events is None:
                self._lifecycle_logs_by_id.pop(lifecycle_id, None)
            elif events:
                self._lifecycle_logs_by_id[lifecycle_id] = events
            # An empty log contributes no row, exactly like the full reference.
            elif lifecycle_id in self._lifecycle_logs_by_id:
                self._lifecycle_logs_by_id.pop(lifecycle_id, None)
        self._lifecycle_logs = [
            self._lifecycle_logs_by_id[lifecycle_id]
            for lifecycle_id in sorted(self._lifecycle_logs_by_id)
        ]

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
        prune_orphaned_drift_snapshots(config, contracts=self._contracts, note_own=True)
        self._drift_snapshots = read_drift_snapshots(config.coordination_root, now=pass_.now)

    def _refresh_workspace(self, pass_: RefreshPass, *, observer_root: Path) -> None:
        if not (pass_.refresh.is_heartbeat or pass_.refresh.affects(ProjectionDomain.WORKSPACE)):
            return
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
