"""What ``knowledge_integrity_check`` returns for a leaf: its latest worklist (MIK-R08 rule 7).

A caller names the leaf by its series contract (``contractPath``); the tool returns the latest
persisted ``knowledge-worklist/v1`` beside it: the summary (state, digest, counts by kind, the inputs
an ``incomplete`` run could not read), one compact row per item, and the file's path, which holds
every item's facts. The tool computes nothing here -- the worklist is recomputed by each
memory-quality run (rule 8) -- so the answer is exactly what the last run persisted, or ``absent``.
MIK-R26 keeps the tool and points it at the validator and this worklist.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agents_remember.application.knowledge_worklist.leaf import (
    WORKLIST_FILE_NAME,
    read_leaf_worklist,
)
from agents_remember.memory_quality.knowledge_worklist_section import worklist_summary

__all__ = ["leaf_worklist_fields"]


def _compact(item: dict[str, Any]) -> dict[str, Any]:
    """One item's wire row; the MIK-R11 ``planning`` mark rides along where the item has one."""

    row = {"id": item.get("id"), "kind": item.get("kind"), "subject": item.get("subject")}
    if item.get("planning") is not None:
        row["planning"] = item["planning"]
    return row


def leaf_worklist_fields(contract_path: str) -> dict[str, Any]:
    """The response fields for the leaf whose series contract is at ``contract_path``."""

    path = Path(contract_path).parent / WORKLIST_FILE_NAME
    try:
        document = read_leaf_worklist(Path(contract_path))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return {
            "worklistState": "unreadable",
            "worklist": {"path": path.as_posix(), "detail": str(error)},
        }
    if document is None:
        return {"worklistState": "absent", "worklist": {"path": path.as_posix()}}
    return {
        "worklistState": "present",
        "worklist": {
            **worklist_summary(document, path.as_posix()),
            "owner": document.get("owner"),
            "items": [_compact(item) for item in document.get("items") or ()],
            "plannedEffects": document.get("plannedEffects"),
        },
    }
