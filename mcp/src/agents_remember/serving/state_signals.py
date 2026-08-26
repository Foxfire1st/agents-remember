"""Task-structural state-signal relay over catalog turn truth."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import cast

from agents_remember.controlplane.operator_inbox_records import (
    OperatorInboxEntry,
    state_signal_landed,
)
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.controlplane.seats import current_seat_occupant
from agents_remember.controlplane.signal_routing import TaskHierarchy, is_seat_dead
from agents_remember.errors import SeatOccupancyError
from agents_remember.models.terminal_catalog import TerminalCatalogEntry, seat_at_turn_boundary
from agents_remember.serving.agent_notifier_models import AgentNotifierFinding
from agents_remember.serving.inbox_delivery import target_session_for_entry
from agents_remember.serving.ports import TerminalCatalogPort

NON_REACTION_WINDOW_SECONDS = 300.0
COMPOUND_IDLE_SWEEP_LATENCY_SECONDS = 10.0
_LEAF_ROLES = frozenset({"worker", "reviewer", "curator"})


@dataclass(frozen=True)
class NonReactionRuntime:
    """Structural stores required to re-evaluate one non-reaction episode."""

    catalog: TerminalCatalogPort
    hierarchy: TaskHierarchy
    inbox_store: OperatorInboxStore


@dataclass(frozen=True)
class _NonReactionEvaluation:
    runtime: NonReactionRuntime
    current: dict[str, OperatorInboxEntry]
    now: datetime
    window: float


def _manager_owned_subordinate(
    hierarchy: TaskHierarchy,
    manager: TerminalCatalogEntry,
    entry: TerminalCatalogEntry,
) -> bool:
    """Whether ``entry`` occupies a leaf directly contained by ``manager``'s master."""

    manager_document = manager.binding_task_document_ref
    entry_document = entry.binding_task_document_ref
    return bool(
        manager_document is not None
        and entry_document is not None
        and manager.binding_role == "manager"
        and entry.binding_role in _LEAF_ROLES
        and hierarchy.parent(entry_document) == manager_document
    )


def _manager_for_subordinate(
    catalog: TerminalCatalogPort,
    hierarchy: TaskHierarchy,
    entry: TerminalCatalogEntry,
) -> TerminalCatalogEntry | None:
    document = entry.binding_task_document_ref
    if document is None or entry.binding_role not in _LEAF_ROLES:
        return None
    master = hierarchy.parent(document)
    if master is None:
        return None
    return current_seat_occupant(catalog.list(), document=master, role="manager")


def _current_manager_rows(
    rows: list[TerminalCatalogEntry],
) -> list[TerminalCatalogEntry]:
    """Select one current manager per canonical master seat from a running snapshot."""

    documents = {
        row.binding_task_document_ref
        for row in rows
        if row.binding_role == "manager" and row.binding_task_document_ref is not None
    }
    managers: list[TerminalCatalogEntry] = []
    for document in documents:
        try:
            manager = cast(
                TerminalCatalogEntry,
                current_seat_occupant(rows, document=document, role="manager"),
            )
        except SeatOccupancyError:
            # An observer must not choose between conflicting generations or abort unrelated
            # signal delivery. The ambiguous canonical seat simply emits no derived idle signal.
            continue
        # Each document was projected from this same running-manager snapshot, so one
        # primary or staged-replacement claimant exists unless the seat was ambiguous above.
        managers.append(manager)
    return managers


def _running_harness_rows(catalog: TerminalCatalogPort) -> list[TerminalCatalogEntry]:
    return [
        entry for entry in catalog.list() if entry.kind == "harness" and entry.status == "running"
    ]


def _manager_idle_members(
    hierarchy: TaskHierarchy,
    running: list[TerminalCatalogEntry],
    manager: TerminalCatalogEntry,
) -> tuple[TerminalCatalogEntry, ...]:
    return (
        manager,
        *(
            entry
            for entry in running
            if entry.id != manager.id and _manager_owned_subordinate(hierarchy, manager, entry)
        ),
    )


def compound_idle_sets(
    catalog: TerminalCatalogPort,
    hierarchy: TaskHierarchy,
) -> dict[str, tuple[TerminalCatalogEntry, ...]]:
    """Every current manager whose complete running task-owned set is at a boundary."""

    running = _running_harness_rows(catalog)
    sets: dict[str, tuple[TerminalCatalogEntry, ...]] = {}
    for manager in _current_manager_rows(running):
        members = _manager_idle_members(hierarchy, running, manager)
        if len(members) == 1:
            continue
        if all(member.turn_state in {"turn-ended", "awaiting-input"} for member in members):
            sets[manager.id] = members
    return sets


