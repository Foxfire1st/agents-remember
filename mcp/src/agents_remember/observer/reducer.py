"""The reducer: events (+ file snapshots) -> resolved projections.

The single owner of interpretation (design §2.5). :func:`project_lifecycle`
folds one lifecycle's event log into a :class:`LifecycleProjection`; it is a pure
function of ``(events, now)`` so "same log => same projection" is testable and a
recorded log doubles as dev/test/demo/replay fixture. :func:`project_workspace`
assembles the whole tree from already-read inputs (logs + provider/enclosure
snapshots), keeping all file I/O at the call edge
(:mod:`agents_remember.observer.projection_store`).

The inferred layer reuses the write-side thresholds rather than re-deriving them:
a ``running`` lifecycle whose last event is older than ``STALE_AFTER_SECONDS``
projects ``paused`` (mirrors ``providers.setup_progress``'s ``_project_running``),
and a dormant never-promoted fleeting lifecycle projects ``abandoned`` (mirrors
``ambient._is_dormant_fleeting`` -- the reducer *projects* the state, the sweep
*prunes* the log). Both are marked ``inferred`` so a renderer never shows a
derived state as a written fact.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from agents_remember.controlplane.stamps import age_seconds
from agents_remember.observer.events import Event
from agents_remember.observer.lifecycle_state import (
    INITIAL_PHASE,
    STATES,
    TERMINAL_STATES,
    coerce_end_outcome,
    coerce_phase,
)
from agents_remember.observer.projection import (
    ActionAvailability,
    EnclosureNode,
    LifecycleProjection,
    WorkspaceProjection,
)
from agents_remember.observer.reducer_impl._attention import (
    _DISMISSABLE_REPO_KINDS,
    _PROVIDER_DOWN,
    _SEVERITY_RANK,
    _ask_text,
    _attach_gates,
    _await_summary,
    _drift_attention,
    _drift_attention_detail,
    _gate_attention,
    _gate_node,
    _is_dismissed,
    _lifecycle_attention,
    _provider_attention,
    _setup_attention,
    _signal_after,
    _start_attention,
    build_attention_queue,
)
from agents_remember.observer.reducer_impl._metrics import (
    _STALENESS_BUCKETS,
    TOKEN_SERIES_MAX,
    TOKEN_SERIES_RECENT,
    _decimate_token_series,
    _metrics,
    _staleness_bucket,
    _stalest,
    build_analytics,
    staleness_histogram,
    token_series,
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
from agents_remember.observer.series_tokens import attach_series_token_totals
from agents_remember.observer.timeutil import STALE_AFTER_SECONDS, TTL_SECONDS

# The whole vocabulary, as strings. Taken from :data:`STATES` rather than from
# ``get_args(State)``: on the union form (``Literal[...] | Other``) ``get_args`` returns
# ``Literal`` OBJECTS, and a set of those matches no event payload -- every correction below
# would be dropped as malformed, silently and forever.
_STATES: frozenset[str] = frozenset(STATES)


def project_lifecycle(events: list[Event], *, now: datetime) -> LifecycleProjection:
    """Fold one lifecycle's event log into its resolved projection.

    Pure: the only inputs are the log and ``now``. ``events`` must be non-empty
    and append-ordered (the store's invariant); ``events[0]`` is the
    self-contained ``lifecycle.started`` that seeds the projection.
    """
    if not events:
        raise ValueError("cannot project an empty event log")
    proj = _seed_from_started(events[0])
    corrections = _index_corrections(events)
    for event in events[1:]:
        if event.kind == "correction.recorded":
            continue  # its effect is applied at the corrected event's position
        before_state = proj.state
        proj = _apply_kind(proj, event)
        corrected = corrections.get(event.id)
        if corrected is not None:
            proj = proj.model_copy(update={"state": corrected})
        if proj.state != before_state:
            # Stamp when the lifecycle entered its new written state (the attention
            # queue's stable, heartbeat-immune dismissal anchor).
            proj = proj.model_copy(update={"stateEnteredAt": event.ts})
    proj = proj.model_copy(update={"lastEventTs": events[-1].ts})
    proj = _project_inferred(proj, events, now)
    return proj.model_copy(
        update={"actions": _lifecycle_actions(proj), "tokenSeries": token_series(events)}
    )


def project_workspace(
    logs: list[list[Event]],
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
    lifecycles = [project_lifecycle(log, now=now) for log in logs if log]
    enriched = [
        enclosure.model_copy(update={"actions": enclosure_actions(enclosure)})
        for enclosure in structure.enclosures
    ]
    lifecycles = _current_event_backed_lifecycles(enriched, lifecycles)
    # Abandon is terminal for the anchor too (L11): the single-writer store invariant means
    # no one may append lifecycle.ended to a log they do not own, so an abandoned worktree's
    # lifecycle is TERMINALIZED here by the reader — exactly the store's prescribed pattern.
    lifecycles = _terminalize_abandoned_anchor_lifecycles(enriched, lifecycles)
    # A persistent lifecycle exists as long as its worktree (note 01): every current
    # worktree-backed enclosure with no event-backed lifecycle projects as PAUSED, so dormant
    # worktrees appear as the lifecycles they are -- not as nothing. Fleeting + active promotion
    # lifecycles still come from the logs above.
    lifecycles = lifecycles + _persistent_lifecycles(enriched, lifecycles)
    lifecycles = _attach_gates(lifecycles, given.gates)
    engine_processes = build_engine_processes(
        given.engine_process_facts,
        enriched,
        providers,
        given.setup_progress,
        given.engine_start_progress,
    )
    series_nodes = attach_series_token_totals(given.series, given.task_documents, lifecycles)
    analytics = build_analytics(
        given,
        series=series_nodes,
        attention_queue=build_attention_queue(lifecycles, providers, given),
        engine_processes=engine_processes,
    )
    return WorkspaceProjection(
        generatedAt=now.isoformat(),
        lifecycles=lifecycles,
        enclosures=enriched,
        providers=providers,
        activeWorktreeGroups=sorted(structure.active_worktree_groups),
        metrics=_metrics(lifecycles, given.sidecar_staleness),
        analytics=analytics,
    )


# --- persistent lifecycles from worktree enclosures (note 01) ----------------


def _current_event_backed_lifecycles(
    enclosures: list[EnclosureNode], lifecycles: list[LifecycleProjection]
) -> list[LifecycleProjection]:
    """Keep only event-backed lifecycles that still have a valid live anchor."""
    by_enclosure = {enclosure.enclosure: enclosure for enclosure in enclosures}
    return [
        lifecycle
        for lifecycle in lifecycles
        if _event_backed_lifecycle_is_current(lifecycle, by_enclosure)
    ]


def _event_backed_lifecycle_is_current(
    lifecycle: LifecycleProjection, enclosures: dict[str, EnclosureNode]
) -> bool:
    if lifecycle.fleeting:
        return True
    if not lifecycle.enclosure:
        return _missing_enclosure_is_still_materializing(lifecycle)
    enclosure = enclosures.get(lifecycle.enclosure)
    if enclosure is None:
        return _missing_enclosure_is_still_materializing(lifecycle)
    return not enclosure.lifecycleId or enclosure.lifecycleId == lifecycle.id


def _missing_enclosure_is_still_materializing(lifecycle: LifecycleProjection) -> bool:
    return (
        lifecycle.state not in TERMINAL_STATES
        and not lifecycle.inferred
        and lifecycle.staleSeconds is not None
        and lifecycle.staleSeconds <= STALE_AFTER_SECONDS
    )


def _terminalize_abandoned_anchor_lifecycles(
    enclosures: list[EnclosureNode], lifecycles: list[LifecycleProjection]
) -> list[LifecycleProjection]:
    """Project ``abandoned`` onto lifecycles whose anchor enclosure was abandoned (L11).

    ``worktree_abandon`` discards the worktrees and records ``cleanup: abandoned`` in the
    contract, but the lifecycle's event log ends mid-``build`` — the abandoning session may
    not own that log (a respawned server clears the ambient registry), and the store's
    single-writer invariant forbids a foreign ``lifecycle.ended`` append. The contract is
    durable OBSERVED ground truth, so the reader projects the terminal state from it.
    """
    abandoned_anchors = {
        enclosure.enclosure for enclosure in enclosures if enclosure.cleanup == "abandoned"
    }
    if not abandoned_anchors:
        return lifecycles
    return [
        lifecycle.model_copy(update={"state": "abandoned"})
        if (
            not lifecycle.fleeting
            and lifecycle.enclosure in abandoned_anchors
            and lifecycle.state not in TERMINAL_STATES
        )
        else lifecycle
        for lifecycle in lifecycles
    ]


def _persistent_lifecycles(
    enclosures: list[EnclosureNode], event_backed: list[LifecycleProjection]
) -> list[LifecycleProjection]:
    """A paused persistent lifecycle for every worktree-backed enclosure with no event log.

    Note 01: a persistent (worktree-backed) lifecycle exists as long as its worktree, ``paused``
    when no session drives it, and is never reaped. The reducer otherwise only materializes
    event-backed lifecycles, so a worktree with no events (e.g. created by a runtime that does not
    emit; ``lifecycleId == ""``) would vanish from the tree. Synthesized lifecycles carry no events
    (``lastEventTs == ""``), which is how the attention queue tells them from a live session gone
    quiet (a dormant *persistent* worktree belongs in the hangar, not the queue -- note 06).
    """
    known_ids = {lc.id for lc in event_backed}
    known_enclosures = {lc.enclosure for lc in event_backed if lc.enclosure}
    synthesized: list[LifecycleProjection] = []
    for enclosure in enclosures:
        if enclosure.lifecycleId and enclosure.lifecycleId in known_ids:
            continue
        if enclosure.enclosure in known_enclosures:
            continue
        # No worktree, no persistent lifecycle (note 01): an abandoned enclosure's worktrees
        # were discarded and a reopened one's (L11) were reclaimed by its completed run —
        # neither has anything to pause. The reopened leaf still appears as its planned task
        # doc row; a fresh lifecycle arrives with the next worktree_start.
        if enclosure.cleanup in ("abandoned", "reopened"):
            continue
        synthesized.append(_persistent_from_enclosure(enclosure))
    return synthesized


def _persistent_from_enclosure(enclosure: EnclosureNode) -> LifecycleProjection:
    """Seed a paused persistent lifecycle from a worktree contract (no event log).

    The lifecycle phase is unknown without events, so it is inferred from the contract's git progress:
    a completed closeout/integration reads ``close``, otherwise ``build`` (worktrees live in build).
    """
    closed = "completed" in {enclosure.closeoutStatus, enclosure.integrationStatus}
    proj = LifecycleProjection(
        id=enclosure.lifecycleId or f"{enclosure.repoName}/{enclosure.taskName}",
        state="paused",
        phase="close" if closed else "build",
        fleeting=False,
        enclosure=enclosure.enclosure,
        repoId=enclosure.repoName,
        startedAt="",
        lastEventTs="",
        inferred=True,
    )
    return proj.model_copy(update={"actions": _lifecycle_actions(proj)})


# --- the fold ----------------------------------------------------------------


def _seed_from_started(event: Event) -> LifecycleProjection:
    data = event.data
    return LifecycleProjection(
        id=event.lifecycleId or "",
        state="running",
        phase=coerce_phase(str(data.get("phase", INITIAL_PHASE))),
        fleeting=bool(data.get("fleeting", True)),
        enclosure=event.enclosure,
        repoId=event.repoId,
        startedAt=event.ts,
        lastEventTs=event.ts,
        stateEnteredAt=event.ts,
    )


def _phase_changed_updates(proj: LifecycleProjection, event: Event) -> dict[str, Any]:
    return {"phase": coerce_phase(str(event.data.get("phase", proj.phase)))}


def _blocked_updates(_proj: LifecycleProjection, event: Event) -> dict[str, Any]:
    return {"state": "blocked", "ask": event.data.get("ask")}


def _awaiting_developer_updates(_proj: LifecycleProjection, event: Event) -> dict[str, Any]:
    """NOTIFY-AND-CONTINUE turn end (leaf-28): non-terminal.

    The summary rides on the projection's ``ask`` carrier (mirroring how the block ask rides
    on ``lifecycle.blocked``) so the awaiting-developer attention item can surface it;
    ``lifecycle.resumed`` clears it back to None on auto-resume.
    """
    summary = event.data.get("summary")
    return {
        "state": "awaiting-developer",
        "ask": {"summary": summary} if summary is not None else None,
    }


def _resumed_updates(_proj: LifecycleProjection, _event: Event) -> dict[str, Any]:
    """Carries both the parked blocked path and the awaiting-developer turn-end path back to
    running (resume() / resume_from_await() both emit this)."""
    return {"state": "running", "ask": None}


def _paused_updates(_proj: LifecycleProjection, _event: Event) -> dict[str, Any]:
    return {"state": "paused"}


def _promoted_updates(_proj: LifecycleProjection, event: Event) -> dict[str, Any]:
    """Promotion fixes the lifecycle's scope, and binds enclosure/repo when the event names them."""
    updates: dict[str, Any] = {"fleeting": False, "scope": event.data.get("scope")}
    if event.enclosure is not None:
        updates["enclosure"] = event.enclosure
    if event.repoId is not None:
        updates["repoId"] = event.repoId
    return updates


