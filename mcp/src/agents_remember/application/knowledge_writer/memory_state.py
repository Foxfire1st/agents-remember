"""The memory tree one writer operation reads and edits, and the base it compares against.

The candidate is the memory working tree K_C: every file under ``knowledge/`` and ``onboarding/``,
read once as bytes. The operation edits parsed copies of the JSON files it touches; nothing reaches
the disk until :meth:`MemoryState.write` is called with the validated result, so a refused operation
leaves every file exactly as it was.

The **base** is the memory worktree's ``HEAD`` commit: the memory line the leaf started from or last
synced to. It answers the two questions only a base can answer: an existing record's revision before
this leaf (a meaning change increments it once, MIK-R07 rule 2), and an entry's anchor before this
leaf (a history row's ``before``). When ``HEAD`` is unconverted and the candidate is converted, the
base is ``HEAD``'s conversion (MIK-R24 rule 7, :mod:`.base_side`), the one the worklist and the gate
read. The exact K_B resolver of MIK-R07 rule 0 belongs to the gate (MIK-R08, MIK-R09); a memory root
that is not a Git work tree has no base, and then every record and entry is new to this leaf.
"""

from __future__ import annotations

import copy
import json
import subprocess
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from agents_remember.application.knowledge_writer.base_side import BaseCode, writer_bases
from agents_remember.kernel.git_command import (
    GIT_METADATA_TIMEOUT_SECONDS,
    GitPreparationError,
    GitRunnerOptions,
    read_git_blobs_bytes,
    run_git,
)
from agents_remember.memory_quality.knowledge_validator.trees import (
    KnowledgeTree,
    knowledge_tree_from_directory,
)
from agents_remember.models.knowledge_files.canonical import CanonicalFormatError, parse_json
from agents_remember.models.knowledge_files.documents import (
    KNOWLEDGE_ROOT,
    RECORD_DIRECTORIES,
    file_sidecar_path,
    history_path,
    owner_history_attempt,
    record_path,
    split_record_filename,
)
from agents_remember.models.knowledge_files.history import is_closed_history, writable_attempt
from agents_remember.models.knowledge_files.ids import RecordKind
from agents_remember.models.knowledge_files.sidecars import FILE_SIDECAR_SCHEMA

EntryList = Literal["realizes", "proves"]
_KIND_OF_DIRECTORY: dict[str, RecordKind] = {
    directory: kind for kind, directory in RECORD_DIRECTORIES.items()
}


@dataclass(frozen=True)
class Owner:
    """Who authors this operation: the task, and the leaf, wave or crossing that owns its history.

    A ``crossing`` owner is a master line's crossing sync (MIK-R24 rule 8 step 4): its rows go into
    the ``<task-id>-crossing-<n>.json`` file the sync opened; it may resolve an existing record its
    sync left conflicted, and authors no entry or new record.
    """

    task: str
    kind: Literal["leaf", "wave", "crossing"]
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


class MergeStageError(RuntimeError):
    """Git cannot say which versions a merge left at an unmerged record path."""


def _side_revisions(root: Path, path: str, blobs: tuple[str, str]) -> list[int]:
    """The ``revision`` of each merge side's version of the record at ``path``."""

    try:
        data = read_git_blobs_bytes(root, list(blobs))
    except (GitPreparationError, OSError, subprocess.SubprocessError) as error:
        raise MergeStageError(f"cannot read the merge sides of {path}: {error}") from error
    revisions = [(_json(data.get(blob)) or {}).get("revision") for blob in blobs]
    integers = [one for one in revisions if isinstance(one, int)]
    if len(integers) != len(revisions):
        raise MergeStageError(f"a merge side of {path} names no integer revision")
    return integers


def _unmerged_stages(root: Path, path: str) -> dict[str, str]:
    """``{stage: blob}`` of ``path`` while a merge leaves it unmerged (empty when it is merged)."""

    try:
        result = run_git(
            root,
            ["ls-files", "-u", "-z", "--", path],
            GitRunnerOptions(timeout=GIT_METADATA_TIMEOUT_SECONDS),
        )
    except subprocess.SubprocessError as error:
        raise MergeStageError(f"cannot list the merge stages of {path}: {error}") from error
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise MergeStageError(f"cannot list the merge stages of {path}: {detail}")
    stages: dict[str, str] = {}
    for item in result.stdout.split("\0"):
        metadata, _tab, _path = item.partition("\t")
        fields = metadata.split()
        if len(fields) == 3:
            stages[fields[2]] = fields[1]
    return stages


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
    base_problem: str | None = None
    """Why the converted base could not be built; the writer refuses with it."""
    _base_records: dict[str, str] | None = None
    _base_anchors: dict[str, dict[str, Any]] | None = None

    @classmethod
    def load(cls, root: Path, *, code: BaseCode | None = None) -> MemoryState:
        """The candidate at ``root`` and its base: ``HEAD``, converted when the candidate is
        converted and ``HEAD`` is not (MIK-R24 rule 7, :mod:`.base_side`; ``code`` names the code
        the conversion reads, its fallback commit, and the converted-base cache)."""

        tree = knowledge_tree_from_directory(root)
        files = dict(tree.files)
        sides = writer_bases(root, tree, code or BaseCode())
        state = cls(
            root=root,
            files=files,
            base=sides.base,
            records=_record_paths(files),
            base_problem=sides.problem,
        )
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

    def history_target(self, owner: Owner) -> tuple[str, int]:
        """The history file this owner writes, and its attempt (L37 ruling on reopen).

        A leaf writes its latest attempt, and the next attempt once the latest is closed in the
        base -- the leaf closed out, and was reopened on a line that holds its frozen file, which it
        never edits. A file closed only in the candidate is still the target, and the write refuses
        it by name (MIK-R07 rule 7). A wave or a crossing has one file.
        """

        if owner.kind != "leaf":
            return history_path(owner.id), 1
        base = self.base.files if self.base is not None else {}
        frozen: dict[int, bool] = {}
        for path in (set(self.files) | set(self.documents) | set(base)) - self.removed:
            attempt = owner_history_attempt(path, owner.id)
            if attempt is not None:
                frozen[attempt] = is_closed_history(base.get(path))
        attempt = writable_attempt(frozen)
        return history_path(owner.id, attempt), attempt

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

    def merged_sides_revision(self, record_id: str) -> int | None:
        """The higher of the two sides' revisions of a record a merge left unmerged, else ``None``.

        A crossing sync leaves a record both sides changed unmerged, its own and incoming versions
        as index stages 2 and 3 (MIK-R24 rule 8 step 4); the resolution's revision is one more than
        this. Raises :class:`MergeStageError` when Git cannot say.
        """

        located = self.records.get(record_id)
        if located is None or not (self.root / ".git").exists():
            return None
        stages = _unmerged_stages(self.root, located[0])
        if "2" not in stages or "3" not in stages:
            return None
        return max(_side_revisions(self.root, located[0], (stages["2"], stages["3"])))

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