def compound_idle_signature(members: tuple[TerminalCatalogEntry, ...]) -> str:
    """Private identity of one idle episode."""

    return ";".join(
        sorted(
            f"{member.id}:{member.turn_state}:{member.turn_state_changed_at or ''}"
            for member in members
        )
    )


def state_signal_held_on_boundary(catalog: TerminalCatalogPort, entry: OperatorInboxEntry) -> bool:
    if entry.messageKind != "state-signal" or state_signal_landed(entry):
        return False
    try:
        target = target_session_for_entry(catalog, entry)
    except SeatOccupancyError:
        # Ambiguity fences this row from redelivery but must not abort unrelated sweep work.
        return True
    return target is not None and target.status == "running"


def evaluate_state_signal_findings(
    catalog: TerminalCatalogPort,
    hierarchy: TaskHierarchy,
) -> list[AgentNotifierFinding]:
    return [
        finding
        for entry in catalog.list()
        if (finding := _safe_state_signal_finding(catalog, hierarchy, entry)) is not None
    ]


def _safe_state_signal_finding(
    catalog: TerminalCatalogPort,
    hierarchy: TaskHierarchy,
    entry: TerminalCatalogEntry,
) -> AgentNotifierFinding | None:
    try:
        return _state_signal_finding(catalog, hierarchy, entry)
    except SeatOccupancyError:
        return None


def current_state_signal_finding(
    catalog: TerminalCatalogPort,
    hierarchy: TaskHierarchy,
    *,
    session_id: str,
    source_id: str,
) -> tuple[TerminalCatalogEntry, AgentNotifierFinding] | None:
    entry = catalog.get(session_id)
    if entry is None:
        return None
    finding = _safe_state_signal_finding(catalog, hierarchy, entry)
    if finding is None or finding.source_id != source_id:
        return None
    return entry, finding


def _state_signal_finding(
    catalog: TerminalCatalogPort,
    hierarchy: TaskHierarchy,
    entry: TerminalCatalogEntry,
) -> AgentNotifierFinding | None:
    manager = _manager_for_subordinate(catalog, hierarchy, entry)
    if not (
        entry.kind == "harness"
        and entry.status == "running"
        and manager is not None
        and entry.turn_state == "turn-ended"
        and entry.terminal_outcome in {"completed", "interrupted"}
        and entry.terminal_evidence_id is not None
        and entry.state_signal_emitted_for != entry.terminal_evidence_id
    ):
        return None
    return AgentNotifierFinding(
        kind="state-signal-due",
        detail=entry.terminal_outcome,
        session_id=entry.id,
        task_document_ref=entry.binding_task_document_ref,
        seat_role=entry.binding_role,
        source_id=entry.terminal_evidence_id,
    )


def evaluate_compound_idle_findings(
    catalog: TerminalCatalogPort,
    hierarchy: TaskHierarchy,
) -> list[AgentNotifierFinding]:
    findings: list[AgentNotifierFinding] = []
    for manager_id, members in compound_idle_sets(catalog, hierarchy).items():
        manager = members[0]
        signature = compound_idle_signature(members)
        if manager.compound_idle_emitted_for == signature:
            continue
        findings.append(
            AgentNotifierFinding(
                kind="compound-idle-due",
                detail="compound-idle",
                session_id=manager_id,
                task_document_ref=manager.binding_task_document_ref,
                seat_role=manager.binding_role,
                source_id=signature,
            )
        )
    return findings


def evaluate_non_reaction_findings(
    catalog: TerminalCatalogPort,
    hierarchy: TaskHierarchy,
    inbox_store: OperatorInboxStore,
    *,
    now: datetime,
    window: float = NON_REACTION_WINDOW_SECONDS,
) -> list[AgentNotifierFinding]:
    current = inbox_store.current()
    evaluation = _NonReactionEvaluation(
        NonReactionRuntime(catalog, hierarchy, inbox_store),
        current,
        now,
        window,
    )
    return [
        finding
        for entry in catalog.list()
        if (finding := _safe_non_reaction_finding(evaluation, entry)) is not None
    ]


def _safe_non_reaction_finding(
    evaluation: _NonReactionEvaluation,
    entry: TerminalCatalogEntry,
) -> AgentNotifierFinding | None:
    try:
        return _non_reaction_finding(evaluation, entry)
    except SeatOccupancyError:
        return None


def current_non_reaction_finding(
    runtime: NonReactionRuntime,
    finding: AgentNotifierFinding,
    *,
    now: datetime,
    window: float = NON_REACTION_WINDOW_SECONDS,
) -> tuple[TerminalCatalogEntry, OperatorInboxEntry, AgentNotifierFinding] | None:
    if finding.session_id is None or finding.source_id is None:
        return None
    entry = runtime.catalog.get(finding.session_id)
    if entry is None:
        return None
    current = runtime.inbox_store.current()
    current_finding = _safe_non_reaction_finding(
        _NonReactionEvaluation(runtime, current, now, window),
        entry,
    )
    if current_finding is None or current_finding.source_id != finding.source_id:
        return None
    return entry, current[finding.source_id], current_finding


