from __future__ import annotations

import contextlib
from collections.abc import Callable
from datetime import datetime

from agents_remember.controlplane import operator_inbox_transitions as inbox_transitions
from agents_remember.controlplane.agent_notifier_signals import (
    AgentNotifierSignalKey,
    AgentNotifierSignalRecord,
    AgentNotifierSignalTarget,
)
from agents_remember.controlplane.escalation_ladder import next_step, seat_is_suspect
from agents_remember.controlplane.operator_inbox_records import (
    InboxOwner,
    OperatorInboxEntry,
)
from agents_remember.controlplane.operator_inbox_transitions import RungAdvance
from agents_remember.controlplane.orchestration_nudges import (
    NudgeReason,
    OrchestrationNudgeRecord,
    nudge_message,
)
from agents_remember.controlplane.orphan_policy import find_orphaned_workers
from agents_remember.controlplane.signal_routing import (
    derive_leaf_manager_owner,
    derive_signal_owner,
)
from agents_remember.observer.events import Event, now_iso
from agents_remember.observer.ulid import new_ulid
from agents_remember.serving._agent_notifier_evaluation import (
    PERSISTENT_FAILURE_ATTEMPTS,
    _ladder_terminal_and_dead,
)
from agents_remember.serving.agent_notifier_models import (
    AgentNotifierActionResult,
    AgentNotifierContext,
    AgentNotifierFinding,
)
from agents_remember.serving.agent_notifier_models import SweepState as _SweepState
from agents_remember.serving.dispatch_brief import (
    DISPATCH_BRIEF_KIND,
    dispatch_stays_on_exact_session,
    fulfill_briefed_expectation,
)
from agents_remember.serving.hosted_session_runtime import HostedSessionRuntime
from agents_remember.serving.inbox_delivery import (
    DEFAULT_DELIVERY_ADMISSION,
    DeliveryAdmission,
    InboxDeliveryLog,
    RedeliveryFloor,
    deliver_inbox_entry,
)
from agents_remember.serving.owner_signals import (
    OwnerSignal,
    OwnerSignalOptions,
    _post_owner_signal,
)
from agents_remember.serving.retire import SeatClosure, retire_entry
from agents_remember.serving.seat_turn_truth import (
    record_non_reaction_emitted,
    record_state_signal_emitted,
)
from agents_remember.serving.state_signals import (
    non_reaction_response,
    state_signal_response,
)

# The one event-rename seam for the compatibility window (260713-TES-L1): every agent-notifier
# event is emitted under BOTH the current and the legacy name so observer-river consumers and
# dashboards on the old name keep working. Remove the legacy prefix and the duplicate append
# with the window; the current name is the only kind afterward.
AGENT_NOTIFIER_EVENT_PREFIX = "orchestration.agent-notifier."
LEGACY_SUPERVISOR_EVENT_PREFIX = "orchestration.supervisor."


def _log_event(ctx: AgentNotifierContext, kind: str, data: dict[str, object]) -> None:
    ctx.event_store.append(
        Event(id=new_ulid(), ts=now_iso(), kind=kind, trust="observed", actor="system", data=data)
    )
    if kind.startswith(AGENT_NOTIFIER_EVENT_PREFIX):
        legacy_kind = LEGACY_SUPERVISOR_EVENT_PREFIX + kind[len(AGENT_NOTIFIER_EVENT_PREFIX) :]
        ctx.event_store.append(
            Event(
                id=new_ulid(),
                ts=now_iso(),
                kind=legacy_kind,
                trust="observed",
                actor="system",
                data=data,
            )
        )


