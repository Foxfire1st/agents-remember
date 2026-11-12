"""The target comparison's own answers, read from the bound trees (MIK-R187 rules 4 and 8).

A staleness read asks one question of one comparison: does this basis still resolve *there*? This
module answers it from the bytes Git holds and from the task's own records:

* a range's ``sha256:`` at a tree, and the same range as it maps into the target's tree of the same
  side -- through :func:`map_range` over the zero-context hunks of the two blobs, exactly as the
  writer maps a ``line_range`` (rule 8's ``tree-blob`` row);
* a memory record's revision at the side a claim names, read from the record file at that tree;
* a proof entry's fingerprint and its recorded execution state, from the sidecar that carries it;
* the ``(row id, attempt)`` pairs of the memory candidate's judgment history, and one history
  file's row ids;
* the target packet's supplemental receipts, and the requirements manifest's registered version of
  a packet (MIK-R102 v2).

**Every method answers ``None`` for "this could not be read".** That is the rule's failure clause: a
blob the tree does not hold, a side that does not resolve, a record file that does not parse, an
unreadable history listing -- each makes its basis stale with that reason, never silently current.
A missing *history file* is the one measured absence: it holds no rows, so it answers ``()``, which
is a stale row rather than an unread file.

**Nothing here writes.** The reads are Git object reads (``cat-file``, ``rev-parse``, ``ls-tree`` and
a blob diff) through the kernel's runner, which sets ``GIT_OPTIONAL_LOCKS=0``. In particular this
module does not build a knowledge index: an index is a cache written under the coordination root,
and a staleness read may not write. A record is read as the file a claim's basis names, which is the
stronger authority anyway.

The judgment rows, history rows and proof entries are read at the **memory candidate**, because rule
8 names no side for those rows and the packet that prepared their bases read them there. A basis
that carries a side of its own (``tree-blob``, ``knowledge-record``) is read at the side it names.

Two limits are stated here rather than hidden: a record is read at the tree the *target* names, so
on a converted memory base the file is looked for in the converted tree, and a path that tree does
not hold answers "no record" -- stale, never current; and :meth:`registered_version` answers only a
strictly later version from ``requirements/manifest.json``, so a manifest that does not name the
source registers nothing.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agents_remember.application.knowledge_worklist.code import (
    BLOB_DIFF_ARGS,
    Hunk,
    map_range,
    parse_hunks,
)
from agents_remember.canvas.explainer_access import object_name, tree_path
from agents_remember.canvas.explainer_staleness import (
    BlockFingerprint,
    BlockIdentity,
    CardSide,
    HunkSides,
    LocatorKind,
    ProofEntryRead,
    RecordKind,
    card_fingerprint,
    file_fingerprint,
    parse_block_identity,
    record_fingerprint,
)
from agents_remember.canvas.explainer_state import ExplainerRefused
from agents_remember.kernel.git_command import (
    GIT_METADATA_TIMEOUT_SECONDS,
    GitRunnerOptions,
    run_git,
    run_git_bytes,
)
from agents_remember.memory_quality.style.citations import extents, grammars
from agents_remember.models.knowledge.review_evidence import (
    ExecutionBasis,
    SupplementalReceipt,
)
from agents_remember.models.knowledge.review_frame import FrameBinding
from agents_remember.models.knowledge_files.anchor_content import (
    RangeOutsideBlobError,
    content_identity,
    line_count,
    range_bytes,
)
from agents_remember.models.knowledge_files.unexplained import ABSENT

__all__ = [
    "ExplainerTargetReads",
    "binding_fingerprint_reader",
    "proof_entry_fingerprint",
    "source_version",
]

#: Where a memory side keeps the rows rule 8 calls ``judgment-row`` and ``history-event``.
HISTORY_ROOT = "knowledge/history/"
#: Where invariant and family records live in a converted memory tree.
KNOWLEDGE_ROOT = "knowledge/"
#: Where the proof entries a claim may rest on are recorded (``onboarding/<path>.json``).
ONBOARDING_ROOT = "onboarding/"
REQUIREMENTS_MANIFEST = ("requirements", "manifest.json")

_MEMORY_SIDE = "memory-candidate"
_MEMORY_SIDES = ("memory-base", "memory-candidate")
_EXECUTIONS: dict[str, ExecutionBasis] = {
    "established": "established",
    "claimed": "claimed",
    "none": "none",
}

_SIDE_REPOSITORIES: dict[str, str] = {
    "code-base": "code_base_repository",
    "code-candidate": "code_candidate_repository",
    "memory-base": "memory_base_repository",
    "memory-candidate": "memory_candidate_repository",
}


def source_version(source: str) -> tuple[str, int] | None:
    """The stable id and version one canonical source's file name carries, or ``None``.

    ``requirements/MIK-R187-v1-judgment-categories.md`` is ``("MIK-R187", 1)``: the same reading
    :func:`agents_remember.application.review_supplemental_evidence.packet_version_stale` takes, so
    the two cannot disagree about which version a receipt examined.
    """

    stem = source.rsplit("/", 1)[-1]
    stable_id, separator, rest = stem.partition("-v")
    if not separator or not stable_id:
        return None
    version = rest.split("-", 1)[0]
    return (stable_id, int(version)) if version.isdigit() else None


def proof_entry_fingerprint(
    entry: Mapping[str, Any], *, anchored_content: str | None = None
) -> str:
    """One proof entry's fingerprint: ``sha256:`` over its recorded content and anchored test bytes.

    When the entry carries a native ``anchor``, the anchored test's content identity replaces the
    sidecar's raw blob and line coordinates so a same-content shift and re-anchor keeps the same
    fingerprint while an un-updated sidecar over changed test code changes it (MIK-R187 rules 3, 8).
    """

    normalized = dict(entry)
    anchor = normalized.get("anchor")
    if isinstance(anchor, Mapping):
        locator = anchor.get("locator")
        kind = locator.get("kind") if isinstance(locator, Mapping) else None
        content = anchored_content if anchored_content is not None else anchor.get("content")
        normalized["anchor"] = {"locator_kind": kind, "content": content}
    elif anchored_content is not None:
        normalized["anchored_content"] = anchored_content
    payload = json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode()
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


@dataclass
class ExplainerTargetReads:
    """The real :class:`TargetReads` over one target comparison's four bound trees.

    ``supplemental`` is the target packet's own receipts, or ``None`` when the caller could not read
    that packet; ``task_root`` is the task whose requirements manifest registers packet versions.
    Both are read-only inputs. Every cache is in memory and never outlives the reader: a second call
    answers the same bytes, and no file and no index is written between them.
    """

    binding: FrameBinding
    task_root: Path | None = None
    supplemental: tuple[SupplementalReceipt, ...] | None = None
    _blobs: dict[tuple[str, str], bytes | None] = field(default_factory=dict, repr=False)
    _listings: dict[tuple[str, str, str], tuple[str, ...] | None] = field(
        default_factory=dict, repr=False
    )
    _hunks: dict[tuple[str, str, str], tuple[Hunk, ...] | None] = field(
        default_factory=dict, repr=False
    )
    _judgments: dict[str, tuple[tuple[str, int], ...] | None] = field(
        default_factory=dict, repr=False
    )
    _proofs: dict[tuple[str, str], tuple[dict[str, Any], str]] | None = field(
        default=None, repr=False
    )
    _sidecar_entries: dict[str, dict[str, tuple[dict[str, Any], str]]] = field(
        default_factory=dict, repr=False
    )
    _records_map: dict[str, dict[str, tuple[str, int]]] = field(default_factory=dict, repr=False)
    _manifest: dict[str, Any] | None = field(default=None, repr=False)
    _manifest_read: bool = field(default=False, repr=False)

    # --- rule 8's tree-blob row ---------------------------------------------------------------

    def content_identity(
        self, *, side: str, tree: str, path: str, lines: tuple[int, int] | None
    ) -> str | None:
        """The hash of ``path``'s bytes at ``tree``, over ``lines`` or the whole blob."""

        repository = self._repository(side)
        data = self._blob(repository, tree, path)
        if data is None:
            return None
        if lines is None:
            return content_identity(data)
        try:
            return content_identity(range_bytes(data, *lines))
        except RangeOutsideBlobError:
            return None

    def mapped_lines(
        self,
        *,
        side: str,
        path: str,
        from_tree: str,
        to_tree: str,
        lines: tuple[int, int],
    ) -> tuple[int, int] | None:
        """``lines`` read at ``from_tree``, as the same lines in ``to_tree``; ``None`` unmappable."""

        if from_tree == to_tree:
            return lines
        repository = self._repository(side)
        if repository is None:
            return None
        before = self._blob_id(repository, from_tree, path)
        after = self._blob_id(repository, to_tree, path)
        if before is None or after is None:
            return None
        if before == after:
            return lines
        hunks = self._hunks_between(repository, before, after)
        if hunks is None:
            return None
        return map_range(hunks, lines)

    # --- rule 8's knowledge-record row ---------------------------------------------------------

    def memory_revision(self, *, side: str, record: str, path: str) -> int | None:
        """The revision one memory side holds for ``record`` at ``path``, or ``None`` for none."""

        if side not in _MEMORY_SIDES:
            return None
        repository = self._repository(side)
        document = self._json(repository, self._tree_for(side), path)
        if not isinstance(document, dict) or document.get("id") != record:
            return None
        revision = document.get("revision")
        if not isinstance(revision, int) or isinstance(revision, bool):
            return None
        return revision

    # --- rule 8's proof-entry row --------------------------------------------------------------

    def proof_entry(self, *, proof: str, record: str) -> ProofEntryRead | None:
        """One proof entry as the target reads it, or ``None`` when it no longer resolves."""

        entries = self._proof_entries()
        if entries is None:
            return None
        found = entries.get((proof, record))
        if found is None:
            return None
        entry, source_path = found
        anchor = entry.get("anchor")
        anchored_content: str | None = None
        if isinstance(anchor, Mapping) and source_path:
            anchored_content, _span = self._resolve_anchor_in_code(
                "code-candidate", source_path, anchor
            )
            if anchored_content is None:
                return ProofEntryRead(
                    fingerprint=None,
                    execution=_execution(entry, anchored_content=None),
                )
        return ProofEntryRead(
            fingerprint=proof_entry_fingerprint(entry, anchored_content=anchored_content),
            execution=_execution(entry, anchored_content=anchored_content),
        )

    # --- rule 8's judgment-row and history-event rows ------------------------------------------

    def judgment_rows(self, *, record: str) -> tuple[tuple[str, int], ...] | None:
        """Every ``(row id, attempt)`` one judgment record holds at the target."""

        if record in self._judgments:
            return self._judgments[record]
        repository = self._repository(_MEMORY_SIDE)
        tree = self._tree_for(_MEMORY_SIDE)
        names = self._listing(repository, tree, HISTORY_ROOT)
        if names is None:
            return None
        rows: list[tuple[str, int]] = []
        for name in names:
            document = self._json(repository, tree, name)
            if not isinstance(document, dict):
                return None
            rows.extend(_rows_of(document, record))
        self._judgments[record] = tuple(rows)
        return self._judgments[record]

    def history_rows(self, *, path: str) -> tuple[str, ...] | None:
        """Every row id one history file holds at the target."""

        repository = self._repository(_MEMORY_SIDE)
        tree = self._tree_for(_MEMORY_SIDE)
        names = self._listing(repository, tree, HISTORY_ROOT)
        if names is None:
            return None
        if path not in names:
            return ()
        document = self._json(repository, tree, path)
        if not isinstance(document, dict):
            return None
        rows = document.get("rows")
        if not isinstance(rows, list):
            return None
        return tuple(str(row["id"]) for row in rows if isinstance(row, dict) and row.get("id"))

    # --- rule 8's supplemental-receipt row -----------------------------------------------------

    def receipts(self) -> tuple[SupplementalReceipt, ...] | None:
        """The target packet's supplemental receipts, or ``None`` when that packet was not read."""

        return self.supplemental

    def registered_version(self, *, source: str) -> int | None:
        """The later version the manifest registers for ``source``, or ``None`` for none."""

        named = source_version(source)
        if named is None:
            return None
        registered = _registered_version(self._requirements_manifest(), named[0])
        if registered is None:
            return None
        return registered if registered > named[1] else None

    # --- the trees and the bytes ---------------------------------------------------------------

    def _tree_for(self, side: str) -> str | None:
        trees = {
            "code-base": self.binding.code_base_tree,
            "code-candidate": self.binding.code_candidate_tree,
            "memory-base": self.binding.compared_memory_base_tree,
            "memory-candidate": self.binding.memory_candidate_tree,
        }
        return trees.get(side)

    def _repository(self, side: str) -> Path | None:
        name = _SIDE_REPOSITORIES.get(side)
        if name is None:
            return None
        root = Path(getattr(self.binding, name))
        return root if root.is_dir() else None

    def _blob(self, repository: Path | None, tree: str | None, path: str) -> bytes | None:
        target = _object_path(tree, path)
        if repository is None or target is None:
            return None
        key = (repository.as_posix(), target)
        if key not in self._blobs:
            completed = run_git_bytes(
                repository,
                ["cat-file", "blob", target],
                GitRunnerOptions(timeout=GIT_METADATA_TIMEOUT_SECONDS),
            )
            self._blobs[key] = completed.stdout if completed.returncode == 0 else None
        return self._blobs[key]

    def _blob_id(self, repository: Path, tree: str, path: str) -> str | None:
        target = _object_path(tree, path)
        if target is None:
            return None
        completed = run_git(
            repository,
            ["rev-parse", "--verify", "--quiet", target],
            GitRunnerOptions(timeout=GIT_METADATA_TIMEOUT_SECONDS),
        )
        if completed.returncode != 0:
            return None
        return completed.stdout.strip() or None

    def _hunks_between(self, repository: Path, before: str, after: str) -> tuple[Any, ...] | None:
        key = (repository.as_posix(), before, after)
        if key not in self._hunks:
            completed = run_git(
                repository,
                [*BLOB_DIFF_ARGS, before, after],
                # Git's environment option overrides even --unified=0.
                GitRunnerOptions(
                    timeout=GIT_METADATA_TIMEOUT_SECONDS, identity={"GIT_DIFF_OPTS": ""}
                ),
            )
            self._hunks[key] = parse_hunks(completed.stdout) if completed.returncode == 0 else None
        return self._hunks[key]

    def _listing(
        self, repository: Path | None, tree: str | None, prefix: str
    ) -> tuple[str, ...] | None:
        if repository is None or tree is None:
            return None
        key = (repository.as_posix(), tree, prefix)
        if key not in self._listings:
            self._listings[key] = _tree_paths(repository, tree, prefix)
        return self._listings[key]

    def _json(self, repository: Path | None, tree: str | None, path: str) -> Any | None:
        if repository is None or tree is None:
            return None
        data = self._blob(repository, tree, path)
        if data is None:
            return None
        try:
            return json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None

    def _proof_entries(self) -> dict[tuple[str, str], tuple[dict[str, Any], str]] | None:
        """Every proof entry the memory candidate's JSON sidecars record, by ``(proof, record)``."""

        if self._proofs is not None:
            return self._proofs
        repository = self._repository(_MEMORY_SIDE)
        tree = self._tree_for(_MEMORY_SIDE)
        names = self._listing(repository, tree, ONBOARDING_ROOT)
        if names is None:
            return None
        found: dict[tuple[str, str], tuple[dict[str, Any], str]] = {}
        for name in names:
            if not name.endswith(".json"):
                continue
            document = self._json(repository, tree, name)
            if not isinstance(document, dict):
                return None
            source_path = str(
                document.get("path") or name.removeprefix(ONBOARDING_ROOT).removesuffix(".json")
            )
            for entry in _proves(document):
                record = str(entry.get("invariant") or "")
                identifier = str(entry.get("id") or f"{name}#{record}")
                found.setdefault((identifier, record), (entry, source_path))
        self._proofs = found
        return found

    def _blob_exists(self, repository: Path | None, blob_id: str) -> bool:
        if repository is None or not blob_id:
            return False
        try:
            name = object_name(blob_id)
        except ExplainerRefused:
            return False
        completed = run_git(
            repository,
            ["cat-file", "-t", name],
            GitRunnerOptions(timeout=GIT_METADATA_TIMEOUT_SECONDS),
        )
        return completed.returncode == 0 and completed.stdout.strip() == "blob"

    def _resolve_anchor_in_code(
        self, side: str, path: str, anchor: Mapping[str, Any]
    ) -> tuple[str | None, tuple[int, int] | None]:
        """Resolve an anchor's locator in ``side``'s code tree at ``path``."""

        locator = anchor.get("locator")
        repository = self._repository(side)
        tree = self._tree_for(side)
        data = self._blob(repository, tree, path)
        blob_id = self._blob_id(repository, tree, path) if repository and tree else None
        if not isinstance(locator, Mapping) or data is None or blob_id is None:
            return None, None
        kind = locator.get("kind")
        if kind == "file":
            return content_identity(data), (1, max(line_count(data), 1))
        if kind == "symbol":
            return _resolve_symbol_in_blob(path, data, str(locator.get("name") or ""))
        if kind == "line_range":
            return self._resolve_line_range_in_code(repository, data, blob_id, anchor, locator)
        return None, None

    def _resolve_line_range_in_code(
        self,
        repository: Path | None,
        data: bytes,
        blob_id: str,
        anchor: Mapping[str, Any],
        locator: Mapping[str, Any],
    ) -> tuple[str | None, tuple[int, int] | None]:
        start, end = locator.get("start"), locator.get("end")
        if not isinstance(start, int) or not isinstance(end, int):
            return None, None
        recorded_blob = str(anchor.get("blob") or "")
        span: tuple[int, int] | None = (start, end)
        if recorded_blob and recorded_blob != blob_id:
            if repository is None or not self._blob_exists(repository, recorded_blob):
                return None, None
            hunks = self._hunks_between(repository, recorded_blob, blob_id)
            span = map_range(hunks, (start, end)) if hunks is not None else None
        if span is None:
            return None, None
        try:
            return content_identity(range_bytes(data, *span)), span
        except RangeOutsideBlobError:
            return None, None

    # --- rule 3: block fingerprints and target hunks -------------------------------------------

    def file_hunks(self, path: str) -> tuple[HunkSides, ...] | None:
        """Every diff hunk of ``path`` between ``code-base`` and ``code-candidate``, or ``None`` unread."""

        base_repo = self._repository("code-base")
        cand_repo = self._repository("code-candidate")
        base_tree = self._tree_for("code-base")
        cand_tree = self._tree_for("code-candidate")
        if base_repo is None or cand_repo is None or base_tree is None or cand_tree is None:
            return None
        if self._listing(cand_repo, cand_tree, "") is None:
            return None
        base_bytes = self._blob(base_repo, base_tree, path)
        cand_bytes = self._blob(cand_repo, cand_tree, path)
        if base_bytes is None and cand_bytes is None:
            return ()
        if base_bytes == cand_bytes:
            return ()
        trees = (base_tree, cand_tree)
        raw_hunks = self._diff_file_hunks(cand_repo, trees, path, (base_bytes, cand_bytes))
        if raw_hunks is None:
            return None
        built: list[HunkSides] = []
        for hunk in raw_hunks:
            base_hash = _side_lines_hash(base_bytes, hunk.old_start, hunk.old_count)
            cand_hash = _side_lines_hash(cand_bytes, hunk.new_start, hunk.new_count)
            if base_hash is None or cand_hash is None:
                continue
            built.append(
                HunkSides(
                    path=path,
                    base=base_hash,
                    candidate=cand_hash,
                    base_span=(hunk.old_start, hunk.old_count),
                    candidate_span=(hunk.new_start, hunk.new_count),
                    trees=trees,
                )
            )
        return tuple(built)

    def _diff_file_hunks(
        self,
        repository: Path,
        trees: tuple[str, str],
        path: str,
        blobs: tuple[bytes | None, bytes | None],
    ) -> tuple[Hunk, ...] | None:
        base_tree, cand_tree = trees
        base_bytes, cand_bytes = blobs
        if base_bytes is None and cand_bytes is not None:
            return (
                Hunk(
                    old_start=0, old_count=0, new_start=1, new_count=max(line_count(cand_bytes), 1)
                ),
            )
        if cand_bytes is None and base_bytes is not None:
            return (
                Hunk(
                    old_start=1, old_count=max(line_count(base_bytes), 1), new_start=0, new_count=0
                ),
            )
        base_oid = self._blob_id(repository, base_tree, path)
        cand_oid = self._blob_id(repository, cand_tree, path)
        if base_oid is None or cand_oid is None:
            return None
        return self._hunks_between(repository, base_oid, cand_oid)

    def block_fingerprint(
        self, reference: str, *, source_binding: FrameBinding | None = None
    ) -> BlockFingerprint | None:
        """The fingerprint of ``reference`` at this comparison, or ``None`` when absent."""

        block = parse_block_identity(reference, binding=source_binding or self.binding)
        if block is None:
            block = self._infer_identity(reference)
        if block is None:
            return None
        if block.kind in {"invariant", "family"}:
            return self._record_block_fingerprint(block)
        if block.kind == "non-text-file":
            return self._file_block_fingerprint(block, reference)
        if block.kind in {"unexplained-hunk", "pure-deletion-hunk"}:
            return self._hunk_block_fingerprint(block, reference)
        return self._card_block_fingerprint(block, source_binding=source_binding)

    def resolve_fingerprints(self, references: Sequence[str]) -> dict[str, str]:
        """Production ``FingerprintReader`` over this binding's trees."""

        resolved: dict[str, str] = {}
        for reference in references:
            fp = self.block_fingerprint(reference)
            if fp is not None and fp.current and fp.value is not None:
                resolved[reference] = fp.value
        return resolved

    def _infer_identity(self, reference: str) -> BlockIdentity | None:
        if reference.startswith(("INV-", "FAM-")):
            kind: RecordKind = "invariant" if reference.startswith("INV-") else "family"
            return BlockIdentity(kind=kind, record=reference)
        entries = self._entries_for_side("memory-candidate") or {}
        if reference in entries:
            _entry, path = entries[reference]
            return BlockIdentity.card(
                entry=reference,
                side="after",
                path=path,
                lines=None,
                trees=self._four_trees(),
            )
        return None

    def _four_trees(self) -> tuple[str, str, str, str]:
        return (
            self.binding.code_base_tree,
            self.binding.code_candidate_tree,
            self.binding.compared_memory_base_tree,
            self.binding.memory_candidate_tree,
        )

    def _record_block_fingerprint(self, block: BlockIdentity) -> BlockFingerprint | None:
        kind: RecordKind = "invariant" if block.kind == "invariant" else "family"
        rec_id = block.record or ""
        records = self._records_for_side("memory-candidate")
        if records is None:
            return BlockFingerprint.uncomputable(block, "the memory tree cannot be read")
        found = records.get(rec_id)
        if found is None:
            base_records = self._records_for_side("memory-base")
            found = None if base_records is None else base_records.get(rec_id)
        if found is None:
            return None
        _rec_kind, revision = found
        return record_fingerprint(kind=kind, record=rec_id, revision=revision)

    def _file_block_fingerprint(
        self, block: BlockIdentity, reference: str
    ) -> BlockFingerprint | None:
        path = block.path or ""
        base_repo = self._repository("code-base")
        cand_repo = self._repository("code-candidate")
        base_tree = self._tree_for("code-base")
        cand_tree = self._tree_for("code-candidate")
        trees = (base_tree or "", cand_tree or "")
        if base_repo is None or cand_repo is None or base_tree is None or cand_tree is None:
            return file_fingerprint(path=path, blob=None, trees=trees, unread=True)
        if self._listing(cand_repo, cand_tree, "") is None:
            return file_fingerprint(path=path, blob=None, trees=trees, unread=True)
        base_blob = self._blob_id(base_repo, base_tree, path)
        cand_blob = self._blob_id(cand_repo, cand_tree, path)
        if base_blob is None and cand_blob is None:
            return None
        if base_blob == cand_blob and not reference.startswith("file:"):
            return None
        return file_fingerprint(
            path=path,
            blob=cand_blob,
            trees=trees,
            absent=(cand_blob is None),
        )

    def _hunk_block_fingerprint(
        self, block: BlockIdentity, reference: str
    ) -> BlockFingerprint | None:
        path = block.path or ""
        hunks = self.file_hunks(path)
        if hunks is None:
            return BlockFingerprint.uncomputable(block, "the code trees cannot be read")
        if not hunks:
            return None
        if reference.startswith("hunk:"):
            for item in hunks:
                if item.fingerprint().value == reference:
                    return item.fingerprint()
        matched = _match_hunk(block, hunks)
        return None if matched is None else matched.fingerprint()

    def _card_block_fingerprint(
        self, block: BlockIdentity, *, source_binding: FrameBinding | None = None
    ) -> BlockFingerprint | None:
        base_repo = self._repository("code-base")
        cand_repo = self._repository("code-candidate")
        base_tree = self._tree_for("code-base")
        cand_tree = self._tree_for("code-candidate")
        if base_repo is None or cand_repo is None or base_tree is None or cand_tree is None:
            return BlockFingerprint.uncomputable(block, "the code trees cannot be read")
        if self._listing(cand_repo, cand_tree, "") is None:
            return BlockFingerprint.uncomputable(block, "the code trees cannot be read")
        entry_id = block.entry or ""
        base_entries = self._entries_for_side("memory-base", hint_path=block.path)
        cand_entries = self._entries_for_side("memory-candidate", hint_path=block.path)
        base_found = None if base_entries is None else base_entries.get(entry_id)
        cand_found = None if cand_entries is None else cand_entries.get(entry_id)
        if base_found is not None or cand_found is not None:
            return self._sidecar_card_fingerprint(block, base_found, cand_found)
        return self._fallback_card_fingerprint(block, source_binding=source_binding)

    def _sidecar_card_fingerprint(
        self,
        block: BlockIdentity,
        base_found: tuple[dict[str, Any], str] | None,
        cand_found: tuple[dict[str, Any], str] | None,
    ) -> BlockFingerprint:
        active = cand_found if cand_found is not None else base_found
        assert active is not None
        active_entry, active_path = active
        anchor = active_entry.get("anchor") or {}
        locator = anchor.get("locator") or {}
        raw_kind = locator.get("kind")
        loc_kind: LocatorKind = (
            "symbol"
            if raw_kind == "symbol"
            else ("line_range" if raw_kind == "line_range" else "file")
        )
        base_side, base_span = self._resolve_card_side(
            "code-base",
            base_found or (active_entry, block.path or active_path),
            present=base_found is not None,
        )
        cand_side, cand_span = self._resolve_card_side(
            "code-candidate",
            cand_found or (active_entry, block.path or active_path),
            present=cand_found is not None,
        )
        chosen_side = (
            "before" if (cand_found is None and base_found is not None) else (block.side or "after")
        )
        chosen_span = base_span if chosen_side in {"before", "code-base"} else cand_span
        target_block = BlockIdentity.card(
            entry=block.entry or "",
            side=chosen_side,
            path=(cand_found or base_found or (active_entry, block.path or ""))[1],
            lines=chosen_span,
            trees=self._four_trees(),
        )
        return card_fingerprint(
            block=target_block, base=base_side, candidate=cand_side, locator=loc_kind
        )

    def _resolve_card_side(
        self, side: str, found: tuple[dict[str, Any], str], *, present: bool
    ) -> tuple[CardSide, tuple[int, int] | None]:
        entry, path = found
        repo = self._repository(side)
        tree = self._tree_for(side)
        blob = self._blob(repo, tree, path)
        if blob is None:
            return CardSide(absent=True), None
        anchor = entry.get("anchor")
        if isinstance(anchor, Mapping):
            hashed, span = self._resolve_anchor_in_code(side, path, anchor)
            if hashed is not None:
                return CardSide(content=hashed), span
            if not present:
                return CardSide(absent=True), None
            return CardSide(), None
        return CardSide(content=content_identity(blob)), (1, max(line_count(blob), 1))

    def _fallback_card_fingerprint(
        self, block: BlockIdentity, *, source_binding: FrameBinding | None = None
    ) -> BlockFingerprint | None:
        path = block.path or ""
        if not path:
            return None
        # If onboarding sidecars exist for this path on candidate and do not list the entry, it is gone.
        cand_doc = self._json(
            self._repository("memory-candidate"),
            self._tree_for("memory-candidate"),
            f"{ONBOARDING_ROOT}{path}.json",
        )
        if (
            isinstance(cand_doc, dict)
            and (cand_doc.get("realizes") or cand_doc.get("proves"))
            and block.entry
            and not block.entry.startswith("INV-")
        ):
            return None
        base_blob = self._blob(self._repository("code-base"), self._tree_for("code-base"), path)
        cand_blob = self._blob(
            self._repository("code-candidate"), self._tree_for("code-candidate"), path
        )
        if base_blob is None and cand_blob is None:
            return None
        if block.start_line is not None and block.end_line is not None:
            return self._range_card_fingerprint(block, path, base_blob, cand_blob, source_binding)
        base_side = (
            CardSide(absent=True)
            if base_blob is None
            else CardSide(content=content_identity(base_blob))
        )
        cand_side = (
            CardSide(absent=True)
            if cand_blob is None
            else CardSide(content=content_identity(cand_blob))
        )
        target_block = BlockIdentity.card(
            entry=block.entry or "",
            side=block.side or "after",
            path=path,
            lines=None,
            trees=self._four_trees(),
        )
        return card_fingerprint(
            block=target_block, base=base_side, candidate=cand_side, locator="file"
        )

    def _range_card_fingerprint(
        self,
        block: BlockIdentity,
        path: str,
        base_blob: bytes | None,
        cand_blob: bytes | None,
        source_binding: FrameBinding | None,
    ) -> BlockFingerprint:
        assert block.start_line is not None and block.end_line is not None
        lines = (block.start_line, block.end_line)
        from_tree = (
            block.candidate_tree
            or (source_binding.code_candidate_tree if source_binding else None)
            or self.binding.code_candidate_tree
        )
        mapped = self.mapped_lines(
            side="code-candidate",
            path=path,
            from_tree=from_tree,
            to_tree=self.binding.code_candidate_tree,
            lines=lines,
        )
        cand_hash = (
            None
            if mapped is None
            else self.content_identity(
                side="code-candidate",
                tree=self.binding.code_candidate_tree,
                path=path,
                lines=mapped,
            )
        )
        base_hash = self.content_identity(
            side="code-base",
            tree=self.binding.code_base_tree,
            path=path,
            lines=mapped or lines,
        )
        base_side = (
            CardSide(absent=True)
            if base_blob is None
            else CardSide(content=base_hash or content_identity(base_blob))
        )
        cand_side = (
            CardSide(absent=True)
            if cand_blob is None
            else (CardSide(content=cand_hash) if cand_hash is not None else CardSide())
        )
        target_block = BlockIdentity.card(
            entry=block.entry or "",
            side=block.side or "after",
            path=path,
            lines=mapped or lines,
            trees=self._four_trees(),
        )
        return card_fingerprint(
            block=target_block, base=base_side, candidate=cand_side, locator="line_range"
        )

    def _entries_for_side(
        self, side: str, *, hint_path: str | None = None
    ) -> dict[str, tuple[dict[str, Any], str]] | None:
        if side in self._sidecar_entries:
            return self._sidecar_entries[side]
        repository = self._repository(side)
        tree = self._tree_for(side)
        names = self._listing(repository, tree, ONBOARDING_ROOT)
        if names is None:
            return None
        found: dict[str, tuple[dict[str, Any], str]] = {}
        ordered = sorted(
            (n for n in names if n.endswith(".json")),
            key=lambda n: (0 if hint_path and n == f"{ONBOARDING_ROOT}{hint_path}.json" else 1, n),
        )
        for name in ordered:
            doc = self._json(repository, tree, name)
            if isinstance(doc, dict):
                _collect_sidecar_entries(doc, name, found)
        self._sidecar_entries[side] = found
        return found

    def _records_for_side(self, side: str) -> dict[str, tuple[str, int]] | None:
        if side in self._records_map:
            return self._records_map[side]
        repository = self._repository(side)
        tree = self._tree_for(side)
        if repository is None or tree is None:
            return None
        names = self._listing(repository, tree, KNOWLEDGE_ROOT)
        if names is None:
            return None
        found: dict[str, tuple[str, int]] = {}
        for name in names:
            if not name.endswith(".json") or name.startswith(HISTORY_ROOT):
                continue
            doc = self._json(repository, tree, name)
            if not isinstance(doc, dict):
                continue
            rec_id = doc.get("id")
            rev = doc.get("revision")
            kind = str(doc.get("kind") or "invariant")
            if isinstance(rec_id, str) and isinstance(rev, int) and not isinstance(rev, bool):
                found[rec_id] = (kind, rev)
        self._records_map[side] = found
        return found

    def _requirements_manifest(self) -> dict[str, Any] | None:
        if self._manifest_read:
            return self._manifest
        self._manifest_read = True
        if self.task_root is None:
            return None
        path = Path(self.task_root).joinpath(*REQUIREMENTS_MANIFEST)
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        self._manifest = document if isinstance(document, dict) else None
        return self._manifest


