"""Shared terminal-session leaf assignment policy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from agents_remember.serving.terminal_catalog import (
    TerminalCatalog,
    TerminalSessionRole,
)

LeafAssignmentStatus = Literal["attached", "leaf-taken", "unknown-session"]


@dataclass(frozen=True)
class LeafAssignmentResult:
    """Result of moving one terminal catalog row to a durable leaf key."""

    status: LeafAssignmentStatus
    session_id: str
    leaf_key: str
    previous_leaf_key: str | None = None
    owner_session_id: str | None = None
    role: TerminalSessionRole | None = None


def leaf_conflict_owner(
    catalog: TerminalCatalog,
    *,
    leaf_key: str | None,
    session_id: str,
    role: TerminalSessionRole,
) -> str | None:
    """Return the different running owner of ``leaf_key`` for ``role``, if one exists."""

    if not leaf_key:
        return None
    owner = catalog.active_for_leaf(leaf_key, role=role)
    if owner is None or owner.id == session_id:
        return None
    return owner.id


def assign_terminal_session_to_leaf(
    catalog: TerminalCatalog,
    *,
    session_id: str,
    leaf_key: str,
) -> LeafAssignmentResult:
    """Move an existing catalog session to ``leaf_key`` using catalog uniqueness rules."""

    entry = catalog.get(session_id)
    if entry is None or entry.status != "running":
        return LeafAssignmentResult(
            status="unknown-session",
            session_id=session_id,
            leaf_key=leaf_key,
        )
    owner = leaf_conflict_owner(
        catalog,
        leaf_key=leaf_key,
        session_id=session_id,
        role=entry.role,
    )
    if owner is not None:
        return LeafAssignmentResult(
            status="leaf-taken",
            session_id=session_id,
            leaf_key=leaf_key,
            previous_leaf_key=entry.leaf_key,
            owner_session_id=owner,
            role=entry.role,
        )
    previous_leaf_key = entry.leaf_key
    catalog.upsert(entry.with_leaf_key(leaf_key))
    return LeafAssignmentResult(
        status="attached",
        session_id=session_id,
        leaf_key=leaf_key,
        previous_leaf_key=previous_leaf_key,
        role=entry.role,
    )
