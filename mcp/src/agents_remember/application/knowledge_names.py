"""Name-not-carry projection for ``knowledge_read`` answers (MIK-R109 rule 2).

A read names where a thing is and does not carry it. The application layer
keeps computing the whole result — the dashboard reader and the command line
call the same functions and keep getting everything (MIK-R108 rule 5) — and
the projection runs where the answer leaves the server for an agent, in
:func:`knowledge_read_payload`.

A projected row holds the record's text ID, its kind, its computed state,
and its location: the file in the memory checkout the server read, as an
absolute path, with the commit it read beside the answer as ``memoryCommit``.
What no file holds stays in short form: the currentness of a record, the
state of a link, a derived status. The text of a record is not in the
answer. Paging stays, within the bound of MIK-R108.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agents_remember.application.read_file_cards import SectionAnchors, _select_section
from agents_remember.kernel import filesystem
from agents_remember.memory.knowledge_index import KnowledgeIndex
from agents_remember.models.tools.tool_answer import TOOL_ANSWER_LIST_MAX

_ENTRY_ID_RE = re.compile(r"\b(?:RLZ|PRF)-[A-Z0-9]+\b")

_GIT_TIMEOUT_SECONDS = 20


class SelectionMovedError(Exception):
    """The memory checkout moved between selection and projection.

    Carries the selection-time commit and the projection-time commit so
    the refusal names the move instead of mixing two snapshots' rows.
    """

    def __init__(self, selection_commit: str | None, current_commit: str | None) -> None:
        super().__init__(selection_commit, current_commit)
        self.selection_commit = selection_commit
        self.current_commit = current_commit


@dataclass(frozen=True)
class SelectionProvenance:
    """The memory epoch a read selected: commit and dirtiness at selection time.

    ``relative`` names the locating the epoch was selected for: with it the
    emitted files are memory-root-relative, otherwise absolute.
    """

    commit: str | None = None
    dirty: bool = False
    relative: bool = False


@dataclass
class NameResolver:
    """Text IDs, record files and states for one projected answer.

    Opened once per answer: the index for UUID→text-ID resolution, one
    scan of the memory tree's record files for ID→file resolution, and the
    memory commit the locations were read from. Card sidecars are read at
    most once per card per answer and cached here.
    """

    memory_root: Path
    index_path: Path
    tree_key: str
    memory_commit: str | None = None
    memory_dirty: bool = False
    relative_locations: bool = False
    files: dict[str, str] = field(default_factory=dict)
    _indexed_sidecars: dict[str, str] = field(default_factory=dict, repr=False)
    _indexed_sidecars_loaded: bool = field(default=False, repr=False)
    _lookups_loaded: bool = field(default=False, repr=False)
    _file_text: dict[str, str | None] = field(default_factory=dict, repr=False)
    _selection_commit: str | None = field(default=None, repr=False)
    _entries: dict[str, str] = field(default_factory=dict)
    _entries_scanned: bool = False
    _entry_files: dict[str, list[str]] = field(default_factory=dict)
    _sidecars: dict[str, dict[str, Any] | None] = field(default_factory=dict)
    _text_ids: dict[str, str | None] = field(default_factory=dict)
    _record_paths: dict[str, str | None] = field(default_factory=dict)
    _sidecar_docs: dict[str, Any] = field(default_factory=dict, repr=False)
    _declared_ids: dict[str, set[str] | None] = field(default_factory=dict, repr=False)
    _file_lines: dict[str, list[str] | None] = field(default_factory=dict, repr=False)
    _declaration_lines: dict[str, dict[str, int] | None] = field(default_factory=dict, repr=False)
    file_reads: int = 0
    sidecar_parses: int = 0

    @classmethod
    def open(
        cls,
        *,
        memory_root: Path,
        index_path: Path,
        tree_key: str,
        relative_locations: bool = False,
        selection: SelectionProvenance | None = None,
    ) -> NameResolver:
        """Open one resolver over a converted memory tree's index and files.

        Commit provenance binds the selection, never a later probe: when
        the caller hands the selection-time epoch, a checkout that moved
        since raises :class:`SelectionMovedError` instead of mixing two
        snapshots' rows. Without a selection the current epoch is both
        provenance and validation, as before.
        """

        root = Path(memory_root).resolve()
        current_commit = _memory_commit(root)
        current_dirty = _memory_dirty(root)
        if selection is not None and selection.commit != current_commit:
            raise SelectionMovedError(selection.commit, current_commit)
        files: dict[str, str] = {}
        knowledge = root / "knowledge"
        if knowledge.is_dir():
            for path in sorted(knowledge.rglob("*.json")):
                stem = path.stem
                rel = path.relative_to(root).as_posix()
                files.setdefault(stem, rel)
        return cls(
            memory_root=root,
            index_path=index_path,
            tree_key=tree_key,
            memory_commit=selection.commit if selection is not None else current_commit,
            memory_dirty=selection.dirty if selection is not None else current_dirty,
            relative_locations=relative_locations,
            files=files,
            _selection_commit=selection.commit if selection is not None else None,
        )

    def revalidate(self) -> NameResolver:
        """Re-check the checkout against the bound selection, reusing caches.

        One shared instance serves sizing and projection: the file and
        index caches bound at selection stay valid only while the checkout
        does. A checkout that moved since raises :class:`SelectionMovedError`
        instead of projecting mixed rows.
        """

        current = _memory_commit(self.memory_root)
        if self._selection_commit is not None and self._selection_commit != current:
            raise SelectionMovedError(self._selection_commit, current)
        return self

    def read_text(self, rel: str) -> str | None:
        """One memory-relative file's text, cached per answer, or ``None``.

        Card and section reads share one read per file per call instead of
        one read per row that cites it.
        """

        if rel not in self._file_text:
            self.file_reads += 1
            try:
                self._file_text[rel] = filesystem.read_text(self.memory_root / rel)
            except (UnicodeDecodeError, OSError, ValueError):
                self._file_text[rel] = None
        return self._file_text[rel]

    def file_lines(self, rel: str) -> list[str] | None:
        """One memory-relative file's lines, split once per answer, or ``None``."""

        if rel not in self._file_lines:
            text = self.read_text(rel)
            self._file_lines[rel] = text.splitlines() if text is not None else None
        return self._file_lines[rel]

    def declares_entry(self, rel: str, entry_id: str) -> bool:
        """Whether one memory-relative JSON file declares an entry ID.

        The sidecar parses once per answer and its declarations index
        once: a declaration is an ``id`` field on a realization/proof
        anchor mapping, while prose that merely names the ID does not
        declare it. Every later entry is one set lookup, not another
        recursive document walk.
        """

        if rel not in self._sidecar_docs:
            self.sidecar_parses += 1
            try:
                self._sidecar_docs[rel] = json.loads(filesystem.read_text(self.memory_root / rel))
            except (UnicodeDecodeError, OSError, ValueError):
                self._sidecar_docs[rel] = None
            self._declared_ids[rel] = _collect_declared_ids(self._sidecar_docs[rel])
        indexed = self._declared_ids.get(rel)
        return indexed is not None and entry_id in indexed

    def line_of(self, rel: str, mark: str) -> int | None:
        """The 1-based declaration line of ``mark`` in one cached file.

        One indexing pass per file maps every ``id``-field declaration to
        its line; every mark after the first is one map lookup, not another
        scan from line 1. An ``id`` field naming the mark wins wherever it
        sits (the F12 declaration preference); otherwise the earliest
        passing mention, as before.
        """

        if rel not in self._declaration_lines:
            self._declaration_lines[rel] = self._index_declaration_lines(rel)
        indexed = self._declaration_lines.get(rel)
        if indexed is not None and mark in indexed:
            return indexed[mark]
        lines = self.file_lines(rel)
        if lines is None:
            return None
        field = re.compile(r"['\"]id['\"]\s*:\s*['\"]" + re.escape(mark) + r"['\"]")
        mention: int | None = None
        for number, line in enumerate(lines, start=1):
            if field.search(line):
                return number
            if mention is None and mark in line:
                mention = number
        return mention

    def _index_declaration_lines(self, rel: str) -> dict[str, int] | None:
        """Every ``id``-field declaration's line in one file, in one pass."""

        lines = self.file_lines(rel)
        if lines is None:
            return None
        field = re.compile(r"['\"]id['\"]\s*:\s*['\"]([^'\"]+)['\"]")
        indexed: dict[str, int] = {}
        for number, line in enumerate(lines, start=1):
            match = field.search(line)
            if match is not None:
                indexed.setdefault(match.group(1), number)
        return indexed

    def text_id(self, projected_uuid: str) -> str | None:
        """The text ID one projected UUID stands for, or ``None``.

        The whole UUID map loads once per answer, so rows share one
        read-only open instead of one open per row.
        """

        if not self._lookups_loaded:
            self._load_index_lookups()
        return self._text_ids.get(projected_uuid)

    def record_path(self, text_id: str) -> str | None:
        """The memory-relative file declaring one record ID, or ``None``.

        The bound index's authoritative record path wins over the filename
        scan: a history mention of the ID never outranks its declaration.
        The whole record map loads once per answer, beside the UUID map.
        """

        bare = text_id.split("@", 1)[0]
        if not self._lookups_loaded:
            self._load_index_lookups()
        if bare in self._record_paths:
            return self._record_paths[bare]
        return None

    def _load_index_lookups(self) -> None:
        """Bulk-load the UUID, record and entry maps once per answer."""

        self._lookups_loaded = True
        try:
            with KnowledgeIndex(self.index_path, expected_key=self.tree_key) as index:
                self._text_ids.update(index.uuid_text_ids())
                for record_id, path in index.record_paths().items():
                    self._record_paths.setdefault(record_id, path)
        except Exception:
            return

    def record_file(self, text_id: str) -> str | None:
        """The memory-relative record file carrying ``text_id``, or ``None``."""

        bare = text_id.split("@", 1)[0]
        if bare in self.files:
            return self.files[bare]
        for stem, rel in self.files.items():
            if stem == bare or stem.startswith(bare + "-"):
                return rel
        return None

    def _scan_entry_files(self) -> None:
        """Collect every file naming an entry ID, once per answer, lazily."""

        for tree in ("knowledge", "onboarding"):
            knowledge = self.memory_root / tree
            if not knowledge.is_dir():
                continue
            for path in sorted(knowledge.rglob("*.json")):
                try:
                    text = filesystem.read_text(path)
                except (UnicodeDecodeError, OSError, ValueError):
                    continue
                rel = path.relative_to(self.memory_root).as_posix()
                for mark in set(_ENTRY_ID_RE.findall(text)):
                    self._entry_files.setdefault(mark, []).append(rel)

    def entry_file(self, entry_id: str) -> str | None:
        """The memory-relative knowledge file declaring one entry ID, or ``None``.

        The authoritative indexed entry sidecar wins: the index built the
        tree the answer binds, so its declaration file is the entry's
        file. Entries the index does not know fall back to the working
        scan. Realization and proof entries are claimed inside record
        files, not in a file of their own. Every file naming the ID is
        collected once per answer; the first file that declares the entry
        (an ``id`` field on a realization/proof anchor, not prose) wins
        over an earlier history mention. The scan runs once per answer,
        lazily, on the first entry row.
        """

        if entry_id in self._entries:
            return self._entries[entry_id]
        indexed = self._indexed_entry_file(entry_id)
        if indexed is not None and self.declares_entry(indexed, entry_id):
            self._entries[entry_id] = indexed
            return indexed
        if not self._entries_scanned:
            self._entries_scanned = True
            self._scan_entry_files()
        if entry_id in self._entries:
            return self._entries[entry_id]
        candidates = self._entry_files.get(entry_id, [])
        for rel in candidates:
            if self.declares_entry(rel, entry_id):
                self._entries[entry_id] = rel
                return rel
        # A file that merely mentions the ID is not its declaration: no
        # authoritative file is claimed rather than an earlier history
        # note that merely names it.
        return None

    def _indexed_entry_file(self, entry_id: str) -> str | None:
        """One entry's indexed sidecar, or ``None`` when the index is silent.

        The whole entry→sidecar map loads once per answer, so entry rows
        share one read-only open instead of one open per row.
        """

        if not self._indexed_sidecars_loaded:
            self._indexed_sidecars_loaded = True
            try:
                with KnowledgeIndex(self.index_path) as index:
                    self._indexed_sidecars = index.entry_sidecars()
            except Exception:
                return None
        return self._indexed_sidecars.get(entry_id)

    def card_references(self, card_rel: str) -> dict[str, Any] | None:
        """The card sidecar's ``references{}`` mapping, or ``None``.

        Read at most once per card per answer: converted ``[n]`` markers
        resolve through the sidecar beside the card. A missing or
        unreadable sidecar leaves legacy matching and title fallback.
        """

        if card_rel not in self._sidecars:
            sidecar = self.memory_root / card_rel
            try:
                stem = sidecar.with_suffix(".json").read_text(encoding="utf-8")
                document = json.loads(stem)
            except (UnicodeDecodeError, OSError, ValueError):
                document = None
            references = document.get("references") if isinstance(document, dict) else None
            self._sidecars[card_rel] = dict(references) if isinstance(references, dict) else None
        return self._sidecars[card_rel]


