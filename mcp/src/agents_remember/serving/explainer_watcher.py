"""The moved watcher: a leaf moved past its explainer's binding (MIK-R186 rule 7).

The environment serves one comparison. When the leaf's state moves past it, the page must say so
without waking the explainer (MIK-R117): this module answers *whether* it moved, *how far*, and the
one line the page and the stream show. It composes four things and owns none of them:

* the binding, read from the environment's ``binding.json`` (:func:`canvas.explainer_state.read_binding`);
* the comparison owner's capture, called **without recording** -- it writes Git objects, and this
  module never pins a ref and never writes a comparison record. Update is what records (rule 6);
* the per-block change count over the changed blocks, from the review lane's own hunk and non-text
  classification (the same subjects MIK-R187 fingerprints), never a second diff engine;
* the master line's two tip commits, read from the series contract, for a ``planning`` leaf's
  planned binding -- that path captures nothing.

Nothing here sends a message, writes a held store or starts a host call. :class:`MoveNoticeChannel`
is in-process and bounded: it carries the notice to the open streams and forgets it.

A notice is one record: ``kind``, ``from``, ``to``, ``blocksChanged`` and ``updateOffered`` (plus the
master tip pair for ``master-moved`` and the exact published ``text``). :func:`notice_for` is what the
``/state`` payload drops into its ``moved`` field; the stream publishes :func:`notice_event` on the
established event shape (``review.memory``'s is the precedent: repo, master, leaf, and the payload).
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections.abc import AsyncGenerator, Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any, Literal, Protocol

from agents_remember.canvas.explainer_paths import (
    GENERATION_SUFFIX,
    explainer_roots,
    leaf_workspace,
    repository_path,
)
from agents_remember.canvas.explainer_staleness import moved_sides
from agents_remember.canvas.explainer_state import (
    ExplainerBinding,
    ExplainerStateError,
    PlannedIdentity,
    agent_id,
    generation,
    read_binding,
)
from agents_remember.canvas.paths import PathRefused, task_root
from agents_remember.kernel.git_command import run_git
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.memory.knowledge.tree_observation import TreeChange
from agents_remember.models.knowledge.review_frame import PLANNED_OWNER, FrameBinding
from agents_remember.models.knowledge.review_refusal import ReviewRefusal
from agents_remember.models.knowledge.review_trees import (
    ReviewKnowledgeSide,
    ReviewTreeComparisonRecord,
    ReviewTrees,
    ReviewTreeSide,
    TreeKnowledge,
)
from agents_remember.models.role_identity import LEAF_ROLES
from agents_remember.serving.paseo.paseo_plugin_files import read_role_agents
from agents_remember.serving.projections.host_activity import activity_directory
from agents_remember.tasks.planned_comparison import (
    contract_branch_lines,
    master_is_organizational,
    resolve_master_contract,
)
from agents_remember.tasks.task_paths import slugify
from agents_remember.worktrees.modules.future_code_candidate import (
    FutureCodeCandidateError,
    capture_future_code_candidate,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract


class LaneObservationPort(Protocol):
    """What one lane observation reports: whether its trees were readable, and their entries."""

    @property
    def available(self) -> bool: ...

    @property
    def detail(self) -> str | None: ...

    @property
    def entries(self) -> Sequence[TreeChange]: ...


class ClassificationPort(Protocol):
    """One changed path classified: its hunks and, when it is not text, its non-text change."""

    @property
    def hunks(self) -> Sequence[object]: ...

    @property
    def non_text(self) -> object | None: ...


class TreeLanePort(Protocol):
    """The review lane open over one comparison: observe the changes, classify each one."""

    def observe(self) -> LaneObservationPort: ...

    def classify(self, change: TreeChange) -> ClassificationPort: ...


@dataclass(frozen=True)
class WatcherApplicationPorts:
    """Application-tier comparison and lane ports injected into the moved watcher.

    The serving tier cannot import the adapters below; the composition root builds this one value
    and hands it to :func:`measure_notice`, :func:`notice_for` and :class:`GitComparisonOwner`.
    """

    recorded_leaf_contract: Callable[[McpRuntimeConfig, str, str, str], WorktreeContract | None]
    comparison_records: Callable[[Path, str], Sequence[ReviewTreeComparisonRecord]]
    memory_converted: Callable[[WorktreeContract], bool | str]
    draft_live_comparison: Callable[[Path, WorktreeContract, Path, str], ReviewTreeComparisonRecord]
    open_tree_lane: Callable[[ReviewTrees], AbstractContextManager[TreeLanePort]]
    execution_receipts: Callable[[Path, str], Sequence[Mapping[str, object]]]
    capture_errors: tuple[type[BaseException], ...] = ()
    lane_errors: tuple[type[BaseException], ...] = ()


__all__ = [
    "NOTICE_EVENT",
    "BlockCounter",
    "CapturedComparison",
    "ComparisonOwner",
    "GitComparisonOwner",
    "LeafSelection",
    "MoveNoticeChannel",
    "MovedNotice",
    "blocks_changed",
    "is_planned",
    "leaf_seats",
    "master_tips",
    "measure_notice",
    "notice_event",
    "notice_for",
    "notices",
    "unexplained_turn_notice",
]

logger = logging.getLogger(__name__)

#: The stream event that carries a notice; ``review.memory`` is the sibling on the same channel.
NOTICE_EVENT = "review.explainer.moved"

NoticeKind = Literal["comparison-moved", "master-moved"]
#: How many notices one subscriber may hold before the oldest is dropped.
SUBSCRIBER_QUEUE = 8


class LeafScopeLike(Protocol):
    """The seat service's leaf scope, read structurally so this module imports no service."""

    @property
    def repository_id(self) -> str: ...

    @property
    def task(self) -> str: ...

    @property
    def leaf_id(self) -> str: ...


