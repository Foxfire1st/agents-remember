"""Share the native comparison admission between reviewer projection and explainer builds.

Typed comparison JSON uses the existing RecordedAnswerReuse owner. Native capture witnesses
stay in process; after restart native admission runs again before any disk value is restored.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, replace
from threading import RLock

from agents_remember.application.review_answer_preparation import (
    ComparisonRetention,
    LiveWitness,
    RecordedReviewAnswers,
)
from agents_remember.application.review_candidate_resolution import recorded_leaf_contract
from agents_remember.application.review_delivered_comparison import (
    recorded_latest,
)
from agents_remember.application.review_tree_comparison import recheck_memory_candidate
from agents_remember.application.review_tree_knowledge import (
    CardsCarry,
    read_prepared_review_trees,
    read_review_trees,
    resolve_tree_comparison,
)
from agents_remember.errors import FutureCodeCandidateError
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.kernel.read_cancellation import SharedFlights
from agents_remember.kernel.recorded_reads import (
    changed_observations,
    observed_path_exists,
    recorded_reads,
    replay_reads,
)
from agents_remember.kernel.reviewer_worklist_process import ReviewerWorklistProcesses
from agents_remember.models.knowledge.review import ReviewRefusal
from agents_remember.models.knowledge.review_trees import ReviewTrees, ReviewTreesResult
from agents_remember.serving.changeset_cache import ReadInput, RepositoryRead
from agents_remember.serving.review_trees import ReviewTreesQuery
from agents_remember.worktrees.modules.capture_baseline import (
    WorktreeCaptureWitness,
    capture_witness,
    memo_capture_baselines,
)
from agents_remember.worktrees.modules.future_code_candidate import capture_future_code_candidate
from agents_remember.worktrees.modules.git import head_commit
from agents_remember.worktrees.worktree_contract import WorktreeContract


def _query_key(query: ReviewTreesQuery) -> tuple[object, ...]:
    return (query.repository_id, query.master, query.leaf_id, query.number, query.recorded)


@dataclass(frozen=True)
class ComparisonInputWitness:
    """Trusted native roots/read set plus captured candidate proofs, never loaded from JSON."""

    native: tuple[WorktreeContract, ReviewTrees]
    reads: tuple[tuple[str, str], ...]
    sources: tuple[ReadInput, ...]
    stamps: tuple[tuple[ReadInput, str | None], ...]
    observe: Callable[[tuple[ReadInput, ...]], tuple[tuple[ReadInput, str | None], ...]]
    code: WorktreeCaptureWitness | None
    memory: WorktreeCaptureWitness | None
    code_head: str | None
    readable: Callable[[], bool]

    def current(self) -> bool:
        if (
            changed_observations(dict(self.reads))
            or self.observe(self.sources) != self.stamps
            or not self.readable()
        ):
            return False
        contract, trees = self.native
        if not trees.live:
            return True  # Read set and object/index source stamps govern a recorded comparison.
        if self.code is None or not self.code.current():
            # Exactly the existing capture owner's strict re-derivation for staged/flagged
            # indexes or changed files, not a weaker status-only fallback.
            try:
                fresh = capture_future_code_candidate(contract, kept=True)
            except (FutureCodeCandidateError, OSError, RuntimeError, ValueError):
                return False
            if (
                fresh.codeCandidateTree != trees.record.code_candidate.tree
                or fresh.codeBaseCommit != contract.code_base_commit
                or fresh.observedCodeHead != self.code_head
            ):
                return False
        if (self.memory is None or not self.memory.current()) and recheck_memory_candidate(
            trees
        ) is not None:
            return False
        return (
            not changed_observations(dict(self.reads)) and self.observe(self.sources) == self.stamps
        )


@dataclass(frozen=True)
class PreparedComparison:
    result: ReviewTreesResult
    native: tuple[WorktreeContract, ReviewTrees]
    witness: ComparisonInputWitness


class SharedComparisonPreparation:
    """One native preparation per exact current selection; disk storage has one existing owner."""

    def __init__(
        self, config: McpRuntimeConfig, answers: RecordedReviewAnswers, *, capacity: int = 64
    ):
        self.config = config
        self.answers = answers
        self.capacity = capacity
        self._kept: OrderedDict[tuple[object, ...], PreparedComparison] = OrderedDict()
        # One preparation per key in flight; it is the flight's, so it stops only when every
        # asker's client is gone and never hands one asker's cancellation to another (D569).
        self._flights: SharedFlights[tuple[object, ...], PreparedComparison | ReviewTreesResult] = (
            SharedFlights()
        )
        self._lock = RLock()

    def prepare(self, query: ReviewTreesQuery) -> PreparedComparison | ReviewTreesResult:
        if query.focused:
            raise ValueError("comparison preparation names a complete comparison, not a focus")
        key = _query_key(query)
        with self._lock:
            kept = self._kept.get(key)
        if kept is not None and kept.witness.current():
            with self._lock:
                if key in self._kept:
                    self._kept.move_to_end(key)
            replay_reads(dict(kept.witness.reads))
            return replace(kept, result=kept.result.model_copy(deep=True))
        value, owner = self._flights.share(
            key, lambda: self._admit(query, key, self._prepare(query))
        )
        if owner:
            return value
        if isinstance(value, PreparedComparison):
            if not value.witness.current():
                return _moved(query)
            replay_reads(dict(value.witness.reads))
            return replace(value, result=value.result.model_copy(deep=True))
        return value.model_copy(deep=True)

    def _admit(
        self,
        query: ReviewTreesQuery,
        key: tuple[object, ...],
        value: PreparedComparison | ReviewTreesResult,
    ) -> PreparedComparison | ReviewTreesResult:
        if not isinstance(value, PreparedComparison):
            return value
        if not value.witness.current():
            return _moved(query)
        with self._lock:
            self._kept[key] = value
            self._kept.move_to_end(key)
            while len(self._kept) > self.capacity:
                self._kept.popitem(last=False)
        return value

    def render(
        self,
        query: ReviewTreesQuery,
        *,
        processes: ReviewerWorklistProcesses | None = None,
        carry: CardsCarry | None = None,
    ) -> ReviewTreesResult:
        """The unchanged page projection, using the comparison it will hand the explainer.

        ``carry`` may answer a card read from the cards made for an earlier comparison (D574).
        """
        if query.focused:
            return read_review_trees(self.config, query, processes=processes, carry=carry)
        prepared = self.prepare(query)
        if isinstance(prepared, ReviewTreesResult):
            return prepared
        return read_prepared_review_trees(query, prepared.native, processes=processes)

    def _prepare(self, query: ReviewTreesQuery) -> PreparedComparison | ReviewTreesResult:
        with recorded_reads() as initial_reads:
            selected = recorded_leaf_contract(
                self.config, query.repository_id, query.master, query.leaf_id
            )
            roots = (
                tuple(
                    root
                    for root in (selected.code_worktree, selected.memory_worktree)
                    if root is not None and observed_path_exists(root)
                )
                if selected is not None and not query.recorded
                else ()
            )
        # Historical comparisons use the durable repository objects. An absent capture
        # worktree is an observed selection predicate, never a failed repository read.
        extra: tuple[ReadInput, ...] = tuple(
            RepositoryRead(
                root,
                references=("HEAD", selected.code_source_branch, selected.memory_source_branch),
            )
            for root in roots
            if selected is not None
        )
        initial_stamps = self.answers.comparison_stamps(extra)
        with memo_capture_baselines(), recorded_reads() as reads:
            replay_reads(initial_reads)
            native = resolve_tree_comparison(self.config, query)
            if not isinstance(native, tuple):
                return read_prepared_review_trees(query, native)
            contract, trees = native
            sources, binding = self.answers.comparison_sources(query, contract, trees, reads)
            sources = tuple(dict.fromkeys((*sources, *extra)))
            code = (
                None
                if contract.code_worktree is None
                else capture_witness(contract.code_worktree, tree=trees.record.code_candidate.tree)
            )
            memory = (
                None
                if contract.memory_worktree is None
                else capture_witness(
                    contract.memory_worktree, tree=trees.record.memory_candidate.tree
                )
            )
            latest = recorded_latest(contract)
            if query.number is None and latest != trees.record.number:
                return _moved(query)
            code_head = (
                None
                if not trees.live or contract.code_worktree is None
                else head_commit(contract.code_worktree)
            )
        replay_reads(reads)
        stamps = self.answers.comparison_stamps(sources)
        if initial_stamps != self.answers.comparison_stamps(extra) or any(
            stamp is None for _, stamp in stamps
        ):
            return _moved(query)
        witness = ComparisonInputWitness(
            native,
            tuple(sorted(reads.items())),
            sources,
            stamps,
            self.answers.comparison_stamps,
            code,
            memory,
            code_head,
            lambda: self.answers.comparison_readable(native),
        )
        if not witness.current():
            return _moved(query)
        record = trees.record
        result = ReviewTreesResult(
            state="trees",
            repository_id=query.repository_id,
            master=query.master,
            leaf_id=query.leaf_id,
            comparison=record,
            latest=latest,
            knowledge_sides=trees.sides(),
            code_sides=trees.code_sides,
        )
        result = self.answers.comparison(
            query,
            native,
            result,
            ComparisonRetention(reads, sources, binding),
            witness.current,
        )
        return PreparedComparison(result, native, witness)


def live_answer_witness(
    comparisons: SharedComparisonPreparation, repository_id: str, master: str, leaf_id: str
) -> LiveWitness | None:
    """The live comparison a live leaf's task-context answers are kept on, proven now (D570).

    The leaf's own live comparison, prepared (or found kept and proven current) by this owner;
    ``None`` for a recorded leaf and for any refusal, which keeps nothing.
    """
    prepared = comparisons.prepare(
        ReviewTreesQuery(repository_id=repository_id, master=master, leaf_id=leaf_id)
    )
    if not isinstance(prepared, PreparedComparison):
        return None
    contract, trees = prepared.native
    if not trees.live:
        return None
    witness = prepared.witness
    return LiveWitness(contract, trees, witness.sources, witness.current)


def _moved(query: ReviewTreesQuery) -> ReviewTreesResult:
    return ReviewTreesResult(
        state="refused",
        repository_id=query.repository_id,
        master=query.master,
        leaf_id=query.leaf_id,
        refusal=ReviewRefusal(
            code="inputs_changing",
            detail="comparison inputs moved during preparation",
            next_action="retry after the named comparison inputs stop changing",
            offending_input="comparison preparation",
        ),
    )