# 260731-EFA-L7 R10: verbatim L7 split (L7-OQ1 Option A serving scope); unchanged edge branch, out of this leaf's behavior scope (mcp/src/agents_remember/serving/_agent_notifier_actions.py:71).
def _redeliver(  # pragma: no cover
    ctx: AgentNotifierContext,
    finding: AgentNotifierFinding,
    *,
    now: datetime,
    sweep: _SweepState,
) -> AgentNotifierActionResult:
    if finding.source_id is None:
        return AgentNotifierActionResult("redeliver", finding, "skipped", "no source entry id")
    entry = sweep.inbox_current.get(finding.source_id)
    if entry is None or entry.state != "pending":
        return AgentNotifierActionResult("redeliver", finding, "skipped", "entry not pending")
    if _ladder_terminal_and_dead(ctx.catalog, entry):
        return _resolve_ladder_terminal(ctx, finding, now=now, sweep=sweep)
    admission = (
        DeliveryAdmission(boundary=True)
        if entry.messageKind == "state-signal"
        else DEFAULT_DELIVERY_ADMISSION
    )
    updated = deliver_inbox_entry(
        InboxDeliveryLog(
            store=ctx.inbox_store,
            entry=entry,
            at=now.isoformat(),
            floor=RedeliveryFloor(
                current=sweep.inbox_current, seconds=ctx.redeliver_rate_limit_seconds
            ),
        ),
        sessions=HostedSessionRuntime(catalog=ctx.catalog, host=ctx.host),
        paster=ctx.paster,
        admission=admission,
    )
    sweep.remember(updated)
    fulfill_briefed_expectation(
        ctx.expectation_store,
        updated,
        current=sweep.expectation_current,
    )
    _log_event(
        ctx,
        "orchestration.agent-notifier.redeliver",
        {
            "entryId": entry.id,
            "deliveryState": updated.deliveryState,
            "attemptCount": updated.attemptCount,
            "sessionId": finding.session_id,
        },
    )
    if (
        entry.messageKind != DISPATCH_BRIEF_KIND
        and updated.deliveryState != "delivered"
        and updated.attemptCount >= PERSISTENT_FAILURE_ATTEMPTS
    ):
        _escalate_inbox_entry(ctx, updated.id, now=now, sweep=sweep)
    return AgentNotifierActionResult(
        "redeliver", finding, updated.deliveryState, updated.deliveryDetail
    )


# 260731-EFA-L7 R10: verbatim L7 split (L7-OQ1 Option A serving scope); unchanged edge branch, out of this leaf's behavior scope (mcp/src/agents_remember/serving/_agent_notifier_actions.py:124).
def _resolve_ladder_terminal(  # pragma: no cover
    ctx: AgentNotifierContext,
    finding: AgentNotifierFinding,
    *,
    now: datetime,
    sweep: _SweepState,
) -> AgentNotifierActionResult:
    if finding.source_id is None:
        return AgentNotifierActionResult("ladder-resolve", finding, "skipped", "no source entry id")
    try:
        resolved, resolved_now = inbox_transitions.mark_ladder_resolved(
            ctx.inbox_store,
            finding.source_id,
            now=now.isoformat(),
            reason="terminal ladder rung reached for non-live target seat",
            current=sweep.inbox_current,
        )
    except KeyError:
        return AgentNotifierActionResult("ladder-resolve", finding, "skipped", "entry missing")
    sweep.remember(resolved)
    if resolved_now:
        _log_event(
            ctx,
            "orchestration.agent-notifier.ladder-resolved",
            {
                "entryId": resolved.id,
                "agentId": resolved.agentId,
                "rung": resolved.rung,
                "state": resolved.state,
                "ladderResolvedAt": resolved.ladderResolvedAt,
            },
        )
    return AgentNotifierActionResult(
        "ladder-resolve", finding, resolved.state, resolved.ladderResolvedReason
    )


# 260731-EFA-L7 R10: verbatim L7 split (L7-OQ1 Option A serving scope); unchanged edge branch, out of this leaf's behavior scope (mcp/src/agents_remember/serving/_agent_notifier_actions.py:161).
def _escalate_inbox_entry(  # pragma: no cover
    ctx: AgentNotifierContext, entry_id: str, *, now: datetime, sweep: _SweepState
) -> None:
    """R4d: hand a persistently-failing row to the escalation ladder by stamping ``escalatedAt``,
    the anchor ``escalation_ladder.rung_due`` measures each rung's dwell from."""
    try:
        escalated = inbox_transitions.mark_escalated(
            ctx.inbox_store, entry_id, now=now.isoformat(), current=sweep.inbox_current
        )
    except KeyError:
        return
    sweep.remember(escalated)
    _log_event(
        ctx,
        "orchestration.agent-notifier.escalate",
        {"entryId": escalated.id, "kind": "inbox", "escalatedAt": escalated.escalatedAt},
    )


