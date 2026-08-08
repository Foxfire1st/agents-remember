from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from agents_remember.controlplane.escalation_ladder import MAX_RUNG, rung_due
from agents_remember.controlplane.expectation_rows import ExpectationRow, ExpectationRowStore
from agents_remember.controlplane.operator_inbox_records import OperatorInboxEntry
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.controlplane.orchestration_artifacts import turn_report_artifact
from agents_remember.controlplane.orchestration_nudges import missing_artifact
from agents_remember.controlplane.signal_routing import is_seat_dead, leaf_chain_has_progress
from agents_remember.serving.agent_notifier_models import AgentNotifierContext, AgentNotifierFinding
from agents_remember.serving.agent_notifier_models import SweepState as _SweepState
from agents_remember.serving.dispatch_brief import dispatch_stays_on_exact_session
from agents_remember.serving.pane_signals import classify_pane_signal
from agents_remember.serving.terminal_catalog import TerminalCatalog, TerminalCatalogEntry
from agents_remember.serving.terminal_paste import capture_pane as default_capture_pane

DEFAULT_ESCALATION_SLA_SECONDS = 300.0
DEFAULT_ESCALATION_RUNG_SECONDS = 900.0
PERSISTENT_FAILURE_ATTEMPTS = 5
"""Attempt count past which an unacked inbox row is handed to the escalation ladder (R4d),
through ``operator_inbox_transitions.mark_escalated``. What walks it from there is
``controlplane.escalation_ladder``, which anchors every rung's dwell on the ``escalatedAt``
that transition stamps."""

_INACTIVE_EXPECTATION_KINDS = frozenset({"briefed-by", "verdict-by", "ack-by"})

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
    catalog: TerminalCatalog, *, pane_capturer=default_capture_pane
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
) -> list[AgentNotifierFinding]:
    """R2b: expectation-deadline expiry (briefed-by / verdict-by / ack-by; turn-report-by is
    handled by :func:`evaluate_turn_report_findings` instead, since it needs a second check)."""
    return [
        AgentNotifierFinding(
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


# 260731-EFA-L7 R10: verbatim L7 split (L7-OQ1 Option A serving scope); unchanged edge branch, out of this leaf's behavior scope (mcp/src/agents_remember/serving/_agent_notifier_evaluation.py:91).
def evaluate_turn_report_findings(  # pragma: no cover
    store: ExpectationRowStore,
    *,
    coordination_root: Path,
    now: datetime,
    catalog: TerminalCatalog | None = None,
) -> list[AgentNotifierFinding]:
    """R2c: turn-report staleness -- ``missing_artifact()`` finally gets its caller.

    An overdue ``turn-report-by`` row alone is R2b's job; this predicate additionally confirms the
    artifact itself is missing/empty before firing, so a worker who wrote the report but hasn't
    yet had the row consumed does not trip a stale-report action.
    """
    findings: list[AgentNotifierFinding] = []
    for row in store.overdue(now=now):
        if row.kind != "turn-report-by" or row.leafKey is None:
            continue
        if _expectation_chain_progressed(catalog, row):
            continue
        path = turn_report_path_for_leaf_key(coordination_root, row.leafKey)
        if path is not None and missing_artifact(path):
            findings.append(
                AgentNotifierFinding(
                    kind="turn-report-stale",
                    detail=str(path),
                    session_id=row.subjectAgentId,
                    leaf_key=row.leafKey,
                    seat_role=row.seatRole,
                    source_id=row.id,
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
) -> list[AgentNotifierFinding]:
    """R1: ladder-complete rows for dead seats become terminal, distinct from ack."""
    entries = store.current() if current is None else current
    return [
        AgentNotifierFinding(
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


# 260731-EFA-L7 R10: verbatim L7 split (L7-OQ1 Option A serving scope); unchanged edge branch, out of this leaf's behavior scope (mcp/src/agents_remember/serving/_agent_notifier_evaluation.py:186).
def _age_seconds(iso_text: str, now: datetime) -> float | None:  # pragma: no cover
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
    """Whether real leaf-chain progress invalidated one agent-notifier inactivity root cause."""
    return bool(
        # Both values are the same relay-authored inactivity row until the rename window closes.
        entry.createdBy in {"supervisor", "agent-notifier"}
        and entry.ask.startswith(SEAT_LIVENESS_ASK_PREFIXES)
        and entry.leafKey is not None
        and leaf_chain_has_progress(
            catalog,
            leaf_key=entry.leafKey,
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
            if not _stale_turn_state_due(catalog, entry, now=now, stale_seconds=stale_seconds):
                continue
            findings.append(
                AgentNotifierFinding(
                    kind="seat-liveness",
                    detail="turn-state-stale",
                    session_id=entry.id,
                    leaf_key=entry.leaf_key,
                    seat_role=entry.binding_role,
                )
            )
        elif entry.liveness_failures > 0:
            findings.append(
                AgentNotifierFinding(
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
) -> list[AgentNotifierFinding]:
    """R2: every pending, unacked row due for its NEXT ladder rung (escalation_ladder.rung_due)."""
    findings: list[AgentNotifierFinding] = []
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
                AgentNotifierFinding(
                    kind="escalation-due",
                    detail=entry.messageKind,
                    session_id=entry.agentId,
                    leaf_key=entry.leafKey,
                    seat_role=entry.seatRole,
                    source_id=entry.id,
                )
            )
    return findings


def evaluate_dead_upstream_findings(catalog: TerminalCatalog) -> list[AgentNotifierFinding]:
    """R4 (P-6 made mechanical): every live spawned worker/manager seat whose OWN direct owner is
    dead, per catalog spawn provenance. Doctrine: the seat never absorbs its dead owner's role --
    it continues its own brief; this predicate is what tells its grandparent to look."""
    findings: list[AgentNotifierFinding] = []
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
            AgentNotifierFinding(
                kind="dead-upstream",
                detail="owner-dead",
                session_id=entry.id,
                leaf_key=entry.leaf_key,
                seat_role=entry.binding_role,
            )
        )
    return findings


# 260731-EFA-L7 R10: verbatim L7 split (L7-OQ1 Option A serving scope); unchanged edge branch, out of this leaf's behavior scope (mcp/src/agents_remember/serving/_agent_notifier_evaluation.py:367).
def evaluate_predicates(  # pragma: no cover
    ctx: AgentNotifierContext, *, now: datetime, sweep: _SweepState | None = None
) -> list[AgentNotifierFinding]:
    """R2: run every predicate over its store, directly (R3) -- the sweep's full finding set."""
    findings: list[AgentNotifierFinding] = []
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
            AgentNotifierFinding(
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