def _ended_updates(_proj: LifecycleProjection, event: Event) -> dict[str, Any]:
    """The ONE way into a terminal state: the outcome names it (see ``TerminalState``)."""
    return {"state": coerce_end_outcome(event.data.get("outcome"))}


def _tool_completed_updates(proj: LifecycleProjection, event: Event) -> dict[str, Any]:
    tokens = event.data.get("tokens")
    return {"tokens": proj.tokens + tokens} if isinstance(tokens, int) else {}


# Each kind owns the projection fields it writes. A kind absent from this table --
# lifecycle.heartbeat and any unknown kind -- is liveness only: staleness is taken from
# events[-1].ts, so no projection field changes for it.
_KIND_UPDATES: dict[str, Callable[[LifecycleProjection, Event], dict[str, Any]]] = {
    "lifecycle.phase-changed": _phase_changed_updates,
    "lifecycle.blocked": _blocked_updates,
    "lifecycle.awaiting-developer": _awaiting_developer_updates,
    "lifecycle.resumed": _resumed_updates,
    "lifecycle.paused": _paused_updates,
    "lifecycle.promoted": _promoted_updates,
    "lifecycle.ended": _ended_updates,
    "tool.completed": _tool_completed_updates,
}


def _apply_kind(proj: LifecycleProjection, event: Event) -> LifecycleProjection:
    """Fold one event into the projection through the kind that owns its fields."""
    updates = _KIND_UPDATES.get(event.kind)
    return proj.model_copy(update={} if updates is None else updates(proj, event))


