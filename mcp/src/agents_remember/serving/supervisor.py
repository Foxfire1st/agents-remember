"""P-15 tiers 1+2: the deterministic supervisor sweep (260707-HFX2-L2).

"The model is never the polling layer." Every intervention the pilot run needed was detectable by
a MECHANICAL predicate (P-15: pane-state, expectation-deadline expiry, turn-report staleness,
unacked-row redelivery, seat-liveness). This module sweeps the authoritative stores on its own
cadence, evaluates those predicates, and acts -- redeliver, auto-nudge, signal-emit, escalate --
with zero tokens spent and zero model calls anywhere in the loop.

R3 (#22 root-cause rule, non-negotiable): every predicate reads :class:`TerminalCatalog` /
:class:`OperatorInboxStore` / :class:`ExpectationRowStore` / the nudge store DIRECTLY. The
projection is a consumer of the ``orchestration.supervisor.*`` events this module emits, never a
source -- so this module imports nothing from ``serving/projector.py`` or ``observer/reducer.py``.

Level-triggered by design: any event lost anywhere (a dropped push, a crashed dispatch call) is
found by the NEXT sweep. This is the backstop even protocol-grade push needs (A2A/MCP; the
Inngest Oct-2025 incident is the reference case for "at-least-once push still needs a
reconciliation sweep").
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from time import perf_counter

from agents_remember.controlplane import operator_inbox_transitions as inbox_transitions
from agents_remember.controlplane.escalation_ladder import (
    MAX_RUNG,
    next_step,
    rung_due,
    seat_is_suspect,
)
from agents_remember.controlplane.expectation_rows import ExpectationRow, ExpectationRowStore
from agents_remember.controlplane.inbox_backoff import require_redelivery_floor_seconds
from agents_remember.controlplane.operator_inbox_records import (
    InboxAddress,
    InboxDeliveryState,
    InboxMessage,
    InboxMessageKind,
    InboxOwner,
    InboxPoster,
    InboxRouting,
    InboxSubject,
    OperatorInboxEntry,
    create_operator_inbox_entry,
)
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.controlplane.operator_inbox_transitions import (
    InboxRenewal,
    RungAdvance,
)
from agents_remember.controlplane.orchestration_artifacts import turn_report_artifact
from agents_remember.controlplane.orchestration_nudges import (
    NudgeReason,
    OrchestrationNudgeRecord,
    missing_artifact,
    nudge_message,
)
from agents_remember.controlplane.orphan_policy import find_orphaned_workers
from agents_remember.controlplane.signal_routing import (
    RoutedOwner,
    derive_leaf_manager_owner,
    derive_signal_owner,
    is_seat_dead,
    leaf_chain_has_progress,
)
from agents_remember.controlplane.supervisor_signals import (
    SupervisorSignalKey,
    SupervisorSignalRecord,
    SupervisorSignalTarget,
)
from agents_remember.observer.events import Event, now_iso
from agents_remember.observer.ulid import new_ulid
from agents_remember.serving.dispatch_brief import (
    DISPATCH_BRIEF_KIND,
    dispatch_stays_on_exact_session,
    fulfill_briefed_expectation,
)
from agents_remember.serving.hosted_session_runtime import HostedSessionRuntime
from agents_remember.serving.inbox_delivery import (
    InboxDeliveryLog,
    RedeliveryFloor,
    deliver_inbox_entry,
)
from agents_remember.serving.inbox_reclamation import (
    InboxReclamationPlan,
    plan_confirmed_gone_reclamation,
)
from agents_remember.serving.pane_signals import classify_pane_signal
from agents_remember.serving.retire import SeatClosure, retire_entry
from agents_remember.serving.supervisor_models import (
    SupervisorActionResult,
    SupervisorContext,
    SupervisorFinding,
    SupervisorSweepResult,
)
from agents_remember.serving.supervisor_models import (
    SweepState as _SweepState,
)
from agents_remember.serving.terminal_catalog import TerminalCatalog, TerminalCatalogEntry
from agents_remember.serving.terminal_paste import capture_pane as default_capture_pane

# R1 (260707-HFX2-L4): conservative built-in fallbacks when a caller (a test, or a context built
# before settings are read) supplies no per-kind/per-rung knobs. ``serving/app.py`` always wires
# the settings-driven ``EscalationSettings`` values in (mirroring how it already resolves
# ``supervisor``/``expectations`` knobs into this module's plain-primitive fields) -- this module
# stays decoupled from the kernel settings loader, R3's "every predicate reads its store directly"
# extended to knobs: no settings TYPE crosses into this file, only resolved numbers.
DEFAULT_ESCALATION_SLA_SECONDS = 300.0
DEFAULT_ESCALATION_RUNG_SECONDS = 900.0
PERSISTENT_FAILURE_ATTEMPTS = 5
"""Attempt count past which an unacked inbox row is handed to the escalation ladder (R4d),
through ``operator_inbox_transitions.mark_escalated``. What walks it from there is
``controlplane.escalation_ladder``, which anchors every rung's dwell on the ``escalatedAt``
that transition stamps."""

_INACTIVE_EXPECTATION_KINDS = frozenset({"briefed-by", "verdict-by", "ack-by"})
# --- R2: predicates ----------------------------------------------------------------------------


def evaluate_pane_findings(
    catalog: TerminalCatalog, *, pane_capturer=default_capture_pane
) -> list[SupervisorFinding]:
    """Diagnostic-only pane classifications; the production sweep does not act on them."""
    findings: list[SupervisorFinding] = []
    for entry in catalog.list():
        if entry.kind != "harness" or entry.status != "running":
            continue
        classification = classify_pane_signal(pane_capturer(entry.tmux_name), harness=entry.harness)
        if classification.signal == "normal":
            continue
        findings.append(
            SupervisorFinding(
                kind="pane-signal",
                detail=classification.signal,
                session_id=entry.id,
                leaf_key=entry.leaf_key,
                seat_role=entry.binding_role,
            )
        )
    return findings


def evaluate_expectation_findings(
    store: ExpectationRowStore,
    *,
    now: datetime,
    catalog: TerminalCatalog | None = None,
) -> list[SupervisorFinding]:
    """R2b: expectation-deadline expiry (briefed-by / verdict-by / ack-by; turn-report-by is
    handled by :func:`evaluate_turn_report_findings` instead, since it needs a second check)."""
    return [
        SupervisorFinding(
            kind="expectation-overdue",
            detail=row.kind,
            session_id=row.subjectAgentId,
            leaf_key=row.leafKey,
            seat_role=row.seatRole,
            source_id=row.id,
        )
        for row in store.overdue(now=now)
        if row.kind in _INACTIVE_EXPECTATION_KINDS
        and not _expectation_chain_progressed(catalog, row)
    ]


def turn_report_path_for_leaf_key(coordination_root: Path, leaf_key: str) -> Path | None:
    """The standard worker turn-report path for a qualified ``repo/master/leaf-id`` key, or
    ``None`` when the key is not in that shape (a malformed/legacy row -- never guessed at)."""
    parts = leaf_key.split("/", 2)
    if len(parts) != 3 or not all(parts):
        return None
    repo, master, doc_id = parts
    task_root = coordination_root / "tasks" / repo / master
    artifact = turn_report_artifact(task_root, doc_id, title=doc_id, runtime_root=coordination_root)
    return Path(artifact.path)


def evaluate_turn_report_findings(
    store: ExpectationRowStore,
    *,
    coordination_root: Path,
    now: datetime,
    catalog: TerminalCatalog | None = None,
) -> list[SupervisorFinding]:
    """R2c: turn-report staleness -- ``missing_artifact()`` finally gets its caller.

    An overdue ``turn-report-by`` row alone is R2b's job; this predicate additionally confirms the
    artifact itself is missing/empty before firing, so a worker who wrote the report but hasn't
    yet had the row consumed does not trip a stale-report action.
    """
    findings: list[SupervisorFinding] = []
    for row in store.overdue(now=now):
        if row.kind != "turn-report-by" or row.leafKey is None:
            continue
        if _expectation_chain_progressed(catalog, row):
            continue
        path = turn_report_path_for_leaf_key(coordination_root, row.leafKey)
        if path is not None and missing_artifact(path):
            findings.append(
                SupervisorFinding(
                    kind="turn-report-stale",
                    detail=str(path),
                    session_id=row.subjectAgentId,
                    leaf_key=row.leafKey,
                    seat_role=row.seatRole,
                    source_id=row.id,
                )
            )
    return findings


def evaluate_inbox_findings(
    store: OperatorInboxStore,
    *,
    now: datetime,
    rate_limit_seconds: float | None = None,
    current: dict[str, OperatorInboxEntry] | None = None,
    limit: int | None = None,
) -> list[SupervisorFinding]:
    """R2d: unacked-row redelivery due (past backoff, clear of the per-target rate limit)."""
    entries = store.list_redeliverable(
        now=now,
        rate_limit_seconds=rate_limit_seconds,
        current=current,
    )
    if limit is not None:
        entries = entries[:limit]
    return [
        SupervisorFinding(
            kind="inbox-redeliverable",
            detail=entry.messageKind,
            session_id=entry.agentId,
            leaf_key=entry.leafKey,
            seat_role=entry.seatRole,
            source_id=entry.id,
        )
        for entry in entries
    ]


def _ladder_terminal_and_dead(catalog: TerminalCatalog, entry: OperatorInboxEntry) -> bool:
    """True only for a pending row whose terminal rung cannot land on a live target seat."""
    return (
        entry.state == "pending"
        and entry.rung >= MAX_RUNG
        and entry.agentId is not None
        and is_seat_dead(catalog, entry.agentId)
    )


def evaluate_ladder_terminal_findings(
    store: OperatorInboxStore,
    catalog: TerminalCatalog,
    *,
    current: dict[str, OperatorInboxEntry] | None = None,
) -> list[SupervisorFinding]:
    """R1: ladder-complete rows for dead seats become terminal, distinct from ack."""
    entries = store.current() if current is None else current
    return [
        SupervisorFinding(
            kind="inbox-ladder-terminal",
            detail="ladder-resolved",
            session_id=entry.agentId,
            leaf_key=entry.leafKey,
            seat_role=entry.seatRole,
            source_id=entry.id,
        )
        for entry in entries.values()
        if _ladder_terminal_and_dead(catalog, entry)
    ]


def _age_seconds(iso_text: str, now: datetime) -> float | None:
    try:
        return (now - datetime.fromisoformat(iso_text)).total_seconds()
    except ValueError:
        return None


def _expectation_chain_progressed(catalog: TerminalCatalog | None, row: ExpectationRow) -> bool:
    return bool(
        catalog is not None
        and row.leafKey is not None
        and leaf_chain_has_progress(
            catalog,
            leaf_key=row.leafKey,
            subject_agent_id=row.subjectAgentId,
            since=row.createdAt,
        )
    )


def _inactivity_signal_chain_progressed(
    catalog: TerminalCatalog, entry: OperatorInboxEntry
) -> bool:
    """Whether real leaf-chain progress invalidated one supervisor inactivity root cause."""
    return bool(
        entry.createdBy == "supervisor"
        and entry.ask.startswith("Supervisor observed seat-liveness:")
        and entry.leafKey is not None
        and leaf_chain_has_progress(
            catalog,
            leaf_key=entry.leafKey,
            subject_agent_id=entry.subjectAgentId,
            since=entry.createdAt,
        )
    )


def _stale_turn_state_due(
    catalog: TerminalCatalog,
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
    if entry.leaf_key is None:
        return True
    return not leaf_chain_has_progress(
        catalog,
        leaf_key=entry.leaf_key,
        subject_agent_id=entry.id,
        since=entry.turn_state_changed_at,
    )


def evaluate_seat_liveness_findings(
    catalog: TerminalCatalog, *, now: datetime, stale_seconds: float
) -> list[SupervisorFinding]:
    """R2e: the L5 hysteresis + L8 turn-state join, with graceful degradation.

    A row already classified by the L8 turn-state prober (``turn_state``/``turn_state_changed_at``
    set) fires when it has sat ``stale`` past ``stale_seconds``. A row the L8 prober has never
    classified (legacy/degraded) falls back to the L5 primitive alone: any recorded liveness
    failure on an otherwise-``running`` row is itself the signal -- ``has_session`` (the row is
    still ``running``) plus catalog status (failures counted but not yet exit-marked).
    """
    findings: list[SupervisorFinding] = []
    for entry in catalog.list():
        if entry.kind != "harness" or entry.status != "running":
            continue
        if entry.turn_state is not None and entry.turn_state_changed_at is not None:
            if not _stale_turn_state_due(catalog, entry, now=now, stale_seconds=stale_seconds):
                continue
            findings.append(
                SupervisorFinding(
                    kind="seat-liveness",
                    detail="turn-state-stale",
                    session_id=entry.id,
                    leaf_key=entry.leaf_key,
                    seat_role=entry.binding_role,
                )
            )
        elif entry.liveness_failures > 0:
            findings.append(
                SupervisorFinding(
                    kind="seat-liveness",
                    detail="liveness-degraded",
                    session_id=entry.id,
                    leaf_key=entry.leaf_key,
                    seat_role=entry.binding_role,
                )
            )
    return findings


def _delivery_failure_still_retrying(entry: OperatorInboxEntry) -> bool:
    """Delivery-failure rows exhaust redelivery before the generic unacked ladder takes over."""
    if dispatch_stays_on_exact_session(entry):
        return True
    return (
        entry.escalatedAt is None
        and entry.deliveryState in ("no-hosted-session", "unconfirmed")
        and entry.attemptCount < PERSISTENT_FAILURE_ATTEMPTS
    )


@dataclass(frozen=True)
class EscalationSchedule:
    """When an unacked row is due for its next ladder rung.

    The SLA (how long a kind of message may sit unacked) and the rung dwell (how long each rung of
    the ladder waits before the next) are one timetable: raising the SLA without the dwell just
    moves where the same storm starts. ``rung_due`` needs both for every row.
    """

    sla_seconds: dict[str, float] = field(default_factory=dict)
    rung_seconds: dict[int, float] = field(default_factory=dict)


def evaluate_escalation_findings(
    store: OperatorInboxStore,
    *,
    now: datetime,
    schedule: EscalationSchedule,
    catalog: TerminalCatalog | None = None,
    current: dict[str, OperatorInboxEntry] | None = None,
) -> list[SupervisorFinding]:
    """R2: every pending, unacked row due for its NEXT ladder rung (escalation_ladder.rung_due)."""
    findings: list[SupervisorFinding] = []
    entries = store.current() if current is None else current
    for entry in entries.values():
        if catalog is not None and _inactivity_signal_chain_progressed(catalog, entry):
            continue
        if _delivery_failure_still_retrying(entry):
            continue
        sla = schedule.sla_seconds.get(entry.messageKind, DEFAULT_ESCALATION_SLA_SECONDS)
        dwell = schedule.rung_seconds.get(entry.rung, DEFAULT_ESCALATION_RUNG_SECONDS)
        if rung_due(entry, now=now, sla_seconds=sla, rung_seconds=dwell):
            findings.append(
                SupervisorFinding(
                    kind="escalation-due",
                    detail=entry.messageKind,
                    session_id=entry.agentId,
                    leaf_key=entry.leafKey,
                    seat_role=entry.seatRole,
                    source_id=entry.id,
                )
            )
    return findings


def evaluate_dead_upstream_findings(catalog: TerminalCatalog) -> list[SupervisorFinding]:
    """R4 (P-6 made mechanical): every live spawned worker/manager seat whose OWN direct owner is
    dead, per catalog spawn provenance. Doctrine: the seat never absorbs its dead owner's role --
    it continues its own brief; this predicate is what tells its grandparent to look."""
    findings: list[SupervisorFinding] = []
    for entry in catalog.list():
        if entry.kind != "harness" or entry.status != "running":
            continue
        if entry.binding_role not in ("worker", "manager"):
            continue
        if entry.spawned_by_session is None:
            continue  # no recorded provenance at all is a legacy/unrouted row, not a dead owner
        if not is_seat_dead(catalog, entry.spawned_by_session):
            continue
        findings.append(
            SupervisorFinding(
                kind="dead-upstream",
                detail="owner-dead",
                session_id=entry.id,
                leaf_key=entry.leaf_key,
                seat_role=entry.binding_role,
            )
        )
    return findings


def evaluate_predicates(
    ctx: SupervisorContext, *, now: datetime, sweep: _SweepState | None = None
) -> list[SupervisorFinding]:
    """R2: run every predicate over its store, directly (R3) -- the sweep's full finding set."""
    findings: list[SupervisorFinding] = []
    inbox_current = sweep.inbox_current if sweep is not None else None
    findings += evaluate_expectation_findings(ctx.expectation_store, now=now, catalog=ctx.catalog)
    findings += evaluate_turn_report_findings(
        ctx.expectation_store,
        coordination_root=ctx.coordination_root,
        now=now,
        catalog=ctx.catalog,
    )
    findings += evaluate_ladder_terminal_findings(
        ctx.inbox_store, ctx.catalog, current=inbox_current
    )
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
            if not _ladder_terminal_and_dead(ctx.catalog, entry)
            and not _inactivity_signal_chain_progressed(ctx.catalog, entry)
        ][: sweep.redeliver_budget]
        findings += [
            SupervisorFinding(
                kind="inbox-redeliverable",
                detail=entry.messageKind,
                session_id=entry.agentId,
                leaf_key=None,
                source_id=entry.id,
            )
            for entry in budgeted
        ]
    findings += evaluate_seat_liveness_findings(
        ctx.catalog, now=now, stale_seconds=ctx.stale_seat_seconds
    )
    # CS-6 D1 load-shed: cap escalation-rung emission per sweep (twin of the redeliver budget).
    # Dropped rows stay rung_due and re-fire next sweep (level-triggered), so nothing is lost --
    # only the per-sweep burst that pegged the river with escalation.rung rows is bounded.
    findings += evaluate_escalation_findings(
        ctx.inbox_store,
        now=now,
        catalog=ctx.catalog,
        current=inbox_current,
        schedule=EscalationSchedule(
            sla_seconds=ctx.escalation_sla_seconds, rung_seconds=ctx.escalation_rung_seconds
        ),
    )[: max(1, ctx.escalation_budget)]
    findings += evaluate_dead_upstream_findings(ctx.catalog)
    return findings