def _auto_nudge(
    ctx: AgentNotifierContext,
    finding: AgentNotifierFinding,
    *,
    now: datetime,
    sweep: _SweepState,
) -> AgentNotifierActionResult:
    owner = derive_signal_owner(
        ctx.catalog,
        sender_agent_id=finding.session_id,
        message_kind="nudge",
        leaf_key=finding.leaf_key,
    )
    if owner.agent_id is None and owner.lifecycle_id is None and owner.role is None:
        return AgentNotifierActionResult("auto-nudge", finding, "skipped", "no routable owner")
    reason: NudgeReason = "inactive"
    subject = finding.leaf_key or finding.session_id or finding.detail
    message = nudge_message(reason, subject=subject)
    record = ctx.nudge_store.record(
        OrchestrationNudgeRecord(
            id=new_ulid(),
            ts=now.isoformat(),
            state="sent",
            reason=reason,
            targetAgentId=owner.agent_id,
            targetLifecycleId=owner.lifecycle_id,
            subjectAgentId=finding.session_id,
            subjectLifecycleId=None,
            artifactPath=None,
            message=message,
        ),
        rate_limit_seconds=ctx.nudge_rate_limit_seconds,
    )
    _log_event(
        ctx,
        "orchestration.nudge",
        {
            "state": record.state,
            "reason": record.reason,
            "targetAgentId": record.targetAgentId,
            "targetLifecycleId": record.targetLifecycleId,
            "subjectAgentId": record.subjectAgentId,
            "artifactPath": record.artifactPath,
            "swept": True,
        },
    )
    if record.state == "rate-limited":
        _mark_expectation_missed(ctx, finding, now=now, sweep=sweep)
        return AgentNotifierActionResult("auto-nudge", finding, "rate-limited", message)
    delivered = _post_owner_signal(
        ctx,
        owner,
        OwnerSignal(
            message_kind="nudge",
            ask=f"Nudge: {subject}",
            response=message,
            leaf_key=finding.leaf_key,
            seat_role=finding.seat_role,
            subject_agent_id=finding.session_id,
        ),
        OwnerSignalOptions(now=now, sweep=sweep),
    )
    _mark_expectation_missed(ctx, finding, now=now, sweep=sweep)
    return AgentNotifierActionResult("auto-nudge", finding, "sent", delivered)


# 260731-EFA-L7 R10: verbatim L7 split (L7-OQ1 Option A serving scope); unchanged edge branch, out of this leaf's behavior scope (mcp/src/agents_remember/serving/_agent_notifier_actions.py:255).
def _mark_expectation_missed(  # pragma: no cover
    ctx: AgentNotifierContext,
    finding: AgentNotifierFinding,
    *,
    now: datetime,
    sweep: _SweepState | None = None,
) -> None:
    """The sweep is the reserved caller of ``mark_missed`` (expectation_rows.py:93-97): an overdue
    row the sweep has now acted on is marked missed, idempotently, every sweep it stays overdue.

    CS-6 D2: pass the sweep's one-read expectation snapshot so K missed-transitions in a sweep do
    O(1) in-memory lookups + one append each, not K full-file pydantic re-folds."""
    if finding.source_id is None:
        return
    current = sweep.expectation_current if sweep is not None else None
    with contextlib.suppress(KeyError):
        marked = ctx.expectation_store.mark_missed(
            finding.source_id, now=now.isoformat(), current=current
        )
        if sweep is not None:
            sweep.remember_expectation(marked)


