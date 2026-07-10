"""Leaf-seat binding roles shared by catalog, spawn, and attach surfaces."""

from __future__ import annotations

from collections.abc import Iterable

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
LEGACY_CHAT_SEAT_ROLE = "chat"
TERMINAL_SEAT_ROLE = "terminal"


def _clean(role: str | None) -> str | None:
    if role is None:
        return None
    cleaned = role.strip()
    return cleaned or None


def migrated_seat_role(
    *, persisted: str | None, spawn_role: str | None, kind: str
) -> str:
    """Resolve a catalog row's binding role, including the one-time legacy fallback."""

    if kind == "terminal":
        return TERMINAL_SEAT_ROLE
    return _clean(persisted) or _clean(spawn_role) or LEGACY_CHAT_SEAT_ROLE


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