def _non_reaction_finding(
    evaluation: _NonReactionEvaluation,
    entry: TerminalCatalogEntry,
) -> AgentNotifierFinding | None:
    is_subject = _is_non_reaction_subject(evaluation, entry)
    if not (
        entry.kind == "harness"
        and entry.status == "running"
        and entry.turn_state == "turn-ended"
        and is_subject
    ):
        return None
    episode = _oldest_landed_episode(evaluation.current, entry)
    if episode is None:
        return None
    oldest, accepted_at = episode
    if (
        entry.non_reaction_emitted_for == oldest.id
        or (evaluation.now - accepted_at).total_seconds() < evaluation.window
    ):
        return None
    return AgentNotifierFinding(
        kind="non-reaction-due",
        detail=oldest.id,
        session_id=entry.id,
        task_document_ref=entry.binding_task_document_ref,
        seat_role=entry.binding_role,
        source_id=oldest.id,
    )


def _is_non_reaction_subject(
    evaluation: _NonReactionEvaluation,
    entry: TerminalCatalogEntry,
) -> bool:
    """Whether this exact generation currently owns a manager or subordinate seat."""

    document = entry.binding_task_document_ref
    current_manager = (
        current_seat_occupant(evaluation.runtime.catalog.list(), document=document, role="manager")
        if entry.binding_role == "manager" and document is not None
        else None
    )
    return (
        current_manager is not None and current_manager.id == entry.id
    ) or _manager_for_subordinate(
        evaluation.runtime.catalog,
        evaluation.runtime.hierarchy,
        entry,
    ) is not None


def _oldest_landed_episode(
    current: dict[str, OperatorInboxEntry], entry: TerminalCatalogEntry
) -> tuple[OperatorInboxEntry, datetime] | None:
    landed = [
        row
        for row in current.values()
        if row.state == "landed"
        and row.deliveredToSession == entry.id
        and row.adapterDeliveryState == "accepted"
        and row.adapterAcceptedAt is not None
    ]
    if not landed:
        return None
    oldest = min(landed, key=lambda row: row.adapterAcceptedAt or "")
    try:
        accepted_at = datetime.fromisoformat(oldest.adapterAcceptedAt or "")
    except ValueError:
        return None
    if accepted_at.utcoffset() is None:
        return None
    return oldest, accepted_at


def evaluate_boundary_drain_findings(
    catalog: TerminalCatalogPort,
    current: dict[str, OperatorInboxEntry],
    *,
    limit: int | None = None,
) -> list[AgentNotifierFinding]:
    findings: list[AgentNotifierFinding] = []
    for entry in current.values():
        if entry.state != "pending" or state_signal_landed(entry):
            continue
        if entry.agentId is not None and is_seat_dead(catalog, entry.agentId):
            continue
        if entry.lastAttemptAt is None:
            continue
        try:
            target = target_session_for_entry(catalog, entry)
        except SeatOccupancyError:
            # This row remains pending until its own canonical seat becomes unambiguous.
            continue
        if (
            target is None
            or not seat_at_turn_boundary(target)
            or target.turn_state_changed_at is None
        ):
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
                task_document_ref=entry.taskDocumentRef,
                seat_role=entry.seatRole,
                source_id=entry.id,
            )
        )
    return findings[:limit] if limit is not None else findings


def _seat_label(entry: TerminalCatalogEntry) -> str:
    document = entry.binding_task_document_ref
    return f"{document.key if document is not None else '-'} as {entry.binding_role}"


def state_signal_response(entry: TerminalCatalogEntry) -> str:
    origin = entry.interrupted_by or "-"
    return (
        f"seat {_seat_label(entry)} turn {entry.terminal_evidence_id or '-'} "
        f"outcome {entry.terminal_outcome or 'unknown'} at {entry.terminal_outcome_at or '-'} "
        f"interrupted_by={origin}"
    )


def compound_idle_response(members: tuple[TerminalCatalogEntry, ...]) -> str:
    manager = members[0]
    subordinates = ", ".join(_seat_label(entry) for entry in members[1:])
    return f"compound-idle set: manager {_seat_label(manager)}; subordinates {subordinates or '-'}"


def non_reaction_response(entry: TerminalCatalogEntry, row: OperatorInboxEntry) -> str:
    return (
        f"seat {_seat_label(entry)} landed-row {row.id} accepted-at "
        f"{row.adapterAcceptedAt or '-'} still turn-ended (non-reaction fact)"
    )