def _signal_emit(
    ctx: AgentNotifierContext,
    finding: AgentNotifierFinding,
    *,
    now: datetime,
    sweep: _SweepState,
) -> AgentNotifierActionResult:
    owner = derive_signal_owner(
        ctx.catalog,
        sender_agent_id=finding.session_id,
        message_kind="escalation",
        leaf_key=finding.leaf_key,
    )
    if owner.agent_id is None and owner.lifecycle_id is None and owner.role is None:
        return AgentNotifierActionResult("signal-emit", finding, "skipped", "no routable owner")
    signal_key = AgentNotifierSignalKey(
        target=AgentNotifierSignalTarget(
            agent_id=owner.agent_id,
            lifecycle_id=owner.lifecycle_id,
            role=owner.role,
            leaf_key=finding.leaf_key,
            seat_role=finding.seat_role,
        ),
        finding_kind=finding.kind,
        detail=finding.detail,
    )
    if ctx.signal_cooldown_store.in_cooldown(
        signal_key,
        now=now,
        cooldown_seconds=ctx.signal_cooldown_seconds,
        records=sweep.signal_current,
    ):
        return AgentNotifierActionResult(
            "signal-emit", finding, "cooldown", "signal cooldown active"
        )
    ask = f"Agent notifier observed {finding.kind}: {finding.detail}"
    response = f"session {finding.session_id or 'unknown'} (leaf {finding.leaf_key or 'unknown'})"
    delivery_state = _post_owner_signal(
        ctx,
        owner,
        OwnerSignal(
            message_kind="escalation",
            ask=ask,
            response=response,
            leaf_key=finding.leaf_key,
            seat_role=finding.seat_role,
            subject_agent_id=finding.session_id,
        ),
        OwnerSignalOptions(now=now, sweep=sweep),
    )
    signal_record = AgentNotifierSignalRecord(
        id=new_ulid(),
        ts=now.isoformat(),
        targetAgentId=owner.agent_id,
        targetLifecycleId=owner.lifecycle_id,
        targetRole=owner.role,
        leafKey=finding.leaf_key,
        seatRole=finding.seat_role,
        findingKind=finding.kind,
        detail=finding.detail,
        deliveryState=delivery_state,
    )
    ctx.signal_cooldown_store.append(signal_record)
    sweep.remember_signal(signal_record)
    _log_event(
        ctx,
        "orchestration.agent-notifier.signal",
        {
            "predicateKind": finding.kind,
            "detail": finding.detail,
            "sessionId": finding.session_id,
            "leafKey": finding.leaf_key,
            "seatRole": finding.seat_role,
            "ownerRole": owner.role,
            "deliveryState": delivery_state,
        },
    )
    return AgentNotifierActionResult("signal-emit", finding, delivery_state)


# 260731-EFA-L7 R10: verbatim L7 split (L7-OQ1 Option A serving scope); unchanged edge branch, out of this leaf's behavior scope (mcp/src/agents_remember/serving/_agent_notifier_actions.py:481).
def _rung_entry(  # pragma: no cover
    finding: AgentNotifierFinding,
    sweep: _SweepState,
) -> tuple[OperatorInboxEntry | None, str | None]:
    if finding.source_id is None:
        return None, "no source entry id"
    if finding.source_id in sweep.escalated_entry_ids:
        return None, "entry already transitioned this sweep"
    entry = sweep.inbox_current.get(finding.source_id)
    if entry is None or entry.state != "pending":
        return None, "entry not pending"
    if dispatch_stays_on_exact_session(entry):
        return None, "dispatch brief stays on its exact session"
    return entry, None


# 260731-EFA-L7 R10: verbatim L7 split (L7-OQ1 Option A serving scope); unchanged edge branch, out of this leaf's behavior scope (mcp/src/agents_remember/serving/_agent_notifier_actions.py:497).
def _escalate_rung(  # pragma: no cover
    ctx: AgentNotifierContext,
    finding: AgentNotifierFinding,
    *,
    now: datetime,
    sweep: _SweepState,
) -> AgentNotifierActionResult:
    """R2: advance one pending, unacked row to its next ladder rung -- renudge (1), skip-level
    (2), or architect custody (3, terminal) -- and durably stamp the transition.

    Ruled invariant (developer, 2026-07-09): the ladder never mints rows. Each rung transition
    MUTATES the one root row -- re-anchors its dwell clock, re-addresses it to the next owner,
    and redelivers it (which increments its attempt count). The prior shape (a new pending row
    per transition, itself ladder-eligible) was a branching process: with an absent developer it
    grew the inbox to 20k+ pending rows and took the host down."""
    entry, refusal = _rung_entry(finding, sweep)
    if entry is None:
        return AgentNotifierActionResult("escalate-rung", finding, "skipped", refusal)
    sweep.escalated_entry_ids.add(entry.id)
    step = next_step(ctx.catalog, entry)
    if step.owner.agent_id is None and step.owner.role is None:
        return AgentNotifierActionResult("escalate-rung", finding, "skipped", "no routable owner")
    advanced = inbox_transitions.advance_rung(
        ctx.inbox_store,
        entry.id,
        RungAdvance(
            rung=step.rung,
            readdress_to=(
                InboxOwner(
                    role=step.owner.role,
                    agent_id=step.owner.agent_id,
                    lifecycle_id=step.owner.lifecycle_id,
                )
                if step.rung > 1
                else None
            ),
        ),
        now=now.isoformat(),
        current=sweep.inbox_current,
    )
    sweep.remember(advanced)
    admission = (
        DeliveryAdmission(boundary=True)
        if advanced.messageKind == "state-signal"
        else DEFAULT_DELIVERY_ADMISSION
    )
    delivered = deliver_inbox_entry(
        InboxDeliveryLog(
            store=ctx.inbox_store,
            entry=advanced,
            at=now.isoformat(),
            floor=RedeliveryFloor(
                current=sweep.inbox_current, seconds=ctx.redeliver_rate_limit_seconds
            ),
        ),
        sessions=HostedSessionRuntime(catalog=ctx.catalog, host=ctx.host),
        paster=ctx.paster,
        admission=admission,
    )
    sweep.remember(delivered)
    delivery_state = delivered.deliveryState
    _log_event(
        ctx,
        "orchestration.escalation.rung",
        {
            "entryId": entry.id,
            "rung": step.rung,
            "action": step.action,
            "ownerRole": step.owner.role,
            "ownerAgentId": step.owner.agent_id,
            "deliveryState": delivery_state,
        },
    )
    # R3: past the respawn threshold, a still-silent addressee seat is marked suspect and
    # respawned rather than waited on further -- a side effect of the transition, not a distinct
    # finding of its own (the seat's silence IS this row's silence).
    if step.rung >= ctx.respawn_after_rung and seat_is_suspect(
        ctx.catalog, entry.agentId, now=now, stale_seconds=ctx.stale_seat_seconds
    ):
        _respawn_suspect(ctx, entry.agentId, now=now, sweep=sweep)
    if _ladder_terminal_and_dead(ctx.catalog, advanced):
        terminal_finding = AgentNotifierFinding(
            kind="inbox-ladder-terminal",
            detail="ladder-resolved",
            session_id=advanced.agentId,
            leaf_key=finding.leaf_key,
            seat_role=finding.seat_role,
            source_id=advanced.id,
        )
        _resolve_ladder_terminal(ctx, terminal_finding, now=now, sweep=sweep)
    return AgentNotifierActionResult("escalate-rung", finding, delivery_state, step.action)