def _collect_declared_ids(document: Any) -> set[str] | None:
    """Every entry ID one parsed sidecar declares, walked once.

    One traversal tests each id-bearing mapping with the declaration
    predicate and descends everywhere, so later entries are set lookups
    instead of repeated whole-document scans. ``None`` when the sidecar
    did not parse to a mapping or list.
    """

    if not isinstance(document, (dict, list)):
        return None
    found: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            ident = value.get("id")
            if isinstance(ident, str) and _declares_in(value, ident):
                found.add(ident)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(document)
    return found


def _declares_in(value: Any, entry_id: str) -> bool:
    if isinstance(value, dict):
        if value.get("id") == entry_id and (
            "anchor" in value or value.get("kind") in ("realization", "proof")
        ):
            return True
        return any(_declares_in(item, entry_id) for item in value.values())
    if isinstance(value, list):
        return any(_declares_in(item, entry_id) for item in value)
    return False


def _memory_dirty(memory_root: Path) -> bool:
    """Whether the memory checkout holds uncommitted knowledge changes.

    Tracked modifications and untracked files both count: an untracked
    record file is a change the index has not seen, so it dirties the
    checkout like any other uncommitted change.
    """

    try:
        completed = subprocess.run(
            ["git", "-C", str(memory_root), "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=False,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0 and bool(completed.stdout.strip())


def _memory_commit(memory_root: Path) -> str | None:
    """The commit the memory checkout was read at, best effort."""

    try:
        completed = subprocess.run(
            ["git", "-C", str(memory_root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    commit = completed.stdout.strip()
    return commit if completed.returncode == 0 and commit else None


def project_knowledge_body(body: dict[str, Any], resolver: NameResolver) -> dict[str, Any]:
    """Project one full ``knowledge_read`` body to names (MIK-R109 rule 2).

    Refusals pass through untouched. Pages keep their paging, counts,
    limitations and short-form blocks; rows, proofs and family headers become
    ID/kind/state/location entries; record text is dropped.
    """

    if body.get("state") == "refused":
        return body
    states = _state_table(body.get("currentness"))
    fallen_back = _problem_fallback(body.get("currentness"))
    projected = dict(body)
    payload = body.get("payload")
    projected_rows: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        rows = payload.get("rows")
        if isinstance(rows, list):
            payload = dict(payload)
            projected_rows = [_project_row(row, resolver, states, fallen_back) for row in rows]
            payload["rows"] = projected_rows
            projected["payload"] = payload
        else:
            items = payload.get("items")
            if isinstance(items, list):
                # An accepted scope continuation carries prose-bearing items,
                # not rows: project every item to a name, like every row.
                payload = dict(payload)
                projected_rows = [
                    _project_item(item, resolver, states, fallen_back) for item in items
                ]
                payload["rows"] = projected_rows
                del payload["items"]
                projected["payload"] = payload
    if isinstance(body.get("proofs"), list):
        projected["proofs"] = [
            _project_proof(proof, states, resolver)
            for proof in body["proofs"]
            if isinstance(proof, dict)
        ]
    if isinstance(body.get("families"), list):
        projected["families"] = [
            _project_family(entry, resolver, states, fallen_back)
            for entry in body["families"]
            if isinstance(entry, dict)
        ]
    if isinstance(body.get("currentness"), dict):
        keep = _referenced_ids(projected)
        projected["currentness"] = _compact_currentness(body["currentness"], keep)
    header = (
        body.get("page", {}).get("headerReference") if isinstance(body.get("page"), dict) else None
    )
    if isinstance(header, dict):
        page = dict(body["page"])
        page["headerReference"] = _project_reference(header, resolver, states, fallen_back)
        projected["page"] = page
    projected["memoryCommit"] = getattr(resolver, "memory_commit", None)
    projected["memoryDirty"] = bool(getattr(resolver, "memory_dirty", False))
    return projected


def _problem_fallback(currentness: Any) -> str | None:
    """The read-wide state rows inherit when no row state was computed.

    A currentness block that carries a read-wide reason (the code tree
    could not be opened, the step failed) explains every entry at once:
    rows the walk could not evaluate are ``unverifiable`` beside that
    reason, never silently stateless and never defaulted to current.
    """

    if not isinstance(currentness, dict):
        return None
    if currentness.get("unverifiableReason") or currentness.get("problem"):
        return "unverifiable"
    return None


def _state_table(currentness: Any) -> dict[str, str]:
    """Record and entry states by text ID, from one currentness block."""

    states: dict[str, str] = {}
    if not isinstance(currentness, dict):
        return states
    for one in currentness.get("invariants", []):
        if not isinstance(one, dict):
            continue
        if isinstance(one.get("id"), str) and isinstance(one.get("state"), str):
            states[one["id"]] = one["state"]
        for entry in one.get("entries", []):
            if (
                isinstance(entry, dict)
                and isinstance(entry.get("id"), str)
                and isinstance(entry.get("state"), str)
            ):
                states[entry["id"]] = entry["state"]
    for family in currentness.get("families", []):
        if not isinstance(family, dict) or not isinstance(family.get("id"), str):
            continue
        stale_count = family.get("staleMembers")
        stale_list = family.get("stale", [])
        stale = (
            (isinstance(stale_count, int) and stale_count > 0)
            or (isinstance(stale_list, list) and len(stale_list) > 0)
            or (
                isinstance(family.get("members"), list)
                and isinstance(stale_list, list)
                and any(member in stale_list for member in family["members"])
            )
        )
        # Only evidenced stale is recorded: a family with no stale evidence
        # stays stateless here rather than defaulting to current, so rows
        # never present an unmeasured state as computed (MIK-R109 rule 2).
        if stale:
            states[family["id"]] = "stale"
    return states


def _referenced_ids(projected: dict[str, Any]) -> set[str]:
    """Every text ID the delivered answer names, segments of memberships included.

    The answer carries the count and the first entries of a longer list
    (MIK-R108 rule 2), so currentness covers the first
    ``TOOL_ANSWER_LIST_MAX`` rows: a state the delivered answer cannot act
    on is not the caller's next step. The whole result behind the answer's
    log keeps every record's state.
    """

    keep: set[str] = set()

    def add(value: Any) -> None:
        if isinstance(value, str) and value:
            keep.add(value)
            keep.update(value.split("/"))

    payload = projected.get("payload")
    if isinstance(payload, dict):
        for row in (payload.get("rows", []) or [])[:TOOL_ANSWER_LIST_MAX]:
            if isinstance(row, dict):
                add(row.get("id"))
                add(row.get("memberOf"))
    for key in ("proofs", "families"):
        for entry in (projected.get(key, []) or [])[:TOOL_ANSWER_LIST_MAX]:
            if isinstance(entry, dict):
                add(entry.get("id"))
    return keep


def _compact_currentness(currentness: dict[str, Any], keep: set[str]) -> dict[str, Any]:
    """One currentness block with entries in compact form (MIK-R109 rule 2).

    The currentness of a record stays in the answer in short form: each entry
    by ID, kind, code path and state only -- and only for the records the
    page names. Blob digests, content digests and reasons stay in the whole
    result behind the answer's log, with the unreferenced records beside
    them: a state the caller cannot act on is not the caller's next step.
    """

    compacted = dict(currentness)
    invariants = []
    for one in currentness.get("invariants", []):
        if not isinstance(one, dict):
            continue
        entries = [
            {key: item[key] for key in ("id", "kind", "path", "state") if key in item}
            for item in one.get("entries", [])
            if isinstance(item, dict)
        ]
        if one.get("id") not in keep and not any(item.get("id") in keep for item in entries):
            continue
        entry = {key: one[key] for key in ("id", "state") if key in one}
        entry["entries"] = [item for item in entries if item.get("id") in keep]
        invariants.append(entry)
    compacted["invariants"] = invariants
    return compacted


# Structural row kinds to their record kind and endpoint role. A row's
# ``kind`` names the record itself (MIK-R109 rule 2); the endpoint rides
# the ``role`` beside the row. Every mapping below is injective on
# (kind, role): rows that share a record's ID stay distinct endpoints,
# so (kind, id, role) is unique wherever (kind, id) was.
_STRUCTURAL_ROLES = {
    "member": ("invariant", "member"),
    "member_reference": ("invariant", "reference"),
    "family_header": ("family", "header"),
    "advertised_family": ("family", "advertised"),
    "chain_family": ("family", "chain"),
    "family_header_reference": ("family", "reference"),
}

# The record/entry kind one projected row names, by the subject's record
# kind. A row's ``kind``/``fact_kind`` describes the row's structural role
# (statement, registered realization); the subject's record kind names the
# record itself. Membership rows name the member record -- an invariant --
# with the family beside it, so ID, kind, state and location name one
# entity; membership itself rides the ``role`` beside the row, never the kind.
_RECORD_KINDS = {
    "invariant_revision": "invariant",
    "realization_claim": "realization",
    "family_revision": "family",
    "family": "family",
    "family_header": "family",
    "chain_family": "family",
    "family_member": "invariant",
    "decision_facet": "decision",
}

# Scope-continuation item kinds, to the same record vocabulary.
_ITEM_KINDS = {
    "invariant_revision": "invariant",
    "family_revision": "family",
    "family_membership": "invariant",
    "realization_claim": "realization",
    "advertised_family": "family",
}


def _row_kind_and_endpoint(
    row: dict[str, Any], subject: dict[str, Any]
) -> tuple[str | None, str | None, tuple[str, str] | None]:
    """One row's subject record kind, projected kind and structural endpoint.

    A structural row names its record (MIK-R109 rule 2): the kind is the
    record's, the endpoint rides the role beside the row so shared IDs
    stay distinct.
    """

    record_kind = subject.get("record_kind")
    kind = _RECORD_KINDS.get(record_kind) if isinstance(record_kind, str) else None
    row_kind = row.get("kind")
    endpoint = _STRUCTURAL_ROLES.get(row_kind) if isinstance(row_kind, str) else None
    if kind is None:
        kind = endpoint[0] if endpoint is not None else row_kind
        if not isinstance(kind, str):
            fact_kind = row.get("fact_kind")
            kind = fact_kind if isinstance(fact_kind, str) else None
    return record_kind, kind, endpoint


def _project_row(
    row: Any,
    resolver: NameResolver,
    states: dict[str, str],
    fallback: str | None = None,
) -> dict[str, Any]:
    """Project one page row to ID/kind/state/location."""

    if not isinstance(row, dict):
        return {"id": None, "kind": "unreadable", "state": None, "location": None}
    subject = row.get("subject")
    if not isinstance(subject, dict):
        subject = {}
    record_kind, kind, endpoint = _row_kind_and_endpoint(row, subject)
    text_id = _row_text_id(row, subject, resolver)
    role: str | None = None
    member_of: str | None = None
    if record_kind == "family_member":
        # A membership names its member record consistently: the member's
        # own text ID, kind and state, with the family riding beside it.
        member_text, family_text = _membership_endpoints(row, subject, resolver)
        if member_text is not None:
            text_id = member_text
        role = "member"
        member_of = family_text
    elif endpoint is not None:
        role = endpoint[1]
    state = _row_state(row, text_id, states, fallback)
    location = _row_location(row, text_id, resolver, member_of)
    projected = {"id": text_id, "kind": kind, "state": state, "location": location}
    if role is not None:
        projected["role"] = role
    if member_of is not None:
        projected["memberOf"] = member_of
    return projected


def _membership_endpoints(
    row: dict[str, Any], subject: dict[str, Any], resolver: NameResolver
) -> tuple[str | None, str | None]:
    """The member and family text IDs one membership row joins, if resolvable.

    Candidates are every UUID the row carries: the member revision travels
    in the row's statement on leaf rows and as ``member_id``,
    ``member_revision_id``, ``invariant_id`` or ``invariant_revision_id`` on
    scope items; the family revision travels in the subject or as
    ``family_id``/``family_revision_id``. Each is resolved through the bound
    index and classified by its text prefix, so the endpoints name records
    even when the row's own ID is composite.
    """

    member_text: str | None = None
    family_text: str | None = None
    candidates: list[Any] = [
        row.get("statement"),
        row.get("member_id"),
        row.get("member_revision_id"),
        row.get("invariant_id"),
        row.get("invariant_revision_id"),
        row.get("item_id"),
        subject.get("record_id"),
        subject.get("revision_id"),
        subject.get("family_id"),
        subject.get("family_revision_id"),
    ]
    for candidate in candidates:
        if not isinstance(candidate, str) or _looks_like_text_id(candidate):
            continue
        try:
            resolved = resolver.text_id(candidate)
        except Exception:
            continue
        if not isinstance(resolved, str):
            continue
        bare = _bare_text_id(resolved)
        head = bare.split("/", 1)[0]
        tail = bare.rsplit("/", 1)[-1]
        if head.split("-", 1)[0] == "FAM" and family_text is None:
            family_text = head
        if tail.split("-", 1)[0] == "INV" and member_text is None:
            member_text = tail
    return member_text, family_text


def _resolve_item_text_id(
    item: dict[str, Any], keys: tuple[str, ...], resolver: NameResolver
) -> str | None:
    """One item's text ID from UUID-or-text fields: a text-looking value wins as-is."""

    for key in keys:
        value = item.get(key)
        if not isinstance(value, str):
            continue
        if _looks_like_text_id(value):
            return _bare_text_id(value)
        try:
            resolved = resolver.text_id(value)
        except Exception:
            continue
        if resolved is not None:
            return _bare_text_id(resolved)
    return None


def _membership_of(item: dict[str, Any], resolver: NameResolver) -> tuple[str | None, str | None]:
    """A membership's (member text ID, family text ID): the member's own ID with the family beside it."""

    member_text, family_text = _membership_endpoints(item, item, resolver)
    member_of = family_text
    if member_of is None:
        family_value = item.get("family_revision_id")
        if isinstance(family_value, str) and not _looks_like_text_id(family_value):
            try:
                resolved_family = resolver.text_id(family_value)
            except Exception:
                resolved_family = None
            if resolved_family is not None:
                member_of = _bare_text_id(resolved_family)
    return member_text, member_of


def _project_item(
    item: Any,
    resolver: NameResolver,
    states: dict[str, str],
    fallback: str | None = None,
) -> dict[str, Any]:
    """Project one scope-continuation item to ID/kind/state/location (no prose)."""

    if not isinstance(item, dict):
        return {"id": None, "kind": "unreadable", "state": None, "location": None}
    raw_kind = item.get("kind")
    kind = _ITEM_KINDS.get(raw_kind, raw_kind) if isinstance(raw_kind, str) else None
    text_id: str | None = None
    if kind in ("realization", "proof"):
        # An entry names its own record: the claim/entry UUID resolves to
        # the RLZ-/PRF- text ID, never the invariant it realizes or proves.
        text_id = _resolve_item_text_id(item, ("claim_id", "entry_id", "proof_id"), resolver)
    if text_id is None:
        text_id = _resolve_item_text_id(
            item,
            ("record_id", "invariant_id", "family_id", "member_id", "claim_id"),
            resolver,
        )
    member_of: str | None = None
    role: str | None = None
    if raw_kind == "family_membership":
        # A membership names its member record consistently: the member's
        # own text ID with the family riding beside it (never the family's).
        role = "member"
        member_text, member_of = _membership_of(item, resolver)
        if member_text is not None:
            text_id = member_text
    elif raw_kind == "advertised_family":
        # An advertised family names the family record; the advertised
        # endpoint rides the role beside the row.
        role = "advertised"
    state = _row_state(item, text_id, states, fallback)
    projected = {"id": text_id, "kind": kind, "state": state, "location": None}
    if text_id is not None:
        projected["location"] = _row_location(item, text_id, resolver, member_of)
    if role is not None:
        projected["role"] = role
    if member_of is not None:
        projected["memberOf"] = member_of
    return projected


def _row_text_id(
    row: dict[str, Any], subject: dict[str, Any], resolver: NameResolver
) -> str | None:
    """The text ID one row names: its own, or its subject's resolved."""

    value = row.get("id")
    if isinstance(value, str):
        if _looks_like_text_id(value):
            return _bare_text_id(value)
        resolved = resolver.text_id(value)
        if resolved is not None:
            return _bare_text_id(resolved)
        return value
    for key in ("record_id", "revision_id"):
        value = subject.get(key)
        if isinstance(value, str):
            if _looks_like_text_id(value):
                return _bare_text_id(value)
            resolved = resolver.text_id(value)
            if resolved is not None:
                return _bare_text_id(resolved)
    return None


def _bare_text_id(value: str) -> str:
    """One text ID without its ``@revision`` suffix, when it carries one."""

    if "@" in value and value.split("@", 1)[0].split("-", 1)[0] in (
        "INV",
        "FAM",
        "DEC",
        "ASM",
        "REQ",
        "RLZ",
        "PRF",
    ):
        return value.split("@", 1)[0]
    return value


def _looks_like_text_id(value: str) -> bool:
    """Whether a string is already a text ID rather than a projected UUID."""

    if "@" in value:
        return True
    return bool(
        value
        and "-" in value
        and value.split("-", 1)[0] in ("INV", "FAM", "DEC", "ASM", "REQ", "RLZ", "PRF")
    )


def _row_state(
    row: dict[str, Any],
    text_id: str | None,
    states: dict[str, str],
    fallback: str | None = None,
) -> str | None:
    """The computed state one row carries: its own, or the walk's for it."""

    state = row.get("state")
    if isinstance(state, str):
        return state
    assessment = row.get("assessment_status")
    if isinstance(assessment, str):
        return assessment
    if text_id is not None:
        # States are keyed by bare text ID: a revision-suffixed family or
        # membership names the same record its bare ID does.
        for key in (text_id, text_id.split("@", 1)[0]):
            if key in states:
                return states[key]
        # A membership names its family before its member; the member's own
        # state answers for the row, so its segment wins over the family's.
        for segment in reversed(text_id.split("/")):
            if segment in states:
                return states[segment]
    return fallback


def _resolve_rel(resolver: NameResolver, text_id: str, *methods: str) -> str | None:
    """One record lookup that never raises: the relative path or ``None``."""

    for method in methods:
        if not hasattr(resolver, method):
            continue
        try:
            found = getattr(resolver, method)(text_id)
        except Exception:
            continue
        if found is not None:
            return found
    return None


def _locate(resolver: NameResolver, rel: str) -> str:
    """The emitted file for one memory-relative path (MIK-R109 rule 2).

    A caller reading its own memory worktree joins the answered absolute
    ``memoryTree.memoryRoot`` with this relative path, so large answers stay within
    their bound on any root length. A caller with no memory worktree
    (a bare checkout) keeps the absolute file with the commit beside the
    answer: only a worktree-relative emission is shortened.
    """

    if getattr(resolver, "relative_locations", False):
        return rel
    return str(resolver.memory_root / rel)


def _entry_location(
    resolver: NameResolver, memory_root: Any, entry_mark: str | None
) -> dict[str, Any] | None:
    """One entry row's claim file with its declaration line, if resolvable."""

    if entry_mark is None or not hasattr(resolver, "entry_file"):
        return None
    try:
        entry_rel = resolver.entry_file(entry_mark)
    except Exception:
        entry_rel = None
    if entry_rel is None or memory_root is None:
        return None
    return {
        "file": _locate(resolver, entry_rel),
        "line": resolver.line_of(entry_rel, entry_mark),
    }


def _segment_record_file(resolver: NameResolver, text_id: str) -> tuple[str | None, str]:
    """One composite ID's member-segment record file: the member's own record wins."""

    record_file: str | None = None
    if "/" in text_id and hasattr(resolver, "record_file"):
        # A membership names its family before its member; the member's own
        # record holds the member's text, so its segment wins over the family's.
        for segment in reversed(text_id.split("/")):
            try:
                record_file = resolver.record_file(segment)
            except Exception:
                record_file = None
            if record_file is not None:
                text_id = segment
                break
    return record_file, text_id


def _member_location(
    resolver: NameResolver,
    text_id: str,
    member_of: str,
    memory_root: Path | None,
) -> dict[str, Any] | None:
    """The file declaring one family member: its own record, then the family list.

    The member's own declaration wins; the family's member list is only the
    fallback when the member's own record is not resolvable.
    """

    member_rel = _resolve_rel(resolver, text_id, "record_path", "record_file")
    if member_rel is None:
        member_rel, _ = _segment_record_file(resolver, text_id)
    if member_rel is not None:
        line = resolver.line_of(member_rel, text_id)
        return {
            "file": _locate(resolver, member_rel),
            "line": line,
        }
    family_rel = _resolve_rel(resolver, member_of, "record_path", "record_file")
    if family_rel is not None and memory_root is not None:
        line = resolver.line_of(family_rel, text_id)
        return {
            "file": _locate(resolver, family_rel),
            "line": line,
        }
    return None


def _row_location(
    row: dict[str, Any],
    text_id: str | None,
    resolver: NameResolver,
    member_of: str | None = None,
) -> dict[str, Any] | None:
    """The memory-checkout file declaring one row, with the line or heading.

    A membership locates its member record's own declaration, falling back
    to the family's member list only when the member's own record is not
    resolvable; every other row locates its own record's declaration.
    """

    if text_id is None:
        return None
    memory_root = getattr(resolver, "memory_root", None)
    if member_of is not None and memory_root is not None:
        member_found = _member_location(resolver, text_id, member_of, memory_root)
        if member_found is not None:
            return member_found
    entry_mark = _entry_mark(text_id)
    entry_location = _entry_location(resolver, memory_root, entry_mark)
    if entry_location is not None:
        return entry_location
    record_file: str | None = _resolve_rel(resolver, text_id, "record_path", "record_file")
    if record_file is None:
        record_file, text_id = _segment_record_file(resolver, text_id)
    if record_file is not None and memory_root is not None:
        line = resolver.line_of(record_file, entry_mark or text_id)
        return {
            "file": _locate(resolver, record_file),
            "line": line,
        }
    card = _citing_card(row, resolver)
    if card is not None and memory_root is not None:
        card_rel, heading, fell_back = card
        located: dict[str, Any] = {
            "file": _locate(resolver, card_rel),
            "heading": heading,
        }
        if fell_back:
            # An unresolved title fallback is not an exact section: the
            # location says so instead of presenting the title as resolved.
            located["titleFallback"] = True
        return located
    return None


def _entry_mark(text_id: str) -> str | None:
    """The entry ID pinpointing one entry row inside its claim file, if any.

    Only entry text IDs (``RLZ-``/``PRF-``) are searched: a projected UUID
    never occurs in a record file, so searching one would only miss. Takes
    the resolved text ID, because raw rows carry UUID subjects, not text.
    """

    segment = text_id.rsplit("/", 1)[-1]
    if segment.split("-", 1)[0] in ("RLZ", "PRF"):
        return segment
    return None


def _citing_card(row: dict[str, Any], resolver: NameResolver) -> tuple[str, str, bool] | None:
    """The card citing one entry row's anchor, its heading, and title fallback.

    The row's recorded anchor identity (entry id, path, blob, content)
    rides into scoring, so two records in one file under distinct sections
    resolve to their own anchors rather than the first path-matching
    section. Without recorded identity the paths alone score path matches.
    The fallback flag tells whether the heading is the card's title
    because no section scored, so locations stay honest about unresolved
    sections.
    """

    paths = {row["path"]} if isinstance(row.get("path"), str) else set()
    locator = row.get("locator")
    if isinstance(locator, dict) and isinstance(locator.get("path"), str):
        paths.add(locator["path"])
    blob = row.get("recordedBlob")
    content = row.get("recordedContent")
    if blob is None and content is None and isinstance(locator, dict):
        blob = locator.get("blob")
        content = locator.get("content")
    entry_id = row.get("id") if isinstance(row.get("id"), str) else None
    wanted = [
        (
            entry_id,
            path,
            blob if isinstance(blob, str) else None,
            content if isinstance(content, str) else None,
        )
        for path in sorted(paths)
    ]
    for code_path in sorted(paths):
        card_rel = f"onboarding/{code_path}.md"
        card_path = resolver.memory_root / card_rel
        if not card_path.is_file():
            continue
        card_text: str | None = resolver.read_text(card_rel)
        title = card_rel
        for line in (card_text or "").splitlines():
            if line.startswith("# "):
                title = line[2:].strip()
                break
        references = resolver.card_references(card_rel)
        heading, fell_back = _select_section(
            card_text,
            {code_path},
            title or card_rel,
            SectionAnchors(
                references=references,
                own_path=code_path,
                wanted=[entry for entry in wanted if entry[1] == code_path] or None,
            ),
        )
        return card_rel, heading, fell_back
    return None


def _line_of(path: Path, mark: str) -> int | None:
    """The 1-based line declaring ``mark`` in ``path``, if any.

    The line with the JSON ``"id"`` field wins over an earlier prose
    mention: opening the location retrieves the declaration, not a
    history note that merely names the ID. The field match tolerates any
    JSON spacing (pretty, canonical or compact), so a spacing variant
    still resolves to the declaration rather than falling back to a
    mention.
    """

    try:
        text = filesystem.read_text(path)
    except (UnicodeDecodeError, OSError, ValueError):
        return None
    field = re.compile(r"['\"]id['\"]\s*:\s*['\"]" + re.escape(mark) + r"['\"]")
    lines = text.splitlines()
    for number, line in enumerate(lines, start=1):
        if field.search(line):
            return number
    for number, line in enumerate(lines, start=1):
        if mark in line:
            return number
    return None


def _proof_state(proof_id: str | None, states: dict[str, str]) -> str | None:
    """One proof's state: its own entry first, then its path segments."""

    if proof_id is None:
        return None
    state = states.get(proof_id)
    if state is None:
        for segment in reversed(proof_id.split("/")):
            if segment in states:
                state = states[segment]
                break
    return state


def _proof_code_location(proof: dict[str, Any]) -> dict[str, Any]:
    """The source test a proof exercises, separate from the record location."""

    code_location: dict[str, Any] = {}
    if isinstance(proof.get("path"), str):
        code_location["file"] = proof["path"]
    anchor = proof.get("anchor")
    if isinstance(anchor, dict):
        code_location["anchor"] = anchor
    return code_location


def _project_proof(
    proof: dict[str, Any], states: dict[str, str], resolver: NameResolver | None = None
) -> dict[str, Any]:
    """Project one proof entry to ID/kind/state/invariant/location (no facet text).

    The caller cites the proof for its invariant's claim and opens the
    named memory record: the invariant stays as the record ID, and the
    location names the proof's memory sidecar with its declaration line.
    The code test the proof exercises rides a separate ``codeLocation``:
    a caller without the source checkout still opens the record.
    """

    raw_id = proof.get("id") if isinstance(proof.get("id"), str) else None
    proof_id = _resolve_text_id(raw_id, resolver)
    state = _proof_state(proof_id, states)
    projected: dict[str, Any] = {"id": proof_id, "kind": "proof", "state": state}
    invariant = proof.get("invariant")
    if isinstance(invariant, str):
        projected["invariant"] = invariant
    location: dict[str, Any] = {}
    if proof_id is not None and resolver is not None:
        record = _row_location({}, proof_id, resolver)
        if record is not None:
            location.update(record)
    code_location = _proof_code_location(proof)
    if code_location:
        projected["codeLocation"] = code_location
    if location:
        projected["location"] = location
    return projected


def _resolve_text_id(value: str | None, resolver: NameResolver | None) -> str | None:
    """One UUID or text ID as the bare text ID it names, if resolvable."""

    if not isinstance(value, str):
        return None
    if _looks_like_text_id(value):
        return _bare_text_id(value)
    if resolver is None:
        return value
    try:
        resolved = resolver.text_id(value)
    except Exception:
        return value
    return _bare_text_id(resolved) if resolved is not None else value


def _project_family(
    entry: dict[str, Any],
    resolver: NameResolver,
    states: dict[str, str],
    fallback: str | None = None,
) -> dict[str, Any]:
    """Project one family header to ID/kind/state/location (no title text).

    A family the walk never evaluated carries the read-wide failure
    state when the walk failed, else no state: null names an unmeasured
    family, never a current one.
    """

    family_id = entry.get("id") if isinstance(entry.get("id"), str) else None
    location = _row_location(entry, family_id, resolver)
    state = states.get(family_id) if family_id else None
    if state is None:
        state = fallback
    return {
        "id": family_id,
        "kind": "family",
        "state": state,
        "location": location,
    }


def _project_reference(
    header: dict[str, Any],
    resolver: NameResolver,
    states: dict[str, str] | None = None,
    fallback: str | None = None,
) -> dict[str, Any]:
    """Project one page header reference to ID/kind/state/location (no title text).

    A header reference names a family: its kind is the family's, and its
    state is the family's evaluated state, the read-wide failure state,
    or null for an unmeasured family -- never the row's structural role.
    """

    family = header.get("id") if isinstance(header.get("id"), str) else None
    if family is None:
        family = header.get("family") if isinstance(header.get("family"), str) else None
    if family is None:
        revision = header.get("family_revision_id")
        try:
            family = resolver.text_id(revision) if isinstance(revision, str) else None
        except Exception:
            family = None
        if isinstance(family, str):
            family = family.split("@", 1)[0]
    location = _row_location(header, family, resolver)
    state: str | None = None
    if family is not None and states is not None:
        state = states.get(family)
        if state is None:
            for key in (family.split("@", 1)[0], *reversed(family.split("/"))):
                if key in states:
                    state = states[key]
                    break
    if state is None:
        state = fallback
    return {
        "kind": "family",
        "id": family,
        "family": family,
        "state": state,
        "location": location,
    }


__all__ = ["NameResolver", "project_knowledge_body"]