def _index_corrections(events: list[Event]) -> dict[str, str]:
    """Map corrected-event-id -> replacement state (append-only corrections, §2.1).

    v1 corrects the ``state`` field (the audit case: a wrongly-recorded outcome).
    A correction names the event it ``corrects`` and the new ``state``; the value
    is validated against the state enum so a malformed correction is ignored.
    """
    index: dict[str, str] = {}
    for event in events:
        if event.kind != "correction.recorded":
            continue
        corrected = event.data.get("corrects")
        new_state = event.data.get("state")
        if isinstance(corrected, str) and isinstance(new_state, str) and new_state in _STATES:
            index[corrected] = new_state
    return index


def _project_inferred(
    proj: LifecycleProjection, events: list[Event], now: datetime
) -> LifecycleProjection:
    age = age_seconds(events[-1].ts, now)
    proj = proj.model_copy(update={"staleSeconds": age})
    if proj.state in TERMINAL_STATES or age is None:
        return proj
    # Mirrors ambient._is_dormant_fleeting: the reducer projects abandoned where
    # the opportunistic sweep would prune. Persistent lifecycles are never reaped.
    if proj.fleeting and age > TTL_SECONDS:
        return proj.model_copy(update={"state": "abandoned", "inferred": True})
    # Mirrors setup_progress._project_running: a live state gone quiet reads paused.
    if proj.state == "running" and age > STALE_AFTER_SECONDS:
        return proj.model_copy(update={"state": "paused", "inferred": True})
    return proj