@dataclass(frozen=True)
class LeafSelection:
    """Which leaf: the review page's own three names, never a path."""

    repository_id: str
    master: str
    leaf_id: str

    def as_key(self) -> tuple[str, str, str]:
        return (self.repository_id, self.master, self.leaf_id)

    @classmethod
    def of(cls, scope: LeafScopeLike) -> LeafSelection:
        """The selection of the seat service's ``LeafScope`` (its ``task`` is the review's master)."""

        return cls(repository_id=scope.repository_id, master=scope.task, leaf_id=scope.leaf_id)


@dataclass(frozen=True)
class CapturedComparison:
    """The four trees a capture produced in memory: objects written, nothing pinned or recorded."""

    record: ReviewTreeComparisonRecord


#: How many review blocks changed between two candidate states; ``None`` when unreadable.
BlockCounter = Callable[[FrameBinding, Any], int | None]


class ComparisonOwner(Protocol):
    """The comparison owner's capture, called without recording (rule 7)."""

    def capture(self, selection: LeafSelection, /) -> CapturedComparison | ReviewRefusal | None: ...


@dataclass(frozen=True)
class MovedNotice:
    """What moved past one binding, and the offer the page shows beside it.

    ``from_number`` is the binding's comparison number and ``to_number`` the latest recorded
    comparison's, when that record holds the captured trees; ``None`` reads "new state". A
    ``master-moved`` notice carries the two master tip commits and no numbers.
    """

    kind: NoticeKind
    from_number: int | None
    to_number: int | None
    blocks_changed: int
    update_offered: bool
    commit_from: str | None = None
    commit_to: str | None = None
    cleared: bool = False
    has_explainer: bool = True

    @classmethod
    def cleared_notice(cls) -> MovedNotice:
        return cls(
            kind="comparison-moved",
            from_number=None,
            to_number=None,
            blocks_changed=0,
            update_offered=False,
            cleared=True,
        )

    @property
    def text(self) -> str:
        """The one line published on the stream and shown by the page (rule 7, at quote level)."""

        if self.cleared:
            return "current"
        if self.kind == "master-moved":
            return f"master moved: {self.commit_from} -> {self.commit_to}"
        source = _number(self.from_number, "no number")
        target = "new state" if self.to_number is None else f"#{self.to_number}"
        return f"task moved: {source} -> {target}, {self.blocks_changed} blocks changed"

    def as_record(self) -> dict[str, Any]:
        """The ``/state`` payload's ``moved`` record, and the stream event's own fields.

        ``blocks`` is the same count under the page model's name, so a reader that predates this
        record still reads one number rather than zero; ``text`` is the published line itself.
        """

        record: dict[str, Any] = {
            "kind": "current" if self.cleared else self.kind,
            "from": self.from_number,
            "to": self.to_number,
            "blocksChanged": self.blocks_changed,
            "blocks": self.blocks_changed,
            "updateOffered": self.update_offered,
            "hasExplainer": self.has_explainer,
            "cleared": self.cleared,
            "text": self.text,
        }
        if self.cleared:
            record["moved"] = None
        if self.commit_from is not None and self.commit_to is not None:
            record["commitFrom"] = self.commit_from
            record["commitTo"] = self.commit_to
        return record


