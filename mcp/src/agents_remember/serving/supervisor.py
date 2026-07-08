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
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from agents_remember.controlplane.expectation_rows import ExpectationRowStore
from agents_remember.controlplane.operator_inbox_records import (
    InboxMessageKind,
    create_operator_inbox_entry,
)
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.controlplane.orchestration_artifacts import turn_report_artifact
from agents_remember.controlplane.orchestration_nudges import (
    NudgeReason,
    OrchestrationNudgeRecord,
    OrchestrationNudgeStore,
    missing_artifact,
    nudge_message,
)
from agents_remember.controlplane.signal_routing import RoutedOwner, derive_signal_owner
from agents_remember.observer.events import Event, now_iso
from agents_remember.observer.store import EventStore
from agents_remember.observer.ulid import new_ulid
from agents_remember.serving.inbox_delivery import deliver_inbox_entry
from agents_remember.serving.pane_signals import classify_pane_signal
from agents_remember.serving.supervisor_heartbeat import SupervisorHeartbeatStore
from agents_remember.serving.terminal import TerminalHost
from agents_remember.serving.terminal_catalog import TerminalCatalog
from agents_remember.serving.terminal_paste import TerminalPaster
from agents_remember.serving.terminal_paste import capture_pane as default_capture_pane

FindingKind = Literal[
    "pane-signal",
    "expectation-overdue",
    "turn-report-stale",
    "inbox-redeliverable",
    "seat-liveness",
]
ActionKind = Literal["redeliver", "auto-nudge", "signal-emit", "none"]

PERSISTENT_FAILURE_ATTEMPTS = 5
"""Attempt count past which an unacked inbox row is handed to the escalation ladder (R4d): this
leaf only reserves the transition (``OperatorInboxStore.mark_escalated``) -- HFX2-L4 owns the
actual ladder that reads it."""

_INACTIVE_EXPECTATION_KINDS = frozenset({"briefed-by", "verdict-by", "ack-by"})


@dataclass(frozen=True)
class SupervisorFinding:
    """One predicate hit: what the sweep saw, read straight off a store (never the projection)."""

    kind: FindingKind
    detail: str
    session_id: str | None = None
    leaf_key: str | None = None
    source_id: str | None = None


@dataclass(frozen=True)
class SupervisorActionResult:
    """One action the sweep took (or explicitly skipped) for a finding."""

    action: ActionKind
    finding: SupervisorFinding
    outcome: str
    detail: str | None = None


@dataclass(frozen=True)
class SupervisorSweepResult:
    findings: tuple[SupervisorFinding, ...]
    actions: tuple[SupervisorActionResult, ...]
    swept_at: str


@dataclass(frozen=True)
class SupervisorContext:
    """Everything one sweep needs -- stores and the delivery seam, read/called directly (R3)."""

    catalog: TerminalCatalog
    host: TerminalHost
    paster: TerminalPaster
    inbox_store: OperatorInboxStore
    expectation_store: ExpectationRowStore
    nudge_store: OrchestrationNudgeStore
    event_store: EventStore
    heartbeat_store: SupervisorHeartbeatStore
    coordination_root: Path
    stale_seat_seconds: float = 120.0
    redeliver_rate_limit_seconds: float | None = None
    nudge_rate_limit_seconds: int = 900


# --- R2: predicates ----------------------------------------------------------------------------


def evaluate_pane_findings(
    catalog: TerminalCatalog, *, pane_capturer=default_capture_pane
) -> list[SupervisorFinding]:
    """R2a: the per-harness pane-state classifier over every RUNNING chat row."""
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
            )
        )
    return findings


