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

from datetime import datetime
from typing import Any, get_args

from agents_remember.controlplane.records import DECISION_STATES, GateRecord
from agents_remember.observer.events import Event
from agents_remember.observer.lifecycle_state import (
    INITIAL_PHASE,
    TERMINAL_STATES,
    State,
    coerce_phase,
)
from agents_remember.observer.projection import (
    ActionAvailability,
    AgentPickupNode,
    Analytics,
    AttentionItem,
    CommitRefNode,
    DriftSnapshotNode,
    EnclosureNode,
    EngineProcessEdge,
    EngineProcessFacts,
    EngineProcessNode,
    GateNode,
    LandingRefNode,
    LedgerNode,
    LifecycleProjection,
    Metrics,
    ProviderBootNode,
    ProviderNode,
    RouteCoverageNode,
    SeriesNode,
    SetupProgressNode,
    SetupSummaryNode,
    SidecarStaleNode,
    TaskDocNode,
    TokenSample,
    ToolReportNode,
    WorkspaceProjection,
)
from agents_remember.observer.series_tokens import attach_series_token_totals
from agents_remember.observer.timeutil import STALE_AFTER_SECONDS, TTL_SECONDS, age_seconds

_STATES: frozenset[str] = frozenset(get_args(State))


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
        proj = _apply_kind(proj, event)
        corrected = corrections.get(event.id)
        if corrected is not None:
            proj = proj.model_copy(update={"state": corrected})
    proj = proj.model_copy(update={"lastEventTs": events[-1].ts})
    proj = _project_inferred(proj, events, now)
    return proj.model_copy(
        update={"actions": _lifecycle_actions(proj), "tokenSeries": token_series(events)}
    )