# 260731-EFA-L7 R10: verbatim L7 split (L7-OQ1 Option A serving scope); unchanged edge branch, out of this leaf's behavior scope (mcp/src/agents_remember/serving/_agent_notifier_actions.py:584).
def _respawn_suspect(  # pragma: no cover
    ctx: AgentNotifierContext,
    agent_id: str | None,
    *,
    now: datetime,
    sweep: _SweepState,
) -> None:
    """R3: retire the suspect seat's husk (HFX-L8 primitives), signal its owner (or the
    orchestrator, when the owner mapping already resolves there for a manager) with a respawn
    directive carrying the pending queue to re-deliver to the successor, and -- if the retired
    seat was itself a manager -- surface its now-orphaned live workers (R3 orphan policy: held,
    never auto re-parented, never absorbing the dead manager's role)."""
    if agent_id is None:
        return
    entry = ctx.catalog.get(agent_id)
    if entry is None or entry.status != "running":
        return
    owner = derive_signal_owner(ctx.catalog, sender_agent_id=agent_id, message_kind="escalation")
    pending_queue = [
        row.id
        for row in sweep.inbox_current.values()
        if row.state == "pending" and row.agentId == agent_id
    ]
    retire_entry(
        ctx.catalog,
        ctx.host,
        entry,
        SeatClosure(
            at=now.isoformat(),
            by_session=None,
            reason="escalation-ladder-suspect",
            edge="agent-notifier-respawn",
        ),
    )
    orphaned: list[str] = []
    if entry.binding_role == "manager":
        orphaned = [
            worker.id for worker in find_orphaned_workers(ctx.catalog, manager_agent_id=agent_id)
        ]
    delivery_state = "skipped"
    if owner.agent_id is not None or owner.role is not None:
        ask = f"Respawn directive: seat {agent_id} ({entry.binding_role}) retired as suspect (R3)"
        response = (
            f"Pending queue for the successor: {pending_queue}. Orphaned workers: {orphaned}."
        )
        delivery_state = _post_owner_signal(
            ctx,
            owner,
            OwnerSignal(message_kind="escalation", ask=ask, response=response),
            OwnerSignalOptions(now=now, sweep=sweep),
        )
    _log_event(
        ctx,
        "orchestration.agent-notifier.respawn",
        {
            "agentId": agent_id,
            "spawnRole": entry.spawn_role,
            "seatRole": entry.binding_role,
            "pendingQueue": pending_queue,
            "orphanedWorkers": orphaned,
            "ownerRole": owner.role,
            "ownerAgentId": owner.agent_id,
            "deliveryState": delivery_state,
        },
    )


