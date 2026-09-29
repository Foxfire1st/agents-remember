"""What ``read_ar_files`` returns beside a card, by the memory tree's format (MIK-R24 rules 5, 9).

* A **converted** tree (``knowledge/layout.json``) is read in the text format: the card's prose, its
  sidecar, and its references resolved -- each ``[n]`` with its note and targets, an anchor target
  with its path filled in, and an ID target with the record it names.
* An **unconverted** tree is returned as it is, marked ``legacy-format``: nothing is resolved from
  sidecars and no knowledge section is returned, so the old format is never misread as the new one.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final, Literal

from agents_remember.models.knowledge_files.canonical import parse_json
from agents_remember.models.knowledge_files.documents import (
    KNOWLEDGE_ROOT,
    LAYOUT_MARKER_PATH,
    RECORD_DIRECTORIES,
)

MemoryFormat = Literal["text/v2", "legacy-format"]
TEXT_FORMAT: Final = "text/v2"
LEGACY_FORMAT: Final = "legacy-format"
_ID_TARGET_KINDS: Final = frozenset({"invariant", "family", "decision", "incident", "record"})
_RECORD_SUMMARY_FIELDS: Final = ("statement", "title", "guarantee", "context", "status", "revision")


def memory_format(memory_root: Path) -> MemoryFormat:
    """``text/v2`` exactly when the memory tree holds the layout marker."""

    return TEXT_FORMAT if (memory_root / LAYOUT_MARKER_PATH).is_file() else LEGACY_FORMAT


def legacy_published_intent(memory_root: Path) -> dict[str, Any]:
    """The knowledge section of an unconverted tree: none, and why (rule 9)."""

    return {
        "state": LEGACY_FORMAT,
        "memoryRoot": memory_root.as_posix(),
        "detail": (
            "this memory tree is unconverted (no knowledge/layout.json); its onboarding is returned "
            "as legacy-format and no knowledge section is read from it. A line that descends from "
            "a converted official line converts through the crossing sync (worktree_sync); an "
            "unconverted official line, or a repository without one, converts by running "
            "agents-remember knowledge-convert on that line and committing it through its normal "
            "route"
        ),
    }


def _record_summary(memory_root: Path, identifier: str) -> dict[str, Any] | None:
    knowledge = memory_root / KNOWLEDGE_ROOT
    for directory in RECORD_DIRECTORIES.values():
        for path in sorted((knowledge / directory).glob(f"{identifier}-*.json")):
            try:
                document = parse_json(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return {"path": path.relative_to(memory_root).as_posix(), "state": "unreadable"}
            summary = {key: document[key] for key in _RECORD_SUMMARY_FIELDS if key in document}
            return {"path": path.relative_to(memory_root).as_posix(), **summary}
    return None


def converted_card_parts(memory_root: Path, onboarding_rel: str) -> dict[str, Any]:
    """The sidecar and resolved references of ``onboarding/<onboarding_rel>`` in a converted tree."""

    sidecar_path = memory_root / "onboarding" / f"{onboarding_rel}.json"
    if not sidecar_path.is_file():
        return {"format": TEXT_FORMAT, "references": []}
    try:
        sidecar = parse_json(sidecar_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"format": TEXT_FORMAT, "references": []}
    own = sidecar.get("path")
    resolved: list[dict[str, Any]] = []
    for number, reference in sorted(
        sidecar.get("references", {}).items(), key=lambda item: int(item[0])
    ):
        targets: list[dict[str, Any]] = []
        for target in reference.get("targets", []):
            if target.get("kind") in {"code", "test"}:
                anchor = target["anchor"]
                targets.append({**target, "anchor": {"path": anchor.get("path", own), **anchor}})
            elif target.get("kind") in _ID_TARGET_KINDS:
                targets.append({**target, "record": _record_summary(memory_root, target["id"])})
            else:
                targets.append(target)
        entry: dict[str, Any] = {"number": int(number), "targets": targets}
        if "note" in reference:
            entry["note"] = reference["note"]
        resolved.append(entry)
    return {"format": TEXT_FORMAT, "sidecar": sidecar, "references": resolved}
