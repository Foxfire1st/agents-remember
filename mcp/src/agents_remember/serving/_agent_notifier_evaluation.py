from __future__ import annotations

from datetime import datetime

from agents_remember.controlplane.interaction_retention import INBOX_PENDING_TTL_SECONDS
from agents_remember.controlplane.operator_inbox_records import OperatorInboxEntry
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.controlplane.signal_routing import (
    TaskHierarchy,
    derive_row_owner,
    derive_signal_owner,
    is_seat_dead,
    task_chain_has_progress,
)
from agents_remember.errors import SeatOccupancyError, StructuralRoutingError
from agents_remember.models.terminal_catalog import TerminalCatalogEntry
from agents_remember.serving.agent_notifier_models import AgentNotifierContext, AgentNotifierFinding
from agents_remember.serving.agent_notifier_models import SweepState as _SweepState
from agents_remember.serving.pane_signals import classify_pane_signal
from agents_remember.serving.ports import TerminalCatalogPort
from agents_remember.serving.state_signals import (
    evaluate_boundary_drain_findings,
    evaluate_compound_idle_findings,
    evaluate_non_reaction_findings,
    evaluate_state_signal_findings,
    state_signal_held_on_boundary,
)
from agents_remember.serving.terminal_paste import capture_pane as default_capture_pane
from agents_remember.tasks.document_refs import TaskDocumentTopology

PERSISTENT_FAILURE_ATTEMPTS = 5
"""Attempt count past which a live-but-silent row resolves terminal ``unresolved`` (N3)."""

REBIND_GRACE_SECONDS = 300.0
"""N2 grace: a pending row addressed to a dead seat rebinds to the same-leaf+role replacement
that exists or appears within this window, else resolves terminal-visible-then-compacted."""

# 260713-TES-L1 rename window: the seat-liveness ask prefix changed from "Supervisor observed
# seat-liveness:" to "Agent notifier observed seat-liveness:". Both prefixes name the SAME
# relay-authored signal identity; legacy pending rows carry the old prefix and must still be
# found by new-format re-fires (coalescing/renewal) and by chain-progress suppression.
SEAT_LIVENESS_ASK_PREFIXES = (
    "Agent notifier observed seat-liveness:",
    "Supervisor observed seat-liveness:",
)
# --- R2: predicates ----------------------------------------------------------------------------


def evaluate_pane_findings(
    catalog: TerminalCatalogPort, *, pane_capturer=default_capture_pane
) -> list[AgentNotifierFinding]:
    """Diagnostic-only pane classifications; the production sweep does not act on them."""
    findings: list[AgentNotifierFinding] = []
    for entry in catalog.list():
        if entry.kind != "harness" or entry.status != "running":
            continue
        classification = classify_pane_signal(pane_capturer(entry.tmux_name), harness=entry.harness)
        if classification.signal == "normal":
            continue
        findings.append(
            AgentNotifierFinding(
                kind="pane-signal",
                detail=classification.signal,
                session_id=entry.id,
                task_document_ref=entry.binding_task_document_ref,
                seat_role=entry.binding_role,
            )
        )
    return findings


# 260731-EFA-L7 R10: verbatim L7 split (L7-OQ1 Option A serving scope); unchanged edge branch, out of this leaf's behavior scope (mcp/src/agents_remember/serving/_agent_notifier_evaluation.py:125).
def evaluate_inbox_findings(  # pragma: no cover
    store: OperatorInboxStore,
    *,
    now: datetime,
    rate_limit_seconds: float | None = None,
    current: dict[str, OperatorInboxEntry] | None = None,
    limit: int | None = None,
) -> list[AgentNotifierFinding]:
    """R2d: unacked-row redelivery due (past backoff, clear of the per-target rate limit)."""
    entries = store.list_redeliverable(
        now=now,
        rate_limit_seconds=rate_limit_seconds,
        current=current,
    )
    if limit is not None:
        entries = entries[:limit]
    return [
        AgentNotifierFinding(
            kind="inbox-redeliverable",
            detail=entry.messageKind,
            session_id=entry.agentId,
            task_document_ref=entry.taskDocumentRef,
            seat_role=entry.seatRole,
            source_id=entry.id,
        )
        for entry in entries
    ]