def evaluate_expectation_findings(
    store: ExpectationRowStore, *, now: datetime
) -> list[SupervisorFinding]:
    """R2b: expectation-deadline expiry (briefed-by / verdict-by / ack-by; turn-report-by is
    handled by :func:`evaluate_turn_report_findings` instead, since it needs a second check)."""
    return [
        SupervisorFinding(
            kind="expectation-overdue",
            detail=row.kind,
            session_id=row.subjectAgentId,
            leaf_key=row.leafKey,
            source_id=row.id,
        )
        for row in store.overdue(now=now)
        if row.kind in _INACTIVE_EXPECTATION_KINDS
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
    store: ExpectationRowStore, *, coordination_root: Path, now: datetime
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
        path = turn_report_path_for_leaf_key(coordination_root, row.leafKey)
        if path is not None and missing_artifact(path):
            findings.append(
                SupervisorFinding(
                    kind="turn-report-stale",
                    detail=str(path),
                    session_id=row.subjectAgentId,
                    leaf_key=row.leafKey,
                    source_id=row.id,
                )
            )
    return findings


def evaluate_inbox_findings(
    store: OperatorInboxStore, *, now: datetime, rate_limit_seconds: float | None = None
) -> list[SupervisorFinding]:
    """R2d: unacked-row redelivery due (past backoff, clear of the per-target rate limit)."""
    return [
        SupervisorFinding(
            kind="inbox-redeliverable",
            detail=entry.messageKind,
            session_id=entry.agentId,
            leaf_key=None,
            source_id=entry.id,
        )
        for entry in store.list_redeliverable(now=now, rate_limit_seconds=rate_limit_seconds)
    ]


def _age_seconds(iso_text: str, now: datetime) -> float | None:
    try:
        return (now - datetime.fromisoformat(iso_text)).total_seconds()
    except ValueError:
        return None


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
            if entry.turn_state != "stale":
                continue
            age = _age_seconds(entry.turn_state_changed_at, now)
            if age is None or age < stale_seconds:
                continue
            findings.append(
                SupervisorFinding(
                    kind="seat-liveness",
                    detail="turn-state-stale",
                    session_id=entry.id,
                    leaf_key=entry.leaf_key,
                )
            )
        elif entry.liveness_failures > 0:
            findings.append(
                SupervisorFinding(
                    kind="seat-liveness",
                    detail="liveness-degraded",
                    session_id=entry.id,
                    leaf_key=entry.leaf_key,
                )
            )
    return findings


def evaluate_predicates(ctx: SupervisorContext, *, now: datetime) -> list[SupervisorFinding]:
    """R2: run every predicate over its store, directly (R3) -- the sweep's full finding set."""
    findings: list[SupervisorFinding] = []
    findings += evaluate_pane_findings(ctx.catalog)
    findings += evaluate_expectation_findings(ctx.expectation_store, now=now)
    findings += evaluate_turn_report_findings(
        ctx.expectation_store, coordination_root=ctx.coordination_root, now=now
    )
    findings += evaluate_inbox_findings(
        ctx.inbox_store, now=now, rate_limit_seconds=ctx.redeliver_rate_limit_seconds
    )
    findings += evaluate_seat_liveness_findings(
        ctx.catalog, now=now, stale_seconds=ctx.stale_seat_seconds
    )
    return findings


# --- R4: actions ---------------------------------------------------------------------------------


def _log_event(ctx: SupervisorContext, kind: str, data: dict[str, object]) -> None:
    ctx.event_store.append(
        Event(id=new_ulid(), ts=now_iso(), kind=kind, trust="observed", actor="system", data=data)
    )


def _redeliver(ctx: SupervisorContext, finding: SupervisorFinding, *, now: datetime) -> SupervisorActionResult:
    if finding.source_id is None:
        return SupervisorActionResult("redeliver", finding, "skipped", "no source entry id")
    entry = ctx.inbox_store.current().get(finding.source_id)
    if entry is None or entry.state != "pending":
        return SupervisorActionResult("redeliver", finding, "skipped", "entry not pending")
    updated = deliver_inbox_entry(
        store=ctx.inbox_store, catalog=ctx.catalog, host=ctx.host, paster=ctx.paster, entry=entry, submit=True
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
    if updated.deliveryState != "delivered" and updated.attemptCount >= PERSISTENT_FAILURE_ATTEMPTS:
        _escalate_inbox_entry(ctx, updated.id, now=now)
    return SupervisorActionResult("redeliver", finding, updated.deliveryState, updated.deliveryDetail)


def _escalate_inbox_entry(ctx: SupervisorContext, entry_id: str, *, now: datetime) -> None:
    """R4d: hand a persistently-failing row to the escalation ladder -- this leaf only stamps the
    reserved ``escalatedAt`` hook HFX2-L4's ladder will read; it builds no ladder itself."""
    try:
        escalated = ctx.inbox_store.mark_escalated(entry_id, now=now.isoformat())
    except KeyError:
        return
    _log_event(
        ctx,
        "orchestration.supervisor.escalate",
        {"entryId": escalated.id, "kind": "inbox", "escalatedAt": escalated.escalatedAt},
    )


def _nudge_reason(finding: SupervisorFinding) -> NudgeReason:
    return "missing-turn-report" if finding.kind == "turn-report-stale" else "inactive"


def _auto_nudge(ctx: SupervisorContext, finding: SupervisorFinding, *, now: datetime) -> SupervisorActionResult:
    owner = derive_signal_owner(ctx.catalog, sender_agent_id=finding.session_id, message_kind="nudge")
    if owner.agent_id is None and owner.lifecycle_id is None:
        return SupervisorActionResult("auto-nudge", finding, "skipped", "no routable owner")
    reason = _nudge_reason(finding)
    subject = finding.leaf_key or finding.session_id or finding.detail
    message = nudge_message(
        reason, subject=subject, artifact_path=finding.detail if finding.kind == "turn-report-stale" else None
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
        _mark_expectation_missed(ctx, finding, now=now)
        return SupervisorActionResult("auto-nudge", finding, "rate-limited", message)
    delivered = _post_owner_signal(
        ctx, owner, message_kind="nudge", ask=f"Nudge: {subject}", response=message, now=now
    )
    _mark_expectation_missed(ctx, finding, now=now)
    return SupervisorActionResult("auto-nudge", finding, "sent", delivered)


def _mark_expectation_missed(ctx: SupervisorContext, finding: SupervisorFinding, *, now: datetime) -> None:
    """The sweep is the reserved caller of ``mark_missed`` (expectation_rows.py:93-97): an overdue
    row the sweep has now acted on is marked missed, idempotently, every sweep it stays overdue."""
    if finding.source_id is None:
        return
    with contextlib.suppress(KeyError):
        ctx.expectation_store.mark_missed(finding.source_id, now=now.isoformat())


def _post_owner_signal(
    ctx: SupervisorContext,
    owner: RoutedOwner,
    *,
    message_kind: InboxMessageKind,
    ask: str,
    response: str,
    now: datetime,
) -> str:
    """R4c: emit one owner-addressed signal row (L1 routing), attempt hosted delivery, and write
    its ack-by expectation row -- the same atomic-at-post shape every other dispatch surface uses."""
    entry = create_operator_inbox_entry(
        entry_id=new_ulid(),
        now=now.isoformat(),
        lifecycle_id=owner.lifecycle_id,
        agent_id=owner.agent_id,
        ask=ask,
        response=response,
        created_by="supervisor",
        created_via="cli",
        sender_role="system",
        recipient_role=owner.role,
        message_kind=message_kind,
        owner_role=owner.role,
        owner_agent_id=owner.agent_id,
        owner_lifecycle_id=owner.lifecycle_id,
    )
    ctx.inbox_store.append(entry)
    delivered = deliver_inbox_entry(
        store=ctx.inbox_store, catalog=ctx.catalog, host=ctx.host, paster=ctx.paster, entry=entry, submit=True
    )
    return delivered.deliveryState


def _signal_emit(ctx: SupervisorContext, finding: SupervisorFinding, *, now: datetime) -> SupervisorActionResult:
    owner = derive_signal_owner(ctx.catalog, sender_agent_id=finding.session_id, message_kind="escalation")
    if owner.agent_id is None and owner.lifecycle_id is None and owner.role is None:
        return SupervisorActionResult("signal-emit", finding, "skipped", "no routable owner")
    ask = f"Supervisor observed {finding.kind}: {finding.detail}"
    response = f"session {finding.session_id or 'unknown'} (leaf {finding.leaf_key or 'unknown'})"
    delivery_state = _post_owner_signal(
        ctx, owner, message_kind="escalation", ask=ask, response=response, now=now
    )
    _log_event(
        ctx,
        "orchestration.supervisor.signal",
        {
            "predicateKind": finding.kind,
            "detail": finding.detail,
            "sessionId": finding.session_id,
            "leafKey": finding.leaf_key,
            "ownerRole": owner.role,
            "deliveryState": delivery_state,
        },
    )
    return SupervisorActionResult("signal-emit", finding, delivery_state)


def act_on_finding(ctx: SupervisorContext, finding: SupervisorFinding, *, now: datetime) -> SupervisorActionResult:
    if finding.kind == "inbox-redeliverable":
        return _redeliver(ctx, finding, now=now)
    if finding.kind in ("expectation-overdue", "turn-report-stale"):
        return _auto_nudge(ctx, finding, now=now)
    if finding.kind in ("pane-signal", "seat-liveness"):
        return _signal_emit(ctx, finding, now=now)
    return SupervisorActionResult("none", finding, "skipped", "unhandled finding kind")


# --- the sweep itself ------------------------------------------------------------------------


def run_supervisor_sweep(ctx: SupervisorContext, *, now: datetime) -> SupervisorSweepResult:
    """One full R1-R5 sweep: evaluate every predicate, act on every finding, tick the heartbeat.

    Every action is logged as an ``orchestration.supervisor.*`` (or the reused ``orchestration.
    nudge``) observer event so the dashboard river shows what code did on whose behalf (R4). The
    heartbeat ticks LAST, unconditionally -- even a sweep with zero findings proves supervisor
    liveness (R5).
    """
    findings = evaluate_predicates(ctx, now=now)
    actions = tuple(act_on_finding(ctx, finding, now=now) for finding in findings)
    ctx.heartbeat_store.tick(now=now)
    return SupervisorSweepResult(findings=tuple(findings), actions=actions, swept_at=now.isoformat())
