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
