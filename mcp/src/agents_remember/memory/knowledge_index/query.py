"""The answers one index gives for one tree (MIK-R23 rule 3).

A :class:`KnowledgeIndex` is one opened index file. Every lookup is a ``SELECT`` on a read-only
connection, and every answer carries the index's :class:`IndexState`: the tree key it was built
for, ``complete`` or ``partial``, and the files that failed. An answer from a partial index says so
in its own value, so a caller cannot present it as complete without discarding that field.

The lookups:

* :meth:`KnowledgeIndex.entries_at_path` -- path -> realization and proof entries;
* :meth:`KnowledgeIndex.invariant` -- invariant -> realizations (with paths), proofs, families,
  the records linking to it (decisions, incidents, facets) and the history rows about it;
* :meth:`KnowledgeIndex.family` -- family -> members and routes;
* :meth:`KnowledgeIndex.families_governing` -- route or directory -> families whose routes are that
  directory or one of its ancestors;
* :meth:`KnowledgeIndex.incoming_links` -- any record -> its incoming links;
* :meth:`KnowledgeIndex.history_rows_of` -- leaf, wave or crossing -> its history rows;
* :meth:`KnowledgeIndex.history_rows_about` -- subject -> the rows about it, across all owners;
* :meth:`KnowledgeIndex.proofs_of` -- invariants -> their proof entries (MIK-R28 rule 4);
* :meth:`KnowledgeIndex.invariants_without_proof` -- the live invariants no proof names (MIK-R28
  rule 5);
* :meth:`KnowledgeIndex.record_ids` -- every record ID of one kind (MIK-R25's per-side currentness);
* :meth:`KnowledgeIndex.entries_under`, :meth:`KnowledgeIndex.entries_in_directory`,
  :meth:`KnowledgeIndex.live_entry_paths_under`, :meth:`KnowledgeIndex.links_to_path` and
  :meth:`KnowledgeIndex.records_of_kind` -- the reader's directory, path and record lookups
  (MIK-R29).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal

import apsw

from agents_remember.memory.knowledge.connection import open_read_only_database
from agents_remember.memory.knowledge_index.projection import INDEX_REPOSITORY_ID
from agents_remember.memory.knowledge_index.schema import INDEX_FORMAT
from agents_remember.models.knowledge_files.sidecars import ROOT_ROUTE_PATH

IndexCompleteness = Literal["complete", "partial"]


class IndexMismatchError(ValueError):
    """An index file is not the index of the tree key it was opened for, or not this format."""


@dataclass(frozen=True)
class IndexState:
    """What an answer was built from: the tree key, completeness, and the files that failed."""

    key: str
    state: IndexCompleteness
    converted: bool
    problems: tuple[tuple[str, str], ...]

    @property
    def complete(self) -> bool:
        return self.state == "complete"


@dataclass(frozen=True)
class Answer[T]:
    """One lookup's value together with the state of the index that produced it."""

    value: T
    index: IndexState


@dataclass(frozen=True)
class Entry:
    """A realization or proof entry: its sidecar document, with the anchor's path filled in."""

    id: str
    kind: Literal["realization", "proof"]
    invariant: str
    path: str
    sidecar: str
    document: dict[str, Any]


@dataclass(frozen=True)
class EntriesAtPath:
    realizations: tuple[Entry, ...]
    proofs: tuple[Entry, ...]


@dataclass(frozen=True)
class Link:
    """One recorded relationship pointing at the looked-up subject, from its owner's side."""

    source: str
    source_kind: str
    relation: str
    target_kind: str
    target: str
    detail: dict[str, Any]
    origin_path: str


@dataclass(frozen=True)
class HistoryRow:
    id: str
    owner: str
    owner_kind: str
    closed: bool
    path: str
    subject: str
    disposition: str
    document: dict[str, Any]


@dataclass(frozen=True)
class Record:
    id: str
    kind: str
    path: str
    revision: int | None
    status: str
    document: dict[str, Any]


@dataclass(frozen=True)
class InvariantKnowledge:
    """Everything the tree records about one invariant, from every owner's side."""

    record: Record | None
    realizations: tuple[Entry, ...]
    proofs: tuple[Entry, ...]
    families: tuple[str, ...]
    linked_from: tuple[Link, ...]
    history: tuple[HistoryRow, ...]