def _row_target_dead(catalog: TerminalCatalogPort, entry: OperatorInboxEntry) -> bool:
    """Whether a pending row is addressed to a seat the rebind machinery owns."""
    return entry.agentId is not None and is_seat_dead(catalog, entry.agentId)


def _row_dead_since(
    catalog: TerminalCatalogPort,
    row: OperatorInboxEntry,
) -> datetime | None:
    """When the row's addressed seat stopped being live, or a bounded fallback anchor.

    Prefers explicit terminal stamps (retired/landed/terminated); an exited-but-unstamped
    catalog row falls back to its last turn-state change, and a seat with no catalog trace at
    all falls back to the row's own delivery timeline so the grace window stays bounded.
    """
    seat = catalog.get(row.agentId) if row.agentId is not None else None
    stamp: str | None = None
    if seat is not None:
        if seat.status == "running":
            return None
        stamp = (
            seat.terminated_at or seat.retired_at or seat.landed_at or seat.turn_state_changed_at
        )
    stamp = stamp or row.lastAttemptAt or row.createdAt
    try:
        return datetime.fromisoformat(stamp)
    except ValueError:
        return None


def evaluate_rebind_findings(
    catalog: TerminalCatalogPort,
    topology: TaskHierarchy,
    *,
    current: dict[str, OperatorInboxEntry] | None = None,
    now: datetime,
    grace_seconds: float = REBIND_GRACE_SECONDS,
) -> list[AgentNotifierFinding]:
    """N14 sweep-time rebinding: pending rows to a dead/replaced seat.

    A live same-leaf+role replacement produces ``rebind-due``; no replacement within the grace
    window produces ``rebind-expired`` (terminal-visible, then compacted). ``dispatch-brief``
    rows never rebind (exact-pinned) and are not evaluated here.
    """
    findings: list[AgentNotifierFinding] = []
    entries = current or {}
    for row in entries.values():
        if row.state != "pending" or row.messageKind == "dispatch-brief" or row.agentId is None:
            continue
        if not is_seat_dead(catalog, row.agentId):
            continue
        try:
            owner = derive_row_owner(catalog, topology, row)
        except StructuralRoutingError:
            # One ambiguous replacement chain fences only this row from rebind evaluation.
            continue
        if owner.agent_id is not None and owner.agent_id != row.agentId:
            findings.append(
                AgentNotifierFinding(
                    kind="rebind-due",
                    detail="replacement-owner",
                    session_id=row.agentId,
                    task_document_ref=row.taskDocumentRef,
                    seat_role=row.seatRole,
                    source_id=row.id,
                )
            )
            continue
        dead_since = _row_dead_since(catalog, row)
        if dead_since is not None and (now - dead_since).total_seconds() >= grace_seconds:
            findings.append(
                AgentNotifierFinding(
                    kind="rebind-expired",
                    detail="rebind-grace-expired",
                    session_id=row.agentId,
                    task_document_ref=row.taskDocumentRef,
                    seat_role=row.seatRole,
                    source_id=row.id,
                )
            )
    return findings


def evaluate_pending_expiry_findings(
    current: dict[str, OperatorInboxEntry],
    *,
    now: datetime,
    ttl_seconds: float = INBOX_PENDING_TTL_SECONDS,
) -> list[AgentNotifierFinding]:
    """Pending rows past the retention boundary: resolve ``expired`` before compaction (D6)."""
    findings: list[AgentNotifierFinding] = []
    for row in current.values():
        if row.state != "pending":
            continue
        age = _age_seconds(row.createdAt, now)
        if age is not None and age >= ttl_seconds:
            findings.append(
                AgentNotifierFinding(
                    kind="inbox-ttl-expired",
                    detail="pending-ttl-expired",
                    session_id=row.agentId,
                    task_document_ref=row.taskDocumentRef,
                    seat_role=row.seatRole,
                    source_id=row.id,
                )
            )
    return findings


# 260731-EFA-L7 R10: verbatim L7 split (L7-OQ1 Option A serving scope); unchanged edge branch, out of this leaf's behavior scope (mcp/src/agents_remember/serving/_agent_notifier_evaluation.py:186).
def _age_seconds(iso_text: str, now: datetime) -> float | None:  # pragma: no cover
    try:
        return (now - datetime.fromisoformat(iso_text)).total_seconds()
    except ValueError:
        return None