def _object_path(tree: str | None, path: str) -> str | None:
    """``<tree>:<path>`` for one Git object read, or ``None`` for a name this reader refuses.

    The two spellings come from the explainer's own admission (:mod:`explainer_access`), so a tree
    that is not one object name, and a path holding ``..``, a drive letter or a backslash, are
    refused here too -- and answered as "could not be read", never as a read.
    """

    if tree is None:
        return None
    try:
        return f"{object_name(tree)}:{tree_path(path)}"
    except ExplainerRefused:
        return None


def _tree_paths(repository: Path, tree: str, prefix: str) -> tuple[str, ...] | None:
    """Every path under ``prefix`` in one tree, or ``None`` when the tree cannot be listed."""

    try:
        name = object_name(tree)
    except ExplainerRefused:
        return None
    completed = run_git(
        repository,
        ["ls-tree", "-r", "--name-only", "-z", name],
        GitRunnerOptions(timeout=GIT_METADATA_TIMEOUT_SECONDS),
    )
    if completed.returncode != 0:
        return None
    return tuple(path for path in completed.stdout.split("\0") if path.startswith(prefix))


def _rows_of(document: dict[str, Any], record: str) -> list[tuple[str, int]]:
    """One history file's rows about ``record``: the row's id and the file's attempt."""

    attempt = document.get("attempt")
    attempt = attempt if isinstance(attempt, int) and not isinstance(attempt, bool) else 1
    rows = document.get("rows")
    if not isinstance(rows, list):
        return []
    return [
        (str(row["id"]), attempt)
        for row in rows
        if isinstance(row, dict) and row.get("subject") == record and row.get("id")
    ]


