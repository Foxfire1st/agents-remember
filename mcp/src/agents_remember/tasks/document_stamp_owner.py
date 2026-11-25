"""The task and leaf a task document's stamped writes belong to.

A leaf's stamp names its master's id as the task and the leaf's own id as the owner; the leaf's
id is also the owner of its history file. A document that is no leaf (a master or a light task)
is its own task and owner. The master is the one the task-document writers resolve
(:mod:`.master_sync`); a master that cannot be read leaves the task empty, which a stamp reports
as unknown with its reason instead of refusing the write.
"""

from __future__ import annotations

from pathlib import Path

from .document import TaskDocument
from .master_sync import folder_master_json_path, master_json_path_from_ref
from .store import read_task_doc
from .writer_stamps import StampOwner


def document_stamp_owner(task_root: Path, document: TaskDocument) -> StampOwner:
    """The stamp owner of a write recorded on ``document`` in the folder ``task_root``."""

    return StampOwner(task=_task_id(task_root, document), kind="leaf", owner_id=document.id)


def _task_id(task_root: Path, document: TaskDocument) -> str:
    if document.kind != "subTask":
        return document.id
    try:
        path = (
            master_json_path_from_ref(task_root, leaf_id=document.id, master_ref=document.master)
            if document.master
            else folder_master_json_path(task_root, document)
        )
        return read_task_doc(path).id if path is not None else ""
    except (OSError, ValueError):
        return ""