@dataclass(frozen=True)
class FamilyKnowledge:
    record: Record | None
    members: tuple[str, ...]
    routes: tuple[str, ...]


class KnowledgeIndex:
    """One opened index file, bound to the tree key it was built for."""

    def __init__(self, path: Path, *, expected_key: str | None = None) -> None:
        self.path = path
        self._connection = open_read_only_database(path)
        try:
            meta = dict(self._rows("SELECT name, value FROM ix_meta"))
        except apsw.Error as error:
            self._connection.close()
            raise IndexMismatchError(f"{path} is not a knowledge index: {error}") from error
        if meta.get("format") != INDEX_FORMAT:
            self._connection.close()
            raise IndexMismatchError(f"{path} is not an {INDEX_FORMAT} file")
        if expected_key is not None and meta.get("key") != expected_key:
            self._connection.close()
            raise IndexMismatchError(
                f"{path} was built for tree {meta.get('key')}, not for {expected_key}"
            )
        problems = tuple(
            (str(path_), str(detail))
            for path_, detail in self._rows("SELECT path, detail FROM ix_problem ORDER BY path")
        )
        self.state = IndexState(
            key=str(meta["key"]),
            state="partial" if meta.get("state") == "partial" else "complete",
            converted=meta.get("converted") == "true",
            problems=problems,
        )

    # -- lifecycle -------------------------------------------------------------------------------

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> KnowledgeIndex:
        return self

    def __exit__(self, *exception: object) -> None:
        self.close()

    @property
    def database_path(self) -> Path:
        """The file the reused read code opens: the index is a dataset of the store's schema."""

        return self.path

    @property
    def repository_id(self) -> str:
        return INDEX_REPOSITORY_ID

    # -- lookups ---------------------------------------------------------------------------------

    def entries_at_path(self, path: str) -> Answer[EntriesAtPath]:
        entries = self._entries("path = ?", (path,))
        return self._answer(
            EntriesAtPath(
                realizations=tuple(e for e in entries if e.kind == "realization"),
                proofs=tuple(e for e in entries if e.kind == "proof"),
            )
        )

    def invariant(self, invariant_id: str) -> Answer[InvariantKnowledge]:
        entries = self._entries("invariant = ?", (invariant_id,))
        families = tuple(
            str(row[0])
            for row in self._rows(
                "SELECT family FROM ix_member WHERE invariant = ? ORDER BY family", (invariant_id,)
            )
        )
        linked = tuple(link for link in self._incoming(invariant_id) if link.relation != "member")
        return self._answer(
            InvariantKnowledge(
                record=self._record(invariant_id),
                realizations=tuple(e for e in entries if e.kind == "realization"),
                proofs=tuple(e for e in entries if e.kind == "proof"),
                families=families,
                linked_from=linked,
                history=self._history("subject = ?", (invariant_id,)),
            )
        )

    def family(self, family_id: str) -> Answer[FamilyKnowledge]:
        members = tuple(
            str(row[0])
            for row in self._rows(
                "SELECT invariant FROM ix_member WHERE family = ? ORDER BY invariant", (family_id,)
            )
        )
        routes = tuple(
            str(row[0])
            for row in self._rows(
                "SELECT route FROM ix_route WHERE family = ? ORDER BY route", (family_id,)
            )
        )
        return self._answer(
            FamilyKnowledge(record=self._record(family_id), members=members, routes=routes)
        )

    def families_governing(self, path: str) -> Answer[tuple[tuple[str, str], ...]]:
        """Return ``(family, route)`` for each family route that is ``path`` or an ancestor."""

        candidates = _self_and_ancestors(path)
        placeholders = ", ".join("?" for _ in candidates)
        rows = self._rows(
            f"SELECT family, route FROM ix_route WHERE route IN ({placeholders}) "
            "ORDER BY family, route",
            tuple(candidates),
        )
        return self._answer(tuple((str(family), str(route)) for family, route in rows))

    def incoming_links(self, record_id: str) -> Answer[tuple[Link, ...]]:
        return self._answer(self._incoming(record_id))

    def history_rows_of(self, owner_id: str) -> Answer[tuple[HistoryRow, ...]]:
        return self._answer(self._history("owner = ?", (owner_id,)))

    def history_rows_about(self, subject: str) -> Answer[tuple[HistoryRow, ...]]:
        return self._answer(self._history("subject = ?", (subject,)))

    def proofs_of(self, invariant_ids: Sequence[str]) -> Answer[tuple[Entry, ...]]:
        """The proof entries of ``invariant_ids`` (MIK-R28 rule 4), by path then entry ID."""

        if not invariant_ids:
            return self._answer(())
        placeholders = ", ".join("?" for _ in invariant_ids)
        return self._answer(
            self._entries(f"kind = 'proof' AND invariant IN ({placeholders})", tuple(invariant_ids))
        )

    def invariants_without_proof(self) -> Answer[tuple[Record, ...]]:
        """Every live invariant no proof entry names (MIK-R28 rule 5), by ID.

        A retired invariant is not live and is not listed. The list is information, not a gate: the
        admission rule accepts criteria other than a proving test (MIK-R27).
        """

        rows = self._rows(
            "SELECT id FROM ix_record WHERE kind = 'invariant' AND status != 'retired' "
            "AND id NOT IN (SELECT invariant FROM ix_entry WHERE kind = 'proof') ORDER BY id"
        )
        records = (self._record(str(row[0])) for row in rows)
        return self._answer(tuple(record for record in records if record is not None))

    def entries_under(self, directory: str) -> Answer[EntriesAtPath]:
        """Entries recorded at ``directory`` or any path below it (MIK-R29's directory listing).

        The root route ``.`` (or an empty directory) holds every entry. A path prefix never matches
        a sibling that merely shares its spelling: ``dash`` holds ``dash/x``, not ``dashboard/x``.
        """

        where, parameters = _under(directory)
        return self._answer(_split(self._entries(where, parameters)))

    def entries_in_directory(self, directory: str) -> Answer[EntriesAtPath]:
        """Entries recorded at files directly in ``directory``, not in its subdirectories."""

        where, parameters = _under(directory)
        prefix = _prefix(directory)
        entries = self._entries(
            f"({where}) AND instr(substr(path, ?), '/') = 0", (*parameters, len(prefix) + 1)
        )
        return self._answer(_split(entries))

    def live_entry_paths_under(self, directory: str) -> Answer[tuple[str, ...]]:
        """The source path of every entry at or under ``directory`` whose invariant is a live
        record (not retired, not missing), one per entry, by path: the reader's counts."""

        where, parameters = _under(directory, column="e.path")
        rows = self._rows(
            "SELECT e.path FROM ix_entry e JOIN ix_record r ON r.id = e.invariant "
            f"WHERE r.status != 'retired' AND ({where}) "
            "ORDER BY e.path, e.id",
            parameters,
        )
        return self._answer(tuple(str(row[0]) for row in rows))

    def links_to_path(self, path: str) -> Answer[tuple[Link, ...]]:
        """Every relationship whose target is the code anchor or route at ``path`` (MIK-R29)."""

        return self._answer(
            self._links("target_kind IN ('anchor', 'route') AND target = ?", (path.strip("/"),))
        )

    def records_of_kind(self, kind: str) -> Answer[tuple[Record, ...]]:
        """Every record of one kind, by ID (MIK-R29's record list and derived decision status)."""

        records = (self._record(one) for one in self.record_ids(kind).value)
        return self._answer(tuple(record for record in records if record is not None))

    def record(self, record_id: str) -> Answer[Record | None]:
        return self._answer(self._record(record_id))

    def record_ids(self, kind: str) -> Answer[tuple[str, ...]]:
        """Every record ID of one kind in the tree, sorted (the reviewer's per-side currentness)."""

        rows = self._rows("SELECT id FROM ix_record WHERE kind = ? ORDER BY id", (kind,))
        return self._answer(tuple(str(row[0]) for row in rows))

    def text_id(self, projected_uuid: str) -> str | None:
        """Return the text ID a projected UUID stands for, or ``None`` (rule 6's reverse map)."""

        row = next(
            iter(self._rows("SELECT id FROM ix_uuid WHERE uuid = ?", (projected_uuid,))), None
        )
        return None if row is None else str(row[0])

    # -- internals -------------------------------------------------------------------------------

    def _answer[T](self, value: T) -> Answer[T]:
        return Answer(value=value, index=self.state)

    def _rows(self, statement: str, parameters: Sequence[Any] = ()) -> list[tuple[Any, ...]]:
        return [tuple(row) for row in self._connection.execute(statement, tuple(parameters))]

    def _entries(self, where: str, parameters: tuple[Any, ...]) -> tuple[Entry, ...]:
        rows = self._rows(
            f"SELECT id, kind, invariant, path, sidecar, document FROM ix_entry WHERE {where} "
            "ORDER BY path, id",
            parameters,
        )
        return tuple(
            Entry(
                id=str(row[0]),
                kind="proof" if row[1] == "proof" else "realization",
                invariant=str(row[2]),
                path=str(row[3]),
                sidecar=str(row[4]),
                document=json.loads(row[5]),
            )
            for row in rows
        )

    def _incoming(self, record_id: str) -> tuple[Link, ...]:
        return self._links("target_kind = 'record' AND target = ?", (record_id,))

    def _links(self, where: str, parameters: tuple[Any, ...]) -> tuple[Link, ...]:
        rows = self._rows(
            "SELECT source, source_kind, relation, target_kind, target, detail, origin_path "
            f"FROM ix_link WHERE {where} "
            "ORDER BY source, relation, origin_path",
            parameters,
        )
        return tuple(
            Link(
                source=str(row[0]),
                source_kind=str(row[1]),
                relation=str(row[2]),
                target_kind=str(row[3]),
                target=str(row[4]),
                detail=json.loads(row[5]),
                origin_path=str(row[6]),
            )
            for row in rows
        )

    def _history(self, where: str, parameters: tuple[Any, ...]) -> tuple[HistoryRow, ...]:
        rows = self._rows(
            "SELECT id, owner, owner_kind, closed, path, subject, disposition, document "
            f"FROM ix_history_row WHERE {where} ORDER BY owner, subject, id",
            parameters,
        )
        return tuple(
            HistoryRow(
                id=str(row[0]),
                owner=str(row[1]),
                owner_kind=str(row[2]),
                closed=bool(row[3]),
                path=str(row[4]),
                subject=str(row[5]),
                disposition=str(row[6]),
                document=json.loads(row[7]),
            )
            for row in rows
        )

    def _record(self, record_id: str) -> Record | None:
        row = next(
            iter(
                self._rows(
                    "SELECT id, kind, path, revision, status, document FROM ix_record WHERE id = ?",
                    (record_id,),
                )
            ),
            None,
        )
        if row is None:
            return None
        return Record(
            id=str(row[0]),
            kind=str(row[1]),
            path=str(row[2]),
            revision=None if row[3] is None else int(row[3]),
            status=str(row[4]),
            document=json.loads(row[5]),
        )