def _proves(document: dict[str, Any]) -> Sequence[dict[str, Any]]:
    """The proof entries one sidecar records; a file that records none answers an empty tuple."""

    entries = document.get("proves")
    if not isinstance(entries, list):
        return ()
    return tuple(entry for entry in entries if isinstance(entry, dict))


def _execution(entry: Mapping[str, Any], *, anchored_content: str | None = None) -> ExecutionBasis:
    """One entry's execution state from recorded or anchored code facts."""

    value = entry.get("execution")
    named = _EXECUTIONS.get(value) if isinstance(value, str) else None
    if named is not None:
        return named
    anchor = entry.get("anchor")
    if isinstance(anchor, Mapping):
        if anchored_content is None:
            return "none"
        recorded = anchor.get("content")
        return "established" if recorded and anchored_content == recorded else "claimed"
    return "none"


def _collect_sidecar_entries(
    doc: dict[str, Any], name: str, found: dict[str, tuple[dict[str, Any], str]]
) -> None:
    source_path = str(doc.get("path") or name.removeprefix(ONBOARDING_ROOT).removesuffix(".json"))
    for key in ("realizes", "proves"):
        items = doc.get(key)
        for item in items if isinstance(items, list) else ():
            if not isinstance(item, dict):
                continue
            if item.get("id"):
                found.setdefault(str(item["id"]), (item, source_path))
            if item.get("invariant"):
                found.setdefault(str(item["invariant"]), (item, source_path))


