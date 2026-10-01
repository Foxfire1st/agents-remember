"""Build one index file from one memory tree's files (MIK-R23 rules 1, 3 and Failure).

The builder reads a :class:`.tree.MemoryTreeSnapshot` and nothing else: no database, no working
directory, no second tree. It parses every knowledge file through the MIK-R21/R07 models, then
writes one row per recorded relationship into the ``ix_*`` tables and the reusable logical tables
(:mod:`.projection`).

**A file that fails its schema is reported, never guessed at.** Its path and the reason are written
to ``ix_problem`` and the index is marked ``partial``; the rest of the tree is indexed. The same
holds for a file at a location its format does not allow (a record whose file name names another
ID, a sidecar whose ``path`` does not match where it sits, a history file not named after its
owner) and for a second file claiming an ID already indexed: the index cannot answer for either
without choosing, so it names the file and says it is incomplete. Integrity beyond that -- whether
IDs resolve, markers match references, routes cover members -- is the validator's (MIK-R22); this
module derives nothing it cannot read off recorded fields.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple

import apsw

from agents_remember.memory.knowledge.connection import create_or_validate_schema
from agents_remember.memory.knowledge_index import projection
from agents_remember.memory.knowledge_index.schema import INDEX_DDL, INDEX_FORMAT
from agents_remember.memory.knowledge_index.tree import MemoryTreeSnapshot
from agents_remember.models.knowledge_files.documents import (
    KNOWLEDGE_ROOT,
    LAYOUT_MARKER_PATH,
    ONBOARDING_ROOT,
    RECORD_DIRECTORIES,
    file_sidecar_path,
    parse_document_text,
    parse_history_document,
    route_sidecar_path,
    split_record_filename,
)
from agents_remember.models.knowledge_files.history import (
    HistoryFile,
    InvariantRow,
    merged_leaf_history,
)
from agents_remember.models.knowledge_files.ids import RecordKind
from agents_remember.models.knowledge_files.records import (
    RECORD_MODELS,
    FamilyRecord,
    KnowledgeRecord,
)
from agents_remember.models.knowledge_files.shapes import (
    ROUTE_TARGET_PREFIX,
    Anchor,
    AnchorTarget,
    IdTarget,
    Link,
    Reference,
    RequirementReference,
    RequirementTarget,
)
from agents_remember.models.knowledge_files.sidecars import (
    FileSidecar,
    LayoutMarker,
    ProofEntry,
    RealizationEntry,
    RouteSidecar,
)

_DIRECTORY_KINDS: dict[str, RecordKind] = {
    directory: kind for kind, directory in RECORD_DIRECTORIES.items()
}
_HISTORY_DIRECTORY = "history"


@dataclass(frozen=True)
class IndexedRecord:
    path: str
    record: KnowledgeRecord


@dataclass(frozen=True)
class IndexedEntry:
    """One realization or proof entry, with the source path and sidecar it was recorded in."""

    path: str
    sidecar: str
    entry: RealizationEntry | ProofEntry


@dataclass
class ParsedTree:
    """The models one tree's files parse to, and every file that did not."""

    records: dict[str, IndexedRecord] = field(default_factory=dict)
    entries: dict[str, IndexedEntry] = field(default_factory=dict)
    file_sidecars: dict[str, FileSidecar] = field(default_factory=dict)
    route_sidecars: dict[str, RouteSidecar] = field(default_factory=dict)
    histories: dict[str, HistoryFile] = field(default_factory=dict)
    """Each owner's history: all of its files read as one (a reopened leaf's attempts, L37)."""
    history_files: dict[str, HistoryFile] = field(default_factory=dict)
    """Each history file, by path."""
    layout: LayoutMarker | None = None
    problems: list[tuple[str, str]] = field(default_factory=list)

    @property
    def state(self) -> str:
        return "partial" if self.problems else "complete"


@dataclass(frozen=True)
class BuildReport:
    """What one build indexed: the tree key, the state and every problem, by path."""

    key: str
    state: str
    converted: bool
    problems: tuple[tuple[str, str], ...]
    record_count: int
    entry_count: int
    history_row_count: int


