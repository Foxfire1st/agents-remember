"""The memory tree one writer operation reads and edits, and the base it compares against.

The candidate is the memory working tree K_C: every file under ``knowledge/`` and ``onboarding/``,
read once as bytes. The operation edits parsed copies of the JSON files it touches; nothing reaches
the disk until :meth:`MemoryState.write` is called with the validated result, so a refused operation
leaves every file exactly as it was.

The **base** is the memory worktree's ``HEAD`` commit: the memory line the leaf started from or last
synced to. It answers the two questions only a base can answer: an existing record's revision before
this leaf (a meaning change increments it once, MIK-R07 rule 2), and an entry's anchor before this
leaf (a history row's ``before``). The exact K_B resolver of MIK-R07 rule 0 belongs to the gate
(MIK-R08, MIK-R09); a memory root that is not a Git work tree has no base, and then every record and
entry is new to this leaf.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from agents_remember.kernel.git_command import GitPreparationError
from agents_remember.memory_quality.knowledge_validator.trees import (
    KnowledgeTree,
    KnowledgeTreeReadError,
    knowledge_tree_from_directory,
    knowledge_tree_from_git,
)
from agents_remember.models.knowledge_files.canonical import CanonicalFormatError, parse_json
from agents_remember.models.knowledge_files.documents import (
    KNOWLEDGE_ROOT,
    RECORD_DIRECTORIES,
    file_sidecar_path,
    record_path,
    split_record_filename,
)
from agents_remember.models.knowledge_files.ids import RecordKind
from agents_remember.models.knowledge_files.sidecars import FILE_SIDECAR_SCHEMA

EntryList = Literal["realizes", "proves"]
_KIND_OF_DIRECTORY: dict[str, RecordKind] = {
    directory: kind for kind, directory in RECORD_DIRECTORIES.items()
}


@dataclass(frozen=True)
class Owner:
    """Who authors this operation: the task, and the leaf or wave that owns its history file."""

    task: str
    kind: Literal["leaf", "wave"]
    id: str

    def origin(self) -> dict[str, Any]:
        return {"task": self.task, self.kind: self.id}

    def authored(self, document: Mapping[str, Any], handoff_entry: str) -> bool:
        """Whether a record's or entry's ``origin`` names this owner and this hand-off entry."""

        origin = document.get("origin")
        if not isinstance(origin, Mapping):
            return False
        named = origin.get(self.kind) if "task" in origin else origin.get("leaf")
        return named == self.id and origin.get("handoffEntry") == handoff_entry


@dataclass(frozen=True)
class EntryLocation:
    """Where one realization or proof entry sits: its sidecar and which list holds it."""

    sidecar: str
    source_path: str
    entries: EntryList
    document: dict[str, Any]


def _json(data: bytes | None) -> dict[str, Any] | None:
    if data is None:
        return None
    try:
        parsed = parse_json(data.decode("utf-8"))
    except (UnicodeDecodeError, CanonicalFormatError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _record_id_and_kind(path: str) -> tuple[str, RecordKind] | None:
    parts = path.split("/")
    if len(parts) != 3 or parts[0] != KNOWLEDGE_ROOT or not parts[2].endswith(".json"):
        return None
    kind = _KIND_OF_DIRECTORY.get(parts[1])
    if kind is None:
        return None
    try:
        record_id, _slug, _extension = split_record_filename(parts[2])
    except ValueError:
        return None
    return record_id, kind


def _record_paths(files: Mapping[str, bytes]) -> dict[str, tuple[str, RecordKind]]:
    found: dict[str, tuple[str, RecordKind]] = {}
    for path in files:
        identified = _record_id_and_kind(path)
        if identified is not None:
            found[identified[0]] = (path, identified[1])
    return found


def _file_sidecars(files: Mapping[str, bytes]) -> Iterator[tuple[str, dict[str, Any]]]:
    for path, data in files.items():
        if path.startswith("onboarding/") and path.endswith(".json"):
            document = _json(data)
            if document is not None and document.get("schema") == FILE_SIDECAR_SCHEMA:
                yield path, document


def read_base(root: Path) -> KnowledgeTree | None:
    """The memory worktree's ``HEAD`` tree, or ``None`` when ``root`` is not a Git work tree."""

    if not (root / ".git").exists():
        return None
    try:
        return knowledge_tree_from_git(root, "HEAD", label=f"{root.name}@HEAD")
    except (KnowledgeTreeReadError, GitPreparationError):
        return None