def _resolve_symbol_in_blob(
    path: str, data: bytes, name: str
) -> tuple[str | None, tuple[int, int] | None]:
    span = _symbol_span(path, data, name)
    if span is None:
        return None, None
    try:
        return content_identity(range_bytes(data, *span)), span
    except RangeOutsideBlobError:
        return None, None


def _symbol_span(path: str, data: bytes, name: str) -> tuple[int, int] | None:
    if not name or grammars.grammar_of(path) is None:
        return None
    lines = data.decode("utf-8", errors="surrogateescape").split("\n")
    try:
        bound = extents.definitions(path, lines)
    except Exception:
        return None
    spans = extents.qualified_spans(name, bound)
    return spans[0] if len(spans) == 1 else None


def _side_lines_hash(data: bytes | None, start: int, count: int) -> str | None:
    if count == 0:
        return ABSENT
    if data is None:
        return None
    try:
        return content_identity(range_bytes(data, start, start + count - 1))
    except RangeOutsideBlobError:
        return None


def _spans_overlap(left: tuple[int, int], right: tuple[int, int]) -> bool:
    return max(left[0], right[0]) <= min(left[1], right[1])


def _inclusive(span: tuple[int, int]) -> tuple[int, int]:
    return (span[0], span[0] + max(span[1], 1) - 1)