def _inactivity_signal_chain_progressed(
    catalog: TerminalCatalogPort,
    topology: TaskHierarchy,
    entry: OperatorInboxEntry,
) -> bool:
    """Whether real leaf-chain progress invalidated one agent-notifier inactivity root cause."""
    return bool(
        # Both values are the same relay-authored inactivity row until the rename window closes.
        entry.createdBy in {"supervisor", "agent-notifier"}
        and entry.ask.startswith(SEAT_LIVENESS_ASK_PREFIXES)
        and entry.subjectTaskDocumentRef is not None
        and task_chain_has_progress(
            catalog,
            topology,
            task_document_ref=entry.subjectTaskDocumentRef,
            subject_agent_id=entry.subjectAgentId,
            since=entry.createdAt,
        )
    )


def _seat_liveness_ask_identity(ask: str) -> str:
    """Canonical identity of a seat-liveness ask across the rename window.

    Both ask prefixes are one signal identity: a new-format re-fire must renew (and a
    chain-progress check must match) a pre-window pending row that carries the legacy prefix.
    Asks that do not start with either prefix are returned unchanged and still compare exactly.
    """
    for prefix in SEAT_LIVENESS_ASK_PREFIXES:
        if ask.startswith(prefix):
            return f"seat-liveness:{ask[len(prefix) :]}"
    return ask


# 260731-EFA-L7 R10: verbatim L7 split (L7-OQ1 Option A serving scope); unchanged edge branch, out of this leaf's behavior scope (mcp/src/agents_remember/serving/_agent_notifier_evaluation.py:223).
def _stale_turn_state_due(  # pragma: no cover
    catalog: TerminalCatalogPort,
    topology: TaskHierarchy,
    entry: TerminalCatalogEntry,
    *,
    now: datetime,
    stale_seconds: float,
) -> bool:
    if entry.turn_state != "stale" or entry.turn_state_changed_at is None:
        return False
    age = _age_seconds(entry.turn_state_changed_at, now)
    if age is None or age < stale_seconds:
        return False
    if entry.binding_task_document_ref is None:
        return True
    return not task_chain_has_progress(
        catalog,
        topology,
        task_document_ref=entry.binding_task_document_ref,
        subject_agent_id=entry.id,
        since=entry.turn_state_changed_at,
    )


def _safe_stale_turn_state_due(
    catalog: TerminalCatalogPort,
    topology: TaskHierarchy,
    entry: TerminalCatalogEntry,
    *,
    now: datetime,
    stale_seconds: float,
) -> bool:
    """Fence one structurally invalid seat without aborting the liveness scan."""

    try:
        return _stale_turn_state_due(
            catalog,
            topology,
            entry,
            now=now,
            stale_seconds=stale_seconds,
        )
    except (SeatOccupancyError, StructuralRoutingError):
        return False


def evaluate_seat_liveness_findings(
    catalog: TerminalCatalogPort,
    topology: TaskHierarchy,
    *,
    now: datetime,
    stale_seconds: float,
) -> list[AgentNotifierFinding]:
    """R2e: the L5 hysteresis + L8 turn-state join, with graceful degradation.

    A row already classified by the L8 turn-state prober (``turn_state``/``turn_state_changed_at``
    set) fires when it has sat ``stale`` past ``stale_seconds``. A row the L8 prober has never
    classified (legacy/degraded) falls back to the L5 primitive alone: any recorded liveness
    failure on an otherwise-``running`` row is itself the signal -- ``has_session`` (the row is
    still ``running``) plus catalog status (failures counted but not yet exit-marked).
    """
    findings: list[AgentNotifierFinding] = []
    for entry in catalog.list():
        if entry.kind != "harness" or entry.status != "running":
            continue
        if entry.turn_state is not None and entry.turn_state_changed_at is not None:
            if not _safe_stale_turn_state_due(
                catalog, topology, entry, now=now, stale_seconds=stale_seconds
            ):
                continue
            findings.append(
                AgentNotifierFinding(
                    kind="seat-liveness",
                    detail="turn-state-stale",
                    session_id=entry.id,
                    task_document_ref=entry.binding_task_document_ref,
                    seat_role=entry.binding_role,
                )
            )
        elif entry.liveness_failures > 0:
            findings.append(
                AgentNotifierFinding(
                    kind="seat-liveness",
                    detail="liveness-degraded",
                    session_id=entry.id,
                    task_document_ref=entry.binding_task_document_ref,
                    seat_role=entry.binding_role,
                )
            )
    return findings


