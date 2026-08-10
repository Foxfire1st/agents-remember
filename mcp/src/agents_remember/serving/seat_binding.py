"""Leaf-seat binding roles shared by catalog, spawn, and attach surfaces.

The chat/terminal role constants and the legacy role resolver live in
``models/terminal_catalog.py``; this module owns the binding decisions on top
of them.
"""

from __future__ import annotations

from collections.abc import Iterable

from agents_remember.models.terminal_catalog import (
    LEGACY_CHAT_SEAT_ROLE,
    TERMINAL_SEAT_ROLE,
    _clean,
)

PIPELINE_SEAT_ROLES = (
    "architect",
    "orchestrator",
    "strategist",
    "designer",
    "manager",
    "worker",
    "reviewer",
    "curator",
    "system-specialist",
    "agent",
)


def attach_seat_role(
    *, requested: str | None, spawn_role: str | None, current: str | None, kind: str
) -> str | None:
    """Resolve an attach role without silently assigning an untyped harness to ``chat``."""

    if kind == "terminal":
        return TERMINAL_SEAT_ROLE
    explicit = _clean(requested)
    if explicit is not None:
        return explicit
    spawned = _clean(spawn_role)
    if spawned is not None:
        return spawned
    existing = _clean(current)
    if existing not in (None, LEGACY_CHAT_SEAT_ROLE):
        return existing
    return None


def role_suffixed_leaf_base(
    leaf_ref: str,
    *,
    roles: Iterable[str] = PIPELINE_SEAT_ROLES,
) -> tuple[str, str] | None:
    """Return ``(base, role)`` for a failed legacy ``leaf-role``/``leaf/role`` ref."""

    lowered = leaf_ref.lower()
    for role in roles:
        for separator in ("-", "/", ":"):
            suffix = f"{separator}{role}"
            if lowered.endswith(suffix) and len(leaf_ref) > len(suffix):
                return leaf_ref[: -len(suffix)], role
    return None
