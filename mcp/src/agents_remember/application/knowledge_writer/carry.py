"""The writer's mechanical carry-forward of ``carried`` entries (MIK-R08 definition 4, MIK-R12).

An entry is ``carried`` when its path's blob at C differs from the entry's ``blob`` while the range
its locator names at C holds identical content: the file changed elsewhere, the anchored bytes did
not. Such an entry needs no disposition; the writer re-records it at C in every operation:

* ``blob`` becomes the path's blob at C;
* a ``line_range`` takes the lines its range maps to through the zero-context diff (a symbol keeps its
  name, and a ``file`` anchor with identical content already has the same blob).

Nothing else changes: not the locator kind, not the name, not ``content``. An entry whose content at
C differs, or whose locator does not resolve there, is never touched -- that is the curator's item.

A row of the owner's open history file that covers a carried entry with the entry's old anchor as its
``after`` gets the new anchor as its ``after`` in the same operation, because MIK-R07 rule 4 binds
``after`` to the entry's anchor in K_C and a mechanical ``blob`` update never reopens an item
(MIK-R07, Failure). A closed history file is frozen and is never edited.
"""

from __future__ import annotations

from typing import Any

from agents_remember.application.knowledge_worklist.code import CodeReadError, CodeTrees
from agents_remember.application.knowledge_writer.code_anchors import CodeSnapshot
from agents_remember.application.knowledge_writer.memory_state import MemoryState, Owner
from agents_remember.memory.conversion.code_objects import CodeObjects
from agents_remember.models.knowledge_files.documents import history_path

__all__ = ["carry_entries"]


def _carried_anchor(code: CodeTrees, source: str, anchor: dict[str, Any], blob: str) -> Any:
    locator = anchor.get("locator") or {}
    try:
        resolved = code.resolve(source, locator, str(anchor.get("blob")), blob)
    except CodeReadError:
        return None
    if resolved is None or resolved.content != anchor.get("content"):
        return None
    moved = dict(locator)
    if moved.get("kind") == "line_range":
        moved["start"], moved["end"] = resolved.span
    return {**anchor, "locator": moved, "blob": blob}


def carry_entries(state: MemoryState, snapshot: CodeSnapshot, owner: Owner) -> tuple[str, ...]:
    """Re-record every carried entry of the candidate at C; return their IDs (sorted)."""

    code = CodeTrees(CodeObjects(snapshot.root), snapshot.tree, snapshot.tree)
    moved: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for path, sidecar in list(state.sidecars()):
        source = str(sidecar.get("path"))
        blob = snapshot.blobs.get(source)
        if blob is None:
            continue
        for key in ("realizes", "proves"):
            for entry in sidecar.get(key) or ():
                anchor = entry.get("anchor")
                if not isinstance(anchor, dict) or anchor.get("blob") == blob:
                    continue
                carried = _carried_anchor(code, source, anchor, blob)
                if carried is None:
                    continue
                entry["anchor"] = carried
                state.touch(path)
                moved[str(entry.get("id"))] = (
                    {**anchor, "path": source},
                    {**carried, "path": source},
                )
    _carry_rows(state, owner, moved)
    return tuple(sorted(moved))


def _carry_rows(
    state: MemoryState, owner: Owner, moved: dict[str, tuple[dict[str, Any], dict[str, Any]]]
) -> None:
    path = history_path(owner.id)
    history = state.document(path)
    if not moved or history is None or history.get("closed") is True:
        return
    for row in history.get("rows") or ():
        for cover in row.get("covers") or ():
            carried = moved.get(str(cover.get("id")))
            if carried is not None and cover.get("after") == carried[0]:
                cover["after"] = carried[1]
                state.touch(path)
