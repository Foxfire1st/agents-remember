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
from datetime import datetime

from agents_remember.controlplane.operator_inbox_records import AgentRole, InboxMessageKind
from agents_remember.serving.terminal_catalog import TerminalCatalog, TerminalCatalogEntry

# One hop up the spawn edge: the role a signal's SENDER was spawned as -> the role its routed
# owner carries. A sender spawned as anything else (orchestrator, strategist, reviewer, ...) has
# no owner-role mapping here -- the caller's explicit recipient_role stands, unrouted.
_OWNER_ROLE_BY_SENDER_SPAWN_ROLE: dict[str, AgentRole] = {
    "worker": "manager",
    "reviewer": "manager",
    "curator": "manager",
    "manager": "orchestrator",
}


@dataclass(frozen=True)
class RoutedOwner:
    """The routed owner address for one signal row; every field ``None`` means "no route derived,
    keep the caller's explicit recipient" -- this module never fabricates an address."""

    role: AgentRole | None = None
    agent_id: str | None = None
    lifecycle_id: str | None = None


def _manager_owner(entry: TerminalCatalogEntry) -> RoutedOwner:
    return RoutedOwner(
        role="manager",
        agent_id=entry.id,
        lifecycle_id=entry.lifecycle_id,
    )


def _master_key(leaf_key: str | None) -> str | None:
    if leaf_key is None:
        return None
    parts = leaf_key.split("/", 2)
    if len(parts) != 3 or not all(parts):
        return None
    return "/".join(parts[:2])


def signal_leaf_key(
    catalog: TerminalCatalog,
    *,
    sender_agent_id: str | None,
    leaf_key: str | None = None,
) -> str | None:
    """Best current leaf/master anchor for a signal sender.

    A bound worker/reviewer supplies its leaf directly. An unbound successor seat can still inherit
    the master anchor from its recorded manager, including a retired prior manager whose catalog
    row remains available for provenance.
    """
    if leaf_key is not None:
        return leaf_key
    sender = catalog.get(sender_agent_id) if sender_agent_id is not None else None
    if sender is None:
        return None
    if sender.binding_leaf_key is not None:
        return sender.binding_leaf_key
    prior_manager = (
        catalog.get(sender.spawned_by_session) if sender.spawned_by_session is not None else None
    )
    return prior_manager.binding_leaf_key if prior_manager is not None else None


def _direct_live_manager(
    catalog: TerminalCatalog, sender_agent_id: str | None
) -> TerminalCatalogEntry | None:
    sender = catalog.get(sender_agent_id) if sender_agent_id is not None else None
    if sender is None or sender.spawned_by_session is None:
        return None
    manager = catalog.get(sender.spawned_by_session)
    if (
        manager is None
        or manager.kind != "harness"
        or manager.status != "running"
        or manager.binding_role != "manager"
    ):
        return None
    return manager


def _live_managers(catalog: TerminalCatalog) -> list[TerminalCatalogEntry]:
    return [
        entry
        for entry in catalog.list()
        if entry.kind == "harness" and entry.status == "running" and entry.binding_role == "manager"
    ]


def _scoped_managers(
    catalog: TerminalCatalog,
    managers: list[TerminalCatalogEntry],
    *,
    route_leaf: str,
) -> list[TerminalCatalogEntry]:
    route_master = _master_key(route_leaf)
    linked_manager_ids = {
        entry.spawned_by_session
        for entry in catalog.list()
        if entry.binding_leaf_key == route_leaf and entry.spawned_by_session is not None
    }
    return [
        manager
        for manager in managers
        if manager.id in linked_manager_ids
        or manager.binding_leaf_key == route_leaf
        or (route_master is not None and _master_key(manager.binding_leaf_key) == route_master)
    ]


def derive_leaf_manager_owner(
    catalog: TerminalCatalog,
    *,
    sender_agent_id: str | None,
    leaf_key: str | None = None,
) -> RoutedOwner:
    """Resolve a leaf signal to its current responsible manager at address time.

    A live direct manager remains authoritative. When that binding is stale, prefer a live manager
    attached to the same qualified master or currently parenting a seat bound to the same leaf.
    Only an unambiguous single live manager is used without a leaf/master anchor. If no concrete
    current manager can be proven, return the role-only manager mailbox; never fall directly to an
    orchestrator or architect because the ladder owns later escalation.
    """
    direct_manager = _direct_live_manager(catalog, sender_agent_id)
    if direct_manager is not None:
        return _manager_owner(direct_manager)

    live_managers = _live_managers(catalog)
    route_leaf = signal_leaf_key(catalog, sender_agent_id=sender_agent_id, leaf_key=leaf_key)
    if route_leaf is not None:
        scoped = _scoped_managers(catalog, live_managers, route_leaf=route_leaf)
        if scoped:
            return _manager_owner(
                max(scoped, key=lambda entry: (entry.last_attached_at, entry.created_at, entry.id))
            )
    return RoutedOwner(role="manager")


