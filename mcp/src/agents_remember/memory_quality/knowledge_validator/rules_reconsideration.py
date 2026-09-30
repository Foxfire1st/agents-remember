"""MIK-R14's guard on ``reconsider_on`` links in the validator's registry (MIK-R22 rule 9).

A ``reconsider_on`` link addresses its alternative by position (``alternative``: the index in
``alternatives``, subject ``reconsider:<DEC-ID>#<index>``). Reordering a decision's alternatives
would silently retarget such a link -- and the worklist subject and every history row about it --
to another alternative. The canonical formatter keeps the authored order, so only an edit moves one.

* ``R14.1-linked-alternative-order`` (refusing): against every comparison base holding the same
  decision, an alternative that a ``reconsider_on`` link addresses on either side keeps its index.
  An alternative is followed by its ``option`` text: a linked alternative whose option appears at
  another index on the other side has moved, and so has an alternative that moved into a linked
  index. An alternative edited in place (its option reworded) stays where it is, so its links still
  mean it. New alternatives are appended after the linked ones.

The check reads the decision files as raw JSON on both sides, so a base the shape rule would not
parse is compared as far as it can be read.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from typing import Any, Final

from agents_remember.memory_quality.knowledge_validator.registry import (
    Finding,
    ValidationContext,
    ValidationRule,
    register_rule,
)
from agents_remember.memory_quality.knowledge_validator.trees import KnowledgeTree
from agents_remember.models.knowledge_files.decisions import RECONSIDER_ON
from agents_remember.models.knowledge_files.documents import KNOWLEDGE_ROOT, RECORD_DIRECTORIES
from agents_remember.models.knowledge_files.records import DecisionRecord

_DECISION_DIRECTORY: Final = f"{KNOWLEDGE_ROOT}/{RECORD_DIRECTORIES['decision']}/"


def _options(document: Mapping[str, Any]) -> list[str | None]:
    alternatives = document.get("alternatives")
    return [
        one.get("option")
        if isinstance(one, Mapping) and isinstance(one.get("option"), str)
        else None
        for one in (alternatives if isinstance(alternatives, list) else ())
    ]


def _linked(document: Mapping[str, Any]) -> set[int]:
    links = document.get("links")
    return {
        one["alternative"]
        for one in (links if isinstance(links, list) else ())
        if isinstance(one, Mapping)
        and one.get("relation") == RECONSIDER_ON
        and isinstance(one.get("alternative"), int)
    }


def _decisions(tree: KnowledgeTree) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for path, data in tree.files.items():
        if not (path.startswith(_DECISION_DIRECTORY) and path.endswith(".json")):
            continue
        try:
            document = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            continue
        if isinstance(document, dict) and isinstance(document.get("id"), str):
            found[document["id"]] = document
    return found


def moved_linked_alternatives(
    before: Mapping[str, Any], after: Mapping[str, Any]
) -> list[tuple[str, int, int]]:
    """``(option, index before, index after)`` of each linked alternative whose index changed.

    ``before`` and ``after`` are one decision's documents on a base and the candidate. A linked
    index on either side is followed by its option to the other side; an option that is not unique
    on a side is not followed (it names no one alternative).
    """

    old, new = _options(before), _options(after)
    moved: set[tuple[str, int, int]] = set()

    def follow(options: list[str | None], index: int, other: list[str | None]) -> int | None:
        if index >= len(options) or options[index] is None:
            return None
        option = options[index]
        if options.count(option) != 1 or other.count(option) != 1:
            return None
        found = other.index(option)
        return None if found == index else found

    for index in _linked(before):
        target = follow(old, index, new)
        if target is not None:
            moved.add((str(old[index]), index, target))
    for index in _linked(after):
        source = follow(new, index, old)
        if source is not None:
            moved.add((str(new[index]), source, index))
    return sorted(moved, key=lambda one: (one[1], one[2]))


def check_linked_alternative_order(context: ValidationContext) -> Iterator[Finding]:
    bases = [_decisions(base) for base in context.bases]
    if not any(bases):
        return
    candidate = _decisions(context.candidate)
    for record_file in context.parsed.records:
        record = record_file.record
        after = candidate.get(record.id)
        if not isinstance(record, DecisionRecord) or after is None:
            continue
        for base in bases:
            before = base.get(record.id)
            if before is None:
                continue
            for option, old, new in moved_linked_alternatives(before, after):
                yield Finding(
                    record_file.path,
                    "alternatives",
                    f"alternative {option!r} moved from index {old} to {new}, and a "
                    "reconsider_on link addresses an alternative by its index "
                    f"(reconsider:{record.id}#<index>), so the move would silently retarget it; "
                    "keep linked alternatives at their index and append new ones after them",
                )


RECONSIDERATION_RULES = (
    ValidationRule(
        "R14.1-linked-alternative-order",
        "MIK-R14 rule 3 (L13 carried decision: link stability)",
        "an alternative a reconsider_on link addresses keeps its index",
        check_linked_alternative_order,
    ),
)

for _rule in RECONSIDERATION_RULES:
    register_rule(_rule)
