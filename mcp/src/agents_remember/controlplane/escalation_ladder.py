"""P-15 tier 3 (260707-HFX2-L4): the escalation ladder walker.

"No signal dies with a silent seat." An unacked ``OperatorInboxEntry`` climbs the spawn edges
deterministically -- rung 1 (renudge the original addressee) -> rung 2 (skip-level: re-address to
the owner's owner, ``signal_routing.derive_skip_level_owner``, which already walks past any dead
intermediate) -> rung 3 (the developer attention queue, terminal). Every transition is pure here:
this module decides WHAT should happen and WHO the next addressee is; ``serving/supervisor.py``
(the only caller, mirroring how it already owns every other predicate/action pairing) is what
reads the stores, calls this, and performs the delivery + durable row update.

Doctrine (R4, with HFX-L6's capture hardening): a spawned seat NEVER absorbs its dead owner's
role -- it continues its own brief and escalates. This module never reassigns a role; it only ever
finds the next LIVE address to hand a signal to.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from agents_remember.controlplane.operator_inbox_records import OperatorInboxEntry
from agents_remember.controlplane.signal_routing import (
    RoutedOwner,
    derive_skip_level_owner,
    is_seat_dead,
)
from agents_remember.serving.terminal_catalog import TerminalCatalog

MAX_RUNG = 3
LadderAction = Literal["renudge", "skip-level", "developer-attention"]

_ACTION_BY_RUNG: dict[int, LadderAction] = {
    1: "renudge",
    2: "skip-level",
    3: "developer-attention",
}


@dataclass(frozen=True)
class LadderStep:
    """One rung transition this walker recommends for a pending, unacked entry."""

    rung: int
    action: LadderAction
    owner: RoutedOwner


def _dwell_anchor(entry: OperatorInboxEntry) -> str:
    """What the current rung's dwell time is measured from: the last rung transition, or the
    row's own creation when it has never been escalated (rung 0 -> 1)."""
    return entry.escalatedAt or entry.createdAt


def rung_due(
    entry: OperatorInboxEntry,
    *,
    now: datetime,
    sla_seconds: float,
    rung_seconds: float,
) -> bool:
    """Whether ``entry`` (still pending, never consumed=acked) is due for its NEXT rung.

    Rung 0 -> 1 uses ``sla_seconds`` (the per-``message_kind`` ack SLA, R1); every later
    transition uses ``rung_seconds`` (that rung's own dwell time, re-anchored at each transition).
    A row already at :data:`MAX_RUNG` (developer attention) never advances further (R5: no
    auto-action past the developer) -- callers re-surface it via a re-surface reminder instead,
    never by calling this walker again for a rung bump.
    """
    if entry.state != "pending" or entry.rung >= MAX_RUNG:
        return False
    try:
        anchor = datetime.fromisoformat(_dwell_anchor(entry))
    except ValueError:
        return False
    threshold = sla_seconds if entry.rung == 0 else rung_seconds
    return (now - anchor).total_seconds() >= threshold


def next_step(
    catalog: TerminalCatalog,
    entry: OperatorInboxEntry,
) -> LadderStep:
    """The next rung + addressee for ``entry``. The addressee that "silence" is measured against
    is the row's own mailbox key (``agentId``/``lifecycleId``/``recipientRole``) -- the seat (or
    role) that has not acked; rung 2 re-addresses to THAT seat's owner's owner."""
    rung = min(entry.rung + 1, MAX_RUNG)
    action = _ACTION_BY_RUNG[rung]
    if action == "renudge":
        owner = RoutedOwner(
            role=entry.recipientRole, agent_id=entry.agentId, lifecycle_id=entry.lifecycleId
        )
    elif action == "skip-level":
        owner = derive_skip_level_owner(
            catalog, sender_agent_id=entry.agentId, message_kind=entry.messageKind
        )
        if owner.agent_id is None and owner.role is None:
            # The hierarchy ceiling: a manager-addressed row has only one level above it
            # (the orchestrator), so "the owner's owner" resolves to nothing -- there is no
            # further seat to skip to below the developer. Jump straight to rung 3 rather than
            # stalling rung 2 forever (doctrine: no signal dies with a silent seat).
            return LadderStep(
                rung=MAX_RUNG, action="developer-attention", owner=RoutedOwner(role="developer")
            )
    else:
        owner = RoutedOwner(role="developer")
    return LadderStep(rung=rung, action=action, owner=owner)


def seat_is_suspect(
    catalog: TerminalCatalog,
    agent_id: str | None,
    *,
    now: datetime,
    stale_seconds: float,
) -> bool:
    """R3: a seat past rung ``respawn_after_rung`` is "suspect" when it shows no liveness signal
    of its own -- dead outright, or a catalog turn-state that has sat ``stale`` past
    ``stale_seconds`` (the same cutoff the L2 seat-liveness predicate already uses). A row can be
    unacked for reasons that are not the seat's fault (recipient_role-only mailbox, no catalog
    trace); this predicate only ever fires for a seat this module can actually observe as dead or
    stalled, never inferred from silence alone.
    """
    if agent_id is None:
        return False
    if is_seat_dead(catalog, agent_id):
        return True
    entry = catalog.get(agent_id)
    if entry is None or entry.turn_state != "stale" or entry.turn_state_changed_at is None:
        return False
    try:
        changed_at = datetime.fromisoformat(entry.turn_state_changed_at)
    except ValueError:
        return False
    return (now - changed_at).total_seconds() >= stale_seconds
