"""What a role launch must carry as instructions.

A role launch compiles this value **before** it builds its launch request, and the value it gets
is one of exactly two modes:

``capsule``
    an admitted capsule was compiled for the seat, and the carrier below is set on the launch.
    This is the only mode in which a seat runs with instructions.

``refused``
    no capsule could be supplied. The launch must not proceed: a session that would run blind is
    exactly the defect this module exists to prevent, and a refusal a caller can ignore is not a
    refusal.

The compiler, the admission operation and the task projection live in ``application``; this
module ranks below them in ``layers.toml`` and holds only the value they answer with.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.serving.capsule_delivery import CapsuleDelivery

#: The operations a launch may select, in the launch's own vocabulary.
LAUNCH_OPERATION = "orientation"
"""The operation a launch compiles.

``orientation`` is not a new selection rule: it is the default the registered
``role_capsule_compile`` operation already declares (``operation: str = "orientation"``), and every
role the corpus admits carries it. A launch therefore compiles the same capsule a caller would get
by asking the shipped operation for that role and leaving its default operation in place.
"""


class LaunchCapsuleMode(Enum):
    """The two answers a launch may get, and the only two."""

    CAPSULE = "capsule"
    REFUSED = "refused"


@dataclass(frozen=True, slots=True)
class LaunchCapsuleRequest:
    """One launch's instruction identity, as the launch point already knows it.

    ``role`` is the seat role the launch was asked for, ``task_document_ref`` the admitted task
    binding when the seat has one, and ``workspace_root`` the directory the session will run in.
    Nothing here is a value the launch would otherwise not have had.
    """

    role: str | None
    workspace_root: Path
    task_document_ref: TaskDocumentRef | None = None
    allow_project_task_binding: bool = False
    """Explicitly allow a sprint/master task binding at the configured Projects workspace.

    The role-aware project launcher uses this only for orchestrator/manager launches when no task
    enclosure exists. The canonical task remains admitted; no taskless identity or fake worktree is
    created. Leaf roles and all other launch points leave it disabled.
    """


@dataclass(frozen=True, slots=True)
class LaunchCapsule:
    """One launch's resolved instruction delivery: the carrier, or the named refusal."""

    mode: LaunchCapsuleMode
    role: str | None = None
    delivery: CapsuleDelivery | None = None
    refusal_status: str = ""
    refusal_detail: str = ""

    @property
    def is_refusal(self) -> bool:
        return self.mode is LaunchCapsuleMode.REFUSED

    def explain(self) -> str:
        """One operator-facing line naming why this launch refused."""

        return (
            f"this launch cannot supply the capsule for role {self.role!r} "
            f"({self.refusal_status}): {self.refusal_detail}"
        )


def refused_launch_capsule(role: str | None, status: str, detail: str) -> LaunchCapsule:
    """The fail-closed answer: no capsule can be supplied for this seat."""

    return LaunchCapsule(
        mode=LaunchCapsuleMode.REFUSED,
        role=role,
        refusal_status=status,
        refusal_detail=detail,
    )


__all__ = [
    "LAUNCH_OPERATION",
    "LaunchCapsule",
    "LaunchCapsuleMode",
    "LaunchCapsuleRequest",
    "refused_launch_capsule",
]
