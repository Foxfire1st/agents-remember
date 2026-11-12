"""The reviewer's tree view: currentness per side, the worklist (MIK-R25).

For one leaf whose memory is converted, the reviewer's comparison is four Git trees
(:mod:`agents_remember.application.review_tree_comparison`). This module answers what the landed
review composition does not already show, and nothing more (rules 2 and 3):

* **Currentness per side (rule 2).** Every invariant and family of each side, through the one
  MIK-R03 state function, computed against that side's own code tree: the before memory tree at
  the code base, the after memory tree at the code candidate.
* **The worklist view (rule 3).** The leaf's current MIK-R08 worklist items, the history rows about
  their subjects, the gate's own per-item verdict and witness, and the gate
  linkage of every changed hunk (linked, or unexplained).
* **The entries (MIK-R31).** When the query names invariants, the view answers only their
  realization and proof entries, located on both code sides with each range's excerpt, for the
  reviewer's focused expression cards (:mod:`.review_tree_entries`). The cards of one selection are
  read on demand, so the leaf-wide view never carries every excerpt of the repository. Each entry
  also carries its four memory values, read at the after-side index (MIK-R89 v2 rule 3), and the
  answer's ``entry_memory`` says whether they could be read.
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
* the worklist is kept in a bounded in-process memo keyed by exact identities
  (:mod:`.review_leaf_view_memo`), so a repeat for unchanged trees computes nothing;
* each uncached worklist runs in the exact package's child interpreter (MIK-R42), so parsing
  knowledge trees and cyclic collection do not compete with the dashboard's subject reads;
* while the child computes, the entry inventory and the currentness -- the view's two other pure
  parts -- are computed beside it in a thread of this process (D569). Each is taken where the view
  always took it, with the reads it made replayed there, so the answer and its recorded reads are
  those of the sequential view. That thread also fills the blob-keyed memos from their store
  before its first computation and stores what it added afterwards (:mod:`.review_memo_store`).

The shared knowledge_tree_diff helper remains here for the public knowledge_diff reader; its file diff is served there and is not included in this tree-view response or its memo."""

from __future__ import annotations

import contextvars
import json
import logging
import re
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Literal, cast

from agents_remember.application.knowledge_worklist.leaf import CandidateTrees, CapturedBase
from agents_remember.application.review_candidate_resolution import (
    recorded_leaf_contract,
    resolve_review_candidate,
)
from agents_remember.application.review_delivered_comparison import (
    delivered_notice,
    recorded_latest,
)
from agents_remember.application.review_leaf_view_memo import (
    LeafViewKey,
    LeafViewParts,
    leaf_view_key,
    moved_inputs,
    remember,
    remembered,
)
from agents_remember.application.review_legacy_comparison import knowledge_unavailable_refusal
from agents_remember.application.review_memo_store import restore_review_memos, save_review_memos
from agents_remember.application.review_tree_comparison import (
    ReviewTrees,
    reopen_review_trees,
)
from agents_remember.application.review_tree_entries import (
    CardEntries,
    tree_entries,
    tree_entry_cards,
    tree_entry_inventory,
)
from agents_remember.application.review_tree_overview import summarize_inventory, summarize_worklist
from agents_remember.application.review_tree_state import history_rows as _history_rows
from agents_remember.application.review_tree_state import side_currentness
from agents_remember.application.review_unexplained_lane import (
    classify_changed_path,
    unexplained_lane,
)
from agents_remember.application.review_worklist_answers import worklist_bound
from agents_remember.application.reviewer_worklist_child import isolated_leaf_worklist
from agents_remember.kernel.git_command import (
    PARSED_DIFF_OPTIONS,
    memo_git_object_lookups,
    read_git_blob_bytes,
    read_git_blobs_bytes,
    run_git,
    shared_blob_reads,
)
from agents_remember.kernel.git_preparation import GitPreparationError
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.kernel.read_cancellation import SharedFlights, raise_if_read_cancelled
from agents_remember.kernel.recorded_reads import recorded_reads, replay_reads
from agents_remember.kernel.reviewer_worklist_process import (
    ReviewerWorklistProcesses,
    WorklistProcessError,
)
from agents_remember.memory.knowledge_index import is_indexed_path
from agents_remember.models.knowledge.base import PROSE_MAX_LENGTH, REFERENCE_MAX_LENGTH
from agents_remember.models.knowledge.item_verdict import ItemVerdict
from agents_remember.models.knowledge.review import ReviewRefusal, ReviewRefusalCode
from agents_remember.models.knowledge.review_code_changes import ReviewCodeChanges
from agents_remember.models.knowledge.review_lane import (
    ReviewFileClassification,
    ReviewUnexplainedLane,
)
from agents_remember.models.knowledge.review_trees import (
    ReviewEntryInventory,
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
)
from agents_remember.serving.build_info import process_serving_build
from agents_remember.serving.review_trees import ReviewTreesQuery
from agents_remember.worktrees.worktree_contract import WorktreeContract

