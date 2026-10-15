"""MIK-R55 rule 4: a record link whose code target this leaf removed.

Links are retrieval pointers, not range-content judgments. A content change raises nothing;
only a disappeared path/directory or a symbol that resolved at B and no longer does at C
holds the leaf. No history row answers it: K_C must remove or restate that target key.
"""

from __future__ import annotations

from typing import Any, Final

from agents_remember.application.knowledge_worklist.code import CodeTrees
from agents_remember.application.knowledge_worklist.knowledge import KnowledgeSide
from agents_remember.application.knowledge_worklist.registry import (
    ItemKind,
    item_id,
    register_item_kind,
)
from agents_remember.models.knowledge_files.reconsideration import link_target_key
from agents_remember.models.knowledge_files.shapes import Anchor

LINK_TARGET_KIND: Final = register_item_kind(
    ItemKind(
        name="link_target",
        subject="record link (link:<record ID>#<target key>)",
        subject_pattern=r"^link:\S",
        facts=("record", "targetKey", "target", "relation", "removed"),
        satisfying_row="no row answers it; remove or restate the link in K_C (MIK-R55 rule 4)",
        row_lookup=lambda _subject, _history: None,
        owner="MIK-R55",
    )
)


def target_key(target: Any) -> str:
    return link_target_key(target if isinstance(target, str) else target.to_document())


def holds_target(side: KnowledgeSide, record_id: str, key: str) -> bool:
    indexed = side.parsed.records.get(record_id)
    return indexed is not None and any(
        target_key(link.target) == key for link in getattr(indexed.record, "links", ())
    )


def _removed(code: CodeTrees, target: Any) -> str | None:
    before, after = code.base(), code.candidate()
    if isinstance(target, str) and target.startswith("route:"):
        route = target.removeprefix("route:")
        prefix = "" if route == "." else f"{route}/"
        if any(path.startswith(prefix) for path in before) and not any(
            path.startswith(prefix) for path in after
        ):
            return "route_absent"
    if isinstance(target, Anchor) and target.path is not None:
        path = target.path
        if path in before and path not in after:
            return "path_absent"
        if target.locator.kind == "symbol" and path in before and path in after:
            locator = target.locator.to_document()
            at_base = code.resolve(path, locator, target.blob, before[path], side="base")
            at_candidate = code.resolve(path, locator, target.blob, after[path])
            if at_base is not None and at_candidate is None:
                return "symbol_absent"
    return None


def link_target_items(
    code: CodeTrees, base: KnowledgeSide, candidate: KnowledgeSide
) -> list[dict[str, Any]]:
    items: dict[str, dict[str, Any]] = {}
    for record_id, indexed in sorted(base.parsed.records.items()):
        for link in getattr(indexed.record, "links", ()):
            if link.relation == "reconsider_on":
                continue
            key = target_key(link.target)
            if not holds_target(candidate, record_id, key):
                continue
            removed = _removed(code, link.target)
            if removed is None:
                continue
            subject = f"link:{record_id}#{key}"
            items[subject] = {
                "id": item_id("link_target", subject, [key, removed]),
                "kind": "link_target",
                "subject": subject,
                "facts": {
                    "record": record_id,
                    "targetKey": key,
                    "relation": link.relation,
                    "target": link.target
                    if isinstance(link.target, str)
                    else link.target.to_document(),
                    "removed": removed,
                },
                "satisfiedBy": None,
            }
    return [items[key] for key in sorted(items)]
