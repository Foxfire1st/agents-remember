"""Domain-invalidated inputs for the shared workspace projection.

The filesystem watcher names which projection domain changed. This module retains one
bounded snapshot of each reader domain so a host activity write does not re-enumerate task
documents, drift snapshots, and Engine Room facts. Each domain is replaced
as a whole when invalidated; deletion therefore reclaims retained rows on that same refresh.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agents_remember.controlplane.attention_dismissals import (
    AttentionDismissalRecord,
    AttentionDismissalStore,
    AttentionObservation,
)
from agents_remember.observer.native_attention import (
    NativeAttentionFacts,
    observe_terminal_notices,
)
from agents_remember.observer.projection import (
    CloseoutQueueNode,
    DriftSnapshotNode,
    EnclosureNode,
    EngineProcessFacts,
    LedgerNode,
    RouteCoverageNode,
    ToolReportNode,
)
from agents_remember.observer.reducer import enclosure_actions
from agents_remember.observer.reducer_impl._attention_native import (
    native_attention_items,
    stall_occurrences,
)
from agents_remember.observer.task_entries import (
    TaskDocumentSource,
    TaskEntries,
    build_task_entries,
)
from agents_remember.observer.worktree_provider_admission import (
    active_enclosure_worktree_groups,
)
from agents_remember.serving.projections.contract_snapshot import (
    ContractSnapshot,
    ContractSnapshotCache,
)
from agents_remember.serving.projections.drift_snapshots import prune_orphaned_drift_snapshots
from agents_remember.serving.projections.host_activity import (
    HostActivityInput,
    HostActivityReconcile,
    HostActivitySnapshot,
)
from agents_remember.serving.projections.native_attention import NativeAttentionInput
from agents_remember.serving.projections.snapshots import (
    read_drift_snapshots,
    read_enclosures,
    read_engine_process_facts,
    read_start_progress_entries,
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
from agents_remember.serving.projections.snapshots_impl._task_document_closure import (
    doc_projection_closure,
)
from agents_remember.serving.projections.snapshots_impl._task_documents import (
    project_task_sources,
    read_task_sources,
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
    WORKSPACE = "workspace"
    DRIFT = "drift"
    START_PROGRESS = "start-progress"
    TOOL_REPORTS = "tool-reports"
    HOST_ACTIVITY = "host-activity"
    # L190-R1-F10/F11: the two native stores the attention queue reads beside the host
    # publication -- one small activation record per series contract plus the terminal notice
    # store, and the held-message queue the delivery machinery names its holders in. Both have
    # real watcher roots (see ``change_watcher.projection_input_roots``), and their values are
    # exactly the invalidation tokens the native reader gates its file reads on.
    ATTENTION_CONTROLPLANE = "attention-controlplane"
    ATTENTION_HELD = "attention-held"


logger = logging.getLogger(__name__)


ALL_PROJECTION_DOMAINS = frozenset(ProjectionDomain)
HEALING_PROJECTION_DOMAINS = ALL_PROJECTION_DOMAINS - {ProjectionDomain.HOST_ACTIVITY}

#: The native reader's file-backed domains (L190-R1-F10/F11): the only two whose stores a pass
#: may re-read. A pass hands them to the reader as its invalidation set, so an age-only
#: heartbeat -- which names none of them -- reaches no store at all.
NATIVE_ATTENTION_DOMAINS = frozenset(
    {ProjectionDomain.ATTENTION_CONTROLPLANE, ProjectionDomain.ATTENTION_HELD}
)


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
        return cls(ProjectionRefreshKind.FULL, HEALING_PROJECTION_DOMAINS)

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
        return domain in self.domains


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


@dataclass(frozen=True)
class ProjectionInputs:
    enclosures: list[EnclosureNode]
    drift_snapshots: list[DriftSnapshotNode]
    route_coverage: list[RouteCoverageNode]
    tool_reports: list[ToolReportNode]
    ledgers: list[LedgerNode]
    task_sources: list[TaskDocumentSource]
    closeout_queues: list[CloseoutQueueNode]
    engine_process_facts: list[EngineProcessFacts]
    engine_start_progress: list[dict[str, Any]]
    attention_dismissals: dict[str, AttentionDismissalRecord]
    active_worktree_groups: list[str]
    host_activity: HostActivitySnapshot
    native_attention: NativeAttentionFacts
    # L190-R1-F04: the canonical task list this pass already built, so the stall clock and the
    # stall rows read one relation and the join is done once per pass rather than twice.
    task_entries: TaskEntries | None = None


@dataclass(frozen=True)
class RefreshPass:
    """One pass of the input reader: the clock every snapshot is aged against, which domains
    the pass was asked to refresh, and whether the task tree already changed under it.

    Every domain refresher decides from these three together -- ``tasks_changed`` alone flips
    task entries, engine facts and drift regardless of what the invalidation set said -- so the
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


