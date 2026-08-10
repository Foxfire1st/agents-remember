"""R4 hierarchical routing: derive a signal's owner address from catalog spawn provenance.

A worker's signal routes to its manager; a manager's signal routes to its orchestrator -- one
hop up the spawn edge, never further (a developer ruling: no layer is addressed its
grandchildren's noise). The address is read straight off the SENDER's own catalog row
(``spawned_by_session`` / ``spawned_by_lifecycle``, declared on ``controlplane/seats.py``'s
``SeatRow``), which is exactly who spawned it; no second catalog lookup is needed to resolve "the
manager's session id" because that IS the sender's ``spawned_by_session``.

``message_kind == "decision-item"`` is routed to the reserved ``architect`` role regardless of
spawn provenance (the queue itself is AQR Q3's job, not this leaf's -- only the routing target is
reserved here).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from agents_remember.controlplane.operator_inbox_records import (
    AgentRole,
    InboxMessageKind,
    OperatorInboxEntry,
)
from agents_remember.controlplane.seats import SeatDirectory, SeatRow

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


def _manager_owner(entry: SeatRow) -> RoutedOwner:
    return RoutedOwner(
        role="manager",
        agent_id=entry.id,
        lifecycle_id=entry.lifecycle_id,
    )


def master_key(leaf_key: str | None) -> str | None:
    """The qualified master prefix (``repo/master``) of a qualified leaf key, or ``None``."""
    if leaf_key is None:
        return None
    parts = leaf_key.split("/", 2)
    if len(parts) != 3 or not all(parts):
        return None
    return "/".join(parts[:2])


def signal_leaf_key(
    catalog: SeatDirectory,
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


def _direct_live_manager(catalog: SeatDirectory, sender_agent_id: str | None) -> SeatRow | None:
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


def _live_managers(catalog: SeatDirectory) -> list[SeatRow]:
    return [
        entry
        for entry in catalog.list()
        if entry.kind == "harness" and entry.status == "running" and entry.binding_role == "manager"
    ]


def _scoped_managers(
    catalog: SeatDirectory,
    managers: list[SeatRow],
    *,
    route_leaf: str,
) -> list[SeatRow]:
    route_master = master_key(route_leaf)
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
        or (route_master is not None and master_key(manager.binding_leaf_key) == route_master)
    ]


def derive_leaf_manager_owner(
    catalog: SeatDirectory,
    *,
    sender_agent_id: str | None,
    leaf_key: str | None = None,
) -> RoutedOwner:
    """Resolve a leaf signal to its current responsible manager at address time.

    A live direct manager remains authoritative. When that binding is stale, prefer a live manager
    attached to the same qualified master or currently parenting a seat bound to the same leaf.
    Only an unambiguous single live manager is used without a leaf/master anchor. If no concrete
    current manager can be proven, return the role-only manager mailbox; never fall directly to an
    orchestrator or architect -- dead-owner rows surface through the rebind/mailbox machinery.
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


def _entry_progressed_after(entry: SeatRow, since: datetime) -> bool:
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
    entry: SeatRow,
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
    entry: SeatRow,
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
    catalog: SeatDirectory,
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
    catalog: SeatDirectory,
    *,
    sender_agent_id: str | None,
    message_kind: InboxMessageKind,
    leaf_key: str | None = None,
) -> RoutedOwner:
    """The owner address for a signal from ``sender_agent_id``, or an empty :class:`RoutedOwner`."""
    if message_kind == "decision-item":
        sender_leaf = signal_leaf_key(catalog, sender_agent_id=sender_agent_id)
        return derive_architect_owner(catalog, leaf_key=leaf_key or sender_leaf)
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


def is_seat_dead(catalog: SeatDirectory, agent_id: str | None) -> bool:
    """Whether ``agent_id`` cannot receive a delivery: unknown to the catalog, or not ``running``
    (260707-HFX2-L4). A row with no catalog trace at all (never spawned through the harness path,
    a role-only mailbox) counts as dead here -- there is nothing live to address, so the rebind
    machinery treats "no evidence of life" the same as "confirmed dead"."""
    if agent_id is None:
        return True
    entry = catalog.get(agent_id)
    return entry is None or entry.status != "running"


def derive_architect_owner(
    catalog: SeatDirectory,
    *,
    leaf_key: str | None = None,
) -> RoutedOwner:
    """Repository+sprint-scoped architect custody (R13), never global first-match.

    The row's ``leafKey`` resolves to its master scope (``repo/master``); only a running
    architect seat bound to that scope is the custody owner. An unscoped request, an
    ambiguous set of scoped seats, or no scoped seat resolves to the role-only architect
    mailbox -- fail-closed, so a second repository's architect can never capture another
    repo's rows. The developer is an authority label, never an address.
    """
    master = master_key(leaf_key)
    scoped = [
        entry
        for entry in catalog.list()
        if entry.kind == "harness"
        and entry.status == "running"
        and entry.binding_role == "architect"
        and _seat_in_scope(entry, leaf_key=leaf_key, master=master)
    ]
    exact = [
        entry
        for entry in scoped
        if leaf_key in (entry.binding_leaf_key, entry.replacement_for_leaf)
    ]
    candidates = exact if len(exact) == 1 else scoped
    if len(candidates) == 1:
        entry = candidates[0]
        return RoutedOwner(role="architect", agent_id=entry.id, lifecycle_id=entry.lifecycle_id)
    return RoutedOwner(role="architect")


