"""The tray's second recipient class: a leaf explainer's live seat (MIK-R211 v5, the Manager's ruling 15:33Z).

The tray is the developer's channel to seats. An AR role seat is named by its launch record; a leaf
explainer has none -- it is no AR role (D607) -- but its environment records which seat is current:
the workspace's ``binding.json``, the generation counter kept with the task, and the attempt the seat
was made with (``explainer_state.seat_for_workspace``). An id that is the current seat of one
environment resolves to that leaf; an archived generation's id, or any other, resolves to nothing,
so the role path's own ``recipient-not-found`` stands.

The resolved record names the leaf and the seat the way a role seat's does, so the message is filed
and its first line written by the same code (``From developer · <leaf>``). It carries no held-message
store: a seat that cannot take the message now is refused by name rather than held.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agents_remember.canvas.explainer_errors import ExplainerRefused, ExplainerStateError
from agents_remember.canvas.explainer_paths import binding_path, explainer_roots, seat_counter_path
from agents_remember.canvas.explainer_state import generation, read_binding, seat_for_workspace
from agents_remember.cli.developer_seats import SeatRecord
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.tasks.document_refs import TaskDocumentRefError, TaskDocumentTopology

#: The recipient role a resolved explainer seat is filed under; the sender word role_message uses.
EXPLAINER_RECIPIENT = "explainer"


def explainer_recipient(
    config: McpRuntimeConfig, agent_id: str
) -> tuple[SeatRecord, dict[str, Any]] | None:
    """The live explainer seat ``agent_id`` names, as a record and a receipt, or ``None``."""

    for task_folder in explainer_roots(Path(config.coordination_root)):
        for workspace in sorted(task_folder.iterdir()):
            if workspace.is_symlink() or not binding_path(workspace).is_file():
                continue
            try:
                number = generation(seat_counter_path(workspace))
                current = seat_for_workspace(workspace, number) if number > 0 else None
            except (ExplainerStateError, ExplainerRefused, OSError, ValueError):
                continue
            if current == agent_id:
                return _resolved(config, workspace, agent_id)
    return None


def _resolved(
    config: McpRuntimeConfig, workspace: Path, agent_id: str
) -> tuple[SeatRecord, dict[str, Any]] | None:
    """The seat's record: its leaf's task references, read from the task documents."""

    try:
        held = read_binding(workspace)
    except ExplainerStateError:
        return None
    if held is None:
        return None
    topology = TaskDocumentTopology(config.coordination_root)
    repository, task = workspace.parent.parent.name, workspace.parent.name
    try:
        leaf = topology.ref_for_id(repository, task, held.binding.leaf_id)
        master = topology.parent(leaf)
        sprint = topology.parent(master) if master is not None else None
    except TaskDocumentRefError:
        return None
    selection = {
        name: ref.model_dump(mode="json")
        for name, ref in (
            ("taskDocumentRef", leaf),
            ("masterDocumentRef", master),
            ("sprintDocumentRef", sprint),
        )
        if ref is not None
    }
    record = SeatRecord(
        path=binding_path(workspace),
        agent_id=agent_id,
        workspace_id=None,
        role=EXPLAINER_RECIPIENT,
        selection=selection,
        created_at=held.prepared_at,
    )
    return record, {"agentId": agent_id, "role": EXPLAINER_RECIPIENT, "selection": selection}


__all__ = ["EXPLAINER_RECIPIENT", "explainer_recipient"]
