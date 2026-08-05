"""Group declined citation repairs into one work order per document.

Each item carries its anchor, range, source, observed replacement evidence, every exact
tree location, and the next action. Grouping by document lets one curator own each write
without overlapping another curator's file.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

CURATOR_TIER = 2
DEVELOPER_TIER = 3


@dataclass(frozen=True)
class Item:
    """One declined citation and the edit that clears it."""

    document: str
    line: int
    kind: str
    code: str
    tier: int
    action: str
    message: str
    subject: str = ""
    anchor: str | None = None
    source: str | None = None
    parser_dependent: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "line": self.line,
            "kind": self.kind,
            "code": self.code,
            "tier": self.tier,
            "subject": self.subject,
            "anchor": self.anchor,
            "source": self.source,
            "parserDependent": self.parser_dependent,
            "action": self.action,
            "message": self.message,
        }


def orders(items: list[Item], subjects: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """One entry per document, in tree order, each holding its own items in line order.

    ``subjects`` maps a document to the source path its card declares, which is the file a
    curator agent opens first. A document with no declared path is still dispatched; the
    agent reads the cited files instead.
    """
    by_document: dict[str, list[Item]] = {}
    for item in items:
        by_document.setdefault(item.document, []).append(item)
    return [
        {
            "document": document,
            "cardPath": (subjects or {}).get(document),
            "itemCount": len(found),
            "codes": sorted({one.code for one in found}),
            "items": [one.to_dict() for one in sorted(found, key=lambda one: (one.line, one.code))],
        }
        for document, found in sorted(by_document.items())
    ]


def counted(items: list[Item]) -> dict[str, int]:
    """Every decline reason with its count, worst first -- the complete list (L6-R15)."""
    tally: dict[str, int] = {}
    for item in items:
        tally[item.code] = tally.get(item.code, 0) + 1
    return dict(sorted(tally.items(), key=lambda one: (-one[1], one[0])))