__all__ = [
    "CardsCarry",
    "carried_cards_view",
    "knowledge_tree_diff",
    "read_prepared_review_trees",
    "read_review_trees",
    "resolve_tree_comparison",
    "side_currentness",
    "snake_keys",
]

logger = logging.getLogger(__name__)

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


#: The cards of an invariant carried from an earlier comparison onto these trees, or ``None`` (D574).
type CardsCarry = Callable[[ReviewTreesQuery, ReviewTrees], ReviewTreesResult | None]


def read_review_trees(
    config: McpRuntimeConfig,
    query: ReviewTreesQuery,
    *,
    processes: ReviewerWorklistProcesses | None = None,
    carry: CardsCarry | None = None,
) -> ReviewTreesResult:
    """The tree view of one leaf: its comparison, currentness and worklist.

    Within one request the same Git lookup of the same object is made once
    (MIK-R81 rule 7): the comparison, the currentness and the worklist all read
    the same trees, and the memo keeps the first answer of every content-addressed
    lookup for the rest of the read.
    """

    with memo_git_object_lookups(), shared_blob_reads():
        return _read_review_trees(config, query, processes=processes, carry=carry)


def _read_review_trees(
    config: McpRuntimeConfig,
    query: ReviewTreesQuery,
    *,
    processes: ReviewerWorklistProcesses | None = None,
    carry: CardsCarry | None = None,
) -> ReviewTreesResult:
    found = resolve_tree_comparison(config, query)
    return read_prepared_review_trees(query, found, processes=processes, carry=carry)


def read_prepared_review_trees(
    query: ReviewTreesQuery,
    found: tuple[WorktreeContract, ReviewTrees] | ReviewRefusal | None,
    *,
    processes: ReviewerWorklistProcesses | None = None,
    carry: CardsCarry | None = None,
) -> ReviewTreesResult:
    """Project one already admitted native comparison without resolving it again."""
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
    if query.invariants or query.entry is not None:
        carried = None if carry is None or query.entry is not None else carry(query, trees)
        return carried if carried is not None else _cards_view(query, trees)
    return _view(query, contract, trees, processes=processes)


def carried_cards_view(
    query: ReviewTreesQuery, trees: ReviewTrees, kept: ReviewTreesResult
) -> ReviewTreesResult:
    """These trees' answer around the entries ``kept`` holds for the same invariants (D574)."""

    assert kept.entry_memory is not None  # a carry needs the kept cards' memory state
    return _focused(query, trees, cards=CardEntries(entries=kept.entries, memory=kept.entry_memory))


def _cards_view(query: ReviewTreesQuery, trees: ReviewTrees) -> ReviewTreesResult:
    cards = (
        tree_entries(trees, query.invariants)
        if query.entry is None
        else tree_entry_cards(trees, query.entry, query.entry_side)
    )
    if isinstance(cards, ReviewRefusal):
        return ReviewTreesResult(
            state="refused",
            repository_id=query.repository_id,
            master=query.master,
            leaf_id=query.leaf_id,
            refusal=cards,
        )
    return _focused(query, trees, cards=cards)


def resolve_tree_comparison(
    config: McpRuntimeConfig, query: ReviewTreesQuery
) -> tuple[WorktreeContract, ReviewTrees] | ReviewRefusal | None:
    """The comparison a query names: a recorded number, or the review's own resolution.

    A leaf-wide read that names a number is pinned to that comparison. On a live leaf whose resolved
    number is that one, the resolution is used as it is (the live worklist stays computed); every
    other numbered read reopens the number's record. A focused read (the cards' named invariants, the
    lane, one file) needs only the trees, so it always reopens.

    A *closed* leaf's numbered read never resolves the default first: that resolution records the
    delivered comparison, so asking for an older number -- or for a number the leaf never recorded --
    used to publish a new record nobody asked for (F04). The record a reader asked for is the only
    thing such a read touches.
    """

    if query.number is None:
        return _resolved(config, query)
    contract = recorded_leaf_contract(config, query.repository_id, query.master, query.leaf_id)
    if contract is None:
        return _no_contract(query.master, query.leaf_id)
    if not query.focused and _is_live(contract):
        current = _resolved(config, query)
        if isinstance(current, tuple) and current[1].record.number == query.number:
            return current
    reopened = reopen_review_trees(config.coordination_root, contract, query.number)
    return reopened if not isinstance(reopened, ReviewTrees) else (contract, reopened)