def _number(value: int | None, fallback: str) -> str:
    return fallback if value is None else f"#{value}"


def _binding_key(binding: ExplainerBinding) -> str:
    held = binding.binding
    return f"{held.comparison_owner}:{held.comparison_reference}:{held.four_tree_digest}:{binding.snapshot}"


@dataclass(frozen=True)
class ComparisonMeasurement:
    """The two collaborators one measurement may replace: the capture owner and the block counter."""

    owner: ComparisonOwner | None = None
    counter: BlockCounter | None = None


def measure_notice(
    config: McpRuntimeConfig,
    selection: LeafSelection,
    ports: WatcherApplicationPorts,
    *,
    measurement: ComparisonMeasurement | None = None,
) -> tuple[Literal["moved", "clean", "unreadable"], MovedNotice | None, str | None]:
    """Measure whether one leaf's environment moved, is clean, or could not be read."""

    measurement = measurement or ComparisonMeasurement()
    state, binding = _binding_state(config, selection)
    if state == "unreadable":
        return ("unreadable", None, None)
    if binding is None:
        return ("clean", None, None)
    key = _binding_key(binding)
    if is_planned(binding):
        outcome, notice = _measure_master_moved(config, selection, binding)
        return (outcome, notice, key)
    outcome, notice = _measure_comparison_moved(config, selection, binding, ports, measurement)
    return (outcome, notice, key)


def notice_for(
    config: McpRuntimeConfig,
    selection: LeafSelection,
    ports: WatcherApplicationPorts,
    *,
    measurement: ComparisonMeasurement | None = None,
) -> MovedNotice | None:
    """The notice one leaf's environment earns now, or ``None`` when nothing moved.

    A leaf without an environment has no notice (rule 7's last clause). A planned binding is
    compared against the master line's tips and captures nothing; a recorded one is captured
    through the owner and compared side by side. Any unreadable input answers ``None``: a watcher
    that cannot tell never claims the leaf moved, and never claims it did not move either.
    """

    return measure_notice(config, selection, ports, measurement=measurement)[1]


def _binding_state(
    config: McpRuntimeConfig, selection: LeafSelection
) -> tuple[Literal["present", "absent", "unreadable"], ExplainerBinding | None]:
    try:
        workspace = leaf_workspace(
            config, selection.repository_id, selection.master, selection.leaf_id
        )
        binding = read_binding(workspace)
    except (
        ExplainerStateError,
        PathRefused,
        OSError,
        RuntimeError,
        ValueError,
    ):  # the watcher never breaks a page: an unreadable leaf is not a notice
        logger.warning("the moved watcher cannot read %s's binding", selection.leaf_id)
        return ("unreadable", None)
    return ("absent", None) if binding is None else ("present", binding)


def _binding(config: McpRuntimeConfig, selection: LeafSelection) -> ExplainerBinding | None:
    return _binding_state(config, selection)[1]


def is_planned(binding: ExplainerBinding) -> bool:
    """Whether this environment serves a planning leaf's planned comparison (rule 4)."""

    return binding.planned is not None or binding.binding.comparison_owner == PLANNED_OWNER


def _measure_comparison_moved(
    config: McpRuntimeConfig,
    selection: LeafSelection,
    binding: ExplainerBinding,
    ports: WatcherApplicationPorts,
    measurement: ComparisonMeasurement,
) -> tuple[Literal["moved", "clean", "unreadable"], MovedNotice | None]:
    captured = (measurement.owner or GitComparisonOwner(config, ports)).capture(selection)
    if not isinstance(captured, CapturedComparison):
        return ("unreadable", None)
    held = binding.binding
    if not moved_sides(held, _current_binding(held, captured.record)):
        return ("clean", None)
    blocks = (measurement.counter or partial(blocks_changed, ports=ports))(held, captured.record)
    if blocks is None:
        return ("unreadable", None)
    return (
        "moved",
        MovedNotice(
            kind="comparison-moved",
            from_number=held.comparison_number,
            to_number=_recorded_number(config, selection, captured.record, ports),
            blocks_changed=blocks,
            update_offered=True,
        ),
    )


