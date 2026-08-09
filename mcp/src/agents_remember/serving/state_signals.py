"""Worker→manager and compound-idle state-signal relay: facts from catalog turn truth, never
inference.

The relay emits exactly one durable state-signal per seat+turn from the lifted
terminal outcome, one compound-idle signal per manager set to the owning
orchestrator, holds delivery at the target's turn boundary, drains pending rows
when a boundary arrives, and surfaces the non-reaction residue fact.
"""

from __future__ import annotations

from datetime import datetime

from agents_remember.controlplane.operator_inbox_records import (
    OperatorInboxEntry,
    state_signal_landed,
)
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.controlplane.signal_routing import is_seat_dead, master_key
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

COMPOUND_IDLE_SWEEP_LATENCY_SECONDS = 10.0
"""Upper bound on compound-idle relay latency: one agent-notifier sweep at the default
10 s cadence (N6). The predicate is evaluated from catalog truth at each projection tick,
so a set observed idle is signaled no later than this bound after that tick."""


def _compound_worker_index(
    catalog: TerminalCatalog,
) -> tuple[
    list[TerminalCatalogEntry],
    dict[str, list[TerminalCatalogEntry]],
    dict[str, list[TerminalCatalogEntry]],
]:
    """Live managers and live workers indexed by spawner id and master key.

    One catalog scan feeds every compound-idle set, so the sweep stays O(catalog + sets),
    never O(managers x catalog). Status is gated FIRST here: non-running rows
    (retired/exited/landed) are never indexed, so their stale turn state never counts.
    """
    managers: list[TerminalCatalogEntry] = []
    by_spawner: dict[str, list[TerminalCatalogEntry]] = {}
    by_master: dict[str, list[TerminalCatalogEntry]] = {}
    for entry in catalog.list():
        if entry.kind != "harness" or entry.status != "running":
            continue
        if entry.binding_role == "manager":
            managers.append(entry)
        elif entry.binding_role == "worker":
            if entry.spawned_by_session is not None:
                by_spawner.setdefault(entry.spawned_by_session, []).append(entry)
            leaf = entry.binding_leaf_key or entry.replacement_for_leaf
            master = master_key(leaf)
            if master is not None:
                by_master.setdefault(master, []).append(entry)
    return managers, by_spawner, by_master


def compound_idle_sets(
    catalog: TerminalCatalog,
) -> dict[str, tuple[TerminalCatalogEntry, ...]]:
    """Every live manager's compound-idle member set, keyed by manager id.

    Membership is master-scoped on EVERY arm: a worker joins only when it is bound
    (``binding_leaf_key`` / ``replacement_for_leaf``) to the manager's master,
    whether or not the manager spawned it. A running member with unclassified turn
    state is unknown and fails the set closed; ``working``/``stale`` members mean
    the set is not idle. A manager with no workers never forms a set.
    """
    managers, by_spawner, by_master = _compound_worker_index(catalog)
    sets: dict[str, tuple[TerminalCatalogEntry, ...]] = {}
    for manager in managers:
        manager_master = master_key(manager.binding_leaf_key)
        members = [manager]
        seen: set[str] = set()
        for worker in [
            *by_spawner.get(manager.id, ()),
            *by_master.get(manager_master or "", ()),
        ]:
            if worker.id in seen:
                continue
            leaf = worker.binding_leaf_key
            if manager_master is None or leaf is None or master_key(leaf) != manager_master:
                continue
            seen.add(worker.id)
            members.append(worker)
        if not members[1:]:
            continue
        if all(member.turn_state in {"turn-ended", "awaiting-input"} for member in members):
            sets[manager.id] = tuple(members)
    return sets


def compound_idle_signature(members: tuple[TerminalCatalogEntry, ...]) -> str:
    """Identity of one idle episode: every member's seat, state and boundary time."""
    return ";".join(
        sorted(
            f"{member.id}:{member.turn_state}:{member.turn_state_changed_at or ''}"
            for member in members
        )
    )


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


def evaluate_compound_idle_findings(
    catalog: TerminalCatalog,
) -> list[AgentNotifierFinding]:
    """A manager seat whose whole live set is at a turn boundary, not yet relayed."""
    findings: list[AgentNotifierFinding] = []
    for manager_id, members in compound_idle_sets(catalog).items():
        manager = members[0]
        signature = compound_idle_signature(members)
        if manager.compound_idle_emitted_for == signature:
            continue
        findings.append(
            AgentNotifierFinding(
                kind="compound-idle-due",
                detail="compound-idle",
                session_id=manager_id,
                leaf_key=manager.binding_leaf_key,
                seat_role=manager.binding_role,
                source_id=signature,
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
        if entry.binding_role not in ("worker", "manager"):
            continue
        if entry.turn_state != "turn-ended":
            continue
        landed = [
            row
            for row in current.values()
            if row.state == "landed"
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
        if entry.agentId is not None and is_seat_dead(catalog, entry.agentId):
            # The rebind machinery owns rows to dead seats (N2/N14); a dead seat has no
            # boundary to cross.
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


def compound_idle_response(members: tuple[TerminalCatalogEntry, ...]) -> str:
    """The self-contained compound-idle payload naming every set member."""
    manager = members[0]
    workers = ", ".join(
        f"{entry.id}@{entry.binding_leaf_key or entry.replacement_for_leaf or '-'}"
        for entry in members[1:]
    )
    return (
        f"compound-idle set: manager {manager.id} leaf {manager.binding_leaf_key or '-'} "
        f"workers {workers or '-'}"
    )


def non_reaction_response(entry: TerminalCatalogEntry, row: OperatorInboxEntry) -> str:
    """The non-reaction residue fact: rows landed at a boundary, seat never left turn-ended."""
    return (
        f"session {entry.id} leaf {entry.binding_leaf_key or '-'} "
        f"landed-row {row.id} accepted-at {row.adapterAcceptedAt or '-'} "
        "still turn-ended (non-reaction fact)"
    )