def _is_live(contract: WorktreeContract) -> bool:
    """Whether this enclosure still has a live candidate, whose resolution computes rather than reads.

    Only a live leaf's resolution may run beside a numbered read: its worklist is the captured
    candidate's own, and the record it answers with is the one the number names. A closed leaf's
    default resolution *records* the delivered comparison, which a read of some other number must
    never do.
    """

    worktree = contract.code_worktree
    return worktree is not None and worktree.exists()


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
    beside = _Beside(contract, trees)
    parts = _leaf_wide_parts(contract, trees, processes=processes)
    if isinstance(parts, ReviewRefusal):
        return ReviewTreesResult(
            state="refused",
            repository_id=query.repository_id,
            master=query.master,
            leaf_id=record.leaf_id,
            refusal=parts,
        )
    inventory = beside.inventory()
    overview = query.view == "overview"
    return ReviewTreesResult(
        state="trees",
        repository_id=query.repository_id,
        master=query.master,
        leaf_id=record.leaf_id,
        comparison=record,
        latest=recorded_latest(contract),
        delivered=delivered_notice(contract, record),
        knowledge_sides=trees.sides(),
        code_sides=trees.code_sides,
        currentness=snake_keys(beside.currentness()),
        worklist=None if overview else parts.worklist,
        worklist_summary=summarize_worklist(parts.worklist) if overview else None,
        code_changes=parts.code_changes,
        entry_inventory=None if overview else inventory,
        overview=summarize_inventory(inventory) if overview else None,
    )


class _Beside:
    """The view's entry inventory and currentness, computed in another thread meanwhile (D569).

    Both are pure functions of the comparison's trees. They run in a copy of the request's context
    (so a read whose askers left stops them too) under their own read recorder, beside the worklist
    child the view waits for. Each part is *taken* where the sequential view computed it: its reads
    are replayed into the
    request's recorder there, and an exception it raised is raised there. A view refused before
    then never takes them, so neither their reads nor their failures reach it, as before. When the
    thread cannot start, each part is computed where it is taken, exactly as the sequential view did.
    """

    def __init__(self, contract: WorktreeContract, trees: ReviewTrees) -> None:
        self._done = threading.Event()
        self._parts: dict[str, tuple[Any, BaseException | None, dict[str, str]]] = {}
        self._inline: ReviewTrees | None = None
        context = contextvars.copy_context()
        try:
            threading.Thread(
                target=context.run,
                args=(self._compute, contract, trees),
                name="review-view-beside",
                daemon=True,
            ).start()
        except RuntimeError:  # no thread: the parts are computed in the request, where taken
            self._inline = trees

    def _compute(self, contract: WorktreeContract, trees: ReviewTrees) -> None:
        try:
            restore_review_memos(contract.coordination_root, process_serving_build().source_digest)
            for name, part in (
                ("inventory", tree_entry_inventory),
                ("currentness", side_currentness),
            ):
                value: Any = None
                failure: BaseException | None = None
                with recorded_reads() as reads:
                    try:
                        value = part(trees)
                    except BaseException as error:  # raised where the part is taken
                        failure = error
                self._parts[name] = (value, failure, dict(reads))
        finally:
            self._done.set()
        # After both parts were handed over: what they added to the memos is queued for the answer
        # writer (nothing waits for it).
        save_review_memos()

    def _take(self, name: str) -> Any:
        self._done.wait()
        value, failure, reads = self._parts[name]
        replay_reads(reads)
        if failure is not None:
            raise failure
        return value

    def inventory(self) -> ReviewEntryInventory:
        if self._inline is not None:
            return tree_entry_inventory(self._inline)
        return cast(ReviewEntryInventory, self._take("inventory"))

    def currentness(self) -> dict[str, Any]:
        if self._inline is not None:
            return side_currentness(self._inline)
        return cast(dict[str, Any], self._take("currentness"))