# 260731-EFA-L7 R10: verbatim L7 split (L7-OQ1 Option A serving scope); unchanged edge branch, out of this leaf's behavior scope (mcp/src/agents_remember/serving/_agent_notifier_actions.py:652).
def _signal_dead_upstream(  # pragma: no cover
    ctx: AgentNotifierContext,
    finding: AgentNotifierFinding,
    *,
    now: datetime,
    sweep: _SweepState,
) -> AgentNotifierActionResult:
    """R4: hand a leaf whose recorded owner died to its current responsible manager.

    Address-time manager resolution repairs stale manager provenance. The ordinary ladder owns any
    later climb; this dead-upstream detector must not skip directly to the orchestrator/architect.
    """
    owner = derive_leaf_manager_owner(
        ctx.catalog, sender_agent_id=finding.session_id, leaf_key=finding.leaf_key
    )
    if owner.agent_id is None and owner.role is None:
        return AgentNotifierActionResult("signal-manager", finding, "skipped", "no manager address")
    ask = (
        f"Dead-upstream (R4/P-6): seat {finding.session_id or 'unknown'} lost its recorded owner; "
        "current manager action is required. The seat continues its own brief and never absorbs "
        "the dead owner's role."
    )
    response = f"leaf {finding.leaf_key or 'unknown'}"
    delivery_state = _post_owner_signal(
        ctx,
        owner,
        OwnerSignal(
            message_kind="escalation",
            ask=ask,
            response=response,
            leaf_key=finding.leaf_key,
            seat_role=finding.seat_role,
            subject_agent_id=finding.session_id,
        ),
        OwnerSignalOptions(now=now, sweep=sweep),
    )
    _log_event(
        ctx,
        "orchestration.agent-notifier.dead-upstream",
        {
            "sessionId": finding.session_id,
            "leafKey": finding.leaf_key,
            "seatRole": finding.seat_role,
            "managerRole": owner.role,
            "managerAgentId": owner.agent_id,
            "deliveryState": delivery_state,
        },
    )
    return AgentNotifierActionResult("signal-manager", finding, delivery_state)


def _emit_state_signal(  # pragma: no cover
    ctx: AgentNotifierContext,
    finding: AgentNotifierFinding,
    *,
    now: datetime,
    sweep: _SweepState,
) -> AgentNotifierActionResult:
    """Emit exactly one durable state-signal for a completed/interrupted seat turn.

    The row is persisted before the marker is set, so a crash between the two leaves a
    pending row that the next sweep coalesces/renews rather than a duplicate. Delivery
    rides the availability gate: a working manager holds the row on its durable schedule.
    """
    if finding.session_id is None:
        return AgentNotifierActionResult("state-signal", finding, "skipped", "no seat row")
    entry = ctx.catalog.get(finding.session_id)
    if (
        entry is None
        or entry.terminal_evidence_id is None
        or entry.state_signal_emitted_for == entry.terminal_evidence_id
    ):
        return AgentNotifierActionResult("state-signal", finding, "skipped", "already emitted")
    owner = derive_leaf_manager_owner(
        ctx.catalog, sender_agent_id=finding.session_id, leaf_key=finding.leaf_key
    )
    if owner.agent_id is None and owner.lifecycle_id is None and owner.role is None:
        return AgentNotifierActionResult("state-signal", finding, "skipped", "no routable owner")
    delivery_state = _post_owner_signal(
        ctx,
        owner,
        OwnerSignal(
            message_kind="state-signal",
            ask=(
                f"Agent notifier observed state-signal: {entry.terminal_outcome or 'unknown'} "
                f"({entry.terminal_evidence_id})"
            ),
            response=state_signal_response(entry),
            leaf_key=finding.leaf_key,
            seat_role=finding.seat_role,
            subject_agent_id=finding.session_id,
        ),
        OwnerSignalOptions(now=now, sweep=sweep, admission=DeliveryAdmission(boundary=True)),
    )
    record_state_signal_emitted(ctx.catalog, finding.session_id, entry.terminal_evidence_id)
    _log_event(
        ctx,
        "orchestration.agent-notifier.state-signal",
        {
            "sessionId": finding.session_id,
            "leafKey": finding.leaf_key,
            "terminalOutcome": entry.terminal_outcome,
            "terminalEvidenceId": entry.terminal_evidence_id,
            "ownerRole": owner.role,
            "ownerAgentId": owner.agent_id,
            "deliveryState": delivery_state,
        },
    )
    return AgentNotifierActionResult(
        "state-signal", finding, delivery_state, entry.terminal_outcome
    )