@dataclass
class MemoryState:
    """The candidate memory tree, the edits of one operation, and the base it compares against."""

    root: Path
    files: dict[str, bytes]
    base: KnowledgeTree | None
    documents: dict[str, dict[str, Any]] = field(default_factory=dict)
    removed: set[str] = field(default_factory=set)
    moved_markdown: dict[str, str] = field(default_factory=dict)
    records: dict[str, tuple[str, RecordKind]] = field(default_factory=dict)
    sidecar_paths: list[str] = field(default_factory=list)
    touched: set[str] = field(default_factory=set)
    minted: set[str] = field(default_factory=set)
    _base_records: dict[str, str] | None = None
    _base_anchors: dict[str, dict[str, Any]] | None = None

    @classmethod
    def load(cls, root: Path) -> MemoryState:
        tree = knowledge_tree_from_directory(root)
        files = dict(tree.files)
        state = cls(root=root, files=files, base=read_base(root), records=_record_paths(files))
        for path, document in _file_sidecars(files):
            state.sidecar_paths.append(path)
            state.documents[path] = document
        return state

    @property
    def converted(self) -> bool:
        return KnowledgeTree(label="candidate", files=self.files).converted

    # -- documents ----------------------------------------------------------------------------

    def document(self, path: str) -> dict[str, Any] | None:
        """The working copy of the JSON document at ``path`` (``None`` if absent or unreadable)."""

        if path in self.removed:
            return None
        if path not in self.documents:
            loaded = _json(self.files.get(path))
            if loaded is None:
                return None
            self.documents[path] = loaded
        return self.documents[path]

    def put(self, path: str, document: dict[str, Any]) -> None:
        self.removed.discard(path)
        if path not in self.documents and document.get("schema") == FILE_SIDECAR_SCHEMA:
            self.sidecar_paths.append(path)
        self.documents[path] = document
        self.touched.add(path)

    def touch(self, path: str) -> None:
        """Mark a document edited in place, so it is rendered and validated."""

        self.touched.add(path)

    # -- records ------------------------------------------------------------------------------

    def known_ids(self) -> set[str]:
        """Every ID the tree holds: records, entries, and history rows of every history file."""

        ids = set(self.records) | self.minted
        for path, data in self.files.items():
            if path.startswith(f"{KNOWLEDGE_ROOT}/history/"):
                history = _json(data) or {}
                ids.update(
                    str(row.get("id")) for row in history.get("rows") or () if isinstance(row, dict)
                )
        for _path, sidecar in self.sidecars():
            for key in ("realizes", "proves"):
                ids.update(str(entry.get("id")) for entry in sidecar.get(key) or ())
        return ids

    def record(self, record_id: str) -> tuple[str, RecordKind, dict[str, Any]] | None:
        located = self.records.get(record_id)
        if located is None:
            return None
        path, kind = located
        document = self.document(path)
        return None if document is None else (path, kind, document)

    def record_by_origin(self, kind: RecordKind, owner: Owner, handoff_entry: str) -> str | None:
        """The ID of the record of ``kind`` this owner authored from ``handoff_entry``, if any."""

        for record_id, (_path, record_kind) in sorted(self.records.items()):
            if record_kind != kind:
                continue
            found = self.record(record_id)
            if found is not None and owner.authored(found[2], handoff_entry):
                return record_id
        return None

    def put_record(
        self, kind: RecordKind, record_id: str, slug: str | None, document: dict[str, Any]
    ) -> str:
        """Place a record's document, keeping its file name unless a new slug renames it."""

        current = self.records.get(record_id)
        path = current[0] if current is not None else None
        if path is None or (slug is not None and not path.endswith(f"{record_id}-{slug}.json")):
            new_path = record_path(kind, record_id, slug or "record")
            if path is not None and path != new_path:
                self.removed.add(path)
                self.documents.pop(path, None)
                self.touched.discard(path)
                markdown = path.removesuffix(".json") + ".md"
                if markdown in self.files:
                    self.moved_markdown[markdown] = new_path.removesuffix(".json") + ".md"
            path = new_path
        self.records[record_id] = (path, kind)
        self.put(path, document)
        return path

    # -- sidecars -----------------------------------------------------------------------------

    def sidecars(self) -> Iterator[tuple[str, dict[str, Any]]]:
        """Every file sidecar of the candidate, with this operation's edits."""

        for path in self.sidecar_paths:
            document = self.document(path)
            if document is not None:
                yield path, document

    def file_sidecar(self, source_path: str) -> tuple[str, dict[str, Any]]:
        """The file sidecar of ``source_path``, created empty when the file has none yet."""

        path = file_sidecar_path(source_path)
        document = self.document(path)
        if document is None:
            document = {
                "schema": FILE_SIDECAR_SCHEMA,
                "path": source_path,
                "references": {},
                "realizes": [],
            }
            self.put(path, document)
        return path, document

    def find_entry(self, entry_id: str) -> EntryLocation | None:
        for path, sidecar in self.sidecars():
            for key in ("realizes", "proves"):
                for entry in sidecar.get(key) or ():
                    if entry.get("id") == entry_id:
                        return EntryLocation(path, str(sidecar.get("path")), key, entry)
        return None

    def entries_by_origin(self, owner: Owner, handoff_entry: str, invariant: str) -> list[str]:
        """The IDs of every entry this owner wrote for ``invariant`` from ``handoff_entry``."""

        return sorted(
            str(entry.get("id"))
            for _path, sidecar in self.sidecars()
            for key in ("realizes", "proves")
            for entry in sidecar.get(key) or ()
            if entry.get("invariant") == invariant and owner.authored(entry, handoff_entry)
        )

    def remove_entry(self, entry_id: str) -> None:
        located = self.find_entry(entry_id)
        if located is None:
            return
        sidecar = self.document(located.sidecar)
        assert sidecar is not None
        sidecar[located.entries] = [
            entry for entry in sidecar[located.entries] if entry.get("id") != entry_id
        ]
        self.touch(located.sidecar)

    # -- the base -----------------------------------------------------------------------------

    def base_record(self, record_id: str) -> dict[str, Any] | None:
        if self.base is None:
            return None
        if self._base_records is None:
            self._base_records = {
                identified[0]: path
                for path in self.base.files
                if (identified := _record_id_and_kind(path)) is not None
            }
        path = self._base_records.get(record_id)
        return None if path is None else _json(self.base.get(path))

    def base_entry_anchor(self, entry_id: str) -> dict[str, Any] | None:
        """The entry's anchor in the base, with its sidecar's source path filled in."""

        if self.base is None:
            return None
        if self._base_anchors is None:
            self._base_anchors = {
                str(entry.get("id")): {**entry["anchor"], "path": sidecar.get("path")}
                for _path, sidecar in _file_sidecars(self.base.files)
                for key in ("realizes", "proves")
                for entry in sidecar.get(key) or ()
                if isinstance(entry, dict) and isinstance(entry.get("anchor"), dict)
            }
        return self._base_anchors.get(entry_id)

    # -- the result ---------------------------------------------------------------------------

    def changed_documents(self) -> dict[str, dict[str, Any]]:
        """Every document this operation placed or edited, by path."""

        return {
            path: self.documents[path]
            for path in sorted(self.touched)
            if path not in self.removed and path in self.documents
        }

    def candidate_files(self, rendered: Mapping[str, bytes]) -> dict[str, bytes]:
        """The candidate tree after this operation: ``rendered`` over the original bytes."""

        files = {path: data for path, data in self.files.items() if path not in self.removed}
        for old, new in self.moved_markdown.items():
            files[new] = files.pop(old)
        files.update(rendered)
        return files

    def write(self, files: Mapping[str, bytes]) -> tuple[list[str], list[str]]:
        """Write ``files`` where they differ from disk and delete what the operation removed."""

        written = []
        for path, data in sorted(files.items()):
            if self.files.get(path) == data:
                continue
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f".{target.name}.ar-writer")
            temporary.write_bytes(data)
            temporary.replace(target)
            written.append(path)
        removed = sorted(path for path in self.files if path not in files)
        for path in removed:
            (self.root / path).unlink(missing_ok=True)
        return written, removed


def deep_copy(document: Mapping[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(dict(document))


def canonical_equal(left: Any, right: Any) -> bool:
    return json.dumps(left, sort_keys=True) == json.dumps(right, sort_keys=True)
