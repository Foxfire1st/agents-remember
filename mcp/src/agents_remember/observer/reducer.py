"""Assemble workspace projections from structural and analytical facts."""

from __future__ import annotations

from datetime import datetime

from agents_remember.observer.projection import (
    ActionAvailability,
    EnclosureNode,
    Metrics,
    WorkspaceProjection,
)
from agents_remember.observer.reducer_impl._attention import (
    _PROVIDER_DOWN,
    _drift_attention,
    _drift_attention_detail,
    _is_dismissed,
    _provider_attention,
    _setup_attention,
    _start_attention,
    build_attention_queue,
)
from agents_remember.observer.reducer_impl._metrics import (
    build_analytics,
)
from agents_remember.observer.reducer_impl._processes import (
    _DECISIVE_SETUP_EDGE_STATES,
    _ENGINE_DOWN,
    _ENGINE_INDEXING,
    _GUIDANCE_PHASE,
    _ROLE_ORDER,
    _SETUP_DONE,
    _SETUP_FAILED,
    _START_PHASE,
    _as_dict,
    _bool_or_none,
    _code_refs,
    _CodeRefs,
    _engine_process,
    _engine_runtime_state,
    _expected_provider_roles,
    _int_or_none,
    _is_disposed,
    _materialize_edge_state,
    _memory_refs,
    _MemoryRefs,
    _missing_facts,
    _process_edges,
    _process_health,
    _process_phase,
    _ProcessLanes,
    _provider_boot_nodes,
    _ref_fact_state,
    _seed_edge_state,
    _setup_facts,
    _SetupFacts,
    _source_files,
    _start_process_node,
    _str_list,
    _str_or_none,
    build_engine_processes,
)
from agents_remember.observer.reducer_impl._types import AnalyticalInputs, WorkspaceStructure
from agents_remember.observer.task_entries import build_task_entries


def project_workspace(
    *,
    structure: WorkspaceStructure,
    now: datetime,
    given: AnalyticalInputs | None = None,
) -> WorkspaceProjection:
    """Assemble the whole tree from already-read logs + structural + analytical snapshots.

    The two bundles are the design's own two slices. ``structure`` is 3a, the workspace as it
    exists. ``given`` is 3b and optional as a set: a caller that wants only the structural tree
    omits it and gets an empty ``analytics``.
    """
    given = given or AnalyticalInputs()
    providers = structure.providers
    enriched = [
        enclosure.model_copy(update={"actions": enclosure_actions(enclosure)})
        for enclosure in structure.enclosures
    ]
    engine_processes = build_engine_processes(
        given.engine_process_facts,
        enriched,
        providers,
        given.setup_progress,
        given.engine_start_progress,
    )
    # L190-R1-F04: one canonical join per pass. The input reader builds it so the stall clock and
    # these rows share one relation; a caller that supplies none still gets the join here.
    tasks = given.task_entries or build_task_entries(
        given.task_sources, enriched, given.host_activity
    )
    analytics = build_analytics(
        given,
        attention_queue=build_attention_queue(providers, given, tasks.tasks, now),
        engine_processes=engine_processes,
    )
    return WorkspaceProjection(
        generatedAt=now.isoformat(),
        tasks=tasks.tasks,
        taskCounts=tasks.counts,
        enclosures=tasks.unbound_enclosures,
        providers=providers,
        activeWorktreeGroups=sorted(structure.active_worktree_groups),
        closeoutQueues=given.closeout_queues,
        metrics=Metrics(
            ignoredHostAgents=given.host_activity.ignored_agents,
        ),
        analytics=analytics,
    )


def enclosure_actions(enclosure: EnclosureNode) -> list[ActionAvailability]:
    return [_integrate_action(enclosure), _cleanup_action(enclosure)]


def _integrate_action(enclosure: EnclosureNode) -> ActionAvailability:
    if enclosure.closeoutStatus != "completed":
        return ActionAvailability(
            action="integrate",
            enabled=False,
            disabledReason="closeout not complete",
            nextSafeAction="run worktree_closeout_preview then apply",
        )
    if enclosure.integrationStatus != "not-started":
        return ActionAvailability(
            action="integrate",
            enabled=False,
            disabledReason=f"integration already {enclosure.integrationStatus}",
        )
    return ActionAvailability(action="integrate", enabled=True)


def _cleanup_action(enclosure: EnclosureNode) -> ActionAvailability:
    if enclosure.integrationStatus != "completed":
        return ActionAvailability(
            action="cleanup",
            enabled=False,
            disabledReason="integration not complete",
            nextSafeAction="integrate first",
        )
    if enclosure.cleanup != "pending":
        return ActionAvailability(
            action="cleanup",
            enabled=False,
            disabledReason=f"already {enclosure.cleanup}",
        )
    return ActionAvailability(action="cleanup", enabled=True)


__all__ = [
    "_DECISIVE_SETUP_EDGE_STATES",
    "_ENGINE_DOWN",
    "_ENGINE_INDEXING",
    "_GUIDANCE_PHASE",
    "_PROVIDER_DOWN",
    "_ROLE_ORDER",
    "_SETUP_DONE",
    "_SETUP_FAILED",
    "_START_PHASE",
    "AnalyticalInputs",
    "WorkspaceStructure",
    "_CodeRefs",
    "_MemoryRefs",
    "_ProcessLanes",
    "_SetupFacts",
    "_as_dict",
    "_bool_or_none",
    "_code_refs",
    "_drift_attention",
    "_drift_attention_detail",
    "_engine_process",
    "_engine_runtime_state",
    "_expected_provider_roles",
    "_int_or_none",
    "_is_dismissed",
    "_is_disposed",
    "_materialize_edge_state",
    "_memory_refs",
    "_missing_facts",
    "_process_edges",
    "_process_health",
    "_process_phase",
    "_provider_attention",
    "_provider_boot_nodes",
    "_ref_fact_state",
    "_seed_edge_state",
    "_setup_attention",
    "_setup_facts",
    "_source_files",
    "_start_attention",
    "_start_process_node",
    "_str_list",
    "_str_or_none",
    "build_analytics",
    "build_attention_queue",
    "build_engine_processes",
    "enclosure_actions",
    "project_workspace",
]