# --- R4: actions ---------------------------------------------------------------------------------


def _log_event(ctx: SupervisorContext, kind: str, data: dict[str, object]) -> None:
    ctx.event_store.append(
        Event(id=new_ulid(), ts=now_iso(), kind=kind, trust="observed", actor="system", data=data)
    )


def _redeliver(
    ctx: SupervisorContext,
    finding: SupervisorFinding,
    *,
    now: datetime,
    sweep: _SweepState,
) -> SupervisorActionResult:
    if finding.source_id is None:
        return SupervisorActionResult("redeliver", finding, "skipped", "no source entry id")
    entry = sweep.inbox_current.get(finding.source_id)
    if entry is None or entry.state != "pending":
        return SupervisorActionResult("redeliver", finding, "skipped", "entry not pending")
    if _ladder_terminal_and_dead(ctx.catalog, entry):
        return _resolve_ladder_terminal(ctx, finding, now=now, sweep=sweep)
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
    )
    sweep.remember(updated)
    fulfill_briefed_expectation(
        ctx.expectation_store,
        updated,
        current=sweep.expectation_current,
    )
    _log_event(
        ctx,
        "orchestration.supervisor.redeliver",
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
    return SupervisorActionResult(
        "redeliver", finding, updated.deliveryState, updated.deliveryDetail
    )


def _resolve_ladder_terminal(
    ctx: SupervisorContext,
    finding: SupervisorFinding,
    *,
    now: datetime,
    sweep: _SweepState,
) -> SupervisorActionResult:
    if finding.source_id is None:
        return SupervisorActionResult("ladder-resolve", finding, "skipped", "no source entry id")
    try:
        resolved, resolved_now = inbox_transitions.mark_ladder_resolved(
            ctx.inbox_store,
            finding.source_id,
            now=now.isoformat(),
            reason="terminal ladder rung reached for non-live target seat",
            current=sweep.inbox_current,
        )
    except KeyError:
        return SupervisorActionResult("ladder-resolve", finding, "skipped", "entry missing")
    sweep.remember(resolved)
    if resolved_now:
        _log_event(
            ctx,
            "orchestration.supervisor.ladder-resolved",
            {
                "entryId": resolved.id,
                "agentId": resolved.agentId,
                "rung": resolved.rung,
                "state": resolved.state,
                "ladderResolvedAt": resolved.ladderResolvedAt,
            },
        )
    return SupervisorActionResult(
        "ladder-resolve", finding, resolved.state, resolved.ladderResolvedReason
    )


def _escalate_inbox_entry(
    ctx: SupervisorContext, entry_id: str, *, now: datetime, sweep: _SweepState
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
        "orchestration.supervisor.escalate",
        {"entryId": escalated.id, "kind": "inbox", "escalatedAt": escalated.escalatedAt},
    )


def _nudge_reason(finding: SupervisorFinding) -> NudgeReason:
    return "missing-turn-report" if finding.kind == "turn-report-stale" else "inactive"


def _auto_nudge(
    ctx: SupervisorContext,
    finding: SupervisorFinding,
    *,
    now: datetime,
    sweep: _SweepState,
) -> SupervisorActionResult:
    owner = derive_signal_owner(
        ctx.catalog,
        sender_agent_id=finding.session_id,
        message_kind="nudge",
        leaf_key=finding.leaf_key,
    )
    if owner.agent_id is None and owner.lifecycle_id is None and owner.role is None:
        return SupervisorActionResult("auto-nudge", finding, "skipped", "no routable owner")
    reason = _nudge_reason(finding)
    subject = finding.leaf_key or finding.session_id or finding.detail
    message = nudge_message(
        reason,
        subject=subject,
        artifact_path=finding.detail if finding.kind == "turn-report-stale" else None,
    )
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
            artifactPath=finding.detail if finding.kind == "turn-report-stale" else None,
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
        return SupervisorActionResult("auto-nudge", finding, "rate-limited", message)
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
        now=now,
        sweep=sweep,
    )
    _mark_expectation_missed(ctx, finding, now=now, sweep=sweep)
    return SupervisorActionResult("auto-nudge", finding, "sent", delivered)


