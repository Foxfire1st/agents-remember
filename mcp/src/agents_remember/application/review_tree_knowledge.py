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
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Literal

from agents_remember.application.knowledge_currentness import CodeTree, invariant_currentness
from agents_remember.application.knowledge_worklist import leaf_worklist, read_leaf_worklist
from agents_remember.application.review_candidate_resolution import (
    recorded_leaf_contract,
    resolve_review_candidate,
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
from agents_remember.kernel.git_command import run_git
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.memory.knowledge_index import HistoryRow, KnowledgeIndex, is_indexed_path
from agents_remember.models.knowledge.base import PROSE_MAX_LENGTH
from agents_remember.models.knowledge.review import ReviewRefusal
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
    "worklist_view",
]

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
_IDENTIFIER_KEY = re.compile(r"^[a-z][A-Za-z0-9]*$")
_CAMEL_HUMP = re.compile(r"(?<=[a-z0-9])([A-Z])")


def read_review_trees(config: McpRuntimeConfig, query: ReviewTreesQuery) -> ReviewTreesResult:
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
    return _view(query, contract, trees)


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
    query: ReviewTreesQuery, contract: WorktreeContract, trees: ReviewTrees
) -> ReviewTreesResult:
    record = trees.record
    available = trees.before.database is not None and trees.after.database is not None
    before_tree = trees.before.wire.tree
    return ReviewTreesResult(
        state="trees",
        repository_id=query.repository_id,
        master=query.master,
        leaf_id=record.leaf_id,
        comparison=record,
        knowledge_sides=trees.sides(),
        code_sides=trees.code_sides,
        knowledge_diff=(
            knowledge_tree_diff(trees.memory_repository, before_tree, record.memory_candidate.tree)
            if available
            else None
        ),
        currentness=snake_keys(side_currentness(trees)),
        worklist=worklist_view(contract, trees, live=trees.live),
    )


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

    changes = list(_changed_files(repository, before, after))
    groups = _Groups()
    for change in changes:
        before_doc = _json_at(repository, before, change.old_path or change.path)
        after_doc = _json_at(repository, after, change.path)
        groups.add(change, before_doc, after_doc)
    return ReviewKnowledgeTreeDiff(
        before_tree=before,
        after_tree=after,
        changed_files=len(changes),
        records=tuple(groups.records[key] for key in sorted(groups.records)),
        sources=tuple(sorted(groups.sources, key=lambda group: group.source_path)),
        history=tuple(groups.history),
        other=tuple(groups.other),
    )


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


def _changed_files(
    repository: Path, before: str, after: str
) -> Iterator[ReviewKnowledgeFileChange]:
    """Every changed knowledge file the index reads, with its patch (renames kept as one change)."""

    for status, old_path, path in _name_status(repository, before, after):
        if not (is_indexed_path(path) or (old_path is not None and is_indexed_path(old_path))):
            continue
        patch = _patch(repository, before, after, (old_path, path) if old_path else (path,))
        yield ReviewKnowledgeFileChange(
            path=path,
            old_path=old_path,
            status=_STATUS.get(status[0], "modified"),
            patch=patch[:_PATCH_LIMIT],
            truncated=len(patch) > _PATCH_LIMIT,
        )


def _name_status(
    repository: Path, before: str, after: str
) -> Iterator[tuple[str, str | None, str]]:
    """``(status, old path, path)`` for each path under the knowledge and onboarding roots."""

    listed = run_git(
        repository,
        [
            "diff",
            "--no-color",
            "--no-ext-diff",
            "--name-status",
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
    position = 0
    while position < len(tokens) and tokens[position]:
        status = tokens[position]
        if status[0] in "RC":
            yield status, tokens[position + 1], tokens[position + 2]
            position += 3
        else:
            yield status, None, tokens[position + 1]
            position += 2


def _patch(repository: Path, before: str, after: str, paths: tuple[str, ...]) -> str:
    shown = run_git(
        repository,
        ["diff", "--no-color", "--no-ext-diff", "-M", before, after, "--", *paths],
    )
    return shown.stdout if shown.returncode == 0 else ""


def _json_at(repository: Path, tree: str, path: str) -> dict[str, Any] | None:
    shown = run_git(repository, ["cat-file", "blob", f"{tree}:{path}"])
    if shown.returncode != 0:
        return None
    try:
        loaded = json.loads(shown.stdout)
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


def worklist_view(
    contract: WorktreeContract, trees: ReviewTrees, *, live: bool
) -> ReviewWorklistView:
    """The leaf's current worklist: computed for a live leaf, the persisted one for a record."""

    if live:
        document = leaf_worklist(contract, persist=False)
        source: Literal["computed", "persisted", "absent"] = "computed"
    else:
        document = read_leaf_worklist(contract.contract_path)
        source = "persisted"
    if document is None:
        return ReviewWorklistView(
            source="absent",
            detail="no MIK-R08 worklist applies to this leaf, or none was persisted for it",
        )
    items = tuple(dict(item) for item in document.get("items", ()))
    return ReviewWorklistView(
        source=source,
        bound=_bound(document.get("pairing") or {}, trees),
        state=str(document.get("state")),
        items=tuple(snake_keys(items)),
        history_rows=tuple(snake_keys(_history_rows(trees, items))),
        changes=tuple(snake_keys(list(document.get("changes", ())))),
        incomplete=tuple(snake_keys(list(document.get("incomplete", ())))),
    )


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
