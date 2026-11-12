"""Undo this leaf's obsolete anchor refresh when its base link is current again (MIK-R54)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from agents_remember.models.knowledge_files.reconsideration import parse_reconsider_subject

from .code_anchors import AnchorResolutionError
from .memory_state import deep_copy

if TYPE_CHECKING:
    from .authoring import Authoring


def restore_refreshes(operation: Authoring) -> None:
    if operation.owner.kind != "leaf":
        return
    for subject in _refreshed_subjects(operation) - operation.reconsiderations.keys():
        parsed = parse_reconsider_subject(subject)
        if parsed is None:
            continue
        decision, alternative = parsed
        found = operation.state.record(decision)
        base = operation.state.base_record(decision)
        if found is None or base is None:
            continue
        _path, _kind, stored = found
        links = deep_copy(stored).get("links") or []
        base_links = base.get("links") or []
        changed = False
        for index, link in enumerate(links):
            old = base_links[index] if index < len(base_links) else None
            if isinstance(old, Mapping) and _restorable(link, old, alternative, operation):
                links[index] = deep_copy(old)
                changed = True
        if changed:
            operation._place_record(
                "decision",
                {**deep_copy(stored), "links": links},
                slug=None,
                before=stored,
                handoff_entry=subject,
            )
            operation.notes.append(
                f"{subject}: the fired item disappeared; restored its current base link"
            )


def _refreshed_subjects(operation: Authoring) -> set[str]:
    subjects: set[str] = set()
    for path in operation.state.files:
        history = operation.state.document(path) if path.startswith("knowledge/history/") else None
        if history is not None and history.get("leaf") == operation.owner.id:
            subjects.update(
                str(row["subject"])
                for row in history.get("rows", [])
                if row.get("disposition") == "still_rejected"
            )
    return subjects


def _restorable(link: Any, old: Any, alternative: int, operation: Authoring) -> bool:
    if not isinstance(link, Mapping) or not isinstance(old, Mapping) or link == old:
        return False
    if (
        any(link.get(key) != old.get(key) for key in ("relation", "alternative"))
        or old.get("relation") != "reconsider_on"
        or old.get("alternative") != alternative
    ):
        return False
    target, original = link.get("target"), old.get("target")
    if not isinstance(target, Mapping) or not isinstance(original, Mapping):
        return False
    path, locator = original.get("path"), original.get("locator")
    if (
        any(target.get(key) != original.get(key) for key in ("path", "locator"))
        or not isinstance(path, str)
        or not isinstance(locator, Mapping)
    ):
        return False
    try:
        return operation.code.resolve(path, locator).content == original.get("content")
    except AnchorResolutionError:
        return False