# --- action availability (the reducer decides, never the UI) ------------------


def _lifecycle_actions(proj: LifecycleProjection) -> list[ActionAvailability]:
    blocked = proj.state == "blocked"
    return [
        ActionAvailability(
            action="resume",
            enabled=blocked,
            disabledReason=None if blocked else "lifecycle is not blocked",
            nextSafeAction=None if blocked else "resume becomes safe once the gate is resolved",
        )
    ]


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
    "TOKEN_SERIES_MAX",
    "TOKEN_SERIES_RECENT",
    "_DECISIVE_SETUP_EDGE_STATES",
    "_DISMISSABLE_REPO_KINDS",
    "_ENGINE_DOWN",
    "_ENGINE_INDEXING",
    "_GUIDANCE_PHASE",
    "_KIND_UPDATES",
    "_PROVIDER_DOWN",
    "_ROLE_ORDER",
    "_SETUP_DONE",
    "_SETUP_FAILED",
    "_SEVERITY_RANK",
    "_STALENESS_BUCKETS",
    "_START_PHASE",
    "AnalyticalInputs",
    "WorkspaceStructure",
    "_CodeRefs",
    "_MemoryRefs",
    "_ProcessLanes",
    "_SetupFacts",
    "_as_dict",
    "_ask_text",
    "_await_summary",
    "_bool_or_none",
    "_code_refs",
    "_decimate_token_series",
    "_drift_attention",
    "_drift_attention_detail",
    "_engine_process",
    "_engine_runtime_state",
    "_expected_provider_roles",
    "_gate_attention",
    "_gate_node",
    "_int_or_none",
    "_is_dismissed",
    "_is_disposed",
    "_lifecycle_attention",
    "_materialize_edge_state",
    "_memory_refs",
    "_metrics",
    "_missing_facts",
    "_paused_updates",
    "_process_edges",
    "_process_health",
    "_process_phase",
    "_provider_attention",
    "_provider_boot_nodes",
    "_ref_fact_state",
    "_seed_edge_state",
    "_setup_attention",
    "_setup_facts",
    "_signal_after",
    "_source_files",
    "_staleness_bucket",
    "_stalest",
    "_start_attention",
    "_start_process_node",
    "_str_list",
    "_str_or_none",
    "build_analytics",
    "build_attention_queue",
    "build_engine_processes",
    "enclosure_actions",
    "project_lifecycle",
    "project_workspace",
    "staleness_histogram",
    "token_series",
]