def project_workspace(
    logs: list[list[Event]],
    *,
    enclosures: list[EnclosureNode],
    providers: list[ProviderNode],
    now: datetime,
    drift_snapshots: list[DriftSnapshotNode] | None = None,
    sidecar_staleness: list[SidecarStaleNode] | None = None,
    setup_summaries: list[SetupSummaryNode] | None = None,
    setup_progress: list[SetupProgressNode] | None = None,
    route_coverage: list[RouteCoverageNode] | None = None,
    tool_reports: list[ToolReportNode] | None = None,
    agent_pickups: list[AgentPickupNode] | None = None,
    ledgers: list[LedgerNode] | None = None,
    task_documents: list[TaskDocNode] | None = None,
    series: list[SeriesNode] | None = None,
    engine_process_facts: list[EngineProcessFacts] | None = None,
    engine_start_progress: list[dict[str, Any]] | None = None,
    gates: list[GateRecord] | None = None,
    stalest_limit: int = 10,
) -> WorkspaceProjection:
    """Assemble the whole tree from already-read logs + structural + analytical snapshots.

    The analytical inputs (slice 3b) are keyword-optional: a caller that wants only
    the structural tree (the 3a contract) omits them and gets an empty ``analytics``.
    """
    lifecycles = [project_lifecycle(log, now=now) for log in logs if log]
    enriched = [
        enclosure.model_copy(update={"actions": enclosure_actions(enclosure)})
        for enclosure in enclosures
    ]
    lifecycles = _current_event_backed_lifecycles(enriched, lifecycles)
    # A persistent lifecycle exists as long as its worktree (note 01): every current
    # worktree-backed enclosure with no event-backed lifecycle projects as PAUSED, so dormant
    # worktrees appear as the lifecycles they are -- not as nothing. Fleeting + active promotion
    # lifecycles still come from the logs above.
    lifecycles = lifecycles + _persistent_lifecycles(enriched, lifecycles)
    lifecycles = _attach_gates(lifecycles, gates or [])
    sidecars = sidecar_staleness or []
    engine_processes = build_engine_processes(
        engine_process_facts or [],
        enriched,
        providers,
        setup_progress or [],
        engine_start_progress or [],
    )
    task_docs = task_documents or []
    series_nodes = attach_series_token_totals(series or [], task_docs, lifecycles)
    analytics = build_analytics(
        drift_snapshots=drift_snapshots or [],
        sidecar_staleness=sidecars,
        setup_summaries=setup_summaries or [],
        setup_progress=setup_progress or [],
        route_coverage=route_coverage or [],
        tool_reports=tool_reports or [],
        agent_pickups=agent_pickups or [],
        ledgers=ledgers or [],
        task_documents=task_docs,
        series=series_nodes,
        attention_queue=build_attention_queue(
            lifecycles,
            providers,
            drift_snapshots or [],
            setup_progress or [],
            engine_start_progress or [],
            gates or [],
        ),
        engine_processes=engine_processes,
        stalest_limit=stalest_limit,
    )
    return WorkspaceProjection(
        generatedAt=now.isoformat(),
        lifecycles=lifecycles,
        enclosures=enriched,
        providers=providers,
        metrics=_metrics(lifecycles, sidecars),
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
        synthesized.append(_persistent_from_enclosure(enclosure))
    return synthesized


def _persistent_from_enclosure(enclosure: EnclosureNode) -> LifecycleProjection:
    """Seed a paused persistent lifecycle from a worktree contract (no event log).

    The l-01 phase is unknown without events, so it is inferred from the contract's git progress:
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
    )


def _apply_kind(proj: LifecycleProjection, event: Event) -> LifecycleProjection:
    data = event.data
    updates: dict[str, Any] = {}
    if event.kind == "lifecycle.phase-changed":
        updates["phase"] = coerce_phase(str(data.get("phase", proj.phase)))
    elif event.kind == "lifecycle.blocked":
        updates["state"] = "blocked"
        updates["ask"] = data.get("ask")
    elif event.kind == "lifecycle.awaiting-developer":
        # NOTIFY-AND-CONTINUE turn end (leaf-28): non-terminal. The summary rides
        # on the projection's ``ask`` carrier (mirroring how the block ask rides on
        # ``lifecycle.blocked``) so the awaiting-developer attention item can surface
        # it; ``lifecycle.resumed`` clears it back to None on auto-resume.
        updates["state"] = "awaiting-developer"
        summary = data.get("summary")
        updates["ask"] = {"summary": summary} if summary is not None else None
    elif event.kind == "lifecycle.resumed":
        # Carries both the parked blocked path and the awaiting-developer turn-end
        # path back to running (resume() / resume_from_await() both emit this).
        updates["state"] = "running"
        updates["ask"] = None
    elif event.kind == "lifecycle.paused":
        updates["state"] = "paused"
    elif event.kind == "lifecycle.promoted":
        updates["fleeting"] = False
        updates["scope"] = data.get("scope")
        if event.enclosure is not None:
            updates["enclosure"] = event.enclosure
        if event.repoId is not None:
            updates["repoId"] = event.repoId
    elif event.kind == "lifecycle.ended":
        updates["state"] = "completed" if data.get("outcome") == "completed" else "abandoned"
    elif event.kind == "tool.completed":
        tokens = data.get("tokens")
        if isinstance(tokens, int):
            updates["tokens"] = proj.tokens + tokens
    # lifecycle.heartbeat (and any unknown kind): liveness only -- staleness is
    # taken from events[-1].ts, so no projection field changes here.
    return proj.model_copy(update=updates)


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


def _metrics(
    lifecycles: list[LifecycleProjection],
    sidecars: list[SidecarStaleNode] | None = None,
) -> Metrics:
    return Metrics(
        lifecycleCount=len(lifecycles),
        runningCount=sum(1 for lc in lifecycles if lc.state == "running"),
        blockedCount=sum(1 for lc in lifecycles if lc.state == "blocked"),
        pausedCount=sum(1 for lc in lifecycles if lc.state == "paused"),
        totalTokens=sum(lc.tokens for lc in lifecycles),
        stalenessHistogram=staleness_histogram(sidecars) if sidecars else {},
    )


# --- analytical rollups (slice 3b) -------------------------------------------


def token_series(events: list[Event]) -> list[TokenSample]:
    """Cumulative token spend over time, from the log's ``tool.completed`` events (§2.4).

    The per-lifecycle fuel gauge: gap #2 ("no token-spend persistence") is closed by
    the event substrate, so the running total is a pure fold of the log.
    """
    total = 0
    samples: list[TokenSample] = []
    for event in events:
        if event.kind != "tool.completed":
            continue
        tokens = event.data.get("tokens")
        if isinstance(tokens, int):
            total += tokens
            samples.append(TokenSample(ts=event.ts, cumulative=total))
    return samples


# Upper bound (exclusive, seconds) per verification-age bucket; the final bucket is
# open-ended and a node with no parseable date falls into "unknown".
_STALENESS_BUCKETS: tuple[tuple[str, float | None], ...] = (
    ("<7d", 7 * 86400.0),
    ("7-30d", 30 * 86400.0),
    ("30-90d", 90 * 86400.0),
    (">90d", None),
)


def staleness_histogram(nodes: list[SidecarStaleNode]) -> dict[str, int]:
    """Bucket sidecars by verification age (surface 11 rollup; unparseable -> unknown)."""
    buckets = {label: 0 for label, _ in _STALENESS_BUCKETS}
    buckets["unknown"] = 0
    for node in nodes:
        buckets[_staleness_bucket(node.ageSeconds)] += 1
    return buckets


def _staleness_bucket(age: float | None) -> str:
    if age is None:
        return "unknown"
    for label, upper in _STALENESS_BUCKETS:
        if upper is None or age < upper:
            return label
    return ">90d"


def build_analytics(
    *,
    drift_snapshots: list[DriftSnapshotNode],
    sidecar_staleness: list[SidecarStaleNode],
    setup_summaries: list[SetupSummaryNode],
    setup_progress: list[SetupProgressNode],
    route_coverage: list[RouteCoverageNode],
    tool_reports: list[ToolReportNode],
    agent_pickups: list[AgentPickupNode] | None = None,
    ledgers: list[LedgerNode],
    task_documents: list[TaskDocNode] | None = None,
    series: list[SeriesNode] | None = None,
    attention_queue: list[AttentionItem] | None = None,
    engine_processes: list[EngineProcessNode] | None = None,
    stalest_limit: int = 10,
) -> Analytics:
    """Assemble the analytical surfaces; the full sidecar list collapses to a leaderboard."""
    return Analytics(
        driftSnapshots=drift_snapshots,
        stalestSidecars=_stalest(sidecar_staleness, stalest_limit),
        setupSummaries=setup_summaries,
        setupProgress=setup_progress,
        routeCoverage=route_coverage,
        toolReports=tool_reports,
        agentPickups=agent_pickups or [],
        ledgers=ledgers,
        taskDocuments=task_documents or [],
        series=series or [],
        attentionQueue=attention_queue or [],
        engineProcesses=engine_processes or [],
    )


def _stalest(nodes: list[SidecarStaleNode], limit: int) -> list[SidecarStaleNode]:
    """The oldest-verified sidecars first; unparseable dates sort last."""
    ranked = sorted(nodes, key=lambda node: (node.ageSeconds is None, -(node.ageSeconds or 0.0)))
    return ranked[:limit]


# --- attention queue (slice 05) ----------------------------------------------

_SEVERITY_RANK: dict[str, int] = {"alarm": 0, "warn": 1, "info": 2}
_PROVIDER_DOWN: frozenset[str] = frozenset({"stopped", "failed", "error"})


def _ask_text(ask: dict[str, Any] | None) -> str | None:
    """The human-facing question from an open block ask, when present."""
    if not ask:
        return None
    question = ask.get("question")
    return str(question) if question is not None else None


def build_attention_queue(
    lifecycles: list[LifecycleProjection],
    providers: list[ProviderNode],
    drift_snapshots: list[DriftSnapshotNode],
    setup_progress: list[SetupProgressNode],
    start_progress: list[dict[str, Any]] | None = None,
    gates: list[GateRecord] | None = None,
) -> list[AttentionItem]:
    """The home-screen attention queue: a ranked cross-section of what needs the human.

    Pure and deterministic -- a total sort by (severity, wait, id) -- so the served
    projection and sim replay stay byte-identical. Every ``waitSeconds`` is an age the
    reducer/readers already computed server-side (``staleSeconds`` /
    ``snapshotStaleSeconds`` / ``heartbeatAgeSeconds``), never a client's render time.
    The taxonomy is extensible (North-Star Constraint 7): each source contributes one
    small builder. Enclosure-derived items (pending review / worktree debt) are the
    hangar's job (5c).
    """
    items = [
        *_lifecycle_attention(lifecycles),
        *_gate_attention(gates or []),
        *_provider_attention(providers),
        *_drift_attention(drift_snapshots),
        *_setup_attention(setup_progress),
        *_start_attention(start_progress or []),
    ]
    items.sort(
        key=lambda item: (_SEVERITY_RANK.get(item.severity, 9), -(item.waitSeconds or 0.0), item.id)
    )
    return items


def _await_summary(ask: dict[str, Any] | None) -> str | None:
    """The developer-facing summary an awaiting-developer turn-end carried."""
    if not ask:
        return None
    summary = ask.get("summary")
    return str(summary) if summary is not None else None


def _lifecycle_attention(lifecycles: list[LifecycleProjection]) -> list[AttentionItem]:
    """Awaiting-developer turn ends and bare blocks, then inferred stale/dormant
    sessions (one item per lifecycle).

    A durable open gate is emitted by ``_gate_attention`` and already materialized
    onto ``lifecycle.gate`` by ``_attach_gates``, so the ``blocked-gate`` item fires
    only for a *bare* ``block()`` with no GateRecord (``lifecycle.gate is None``) --
    that ``and lifecycle.gate is None`` is the gate-open/blocked-gate dedup.
    """
    items: list[AttentionItem] = []
    for lifecycle in lifecycles:
        if lifecycle.state == "awaiting-developer":
            # NOTIFY-AND-CONTINUE turn end (leaf-28): one info item, the developer's
            # cue that the turn is theirs. enclosure is the deep-link anchor when set.
            items.append(
                AttentionItem(
                    id=f"awaiting-developer:{lifecycle.id}",
                    kind="awaiting-developer",
                    severity="info",
                    lane="lifecycle",
                    title="Turn complete — your move",
                    detail=_await_summary(lifecycle.ask),
                    waitSeconds=lifecycle.staleSeconds,
                    lifecycleId=lifecycle.id,
                    enclosure=lifecycle.enclosure,
                    repoId=lifecycle.repoId,
                )
            )
        elif lifecycle.state == "blocked" and lifecycle.gate is None:
            items.append(
                AttentionItem(
                    id=f"blocked-gate:{lifecycle.id}",
                    kind="blocked-gate",
                    severity="warn",
                    lane="lifecycle",
                    title="Gate — input needed",
                    detail=_ask_text(lifecycle.ask),
                    waitSeconds=lifecycle.staleSeconds,
                    lifecycleId=lifecycle.id,
                    enclosure=lifecycle.enclosure,
                    repoId=lifecycle.repoId,
                )
            )
        elif lifecycle.inferred and lifecycle.state == "paused" and lifecycle.lastEventTs:
            # Only an event-backed session that went quiet is queue-worthy; a synthesized dormant
            # persistent worktree (lastEventTs == "") is the hangar's job (note 06), not the queue.
            items.append(
                AttentionItem(
                    id=f"stale-session:{lifecycle.id}",
                    kind="stale-session",
                    severity="info",
                    lane="lifecycle",
                    title="Session gone quiet",
                    waitSeconds=lifecycle.staleSeconds,
                    lifecycleId=lifecycle.id,
                    repoId=lifecycle.repoId,
                )
            )
        elif lifecycle.inferred and lifecycle.state == "abandoned" and lifecycle.lastEventTs:
            items.append(
                AttentionItem(
                    id=f"dormant-fleeting:{lifecycle.id}",
                    kind="dormant-fleeting",
                    severity="info",
                    lane="lifecycle",
                    title="Fleeting session dormant",
                    waitSeconds=lifecycle.staleSeconds,
                    lifecycleId=lifecycle.id,
                    repoId=lifecycle.repoId,
                )
            )
    return items


def _gate_node(gate: GateRecord) -> GateNode:
    """Project one durable gate; an open gate exposes the decision verbs the cockpit can POST."""
    return GateNode(
        id=gate.id,
        kind=gate.kind,
        state=gate.state,
        decidedBy=gate.decidedBy,
        decidedVia=gate.decidedVia,
        decisions=sorted(DECISION_STATES) if gate.state == "open" else [],
        packet=gate.packet,
        ts=gate.ts,
    )


def _attach_gates(
    lifecycles: list[LifecycleProjection], gates: list[GateRecord]
) -> list[LifecycleProjection]:
    """Materialize each lifecycle's latest open gate onto its projection (slice 6c)."""
    by_lifecycle: dict[str, list[GateRecord]] = {}
    for gate in gates:
        by_lifecycle.setdefault(gate.lifecycleId or "", []).append(gate)
    attached: list[LifecycleProjection] = []
    for lifecycle in lifecycles:
        open_gates = [g for g in by_lifecycle.get(lifecycle.id, []) if g.state == "open"]
        if open_gates:
            latest = max(open_gates, key=lambda gate: gate.ts)
            attached.append(lifecycle.model_copy(update={"gate": _gate_node(latest)}))
        else:
            attached.append(lifecycle)
    return attached


def _gate_attention(gates: list[GateRecord]) -> list[AttentionItem]:
    """An open gate -- the operator must decide it (slice 6c)."""
    return [
        AttentionItem(
            id=f"gate:{gate.id}",
            kind="gate-open",
            severity="warn",
            lane="lifecycle",
            title=f"Gate — {gate.kind}",
            detail="awaiting your decision",
            lifecycleId=gate.lifecycleId,
            gateId=gate.id,
        )
        for gate in gates
        if gate.state == "open"
    ]


def _provider_attention(providers: list[ProviderNode]) -> list[AttentionItem]:
    """A down/crashed provider -- the 2026-06-09 invisible fire, made an alarm."""
    return [
        AttentionItem(
            id=f"provider-down:{provider.id}",
            kind="provider-down",
            severity="alarm",
            lane="repo",
            title=f"Provider {provider.id} down",
            detail=provider.state,
            waitSeconds=provider.snapshotStaleSeconds,
            providerId=provider.id,
        )
        for provider in providers
        if provider.ok is False or provider.state in _PROVIDER_DOWN
    ]


def _drift_attention(drift_snapshots: list[DriftSnapshotNode]) -> list[AttentionItem]:
    """Onboarding that needs a refresh decision (actionable drift)."""
    return [
        AttentionItem(
            id=f"actionable-drift:{drift.repository}",
            kind="actionable-drift",
            severity="warn",
            lane="repo",
            title=f"{drift.actionableCount} actionable drift",
            waitSeconds=drift.snapshotStaleSeconds,
            repoId=drift.repository,
        )
        for drift in drift_snapshots
        if drift.actionableCount > 0
    ]


def _setup_attention(setup_progress: list[SetupProgressNode]) -> list[AttentionItem]:
    """A provider-setup boot that failed or went stale mid-flight."""
    return [
        AttentionItem(
            id=f"failed-setup:{setup.group}",
            kind="failed-setup",
            severity="alarm" if setup.failedPhases else "warn",
            lane="worktree",
            title="Provider setup needs attention",
            detail=setup.currentPhase,
            waitSeconds=setup.heartbeatAgeSeconds,
            enclosure=setup.group,
        )
        for setup in setup_progress
        if setup.failedPhases or setup.state in {"failed", "stale"}
    ]


def _start_attention(start_progress: list[dict[str, Any]]) -> list[AttentionItem]:
    """A ``worktree_start`` gated before its contract was written -- the same master-caution the agent
    raises in chat (§9). Only blocked entries are alarms; a happy-path progress beat (no
    ``blockedReason``) is observability, not an alarm. Steady ``warn`` -- a human-choice gate, not a
    fault (faults flicker, §3.2)."""
    items: list[AttentionItem] = []
    for entry in start_progress:
        reason = _str_or_none(entry.get("blockedReason"))
        if reason is None:
            continue
        group = str(entry.get("worktreeGroup", ""))
        items.append(
            AttentionItem(
                id=f"blocked-start:{group.rsplit('/', 1)[-1]}",
                kind="blocked-start",
                severity="warn",
                lane="worktree",
                title="Worktree start blocked",
                detail=reason,
                enclosure=group or None,
                repoId=_str_or_none(entry.get("repoName")),
            )
        )
    return items


# --- engine room process map (slice 5e) --------------------------------------

_ENGINE_DOWN: frozenset[str] = frozenset({"stopped", "failed", "error", "down", "unreachable"})
_ENGINE_INDEXING: frozenset[str] = frozenset(
    {"indexing", "indexing-pending", "reindexing", "seeding"}
)
_SETUP_FAILED: frozenset[str] = frozenset({"failed", "failed-unchecked"})
_SETUP_DONE: frozenset[str] = frozenset({"ok", "complete", "prepared"})
_ROLE_ORDER: dict[str, int] = {"code": 0, "memory": 1}

# The ``lifecycle_guidance`` phases -> the process-map phase vocabulary (slice 5e; 05l adds ``abandoned``).
_GUIDANCE_PHASE: dict[str, str] = {
    "worktree-started": "worktree-started",
    "closeout-pending": "closeout-pending",
    "commit-approval-pending": "commit-approval-pending",
    "integration-pending": "integration-pending",
    "integration-blocked": "integration-blocked",
    "carryover-pending": "carryover-pending",
    "cleanup-pending": "cleanup-pending",
    "cleanup-completed": "completed",
    "abandoned": "abandoned",
}


def _is_disposed(fact: EngineProcessFacts) -> bool:
    """A cleaned-up or abandoned worktree whose runtime is gone (05l Gap B).

    ``cleanup`` reaching ``completed``/``abandoned`` means ``worktree_cleanup``/``worktree_abandon``
    already removed the worktrees + reclaimed the provider stack, so the enclosure is disposed. Drop
    it from the active engine-room set so the frontend (05k) animates the removal instead of
    rendering a fully-active phantom. ``cleanup-pending`` (cleanup not yet run) is intentionally kept,
    so the de-materialise beat still has a live node to animate.
    """
    return str(fact.contract.get("cleanup", "")) in {"completed", "abandoned"}


def build_engine_processes(
    facts: list[EngineProcessFacts],
    enclosures: list[EnclosureNode],
    providers: list[ProviderNode],
    setup_progress: list[SetupProgressNode],
    start_progress: list[dict[str, Any]] | None = None,
) -> list[EngineProcessNode]:
    """The enclosure-centered Engine Room process map (slice 5e), composed -- not read.

    One node per worktree contract, joining the recorded contract + status guidance (existence,
    dirty, base freshness, provider boot) with the worktree's isolated provider stack and its
    setup-progress boot sequence. ``start_progress`` (§5.4) adds a node for any start blocked
    *before* its contract was written -- the one start state the contract-keyed surface cannot
    see. Pure and deterministic (sorted by repo/task/id) so the served projection and sim replay
    stay byte-identical. The provider/setup join is on the worktree *group basename*:
    ``EnclosureNode.worktreeGroup`` is a full path, while ``ProviderNode.worktreeGroup`` and
    ``SetupProgressNode.group`` are the basename -- matching the existing dashboard join.
    """
    enclosure_by_id = {enclosure.enclosure: enclosure for enclosure in enclosures}
    providers_by_group: dict[str, list[ProviderNode]] = {}
    for provider in providers:
        if provider.scope == "worktree" and provider.worktreeGroup:
            providers_by_group.setdefault(provider.worktreeGroup, []).append(provider)
    for stack in providers_by_group.values():
        stack.sort(key=lambda provider: (_ROLE_ORDER.get(provider.role or "", 9), provider.id))
    setup_by_group = {node.group: node for node in setup_progress}
    nodes = [
        _engine_process(
            fact,
            enclosure_by_id.get(str(fact.contract.get("contract_path", ""))),
            providers_by_group,
            setup_by_group,
        )
        for fact in facts
        if not _is_disposed(fact)
    ]
    contract_groups = {
        str(fact.contract.get("worktree_group", "")).rsplit("/", 1)[-1] for fact in facts
    }
    for entry in start_progress or []:
        group = str(entry.get("worktreeGroup", "")).rsplit("/", 1)[-1]
        if group and group in contract_groups:
            continue  # the contract now anchors this enclosure; do not double-render
        nodes.append(_start_process_node(entry))
    nodes.sort(key=lambda node: (node.repoName, node.taskName, node.id))
    return nodes


# The pre-contract start phases (§5.4) -> the process-map phase vocabulary.
_START_PHASE: dict[str, str] = {
    "stale-base-blocked": "preflight",
    "memory-blocked": "memory-compatibility",
    "provider-blocked": "provider-setup",
    "preflight": "preflight",
    "code-worktree": "code-worktree",
    "contract-written": "contract-written",
}


def _start_process_node(entry: dict[str, Any]) -> EngineProcessNode:
    """Synthesize a node for a start blocked before its contract exists (slice 5e §5.4)."""
    completed = [str(phase) for phase in entry.get("completedPhases", []) or []]
    code_exists = "code-worktree" in completed
    blocked_reason = _str_or_none(entry.get("blockedReason"))
    phase = _START_PHASE.get(str(entry.get("phase", "")), "unknown")
    memory_mode = str(entry.get("memoryMode", ""))
    source_file = str(entry.get("sourceFile", ""))
    code_source = CommitRefNode(
        branch=_str_or_none(entry.get("codeSourceBranch")),
        commit=_str_or_none(entry.get("codeBaseCommit")),
        path=_str_or_none(entry.get("codeRepoPath")),
        factState="derived",
    )
    code_worktree = CommitRefNode(
        path=_str_or_none(entry.get("codeWorktree")),
        exists=code_exists,
        factState="observed" if code_exists else "planned",
    )
    memory_source = CommitRefNode(factState="planned") if memory_mode == "external" else None
    memory_worktree = (
        CommitRefNode(exists=False, factState="missing") if memory_mode == "external" else None
    )
    edges = [
        EngineProcessEdge(
            id="code-worktree-add",
            fromNode="code-source",
            toNode="code-worktree",
            kind="worktree-add",
            state="complete" if code_exists else "planned",
            label="add code worktree",
        ),
        EngineProcessEdge(
            id="cgc-seed",
            fromNode="code-worktree",
            toNode="cgc-engine",
            kind="cgc-seed",
            state="planned",
            label="CGC seed",
        ),
    ]
    if memory_mode == "external":
        edges.append(
            EngineProcessEdge(
                id="memory-worktree-add",
                fromNode="memory-source",
                toNode="memory-worktree",
                kind="ledger-map",
                state="blocked" if blocked_reason else "planned",
                label="ledger-map + memory worktree",
            )
        )
        edges.append(
            EngineProcessEdge(
                id="grepai-clone",
                fromNode="memory-worktree",
                toNode="grepai-engine",
                kind="grepai-clone",
                state="planned",
                label="GrepAI clone",
            )
        )
    missing = [f"start gated at {phase} — contract not yet written"]
    if blocked_reason:
        missing.append(blocked_reason)
    choices = [str(choice) for choice in entry.get("choices", []) or []]
    return EngineProcessNode(
        id=source_file or f"start:{entry.get('worktreeName', '')}",
        enclosure=source_file,
        worktreeGroup=str(entry.get("worktreeGroup", "")),
        taskId=str(entry.get("worktreeName", "")).upper(),
        taskName=str(entry.get("taskName", "")),
        repoName=str(entry.get("repoName", "")),
        phase=phase,
        health="blocked" if blocked_reason else "running",
        codeSource=code_source,
        codeWorktree=code_worktree,
        memoryMode=memory_mode,
        memorySource=memory_source,
        memoryWorktree=memory_worktree,
        humanReviewStatus="pending-review",
        closeoutStatus="not-started",
        integrationStatus="not-started",
        cleanup="pending",
        providers=[],
        edges=edges,
        actions=[],
        nextAction=choices[0] if choices else None,
        summary=blocked_reason or "Worktree start in progress (pre-contract).",
        missingFacts=missing,
        sourceFiles=[source_file] if source_file else [],
    )


def _engine_process(
    fact: EngineProcessFacts,
    enclosure: EnclosureNode | None,
    providers_by_group: dict[str, list[ProviderNode]],
    setup_by_group: dict[str, SetupProgressNode],
) -> EngineProcessNode:
    cp = fact.contract
    status = fact.status or {}
    guidance = fact.guidance
    enclosure_id = str(cp.get("contract_path", ""))
    group_full = str(cp.get("worktree_group", ""))
    group_key = group_full.rsplit("/", 1)[-1]
    memory_mode = str(cp.get("memory_mode", ""))

    freshness = _as_dict(status.get("freshness"))
    behind_official = freshness.get("state") == "behind-official"
    code_fresh = _as_dict(freshness.get("code"))
    memory_fresh = _as_dict(freshness.get("memory"))

    code_source = CommitRefNode(
        branch=_str_or_none(cp.get("code_source_branch")),
        commit=_str_or_none(cp.get("code_base_commit")),
        path=_str_or_none(cp.get("code_repo_path")),
        behindSource=_int_or_none(code_fresh.get("baseBehindSource")),
        factState="observed" if status else "derived",
    )
    code_exists = status.get("code_worktree_exists")
    code_worktree = CommitRefNode(
        branch=_str_or_none(cp.get("code_work_branch")),
        commit=_str_or_none(cp.get("code_commit")) or _str_or_none(cp.get("code_base_commit")),
        path=_str_or_none(cp.get("code_worktree")),
        exists=code_exists if isinstance(code_exists, bool) else None,
        dirty=_bool_or_none(status.get("code_worktree_dirty")),
        factState=_ref_fact_state(bool(status), code_exists),
    )

    memory_source: CommitRefNode | None = None
    memory_worktree: CommitRefNode | None = None
    ledger_path: str | None = None
    if memory_mode == "external":
        memory_exists = status.get("memory_worktree_exists")
        memory_source = CommitRefNode(
            branch=_str_or_none(cp.get("memory_source_branch")),
            commit=_str_or_none(cp.get("memory_base_commit")),
            path=_str_or_none(cp.get("memory_repo_path")),
            behindSource=_int_or_none(memory_fresh.get("baseBehindSource")),
            factState="observed" if status else "derived",
        )
        memory_worktree = CommitRefNode(
            branch=_str_or_none(cp.get("memory_work_branch")),
            commit=_str_or_none(cp.get("memory_content_commit"))
            or _str_or_none(cp.get("memory_base_commit")),
            path=_str_or_none(cp.get("memory_worktree")),
            exists=memory_exists if isinstance(memory_exists, bool) else None,
            dirty=_bool_or_none(status.get("memory_worktree_dirty")),
            factState=_ref_fact_state(bool(status), memory_exists),
        )
        ledger_path = _str_or_none(cp.get("ledger_path"))

    setup_node = setup_by_group.get(group_key)
    prov_status = _as_dict(status.get("providers"))
    setup_state = setup_node.state if setup_node else _str_or_none(prov_status.get("state"))
    current_phase = setup_node.currentPhase if setup_node else None
    heartbeat = setup_node.heartbeatAgeSeconds if setup_node else None
    failed_phases = (
        list(setup_node.failedPhases)
        if setup_node
        else [str(line) for line in prov_status.get("failedPhases", []) or []]
    )
    completed_phases = [str(line) for line in prov_status.get("completedPhases", []) or []]
    seed_fallback = bool(prov_status.get("seedFallback"))
    retry = prov_status.get("retryArgs")
    retry_args = retry if isinstance(retry, dict) else None

    observed_providers = providers_by_group.get(group_key, [])
    boot = _provider_boot_nodes(
        observed_providers,
        group_key=group_key,
        memory_mode=memory_mode,
        setup_state=setup_state,
    )
    has_provider_surface = bool(setup_node or observed_providers)

    phase = _process_phase(guidance.get("phase"), setup_state, behind_official=behind_official)
    health = _process_health(phase, setup_state, failed_phases)
    edges = _process_edges(
        memory_mode=memory_mode,
        code_worktree=code_worktree,
        memory_worktree=memory_worktree,
        boot=boot,
        setup_state=setup_state,
        failed_phases=failed_phases,
        behind_official=behind_official,
    )

    return EngineProcessNode(
        id=enclosure_id,
        enclosure=enclosure_id,
        worktreeGroup=group_full,
        taskId=str(cp.get("task_id", "")),
        leafId=str(cp.get("leaf_id", "")),
        taskName=str(cp.get("task_name", "")),
        repoName=str(cp.get("repo_name", "")),
        lifecycleId=_str_or_none(cp.get("lifecycle_id")),
        phase=phase,
        health=health,
        codeSource=code_source,
        codeWorktree=code_worktree,
        memoryMode=memory_mode,
        memorySource=memory_source,
        memoryWorktree=memory_worktree,
        ledgerPath=ledger_path,
        ledgerRows=fact.ledger_rows,
        ledgerRowCount=fact.ledger_row_count,
        humanReviewStatus=str(cp.get("human_review_status", "")),
        closeoutStatus=str(cp.get("closeout_status", "")),
        integrationStatus=str(cp.get("integration_status", "")),
        integrationStrategy=_str_or_none(cp.get("integration_strategy")),
        cleanup=str(cp.get("cleanup", "")),
        carryoverDoneAt=_str_or_none(status.get("carryoverDoneAt")),
        setupState=setup_state,
        currentPhase=current_phase,
        completedPhases=completed_phases,
        failedPhases=failed_phases,
        heartbeatAgeSeconds=heartbeat,
        seedFallback=seed_fallback,
        retryArgs=retry_args,
        providers=boot,
        edges=edges,
        landing=[LandingRefNode(**ref) for ref in (status.get("landing") or [])],
        actions=list(enclosure.actions) if enclosure else [],
        nextAction=_str_or_none(guidance.get("nextOperation")),
        summary=str(guidance.get("summary", "")),
        missingFacts=_missing_facts(
            has_status=bool(status),
            contract=cp,
            memory_mode=memory_mode,
            setup_state=setup_state,
            boot=boot,
        ),
        sourceFiles=_source_files(cp, memory_mode, group_full, has_setup=has_provider_surface),
    )


def _provider_boot_nodes(
    providers: list[ProviderNode],
    *,
    group_key: str,
    memory_mode: str,
    setup_state: str | None,
) -> list[ProviderBootNode]:
    boot = [
        ProviderBootNode(
            id=provider.id,
            role=provider.role or "code",
            runtimeState=_engine_runtime_state(provider),
        )
        for provider in providers
    ]
    if setup_state in {"running", "blocked"} and not boot:
        return boot
    observed_roles = {node.role for node in boot}
    for role in _expected_provider_roles(memory_mode):
        if role not in observed_roles:
            boot.append(
                ProviderBootNode(
                    id=f"missing-{role}@{group_key}",
                    role=role,
                    runtimeState="missing",
                    factState="missing",
                )
            )
    boot.sort(key=lambda node: (_ROLE_ORDER.get(node.role, 9), node.id))
    return boot


def _expected_provider_roles(memory_mode: str) -> list[str]:
    roles = ["code"]
    if memory_mode == "external":
        roles.append("memory")
    return roles


def _engine_runtime_state(provider: ProviderNode) -> str:
    """Bucket a worktree engine's runtime state (mirrors the dashboard ``engineState``)."""
    if provider.ok is False or provider.state in _ENGINE_DOWN:
        return "down"
    if provider.indexingState in _ENGINE_INDEXING:
        return "indexing"
    if provider.state == "configured":
        return "configured"
    return "nominal"


def _ref_fact_state(has_status: bool, exists: object) -> str:
    """observed = on disk; derived = recorded but absent; missing = unobservable."""
    if not has_status:
        return "missing"
    if exists is True:
        return "observed"
    if exists is False:
        return "derived"
    return "missing"


def _process_phase(
    guidance_phase: object, setup_state: str | None, *, behind_official: bool
) -> str:
    base = _GUIDANCE_PHASE.get(str(guidance_phase or ""), "unknown")
    if base != "worktree-started":
        return base
    if behind_official:
        return "sync-needed"
    if setup_state in {"running", "stale"} or setup_state in _SETUP_FAILED:
        return "provider-setup"
    return base


def _process_health(phase: str, setup_state: str | None, failed_phases: list[str]) -> str:
    if failed_phases or setup_state in _SETUP_FAILED:
        return "failed"
    if setup_state == "stale":
        return "stale"
    if setup_state == "running":
        return "running"
    if phase in {"commit-approval-pending", "integration-blocked", "sync-needed"}:
        return "blocked"
    if phase == "completed":
        return "complete"
    return "nominal"


def _process_edges(
    *,
    memory_mode: str,
    code_worktree: CommitRefNode,
    memory_worktree: CommitRefNode | None,
    boot: list[ProviderBootNode],
    setup_state: str | None,
    failed_phases: list[str],
    behind_official: bool,
) -> list[EngineProcessEdge]:
    """The core conduits: worktree materialization, provider seed/clone, and a sync feedback edge."""
    edges = [
        EngineProcessEdge(
            id="code-worktree-add",
            fromNode="code-source",
            toNode="code-worktree",
            kind="worktree-add",
            state=_materialize_edge_state(code_worktree),
            label="add code worktree",
        ),
        EngineProcessEdge(
            id="cgc-seed",
            fromNode="code-worktree",
            toNode="cgc-engine",
            kind="cgc-seed",
            state=_seed_edge_state(
                setup_state,
                failed_phases,
                any(node.role == "code" and node.factState != "missing" for node in boot),
                "codegraphcontext",
            ),
            label="CGC seed",
        ),
    ]
    if memory_mode == "external":
        edges.append(
            EngineProcessEdge(
                id="memory-worktree-add",
                fromNode="memory-source",
                toNode="memory-worktree",
                kind="ledger-map",
                state=_materialize_edge_state(memory_worktree),
                label="ledger-map + memory worktree",
            )
        )
        edges.append(
            EngineProcessEdge(
                id="grepai-clone",
                fromNode="memory-worktree",
                toNode="grepai-engine",
                kind="grepai-clone",
                state=_seed_edge_state(
                    setup_state,
                    failed_phases,
                    any(node.role == "memory" and node.factState != "missing" for node in boot),
                    "grepai",
                ),
                label="GrepAI clone",
            )
        )
    if behind_official:
        edges.append(
            EngineProcessEdge(
                id="sync",
                fromNode="official-line",
                toNode="code-worktree",
                kind="sync",
                state="blocked",
                label="official line moved — sync",
            )
        )
    return edges


def _materialize_edge_state(ref: CommitRefNode | None) -> str:
    if ref is None:
        return "skipped"
    if ref.exists is True:
        return "complete"
    if ref.exists is None:
        return "unknown"
    return "planned"


def _seed_edge_state(
    setup_state: str | None, failed_phases: list[str], has_engine: bool, token: str
) -> str:
    if any(token in line for line in failed_phases):
        return "failed"
    if setup_state == "running":
        return "running"
    if setup_state == "stale":
        return "stale"
    if setup_state in _SETUP_FAILED:
        return "failed"
    if has_engine and (setup_state is None or setup_state in _SETUP_DONE):
        return "complete"
    if setup_state == "skipped":
        return "skipped"
    return "planned"


def _missing_facts(
    *,
    has_status: bool,
    contract: dict[str, Any],
    memory_mode: str,
    setup_state: str | None,
    boot: list[ProviderBootNode],
) -> list[str]:
    missing: list[str] = []
    if not has_status:
        missing.append(
            "worktree existence, dirty, and base freshness not observed (status probe unavailable)"
        )
    if not contract.get("code_base_commit"):
        missing.append("code base commit not recorded in the contract")
    if memory_mode == "external" and not contract.get("ledger_path"):
        missing.append("memory ledger path not recorded")
    if setup_state is None and all(node.factState == "missing" for node in boot):
        missing.append("provider setup not observed for this worktree group")
    missing_roles = [node.role for node in boot if node.factState == "missing"]
    if missing_roles:
        missing.append(
            "provider runtime not observed for this worktree group: "
            + ", ".join(sorted(missing_roles, key=lambda role: _ROLE_ORDER.get(role, 9)))
        )
    return missing


def _source_files(
    contract: dict[str, Any], memory_mode: str, group_full: str, *, has_setup: bool
) -> list[str]:
    sources = [str(contract.get("contract_path", ""))]
    if contract.get("code_worktree"):
        sources.append(str(contract["code_worktree"]))
    if memory_mode == "external" and contract.get("memory_worktree"):
        sources.append(str(contract["memory_worktree"]))
    if has_setup and group_full:
        sources.append(f"{group_full}/provider-runtime/setup-progress.json")
    return [path for path in sources if path]


def _as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _int_or_none(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    return value if isinstance(value, int) else None


def _bool_or_none(value: object) -> bool | None:
    return value if isinstance(value, bool) else None