def _mark_expectation_missed(
    ctx: SupervisorContext,
    finding: SupervisorFinding,
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


def _find_coalescible(
    entries: dict[str, OperatorInboxEntry],
    *,
    ask: str,
    message_kind: InboxMessageKind,
    leaf_key: str | None,
    seat_role: str | None,
) -> OperatorInboxEntry | None:
    """The ruled coalescing lookup (developer, 2026-07-09): a supervisor-authored condition that
    is still pending under the SAME ask is the row to renew -- matched on content, not address,
    so a row the ladder has re-addressed still coalesces with its re-firing root condition."""
    for row in entries.values():
        if (
            row.state == "pending"
            and row.createdBy == "supervisor"
            and row.messageKind == message_kind
            and row.ask == ask
            and row.leafKey == leaf_key
            and row.seatRole == seat_role
        ):
            return row
    return None


@dataclass(frozen=True)
class OwnerSignal:
    """One owner-addressed signal: what is being said, and about which seat.

    The message and its subject are inseparable here -- coalescing looks up an existing row by
    (ask, kind, leaf, role), and renewal rewrites the subject from the same value, so a message
    carrying someone else's subject silently renews the wrong row.
    """

    message_kind: InboxMessageKind
    ask: str
    response: str
    leaf_key: str | None = None
    seat_role: str | None = None
    subject_agent_id: str | None = None


def _post_owner_signal(
    ctx: SupervisorContext,
    owner: RoutedOwner,
    signal: OwnerSignal,
    *,
    now: datetime,
    sweep: _SweepState | None = None,
) -> InboxDeliveryState:
    """R4c: emit one owner-addressed signal row (L1 routing), attempt hosted delivery, and write
    its ack-by expectation row -- the same atomic-at-post shape every other dispatch surface uses.

    Ruled invariant (developer, 2026-07-09): one row per root cause. A condition that re-fires
    while its row is still pending RENEWS that row (bumped date, refreshed detail) instead of
    appending a duplicate -- the storm that took the host down was this function minting a new
    pending row per re-fire, each of which the ladder then escalated into more rows."""
    entries = sweep.inbox_current if sweep is not None else ctx.inbox_store.current()
    subject = InboxSubject(
        leaf_key=signal.leaf_key, seat_role=signal.seat_role, agent_id=signal.subject_agent_id
    )
    existing = _find_coalescible(
        entries,
        ask=signal.ask,
        message_kind=signal.message_kind,
        leaf_key=signal.leaf_key,
        seat_role=signal.seat_role,
    )
    if existing is not None:
        entry = inbox_transitions.renew(
            ctx.inbox_store,
            existing.id,
            InboxRenewal(
                response=signal.response,
                subject=subject,
                readdress_to=InboxOwner(
                    role=owner.role, agent_id=owner.agent_id, lifecycle_id=owner.lifecycle_id
                ),
            ),
            now=now.isoformat(),
            current=entries,
        )
    else:
        entry = create_operator_inbox_entry(
            InboxMessage(
                ask=signal.ask,
                response=signal.response,
                message_kind=signal.message_kind,
                subject=subject,
            ),
            entry_id=new_ulid(),
            now=now.isoformat(),
            routing=InboxRouting(
                address=InboxAddress(
                    lifecycle_id=owner.lifecycle_id,
                    agent_id=owner.agent_id,
                    recipient_role=owner.role,
                ),
                owner=InboxOwner(
                    role=owner.role, agent_id=owner.agent_id, lifecycle_id=owner.lifecycle_id
                ),
            ),
            poster=InboxPoster(created_by="supervisor", created_via="cli", sender_role="system"),
        )
        ctx.inbox_store.append(entry)
    if sweep is not None:
        sweep.remember(entry)
    delivered = deliver_inbox_entry(
        InboxDeliveryLog(
            store=ctx.inbox_store,
            entry=entry,
            at=now.isoformat(),
            floor=RedeliveryFloor(
                current=sweep.inbox_current if sweep is not None else None,
                seconds=ctx.redeliver_rate_limit_seconds,
            ),
        ),
        sessions=HostedSessionRuntime(catalog=ctx.catalog, host=ctx.host),
        paster=ctx.paster,
    )
    if sweep is not None:
        sweep.remember(delivered)
    return delivered.deliveryState


def _signal_emit(
    ctx: SupervisorContext,
    finding: SupervisorFinding,
    *,
    now: datetime,
    sweep: _SweepState,
) -> SupervisorActionResult:
    owner = derive_signal_owner(
        ctx.catalog,
        sender_agent_id=finding.session_id,
        message_kind="escalation",
        leaf_key=finding.leaf_key,
    )
    if owner.agent_id is None and owner.lifecycle_id is None and owner.role is None:
        return SupervisorActionResult("signal-emit", finding, "skipped", "no routable owner")
    signal_key = SupervisorSignalKey(
        target=SupervisorSignalTarget(
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
        return SupervisorActionResult("signal-emit", finding, "cooldown", "signal cooldown active")
    ask = f"Supervisor observed {finding.kind}: {finding.detail}"
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
        now=now,
        sweep=sweep,
    )
    signal_record = SupervisorSignalRecord(
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
        "orchestration.supervisor.signal",
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
    return SupervisorActionResult("signal-emit", finding, delivery_state)


def _rung_entry(
    finding: SupervisorFinding,
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


def _escalate_rung(
    ctx: SupervisorContext,
    finding: SupervisorFinding,
    *,
    now: datetime,
    sweep: _SweepState,
) -> SupervisorActionResult:
    """R2: advance one pending, unacked row to its next ladder rung -- renudge (1), skip-level
    (2), or architect custody (3, terminal) -- and durably stamp the transition.

    Ruled invariant (developer, 2026-07-09): the ladder never mints rows. Each rung transition
    MUTATES the one root row -- re-anchors its dwell clock, re-addresses it to the next owner,
    and redelivers it (which increments its attempt count). The prior shape (a new pending row
    per transition, itself ladder-eligible) was a branching process: with an absent developer it
    grew the inbox to 20k+ pending rows and took the host down."""
    entry, refusal = _rung_entry(finding, sweep)
    if entry is None:
        return SupervisorActionResult("escalate-rung", finding, "skipped", refusal)
    sweep.escalated_entry_ids.add(entry.id)
    step = next_step(ctx.catalog, entry)
    if step.owner.agent_id is None and step.owner.role is None:
        return SupervisorActionResult("escalate-rung", finding, "skipped", "no routable owner")
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
        terminal_finding = SupervisorFinding(
            kind="inbox-ladder-terminal",
            detail="ladder-resolved",
            session_id=advanced.agentId,
            leaf_key=finding.leaf_key,
            seat_role=finding.seat_role,
            source_id=advanced.id,
        )
        _resolve_ladder_terminal(ctx, terminal_finding, now=now, sweep=sweep)
    return SupervisorActionResult("escalate-rung", finding, delivery_state, step.action)


def _respawn_suspect(
    ctx: SupervisorContext,
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
            edge="supervisor-respawn",
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
            now=now,
            sweep=sweep,
        )
    _log_event(
        ctx,
        "orchestration.supervisor.respawn",
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


def _signal_dead_upstream(
    ctx: SupervisorContext,
    finding: SupervisorFinding,
    *,
    now: datetime,
    sweep: _SweepState,
) -> SupervisorActionResult:
    """R4: hand a leaf whose recorded owner died to its current responsible manager.

    Address-time manager resolution repairs stale manager provenance. The ordinary ladder owns any
    later climb; this dead-upstream detector must not skip directly to the orchestrator/architect.
    """
    owner = derive_leaf_manager_owner(
        ctx.catalog, sender_agent_id=finding.session_id, leaf_key=finding.leaf_key
    )
    if owner.agent_id is None and owner.role is None:
        return SupervisorActionResult("signal-manager", finding, "skipped", "no manager address")
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
        now=now,
        sweep=sweep,
    )
    _log_event(
        ctx,
        "orchestration.supervisor.dead-upstream",
        {
            "sessionId": finding.session_id,
            "leafKey": finding.leaf_key,
            "seatRole": finding.seat_role,
            "managerRole": owner.role,
            "managerAgentId": owner.agent_id,
            "deliveryState": delivery_state,
        },
    )
    return SupervisorActionResult("signal-manager", finding, delivery_state)


_FindingAction = Callable[..., SupervisorActionResult]

# One predicate kind -> the one act-phase handler that answers it. A finding kind with no entry
# here is reported as unhandled rather than silently doing nothing, so adding a predicate without
# its handler is visible in the sweep result instead of being lost.
_FINDING_ACTIONS: dict[str, _FindingAction] = {
    "inbox-redeliverable": _redeliver,
    "inbox-ladder-terminal": _resolve_ladder_terminal,
    "expectation-overdue": _auto_nudge,
    "turn-report-stale": _auto_nudge,
    "escalation-due": _escalate_rung,
    "dead-upstream": _signal_dead_upstream,
    "seat-liveness": _signal_emit,
}


def act_on_finding(
    ctx: SupervisorContext,
    finding: SupervisorFinding,
    *,
    now: datetime,
    sweep: _SweepState | None = None,
) -> SupervisorActionResult:
    if sweep is None:
        current = ctx.inbox_store.current()
        sweep = _SweepState(inbox_current=current, redeliver_budget=ctx.redeliver_budget)
    action = _FINDING_ACTIONS.get(finding.kind)
    if action is None:
        return SupervisorActionResult("none", finding, "skipped", "unhandled finding kind")
    return action(ctx, finding, now=now, sweep=sweep)


# --- the sweep itself ------------------------------------------------------------------------


def run_supervisor_sweep(ctx: SupervisorContext, *, now: datetime) -> SupervisorSweepResult:
    """One full R1-R5 sweep: evaluate every predicate, act on every finding, tick the heartbeat.

    Every action is logged as an ``orchestration.supervisor.*`` (or the reused ``orchestration.
    nudge``) observer event so the dashboard river shows what code did on whose behalf (R4). The
    heartbeat ticks LAST, unconditionally -- even a sweep with zero findings proves supervisor
    liveness (R5).
    """
    started = perf_counter()
    # R1-R8 (260712-TRH-L5): fold once under the inbox writer lock, join one catalog snapshot,
    # terminally resolve only positively-gone supervisor alerts, then compact before selecting
    # anything for redelivery. The existing TTL/cap compaction remains the fallback in this same
    # transaction. Holding the lock through resolve+compact makes a concurrent consume authoritative.
    reclamation: InboxReclamationPlan | None = None

    def reconcile(current: dict[str, OperatorInboxEntry]) -> dict[str, str]:
        nonlocal reclamation
        reclamation = plan_confirmed_gone_reclamation(
            current,
            catalog_entries=ctx.catalog.list(include_terminated=True),
            snapshotter=ctx.tmux_name_snapshotter,
        )
        return dict(reclamation.resolve_reasons)

    inbox_removed, current, resolved_entries = ctx.inbox_store.reconcile_and_compact(
        now=now,
        reconcile=reconcile,
    )
    if inbox_removed or resolved_entries:
        data: dict[str, object] = {"removed": inbox_removed, "kept": len(current)}
        if reclamation is not None and reclamation.candidate_row_count:
            data.update(
                {
                    "resolvedRowCount": len(resolved_entries),
                    "uniqueSubjectCount": reclamation.unique_subject_count,
                    "keptCandidateCount": reclamation.kept_row_count,
                    "evidenceClass": reclamation.evidence_class,
                }
            )
        _log_event(
            ctx,
            "orchestration.supervisor.inbox-compacted",
            data,
        )
    # CS-6 D2/D3: read + reclaim the append-only signal cooldown log ONCE per sweep. compact()
    # drops rows older than the cooldown window (they can no longer suppress a signal) and returns
    # the kept snapshot, which every per-finding in_cooldown check reads in-memory -- so the store
    # is read once per sweep and bounded on disk, instead of re-parsed once per finding forever.
    signal_retain = require_redelivery_floor_seconds(
        ctx.signal_cooldown_seconds, owner="supervisor signal cooldown"
    )
    _signals_removed, signal_snapshot = ctx.signal_cooldown_store.compact(
        now=now, retain_seconds=signal_retain
    )
    # CS-6 D3 (F4): read + reclaim the expectation log ONCE per sweep — compact() folds by id, drops
    # met/missed rows past the retention window, and returns the kept snapshot that both the finding
    # act-phase mark and the sweep reuse. After the first compaction the file stays bounded, so this
    # one read is O(bounded), not O(daemon-lifetime).
    _expectations_removed, expectation_snapshot = ctx.expectation_store.compact(now=now)
    sweep = _SweepState(
        inbox_current=current,
        redeliver_budget=max(1, ctx.redeliver_budget),
        pending_inbox_count=sum(1 for entry in current.values() if entry.state == "pending"),
        redeliverable_entries=ctx.inbox_store.list_redeliverable(
            now=now,
            rate_limit_seconds=ctx.redeliver_rate_limit_seconds,
            current=current,
        ),
        signal_current=signal_snapshot,
        expectation_current=expectation_snapshot,
    )
    findings = evaluate_predicates(ctx, now=now, sweep=sweep)
    actions = tuple(act_on_finding(ctx, finding, now=now, sweep=sweep) for finding in findings)
    duration_seconds = perf_counter() - started
    ctx.heartbeat_store.tick(
        now=now,
        pending_inbox_count=sweep.pending_inbox_count,
        redeliverable_inbox_count=sweep.redeliverable_inbox_count,
        last_sweep_duration_seconds=duration_seconds,
    )
    return SupervisorSweepResult(
        findings=tuple(findings),
        actions=actions,
        swept_at=now.isoformat(),
        pending_inbox_count=sweep.pending_inbox_count,
        redeliverable_inbox_count=sweep.redeliverable_inbox_count,
        duration_seconds=duration_seconds,
    )
