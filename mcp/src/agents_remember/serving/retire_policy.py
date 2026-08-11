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
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks.document_refs import TaskDocumentRefError, TaskDocumentTopology

MANAGER_RETIRE_ROLES = frozenset({"worker", "reviewer", "curator"})


class RetirePolicyError(AgentsRememberError):
    """Raised when an actor lacks retire authority over a target seat."""


@dataclass(frozen=True)
class SeatRef:
    """One retire-policy seat, identified by its canonical document+role binding."""

    session_id: str
    task_document_ref: TaskDocumentRef | None
    seat_role: str


def check_retire_authority(actor: SeatRef, target: SeatRef, topology: TaskDocumentTopology) -> None:
    """Raise :class:`RetirePolicyError` unless ``actor`` may retire ``target``.

    Owner-never-self-retires is checked FIRST, unconditionally -- no role's authority ever
    overrides it. A manager may then retire only a worker/reviewer/curator of its own master; anything
    else it name-refuses. Only the orchestrator has portfolio-wide retire authority.
    """
    if actor.session_id == target.session_id:
        raise RetirePolicyError("a seat never retires itself (owner-never-self-retires)")
    if actor.seat_role == "manager":
        try:
            target_parent = (
                topology.parent(target.task_document_ref)
                if target.task_document_ref is not None
                else None
            )
        except TaskDocumentRefError as exc:
            raise RetirePolicyError(f"cannot resolve target task containment: {exc}") from exc
        if (
            actor.task_document_ref is None
            or target.seat_role not in MANAGER_RETIRE_ROLES
            or target_parent != actor.task_document_ref
        ):
            raise RetirePolicyError(
                "manager may retire only worker/reviewer/curator seats of its own master "
                f"({actor.task_document_ref!r}); target is {target.seat_role!r} "
                f"under {target_parent!r}"
            )
        return
    if actor.seat_role == "orchestrator":
        return
    raise RetirePolicyError(f"role {actor.seat_role!r} has no retire authority")