def _self_and_ancestors(path: str) -> list[str]:
    """Return ``path`` and every ancestor directory of it, nearest first, ending with the root
    route ``.`` (MIK-R04: a family routed at ``.`` governs every path)."""

    current = PurePosixPath(path.strip("/"))
    candidates = [current.as_posix()]
    candidates.extend(parent.as_posix() for parent in current.parents if parent.as_posix() != ".")
    if candidates[-1] != ROOT_ROUTE_PATH:
        candidates.append(ROOT_ROUTE_PATH)
    return candidates


def _prefix(directory: str) -> str:
    """The spelling every path below ``directory`` starts with (empty for the root)."""

    stripped = directory.strip("/")
    return "" if stripped in ("", ROOT_ROUTE_PATH) else f"{stripped}/"


def _under(directory: str, *, column: str = "path") -> tuple[str, tuple[Any, ...]]:
    """The ``ix_entry`` condition for "``column`` is at or under ``directory``", with parameters."""

    prefix = _prefix(directory)
    if not prefix:
        return "1 = 1", ()
    return f"{column} = ? OR substr({column}, 1, ?) = ?", (prefix[:-1], len(prefix), prefix)


def _split(entries: Sequence[Entry]) -> EntriesAtPath:
    return EntriesAtPath(
        realizations=tuple(e for e in entries if e.kind == "realization"),
        proofs=tuple(e for e in entries if e.kind == "proof"),
    )