def _parsed_at(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _entry_progressed_after(entry: TerminalCatalogEntry, since: datetime) -> bool:
    if entry.status == "running" and entry.turn_state == "working":
        return True
    timestamps = (
        entry.created_at,
        entry.last_attached_at,
        entry.turn_state_changed_at,
        entry.landed_at,
    )
    return any(at is not None and at > since for at in map(_parsed_at, timestamps))


def _entry_carries_leaf_chain(
    entry: TerminalCatalogEntry,
    *,
    leaf_key: str,
    manager_agent_id: str | None,
) -> bool:
    if entry.binding_leaf_key == leaf_key or (
        manager_agent_id is not None and entry.id == manager_agent_id
    ):
        return True
    # A replacement may be unbound while another role occupies the leaf. Same-manager provenance
    # alone is insufficient because one manager can drive parallel leaves; the explicit replacement
    # leaf is the production discriminator (catalog cwd is fleet-wide and cannot distinguish work).
    return bool(
        manager_agent_id is not None
        and entry.spawned_by_session == manager_agent_id
        and entry.binding_role in ("worker", "reviewer", "curator")
        and entry.leaf_key is None
        and entry.replacement_for_leaf == leaf_key
    )


def _is_chain_progress(
    entry: TerminalCatalogEntry,
    *,
    leaf_key: str,
    manager_agent_id: str | None,
    subject_agent_id: str | None,
    since: datetime,
) -> bool:
    if entry.id == subject_agent_id or entry.status not in ("running", "landed"):
        return False
    return _entry_carries_leaf_chain(
        entry,
        leaf_key=leaf_key,
        manager_agent_id=manager_agent_id,
    ) and _entry_progressed_after(entry, since)


def leaf_chain_has_progress(
    catalog: TerminalCatalog,
    *,
    leaf_key: str,
    subject_agent_id: str | None,
    since: str,
) -> bool:
    """Whether another observable seat carrying ``leaf_key`` progressed after ``since``.

    The chain includes exact-leaf seats, the current manager, and an unbound worker/reviewer/curator
    whose catalog row explicitly names this leaf as its replacement target.
    """
    since_at = _parsed_at(since)
    if since_at is None:
        return False
    manager = derive_leaf_manager_owner(
        catalog, sender_agent_id=subject_agent_id, leaf_key=leaf_key
    )
    return any(
        _is_chain_progress(
            entry,
            leaf_key=leaf_key,
            manager_agent_id=manager.agent_id,
            subject_agent_id=subject_agent_id,
            since=since_at,
        )
        for entry in catalog.list()
    )


def derive_signal_owner(
    catalog: TerminalCatalog,
    *,
    sender_agent_id: str | None,
    message_kind: InboxMessageKind,
    leaf_key: str | None = None,
) -> RoutedOwner:
    """The owner address for a signal from ``sender_agent_id``, or an empty :class:`RoutedOwner`."""
    if message_kind == "decision-item":
        return RoutedOwner(role="architect")
    if sender_agent_id is None:
        return RoutedOwner()
    entry = catalog.get(sender_agent_id)
    if entry is None:
        return RoutedOwner()
    owner_role = _OWNER_ROLE_BY_SENDER_SPAWN_ROLE.get(entry.binding_role)
    if owner_role is None:
        return RoutedOwner()
    if owner_role == "manager":
        return derive_leaf_manager_owner(
            catalog, sender_agent_id=sender_agent_id, leaf_key=leaf_key
        )
    return RoutedOwner(
        role=owner_role,
        agent_id=entry.spawned_by_session,
        lifecycle_id=entry.spawned_by_lifecycle,
    )


def _derive_spawn_owner(
    catalog: TerminalCatalog,
    *,
    sender_agent_id: str | None,
    message_kind: InboxMessageKind,
) -> RoutedOwner:
    """One historical provenance hop for the escalation ladder only.

    Initial signal addressing uses :func:`derive_signal_owner` and refuses stale managers. Once a
    manager has failed, the ladder still needs the recorded edge in order to climb past that dead
    seat to its owner; replacing this with current-manager routing would erase the path it must walk.
    """
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


def derive_architect_owner(catalog: TerminalCatalog) -> RoutedOwner:
    """The ladder's terminal addressee (ruled 2026-07-09): the LIVE architect seat when one is
    attached, else the role-only architect mailbox. The developer is an authority label, never an
    address -- a human-shaped mailbox cannot mechanically ack, which is how the 2026-07-09 storm's
    rows became immortal. The architect seat acks custody and briefs the human; with no architect
    seat attached the role-addressed row waits, level-triggered, for the next architect session
    (delivery on appearance, or poll at session start)."""
    for entry in catalog.list():
        if (
            entry.kind == "harness"
            and entry.status == "running"
            and entry.binding_role == "architect"
        ):
            return RoutedOwner(role="architect", agent_id=entry.id, lifecycle_id=entry.lifecycle_id)
    return RoutedOwner(role="architect")


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
        owner = _derive_spawn_owner(
            catalog, sender_agent_id=current_agent_id, message_kind=message_kind
        )
        if owner.agent_id is None:
            return owner
        current_agent_id = owner.agent_id
        hops_done += 1
        if hops_done >= 2 and not is_seat_dead(catalog, owner.agent_id):
            return owner
        if len(seen) > 64:  # pathological-chain guard; never hangs on a corrupt catalog
            return owner
