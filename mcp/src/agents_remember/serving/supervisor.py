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
from dataclasses import field as dataclass_field
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Literal

from agents_remember.controlplane.escalation_ladder import (
    MAX_RUNG,
    next_step,
    rung_due,
    seat_is_suspect,
)
from agents_remember.controlplane.expectation_rows import ExpectationRowStore
from agents_remember.controlplane.operator_inbox_records import (
    InboxMessageKind,
    OperatorInboxEntry,
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
from agents_remember.controlplane.orphan_policy import find_orphaned_workers
from agents_remember.controlplane.signal_routing import (
    RoutedOwner,
    derive_signal_owner,
    derive_skip_level_owner,
    is_seat_dead,
)
from agents_remember.observer.events import Event, now_iso
from agents_remember.observer.store import EventStore
from agents_remember.observer.ulid import new_ulid
from agents_remember.serving.inbox_delivery import deliver_inbox_entry
from agents_remember.serving.pane_signals import classify_pane_signal
from agents_remember.serving.retire import retire_entry
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
    "inbox-ladder-terminal",
    "seat-liveness",
    "escalation-due",
    "dead-upstream",
]
ActionKind = Literal[
    "redeliver",
    "ladder-resolve",
    "auto-nudge",
    "signal-emit",
    "escalate-rung",
    "signal-grandparent",
    "none",
]

# R1 (260707-HFX2-L4): conservative built-in fallbacks when a caller (a test, or a context built
# before settings are read) supplies no per-kind/per-rung knobs. ``serving/app.py`` always wires
# the settings-driven ``EscalationSettings`` values in (mirroring how it already resolves
# ``supervisor``/``expectations`` knobs into this module's plain-primitive fields) -- this module
# stays decoupled from the kernel settings loader, R3's "every predicate reads its store directly"
# extended to knobs: no settings TYPE crosses into this file, only resolved numbers.
DEFAULT_ESCALATION_SLA_SECONDS = 300.0
DEFAULT_ESCALATION_RUNG_SECONDS = 900.0
DEFAULT_RESPAWN_AFTER_RUNG = 2

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
    pending_inbox_count: int = 0
    redeliverable_inbox_count: int = 0
    duration_seconds: float | None = None


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
    # R1 (260707-HFX2-L4): the escalation ladder's own knobs -- per-``message_kind`` ack SLA
    # (rung 0 -> 1), per-rung dwell time thereafter (keyed 1/2), and the rung a silent seat is
    # marked suspect-for-respawn at (R3). ``None`` entries in the dicts fall back to the module
    # defaults above.
    escalation_sla_seconds: dict[str, float] = dataclass_field(default_factory=dict)
    escalation_rung_seconds: dict[int, float] = dataclass_field(default_factory=dict)
    respawn_after_rung: int = DEFAULT_RESPAWN_AFTER_RUNG
    redeliver_budget: int = 250


@dataclass
class _SweepState:
    inbox_current: dict[str, OperatorInboxEntry]
    redeliver_budget: int
    pending_inbox_count: int = 0
    redeliverable_entries: list[OperatorInboxEntry] = dataclass_field(default_factory=list)

    @property
    def redeliverable_inbox_count(self) -> int:
        return len(self.redeliverable_entries)

    def remember(self, entry: OperatorInboxEntry) -> None:
        self.inbox_current[entry.id] = entry


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
            leaf_key=None,
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
            leaf_key=None,
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


def evaluate_escalation_findings(
    store: OperatorInboxStore,
    *,
    now: datetime,
    sla_seconds: dict[str, float],
    rung_seconds: dict[int, float],
    current: dict[str, OperatorInboxEntry] | None = None,
) -> list[SupervisorFinding]:
    """R2: every pending, unacked row due for its NEXT ladder rung (escalation_ladder.rung_due)."""
    findings: list[SupervisorFinding] = []
    entries = store.current() if current is None else current
    for entry in entries.values():
        sla = sla_seconds.get(entry.messageKind, DEFAULT_ESCALATION_SLA_SECONDS)
        dwell = rung_seconds.get(entry.rung, DEFAULT_ESCALATION_RUNG_SECONDS)
        if rung_due(entry, now=now, sla_seconds=sla, rung_seconds=dwell):
            findings.append(
                SupervisorFinding(
                    kind="escalation-due",
                    detail=entry.messageKind,
                    session_id=entry.agentId,
                    leaf_key=None,
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
        if entry.spawn_role not in ("worker", "manager"):
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
            )
        )
    return findings