def _current_binding(bound: FrameBinding, record: ReviewTreeComparisonRecord) -> FrameBinding:
    """The capture as a binding of the same identity, so only the sides can differ.

    The capture is not a record and carries no number of its own; borrowing the bound number and
    reference keeps the identity test about the four sides (and the conversion) alone.
    """

    numbered = record.model_copy(update={"number": bound.comparison_number})
    return FrameBinding.from_record(
        numbered,
        comparison_owner=bound.comparison_owner,
        comparison_reference=bound.comparison_reference,
    )


def _recorded_number(
    config: McpRuntimeConfig,
    selection: LeafSelection,
    captured: ReviewTreeComparisonRecord,
    ports: WatcherApplicationPorts,
) -> int | None:
    """The latest recorded comparison's number when that record holds the captured trees."""

    try:
        root = task_root(config, selection.repository_id, selection.master)
    except (PathRefused, OSError):
        return None
    records = ports.comparison_records(root, selection.leaf_id)
    if not records:
        return None
    latest = records[-1]
    return latest.number if latest.same_trees(captured) else None


def _measure_master_moved(
    config: McpRuntimeConfig, selection: LeafSelection, binding: ExplainerBinding
) -> tuple[Literal["moved", "clean", "unreadable"], MovedNotice | None]:
    """A ``planning`` leaf's notice: the master tips against the planned binding (rule 7)."""

    planned = binding.planned
    tips = master_tips(config, selection, planned)
    if planned is None or tips is None:
        return ("unreadable", None)
    code_tip, memory_tip = tips
    if code_tip != planned.code_tip:
        pair = (planned.code_tip, code_tip)
    elif memory_tip != planned.memory_tip:
        pair = (planned.memory_tip, memory_tip)
    else:
        return ("clean", None)
    return (
        "moved",
        MovedNotice(
            kind="master-moved",
            from_number=None,
            to_number=None,
            blocks_changed=0,
            update_offered=True,
            commit_from=pair[0],
            commit_to=pair[1],
        ),
    )


def master_tips(
    config: McpRuntimeConfig, selection: LeafSelection, planned: PlannedIdentity | None = None
) -> tuple[str, str] | None:
    """The tips of the two lines the planned binding was built from.

    The contract names each side's work and source branches, and the planned binding was built at
    one of them. The line that still points at -- or contains -- the bound tip is that one, so a
    notice follows the master's own line rather than a guess between the two names.
    """

    try:
        task_root(config, selection.repository_id, selection.master)
        contract = resolve_master_contract(
            config.coordination_root, selection.repository_id, selection.master
        )
        if contract is None:
            return None
        organizational = master_is_organizational(
            config.coordination_root, selection.repository_id, selection.master
        )
        code_lines, memory_lines = contract_branch_lines(
            contract.read_text(encoding="utf-8"), organizational=organizational
        )
        code = _line_tip(
            repository_path(config, selection.repository_id),
            None if planned is None else planned.code_tip,
            code_lines,
        )
        memory = _line_tip(
            repository_path(config, selection.repository_id, memory=True),
            None if planned is None else planned.memory_tip,
            memory_lines,
        )
    except (KeyError, PathRefused, OSError, UnicodeDecodeError, ValueError):
        return None
    if code is None or memory is None:
        return None
    return (code, memory)


