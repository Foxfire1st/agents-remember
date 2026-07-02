"""Leaf task-document lookup and lifecycle stamping (task domain).

A leaf's JSON-primary task document is the lifecycle-keyed work content of its
enclosure (task_doc stamps ``lifecycleId`` at create time when authored against
a leaf contract). Reopen (L11) makes the binding survive restarts too: the doc
follows the enclosure across lifecycles by explicit restamp, never by read-time
heuristics. The lookup mirrors the observer projection's exact joins — doc id,
``enclosures[]`` refs, then file stem, all case-insensitive (doc ids are
authored labels like ``260628-L11`` while enclosure leaf ids are lowercase
directory names like ``260628-l11``).
"""

from __future__ import annotations

from pathlib import Path

from agents_remember.tasks.document import TaskDocument
from agents_remember.tasks.store import read_task_doc, write_task_docs


def find_leaf_doc(task_root: Path, leaf_id: str) -> tuple[Path, TaskDocument] | None:
    """The leaf's task document under ``task_root``, or None when never authored."""
    want = leaf_id.strip().lower()
    if not want:
        return None
    for json_path in sorted(task_root.glob("*.json")):
        try:
            doc = read_task_doc(json_path)
        except Exception:
            continue
        if doc.kind == "master":
            continue
        if (doc.id or "").strip().lower() == want:
            return json_path, doc
        if any((ref.leafId or "").strip().lower() == want for ref in doc.enclosures):
            return json_path, doc
        if json_path.stem.strip().lower() == want:
            return json_path, doc
    return None


def restamp_leaf_doc_lifecycle(task_root: Path, leaf_id: str, lifecycle_id: str) -> dict | None:
    """Point the leaf's doc at ``lifecycle_id`` (the enclosure's current lifecycle).

    Overwrites any previous stamp: the enclosure's newest lifecycle IS the doc's
    binding — a reopened leaf's doc must follow the fresh lifecycle, not the
    finalized one. Returns a small report dict, or None when the leaf has no doc
    yet (a first start authors the doc afterwards, already stamped by task_doc).
    """
    found = find_leaf_doc(task_root, leaf_id)
    if found is None:
        return None
    json_path, doc = found
    if doc.lifecycleId == lifecycle_id:
        return {"docPath": json_path.as_posix(), "lifecycleId": lifecycle_id, "changed": False}
    data = doc.model_dump(by_alias=True)
    data["lifecycleId"] = lifecycle_id
    updated = TaskDocument.model_validate(data)
    write_task_docs(task_root, [updated])
    return {"docPath": json_path.as_posix(), "lifecycleId": lifecycle_id, "changed": True}
