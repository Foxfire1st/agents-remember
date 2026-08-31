"""Server-side seat-retire authority policy (260707-HFX-L8, issue #12).

Authority split (developer ruling 2026-07-07): a manager may retire the leaf execution seats of its
own master and its same-master master-exit reviewer. The architect may retire its same-sprint
plan-review reviewer. The orchestrator holds the portfolio view and may retire any seat, including
a completed manager or super-exit reviewer. No seat ever retires itself. Refusals are loud and name
the exact policy clause that fired.
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
    structural_parent_task_document_ref: TaskDocumentRef | None = None
    structural_parent_role: str | None = None


def check_retire_authority(actor: SeatRef, target: SeatRef, topology: TaskDocumentTopology) -> None:
    """Raise :class:`RetirePolicyError` unless ``actor`` may retire ``target``.

    Owner-never-self-retires is checked FIRST, unconditionally -- no role's authority ever
    overrides it. A manager may then retire only a worker/reviewer/curator on one of its leaves or
    its same-master reviewer. The architect may retire only its same-sprint reviewer. Only the
    orchestrator has portfolio-wide retire authority.
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
            or (
                target_parent != actor.task_document_ref
                and not (
                    target.seat_role == "reviewer"
                    and target.task_document_ref == actor.task_document_ref
                )
            )
        ):
            raise RetirePolicyError(
                "manager may retire only worker/reviewer/curator seats of its own master: "
                "on its leaves or as its same-master reviewer "
                f"({actor.task_document_ref!r}); target is {target.seat_role!r} "
                f"under {target_parent!r}"
            )
        return
    if actor.seat_role == "architect":
        if (
            actor.task_document_ref is not None
            and target.task_document_ref == actor.task_document_ref
            and target.seat_role == "reviewer"
            and target.structural_parent_task_document_ref == actor.task_document_ref
            and target.structural_parent_role == "architect"
        ):
            return
        raise RetirePolicyError("architect may retire only its same-sprint plan-review reviewer")
    if actor.seat_role == "orchestrator":
        return
    raise RetirePolicyError(f"role {actor.seat_role!r} has no retire authority")
