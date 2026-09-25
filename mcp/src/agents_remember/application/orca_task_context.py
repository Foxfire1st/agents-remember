"""Task-hierarchy context and stable selection binding for Orca role launches."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.orca_launcher import OrcaRole, OrcaSelection
from agents_remember.tasks.document_refs import (
    ResolvedTaskDocument,
    TaskDocumentTopology,
)

ROLE_LEVELS = {
    "architect": "portfolio",
    "system-specialist": "portfolio",
    "orchestrator": "portfolio",
    "manager": "master",
    "worker": "leaf",
    "reviewer": "leaf",
    "curator": "leaf",
}
TASKLESS_ROLES = frozenset({"architect", "system-specialist"})
LEAF_ROLES = frozenset({"worker", "reviewer", "curator"})


@dataclass(frozen=True)
class OrcaRoleContext:
    role: OrcaRole
    sprint: ResolvedTaskDocument | None
    master: ResolvedTaskDocument | None
    task: ResolvedTaskDocument | None
    effective_task: ResolvedTaskDocument | None


def resolve_orca_role_context(
    config: McpRuntimeConfig, selection: OrcaSelection
) -> OrcaRoleContext:
    """Resolve the selected sprint/master/leaf chain from canonical task documents."""

    needs_sprint = selection.role not in TASKLESS_ROLES
    needs_master = selection.role in {"manager", *LEAF_ROLES}
    needs_task = selection.role in LEAF_ROLES
    refs = (
        selection.sprint_document_ref,
        selection.master_document_ref,
        selection.task_document_ref,
    )
    required = (needs_sprint, needs_master, needs_task)
    for label, ref, needed in zip(("sprint", "master", "task"), refs, required, strict=True):
        if needed and ref is None:
            raise ValueError(f"{selection.role} requires a canonical {label} selection.")
        if not needed and ref is not None:
            raise ValueError(f"{selection.role} does not accept a {label} selection.")

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
    return OrcaRoleContext(selection.role, sprint, master, task, task or master or sprint)


def selection_binding(selection: OrcaSelection | OrcaRoleContext) -> dict[str, Any]:
    """Serialize the exact canonical task references selected for one role."""

    if isinstance(selection, OrcaRoleContext):
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
