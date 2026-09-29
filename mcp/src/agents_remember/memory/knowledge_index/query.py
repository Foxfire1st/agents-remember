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
* :meth:`KnowledgeIndex.history_rows_about` -- subject -> the rows about it, across all owners.
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

    def record(self, record_id: str) -> Answer[Record | None]:
        return self._answer(self._record(record_id))

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
        rows = self._rows(
            "SELECT source, source_kind, relation, target_kind, target, detail, origin_path "
            "FROM ix_link WHERE target_kind = 'record' AND target = ? "
            "ORDER BY source, relation, origin_path",
            (record_id,),
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
    """Return ``path`` and every ancestor directory of it, nearest first (never the root ``""``)."""

    current = PurePosixPath(path.strip("/"))
    candidates = [current.as_posix()]
    candidates.extend(parent.as_posix() for parent in current.parents if parent.as_posix() != ".")
    return candidates
