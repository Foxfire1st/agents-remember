"""Which seats one ``role_start_set`` call starts for its leaf.

The leaf's task document decides the set: a consolidation task's set is its Consolidator and
its Reviewer, an ordinary leaf's set is its Worker, its Reviewer and its Curator. So a set holds a
Worker or a Consolidator, never both, and a set with a Consolidator has no Curator. The caller's
seat choices and replacements must name seats of that set; a call naming another seat is
refused before anything is prepared, with the set the document gives and how to call again.

The seat that does the leaf's work opens at once; every other seat is held until its first
message, and a set's ``reviewer_message`` opens the Reviewer at once.
"""

from __future__ import annotations

from pathlib import Path

from agents_remember.cli.role_message_delivery import _Refused
from agents_remember.models.role_agents import RoleStartSetCall
from agents_remember.tasks.consolidation_task import (
    CONSOLIDATION_LEAF_SEATS,
    ORDINARY_LEAF_SEATS,
    leaf_seat_roles_of,
)

#: The seats a set opens at once: the one that does the leaf's work.
OPEN_AT_ONCE_SEATS = frozenset({"worker", "consolidator"})
#: Every seat a set call can name a choice or a replacement for.
NAMEABLE_SEATS: tuple[str, ...] = (*ORDINARY_LEAF_SEATS, "consolidator")


def set_seat_roles(coordination_root: Path, call: RoleStartSetCall) -> tuple[str, ...]:
    """The seats this set starts, in start order, or a refusal naming the seats it may not."""

    unknown = sorted(set(call.replaces) - set(NAMEABLE_SEATS))
    if unknown:
        raise _Refused(
            "launch-refused",
            f"The set names replacements for no seat of a leaf: {', '.join(unknown)}. A set "
            f"replaces only {', '.join(NAMEABLE_SEATS)}.",
            "Name in replaces only seats of the leaf's set and call again.",
        )
    roles = (
        leaf_seat_roles_of(coordination_root, call.task_document_ref)
        if call.task_document_ref is not None
        else ORDINARY_LEAF_SEATS
    )
    foreign = sorted(_named_seats(call) - set(roles))
    if foreign:
        raise _Refused("launch-refused", _foreign_detail(roles, foreign), _foreign_remedy(roles))
    return roles


def _named_seats(call: RoleStartSetCall) -> set[str]:
    """The seats the call names a launch choice or a replacement for."""

    chosen = {
        role
        for role in NAMEABLE_SEATS
        if (override := getattr(call, role)).agent
        or override.model
        or override.effort
        or override.feature_values
    }
    return chosen | set(call.replaces)


def _foreign_detail(roles: tuple[str, ...], foreign: list[str]) -> str:
    kind = (
        "a consolidation task: its task document names a consolidationList"
        if roles == CONSOLIDATION_LEAF_SEATS
        else "an ordinary task: its task document names no consolidationList"
    )
    return (
        f"This leaf is {kind}, so its set is {', '.join(roles)}. The call names "
        f"{', '.join(foreign)}, which this set does not start; a set holds a Worker or a "
        "Consolidator, never both, and a set with a Consolidator has no Curator."
    )


def _foreign_remedy(roles: tuple[str, ...]) -> str:
    if roles == CONSOLIDATION_LEAF_SEATS:
        return (
            "Call role_start_set again with choices and replacements only for consolidator and "
            "reviewer."
        )
    return (
        "Call role_start_set again with choices and replacements only for worker, reviewer and "
        "curator; for a consolidation pass, name its list in the task document's "
        "consolidationList with task_doc before the set starts."
    )


__all__ = ["NAMEABLE_SEATS", "OPEN_AT_ONCE_SEATS", "set_seat_roles"]