#: How long retained repo surfaces stay fresh without a full pass (F09): mirrors
#: ``REPO_SURFACE_REFRESH_TTL_SECONDS`` in ``projection_store`` (kept as a local
#: constant because that module imports this one). Memory edits stay visible within
#: the same bound as before; noop ticks inside it reach no surface reader.
_REPO_SURFACES_TTL_SECONDS = 120.0

RepoSurfaceReader = Callable[
    ["McpRuntimeConfig", datetime],
    tuple[list[RouteCoverageNode], list[LedgerNode]],
]


@dataclass(frozen=True)
class ProjectionReaders:
    """The repo-surface bundle and optional native landing probe for one input pass."""

    repo_surfaces: RepoSurfaceReader
    landing_state: LandingStateReader | None = None


class ProjectionInputState:
    """One fixed-slot retained input snapshot with explicit domain invalidation."""

    def __init__(
        self,
        *,
        contract_cache: ContractSnapshotCache | None = None,
        host_activity_reconcile: HostActivityReconcile | None = None,
        host_supervisor_armed: Callable[[], bool] | None = None,
    ) -> None:
        self._contract_cache = contract_cache or ContractSnapshotCache()
        self._contracts: ContractSnapshot | None = None
        self._task_refresh_pending = False
        self._enclosures: list[EnclosureNode] = []
        self._drift_snapshots: list[DriftSnapshotNode] = []
        self._tool_reports: list[ToolReportNode] = []
        self._task_sources: list[TaskDocumentSource] = []
        self._closeout_queues: list[CloseoutQueueNode] = []
        self._closeout_cache: CloseoutQueueCache | None = None
        self._engine_process_facts: list[EngineProcessFacts] = []
        self._engine_start_progress: list[dict[str, Any]] = []
        self._attention_dismissals: dict[str, AttentionDismissalRecord] = {}
        self._route_coverage: list[RouteCoverageNode] = []
        self._ledgers: list[LedgerNode] = []
        self._surfaces_at: datetime | None = None
        self._engine_groups: set[str] = set()
        self._ages_at: datetime | None = None
        self._host_activity_reader = HostActivityInput(
            host_activity_reconcile, host_supervisor_armed
        )
        self._host_activity = HostActivitySnapshot()
        self._host_activity_started = False
        self._native_attention_reader = NativeAttentionInput()
        self._native_attention = NativeAttentionFacts()
        self._native_attention_started = False
        self._task_entries: TaskEntries | None = None

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
            pass_ = replace(
                pass_,
                refresh=replace(
                    ProjectionRefresh.full(),
                    domains=HEALING_PROJECTION_DOMAINS | pass_.refresh.domains,
                ),
                tasks_changed=True,
            )
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
        if not self._host_activity_started or pass_.refresh.affects(ProjectionDomain.HOST_ACTIVITY):
            self._host_activity = self._host_activity_reader.read(config)
            self._host_activity_started = True
        engine_groups = active_enclosure_worktree_groups(self._enclosures)
        engines = ActiveGroups(engine_groups, engine_groups != self._engine_groups)
        self._engine_groups = engine_groups

        observations: list[AttentionObservation] = []
        # MIK-R200 rule 9: the five native facts are re-derived from retained inputs and the
        # files their owners publish into -- no host poll, no second timer, no store sweep.
        # L190-R1-F11: the pass names the native domains that actually moved, so the reader
        # re-parses a store only on its owning invalidation; an age-only heartbeat names none
        # and every retained fact (with the deadline evaluation derived from it) stands.
        entries = build_task_entries(
            self._task_sources,
            [
                enclosure.model_copy(update={"actions": enclosure_actions(enclosure)})
                for enclosure in self._enclosures
            ],
            self._host_activity,
        )
        self._task_entries = entries
        self._native_attention = self._native_attention_reader.read(
            config,
            host=self._host_activity,
            task_entries=entries,
            now=pass_.now,
            refreshed=self._native_refreshed(pass_.refresh),
        )
        self._native_attention_started = True
        # MIK-R200@v2 rule 7: a live cause's acknowledgement is reclaimed by the observation
        # that its occurrence ended -- the same pass that publishes or withholds the row.
        self._observe_native_kinds(entries, pass_.now, observations)
        self._refresh_engine_facts(config, pass_, engines, landing_state=readers.landing_state)
        self._refresh_drift(config, pass_, observations)
        # MIK-R200 rule 7: the two notice kinds own their acknowledgements through this same
        # observation list -- kept while their unchanged occurrence is retained, reclaimed when
        # the bounded store drops it. An unreadable notice store supplies no observation, so it
        # can end no acknowledgement.
        if self._native_attention.notices_complete:
            notice_observations = {
                kind: AttentionObservation(kind, pass_.now)
                for kind in ("task-landed", "task-abandoned")
            }
            for kind, observation in notice_observations.items():
                observe_terminal_notices(
                    tuple(
                        notice for notice in self._native_attention.notices if notice.kind == kind
                    ),
                    {kind: observation},
                )
            observations.extend(notice_observations.values())
        self._refresh_progress(config, pass_, observations)
        self._refresh_workspace(pass_, observer_root=observer_root, observations=observations)

        # MIK-R81 rule 2 (F09): the repo surfaces (memory onboarding/code roots) have no
        # watch root, so no change tick can name them; they refresh on full passes and
        # when their TTL lapses, and every other pass retains them unread. A noop tick
        # therefore reaches no surface reader at all.
        if pass_.refresh.kind == ProjectionRefreshKind.FULL or self._surfaces_stale(now):
            routes, ledgers = readers.repo_surfaces(config, now)
            self._route_coverage = routes
            self._ledgers = ledgers
            self._surfaces_at = now
        else:
            routes, ledgers = (
                self._route_coverage,
                self._ledgers,
            )
        self._ages_at = now
        return ProjectionInputs(
            enclosures=self._enclosures,
            drift_snapshots=self._drift_snapshots,
            route_coverage=routes,
            tool_reports=self._tool_reports,
            ledgers=ledgers,
            task_sources=self._task_sources,
            closeout_queues=self._closeout_queues,
            engine_process_facts=self._engine_process_facts,
            engine_start_progress=self._engine_start_progress,
            attention_dismissals=self._attention_dismissals,
            active_worktree_groups=sorted(engine_groups),
            host_activity=self._host_activity,
            native_attention=self._native_attention,
            task_entries=self._task_entries,
        )

    def _native_refreshed(self, refresh: ProjectionRefresh) -> frozenset[ProjectionDomain] | None:
        """The native domains whose stores this pass may re-read (L190-R1-F10/F11).

        ``None`` means "everything": the reader has not run yet, so each file-backed kind is
        read once. Afterwards a pass names exactly the native domains it saw move -- ``None``
        never returns again for a state that has already read them. A change names the
        intersection of the wake's domains with :data:`NATIVE_ATTENTION_DOMAINS` (empty when
        an unrelated input moved), a heartbeat names nothing, and a full pass -- whose domains
        are the healing set -- names both. The reader keeps whatever it was not told to
        re-read, so an age-only heartbeat reaches no store. A full pass also bounds the one
        case a single domain cannot settle on its own: an interrupted notice whose native
        verdict needs the task tree settles on that heal within the watcher's five-minute
        full-refresh bound.
        """

        if not self._native_attention_started:
            return None
        return frozenset(refresh.domains & NATIVE_ATTENTION_DOMAINS)

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
        task_sources = read_task_sources(config.coordination_root, now=pass_.now)
        # MIK-R81 rule 2 (F09): a TASKS invalidation re-reads the task tree, but the
        # downstream cascade (engine facts, drift) flips only when the
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
        self._task_sources = task_sources
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
        sources = project_task_sources(
            closure_docs, coordination_root=config.coordination_root, now=pass_.now
        )
        # Replacing the whole closure also drops newly invalid documents. Full and
        # incremental reads have the same authority, including declared row facts.
        retained_sources = {
            source.path: replace(
                source,
                summary=source.summary.model_copy(
                    update={"ageSeconds": _file_age_seconds(source.path, pass_.now)}
                ),
            )
            for source in self._task_sources
            if source.path not in closure
        }
        retained_sources.update({source.path: source for source in sources})
        self._task_sources = [retained_sources[path] for path in sorted(retained_sources)]

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

    def _refresh_drift(
        self, config: McpRuntimeConfig, pass_: RefreshPass, observations: list[AttentionObservation]
    ) -> None:
        if not pass_.tasks_changed and not pass_.refresh.affects(ProjectionDomain.DRIFT):
            return
        assert self._contracts is not None
        drift = AttentionObservation("actionable-drift", pass_.now)
        observations.append(drift)
        prune_orphaned_drift_snapshots(
            config, contracts=self._contracts, observation=drift, note_own=True
        )
        snapshots = read_drift_snapshots(config.coordination_root, now=pass_.now, observation=drift)
        # Completeness governs acknowledgement adoption/removal, not visibility:
        # a failed sibling read cannot hide a valid changed occurrence.
        self._drift_snapshots = snapshots

    def _observe_native_kinds(
        self, entries: TaskEntries, now: datetime, observations: list[AttentionObservation]
    ) -> None:
        """The live native kinds' own occurrence ends (MIK-R200@v2 rule 7).

        Dismissing a live cause hides exactly one occurrence, so the acknowledgement must leave
        when that occurrence ends: the prompt is answered, the message is delivered or withdrawn,
        or the task's agent takes a turn and the idle interval it acknowledged is over. The proof
        is the same complete observation that publishes the row, and the identities are the rows
        themselves -- ``native_attention_items`` is the one owner of every occurrence id, so an
        occurrence can never be published under one identity and reclaimed under another.

        An incomplete observation supplies no proof of an end. An unreadable host publication, an
        unreadable hold store, or an unknown host (which holds no stall interval at all) leaves
        every acknowledgement of that kind in place. A row that is still published is never
        reported healthy either: a live cause cannot be acknowledged out of existence, only out
        of sight, so nothing here reclaims a row that this same pass derives.
        """

        published: dict[str, set[str]] = {}
        for row in native_attention_items(self._native_attention, entries.tasks, now):
            published.setdefault(row.kind, set()).add(row.id)
        # A stall row appears only once its deadline passes, but its interval is live from the
        # moment the task goes idle. The acknowledgement names the interval, so the interval --
        # not the row -- is what must still exist for it to be kept.
        published["task-stalled"] = set(stall_occurrences(self._native_attention, entries.tasks))
        complete = {
            "permission-wait": self._native_attention.permissions_complete,
            "held-message": self._native_attention.held_complete,
            "task-stalled": self._host_activity.known,
        }
        for kind, whole in complete.items():
            if not whole:
                continue
            observation = AttentionObservation(kind, now)
            for item_id in published.get(kind, ()):
                observation.observe(item_id)
            observations.append(observation)

    def _refresh_workspace(
        self, pass_: RefreshPass, *, observer_root: Path, observations: list[AttentionObservation]
    ) -> None:
        if not (
            observations
            or pass_.refresh.is_heartbeat
            or pass_.refresh.affects(ProjectionDomain.WORKSPACE)
        ):
            return
        store = AttentionDismissalStore(observer_root)
        try:
            self._attention_dismissals = store.reconcile(observations)
        except OSError:
            # R166: losing a disposable write must not stop publication. Retain
            # the last successfully read acknowledgements until a later read.
            logger.warning("Attention dismissals could not be refreshed", exc_info=True)

    def _refresh_progress(
        self, config: McpRuntimeConfig, pass_: RefreshPass, observations: list[AttentionObservation]
    ) -> None:
        if pass_.refresh.affects(ProjectionDomain.START_PROGRESS):
            starts = AttentionObservation("blocked-start", pass_.now)
            observations.append(starts)
            self._engine_start_progress = read_start_progress_entries(
                config.coordination_root, now=pass_.now, observation=starts
            )
        if pass_.refresh.affects(ProjectionDomain.TOOL_REPORTS):
            self._tool_reports = read_tool_reports(config.coordination_root, now=pass_.now)

    def _advance_ages(self, now: datetime) -> None:
        if self._ages_at is None:
            return
        elapsed = max(0.0, (now - self._ages_at).total_seconds())
        if elapsed == 0:
            return
        self._drift_snapshots = _advance_model_age(
            self._drift_snapshots, "snapshotStaleSeconds", elapsed
        )
        self._tool_reports = _advance_model_age(self._tool_reports, "ageSeconds", elapsed)
        aged = _advance_model_age(
            [source.summary for source in self._task_sources], "ageSeconds", elapsed
        )
        self._task_sources = [
            replace(source, summary=summary)
            for source, summary in zip(self._task_sources, aged, strict=True)
        ]
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
