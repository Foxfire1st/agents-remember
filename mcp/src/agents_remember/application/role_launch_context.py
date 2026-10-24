"""Task-hierarchy context and stable selection binding for role launches."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.role_launcher import LauncherRole, RoleSelection
from agents_remember.tasks.document_refs import (
    ResolvedTaskDocument,
    TaskDocumentTopology,
)

ROLE_LEVELS = {
    "architect": "portfolio",
    "investigator": "portfolio",
    "orchestrator": "portfolio",
    "manager": "master",
    "worker": "leaf",
    "reviewer": "leaf",
    "curator": "leaf",
    # The free agent a new user reaches through the one start call (MIK-R196): it holds no
    # task altitude, so no per-level override applies to it and its role row is the whole
    # of its launch choices.
    "bootstrap": "free-agent",
}
# The roles whose execution is keyed by its request id rather than by a document: a
# selection with no document at all. ``bootstrap`` joins them as the identity-free first
# seat (MIK-R196 rule 1), not as a second identity of any other role.
TASKLESS_ROLES = frozenset({"architect", "investigator", "bootstrap"})
LEAF_ROLES = frozenset({"worker", "reviewer", "curator"})

# What may be started without a parent (MIK-R113 rule 6): the selections the
# dashboard's start accepts, per role as (sprint, master, task) with True =
# required and False = forbidden. The investigator takes no leaf and at least
# one of sprint/master; that row reads (True, None, False) with None = accepted
# but not required. The dashboard's start and a chat without a role use this
# one table: the dispatch endpoint checks it for a start with no starter, and
# ``resolve_role_launch_context`` below reads it too, so the two cannot drift
# apart and a refused selection is refused with the same reason either way.
PARENTLESS_START_SELECTIONS: dict[str, tuple[bool | None, bool | None, bool | None]] = {
    "architect": (False, False, False),
    # The free agent takes no document at all (MIK-R196 rule 2): no sprint, master, leaf,
    # task document, worktree or enclosure is invented for it, and a repository is chosen
    # inside its dashboard conversation rather than by its launch.
    "bootstrap": (False, False, False),
    "investigator": (True, None, False),
    "orchestrator": (True, False, False),
    "manager": (True, True, False),
    "worker": (True, True, True),
    "reviewer": (True, True, True),
    "curator": (True, True, True),
}


def parentless_selection_violation(
    role: str,
    sprint_ref: Any,
    master_ref: Any,
    task_ref: Any,
) -> str | None:
    """Why a parentless start of ``role`` on these references is refused, else None."""

    if role == "investigator" and task_ref is not None:
        return (
            "An investigator does not accept a leaf selection; start it on the master and "
            "name the leaf in the first message."
        )
    if role == "investigator":
        required: tuple[bool | None, bool | None, bool | None] = (
            bool(sprint_ref or master_ref),
            bool(master_ref),
            False,
        )
    else:
        required = PARENTLESS_START_SELECTIONS[role]
    for label, ref, needed in zip(
        ("sprint", "master", "task"),
        (sprint_ref, master_ref, task_ref),
        required,
        strict=True,
    ):
        if needed and ref is None:
            return f"{role} requires a canonical {label} selection."
        if not needed and ref is not None:
            return f"{role} does not accept a {label} selection."
    return None


@dataclass(frozen=True)
class RoleLaunchContext:
    role: LauncherRole
    sprint: ResolvedTaskDocument | None
    master: ResolvedTaskDocument | None
    task: ResolvedTaskDocument | None
    effective_task: ResolvedTaskDocument | None


def resolve_role_launch_context(
    config: McpRuntimeConfig, selection: RoleSelection
) -> RoleLaunchContext:
    """Resolve the selected sprint/master/leaf chain from canonical task documents."""

    violated = parentless_selection_violation(
        selection.role,
        selection.sprint_document_ref,
        selection.master_document_ref,
        selection.task_document_ref,
    )
    if violated is not None:
        raise ValueError(violated)

    topology = TaskDocumentTopology(config.coordination_root)
    sprint = (
        topology.resolve(selection.sprint_document_ref) if selection.sprint_document_ref else None
    )
    master = (
        topology.resolve(selection.master_document_ref) if selection.master_document_ref else None
    )
    task = topology.resolve(selection.task_document_ref) if selection.task_document_ref else None
    if sprint and (sprint.document.kind != "master" or not sprint.document.orchestrates):
        raise ValueError("The selected sprint is not a canonical orchestration document.")
    if master:
        if master.document.kind != "master" or master.document.orchestrates:
            raise ValueError("The selected master is not a canonical non-sprint master document.")
        if sprint is None or topology.parent(master.ref) != sprint.ref:
            raise ValueError("The selected master is not commanded by the selected sprint.")
    if task and (
        task.document.kind != "subTask" or master is None or topology.parent(task.ref) != master.ref
    ):
        raise ValueError("The selected leaf is not a child of the selected master.")
    return RoleLaunchContext(selection.role, sprint, master, task, task or master or sprint)


def selection_binding(selection: RoleSelection | RoleLaunchContext) -> dict[str, Any]:
    """Serialize the exact canonical task references selected for one role."""

    if isinstance(selection, RoleLaunchContext):
        refs = {
            "sprintDocumentRef": selection.sprint.ref if selection.sprint else None,
            "masterDocumentRef": selection.master.ref if selection.master else None,
            "taskDocumentRef": selection.task.ref if selection.task else None,
        }
        role = selection.role
    else:
        refs = {
            "sprintDocumentRef": selection.sprint_document_ref,
            "masterDocumentRef": selection.master_document_ref,
            "taskDocumentRef": selection.task_document_ref,
        }
        role = selection.role
    return {
        "role": role,
        **{name: ref.model_dump(mode="json") if ref else None for name, ref in refs.items()},
    }
