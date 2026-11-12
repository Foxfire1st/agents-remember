"""A label never removes a landed leaf: the one check behind every place that completes a master.

An abandoned row is a finished record of a leaf that will not run. It blocks nothing and is asked
for no enclosure, unless the enclosure it does have records a completed integration: that row
contradicts itself. Every operation that completes a master decides this again at its own moment
(the task tool for an organizational master, the closeout for an atomic one, and finalization for
both), because the row's label may have been set after the operation before it looked.
"""

from __future__ import annotations

from pathlib import Path

from agents_remember.tasks import TaskDocument
from agents_remember.worktrees.task_resolver import leaf_enclosure_path
from agents_remember.worktrees.worktree_contract import load_contract


def require_abandoned_rows_unlanded(
    task_root: Path, master: TaskDocument, *, then: str, error: type[Exception]
) -> None:
    """Refuse, by name, an abandoned row whose enclosure records a completed integration.

    ``then`` says how the refused operation is repeated once the row is reconciled.
    """

    for row in master.subTasks:
        if row.status != "abandoned":
            continue
        path = leaf_enclosure_path(task_root, row.number)
        if not path.exists() and not path.is_symlink():
            continue
        try:
            enclosure = load_contract(path)
        except (OSError, ValueError) as exc:
            raise error(
                f"master {master.id!r} cannot be completed: enclosure {path} of abandoned row "
                f"{row.number!r} cannot be read ({exc}), so it is not known whether that leaf "
                f"landed; repair or remove that file, then {then}"
            ) from exc
        if enclosure.integration_status == "completed":
            raise error(
                f"master {master.id!r} cannot be completed: row {row.number!r} is abandoned but "
                f"enclosure {path} records completed integration; reconcile that row's status "
                "with its landing (the leaf landed, so set the row to Completed with task_doc "
                f"set_subtask on the master), then {then}"
            )
