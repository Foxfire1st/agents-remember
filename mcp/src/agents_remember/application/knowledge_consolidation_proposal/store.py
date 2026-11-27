"""The facts a consolidation proposal is computed from, read once from one tree's index.

A proposal joins three relationships of the memory: which live rule belongs to which live family,
in which files and folders each rule is realized, and which live rules belong to no family. The
index answers each of them with one listing (the family and invariant records, and every realization
entry), so the store is filled by a constant number of reads whatever the number of families; every
proposal after that is a lookup in the maps built here, never another read of the store per family.

A folder is the directory that holds a realization entry's source file (``.`` for a file at the
repository root), used by the folder seed. Loose rules attach to a proposed family by exact
realization file (``entry.path``), so only unaffiliated rules realized in the same files as a
family's members are attached. Retired records take no part: a retired rule is proposed nowhere,
and a rule whose only family is retired is a rule in no family.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from agents_remember.memory.knowledge_index.projection import RETIRED_STATUS
from agents_remember.memory.knowledge_index.query import KnowledgeIndex, Record


@dataclass(frozen=True)
class FamilyFacts:
    """A live family as a proposal names it: its title, members and first stamp."""

    id: str
    title: str
    members: tuple[str, ...]
    stamped_at: str | None
    """The ``at`` of the family record's current writer stamp, when it has one."""


@dataclass
class ProposalStore:
    """The live families and rules of one tree with the files and folders their rules realize."""

    families: dict[str, FamilyFacts] = field(default_factory=dict)
    rules: set[str] = field(default_factory=set)
    rule_origins: dict[str, str] = field(default_factory=dict)
    """Each live rule's creating leaf (``origin.leaf``), where the record names one."""
    rule_families: dict[str, set[str]] = field(default_factory=dict)
    rule_files: dict[str, set[str]] = field(default_factory=dict)
    rule_folders: dict[str, set[str]] = field(default_factory=dict)
    loose_by_file: dict[str, set[str]] = field(default_factory=dict)
    """The live rules in no family, by each file they are realized in."""

    def loose_rules(self) -> set[str]:
        """The live rules that no live family holds."""

        return {rule for rule in self.rules if rule not in self.rule_families}

    def member_files(self, family: str) -> set[str]:
        """The files where any member of ``family`` is realized."""

        files: set[str] = set()
        for member in self.families[family].members:
            files |= self.rule_files.get(member, set())
        return files

    def member_folders(self, family: str) -> set[str]:
        """The folders where any member of ``family`` is realized."""

        folders: set[str] = set()
        for member in self.families[family].members:
            folders |= self.rule_folders.get(member, set())
        return folders

    def attached_rules(self, family: str) -> list[str]:
        """The rules in no family realized in a file where a member of ``family`` is realized."""

        attached: set[str] = set()
        for file_path in self.member_files(family):
            attached |= self.loose_by_file.get(file_path, set())
        return sorted(attached)


def folder_of(path: str) -> str:
    """The directory that holds ``path``: ``.`` for a file at the repository root."""

    return PurePosixPath(path.strip("/")).parent.as_posix()


def load_store(index: KnowledgeIndex) -> ProposalStore:
    """Read the families, rules and realization files/folders of the index's tree, once each."""

    store = ProposalStore()
    for record in index.records_of_kind("invariant").value:
        if record.status != RETIRED_STATUS:
            store.rules.add(record.id)
            leaf = _origin_leaf(record)
            if leaf is not None:
                store.rule_origins[record.id] = leaf
    for record in index.records_of_kind("family").value:
        if record.status != RETIRED_STATUS:
            store.families[record.id] = _family(record, store.rules)
    for family in store.families.values():
        for member in family.members:
            store.rule_families.setdefault(member, set()).add(family.id)
    _place_realizations(store, index)
    return store


def _place_realizations(store: ProposalStore, index: KnowledgeIndex) -> None:
    """Fill each live rule's files and folders and the loose rules per file in one entry pass."""

    realized_files: defaultdict[str, set[str]] = defaultdict(set)
    realized_folders: defaultdict[str, set[str]] = defaultdict(set)
    for entry in index.entries_under(".").value.realizations:
        if entry.invariant in store.rules:
            normalized = PurePosixPath(entry.path.strip("/")).as_posix()
            realized_files[entry.invariant].add(normalized)
            realized_folders[entry.invariant].add(folder_of(normalized))
    store.rule_files = dict(realized_files)
    store.rule_folders = dict(realized_folders)
    loose: defaultdict[str, set[str]] = defaultdict(set)
    for rule in store.loose_rules():
        for file_path in realized_files.get(rule, set()):
            loose[file_path].add(rule)
    store.loose_by_file = dict(loose)


def _origin_leaf(record: Record) -> str | None:
    origin = record.document.get("origin")
    leaf = origin.get("leaf") if isinstance(origin, dict) else None
    return leaf if isinstance(leaf, str) else None


def _family(record: Record, live_rules: set[str]) -> FamilyFacts:
    document = record.document
    members = document.get("members")
    title = document.get("title")
    writer = document.get("writer")
    at = writer.get("at") if isinstance(writer, dict) else None
    return FamilyFacts(
        id=record.id,
        title=title if isinstance(title, str) else "",
        members=tuple(
            member
            for member in (members if isinstance(members, list) else [])
            if isinstance(member, str) and member in live_rules
        ),
        stamped_at=at if isinstance(at, str) else None,
    )
