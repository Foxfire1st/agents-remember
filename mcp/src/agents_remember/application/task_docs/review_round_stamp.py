"""The stamps a recorded memory review round stores.

The recorder is the agent this tool server was launched for, read from its launch binding and its
seat's launch receipt (:func:`caller_stamp`). The agent that handed the memory change to the review
is the Curator or Consolidator registered as active on the leaf at that moment, stamped with the
same time (:func:`registered_handover_stamp`), so the round keeps it whatever seat holds the role
later. The review payload names neither. The closeout takes both stamps of the passed memory round
from here. A code round stores no stamp: nothing in the memory reads it.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agents_remember.application.writer_stamp_source import caller_stamp
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import TaskDocument
from agents_remember.tasks.document_stamp_owner import document_stamp_owner
from agents_remember.tasks.store import json_path_for
from agents_remember.worktrees.review_handover import registered_handover_stamp
from agents_remember.worktrees.review_history import RoundStamps


def memory_round_stamp(
    coordination_root: Path,
    task_root: Path,
    document: TaskDocument,
    payload: Mapping[str, Any] | None,
    *,
    repository: str,
) -> RoundStamps | None:
    """The stamps for a memory review result, or ``None`` for a code result."""

    if not isinstance(payload, Mapping) or payload.get("reviewKind") != "memory":
        return None
    owner = document_stamp_owner(task_root, document)
    recorded = caller_stamp(owner, coordination_root=coordination_root)
    leaf = TaskDocumentRef(
        repository=repository, path=f"{task_root.name}/{json_path_for(task_root, document).name}"
    )
    handed = registered_handover_stamp(task_root, leaf, owner, recorded.at)
    return RoundStamps(recorded_by=recorded.to_document(), handed_over_by=handed.to_document())