def _seat_in_scope(
    entry: SeatRow,
    *,
    leaf_key: str | None,
    master: str | None,
) -> bool:
    """Whether a seat's binding or replacement target falls inside ``master``'s scope."""
    if entry.sprint_key is not None:
        return master is not None and entry.sprint_key == master
    for anchor in (entry.binding_leaf_key, entry.replacement_for_leaf):
        if anchor is None:
            continue
        if anchor == leaf_key:
            return True
        if master is not None and master_key(anchor) == master:
            return True
    return False


def _scoped_orchestrator(
    catalog: SeatDirectory,
    *,
    master: str | None,
) -> SeatRow | None:
    """The live orchestrator bound to ``master``, or ``None`` (never a global fallback)."""
    candidates = [
        entry
        for entry in catalog.list()
        if entry.kind == "harness"
        and entry.status == "running"
        and entry.binding_role == "orchestrator"
        and _seat_in_scope(entry, leaf_key=None, master=master)
    ]
    if len(candidates) != 1:
        return None
    return candidates[0]


def derive_row_owner(
    catalog: SeatDirectory,
    entry: OperatorInboxEntry,
) -> RoutedOwner:
    """The CURRENT qualified owner of one pending inbox row (N14 sweep-time rebinding).

    Derived from the row's durable subject identity (leaf key + seat role + subject agent),
    never from the stamped address: a worker/reviewer/curator row re-resolves its manager, a
    manager row re-resolves its orchestrator, and a row whose chain is entirely dead falls
    back through the stamped ``ownerRole`` to the scoped architect mailbox. ``dispatch-brief``
    rows never rebind (exact-pinned; a replacement seat receives a fresh brief from its owner).
    """
    if entry.messageKind == "dispatch-brief":
        return RoutedOwner()
    role = entry.seatRole or entry.senderRole
    subject_agent_id = entry.subjectAgentId or entry.senderAgentId or entry.ownerAgentId
    owner = _owner_for_role(catalog, role, subject_agent_id, entry)
    if owner.agent_id is not None or owner.role is not None:
        return owner
    return _owner_for_stamped_role(catalog, entry, subject_agent_id)


def _owner_for_role(
    catalog: SeatDirectory,
    role: str | None,
    subject_agent_id: str | None,
    entry: OperatorInboxEntry,
) -> RoutedOwner:
    """The live owner for a row whose durable subject names a spawned seat role."""
    if role in ("worker", "reviewer", "curator"):
        return derive_leaf_manager_owner(
            catalog, sender_agent_id=subject_agent_id, leaf_key=entry.leafKey
        )
    if role == "manager":
        return _orchestrator_owner(
            catalog,
            entry,
            subject_agent_id=subject_agent_id,
        )
    return RoutedOwner()


def _orchestrator_owner(
    catalog: SeatDirectory,
    entry: OperatorInboxEntry,
    *,
    subject_agent_id: str | None,
) -> RoutedOwner:
    """The manager's current orchestrator: live spawn provenance, else master-scoped
    replacement, else the role-only orchestrator mailbox."""
    owner = derive_signal_owner(
        catalog,
        sender_agent_id=subject_agent_id,
        message_kind=entry.messageKind or "state-signal",
        leaf_key=entry.leafKey,
    )
    if (
        owner.role == "orchestrator"
        and owner.agent_id is not None
        and is_seat_dead(catalog, owner.agent_id)
    ):
        return _live_scoped_orchestrator(catalog, entry)
    return owner


def _live_scoped_orchestrator(
    catalog: SeatDirectory,
    entry: OperatorInboxEntry,
) -> RoutedOwner:
    """A live orchestrator bound to the row's master, else the role-only orchestrator mailbox."""
    replacement = _scoped_orchestrator(catalog, master=master_key(entry.leafKey))
    if replacement is not None:
        return RoutedOwner(
            role="orchestrator",
            agent_id=replacement.id,
            lifecycle_id=replacement.lifecycle_id,
        )
    return RoutedOwner(role="orchestrator")


def _owner_for_stamped_role(
    catalog: SeatDirectory,
    entry: OperatorInboxEntry,
    subject_agent_id: str | None,
) -> RoutedOwner:
    """Fallback through the stamped routed owner when the row carries no seat-role subject."""
    if entry.ownerRole == "manager":
        return derive_leaf_manager_owner(
            catalog, sender_agent_id=subject_agent_id, leaf_key=entry.leafKey
        )
    if entry.ownerRole == "orchestrator":
        return _live_scoped_orchestrator(catalog, entry)
    if entry.ownerRole == "architect":
        return derive_architect_owner(catalog, leaf_key=entry.leafKey)
    return RoutedOwner()
