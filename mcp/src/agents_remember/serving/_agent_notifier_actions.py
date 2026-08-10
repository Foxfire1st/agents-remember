from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from agents_remember.controlplane import operator_inbox_transitions as inbox_transitions
from agents_remember.controlplane.agent_notifier_signals import (
    AgentNotifierSignalKey,
    AgentNotifierSignalRecord,
    AgentNotifierSignalTarget,
)
from agents_remember.controlplane.operator_inbox_records import (
    InboxOwner,
)
from agents_remember.controlplane.operator_inbox_transitions import ExpiryOptions
from agents_remember.controlplane.signal_routing import (
    derive_architect_owner,
    derive_leaf_manager_owner,
    derive_row_owner,
    derive_signal_owner,
)
from agents_remember.observer.events import Event, now_iso
from agents_remember.observer.ulid import new_ulid
from agents_remember.serving._agent_notifier_evaluation import (
    PERSISTENT_FAILURE_ATTEMPTS,
)
from agents_remember.serving.agent_notifier_models import (
    AgentNotifierActionResult,
    AgentNotifierContext,
    AgentNotifierFinding,
)
from agents_remember.serving.agent_notifier_models import SweepState as _SweepState
from agents_remember.serving.dispatch_brief import (
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
from agents_remember.serving.seat_turn_truth import (
    record_compound_idle_emitted,
    record_non_reaction_emitted,
    record_state_signal_emitted,
)
from agents_remember.serving.state_signals import (
    compound_idle_response,
    compound_idle_sets,
    compound_idle_signature,
    current_non_reaction_finding,
    current_state_signal_finding,
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
    if updated.state == "pending" and updated.attemptCount >= PERSISTENT_FAILURE_ATTEMPTS:
        _mark_unresolved(ctx, updated.id, now=now, sweep=sweep)
    return AgentNotifierActionResult(
        "redeliver", finding, updated.deliveryState, updated.deliveryDetail
    )


def _mark_unresolved(
    ctx: AgentNotifierContext,
    entry_id: str,
    *,
    now: datetime,
    sweep: _SweepState,
) -> None:
    """N3 attempt ceiling: 5 attempts without a landing resolve the row ``unresolved``."""
    try:
        unresolved, _ = inbox_transitions.mark_unresolved(
            ctx.inbox_store,
            entry_id,
            now=now.isoformat(),
            reason="attempt-limit",
        )
    except KeyError:
        return
    sweep.remember(unresolved)
    _log_event(
        ctx,
        "orchestration.agent-notifier.unresolved",
        {
            "entryId": unresolved.id,
            "agentId": unresolved.agentId,
            "state": unresolved.state,
            "attemptCount": unresolved.attemptCount,
            "terminalAt": unresolved.terminalAt,
        },
    )


def _rebind_due(
    ctx: AgentNotifierContext,
    finding: AgentNotifierFinding,
    *,
    now: datetime,
    sweep: _SweepState,
) -> AgentNotifierActionResult:
    """Sweep-time rebind (N14): move a pending row onto its current qualified owner."""
    if finding.source_id is None:
        return AgentNotifierActionResult("rebind", finding, "skipped", "no source entry id")
    entry = sweep.inbox_current.get(finding.source_id)
    if entry is None or entry.state != "pending":
        return AgentNotifierActionResult("rebind", finding, "skipped", "entry not pending")
    owner = derive_row_owner(ctx.catalog, entry)
    if owner.agent_id is None or owner.agent_id == entry.agentId:
        return AgentNotifierActionResult("rebind", finding, "skipped", "no replacement owner")
    rebound, _ = inbox_transitions.rebind_entry(
        ctx.inbox_store,
        entry.id,
        InboxOwner(role=owner.role, agent_id=owner.agent_id, lifecycle_id=owner.lifecycle_id),
        now=now.isoformat(),
        current=sweep.inbox_current,
    )
    sweep.remember(rebound)
    _log_event(
        ctx,
        "orchestration.agent-notifier.rebind",
        {
            "entryId": rebound.id,
            "fromAgentId": entry.agentId,
            "toAgentId": owner.agent_id,
            "toRole": owner.role,
            "leafKey": rebound.leafKey,
        },
    )
    return AgentNotifierActionResult("rebind", finding, "rebound", owner.agent_id)


def _rebind_expired(
    ctx: AgentNotifierContext,
    finding: AgentNotifierFinding,
    *,
    now: datetime,
    sweep: _SweepState,
) -> AgentNotifierActionResult:
    """N2 grace expiry: no replacement appeared -- terminal-visible, then compacted.

    The terminal marker is readdressed to the scoped architect mailbox (N3: a mailbox, not a
    rung) so a dead owner chain stays inspectable during the marker-retention window.
    """
    if finding.source_id is None:
        return AgentNotifierActionResult("expire", finding, "skipped", "no source entry id")
    entry = sweep.inbox_current.get(finding.source_id)
    if entry is None or entry.state != "pending":
        return AgentNotifierActionResult("expire", finding, "skipped", "entry not pending")
    owner = derive_row_owner(ctx.catalog, entry)
    if owner.agent_id is not None and owner.agent_id != entry.agentId:
        return _rebind_due(ctx, finding, now=now, sweep=sweep)
    mailbox = derive_architect_owner(ctx.catalog, leaf_key=entry.leafKey)
    expired, _ = inbox_transitions.mark_expired(
        ctx.inbox_store,
        entry.id,
        now=now.isoformat(),
        options=ExpiryOptions(
            reason="rebind-grace-expired",
            readdress_to=InboxOwner(
                role=mailbox.role, agent_id=mailbox.agent_id, lifecycle_id=mailbox.lifecycle_id
            ),
        ),
    )
    sweep.remember(expired)
    _log_event(
        ctx,
        "orchestration.agent-notifier.rebind-expired",
        {
            "entryId": expired.id,
            "agentId": expired.agentId,
            "state": expired.state,
            "terminalAt": expired.terminalAt,
            "architectRole": mailbox.role,
            "architectAgentId": mailbox.agent_id,
        },
    )
    return AgentNotifierActionResult("expire", finding, expired.state, "rebind-grace-expired")


def _expire_pending(
    ctx: AgentNotifierContext,
    finding: AgentNotifierFinding,
    *,
    now: datetime,
    sweep: _SweepState,
) -> AgentNotifierActionResult:
    """Retention boundary (D6/§9): a pending row past the 48h window resolves ``expired``."""
    if finding.source_id is None:
        return AgentNotifierActionResult("expire", finding, "skipped", "no source entry id")
    entry = sweep.inbox_current.get(finding.source_id)
    if entry is None or entry.state != "pending":
        return AgentNotifierActionResult("expire", finding, "skipped", "entry not pending")
    expired, _ = inbox_transitions.mark_expired(
        ctx.inbox_store,
        entry.id,
        now=now.isoformat(),
        options=ExpiryOptions(reason="pending-ttl-expired"),
    )
    sweep.remember(expired)
    _log_event(
        ctx,
        "orchestration.agent-notifier.inbox-expired",
        {"entryId": expired.id, "agentId": expired.agentId, "terminalAt": expired.terminalAt},
    )
    return AgentNotifierActionResult("expire", finding, expired.state, "pending-ttl-expired")


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


# 260731-EFA-L7 R10: verbatim L7 split (L7-OQ1 Option A serving scope); unchanged edge branch, out of this leaf's behavior scope (mcp/src/agents_remember/serving/_agent_notifier_actions.py:368).
def _signal_dead_upstream(  # pragma: no cover
    ctx: AgentNotifierContext,
    finding: AgentNotifierFinding,
    *,
    now: datetime,
    sweep: _SweepState,
) -> AgentNotifierActionResult:
    """R4: hand a leaf whose recorded owner died to its current responsible manager.

    Address-time manager resolution repairs stale manager provenance. The detector must not
    skip directly to the orchestrator/architect: when no current manager exists, a dead owner
    chain surfaces through the rebind machinery to the scoped architect mailbox
    (mailbox-not-rung).
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
    if finding.session_id is None or finding.source_id is None:
        return AgentNotifierActionResult("state-signal", finding, "skipped", "no seat row")
    current = current_state_signal_finding(
        ctx.catalog, session_id=finding.session_id, source_id=finding.source_id
    )
    if current is None:
        return AgentNotifierActionResult("state-signal", finding, "skipped", "no longer due")
    entry, current_finding = current
    owner = derive_leaf_manager_owner(
        ctx.catalog, sender_agent_id=current_finding.session_id, leaf_key=current_finding.leaf_key
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
            leaf_key=current_finding.leaf_key,
            seat_role=current_finding.seat_role,
            subject_agent_id=current_finding.session_id,
        ),
        OwnerSignalOptions(now=now, sweep=sweep, admission=DeliveryAdmission(boundary=True)),
    )
    assert entry.terminal_evidence_id is not None
    record_state_signal_emitted(ctx.catalog, finding.session_id, entry.terminal_evidence_id)
    _log_event(
        ctx,
        "orchestration.agent-notifier.state-signal",
        {
            "sessionId": current_finding.session_id,
            "leafKey": current_finding.leaf_key,
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


def _emit_compound_idle(
    ctx: AgentNotifierContext,
    finding: AgentNotifierFinding,
    *,
    now: datetime,
    sweep: _SweepState,
) -> AgentNotifierActionResult:
    """Emit exactly one durable compound-idle state-signal to the owning orchestrator.

    Ownership is the manager's own spawn provenance (one hop up, sprint-scope bounded):
    a manager with no recorded orchestrator edge is never routed by a global fallback.
    The episode signature is derived from the ACTION-time member read (a concurrent
    catalog write cannot leave a stale marker behind); a seat returning to activity
    changes that signature, which is the re-arm.
    """
    if finding.session_id is None:
        return AgentNotifierActionResult("compound-idle", finding, "skipped", "no seat row")
    entry = ctx.catalog.get(finding.session_id)
    if entry is None:
        return AgentNotifierActionResult("compound-idle", finding, "skipped", "no seat row")
    members = compound_idle_sets(ctx.catalog).get(entry.id)
    if members is None:
        return AgentNotifierActionResult("compound-idle", finding, "skipped", "no longer idle")
    signature = compound_idle_signature(members)
    if entry.compound_idle_emitted_for == signature:
        return AgentNotifierActionResult("compound-idle", finding, "skipped", "already emitted")
    owner = derive_signal_owner(ctx.catalog, sender_agent_id=entry.id, message_kind="state-signal")
    if owner.agent_id is None and owner.lifecycle_id is None:
        return AgentNotifierActionResult("compound-idle", finding, "skipped", "no routable owner")
    delivery_state = _post_owner_signal(
        ctx,
        owner,
        OwnerSignal(
            message_kind="state-signal",
            ask=f"Agent notifier observed state-signal: compound-idle ({signature})",
            response=compound_idle_response(members),
            leaf_key=finding.leaf_key,
            seat_role=finding.seat_role,
            subject_agent_id=entry.id,
        ),
        OwnerSignalOptions(now=now, sweep=sweep, admission=DeliveryAdmission(boundary=True)),
    )
    record_compound_idle_emitted(ctx.catalog, entry.id, signature)
    _log_event(
        ctx,
        "orchestration.agent-notifier.state-signal",
        {
            "sessionId": entry.id,
            "leafKey": finding.leaf_key,
            "terminalOutcome": "compound-idle",
            "episode": signature,
            "ownerRole": owner.role,
            "ownerAgentId": owner.agent_id,
            "deliveryState": delivery_state,
        },
    )
    return AgentNotifierActionResult("compound-idle", finding, delivery_state)


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
    current = current_non_reaction_finding(
        ctx.catalog,
        ctx.inbox_store,
        finding,
        now=now,
    )
    if current is None:
        return AgentNotifierActionResult("non-reaction", finding, "skipped", "no longer due")
    entry, row, current_finding = current
    if entry.binding_role == "manager":
        owner = derive_signal_owner(
            ctx.catalog, sender_agent_id=finding.session_id, message_kind="state-signal"
        )
        if owner.agent_id is None and owner.lifecycle_id is None:
            return AgentNotifierActionResult(
                "non-reaction", finding, "skipped", "no routable owner"
            )
    else:
        owner = derive_leaf_manager_owner(
            ctx.catalog,
            sender_agent_id=current_finding.session_id,
            leaf_key=current_finding.leaf_key,
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
            leaf_key=current_finding.leaf_key,
            seat_role=current_finding.seat_role,
            subject_agent_id=current_finding.session_id,
        ),
        OwnerSignalOptions(now=now, sweep=sweep, admission=DeliveryAdmission(boundary=True)),
    )
    record_non_reaction_emitted(ctx.catalog, finding.session_id, finding.source_id)
    _log_event(
        ctx,
        "orchestration.agent-notifier.state-signal",
        {
            "sessionId": current_finding.session_id,
            "leafKey": current_finding.leaf_key,
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
    "rebind-due": _rebind_due,
    "rebind-expired": _rebind_expired,
    "inbox-ttl-expired": _expire_pending,
    "dead-upstream": _signal_dead_upstream,
    "seat-liveness": _signal_emit,
    "state-signal-due": _emit_state_signal,
    "compound-idle-due": _emit_compound_idle,
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