def contract_branches(text: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Each side's ``work_branch`` and ``source_branch``, work_branch first; ``HEAD`` when unnamed."""

    return contract_branch_lines(text)


def _line_tip(repository: Path, bound: str | None, lines: tuple[str, ...]) -> str | None:
    """The current tip of the line the bound commit came from, or of the first named line."""

    if bound is not None:
        holding = _holding_line(repository, bound, lines)
        if holding is not None:
            return rev_parse(repository, holding)
    for line in lines:
        resolved = rev_parse(repository, line)
        if resolved is not None:
            return resolved
    return None


def _holding_line(repository: Path, tip: str, lines: tuple[str, ...]) -> str | None:
    """The named line that still holds ``tip``, at its tip or in its ancestry, in priority order."""

    for line in lines:
        current = rev_parse(repository, line)
        if current is None:
            continue
        if current == tip:
            return line
        try:
            contained = run_git(repository, ["merge-base", "--is-ancestor", tip, line])
        except (OSError, RuntimeError):
            continue
        if contained.returncode == 0:
            return line
    return None


def rev_parse(repository: Path, ref: str) -> str | None:
    """One commit id, or ``None`` when Git does not answer; never a guess."""

    try:
        result = run_git(repository, ["rev-parse", "--verify", "--quiet", ref])
    except (OSError, RuntimeError):
        return None
    if result.returncode != 0:
        return None
    resolved = result.stdout.strip()
    return resolved or None


class GitComparisonOwner:
    """The tree comparison owner's own capture, called without its recording step (rule 7).

    ``capture`` derives the code candidate through the shipped private-index capture and drafts the
    four trees in memory through the owner's own draft; it pins no ref and writes no record. The
    contract is looked up the way every review looks it up, and a test may hand one in.
    """

    def __init__(
        self,
        config: McpRuntimeConfig,
        ports: WatcherApplicationPorts,
        *,
        resolve_contract: Callable[[LeafSelection], WorktreeContract | None] | None = None,
    ) -> None:
        self._config = config
        self._ports = ports
        self._resolve = resolve_contract or self._leaf_contract

    def _leaf_contract(self, selection: LeafSelection) -> WorktreeContract | None:
        return self._ports.recorded_leaf_contract(
            self._config, selection.repository_id, selection.master, selection.leaf_id
        )

    def capture(self, selection: LeafSelection) -> CapturedComparison | None:
        contract = self._resolve(selection)
        if contract is None or not self._ports.memory_converted(contract):
            return None
        worktree = contract.memory_worktree
        repository = contract.memory_repo_path or worktree
        if worktree is None or repository is None:
            return None
        try:
            code_tree = capture_future_code_candidate(contract).codeCandidateTree
            record = self._ports.draft_live_comparison(
                self._config.coordination_root, contract, repository, code_tree
            )
        except (
            FutureCodeCandidateError,
            *self._ports.capture_errors,
            OSError,
            RuntimeError,
            ValueError,
        ) as error:
            logger.warning("the moved watcher cannot capture %s: %s", selection.leaf_id, error)
            return None
        return CapturedComparison(record)


def blocks_changed(
    binding: FrameBinding,
    captured: ReviewTrees | ReviewTreeComparisonRecord,
    *,
    ports: WatcherApplicationPorts,
) -> int | None:
    """How many review blocks changed between the bound candidate and the captured one.

    The blocks are the review lane's own: one per changed text hunk and one per non-text change,
    the identities MIK-R187 fingerprints. The lane reads the two candidate states as its base and
    candidate, so no second diff is computed here; ``None`` when the trees cannot be read.
    """

    try:
        trees = changes_between(binding, captured)
        return _count_blocks(trees, ports)
    except (*ports.lane_errors, OSError, RuntimeError, ValueError) as error:
        logger.warning("the moved watcher cannot count its blocks: %s", error)
        return None


def changes_between(
    binding: FrameBinding, captured: ReviewTrees | ReviewTreeComparisonRecord
) -> ReviewTrees:
    """The two candidate states as one in-memory comparison, for the lane to classify.

    The explainer's Update hands this the bound comparison's ``ReviewTrees`` — the whole view, whose
    candidate sides hang off ``record`` — while the watcher's own callers hand it a bare record.
    Reading ``code_candidate`` off the view raised ``AttributeError`` and answered every Update of a
    moved page with a 500, so the record is taken from either shape.
    """

    captured = captured.record if isinstance(captured, ReviewTrees) else captured
    record = ReviewTreeComparisonRecord(
        task_id=binding.task_id,
        leaf_id=binding.leaf_id,
        number=0,
        code_base=ReviewTreeSide(
            repository=binding.code_candidate_repository, tree=binding.code_candidate_tree
        ),
        code_candidate=ReviewTreeSide(
            repository=captured.code_candidate.repository, tree=captured.code_candidate.tree
        ),
        memory_base=ReviewTreeSide(
            repository=binding.memory_candidate_repository, tree=binding.memory_candidate_tree
        ),
        memory_candidate=ReviewTreeSide(
            repository=captured.memory_candidate.repository,
            tree=captured.memory_candidate.tree,
        ),
        recorded_at="the moved watcher's in-memory comparison",
    )
    memory_repository = Path(captured.memory_candidate.repository)
    return ReviewTrees(
        record=record,
        record_path=None,
        memory_repository=memory_repository,
        before=_unread_knowledge("before", record.memory_base.tree),
        after=_unread_knowledge("after", record.memory_candidate.tree),
    )


def _unread_knowledge(side: Literal["before", "after"], tree: str) -> TreeKnowledge:
    """One memory side named as unread: the block count needs the hunks, not the attribution."""

    wire = ReviewKnowledgeSide(
        side=side,
        state="available",
        tree=tree,
        index_state=None,
        detail="the moved watcher reads the changed blocks only, not the knowledge index",
    )
    return TreeKnowledge(wire=wire, database=None)


def _count_blocks(trees: ReviewTrees, ports: WatcherApplicationPorts) -> int | None:
    with ports.open_tree_lane(trees) as lane:
        observed = lane.observe()
        if not observed.available:
            logger.warning("the moved watcher cannot list the changed paths: %s", observed.detail)
            return None
        return _blocks_of(lane, observed.entries)


def _blocks_of(lane: TreeLanePort, changes: Sequence[TreeChange]) -> int:
    total = 0
    for change in changes:
        result = lane.classify(change)
        total += len(result.hunks) + (1 if result.non_text is not None else 0)
    return total


@dataclass
class _Subscriber:
    loop: asyncio.AbstractEventLoop
    queue: asyncio.Queue[MovedNotice] = field(
        default_factory=lambda: asyncio.Queue(SUBSCRIBER_QUEUE)
    )


class MoveNoticeChannel:
    """The in-process publication of notices to the open review streams (rule 7).

    It holds one latest notice per leaf and hands each subscriber its own bounded queue. Nothing is
    sent to a seat, nothing is held, and nothing is written: closing the stream forgets the reader.
    Publishing is deduplicated, so a page that reconnects does not repeat a notice it already has.
    """

    def __init__(self, *, history: int = 64) -> None:
        self._lock = threading.Lock()
        self._latest: dict[tuple[str, str, str], MovedNotice] = {}
        self._binding_keys: dict[tuple[str, str, str], str] = {}
        self._subscribers: dict[tuple[str, str, str], list[_Subscriber]] = {}
        self._history = max(history, 1)

    def has_subscribers(self, selection: LeafSelection) -> bool:
        with self._lock:
            return bool(self._subscribers.get(selection.as_key()))

    def publish(
        self,
        selection: LeafSelection,
        notice: MovedNotice,
        *,
        binding_key: str | None = None,
    ) -> bool:
        """Record the notice as this leaf's latest and hand it to every open stream."""

        key = selection.as_key()
        with self._lock:
            if self._latest.get(key) == notice and (
                binding_key is None or self._binding_keys.get(key) == binding_key
            ):
                return False
            self._latest[key] = notice
            if binding_key is not None:
                self._binding_keys[key] = binding_key
            while len(self._latest) > self._history:
                evicted = next(iter(self._latest))
                self._latest.pop(evicted, None)
                self._binding_keys.pop(evicted, None)
            subscribers = list(self._subscribers.get(key, ()))
        for subscriber in subscribers:
            try:
                subscriber.loop.call_soon_threadsafe(_offer, subscriber.queue, notice)
            except RuntimeError:  # that reader's stream is gone; the notice is still the latest
                continue
        return True

    def latest(
        self, selection: LeafSelection, *, binding_key: str | None = None
    ) -> MovedNotice | None:
        key = selection.as_key()
        with self._lock:
            if binding_key is not None:
                held_key = self._binding_keys.get(key)
                if held_key is not None and held_key != binding_key:
                    self._latest.pop(key, None)
                    self._binding_keys.pop(key, None)
                    return None
            return self._latest.get(key)

    def listen(self, selection: LeafSelection) -> AsyncGenerator[MovedNotice]:
        """Every notice for one leaf from now on, after this leaf's latest one when it has it."""

        return self._listen(selection)

    async def _listen(self, selection: LeafSelection) -> AsyncGenerator[MovedNotice]:
        subscriber = _Subscriber(loop=asyncio.get_running_loop())
        key = selection.as_key()
        with self._lock:
            self._subscribers.setdefault(key, []).append(subscriber)
        last: MovedNotice | None = None
        try:
            latest = self.latest(selection)
            if latest is not None:
                last = latest
                yield latest
            while True:
                notice = await subscriber.queue.get()
                if notice != last:
                    last = notice
                    yield notice
        finally:
            with self._lock:
                readers = self._subscribers.get(key, [])
                if subscriber in readers:
                    readers.remove(subscriber)
                if not readers:
                    self._subscribers.pop(key, None)

    def clear(self, selection: LeafSelection | None = None, *, broadcast: bool = True) -> None:
        """Forget one leaf's notices, or every leaf's, and notify open streams."""

        cleared = MovedNotice.cleared_notice()
        subscribers: list[_Subscriber] = []
        with self._lock:
            if selection is None:
                self._latest.clear()
                self._binding_keys.clear()
                if broadcast:
                    for group in self._subscribers.values():
                        subscribers.extend(group)
            else:
                key = selection.as_key()
                self._latest.pop(key, None)
                self._binding_keys.pop(key, None)
                if broadcast:
                    subscribers.extend(self._subscribers.get(key, ()))
        for subscriber in subscribers:
            try:
                subscriber.loop.call_soon_threadsafe(_offer, subscriber.queue, cleared)
            except RuntimeError:
                continue


def _offer(queue: asyncio.Queue[MovedNotice], notice: MovedNotice) -> None:
    if queue.full():
        queue.get_nowait()
    queue.put_nowait(notice)


#: The process's one channel: the watcher publishes here and every open stream reads it.
notices = MoveNoticeChannel()


def notice_event(selection: LeafSelection, notice: MovedNotice) -> dict[str, Any]:
    """One stream event's data: the leaf's names beside the notice's own record."""

    return {
        "repo": selection.repository_id,
        "master": selection.master,
        "leaf": selection.leaf_id,
        **notice.as_record(),
    }


def _doc_id_at(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if isinstance(doc, dict) and isinstance(doc.get("id"), str) and doc["id"]:
        return str(doc["id"])
    return None


def _match_explainer_dir(exp_dir: Path, cleaned: str) -> str | None:
    if not exp_dir.is_dir():
        return None
    if (exp_dir / cleaned).is_dir():
        return cleaned
    target_slug = slugify(cleaned)
    for child in sorted(exp_dir.iterdir()):
        if child.is_dir() and slugify(child.name) == target_slug:
            return child.name
    return None


def _resolve_leaf_id(
    coordination_root: Path, repository_id: str, master: str, stem_or_id: str
) -> str:
    cleaned = stem_or_id.removesuffix(".md").removesuffix(".json")
    task_dir = coordination_root / "tasks" / repository_id / master
    direct = _doc_id_at(task_dir / f"{cleaned}.json")
    if direct is not None:
        return direct
    matched_exp = _match_explainer_dir(
        coordination_root / "explainer" / repository_id / master, cleaned
    )
    if matched_exp is not None:
        return matched_exp
    if task_dir.is_dir():
        target_slug = slugify(cleaned)
        for path in sorted(task_dir.glob("*.json")):
            if path.name == "task.json":
                continue
            doc_id = _doc_id_at(path)
            if doc_id and (doc_id == cleaned or slugify(doc_id) == target_slug):
                return doc_id
    return cleaned


def _watched_leaf(
    config: McpRuntimeConfig,
    selection: LeafSelection,
    active_leaves: set[tuple[str, str, str]],
    active_streams: Callable[[LeafSelection], bool] | None,
) -> bool:
    """Whether this leaf is one the watcher must answer for: an environment or an open stream."""

    if selection.as_key() in active_leaves:
        return True
    if active_streams is not None and active_streams(selection):
        return True
    try:
        workspace = leaf_workspace(
            config, selection.repository_id, selection.master, selection.leaf_id
        )
    except (PathRefused, OSError):
        return False
    return workspace.is_dir()


def _execution_receipt_seats(
    config: McpRuntimeConfig,
    active_leaves: set[tuple[str, str, str]],
    active_streams: Callable[[LeafSelection], bool] | None,
    ports: WatcherApplicationPorts,
) -> dict[str, LeafSelection]:
    """The Worker/Reviewer/Curator seats of the leaves this watcher watches.

    Each active leaf's own master folder is listed once (the number of executions a master ran),
    never the task tree: a receipt is read because its leaf is watched, not to discover the leaf.
    """

    found: dict[str, LeafSelection] = {}
    for repository_id, master, leaf_id in sorted(active_leaves):
        selection = LeafSelection(repository_id, master, leaf_id)
        if not _watched_leaf(config, selection, active_leaves, active_streams):
            continue
        try:
            master_root = task_root(config, repository_id, master)
        except (PathRefused, OSError):
            continue
        for receipt in ports.execution_receipts(master_root, leaf_id):
            agent = receipt.get("agentId")
            if not isinstance(agent, str) or not agent:
                execution = receipt.get("execution")
                agent = execution.get("agentId") if isinstance(execution, Mapping) else None
            if not isinstance(agent, str) or not agent:
                continue
            recorded = receipt.get("selection")
            role = receipt.get("role") or (
                recorded.get("role") if isinstance(recorded, Mapping) else None
            )
            if role not in LEAF_ROLES:
                continue
            found[agent] = selection
    return found


def _plugin_role_seats(
    config: McpRuntimeConfig,
    active_leaves: set[tuple[str, str, str]],
    active_streams: Callable[[LeafSelection], bool] | None,
) -> dict[str, LeafSelection]:
    found: dict[str, LeafSelection] = {}
    activity = activity_directory(config)
    if activity is None or not activity.parent.is_dir():
        return found
    for seat_id, record in read_role_agents(activity.parent).items():
        if record.get("role") not in LEAF_ROLES:
            continue
        task_key = record.get("task")
        if not isinstance(task_key, str) or ":" not in task_key or "/" not in task_key:
            continue
        repo, _, rest = task_key.partition(":")
        master, _, leaf_part = rest.partition("/")
        if not (repo and master and leaf_part):
            continue
        leaf_id = _resolve_leaf_id(config.coordination_root, repo, master, leaf_part)
        selection = LeafSelection(repo, master, leaf_id)
        if _watched_leaf(config, selection, active_leaves, active_streams):
            found[seat_id] = selection
    return found


def _receipt_leaf_seats(
    config: McpRuntimeConfig,
    active_leaves: set[tuple[str, str, str]],
    active_streams: Callable[[LeafSelection], bool] | None,
    ports: WatcherApplicationPorts,
) -> dict[str, LeafSelection]:
    found = _execution_receipt_seats(config, active_leaves, active_streams, ports)
    found.update(_plugin_role_seats(config, active_leaves, active_streams))
    return found


def leaf_seats(
    config: McpRuntimeConfig,
    ports: WatcherApplicationPorts,
    *,
    active_streams: Callable[[LeafSelection], bool] | None = None,
) -> dict[str, LeafSelection]:
    """Every watched leaf's seats: its explainer generation id and its Worker/Reviewer/Curator."""

    found: dict[str, LeafSelection] = {}
    active_leaves: set[tuple[str, str, str]] = set()
    for folder in explainer_roots(config.coordination_root):
        repository_id, master = folder.parent.name, folder.name
        try:
            children = sorted(folder.iterdir())
        except OSError:
            continue
        for child in children:
            if not child.is_dir() or child.is_symlink():
                continue
            selection = LeafSelection(repository_id, master, child.name)
            active_leaves.add(selection.as_key())
            try:
                number = generation(folder / (child.name + GENERATION_SUFFIX))
            except (ExplainerStateError, OSError):
                number = 0
            if number > 0:
                found[agent_id(child, number)] = selection
    found.update(_receipt_leaf_seats(config, active_leaves, active_streams, ports))
    return found


def unexplained_turn_notice(
    config: McpRuntimeConfig,
    selection: LeafSelection,
    baselines: dict[tuple[str, str, str], ReviewTreeComparisonRecord],
    ports: WatcherApplicationPorts,
    *,
    measurement: ComparisonMeasurement | None = None,
) -> MovedNotice | None:
    """Measure code/memory movement for a live leaf that has no explainer environment yet."""

    measurement = measurement or ComparisonMeasurement()
    captured = (measurement.owner or GitComparisonOwner(config, ports)).capture(selection)
    if not isinstance(captured, CapturedComparison):
        return None
    key = selection.as_key()
    previous = baselines.get(key)
    if previous is None:
        try:
            root = task_root(config, selection.repository_id, selection.master)
            records = ports.comparison_records(root, selection.leaf_id)
            previous = records[-1] if records else None
        except (PathRefused, OSError):
            previous = None
    baselines[key] = captured.record
    if previous is not None and previous.same_trees(captured.record):
        return None
    blocks = 1
    if previous is not None:
        prev_binding = FrameBinding.from_record(
            previous,
            comparison_owner="agents_remember.application.review_tree_comparison",
            comparison_reference=f"review-comparison:{previous.task_id}/{previous.leaf_id}/{previous.number}",
        )
        counted = (measurement.counter or partial(blocks_changed, ports=ports))(
            prev_binding, captured.record
        )
        blocks = 1 if counted is None else max(counted, 1)
    return MovedNotice(
        kind="comparison-moved",
        from_number=None if previous is None else previous.number,
        to_number=_recorded_number(config, selection, captured.record, ports),
        blocks_changed=blocks,
        update_offered=False,
        has_explainer=False,
    )
