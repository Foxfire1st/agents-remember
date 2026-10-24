"""Log/snapshot reading and atomic projection writes -- the reducer's I/O edge.

``project_and_write`` ties domain-invalidated source reads, pure reduction and
publication together (the entry the serving layer drives on a tick). Projections are published through
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
from typing import TYPE_CHECKING, Any

from agents_remember.kernel.atomic_write import atomic_write_text
from agents_remember.observer.projection import (
    TASK_DOCUMENTS_PAYLOAD_BUDGET_BYTES,
    LedgerNode,
    RouteCoverageNode,
    WorkspaceProjection,
    task_documents_body_bytes,
)
from agents_remember.observer.reducer import (
    AnalyticalInputs,
    WorkspaceStructure,
    project_workspace,
)
from agents_remember.serving.delta import StableProjectionState, stable_projection_state
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
)

if TYPE_CHECKING:
    from agents_remember.kernel.primitives.runtime_config import (
        McpRuntimeConfig,
    )
    from agents_remember.serving.projections.landing_state import LandingStateReader

LATEST_STATE = "latest-state.json"
LATEST_METRICS = "latest-metrics.json"
# Repo-surface TTL: this walk opens+parses every onboarding document across EVERY managed
# repo (8 memory repos, thousands of markdown files) — py-spy measured it as the dominant slice
# of the projection tick at the old 15 s TTL. Coverage/ledger panels tolerate minutes
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
    value: tuple[list[RouteCoverageNode], list[LedgerNode]]


_repo_surface_cache: dict[tuple[tuple[str, str, str], ...], _RepoSurfaceCacheEntry] = {}


# ONE leaf-enclosure-contract enumeration + parse pass per tick, shared by
# read_enclosures, read_engine_process_facts, and drift-snapshot pruning (each previously ran its
# own walk + load_contract pass = 3x per tick). The cache reuses a parsed contract while its
# (mtime_ns, size, ctime_ns) is unchanged and prunes to the live enumeration each build. Mutated only on the
# projection worker thread (ticks are serialized by the projector's awaited asyncio.to_thread),
# matching the serialized projection input snapshot.
_contract_snapshot_cache = ContractSnapshotCache()


def write_projection(root: Path, projection: WorkspaceProjection) -> None:
    """Atomically write ``latest-state.json`` + ``latest-metrics.json``."""
    root.mkdir(parents=True, exist_ok=True)
    # JSON mode: a task's `providerLimit` carries `datetime` values (MIK-R190 rule 13) and this
    # file is JSON. Without `mode="json"` one limited task made `json.dumps` raise, so every
    # projection tick failed and nothing served advanced while any seat was limited.
    _write_json(
        root / LATEST_STATE, projection.model_dump(mode="json", by_alias=True, exclude_none=True)
    )
    _write_json(
        root / LATEST_METRICS,
        projection.metrics.model_dump(mode="json", by_alias=True, exclude_none=True),
    )


@dataclass
class ProjectionTickState:
    """What a projector carries between ticks: the retained input snapshot it re-reads
    incrementally, and the pre-read landing-state refresher it drives before reading. Both are
    the projector's own long-lived collaborators; a tick given some of them and not others
    silently reverts to a cold read.

    ``stable`` is filled in by the tick itself: it is the stable form the tick computed for its
    own write decision, published afterwards instead of being computed a second time. The value
    is live only for the tick that set it, so it is not part of the state a tick starts from.
    """

    input_state: ProjectionInputState | None = None
    landing_state: LandingStateReader | None = None
    # The stable form this tick computed for its own write decision, so publication compares the
    # same dump instead of computing a second one (MIK-R179 rule 2: no repeated work with no new
    # input). ``None`` until the tick has computed it.
    stable: StableProjectionState | None = None


def project_and_write(
    config: McpRuntimeConfig,
    *,
    now: datetime | None = None,
    refresh: ProjectionRefresh | None = None,
    previous_stable: StableProjectionState | None = None,
    tick: ProjectionTickState | None = None,
) -> WorkspaceProjection:
    """Read logs + structural + analytical snapshots, reduce the tree, write it atomically.

    ``latest-state.json`` (and its metrics sibling) is rewritten only when the
    projection's stable content moved since ``previous_stable`` (MIK-R81 rule 3:
    a tick whose content did not change costs no file write and wakes no echo).
    The first projection (``previous_stable=None``) always writes.
    """
    tick = tick or ProjectionTickState()
    moment = now or datetime.now(UTC)
    root = observer_root(config)
    state = tick.input_state or ProjectionInputState(contract_cache=_contract_snapshot_cache)
    inputs = state.read(
        config,
        ProjectionReaders(
            repo_surfaces=_gather_repo_surfaces_cached,
            landing_state=tick.landing_state,
        ),
        observer_root=root,
        pass_=RefreshPass(now=moment, refresh=refresh or ProjectionRefresh.full()),
    )
    projection = project_workspace(
        structure=WorkspaceStructure(
            enclosures=inputs.enclosures,
            # The Topology constellation filters its tree on this active set — the same
            # active-enclosure admission the Engine Room's engine_process_facts use, so both
            # views share one definition of "active" rather than diverging.
            active_worktree_groups=inputs.active_worktree_groups,
        ),
        now=moment,
        given=AnalyticalInputs(
            drift_snapshots=inputs.drift_snapshots,
            route_coverage=inputs.route_coverage,
            tool_reports=inputs.tool_reports,
            ledgers=inputs.ledgers,
            task_sources=inputs.task_sources,
            host_activity=inputs.host_activity,
            closeout_queues=inputs.closeout_queues,
            engine_process_facts=inputs.engine_process_facts,
            engine_start_progress=inputs.engine_start_progress,
            attention_dismissals=inputs.attention_dismissals,
            native_attention=inputs.native_attention,
            task_entries=inputs.task_entries,
        ),
    )
    _warn_if_task_documents_payload_over_budget(projection, now=moment)
    if previous_stable is None:
        write_projection(root, projection)
    else:
        tick.stable = stable_projection_state(projection)
        if tick.stable != previous_stable:
            write_projection(root, projection)
    return projection


def _warn_if_task_documents_payload_over_budget(
    projection: WorkspaceProjection, *, now: datetime
) -> int:
    """Flag (rate-limited) when the broadcast serialises a body-heavy task-doc payload.

    Returns the measured body-byte cost so callers/tests can assert on it. This is observability only --
    no truncation or contract change. Full document bodies are read on demand;
    this measures only the text previews retained in native task summaries.
    """
    payload_bytes = task_documents_body_bytes(
        [task.summary for task in projection.tasks if task.summary is not None]
    )
    if payload_bytes <= TASK_DOCUMENTS_PAYLOAD_BUDGET_BYTES:
        return payload_bytes
    last = _last_task_payload_warn.get("at")
    if last is not None:
        age = (now - last).total_seconds()
        if 0 <= age < TASK_DOCUMENTS_PAYLOAD_WARN_INTERVAL_SECONDS:
            return payload_bytes
    _last_task_payload_warn["at"] = now
    logger.warning(
        "task summary preview payload %d bytes exceeds budget %d across %d documents",
        payload_bytes,
        TASK_DOCUMENTS_PAYLOAD_BUDGET_BYTES,
        len([task.summary for task in projection.tasks if task.summary is not None]),
    )
    return payload_bytes


def _gather_repo_surfaces(
    config: McpRuntimeConfig,
) -> tuple[list[RouteCoverageNode], list[LedgerNode]]:
    """Per-repo analytical surfaces (route coverage, ledger).

    These walk each managed repo's onboarding/memory roots, so they are gathered
    per ``RepositoryScope`` rather than once under the coordination root.
    """
    route_coverage: list[RouteCoverageNode] = []
    ledgers: list[LedgerNode] = []
    for scope in config.repositories.values():
        if scope.memory_root is None:
            continue
        onboarding_root = scope.memory_root / "onboarding"
        route_coverage.extend(read_route_coverage(onboarding_root, repository=scope.repo_id))
        ledger = read_ledger(scope.memory_root, code_root=scope.path)
        if ledger is not None:
            ledgers.append(ledger)
    return route_coverage, ledgers


def _gather_repo_surfaces_cached(
    config: McpRuntimeConfig, moment: datetime
) -> tuple[list[RouteCoverageNode], list[LedgerNode]]:
    key = _repo_surface_cache_key(config)
    entry = _repo_surface_cache.get(key)
    if entry is not None:
        age = (moment - entry.at).total_seconds()
        if 0 <= age < REPO_SURFACE_REFRESH_TTL_SECONDS:
            return entry.value
    value = _gather_repo_surfaces(config)
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
