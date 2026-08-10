"""Shared terminal-session leaf assignment policy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from agents_remember.serving.seat_binding import attach_seat_role
from agents_remember.serving.sprint_role_binding import sprint_binding_for_attachment
from agents_remember.serving.terminal_catalog import TerminalCatalog

LeafAssignmentStatus = Literal[
    "attached",
    "leaf-taken",
    "unknown-session",
    "role-required",
    "sprint-binding-required",
    "sprint-binding-conflict",
]


class LeafAssignmentHost(Protocol):
    def has_session(self, tmux_name: str) -> bool: ...


@dataclass(frozen=True)
class LeafAssignmentResult:
    """Result of moving one terminal catalog row to a durable leaf key."""

    status: LeafAssignmentStatus
    session_id: str
    leaf_key: str
    previous_leaf_key: str | None = None
    owner_session_id: str | None = None
    role: str | None = None
    seat_role: str | None = None
    previous_seat_role: str | None = None


def leaf_conflict_owner(
    catalog: TerminalCatalog,
    *,
    leaf_key: str | None,
    session_id: str,
    seat_role: str,
    host: LeafAssignmentHost,
) -> str | None:
    """Return the different live owner of ``(leaf_key, seat_role)``, if one exists."""

    if not leaf_key:
        return None
    owner = catalog.active_for_leaf(leaf_key, seat_role=seat_role)
    if owner is None or owner.id == session_id:
        return None
    if not host.has_session(owner.tmux_name):
        catalog.mark_exited(owner.id)
        return None
    return owner.id


def assign_terminal_session_to_leaf(
    catalog: TerminalCatalog,
    host: LeafAssignmentHost,
    *,
    session_id: str,
    leaf_key: str,
    role: str | None = None,
) -> LeafAssignmentResult:
    """Move a running catalog session to one live-arbitrated ``(leaf, role)`` binding."""

    entry = catalog.get(session_id)
    if entry is None or entry.status != "running":
        return LeafAssignmentResult(
            status="unknown-session",
            session_id=session_id,
            leaf_key=leaf_key,
        )
    seat_role = attach_seat_role(
        requested=role,
        spawn_role=entry.spawn_role,
        current=entry.seat_role,
        kind=entry.kind,
    )
    if seat_role is None:
        return LeafAssignmentResult(
            status="role-required",
            session_id=session_id,
            leaf_key=leaf_key,
            previous_leaf_key=entry.leaf_key,
            previous_seat_role=entry.binding_role,
            role=entry.role,
        )
    binding, binding_refusal = sprint_binding_for_attachment(
        seat_role, leaf_key=leaf_key, entry=entry
    )
    if binding_refusal is not None:
        return LeafAssignmentResult(
            status=binding_refusal,
            session_id=session_id,
            leaf_key=leaf_key,
            previous_leaf_key=entry.leaf_key,
            role=entry.role,
            seat_role=seat_role,
            previous_seat_role=entry.binding_role,
        )
    owner = leaf_conflict_owner(
        catalog,
        leaf_key=leaf_key,
        session_id=session_id,
        seat_role=seat_role,
        host=host,
    )
    if owner is not None:
        return LeafAssignmentResult(
            status="leaf-taken",
            session_id=session_id,
            leaf_key=leaf_key,
            previous_leaf_key=entry.leaf_key,
            owner_session_id=owner,
            role=entry.role,
            seat_role=seat_role,
            previous_seat_role=entry.binding_role,
        )
    previous_leaf_key = entry.leaf_key
    previous_seat_role = entry.binding_role
    catalog.upsert(
        entry.with_leaf_binding(
            leaf_key,
            seat_role,
            spawn_repo=binding.repo if binding is not None else None,
            spawn_sprint=binding.sprint if binding is not None else None,
        )
    )
    return LeafAssignmentResult(
        status="attached",
        session_id=session_id,
        leaf_key=leaf_key,
        previous_leaf_key=previous_leaf_key,
        role=entry.role,
        seat_role=seat_role,
        previous_seat_role=previous_seat_role,
    )