def _match_hunk_exact(block: BlockIdentity, hunks: Sequence[HunkSides]) -> HunkSides | None:
    if block.before_span is not None and block.after_span is not None:
        for item in hunks:
            if item.base_span == block.before_span and item.candidate_span == block.after_span:
                return item
    if block.start_line is not None and block.end_line is not None:
        wanted = (block.start_line, block.end_line)
        for item in hunks:
            if item.identity_span == wanted:
                return item
    return None


def _match_hunk(block: BlockIdentity, hunks: Sequence[HunkSides]) -> HunkSides | None:
    exact = _match_hunk_exact(block, hunks)
    if exact is not None:
        return exact
    wanted_ranges: list[tuple[tuple[int, int], bool]] = []
    if block.start_line is not None and block.end_line is not None:
        wanted_ranges.append(((block.start_line, block.end_line), False))
    if block.before_span is not None:
        wanted_ranges.append((_inclusive(block.before_span), True))
    if block.after_span is not None:
        wanted_ranges.append((_inclusive(block.after_span), False))
    for rng, is_base in wanted_ranges:
        for item in hunks:
            target_rng = _inclusive(item.base_span) if is_base else item.identity_span
            if _spans_overlap(target_rng, rng):
                return item
    return hunks[0] if len(hunks) == 1 else None


def binding_fingerprint_reader(
    binding: FrameBinding, *, task_root: Path | None = None
) -> Callable[[Sequence[str]], dict[str, str]]:
    """Build a ``FingerprintReader`` callable over ``binding``'s four trees."""

    reads = ExplainerTargetReads(binding=binding, task_root=task_root)
    return reads.resolve_fingerprints


def _registered_version(manifest: dict[str, Any] | None, stable_id: str) -> int | None:
    """The version one requirements manifest registers for a stable id, or ``None``."""

    packets = manifest.get("packets") if isinstance(manifest, dict) else None
    if not isinstance(packets, list):
        return None
    for entry in packets:
        if not isinstance(entry, dict) or entry.get("id") != stable_id:
            continue
        version = entry.get("version")
        text = version.lstrip("v") if isinstance(version, str) else ""
        return int(text) if text.isdigit() else None
    return None