def parse_tree(snapshot: MemoryTreeSnapshot) -> ParsedTree:
    """Parse every indexed file of ``snapshot``; a file that fails is a problem, not an error."""

    parsed = ParsedTree()
    for path in sorted(snapshot.files):
        try:
            _parse_file(parsed, path, snapshot.files[path].decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as error:
            parsed.problems.append((path, _first_line(error)))
    return parsed


def build_index(snapshot: MemoryTreeSnapshot, destination: Path) -> BuildReport:
    """Write the index of ``snapshot`` to ``destination`` (a new file) and report what it holds."""

    parsed = parse_tree(snapshot)
    connection = apsw.Connection(str(destination))
    try:
        connection.execute("PRAGMA journal_mode=OFF")
        connection.execute("PRAGMA synchronous=OFF")
        connection.execute("PRAGMA foreign_keys=ON")
        create_or_validate_schema(connection)
        connection.execute("BEGIN")
        for statement in INDEX_DDL:
            connection.execute(statement)
        _write_rows(connection, parsed)
        projection.project(connection, parsed)
        _write_meta(connection, snapshot, parsed)
        connection.execute("COMMIT")
    finally:
        connection.close()
    return BuildReport(
        key=snapshot.key,
        state=parsed.state,
        converted=parsed.layout is not None,
        problems=tuple(parsed.problems),
        record_count=len(parsed.records),
        entry_count=len(parsed.entries),
        history_row_count=sum(len(history.rows) for history in parsed.history_files.values()),
    )


# --------------------------------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------------------------------


def _parse_file(parsed: ParsedTree, path: str, text: str) -> None:
    if path == LAYOUT_MARKER_PATH:
        document = parse_document_text(text)
        if not isinstance(document, LayoutMarker):
            raise ValueError(f"{LAYOUT_MARKER_PATH} is not the layout marker")
        parsed.layout = document
        return
    parts = path.split("/")
    if parts[0] == KNOWLEDGE_ROOT:
        _parse_knowledge_file(parsed, path, parts, text)
    elif parts[0] == ONBOARDING_ROOT:
        _parse_onboarding_file(parsed, path, text)


def _parse_knowledge_file(parsed: ParsedTree, path: str, parts: list[str], text: str) -> None:
    if len(parts) != 3:
        raise ValueError("not a knowledge file location: knowledge/<kind-dir>/<file>.json")
    directory, filename = parts[1], parts[2]
    if directory == _HISTORY_DIRECTORY:
        history = parse_history_document(path, text)
        parsed.history_files[path] = history
        owned = [one for one in parsed.history_files.values() if one.owner_id == history.owner_id]
        merged = merged_leaf_history(owned)
        if merged is not None:
            parsed.histories[history.owner_id] = merged
        return
    kind = _DIRECTORY_KINDS.get(directory)
    if kind is None:
        raise ValueError(f"knowledge/{directory}/ is not a record directory")
    record_id, _slug, _extension = split_record_filename(filename)
    record = parse_document_text(text)
    if not isinstance(record, RECORD_MODELS[kind]):
        raise ValueError(f"a file under knowledge/{directory}/ holds an ar-{kind} record")
    if record.id != record_id:
        raise ValueError(f"the file name names {record_id} but the record is {record.id}")
    earlier = parsed.records.get(record.id)
    if earlier is not None:
        raise ValueError(f"{record.id} is already recorded in {earlier.path}")
    parsed.records[record.id] = IndexedRecord(path=path, record=record)


def _parse_onboarding_file(parsed: ParsedTree, path: str, text: str) -> None:
    document = parse_document_text(text)
    if isinstance(document, FileSidecar):
        if file_sidecar_path(document.path) != path:
            raise ValueError(
                f"the sidecar of {document.path} lives at {file_sidecar_path(document.path)}"
            )
        entries = [*document.realizes, *(document.proves or ())]
        for entry in entries:
            earlier = parsed.entries.get(entry.id)
            if earlier is not None:
                raise ValueError(f"entry {entry.id} is already recorded in {earlier.sidecar}")
        for entry in entries:
            parsed.entries[entry.id] = IndexedEntry(path=document.path, sidecar=path, entry=entry)
        parsed.file_sidecars[path] = document
    elif isinstance(document, RouteSidecar):
        if route_sidecar_path(document.path) != path:
            raise ValueError(
                f"the route sidecar of {document.path} lives at {route_sidecar_path(document.path)}"
            )
        parsed.route_sidecars[path] = document
    else:
        raise ValueError("an onboarding JSON file is a file or route sidecar")


def _first_line(error: BaseException) -> str:
    text = str(error).strip()
    return text if len(text) <= 2000 else f"{text[:2000]}…"


# --------------------------------------------------------------------------------------------------
# Rows
# --------------------------------------------------------------------------------------------------


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _write_rows(connection: apsw.Connection, parsed: ParsedTree) -> None:
    connection.executemany("INSERT INTO ix_problem (path, detail) VALUES (?, ?)", parsed.problems)
    for indexed in parsed.records.values():
        _write_record(connection, indexed)
    for indexed in parsed.entries.values():
        document = indexed.entry.to_document()
        document["anchor"] = {**document["anchor"], "path": indexed.path}
        connection.execute(
            "INSERT INTO ix_entry (id, kind, invariant, path, sidecar, document) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                indexed.entry.id,
                "realization" if isinstance(indexed.entry, RealizationEntry) else "proof",
                indexed.entry.invariant,
                indexed.path,
                indexed.sidecar,
                _json(document),
            ),
        )
    for path, sidecar in parsed.file_sidecars.items():
        _write_references(
            connection, _Owner(sidecar.path, "file", path), sidecar.references, own=sidecar.path
        )
    for path, sidecar in parsed.route_sidecars.items():
        _write_references(
            connection, _Owner(sidecar.path, "route", path), sidecar.references, own=None
        )
    for path, history in parsed.history_files.items():
        _write_history(connection, path, history)


