"""Read the master's declared open siblings for a packet-write answer, without a gate."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agents_remember.models.task_document import RESOLVED_MASTER_ROW_STATUSES
from agents_remember.models.task_intent import ApprovedRequirementPacketRef
from agents_remember.tasks import TaskDocument, read_task_doc
from agents_remember.tasks.master_sync import folder_master_json_path, master_json_path_from_ref

_MESSAGE = "Sweep these against the new or revised packet before release (operations/planning.md)."
_PACKET = re.compile(r"\b([A-Za-z][A-Za-z0-9_.-]*@v[1-9][0-9]*)\b")
_LOGGER = logging.getLogger(__name__)


def _requirement_ids(document: TaskDocument) -> list[str]:
    identities: list[str] = []
    for requirement in document.requirements:
        if isinstance(requirement, ApprovedRequirementPacketRef):
            identities.append(f"{requirement.stableId}@{requirement.version}")
        else:
            identities.extend(_PACKET.findall(requirement))
    return list(dict.fromkeys(identities))


def _unavailable(sibling_id: str, cause: str) -> dict[str, Any]:
    _LOGGER.warning("Alignment sweep: row %r unavailable (%s)", sibling_id, cause)
    return {"id": sibling_id, "state": "unavailable"}


def _sibling(sibling_id: str, reference: str, master_root: Path) -> dict[str, Any]:
    if not reference.strip():
        return _unavailable(sibling_id, "no-file")
    sibling_path = Path(reference)
    if not sibling_path.name:
        return _unavailable(sibling_id, "no-name")
    try:
        sibling = read_task_doc(master_root / sibling_path.with_suffix(".json"))
    except FileNotFoundError:
        return _unavailable(sibling_id, "missing")
    return {"id": sibling_id, "state": "available", "requirements": _requirement_ids(sibling)}


def alignment_sweep(
    task_root: Path,
    document: TaskDocument,
    *,
    operation: str,
    fields: Mapping[str, Any],
    original: TaskDocument | None,
) -> dict[str, Any] | None:
    """Name siblings only for leaf creation or an authored requirements replacement.

    This derives response metadata before publication. It neither changes task documents nor
    decides whether the author may release a packet. Master rows, not a folder census, declare
    the sibling set; the native task reader supplies each referenced requirement identity.
    """
    if document.kind != "subTask":
        return None
    requirement_edit = operation in {"set_field", "replace"} and (
        "requirements" in fields
        or (original is not None and original.requirements != document.requirements)
    )
    if operation != "create" and not requirement_edit:
        return None
    master_path = (
        master_json_path_from_ref(task_root, leaf_id=document.id, master_ref=document.master)
        if document.master
        else folder_master_json_path(task_root, document)
    )
    if master_path is None:
        return None
    master = read_task_doc(master_path)
    rows = [
        row
        for row in master.subTasks
        if row.number != document.id
        and row.status not in RESOLVED_MASTER_ROW_STATUSES
        and row.retirement is None
    ]
    if not rows:
        return None
    if len(rows) > 12:
        return {
            "message": _MESSAGE,
            "openLeafCount": len(rows),
            "masterTaskDocument": str(master_path.with_suffix(".md")),
        }
    return {
        "message": _MESSAGE,
        "openLeaves": [_sibling(row.number, row.file, master_path.parent) for row in rows],
    }
