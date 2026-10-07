"""The reviewer's tree view: knowledge as a Git diff, currentness per side, the worklist (MIK-R25).

For one leaf whose memory is converted, the reviewer's comparison is four Git trees
(:mod:`agents_remember.application.review_tree_comparison`). This module answers what the landed
review composition does not already show, and nothing more (rules 2 and 3):

* **The knowledge diff (rule 2).** The Git diff of the two memory trees over the knowledge files
  -- records, sidecars and history files, the files the index reads -- grouped by record and by
  source path. A sidecar's changed entries are named under the source path and under the record
  they realize or prove, so a record's code-side changes are visible from both.
* **Currentness per side (rule 2).** Every invariant and family of each side, through the one
  MIK-R03 state function, computed against that side's own code tree: the before memory tree at
  the code base, the after memory tree at the code candidate.
* **The worklist view (rule 3).** The leaf's current MIK-R08 worklist items, the history rows about
  their subjects (shown without a current or stale mark until MIK-R09 supplies one), and the gate
  linkage of every changed hunk (linked, or unexplained).
* **The entries (MIK-R31).** When the query names invariants, the view answers only their
  realization and proof entries, located on both code sides with each range's excerpt, for the
  reviewer's focused expression cards (:mod:`.review_tree_entries`). The cards of one selection are
  read on demand, so the leaf-wide view never carries every excerpt of the repository.
* **The unexplained-changes lane (MIK-R32).** ``lane`` answers only the lane's two destinations, and
  ``file`` only one changed path's classification (:mod:`.review_unexplained_lane`); the worklist
  view of rule 3 stays in the leaf-wide view beside them.

The route's wire keys are snake_case, the review surface's convention (MIK-L25 review F9): the
currentness documents and the worklist's own documents, which their owners spell in camelCase, are
re-keyed here by :func:`snake_keys` -- identifier keys only, so a path or an ID used as a key is
never rewritten.

Each memory side is read through the index of its tree; no database copy is read or made.

**Cost (MIK-R40 rule 4).** The leaf-wide view is asked for once when the reviewer opens, in the
process that also answers every click, so it is built not to compete with them:

* its worklist is computed over the two candidate trees the comparison already holds
  (:class:`~.knowledge_worklist.leaf.CandidateTrees`), so the view captures nothing itself;
* the knowledge diff asks Git three questions whatever the number of changed files
  (:func:`_measured_tree_diff`);
* both computed parts are kept in a bounded in-process memo keyed by exact identities
  (:mod:`.review_leaf_view_memo`), so a repeat for unchanged trees computes neither;
* each uncached worklist runs in the exact package's child interpreter (MIK-R42), so parsing
  knowledge trees and cyclic collection do not compete with the dashboard's subject reads.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Literal, cast

from agents_remember.application.knowledge_currentness import CodeTree, invariant_currentness
from agents_remember.application.knowledge_worklist import read_leaf_worklist
from agents_remember.application.knowledge_worklist.leaf import CandidateTrees, CapturedBase
from agents_remember.application.review_candidate_resolution import (
    recorded_leaf_contract,
    resolve_review_candidate,
)
from agents_remember.application.review_leaf_view_memo import (
    LeafViewParts,
    leaf_view_key,
    moved_inputs,
    remember,
    remembered,
)
from agents_remember.application.review_legacy_comparison import knowledge_unavailable_refusal
from agents_remember.application.review_tree_comparison import (
    ReviewTrees,
    reopen_review_trees,
)
from agents_remember.application.review_tree_entries import tree_entries
from agents_remember.application.review_unexplained_lane import (
    classify_changed_path,
    unexplained_lane,
)
from agents_remember.application.reviewer_worklist_child import isolated_leaf_worklist
from agents_remember.kernel.git_command import (
    PARSED_DIFF_OPTIONS,
    read_git_blob_bytes,
    read_git_blobs_bytes,
    run_git,
)
from agents_remember.kernel.git_preparation import GitPreparationError
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.kernel.recorded_reads import recorded_reads, replay_reads
from agents_remember.kernel.reviewer_worklist_process import (
    ReviewerWorklistProcesses,
    WorklistProcessError,
)
from agents_remember.memory.knowledge_index import HistoryRow, KnowledgeIndex, is_indexed_path
from agents_remember.models.knowledge.base import PROSE_MAX_LENGTH, REFERENCE_MAX_LENGTH
from agents_remember.models.knowledge.review import ReviewRefusal, ReviewRefusalCode
from agents_remember.models.knowledge.review_lane import (
    ReviewFileClassification,
    ReviewUnexplainedLane,
)
from agents_remember.models.knowledge.review_tree_entries import ReviewTreeEntry
from agents_remember.models.knowledge.review_trees import (
    ReviewKnowledgeFileChange,
    ReviewKnowledgeRecordGroup,
    ReviewKnowledgeSourceGroup,
    ReviewKnowledgeTreeDiff,
    ReviewTreesResult,
    ReviewWorklistView,
)
from agents_remember.models.knowledge_files.documents import (
    KNOWLEDGE_ROOT,
    ONBOARDING_ROOT,
    owner_history_attempt,
)
from agents_remember.serving.review_trees import ReviewTreesQuery
from agents_remember.worktrees.worktree_contract import WorktreeContract

__all__ = [
    "knowledge_tree_diff",
    "read_review_trees",
    "side_currentness",
    "snake_keys",
]

# One computation, and at most one more when an input the view read changed meanwhile.
_COMPUTE_ATTEMPTS: Final = 2
_HISTORY_PREFIX: Final = f"{KNOWLEDGE_ROOT}/history/"
_PATCH_LIMIT: Final = PROSE_MAX_LENGTH - 200
_STATUS: Final[dict[str, Literal["added", "deleted", "modified", "renamed", "type_changed"]]] = {
    "A": "added",
    "D": "deleted",
    "M": "modified",
    "R": "renamed",
    "T": "type_changed",
}
_SCHEMA_KIND = re.compile(r"^ar-([a-z-]+)/v\d+$")
_NO_OBJECT = re.compile(r"^0+$")
_IDENTIFIER_KEY = re.compile(r"^[a-z][A-Za-z0-9]*$")
_CAMEL_HUMP = re.compile(r"(?<=[a-z0-9])([A-Z])")


def read_review_trees(
    config: McpRuntimeConfig,
    query: ReviewTreesQuery,
    *,
    processes: ReviewerWorklistProcesses | None = None,
) -> ReviewTreesResult:
    """The tree view of one leaf: its comparison, knowledge diff, currentness and worklist."""

    found = _comparison(config, query)
    if isinstance(found, ReviewRefusal):
        return ReviewTreesResult(
            state="refused",
            repository_id=query.repository_id,
            master=query.master,
            leaf_id=query.leaf_id,
            refusal=found,
        )
    if found is None:
        return ReviewTreesResult(
            state="not-converted",
            repository_id=query.repository_id,
            master=query.master,
            leaf_id=query.leaf_id,
        )
    contract, trees = found
    if query.file is not None:
        return _file_view(query, trees)
    if query.lane:
        return _focused(query, trees, lane=unexplained_lane(trees))
    if query.invariants:
        return _focused(query, trees, entries=tree_entries(trees, query.invariants))
    return _view(query, contract, trees, processes=processes)


def _comparison(
    config: McpRuntimeConfig, query: ReviewTreesQuery
) -> tuple[WorktreeContract, ReviewTrees] | ReviewRefusal | None:
    """The comparison a query names: a recorded number, or the review's own resolution.

    A leaf-wide read that names a number is pinned to that comparison. When the number is the one
    the review resolves to now, the resolution is used as it is (a live leaf keeps its computed
    worklist); any other number is reopened from its record. A focused read (the cards' named
    invariants, the lane, one file) needs only the trees, so it always reopens.
    """

    if query.number is None:
        return _resolved(config, query)
    if not query.focused:
        current = _resolved(config, query)
        if isinstance(current, tuple) and current[1].record.number == query.number:
            return current
    contract = recorded_leaf_contract(config, query.repository_id, query.master, query.leaf_id)
    if contract is None:
        return _no_contract(query.master, query.leaf_id)
    reopened = reopen_review_trees(config.coordination_root, contract, query.number)
    return reopened if not isinstance(reopened, ReviewTrees) else (contract, reopened)


def _resolved(
    config: McpRuntimeConfig, query: ReviewTreesQuery
) -> tuple[WorktreeContract, ReviewTrees] | ReviewRefusal | None:
    resolved = resolve_review_candidate(
        config, query.repository_id, query.master, query.leaf_id, recorded=query.recorded
    )
    if isinstance(resolved, ReviewRefusal):
        return resolved
    if resolved.trees is None or resolved.contract is None:
        return knowledge_unavailable_refusal(resolved)
    return resolved.contract, resolved.trees


def _view(
    query: ReviewTreesQuery,
    contract: WorktreeContract,
    trees: ReviewTrees,
    *,
    processes: ReviewerWorklistProcesses | None = None,
) -> ReviewTreesResult:
    record = trees.record
    parts = _leaf_wide_parts(contract, trees, processes=processes)
    if isinstance(parts, ReviewRefusal):
        return ReviewTreesResult(
            state="refused",
            repository_id=query.repository_id,
            master=query.master,
            leaf_id=record.leaf_id,
            refusal=parts,
        )
    return ReviewTreesResult(
        state="trees",
        repository_id=query.repository_id,
        master=query.master,
        leaf_id=record.leaf_id,
        comparison=record,
        knowledge_sides=trees.sides(),
        code_sides=trees.code_sides,
        knowledge_diff=parts.knowledge_diff,
        currentness=snake_keys(side_currentness(trees)),
        worklist=parts.worklist,
    )


def _leaf_wide_parts(
    contract: WorktreeContract,
    trees: ReviewTrees,
    *,
    processes: ReviewerWorklistProcesses | None = None,
) -> LeafViewParts | ReviewRefusal:
    """The view's knowledge diff and worklist: the kept ones for these exact trees, or computed.

    A live comparison's parts are looked up under everything they are a function of
    (:func:`~.review_leaf_view_memo.leaf_view_key`). They are kept only when every read behind
    them succeeded: both knowledge sides readable, no Git read of the diff failed, and a
    ``complete`` worklist. Anything else is returned as computed and read again next time.

    An input the view read that changed while it was being computed (MIK-R42 ruling 2) does not
    refuse the reader: the view is computed once more on the state that exists now, with a key
    taken again. Two mismatching attempts are answered as such; sharing may have supplied the
    first attempt from a computation that began before this request.
    """

    moved: tuple[str, ...] = ()
    for _attempt in range(_COMPUTE_ATTEMPTS):
        key = leaf_view_key(contract, trees)
        kept = None if key is None else remembered(key)
        if kept is not None:
            return kept
        try:
            parts, settled, reads = _compute_leaf_parts(contract, trees, processes=processes)
        except WorklistProcessError as error:
            return ReviewRefusal(
                code=cast(ReviewRefusalCode, error.code),
                detail=str(error)[:PROSE_MAX_LENGTH],
                next_action=error.next_action,
                offending_input="reviewer worklist process",
            )
        moved = moved_inputs(key, reads)
        if not moved:
            if key is not None and settled:
                remember(key, parts, reads)
            return parts
    return ReviewRefusal(
        code="inputs_changing",
        detail=(
            "task documents or other inputs of the view did not match the request in "
            f"either of {_COMPUTE_ATTEMPTS} computation attempts: {'; '.join(moved)}"
        )[:PROSE_MAX_LENGTH],
        next_action="retry once the named inputs stop changing",
        offending_input="; ".join(moved)[:REFERENCE_MAX_LENGTH],
    )


def _compute_leaf_parts(
    contract: WorktreeContract,
    trees: ReviewTrees,
    *,
    processes: ReviewerWorklistProcesses | None = None,
) -> tuple[LeafViewParts, bool, dict[str, str]]:
    """Compute the view while observing its inputs; failed reads leave it unkeepable."""

    available = trees.before.database is not None and trees.after.database is not None
    reads: dict[str, str] = {}
    try:
        with recorded_reads() as reads:
            measured = (
                _measured_tree_diff(
                    trees.memory_repository,
                    trees.before.wire.tree,
                    trees.record.memory_candidate.tree,
                )
                if available
                else None
            )
            worklist, settled = _worklist_view(
                contract, trees, live=trees.live, processes=processes
            )
    finally:
        replay_reads(reads)
    parts = LeafViewParts(None if measured is None else measured.diff, worklist)
    return parts, measured is not None and measured.complete and settled, reads


def _focused(
    query: ReviewTreesQuery,
    trees: ReviewTrees,
    *,
    entries: tuple[ReviewTreeEntry, ...] = (),
    lane: ReviewUnexplainedLane | None = None,
    file_classification: ReviewFileClassification | None = None,
) -> ReviewTreesResult:
    """The comparison and one focused answer: the cards' entries, the lane, or one file."""

    return ReviewTreesResult(
        state="trees",
        repository_id=query.repository_id,
        master=query.master,
        leaf_id=trees.record.leaf_id,
        comparison=trees.record,
        knowledge_sides=trees.sides(),
        code_sides=trees.code_sides,
        entries=entries,
        lane=lane,
        file_classification=file_classification,
    )