def _write_record(connection: apsw.Connection, indexed: IndexedRecord) -> None:
    record = indexed.record
    connection.execute(
        "INSERT INTO ix_record (id, kind, path, revision, status, document) VALUES (?, ?, ?, ?, ?, ?)",
        (
            record.id,
            record.record_kind,
            indexed.path,
            getattr(record, "revision", None),
            str(getattr(record, "status", "")),
            _json(record.to_document()),
        ),
    )
    if isinstance(record, FamilyRecord):
        connection.executemany(
            "INSERT INTO ix_member (family, invariant) VALUES (?, ?)",
            [(record.id, member) for member in record.members],
        )
        connection.executemany(
            "INSERT INTO ix_route (family, route) VALUES (?, ?)",
            [(record.id, route) for route in record.routes],
        )
    owner = _Owner(record.id, record.record_kind, indexed.path)
    for member in getattr(record, "members", ()):
        _link(connection, owner, "member", ("record", member, {"id": member}))
    for superseded in getattr(record, "supersedes", ()):
        _link(connection, owner, "supersedes", ("record", superseded, {"id": superseded}))
    for link in getattr(record, "links", ()):
        _write_link(connection, owner, link)


def _write_link(connection: apsw.Connection, owner: _Owner, link: Link) -> None:
    target = link.target
    detail: dict[str, Any]
    endpoint: tuple[str, str, dict[str, Any]]
    if isinstance(target, Anchor):
        detail = target.to_document()
        endpoint = ("anchor", target.path or "", detail)
    elif isinstance(target, RequirementReference):
        detail = target.to_document()
        endpoint = ("requirement", _requirement_key(target), detail)
    elif target.startswith(ROUTE_TARGET_PREFIX):
        detail = {"route": target}
        endpoint = ("route", target[len(ROUTE_TARGET_PREFIX) :], detail)
    else:
        detail = {"id": target}
        endpoint = ("record", target, detail)
    if link.alternative is not None:
        endpoint[2]["alternative"] = link.alternative
    _link(connection, owner, link.relation, endpoint)


def _write_references(
    connection: apsw.Connection,
    owner: _Owner,
    references: dict[str, Reference],
    *,
    own: str | None,
) -> None:
    for number, reference in references.items():
        for target in reference.targets:
            endpoint = _reference_endpoint(target, own)
            if endpoint is not None:
                endpoint[2]["reference"] = number
                _link(connection, owner, "cites", endpoint)


def _reference_endpoint(target: object, own: str | None) -> tuple[str, str, dict[str, Any]] | None:
    if isinstance(target, IdTarget):
        return ("record", target.id, {"id": target.id, "kind": target.kind})
    if isinstance(target, AnchorTarget):
        anchor = target.anchor.to_document()
        path = target.anchor.path or own or ""
        return ("anchor", path, {"kind": target.kind, "anchor": {**anchor, "path": path}})
    if isinstance(target, RequirementTarget):
        return (
            "requirement",
            _requirement_key(target.requirement),
            target.requirement.to_document(),
        )
    return None


def _requirement_key(reference: RequirementReference) -> str:
    return f"{reference.task.repository}/{reference.task.path}#{reference.id}@{reference.version}"


class _Owner(NamedTuple):
    """The owner side of a recorded relationship: who records it, its kind, and in which file."""

    source: str
    kind: str
    path: str


def _link(
    connection: apsw.Connection,
    owner: _Owner,
    relation: str,
    endpoint: tuple[str, str, dict[str, Any]],
) -> None:
    target_kind, target, detail = endpoint
    connection.execute(
        "INSERT INTO ix_link (source, source_kind, relation, target_kind, target, detail, origin_path) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (owner.source, owner.kind, relation, target_kind, target, _json(detail), owner.path),
    )


def _write_history(connection: apsw.Connection, path: str, history: HistoryFile) -> None:
    for row in history.rows:
        connection.execute(
            "INSERT INTO ix_history_row (id, owner, owner_kind, closed, path, subject, "
            "disposition, document) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                row.id,
                history.owner_id,
                history.owner_kind,
                int(history.closed),
                path,
                row.subject,
                row.disposition,
                _json(row.to_document()),
            ),
        )
        if isinstance(row, InvariantRow):
            for because in row.because or ():
                endpoint = (
                    ("record", because, {"id": because})
                    if isinstance(because, str)
                    else ("requirement", _requirement_key(because), because.to_document())
                )
                _link(connection, _Owner(row.id, "history_row", path), "because", endpoint)


def _write_meta(
    connection: apsw.Connection, snapshot: MemoryTreeSnapshot, parsed: ParsedTree
) -> None:
    rows: Iterable[tuple[str, str]] = (
        ("format", INDEX_FORMAT),
        ("key", snapshot.key),
        ("source", snapshot.source),
        ("location", snapshot.location),
        ("state", parsed.state),
        ("converted", "true" if parsed.layout is not None else "false"),
        ("builtAt", datetime.now(UTC).isoformat()),
    )
    connection.executemany("INSERT INTO ix_meta (name, value) VALUES (?, ?)", rows)
