"""Resolving a requirement endpoint of the text knowledge format (MIK-R13 rule 4, MIK-R21 rule 6).

A record's link or reference may name a requirement packet by ``{ task, packet, id, version }``
(:class:`…knowledge_files.shapes.RequirementReference`). ``task`` is ``{ repository, path }``, with
``path`` relative to ``tasks/<repository>/`` of the coordination root, so the owning task's root is
``<coordination root>/tasks/<repository>/<path>``.

The **requirement owner remains the resolver**: this module only locates that task root and hands
``{ packet, id, version }`` to :func:`.requirement_owner.consume_owner_resolution`, which carries
the owner's own answer back verbatim. Two answers are this module's own, and both are about the
root, never the packet:

* ``requirement-task-plane-unavailable`` -- the caller has no coordination root (for example a
  standalone run);
* ``requirement-task-outside-tasks`` -- ``repository`` is not one plain directory name, so the
  task root would leave ``tasks/``.

An endpoint that does not resolve is **reported as unresolved, never refused**: the record keeps
it exactly as written. The resolved task root is returned so a later reader -- MIK-R14's lookup of a
newer approved version in that task's ``requirements/manifest.json`` -- starts from the same place.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from agents_remember.memory.knowledge.requirement_owner import consume_owner_resolution
from agents_remember.models.knowledge.requirement import RequirementOwnerRef
from agents_remember.models.knowledge_files.shapes import RequirementReference

TASK_PLANE_UNAVAILABLE: Final = "requirement-task-plane-unavailable"
TASK_OUTSIDE_TASKS: Final = "requirement-task-outside-tasks"

EndpointState = Literal["resolved", "unresolved"]


@dataclass(frozen=True)
class RequirementEndpoint:
    """One requirement endpoint and the owner's answer about it.

    ``task_root`` is the owning task's directory whenever it could be located, resolved or not.
    ``code`` and ``detail`` are the owner's refusal, verbatim, or one of this module's two root
    answers; both are empty when the endpoint resolved.
    """

    reference: RequirementReference
    state: EndpointState
    task_root: Path | None
    code: str = ""
    detail: str = ""

    @property
    def key(self) -> str:
        """``<repository>/<task path>#<id>@<version>``, the index's spelling of the endpoint."""

        task = self.reference.task
        return f"{task.repository}/{task.path}#{self.reference.id}@{self.reference.version}"


def requirement_task_root(coordination_root: Path, reference: RequirementReference) -> Path | None:
    """The owning task's root, or ``None`` when ``repository`` is not one plain directory name."""

    repository = reference.task.repository
    if "/" in repository or "\\" in repository or repository in {".", ".."}:
        return None
    return coordination_root / "tasks" / repository / reference.task.path


def resolve_requirement_endpoint(
    coordination_root: Path | None, reference: RequirementReference
) -> RequirementEndpoint:
    """Ask the requirement owner whether ``reference`` names an existing packet of that version."""

    if coordination_root is None:
        return RequirementEndpoint(
            reference,
            "unresolved",
            None,
            TASK_PLANE_UNAVAILABLE,
            "no coordination root was given, so the owning task cannot be read",
        )
    task_root = requirement_task_root(coordination_root, reference)
    if task_root is None:
        return RequirementEndpoint(
            reference,
            "unresolved",
            None,
            TASK_OUTSIDE_TASKS,
            f"task repository {reference.task.repository!r} is not one directory under tasks/",
        )
    resolution = consume_owner_resolution(
        task_root,
        RequirementOwnerRef(
            path=reference.packet, stableId=reference.id, version=reference.version
        ),
    )
    if resolution.state == "resolved":
        return RequirementEndpoint(reference, "resolved", task_root)
    return RequirementEndpoint(
        reference,
        "unresolved",
        task_root,
        resolution.refusal_code or "",
        resolution.refusal_detail or "",
    )