def _leaf_wide_parts(
    contract: WorktreeContract,
    trees: ReviewTrees,
    *,
    processes: ReviewerWorklistProcesses | None = None,
) -> LeafViewParts | ReviewRefusal:
    """The view's worklist: the kept ones for these exact trees, or computed.

    A live comparison's parts are looked up under everything they are a function of
    (:func:`~.review_leaf_view_memo.leaf_view_key`). They are kept only when every read behind
    them succeeded: both knowledge sides readable and a
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
        if key is None:
            answered, moved = _leaf_parts_once(contract, trees, None, processes)
        else:
            answered, moved = _joined_leaf_parts(contract, trees, key, processes)
        if answered is not None:
            return answered
    return ReviewRefusal(
        code="inputs_changing",
        detail=(
            "task documents or other inputs of the view did not match the request in "
            f"either of {_COMPUTE_ATTEMPTS} computation attempts: {'; '.join(moved)}"
        )[:PROSE_MAX_LENGTH],
        next_action="retry once the named inputs stop changing",
        offending_input="; ".join(moved)[:REFERENCE_MAX_LENGTH],
    )


#: One leaf-wide computation per exact view key in flight (D569): an open that arrives while the
#: same trees' view is being built -- by a turn end or a watched change, or by another open --
#: waits for that computation instead of starting a second, and replays the reads it consumed.
_LEAF_PARTS_FLIGHTS: SharedFlights[
    LeafViewKey,
    tuple[tuple[LeafViewParts | ReviewRefusal | None, tuple[str, ...]], dict[str, str]],
] = SharedFlights()


def _joined_leaf_parts(
    contract: WorktreeContract,
    trees: ReviewTrees,
    key: LeafViewKey,
    processes: ReviewerWorklistProcesses | None,
) -> tuple[LeafViewParts | ReviewRefusal | None, tuple[str, ...]]:
    """One attempt, shared with every asker of the same view key while it is in flight.

    A waiter's wait is logged with its length: an open that joined a build reports the time it
    waited, never a computation it did not run.
    """

    def owned() -> tuple[
        tuple[LeafViewParts | ReviewRefusal | None, tuple[str, ...]], dict[str, str]
    ]:
        with recorded_reads() as consumed:
            answered = _leaf_parts_once(contract, trees, key, processes)
        return answered, dict(consumed)

    started = time.monotonic()
    (answered, reads), owner = _LEAF_PARTS_FLIGHTS.share(key, owned)
    replay_reads(reads)
    if owner:
        return answered
    logger.info(
        "joined the leaf-wide view in flight for %s; waited %.2f s",
        contract.leaf_id or contract.task_name,
        time.monotonic() - started,
    )
    return deepcopy(answered)


def _leaf_parts_once(
    contract: WorktreeContract,
    trees: ReviewTrees,
    key: LeafViewKey | None,
    processes: ReviewerWorklistProcesses | None,
) -> tuple[LeafViewParts | ReviewRefusal | None, tuple[str, ...]]:
    """One computation attempt: its answer, or ``None`` with the inputs that moved meanwhile."""

    try:
        parts, settled, reads = _compute_leaf_parts(contract, trees, processes=processes)
    except WorklistProcessError as error:
        return (
            ReviewRefusal(
                code=cast(ReviewRefusalCode, error.code),
                detail=str(error)[:PROSE_MAX_LENGTH],
                next_action=error.next_action,
                offending_input="reviewer worklist process",
            ),
            (),
        )
    moved = moved_inputs(key, reads)
    if moved:
        return None, moved
    # A computation whose every asker is gone -- a superseded build whose child landed -- keeps
    # nothing (D569 mean 4).
    raise_if_read_cancelled()
    if key is not None and settled:
        remember(key, parts, reads)
    return parts, ()


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
            worklist, settled = _worklist_view(
                contract, trees, live=trees.live, processes=processes
            )
    finally:
        replay_reads(reads)
    parts = LeafViewParts(worklist, worklist.code_changes)
    decided = all(state.state != "undecided" for state in worklist.item_states.values())
    return (
        parts,
        available
        and settled
        and decided
        and worklist.code_changes is not None
        and worklist.code_changes.state == "facts",
        reads,
    )


def _focused(
    query: ReviewTreesQuery,
    trees: ReviewTrees,
    *,
    cards: CardEntries | None = None,
    lane: ReviewUnexplainedLane | None = None,
    file_classification: ReviewFileClassification | None = None,
) -> ReviewTreesResult:
    """The comparison and one focused answer: the cards' entries, the lane, or one file.

    Only the cards' answer carries ``entry_memory`` (MIK-R89 v2 rule 3); the lane and file answers,
    like the leaf-wide view, are unchanged by it.
    """

    return ReviewTreesResult(
        state="trees",
        repository_id=query.repository_id,
        master=query.master,
        leaf_id=trees.record.leaf_id,
        comparison=trees.record,
        knowledge_sides=trees.sides(),
        code_sides=trees.code_sides,
        entries=() if cards is None else cards.entries,
        entry_memory=None if cards is None else cards.memory,
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
    """``value`` with every identifier-shaped camelCase key re-spelled in snake_case, recursively.

    A worklist's documents run to hundreds of thousands of values, so the plain JSON types are
    told apart by their exact type first; anything else is classified as before (D569).
    """

    kind = type(value)
    if kind in _PLAIN_SCALARS:
        return value
    if kind is dict or (kind is not list and kind is not tuple and isinstance(value, Mapping)):
        return {_snake(key): snake_keys(item) for key, item in value.items()}
    if kind is list or kind is tuple or isinstance(value, list | tuple):
        return [snake_keys(item) for item in value]
    return value


_PLAIN_SCALARS: Final = frozenset({str, int, float, bool, type(None)})


def _snake(key: object) -> object:
    if not isinstance(key, str) or _IDENTIFIER_KEY.match(key) is None:
        return key
    return _CAMEL_HUMP.sub(lambda hump: f"_{hump.group(1).lower()}", key)


# -- rule 2: the Git diff of the memory trees ----------------------------------------------------


def knowledge_tree_diff(
    repository: Path, before: str, after: str, *, served: Callable[[str], bool] = is_indexed_path
) -> ReviewKnowledgeTreeDiff:
    """The knowledge files that differ between two memory trees, with their Git patches, grouped.

    ``served`` says which changed paths are answered; the reviewer shows what its index reads.
    """

    return _measured_tree_diff(repository, before, after, served).diff


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

    def among(self, served: Callable[[str], bool]) -> bool:
        return served(self.path) or (self.old_path is not None and served(self.old_path))

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


def _measured_tree_diff(
    repository: Path, before: str, after: str, served: Callable[[str], bool] = is_indexed_path
) -> _MeasuredDiff:
    """The knowledge diff from a bounded number of Git children (MIK-R40 rule 4).

    Git is asked three questions whatever the number of changed files: which paths changed and
    which blobs they hold, the patch of the two trees, and the bytes of those blobs. The patch is
    one text cut at its file headers; every cut is bound to the exact path Git listed, including
    C-quoted names. A filename is an address: bracket or glob characters do not select siblings.
    A failed aggregate read is retried once and leaves the result unkeepable.
    """

    listed = _listed_changes(repository, before, after)
    kept = [one.among(served) for one in listed]
    patches, complete = [""] * len(listed), True
    if any(kept):
        patches, complete = _patches(repository, before, after, listed)
    indexed = [(one, patch) for one, patch, keep in zip(listed, patches, kept, strict=True) if keep]
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

    candidate = CandidateTrees(
        code=trees.record.code_candidate.tree, memory=trees.record.memory_candidate.tree
    )
    if live and (trees.record.code_base.commit is None or trees.record.memory_base.commit is None):
        raise WorklistProcessError("reviewer worklist comparison has no captured base commits")
    base = CapturedBase(
        code=trees.record.code_base.commit or trees.record.code_base.tree,
        memory=trees.record.memory_base.commit or trees.record.memory_base.tree,
        read_tree=trees.before.wire.tree,
    )
    document = isolated_leaf_worklist(
        contract, candidate=candidate, base=base, processes=processes, trees=trees
    )
    source: Literal["computed", "persisted", "absent"] = "computed" if live else "persisted"
    review_parts = {} if document is None else document.get("_review", {})
    facts = (
        ReviewCodeChanges.model_validate(review_parts["code_changes"])
        if "code_changes" in review_parts
        else None
    )
    if not document or "state" not in document:
        return (
            ReviewWorklistView(
                source="absent",
                code_changes=facts,
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
        code_changes=facts,
        source=source,
        bound=_bound(document.get("pairing") or {}, trees),
        state=str(document.get("state")),
        items=tuple(snake_keys(items)),
        history_rows=tuple(snake_keys(_history_rows(trees, items))),
        item_states={
            key: ItemVerdict.model_validate(value)
            for key, value in review_parts.get("item_states", {}).items()
        },
        changes=tuple(snake_keys(list(document.get("changes", ())))),
        incomplete=tuple(snake_keys(list(document.get("incomplete", ())))),
    )
    return view, live and document.get("state") == "complete"


_bound = worklist_bound


def _no_contract(master: str, leaf_id: str) -> ReviewRefusal:
    return ReviewRefusal(
        code="candidate_unresolved",
        detail="no readable leaf enclosure contract records this leaf under the named master",
        next_action="open the review for a leaf whose enclosure contract exists",
        offending_input=f"{master}/{leaf_id}",
    )
