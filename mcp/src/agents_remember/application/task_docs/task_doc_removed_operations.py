"""Removed task_doc read operations (MIK-R111 rule 2, F01.11).

``get`` and ``read_steps`` were removed and are not reads here: the rendered
as a file. Their refusal names the canonical JSON and rendered filenames
from the addressing alone, never parsing the document, so a missing or
damaged document refuses exactly like a valid one.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agents_remember.tasks import markdown_path_for, read_task_doc

from .task_doc_response import task_doc_result


def removed_read_operation_refusal(
    task_root: Path, slug: str | None, operation: str
) -> dict[str, Any]:
    """Refuse a removed read operation with the files to read instead.

    Target and address validation already ran in the caller (an unknown repo
    or task still raises there); only content reads are skipped here. When
    the document does parse, its identity rides as before.
    """
    stem = slug or "task"
    json_path = task_root / f"{stem}.json"
    rendered_path = task_root / f"{stem}.md"
    try:
        doc = read_task_doc(json_path)
    except (OSError, ValueError):
        doc = None
    if doc is None:
        sentence = (
            f"task_doc {operation} was removed: the task document {json_path.as_posix()} "
            f"cannot be read here; read the rendered {rendered_path.name} beside it "
            "with the file tool."
        )
        return {
            "ok": False,
            "operation": f"task_doc.{operation}",
            "slug": stem,
            "status": "unknown",
            "docPath": json_path.as_posix(),
            "renderedPath": rendered_path.as_posix(),
            "code": "operation-removed",
            "error": sentence,
            "detail": sentence,
            "nextAction": "Read the rendered task document with the file tool.",
        }
    markdown_path = markdown_path_for(task_root, doc)
    sentence = (
        f"task_doc {operation} was removed: read the rendered document "
        f"{markdown_path.as_posix()} with the file tool, "
        f"and the JSON beside it ({json_path.as_posix()})."
    )
    result = task_doc_result(operation, doc, json_path, markdown_path, master_sync=None)
    result["ok"] = False
    result["code"] = "operation-removed"
    result["error"] = sentence
    result["detail"] = sentence
    result["nextAction"] = "Read the rendered task document with the file tool."
    return result