def _emit_non_reaction(  # pragma: no cover
    ctx: AgentNotifierContext,
    finding: AgentNotifierFinding,
    *,
    now: datetime,
    sweep: _SweepState,
) -> AgentNotifierActionResult:
    """Relay the non-reaction residue fact to the seat's owner, once per landed-row episode."""
    if finding.session_id is None or finding.source_id is None:
        return AgentNotifierActionResult("non-reaction", finding, "skipped", "no seat row")
    entry = ctx.catalog.get(finding.session_id)
    row = sweep.inbox_current.get(finding.source_id)
    if entry is None or row is None:
        return AgentNotifierActionResult("non-reaction", finding, "skipped", "row not pending")
    if entry.non_reaction_emitted_for == finding.source_id:
        return AgentNotifierActionResult("non-reaction", finding, "skipped", "already emitted")
    owner = derive_leaf_manager_owner(
        ctx.catalog, sender_agent_id=finding.session_id, leaf_key=finding.leaf_key
    )
    if owner.agent_id is None and owner.lifecycle_id is None and owner.role is None:
        return AgentNotifierActionResult("non-reaction", finding, "skipped", "no routable owner")
    delivery_state = _post_owner_signal(
        ctx,
        owner,
        OwnerSignal(
            message_kind="state-signal",
            ask="Agent notifier observed state-signal: non-reaction",
            response=non_reaction_response(entry, row),
            leaf_key=finding.leaf_key,
            seat_role=finding.seat_role,
            subject_agent_id=finding.session_id,
        ),
        OwnerSignalOptions(now=now, sweep=sweep, admission=DeliveryAdmission(boundary=True)),
    )
    record_non_reaction_emitted(ctx.catalog, finding.session_id, finding.source_id)
    _log_event(
        ctx,
        "orchestration.agent-notifier.state-signal",
        {
            "sessionId": finding.session_id,
            "leafKey": finding.leaf_key,
            "terminalOutcome": "non-reaction",
            "landedRowId": finding.source_id,
            "ownerRole": owner.role,
            "ownerAgentId": owner.agent_id,
            "deliveryState": delivery_state,
        },
    )
    return AgentNotifierActionResult("non-reaction", finding, delivery_state, finding.source_id)


def _drain_boundary(  # pragma: no cover
    ctx: AgentNotifierContext,
    finding: AgentNotifierFinding,
    *,
    now: datetime,
    sweep: _SweepState,
) -> AgentNotifierActionResult:
    """Push one pending row whose target crossed a turn boundary after the last attempt."""
    result = _redeliver(ctx, finding, now=now, sweep=sweep)
    return AgentNotifierActionResult("boundary-drain", finding, result.outcome, result.detail)


_FindingAction = Callable[..., AgentNotifierActionResult]

# One predicate kind -> the one act-phase handler that answers it. A finding kind with no entry
# here is reported as unhandled rather than silently doing nothing, so adding a predicate without
# its handler is visible in the sweep result instead of being lost.
_FINDING_ACTIONS: dict[str, _FindingAction] = {
    "inbox-redeliverable": _redeliver,
    "inbox-ladder-terminal": _resolve_ladder_terminal,
    "expectation-overdue": _auto_nudge,
    "escalation-due": _escalate_rung,
    "dead-upstream": _signal_dead_upstream,
    "seat-liveness": _signal_emit,
    "state-signal-due": _emit_state_signal,
    "non-reaction-due": _emit_non_reaction,
    "boundary-drain": _drain_boundary,
}


def act_on_finding(
    ctx: AgentNotifierContext,
    finding: AgentNotifierFinding,
    *,
    now: datetime,
    sweep: _SweepState | None = None,
) -> AgentNotifierActionResult:
    if sweep is None:
        current = ctx.inbox_store.current()
        sweep = _SweepState(inbox_current=current, redeliver_budget=ctx.redeliver_budget)
    action = _FINDING_ACTIONS.get(finding.kind)
    if action is None:
        return AgentNotifierActionResult("none", finding, "skipped", "unhandled finding kind")
    return action(ctx, finding, now=now, sweep=sweep)
