"""Server-side seat-retire authority policy (260707-HFX-L8, issue #12).

Authority split (developer ruling 2026-07-07): a manager lives OUTSIDE the master stack it
manages, so it may retire only the worker/reviewer/curator seats of its OWN master -- it can never unseat
itself by construction (a manager's own seat is never a worker/reviewer of its own master). The
orchestrator holds the portfolio view and may retire any seat, including a completed manager. No
seat ever retires itself. Refusals are loud and name the exact policy clause that fired.
"""

from __future__ import annotations

from dataclasses import dataclass

from agents_remember.errors import AgentsRememberError

MANAGER_RETIRE_ROLES = frozenset({"worker", "reviewer", "curator"})


class RetirePolicyError(AgentsRememberError):
    """Raised when an actor lacks retire authority over a target seat."""


@dataclass(frozen=True)
class SeatRef:
    """One retire-policy seat, identified by its ``(leaf_key, seat_role)`` binding."""

    session_id: str
    leaf_key: str | None
    seat_role: str

    @property
    def master(self) -> str | None:
        return master_of(self.leaf_key)


def master_of(leaf_key: str | None) -> str | None:
    """The master-folder segment of a qualified leaf key (``repo/master/doc-id``), or ``None``.

    Uniform for every dispatch level: a worker/reviewer's ``leaf_key`` names its own leaf under the
    master folder, and a manager's own ``leaf_key`` names the master task-doc itself under the same
    folder -- either way the second path segment is the master identity retire authority compares.
    """
    if not leaf_key:
        return None
    parts = leaf_key.split("/", 2)
    return parts[1] if len(parts) >= 2 else None


def check_retire_authority(actor: SeatRef, target: SeatRef) -> None:
    """Raise :class:`RetirePolicyError` unless ``actor`` may retire ``target``.

    Owner-never-self-retires is checked FIRST, unconditionally -- no role's authority ever
    overrides it. A manager may then retire only a worker/reviewer/curator of its own master; anything
    else it name-refuses. Only the orchestrator has portfolio-wide retire authority.
    """
    if actor.session_id == target.session_id:
        raise RetirePolicyError("a seat never retires itself (owner-never-self-retires)")
    if actor.seat_role == "manager":
        if target.seat_role not in MANAGER_RETIRE_ROLES or target.master != actor.master:
            raise RetirePolicyError(
                "manager may retire only worker/reviewer/curator seats of its own master "
                f"({actor.master!r}); target is {target.seat_role!r} of {target.master!r}"
            )
        return
    if actor.seat_role == "orchestrator":
        return
    raise RetirePolicyError(f"role {actor.seat_role!r} has no retire authority")
