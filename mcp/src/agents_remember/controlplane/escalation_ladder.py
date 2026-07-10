"""P-15 tier 3 (260707-HFX2-L4): the escalation ladder walker.

"No signal dies with a silent seat." An unacked ``OperatorInboxEntry`` climbs the spawn edges
deterministically -- rung 1 (renudge the original addressee) -> rung 2 (skip-level: re-address to
the owner's owner, ``signal_routing.derive_skip_level_owner``, which already walks past any dead
intermediate) -> rung 3 (architect custody, terminal). Every transition is pure here: this module
decides WHAT should happen and WHO the next addressee is; ``serving/supervisor.py`` (the only
caller, mirroring how it already owns every other predicate/action pairing) is what reads the
stores, calls this, and performs the delivery + durable row update.

Ruled terminal (developer, 2026-07-09): the ladder ends at the ARCHITECT -- the live seat the
human actually talks to -- never at a "developer" mailbox. The human is an authority, not an
address: a human-shaped mailbox can never mechanically ack, which is exactly how the 2026-07-09
storm's rows became immortal. The architect seat acks CUSTODY (consume) and briefs the human with
a catch-up report when they return; it is never renudged repeatedly past that, because the
architect can do nothing to make the human react faster. With no architect seat attached the row
stays role-addressed and level-triggered -- it lands the moment an architect session appears
(delivery or poll-at-session-start) and ages out via the inbox pending TTL if none ever does.

Doctrine (R4, with HFX-L6's capture hardening): a spawned seat NEVER absorbs its dead owner's
role -- it continues its own brief and escalates. This module never reassigns a role; it only ever
finds the next LIVE address to hand a signal to.

Ruled invariant (developer, 2026-07-09): "no signal dies with a silent seat" is subordinate to
"no signal outranks system health". The ladder climbs by mutating ONE row -- re-anchor, re-address,
redeliver, increment tries -- and never mints a sibling row for a transition. Rows the developer
never acks age out via the inbox pending TTL; the artifact on disk, not the inbox row, is the
durable record. The 2026-07-09 escalation storm (each transition posting a new ladder-eligible
pending row, exempt from compaction, addressed to an absent human) is the incident this rule
retires.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from agents_remember.controlplane.operator_inbox_records import OperatorInboxEntry
from agents_remember.controlplane.signal_routing import (
    RoutedOwner,
    derive_architect_owner,
    derive_skip_level_owner,
    is_seat_dead,
)
from agents_remember.serving.terminal_catalog import TerminalCatalog

MAX_RUNG = 3
MIN_RUNG_DWELL_SECONDS = 5 * 60.0
LadderAction = Literal["renudge", "skip-level", "architect-attention"]

_ACTION_BY_RUNG: dict[int, LadderAction] = {
    1: "renudge",
    2: "skip-level",
    3: "architect-attention",
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


def _parse_anchor(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _minimum_dwell_anchor(entry: OperatorInboxEntry, escalated_at: datetime) -> datetime:
    """Redundant hard-floor anchor for later rung transitions.

    ``advance_rung`` stamps both ``escalatedAt`` and the dedicated ``rungTransitionAt``. The normal
    per-rung dwell continues to use ``escalatedAt``; the second stamp independently enforces the
    ruled five-minute safety floor. This redundancy is deliberate: the live 2026-07-09 cascade
    showed that one stale escalation anchor can collapse several rungs into seconds. General row
    ``ts`` is intentionally excluded because delivery and renewal also change it.
    """
    transition_at = _parse_anchor(entry.rungTransitionAt or "")
    return max(escalated_at, transition_at) if transition_at is not None else escalated_at


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
    A row already at :data:`MAX_RUNG` (architect custody) never advances further (R5: no
    auto-action past the architect -- and no repeated nudging of a seat that cannot make the
    human react faster).
    """
    if entry.state != "pending" or entry.rung >= MAX_RUNG:
        return False
    anchor = _parse_anchor(_dwell_anchor(entry))
    if anchor is None:
        return False
    threshold = sla_seconds if entry.rung == 0 else rung_seconds
    if (now - anchor).total_seconds() < threshold:
        return False
    if entry.rung == 0:
        return True
    floor_anchor = _minimum_dwell_anchor(entry, anchor)
    return (now - floor_anchor).total_seconds() >= MIN_RUNG_DWELL_SECONDS


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
            # further seat to skip to below the architect. Jump straight to rung 3 rather than
            # stalling rung 2 forever (doctrine: no signal dies with a silent seat).
            return LadderStep(
                rung=MAX_RUNG,
                action="architect-attention",
                owner=derive_architect_owner(catalog),
            )
    else:
        owner = derive_architect_owner(catalog)
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

    An ARCHITECT seat is never suspect (ruled 2026-07-09): it is the human's own session, so its
    silence means the human is away -- retiring/respawning it cannot produce the human and would
    kill the very seat that holds custody of their catch-up report.
    """
    if agent_id is None:
        return False
    live = catalog.get(agent_id)
    if live is not None and live.spawn_role == "architect":
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
