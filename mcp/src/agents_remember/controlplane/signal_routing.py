"""R4 hierarchical routing: derive a signal's owner address from catalog spawn provenance.

A worker's signal routes to its manager; a manager's signal routes to its orchestrator -- one
hop up the spawn edge, never further (a developer ruling: no layer is addressed its
grandchildren's noise). The address is read straight off the SENDER's own catalog row
(``spawned_by_session`` / ``spawned_by_lifecycle`` -- terminal_catalog.py:48-59), which is exactly
who spawned it; no second catalog lookup is needed to resolve "the manager's session id" because
that IS the sender's ``spawned_by_session``.

``message_kind == "decision-item"`` is routed to the reserved ``architect`` role regardless of
spawn provenance (the queue itself is AQR Q3's job, not this leaf's -- only the routing target is
reserved here).
"""

from __future__ import annotations

from dataclasses import dataclass

from agents_remember.controlplane.operator_inbox_records import AgentRole, InboxMessageKind
from agents_remember.serving.terminal_catalog import TerminalCatalog

# One hop up the spawn edge: the role a signal's SENDER was spawned as -> the role its routed
# owner carries. A sender spawned as anything else (orchestrator, strategist, reviewer, ...) has
# no owner-role mapping here -- the caller's explicit recipient_role stands, unrouted.
_OWNER_ROLE_BY_SENDER_SPAWN_ROLE: dict[str, AgentRole] = {
    "worker": "manager",
    "manager": "orchestrator",
}


@dataclass(frozen=True)
class RoutedOwner:
    """The routed owner address for one signal row; every field ``None`` means "no route derived,
    keep the caller's explicit recipient" -- this module never fabricates an address."""

    role: AgentRole | None = None
    agent_id: str | None = None
    lifecycle_id: str | None = None


def derive_signal_owner(
    catalog: TerminalCatalog,
    *,
    sender_agent_id: str | None,
    message_kind: InboxMessageKind,
) -> RoutedOwner:
    """The owner address for a signal from ``sender_agent_id``, or an empty :class:`RoutedOwner`."""
    if message_kind == "decision-item":
        return RoutedOwner(role="architect")
    if sender_agent_id is None:
        return RoutedOwner()
    entry = catalog.get(sender_agent_id)
    if entry is None or entry.spawn_role is None:
        return RoutedOwner()
    owner_role = _OWNER_ROLE_BY_SENDER_SPAWN_ROLE.get(entry.spawn_role)
    if owner_role is None:
        return RoutedOwner()
    return RoutedOwner(
        role=owner_role,
        agent_id=entry.spawned_by_session,
        lifecycle_id=entry.spawned_by_lifecycle,
    )


def is_seat_dead(catalog: TerminalCatalog, agent_id: str | None) -> bool:
    """Whether ``agent_id`` cannot receive a delivery: unknown to the catalog, or not ``running``
    (260707-HFX2-L4). A row with no catalog trace at all (never spawned through the harness path,
    a role-only mailbox) counts as dead here -- there is nothing live to skip TO it, so the ladder
    walk (below) treats "no evidence of life" the same as "confirmed dead"."""
    if agent_id is None:
        return True
    entry = catalog.get(agent_id)
    return entry is None or entry.status != "running"


def derive_skip_level_owner(
    catalog: TerminalCatalog,
    *,
    sender_agent_id: str | None,
    message_kind: InboxMessageKind,
) -> RoutedOwner:
    """R2/R4 (260707-HFX2-L4): the SENDER's owner's owner -- two spawn-provenance hops, walking
    PAST any dead node encountered along the way rather than landing a signal on it.

    This is deliberately a second, separate function rather than a parameter on
    :func:`derive_signal_owner` (whose one-hop invariant a test locks: "no layer is addressed its
    grandchildren's noise" -- that rule is about who ADDRESSES whom, not about how many hops THIS
    walker takes to find a live address for the SAME sender). Two hops means: hop 1 is
    ``derive_signal_owner(sender)`` (the ordinary owner -- the rung-1 addressee); hop 2 is
    ``derive_signal_owner(hop-1's owner)`` (the owner's owner -- the rung-2/skip-level and R4
    grandparent-signal target). If landing on a hop is dead, the walk continues FROM that hop
    exactly as if it had answered -- so a dead direct owner is transparently skipped (its own
    owner becomes the effective rung-1 stand-in) and a dead owner's-owner is walked past too (the
    walk never stops on a confirmed-dead address). A cycle or an exhausted chain (no further owner
    role mapping -- e.g. the top is an orchestrator, which has none) returns whatever the walk last
    resolved, ``RoutedOwner()`` if nothing did.
    """
    seen: set[str] = set()
    current_agent_id = sender_agent_id
    hops_done = 0
    owner = RoutedOwner()
    while True:
        if current_agent_id is None or current_agent_id in seen:
            return owner if hops_done >= 2 else RoutedOwner()
        seen.add(current_agent_id)
        owner = derive_signal_owner(catalog, sender_agent_id=current_agent_id, message_kind=message_kind)
        if owner.agent_id is None:
            return owner
        current_agent_id = owner.agent_id
        hops_done += 1
        if hops_done >= 2 and not is_seat_dead(catalog, owner.agent_id):
            return owner
        if len(seen) > 64:  # pathological-chain guard; never hangs on a corrupt catalog
            return owner