def evaluate_predicates(
    ctx: SupervisorContext, *, now: datetime, sweep: _SweepState | None = None
) -> list[SupervisorFinding]:
    """R2: run every predicate over its store, directly (R3) -- the sweep's full finding set."""
    findings: list[SupervisorFinding] = []
    inbox_current = sweep.inbox_current if sweep is not None else None
    findings += evaluate_pane_findings(ctx.catalog)
    findings += evaluate_expectation_findings(ctx.expectation_store, now=now)
    findings += evaluate_turn_report_findings(
        ctx.expectation_store, coordination_root=ctx.coordination_root, now=now
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
    findings += evaluate_escalation_findings(
        ctx.inbox_store,
        now=now,
        sla_seconds=ctx.escalation_sla_seconds,
        rung_seconds=ctx.escalation_rung_seconds,
        current=inbox_current,
    )
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
        store=ctx.inbox_store,
        catalog=ctx.catalog,
        host=ctx.host,
        paster=ctx.paster,
        entry=entry,
        submit=True,
        current=sweep.inbox_current,
    )
    sweep.remember(updated)
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
        _escalate_inbox_entry(ctx, updated.id, now=now, sweep=sweep)
    return SupervisorActionResult("redeliver", finding, updated.deliveryState, updated.deliveryDetail)


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
        resolved, resolved_now = ctx.inbox_store.mark_ladder_resolved(
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
    return SupervisorActionResult("ladder-resolve", finding, resolved.state, resolved.ladderResolvedReason)


def _escalate_inbox_entry(
    ctx: SupervisorContext, entry_id: str, *, now: datetime, sweep: _SweepState
) -> None:
    """R4d: hand a persistently-failing row to the escalation ladder -- this leaf only stamps the
    reserved ``escalatedAt`` hook HFX2-L4's ladder will read; it builds no ladder itself."""
    try:
        escalated = ctx.inbox_store.mark_escalated(
            entry_id, now=now.isoformat(), current=sweep.inbox_current
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
        ctx,
        owner,
        message_kind="nudge",
        ask=f"Nudge: {subject}",
        response=message,
        now=now,
        sweep=sweep,
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
    sweep: _SweepState | None = None,
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
    if sweep is not None:
        sweep.remember(entry)
    delivered = deliver_inbox_entry(
        store=ctx.inbox_store,
        catalog=ctx.catalog,
        host=ctx.host,
        paster=ctx.paster,
        entry=entry,
        submit=True,
        current=sweep.inbox_current if sweep is not None else None,
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
    owner = derive_signal_owner(ctx.catalog, sender_agent_id=finding.session_id, message_kind="escalation")
    if owner.agent_id is None and owner.lifecycle_id is None and owner.role is None:
        return SupervisorActionResult("signal-emit", finding, "skipped", "no routable owner")
    ask = f"Supervisor observed {finding.kind}: {finding.detail}"
    response = f"session {finding.session_id or 'unknown'} (leaf {finding.leaf_key or 'unknown'})"
    delivery_state = _post_owner_signal(
        ctx,
        owner,
        message_kind="escalation",
        ask=ask,
        response=response,
        now=now,
        sweep=sweep,
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


def _escalate_rung(
    ctx: SupervisorContext,
    finding: SupervisorFinding,
    *,
    now: datetime,
    sweep: _SweepState,
) -> SupervisorActionResult:
    """R2: advance one pending, unacked row to its next ladder rung -- renudge (1), skip-level
    (2), or the developer attention queue (3, terminal) -- and durably stamp the transition."""
    if finding.source_id is None:
        return SupervisorActionResult("escalate-rung", finding, "skipped", "no source entry id")
    entry = sweep.inbox_current.get(finding.source_id)
    if entry is None or entry.state != "pending":
        return SupervisorActionResult("escalate-rung", finding, "skipped", "entry not pending")
    step = next_step(ctx.catalog, entry)
    if step.owner.agent_id is None and step.owner.role is None:
        return SupervisorActionResult("escalate-rung", finding, "skipped", "no routable owner")
    ask = (
        f"Escalation rung {step.rung} ({step.action}): {entry.messageKind} unacked "
        f"since {entry.createdAt} (original entry {entry.id})"
    )
    response = f"Original ask: {entry.ask}\nOriginal response: {entry.response}"
    message_kind: InboxMessageKind = "nudge" if step.action == "renudge" else "escalation"
    delivery_state = _post_owner_signal(
        ctx,
        step.owner,
        message_kind=message_kind,
        ask=ask,
        response=response,
        now=now,
        sweep=sweep,
    )
    advanced = ctx.inbox_store.advance_rung(
        entry.id, rung=step.rung, now=now.isoformat(), current=sweep.inbox_current
    )
    sweep.remember(advanced)
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
        at=now.isoformat(),
        by_session=None,
        reason="escalation-ladder-suspect",
        edge="supervisor-respawn",
    )
    orphaned: list[str] = []
    if entry.spawn_role == "manager":
        orphaned = [worker.id for worker in find_orphaned_workers(ctx.catalog, manager_agent_id=agent_id)]
    delivery_state = "skipped"
    if owner.agent_id is not None or owner.role is not None:
        ask = f"Respawn directive: seat {agent_id} ({entry.spawn_role}) retired as suspect (R3)"
        response = f"Pending queue for the successor: {pending_queue}. Orphaned workers: {orphaned}."
        delivery_state = _post_owner_signal(
            ctx,
            owner,
            message_kind="escalation",
            ask=ask,
            response=response,
            now=now,
            sweep=sweep,
        )
    _log_event(
        ctx,
        "orchestration.supervisor.respawn",
        {
            "agentId": agent_id,
            "spawnRole": entry.spawn_role,
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
    """R4: signal the seat's grandparent (the same 2-hop, dead-node-skipping walk rung 2 uses) --
    doctrine: the seat never absorbs its dead owner's role, it continues its own brief."""
    owner = derive_skip_level_owner(
        ctx.catalog, sender_agent_id=finding.session_id, message_kind="escalation"
    )
    if owner.agent_id is None and owner.role is None:
        return SupervisorActionResult("signal-grandparent", finding, "skipped", "no routable grandparent")
    ask = (
        f"Dead-upstream (R4/P-6): seat {finding.session_id or 'unknown'} lost its owner; "
        "it continues its own brief and escalates -- it never absorbs the dead owner's role."
    )
    response = f"leaf {finding.leaf_key or 'unknown'}"
    delivery_state = _post_owner_signal(
        ctx,
        owner,
        message_kind="escalation",
        ask=ask,
        response=response,
        now=now,
        sweep=sweep,
    )
    _log_event(
        ctx,
        "orchestration.supervisor.dead-upstream",
        {
            "sessionId": finding.session_id,
            "leafKey": finding.leaf_key,
            "grandparentRole": owner.role,
            "grandparentAgentId": owner.agent_id,
            "deliveryState": delivery_state,
        },
    )
    return SupervisorActionResult("signal-grandparent", finding, delivery_state)


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
    if finding.kind == "inbox-redeliverable":
        return _redeliver(ctx, finding, now=now, sweep=sweep)
    if finding.kind == "inbox-ladder-terminal":
        return _resolve_ladder_terminal(ctx, finding, now=now, sweep=sweep)
    if finding.kind in ("expectation-overdue", "turn-report-stale"):
        return _auto_nudge(ctx, finding, now=now, sweep=sweep)
    if finding.kind == "escalation-due":
        return _escalate_rung(ctx, finding, now=now, sweep=sweep)
    if finding.kind == "dead-upstream":
        return _signal_dead_upstream(ctx, finding, now=now, sweep=sweep)
    if finding.kind in ("pane-signal", "seat-liveness"):
        return _signal_emit(ctx, finding, now=now, sweep=sweep)
    return SupervisorActionResult("none", finding, "skipped", "unhandled finding kind")


# --- the sweep itself ------------------------------------------------------------------------


def run_supervisor_sweep(ctx: SupervisorContext, *, now: datetime) -> SupervisorSweepResult:
    """One full R1-R5 sweep: evaluate every predicate, act on every finding, tick the heartbeat.

    Every action is logged as an ``orchestration.supervisor.*`` (or the reused ``orchestration.
    nudge``) observer event so the dashboard river shows what code did on whose behalf (R4). The
    heartbeat ticks LAST, unconditionally -- even a sweep with zero findings proves supervisor
    liveness (R5).
    """
    started = perf_counter()
    current = ctx.inbox_store.current()
    sweep = _SweepState(
        inbox_current=current,
        redeliver_budget=max(1, ctx.redeliver_budget),
        pending_inbox_count=sum(1 for entry in current.values() if entry.state == "pending"),
        redeliverable_entries=ctx.inbox_store.list_redeliverable(
            now=now,
            rate_limit_seconds=ctx.redeliver_rate_limit_seconds,
            current=current,
        ),
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