def _file_view(query: ReviewTreesQuery, trees: ReviewTrees) -> ReviewTreesResult:
    """One changed path's classification, or the refusal a path the comparison did not change earns."""

    assert query.file is not None
    classified = classify_changed_path(trees, query.file)
    if isinstance(classified, ReviewRefusal):
        return ReviewTreesResult(
            state="refused",
            repository_id=query.repository_id,
            master=query.master,
            leaf_id=trees.record.leaf_id,
            comparison=trees.record,
            refusal=classified,
        )
    return _focused(query, trees, file_classification=classified)


def snake_keys(value: Any) -> Any:
    """``value`` with every identifier-shaped camelCase key re-spelled in snake_case, recursively."""

    if isinstance(value, Mapping):
        return {_snake(key): snake_keys(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [snake_keys(item) for item in value]
    return value


def _snake(key: object) -> object:
    if not isinstance(key, str) or _IDENTIFIER_KEY.match(key) is None:
        return key
    return _CAMEL_HUMP.sub(lambda hump: f"_{hump.group(1).lower()}", key)


# -- rule 2: the Git diff of the memory trees ----------------------------------------------------


def knowledge_tree_diff(repository: Path, before: str, after: str) -> ReviewKnowledgeTreeDiff:
    """The knowledge files that differ between two memory trees, with their Git patches, grouped."""

    return _measured_tree_diff(repository, before, after).diff


@dataclass(frozen=True)
class _MeasuredDiff:
    """One knowledge diff, and whether every Git read behind it succeeded (only then is it kept)."""

    diff: ReviewKnowledgeTreeDiff
    complete: bool


@dataclass(frozen=True)
class _Listed:
    """One changed path of the two memory trees as Git lists it: its status, paths and objects."""

    status: str
    old_path: str | None
    path: str
    before: str | None
    """The object the before tree holds at the old path, when it holds one there."""
    after: str | None
    """The object the after tree holds at the path, when it holds one there."""

    @property
    def indexed(self) -> bool:
        return is_indexed_path(self.path) or (
            self.old_path is not None and is_indexed_path(self.old_path)
        )

    @property
    def sections(self) -> int:
        """How many file sections Git's patch prints for it: a type change is a removal and an
        addition."""

        return 2 if self.status[:1] == "T" else 1

    @property
    def headers(self) -> set[str]:
        """The exact header spellings for either core.quotePath setting."""

        return {
            f"diff --git {_quoted_git_path('a/' + (self.old_path or self.path), high)} "
            f"{_quoted_git_path('b/' + self.path, high)}"
            for high in (False, True)
        }


def _measured_tree_diff(repository: Path, before: str, after: str) -> _MeasuredDiff:
    """The knowledge diff from a bounded number of Git children (MIK-R40 rule 4).

    Git is asked three questions whatever the number of changed files: which paths changed and
    which blobs they hold, the patch of the two trees, and the bytes of those blobs. The patch is
    one text cut at its file headers; every cut is bound to the exact path Git listed, including
    C-quoted names. A filename is an address: bracket or glob characters do not select siblings.
    A failed aggregate read is retried once and leaves the result unkeepable.
    """

    listed = _listed_changes(repository, before, after)
    patches, complete = _patches(repository, before, after, listed)
    indexed = [(one, patch) for one, patch in zip(listed, patches, strict=True) if one.indexed]
    documents, documents_complete = _documents(repository, [one for one, _ in indexed])
    changes: list[ReviewKnowledgeFileChange] = []
    groups = _Groups()
    for one, patch in indexed:
        change = ReviewKnowledgeFileChange(
            path=one.path,
            old_path=one.old_path,
            status=_STATUS.get(one.status[0], "modified"),
            patch=patch[:_PATCH_LIMIT],
            truncated=len(patch) > _PATCH_LIMIT,
        )
        changes.append(change)
        groups.add(change, _document(documents, one.before), _document(documents, one.after))
    diff = ReviewKnowledgeTreeDiff(
        before_tree=before,
        after_tree=after,
        changed_files=len(changes),
        records=tuple(groups.records[key] for key in sorted(groups.records)),
        sources=tuple(sorted(groups.sources, key=lambda group: group.source_path)),
        history=tuple(groups.history),
        other=tuple(groups.other),
    )
    return _MeasuredDiff(diff, complete and documents_complete)


@dataclass
class _Groups:
    """The two groupings of rule 2, plus history files and any other knowledge file."""

    records: dict[str, ReviewKnowledgeRecordGroup] = field(default_factory=dict)
    sources: list[ReviewKnowledgeSourceGroup] = field(default_factory=list)
    history: list[ReviewKnowledgeFileChange] = field(default_factory=list)
    other: list[ReviewKnowledgeFileChange] = field(default_factory=list)

    def add(
        self,
        change: ReviewKnowledgeFileChange,
        before_doc: dict[str, Any] | None,
        after_doc: dict[str, Any] | None,
    ) -> None:
        document = after_doc if after_doc is not None else before_doc
        record = _record_id(document)
        source = _source_path(document)
        if change.path.startswith(_HISTORY_PREFIX):
            self.history.append(change)
        elif change.path.startswith(f"{KNOWLEDGE_ROOT}/") and record is not None:
            self._record(record[0], record[1], files=(change,))
        elif change.path.startswith(f"{ONBOARDING_ROOT}/") and source is not None:
            self._source(change, source, _changed_entries(before_doc, after_doc))
        else:
            self.other.append(change)

    def _record(
        self,
        identifier: str,
        kind: str,
        *,
        files: tuple[ReviewKnowledgeFileChange, ...] = (),
        entries: tuple[tuple[str, str, str], ...] = (),
    ) -> None:
        group = self.records.get(identifier) or ReviewKnowledgeRecordGroup(
            record_id=identifier, kind=kind
        )
        self.records[identifier] = group.model_copy(
            update={"files": (*group.files, *files), "entries": (*group.entries, *entries)}
        )

    def _source(
        self, change: ReviewKnowledgeFileChange, source: str, entries: list[tuple[str, str, str]]
    ) -> None:
        self.sources.append(
            ReviewKnowledgeSourceGroup(
                source_path=source,
                files=(change,),
                records=tuple(sorted({invariant for _, invariant, _ in entries})),
            )
        )
        for entry_id, invariant, how in entries:
            self._record(invariant, "invariant", entries=((entry_id, source, how),))


def _listed_changes(repository: Path, before: str, after: str) -> list[_Listed]:
    """Every changed path under the knowledge and onboarding roots (renames kept as one change)."""

    listed = run_git(
        repository,
        [
            "diff",
            *PARSED_DIFF_OPTIONS,
            "--raw",
            "--no-abbrev",
            "-z",
            "-M",
            before,
            after,
            "--",
            KNOWLEDGE_ROOT,
            ONBOARDING_ROOT,
        ],
    )
    if listed.returncode != 0:
        raise ValueError(
            f"the memory trees {before} and {after} cannot be compared: {listed.stderr}"
        )
    tokens = listed.stdout.split("\0")
    changes: list[_Listed] = []
    position = 0
    while position < len(tokens) and tokens[position]:
        _old_mode, _new_mode, old_object, new_object, status = tokens[position][1:].split(" ")
        renamed = status[0] in "RC"
        changes.append(
            _Listed(
                status=status,
                old_path=tokens[position + 1] if renamed else None,
                path=tokens[position + 2 if renamed else position + 1],
                before=_held_object(old_object),
                after=_held_object(new_object),
            )
        )
        position += 3 if renamed else 2
    return changes


def _held_object(object_id: str) -> str | None:
    """The object a tree holds at a listed path; Git names none with zeros (added, deleted)."""

    return None if _NO_OBJECT.match(object_id) else object_id


def _patches(
    repository: Path, before: str, after: str, listed: list[_Listed]
) -> tuple[list[str], bool]:
    """Exact-path patches from one aggregate read; recover a failed read once, never per file.

    The prior owner recovered a failed aggregate read by asking for each file. The identical
    batch retry preserves that successful-recovery operation within a fixed child bound
    (L40-R1-F4). Any first read failure leaves the result unkeepable, even when recovery succeeds.
    """

    if not any(one.indexed for one in listed):
        return [""] * len(listed), True
    args = [
        "diff",
        *PARSED_DIFF_OPTIONS,
        "-M",
        before,
        after,
        "--",
        KNOWLEDGE_ROOT,
        ONBOARDING_ROOT,
    ]
    whole = run_git(repository, args)
    complete = whole.returncode == 0
    if not complete:
        whole = run_git(repository, args)
    if whole.returncode != 0:
        return [""] * len(listed), False
    cut = _cut_patch(whole.stdout, listed)
    if cut is None:
        raise ValueError("the memory-tree patch sections do not match the exact changed paths")
    return cut, complete


def _cut_patch(patch: str, listed: list[_Listed]) -> list[str] | None:
    """Bind Git's unchanged file-section text to the exact paths in its NUL-delimited listing."""

    sections = _patch_sections(patch)
    if sections is None:
        return None
    cut: list[str] = []
    position = 0
    for one in listed:
        taken = sections[position : position + one.sections]
        position += one.sections
        if len(taken) != one.sections:
            return None
        if not all(section.split("\n", 1)[0] in one.headers for section in taken):
            return None
        cut.append("".join(taken))
    return cut if position == len(sections) else None


def _patch_sections(patch: str) -> list[str] | None:
    """Git's file sections, retaining their exact text and requiring the first header at zero."""

    starts = [match.start() for match in re.finditer(r"(?m)^diff --git ", patch)]
    if starts and starts[0] != 0:
        return None
    return [patch[start:end] for start, end in zip(starts, [*starts[1:], len(patch)], strict=True)]


_GIT_PATH_ESCAPES: Final = {
    7: b"\\a",
    8: b"\\b",
    9: b"\\t",
    10: b"\\n",
    11: b"\\v",
    12: b"\\f",
    13: b"\\r",
    34: b'\\"',
    92: b"\\\\",
}


def _quoted_git_path(path: str, quote_high: bool) -> str:
    """Git's C-quoted header spelling; byte escapes retain Unicode and arbitrary pathname bytes."""

    raw = path.encode("utf-8", "surrogateescape")
    quoted = b"".join(
        _GIT_PATH_ESCAPES.get(
            byte,
            f"\\{byte:03o}".encode()
            if byte < 32 or byte == 127 or (quote_high and byte >= 128)
            else bytes([byte]),
        )
        for byte in raw
    )
    text = quoted.decode("utf-8", "surrogateescape")
    return f'"{text}"' if quoted != raw else text


def _documents(
    repository: Path, changes: list[_Listed]
) -> tuple[dict[str, dict[str, Any] | None], bool]:
    """The JSON object of every blob the changes name, read through one Git child."""

    wanted = {blob for one in changes for blob in (one.before, one.after) if blob is not None}
    try:
        blobs = read_git_blobs_bytes(repository, wanted)
    except (GitPreparationError, OSError):
        # One object Git cannot produce as a blob fails the whole batch: each is asked for on its
        # own, as before, so only that one is missing. The diff is then not kept.
        return {blob: _json_of(data) for blob, data in _each_blob(repository, wanted)}, False
    return {blob: _json_of(data) for blob, data in blobs.items()}, True


def _each_blob(repository: Path, wanted: set[str]) -> Iterator[tuple[str, bytes]]:
    for blob in sorted(wanted):
        try:
            yield blob, read_git_blob_bytes(repository, blob)
        except (GitPreparationError, OSError):
            continue


def _document(
    documents: Mapping[str, dict[str, Any] | None], blob: str | None
) -> dict[str, Any] | None:
    return None if blob is None else documents.get(blob)


def _json_of(data: bytes) -> dict[str, Any] | None:
    """A blob's JSON object, decoded as Git's text output of it was: bytes that are not UTF-8
    survive as escapes, so a file that is no JSON object answers ``None`` exactly as before."""

    try:
        loaded = json.loads(data.decode("utf-8", "surrogateescape"))
    except ValueError:
        return None
    return loaded if isinstance(loaded, dict) else None


def _record_id(document: Mapping[str, Any] | None) -> tuple[str, str] | None:
    if document is None or not isinstance(document.get("id"), str):
        return None
    matched = _SCHEMA_KIND.match(str(document.get("schema", "")))
    if matched is None or matched.group(1) in {"history", "memory-layout", "onboarding-file"}:
        return None
    return str(document["id"]), matched.group(1).replace("-", "_")


def _source_path(document: Mapping[str, Any] | None) -> str | None:
    if document is None or not isinstance(document.get("path"), str):
        return None
    return str(document["path"])


def _entries(document: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    if document is None:
        return {}
    listed = [*(document.get("realizes") or ()), *(document.get("proves") or ())]
    return {
        str(entry["id"]): entry for entry in listed if isinstance(entry, dict) and "id" in entry
    }


def _changed_entries(
    before: Mapping[str, Any] | None, after: Mapping[str, Any] | None
) -> list[tuple[str, str, str]]:
    """``(entry id, invariant, added|removed|changed)`` for every entry that differs."""

    old, new = _entries(before), _entries(after)
    changed: list[tuple[str, str, str]] = []
    for entry_id in sorted(set(old) | set(new)):
        if entry_id not in old:
            how, entry = "added", new[entry_id]
        elif entry_id not in new:
            how, entry = "removed", old[entry_id]
        elif old[entry_id] != new[entry_id]:
            how, entry = "changed", new[entry_id]
        else:
            continue
        changed.append((entry_id, str(entry.get("invariant", "")), how))
    return changed


# -- rule 2: currentness per side ------------------------------------------------------------------


def side_currentness(trees: ReviewTrees) -> dict[str, dict[str, Any]]:
    """Each side's MIK-R03 currentness, at that side's own code tree; an unread side says why."""

    record = trees.record
    code = {
        "before": CodeTree(Path(record.code_base.repository), record.code_base.tree),
        "after": CodeTree(Path(record.code_candidate.repository), record.code_candidate.tree),
    }
    result: dict[str, dict[str, Any]] = {}
    for side in (trees.before, trees.after):
        name = side.wire.side
        if side.database is None:
            result[name] = {"unverifiableReason": side.wire.detail or side.wire.state}
            continue
        with KnowledgeIndex(side.database) as index:
            currentness = invariant_currentness(
                code[name],
                index,
                index.record_ids("invariant").value,
                index.record_ids("family").value,
            )
            result[name] = {**currentness.to_document(), "indexState": index.state.state}
    return result


# -- rule 3: the worklist view -----------------------------------------------------------------------


def _worklist_view(
    contract: WorktreeContract,
    trees: ReviewTrees,
    *,
    live: bool,
    processes: ReviewerWorklistProcesses | None = None,
) -> tuple[ReviewWorklistView, bool]:
    """The worklist view, and whether it is a computed view over complete inputs (only then is it
    kept).

    A live leaf's worklist is computed over the comparison's own two candidate trees, so it reads
    exactly what the review compares and captures no worktree a second time.
    """

    if live:
        candidate = CandidateTrees(
            code=trees.record.code_candidate.tree, memory=trees.record.memory_candidate.tree
        )
        if trees.record.code_base.commit is None or trees.record.memory_base.commit is None:
            raise WorklistProcessError("reviewer worklist comparison has no captured base commits")
        base = CapturedBase(
            code=trees.record.code_base.commit,
            memory=trees.record.memory_base.commit,
            read_tree=trees.before.wire.tree,
        )
        document = isolated_leaf_worklist(
            contract, candidate=candidate, base=base, processes=processes
        )
        source: Literal["computed", "persisted", "absent"] = "computed"
    else:
        document = read_leaf_worklist(contract.contract_path)
        source = "persisted"
    if document is None:
        return (
            ReviewWorklistView(
                source="absent",
                detail="no MIK-R08 worklist applies to this leaf, or none was persisted for it",
            ),
            False,
        )
    if (
        live
        and document.get("state") == "complete"
        and not _bound(document.get("pairing") or {}, trees)
    ):
        raise WorklistProcessError(
            "reviewer worklist child result does not bind the supplied comparison"
        )
    items = tuple(dict(item) for item in document.get("items", ()))
    view = ReviewWorklistView(
        source=source,
        bound=_bound(document.get("pairing") or {}, trees),
        state=str(document.get("state")),
        items=tuple(snake_keys(items)),
        history_rows=tuple(snake_keys(_history_rows(trees, items))),
        changes=tuple(snake_keys(list(document.get("changes", ())))),
        incomplete=tuple(snake_keys(list(document.get("incomplete", ())))),
    )
    return view, live and document.get("state") == "complete"


def _bound(pairing: Mapping[str, Any], trees: ReviewTrees) -> bool:
    """Whether the worklist's recorded pairing is exactly this comparison's four trees."""

    record = trees.record
    try:
        return (
            pairing["base"]["tree"] == record.code_base.tree
            and pairing["candidate"]["tree"] == record.code_candidate.tree
            and pairing["memoryBase"]["commit"] == record.memory_base.commit
            and pairing["memoryCandidate"]["tree"] == record.memory_candidate.tree
        )
    except (KeyError, TypeError):
        return False


def _history_rows(
    trees: ReviewTrees, items: tuple[dict[str, Any], ...]
) -> tuple[dict[str, Any], ...]:
    """The after tree's history rows about the items' subjects, with no currency mark (rule 3).

    An item names the subject its answering row carries in ``facts.row`` when that is not its own
    subject (an unexplained hunk is answered by a ``hunk:…`` row, MIK-R10), so both are looked up.
    Of one owner's rows about a subject only its latest attempt's is shown: a leaf that continued
    after a closeout answers the subject again in its next attempt file, and that judgment governs
    (L37 ruling B). The superseded row stays in its frozen file and in the knowledge diff.
    """

    database = trees.after.database
    subjects = sorted({subject for item in items for subject in _row_subjects(item)})
    if database is None or not subjects:
        return ()
    rows: list[dict[str, Any]] = []
    with KnowledgeIndex(database) as index:
        for subject in subjects:
            rows += [
                {
                    "id": row.id,
                    "owner": row.owner,
                    "ownerKind": row.owner_kind,
                    "closed": row.closed,
                    "path": row.path,
                    "subject": row.subject,
                    "disposition": row.disposition,
                    "row": row.document,
                }
                for row in _governing(index.history_rows_about(subject).value)
            ]
    return tuple(rows)


def _governing(rows: tuple[HistoryRow, ...]) -> Iterator[HistoryRow]:
    """Each owner's row about one subject from that owner's latest attempt file."""

    attempts = [owner_history_attempt(row.path, row.owner) or 1 for row in rows]
    latest: dict[str, int] = {}
    for row, attempt in zip(rows, attempts, strict=True):
        latest[row.owner] = max(latest.get(row.owner, 0), attempt)
    for row, attempt in zip(rows, attempts, strict=True):
        if attempt == latest[row.owner]:
            yield row


def _row_subjects(item: Mapping[str, Any]) -> Iterator[str]:
    if item.get("subject"):
        yield str(item["subject"])
    facts = item.get("facts")
    row = facts.get("row") if isinstance(facts, Mapping) else None
    if isinstance(row, str) and row:
        yield row


def _no_contract(master: str, leaf_id: str) -> ReviewRefusal:
    return ReviewRefusal(
        code="candidate_unresolved",
        detail="no readable leaf enclosure contract records this leaf under the named master",
        next_action="open the review for a leaf whose enclosure contract exists",
        offending_input=f"{master}/{leaf_id}",
    )
