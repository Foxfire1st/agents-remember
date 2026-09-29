"""The knowledge sides K_B and K_C, read through the derived index's parser (MIK-R08 definition 7).

Each side is one memory tree parsed by :func:`agents_remember.memory.knowledge_index.parse_tree`,
the MIK-R23 index builder's own parse of the MIK-R21/R07 models: records by ID, realization and proof
entries by ID with the source path whose sidecar holds them, and history files by owner. The worklist
compares the two sides over those parsed relationships and over the exact bytes of each record file,
so "its record file differs" means the file's bytes or its location differ, and nothing is inferred
from a name.

A side whose files do not all parse cannot be compared: the index would be ``partial``, and a
worklist built over it could miss an entry. That side is **unreadable input** (MIK-R08 rule 4) and
the run is ``incomplete``, naming every file that failed.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import cached_property

from agents_remember.memory.knowledge_index import (
    MemoryTreeSnapshot,
    is_indexed_path,
    parse_tree,
)
from agents_remember.memory.knowledge_index.build import IndexedEntry, ParsedTree
from agents_remember.memory_quality.knowledge_validator.trees import KnowledgeTree
from agents_remember.models.knowledge_files.history import HistoryFile
from agents_remember.models.knowledge_files.records import FamilyRecord, InvariantRecord
from agents_remember.models.knowledge_files.shapes import Anchor

__all__ = ["KnowledgeSide", "KnowledgeSideUnreadable", "anchor_document"]


class KnowledgeSideUnreadable(ValueError):
    """A knowledge side has files that do not parse; ``problems`` names each one."""

    def __init__(self, label: str, problems: tuple[tuple[str, str], ...]) -> None:
        self.label = label
        self.problems = problems
        named = "; ".join(f"{path}: {reason}" for path, reason in problems[:5])
        more = f" (and {len(problems) - 5} more)" if len(problems) > 5 else ""
        super().__init__(f"{label} has files that do not parse: {named}{more}")


def anchor_document(anchor: Anchor, path: str) -> dict[str, object]:
    """An entry's anchor as a document, with the path of the sidecar's source file filled in."""

    return {**anchor.to_document(), "path": path}


@dataclass(frozen=True)
class KnowledgeSide:
    """One parsed memory tree: its key, its records, entries and history files."""

    label: str
    key: str
    converted: bool
    parsed: ParsedTree
    files: Mapping[str, bytes]

    @classmethod
    def from_snapshot(cls, label: str, snapshot: MemoryTreeSnapshot) -> KnowledgeSide:
        parsed = parse_tree(snapshot)
        if parsed.problems:
            raise KnowledgeSideUnreadable(label, tuple(sorted(parsed.problems)))
        return cls(
            label=label,
            key=snapshot.key,
            converted=parsed.layout is not None,
            parsed=parsed,
            files=snapshot.files,
        )

    @classmethod
    def from_tree(cls, label: str, key: str, tree: KnowledgeTree) -> KnowledgeSide:
        """A side given as the validator's tree (the converted base of MIK-R24 rule 7)."""

        files = {path: data for path, data in tree.files.items() if is_indexed_path(path)}
        snapshot = MemoryTreeSnapshot(key=key, source="git", location=tree.label, files=files)
        return cls.from_snapshot(label, snapshot)

    @cached_property
    def invariants(self) -> dict[str, InvariantRecord]:
        return {
            record_id: indexed.record
            for record_id, indexed in self.parsed.records.items()
            if isinstance(indexed.record, InvariantRecord)
        }

    @cached_property
    def families(self) -> dict[str, FamilyRecord]:
        return {
            record_id: indexed.record
            for record_id, indexed in self.parsed.records.items()
            if isinstance(indexed.record, FamilyRecord)
        }

    @property
    def entries(self) -> Mapping[str, IndexedEntry]:
        return self.parsed.entries

    @cached_property
    def entries_by_invariant(self) -> dict[str, tuple[str, ...]]:
        grouped: dict[str, list[str]] = {}
        for entry_id, indexed in self.parsed.entries.items():
            grouped.setdefault(indexed.entry.invariant, []).append(entry_id)
        return {invariant: tuple(sorted(ids)) for invariant, ids in grouped.items()}

    @cached_property
    def entries_by_path(self) -> dict[str, tuple[str, ...]]:
        grouped: dict[str, list[str]] = {}
        for entry_id, indexed in self.parsed.entries.items():
            grouped.setdefault(indexed.path, []).append(entry_id)
        return {path: tuple(sorted(ids)) for path, ids in grouped.items()}

    @cached_property
    def families_of(self) -> dict[str, tuple[str, ...]]:
        """Each invariant's families on this side, by the families' ``members``."""

        grouped: dict[str, list[str]] = {}
        for family_id, family in self.families.items():
            for member in family.members:
                grouped.setdefault(member, []).append(family_id)
        return {invariant: tuple(sorted(ids)) for invariant, ids in grouped.items()}

    def record_file(self, record_id: str) -> tuple[str, bytes] | None:
        """The record's file: its location and exact bytes, or ``None`` when the side has none."""

        indexed = self.parsed.records.get(record_id)
        if indexed is None:
            return None
        return indexed.path, self.files[indexed.path]

    def revision(self, record_id: str) -> int | None:
        record = self.invariants.get(record_id) or self.families.get(record_id)
        return None if record is None else record.revision

    def history(self, owner_id: str) -> HistoryFile | None:
        return self.parsed.histories.get(owner_id)

    @cached_property
    def linked_from(self) -> dict[str, tuple[str, ...]]:
        """Each record ID another record's ``links`` name by ID (context, MIK-R08 rule 5)."""

        grouped: dict[str, set[str]] = {}
        for record_id, indexed in self.parsed.records.items():
            for link in getattr(indexed.record, "links", ()):
                if isinstance(link.target, str):
                    grouped.setdefault(link.target, set()).add(record_id)
        return {target: tuple(sorted(ids)) for target, ids in grouped.items()}
