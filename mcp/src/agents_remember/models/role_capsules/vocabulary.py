"""The frozen role and operation vocabularies one role capsule may be compiled for.

These two tuples are the closed vocabulary the canonical composition manifest is
required to agree with. They are declared here, not read from the manifest, so a
manifest edit that renamed or added a role/operation fails compilation instead of
silently minting a new one: a caller-controlled string must never acquire another
role, and an unknown operation is an explicit error rather than a fallback.

``launcher`` is deliberately absent from :data:`CAPSULE_ROLES`. The ambient
launcher is a routing condition, not a role; it is reached through
:data:`CAPSULE_LAUNCHER_MODE` and its own core block.

``bootstrap`` is a real role and is present: it is the new user's first-hour seat, and
it is the only role that carries the ``bootstrap`` operation. It is deliberately *last*
in the registry order so that the nine roles that pre-date it keep their existing
positions — a compilation, a settings key, or a dashboard projection addressed to an
earlier role therefore cannot shift because this one was added.

**``bootstrap`` is a FREE agent, not a structural seat** (developer ruling 2026-09-16:
*"It is not a task related agent. Can't be. It needs to be free agent. All what it needs
is that can 'call'."*). It has no task altitude and is deliberately absent from
``SPRINT_ROLES``/``MASTER_ROLES``/``LEAF_ROLES`` and from
``serving/structural_seats.py``: a session opens with ``AR_SPAWN_ROLE=bootstrap`` and no
task document, and its instructions are its compiled capsule rather than a dispatch
brief. Do **not** "fix" the absent altitude by adding one — a task altitude is the wrong
shape for this seat.
"""

from __future__ import annotations

from typing import Literal, get_args

#: The ten lifecycle roles. Every one of them has its own canonical
#: ``roles/<role>.md`` source; nine are dispatched task seats and ``bootstrap`` is the
#: free agent the module docstring describes.
type CapsuleRole = Literal[
    "architect",
    "orchestrator",
    "designer",
    "strategist",
    "manager",
    "worker",
    "curator",
    "reviewer",
    "system-specialist",
    "bootstrap",
]

#: The nine operations, matching the canonical ``operations/<name>.md`` blocks.
type CapsuleOperation = Literal[
    "orientation",
    "planning",
    "implementation",
    "review",
    "curation",
    "coordination",
    "authorized-closeout",
    "recovery",
    "bootstrap",
]

#: ``role`` is a dispatched role seat; ``launcher`` is the ambient no-role routing
#: condition. The distinction is part of the frozen contract because the two compose
#: from different sources and must not be conflated by a caller-supplied string.
type CapsuleSeatKind = Literal["role", "launcher"]

#: The composition roots a capsule composes from, in composition order. ``skill`` is
#: admitted alongside the others but is **not composed into the instruction stream**: a
#: skill is separately delivered content the capsule *points at*, so its root file is
#: admitted only to give the reference a content-addressed revision.
type CapsuleCompositionRoot = Literal["core", "role", "operation", "specialization", "skill"]

CAPSULE_LAUNCHER_MODE: CapsuleSeatKind = "launcher"
CAPSULE_ROLE_MODE: CapsuleSeatKind = "role"

CAPSULE_SEAT_KINDS: tuple[CapsuleSeatKind, ...] = get_args(CapsuleSeatKind)

#: The ten roles, in the registry's canonical order. This tuple and the
#: :data:`CapsuleRole` literal above are one registry with two readers — the type
#: checker reads the literal, runtime selection reads the tuple — so a test holds the
#: literal's own arguments to this exact tuple and the pair cannot drift apart.
CAPSULE_ROLES: tuple[CapsuleRole, ...] = (
    "architect",
    "orchestrator",
    "designer",
    "strategist",
    "manager",
    "worker",
    "curator",
    "reviewer",
    "system-specialist",
    "bootstrap",
)

#: The nine operations, in the order the architecture names them, with the one
#: deliberate extension appended rather than inserted: ``bootstrap``, the first-hour
#: operation the ``bootstrap`` role carries.
CAPSULE_OPERATIONS: tuple[CapsuleOperation, ...] = (
    "orientation",
    "planning",
    "implementation",
    "review",
    "curation",
    "coordination",
    "authorized-closeout",
    "recovery",
    "bootstrap",
)

#: The composition order: shared core first, then the seat's own block, then the
#: operation it is running, then explicitly admitted repository specialization. This
#: order is contract, not presentation.
CAPSULE_COMPOSITION_ORDER: tuple[CapsuleCompositionRoot, ...] = (
    "core",
    "role",
    "operation",
    "specialization",
)


def is_capsule_role(value: str) -> bool:
    """Whether ``value`` is one of the ten frozen roles (exact, not normalized)."""

    return value in CAPSULE_ROLES


def is_capsule_operation(value: str) -> bool:
    """Whether ``value`` is one of the nine frozen operations (exact, not normalized)."""

    return value in CAPSULE_OPERATIONS


def capsule_role_or_none(value: str) -> CapsuleRole | None:
    """Return ``value`` narrowed to :data:`CapsuleRole`, or ``None`` when unknown.

    This is the only sanctioned way a plain string becomes a role, and it answers by
    exact membership in the frozen registry. It never normalizes case, strips
    surrounding text, or guesses from a nearby token.
    """

    for role in CAPSULE_ROLES:
        if value == role:
            return role
    return None


def capsule_operation_or_none(value: str) -> CapsuleOperation | None:
    """Return ``value`` narrowed to :data:`CapsuleOperation`, or ``None`` when unknown."""

    for operation in CAPSULE_OPERATIONS:
        if value == operation:
            return operation
    return None


__all__ = [
    "CAPSULE_COMPOSITION_ORDER",
    "CAPSULE_LAUNCHER_MODE",
    "CAPSULE_OPERATIONS",
    "CAPSULE_ROLES",
    "CAPSULE_ROLE_MODE",
    "CAPSULE_SEAT_KINDS",
    "CapsuleCompositionRoot",
    "CapsuleOperation",
    "CapsuleRole",
    "CapsuleSeatKind",
    "capsule_operation_or_none",
    "capsule_role_or_none",
    "is_capsule_operation",
    "is_capsule_role",
]
