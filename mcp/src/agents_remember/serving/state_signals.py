"""Worker→manager state-signal relay: facts from catalog turn truth, never inference.

The relay emits exactly one durable state-signal per seat+turn from the lifted
terminal outcome, holds delivery at the manager's turn boundary, drains pending
rows when a boundary arrives, and surfaces the non-reaction residue fact.
"""

from __future__ import annotations

from datetime import datetime

from agents_remember.controlplane.operator_inbox_records import (
    OperatorInboxEntry,
    state_signal_landed,
)
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.serving.agent_notifier_models import AgentNotifierFinding
from agents_remember.serving.inbox_delivery import target_session_for_entry
from agents_remember.serving.terminal_catalog import (
    TerminalCatalog,
    TerminalCatalogEntry,
    seat_at_turn_boundary,
)

NON_REACTION_WINDOW_SECONDS = 300.0
"""Bounded window after which a seat that landed rows at a boundary and never left
``turn-ended`` is relayed to its owner as the non-reaction residue fact."""


def state_signal_held_on_boundary(catalog: TerminalCatalog, entry: OperatorInboxEntry) -> bool:
    """Whether a non-landed state-signal row is merely boundary-held by a LIVE target seat.

    A live addressee's availability gate owns delivery timing: the row must not be
    redelivered on the backoff schedule or climb the escalation ladder while its target
    is running but not at a turn boundary. Dead/archived targets keep the ordinary
    redelivery/ladder safety net.
    """
    if entry.messageKind != "state-signal" or state_signal_landed(entry):
        return False
    target = target_session_for_entry(catalog, entry)
    return target is not None and target.status == "running"


def evaluate_state_signal_findings(
    catalog: TerminalCatalog,
) -> list[AgentNotifierFinding]:
    """A live seat whose turn ended with a terminal outcome not yet relayed."""
    findings: list[AgentNotifierFinding] = []
    for entry in catalog.list():
        if entry.kind != "harness" or entry.status != "running":
            continue
        if entry.binding_role != "worker":
            continue
        if entry.turn_state != "turn-ended":
            continue
        if entry.terminal_outcome not in {"completed", "interrupted"}:
            continue
        if entry.terminal_evidence_id is None:
            continue
        if entry.state_signal_emitted_for == entry.terminal_evidence_id:
            continue
        findings.append(
            AgentNotifierFinding(
                kind="state-signal-due",
                detail=entry.terminal_outcome,
                session_id=entry.id,
                leaf_key=entry.binding_leaf_key,
                seat_role=entry.binding_role,
                source_id=entry.terminal_evidence_id,
            )
        )
    return findings


def evaluate_non_reaction_findings(
    catalog: TerminalCatalog,
    inbox_store: OperatorInboxStore,
    *,
    now: datetime,
    window: float = NON_REACTION_WINDOW_SECONDS,
) -> list[AgentNotifierFinding]:
    """A seat still ``turn-ended`` long after rows landed at its boundary."""
    current = inbox_store.current()
    findings: list[AgentNotifierFinding] = []
    for entry in catalog.list():
        if entry.kind != "harness" or entry.status != "running":
            continue
        if entry.binding_role != "worker":
            continue
        if entry.turn_state != "turn-ended":
            continue
        landed = [
            row
            for row in current.values()
            if row.state == "pending"
            and row.deliveredToSession == entry.id
            and row.adapterDeliveryState == "accepted"
            and row.adapterAcceptedAt is not None
        ]
        if not landed:
            continue
        oldest = min(landed, key=lambda row: row.adapterAcceptedAt or "")
        if entry.non_reaction_emitted_for == oldest.id:
            continue
        try:
            accepted_at = datetime.fromisoformat(oldest.adapterAcceptedAt or "")
        except ValueError:
            continue
        if (now - accepted_at).total_seconds() < window:
            continue
        findings.append(
            AgentNotifierFinding(
                kind="non-reaction-due",
                detail=oldest.id,
                session_id=entry.id,
                leaf_key=entry.binding_leaf_key,
                seat_role=entry.binding_role,
                source_id=oldest.id,
            )
        )
    return findings


def evaluate_boundary_drain_findings(
    catalog: TerminalCatalog,
    current: dict[str, OperatorInboxEntry],
    *,
    limit: int | None = None,
) -> list[AgentNotifierFinding]:
    """Pending rows whose target seat crossed a turn boundary after the last attempt.

    The boundary transition is the event-driven drain (N15): the durable backoff schedule
    remains the backstop for seats that never go idle. Rows whose last attempt already
    happened at this boundary stay on the schedule.
    """
    findings: list[AgentNotifierFinding] = []
    for entry in current.values():
        if entry.state != "pending":
            continue
        if state_signal_landed(entry):
            continue
        if entry.lastAttemptAt is None:
            continue
        target = target_session_for_entry(catalog, entry)
        if target is None or not seat_at_turn_boundary(target):
            continue
        if target.turn_state_changed_at is None:
            continue
        try:
            boundary_at = datetime.fromisoformat(target.turn_state_changed_at)
            attempted_at = datetime.fromisoformat(entry.lastAttemptAt)
        except ValueError:
            continue
        if boundary_at <= attempted_at:
            continue
        findings.append(
            AgentNotifierFinding(
                kind="boundary-drain",
                detail=entry.messageKind,
                session_id=entry.agentId,
                leaf_key=entry.leafKey,
                seat_role=entry.seatRole,
                source_id=entry.id,
            )
        )
    return findings[:limit] if limit is not None else findings


def state_signal_response(entry: TerminalCatalogEntry) -> str:
    """The self-contained state-signal payload: seat, leaf, turn, outcome, timestamps, origin."""
    origin = entry.interrupted_by or "-"
    return (
        f"session {entry.id} leaf {entry.binding_leaf_key or '-'} "
        f"turn {entry.terminal_evidence_id or '-'} outcome {entry.terminal_outcome or 'unknown'} "
        f"at {entry.terminal_outcome_at or '-'} interrupted_by={origin}"
    )


def non_reaction_response(entry: TerminalCatalogEntry, row: OperatorInboxEntry) -> str:
    """The non-reaction residue fact: rows landed at a boundary, seat never left turn-ended."""
    return (
        f"session {entry.id} leaf {entry.binding_leaf_key or '-'} "
        f"landed-row {row.id} accepted-at {row.adapterAcceptedAt or '-'} "
        "still turn-ended (non-reaction fact)"
    )