def evaluate_dead_upstream_findings(
    catalog: TerminalCatalogPort, topology: TaskHierarchy
) -> list[AgentNotifierFinding]:
    """Every live subordinate whose task-structural parent seat has no live occupant."""
    findings: list[AgentNotifierFinding] = []
    for entry in catalog.list():
        if entry.kind != "harness" or entry.status != "running":
            continue
        if entry.binding_role not in ("worker", "reviewer", "curator", "manager"):
            continue
        try:
            owner = derive_signal_owner(
                catalog,
                topology,
                sender_agent_id=entry.id,
                message_kind="state-signal",
            )
        except StructuralRoutingError:
            # A corrupt upstream seat must not suppress unrelated findings or the heartbeat.
            continue
        if owner.task_document_ref is None or owner.agent_id is not None:
            continue
        findings.append(
            AgentNotifierFinding(
                kind="dead-upstream",
                detail="structural-owner-missing",
                session_id=entry.id,
                task_document_ref=entry.binding_task_document_ref,
                seat_role=entry.binding_role,
            )
        )
    return findings


# 260731-EFA-L7 R10: verbatim L7 split (L7-OQ1 Option A serving scope); unchanged edge branch, out of this leaf's behavior scope (mcp/src/agents_remember/serving/_agent_notifier_evaluation.py:367).
def evaluate_predicates(  # pragma: no cover
    ctx: AgentNotifierContext, *, now: datetime, sweep: _SweepState | None = None
) -> list[AgentNotifierFinding]:
    """Run every fact-relay predicate over its store -- the sweep's full finding set.

    The relay interprets nothing: seat-liveness and dead-upstream findings are
    ``escalationBudget``-shed per sweep (load-shed, level-triggered re-fire), the twin of
    ``redeliverBudget``. Expectation rows are never evaluated here -- they are an
    owner-visible deadline surface only.
    """
    findings: list[AgentNotifierFinding] = []
    topology = TaskDocumentTopology(ctx.coordination_root)
    inbox_current = sweep.inbox_current if sweep is not None else None
    if sweep is None:
        findings += evaluate_inbox_findings(
            ctx.inbox_store,
            now=now,
            rate_limit_seconds=ctx.redeliver_rate_limit_seconds,
            current=inbox_current,
            limit=ctx.redeliver_budget,
        )
    else:
        budgeted = [
            entry
            for entry in sweep.redeliverable_entries
            if _redelivery_route_is_actionable(ctx.catalog, topology, entry)
        ][: sweep.redeliver_budget]
        findings += [
            AgentNotifierFinding(
                kind="inbox-redeliverable",
                detail=entry.messageKind,
                session_id=entry.agentId,
                task_document_ref=entry.taskDocumentRef,
                source_id=entry.id,
            )
            for entry in budgeted
        ]
    owner_signal_findings = evaluate_seat_liveness_findings(
        ctx.catalog, topology, now=now, stale_seconds=ctx.stale_seat_seconds
    ) + evaluate_dead_upstream_findings(ctx.catalog, topology)
    findings += owner_signal_findings[: max(1, ctx.escalation_budget)]
    entries = inbox_current if inbox_current is not None else ctx.inbox_store.current()
    findings += evaluate_rebind_findings(ctx.catalog, topology, current=entries, now=now)
    findings += evaluate_pending_expiry_findings(entries, now=now)
    findings += evaluate_state_signal_findings(ctx.catalog, topology)
    findings += evaluate_compound_idle_findings(ctx.catalog, topology)
    findings += evaluate_non_reaction_findings(ctx.catalog, topology, ctx.inbox_store, now=now)
    findings += evaluate_boundary_drain_findings(
        ctx.catalog,
        sweep.inbox_current if sweep is not None else {},
        limit=max(1, ctx.redeliver_budget),
    )
    return findings


def _redelivery_route_is_actionable(
    catalog: TerminalCatalogPort,
    topology: TaskHierarchy,
    entry: OperatorInboxEntry,
) -> bool:
    """Return false when only this row's structural route cannot be proven."""

    try:
        return (
            not _inactivity_signal_chain_progressed(catalog, topology, entry)
            and not state_signal_held_on_boundary(catalog, entry)
            and not _row_target_dead(catalog, entry)
        )
    except (SeatOccupancyError, StructuralRoutingError):
        return False
