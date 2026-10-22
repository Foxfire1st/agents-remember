"""The pair a landed leaf delivered, and the comparison a landed review opens by default (MIK-R195).

A leaf's enclosure records what it delivered: the code commit its landing produced and the memory
content commit its closeout published. Those are **delivery records, not review records**. The last
numbered comparison a leaf holds may predate curation -- it compares the recorded base with itself
and honestly reports no knowledge change -- so reading ``records[-1]`` as *what the leaf delivered*
is the defect this module answers.

Three duties, and no fourth:

* **Establish the delivered pair** (:func:`delivered_pair`) from the enclosure itself: the recorded
  code base, the successful code landing record, the recorded memory base and the closeout's memory
  content commit. Every endpoint is resolved to its exact commit and tree in the repository its
  record names. Nothing here reads ``HEAD``, today's source-line tip or the last review record, and
  a side that cannot be established is a named refusal that identifies it -- never a substitution.
* **Open it as a comparison** (:func:`delivered_comparison`): the comparison is a four-tree record
  like any other. An existing record whose four trees are exactly this pair is reused unchanged; a
  pair no record holds is recorded by the existing numbering owner
  (:func:`~agents_remember.application.review_tree_comparison._record`), so its number, identity and
  create-only safety stay that owner's and a repeated or concurrent open cannot number it twice.
* **Say whether the shown comparison is that pair** (:func:`delivered_notice`): the one concise
  line the page shows when the reader is looking at another pair. It carries identities, not a
  verdict: a matching pair is never marked as moved merely because it was recorded earlier, and a
  pair that cannot be established is ``unavailable`` rather than silently equal.

A leaf that records no delivery at all -- no closeout, no landing, no delivered commit -- has no
delivered pair, and the caller keeps its existing recorded default (``None`` here says exactly
that). A leaf that *claims* a delivery whose endpoint this module cannot establish answers a
refusal naming the cell or commit that failed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, cast

from agents_remember.application.review_candidate_resolution import (
    ReviewCandidateResolution,
    refusal,
)
from agents_remember.application.review_tree_comparison import (
    ReviewTrees,
    TreeSideUnreadable,
    _has_marker,  # the layout-marker owner: converted knowledge is its marker, nothing else
    _record,  # the comparison-record owner: numbering, identity and create-only safety are its own
    comparison_directory,
    comparison_records,
    converted_base_side,
    reopened_trees,
    review_task_id,
    tree_resolution,
)
from agents_remember.kernel.git_command import (
    GIT_METADATA_TIMEOUT_SECONDS,
    GitRunnerOptions,
    run_git,
)
from agents_remember.models.knowledge.review import ReviewRefusal
from agents_remember.models.knowledge.review_trees import (
    DeliveredComparisonState,
    ReviewConvertedBase,
    ReviewDeliveredComparison,
    ReviewTreeComparisonRecord,
    ReviewTreeSide,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract

__all__ = [
    "DeliveredComparison",
    "DeliveredPair",
    "delivered_comparison",
    "delivered_default",
    "delivered_notice",
    "delivered_pair",
    "recorded_latest",
]

# The one action a reader can take when a delivery endpoint cannot be established. It is stated once
# because the refusal is the whole answer: nothing on this surface may invent an endpoint.
_RESTORE_ACTION = (
    "restore the recorded landing commit or closeout memory commit in its repository, then reopen "
    "the review; no other commit, tree or comparison is substituted for the delivery"
)

_LANDED_STATUSES: Final = frozenset({"completed", "checkpointed"})


@dataclass(frozen=True)
class DeliveredPair:
    """The four exact sides a leaf delivered, and the record each candidate came from."""

    code_base: ReviewTreeSide
    code_candidate: ReviewTreeSide
    memory_base: ReviewTreeSide
    memory_candidate: ReviewTreeSide
    code_landing: str
    memory_delivery: str
    # K_B's conversion when the recorded base predates conversion and K_C is converted (MIK-R24
    # rule 7): the same reading a live comparison of this pair made, so the before side is the same
    # knowledge either way.
    converted_base: ReviewConvertedBase | None = None

    def sides(self) -> tuple[ReviewTreeSide, ...]:
        return (self.code_base, self.code_candidate, self.memory_base, self.memory_candidate)

    def draft(self, contract: WorktreeContract) -> ReviewTreeComparisonRecord:
        """The record this pair is, at the placeholder number the recording owner replaces."""

        return ReviewTreeComparisonRecord(
            task_id=review_task_id(contract.task_root),
            leaf_id=contract.leaf_id or contract.task_name,
            number=1,
            code_base=self.code_base,
            code_candidate=self.code_candidate,
            memory_base=self.memory_base,
            memory_candidate=self.memory_candidate,
            converted_base=self.converted_base,
            recorded_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )


@dataclass(frozen=True)
class DeliveredComparison:
    """The delivered pair, the record that holds it (when one does), and that comparison opened."""

    pair: DeliveredPair
    record: ReviewTreeComparisonRecord | None
    trees: ReviewTrees | None

    @property
    def number(self) -> int | None:
        return None if self.record is None else self.record.number


# -- the delivered pair --------------------------------------------------------------------------


def delivered_pair(contract: WorktreeContract) -> DeliveredPair | ReviewRefusal | None:
    """The four sides the enclosure records for this delivery, or ``None`` for a leaf with none.

    ``None`` is not an unavailable delivery: it is a leaf that records no delivery at all, whose
    review keeps the default it had. Every other failure is a refusal naming the side that failed.
    """

    if contract.kind != "leaf" or not _records_a_delivery(contract):
        return None
    code_repository, memory_repository = contract.code_repo_path, contract.memory_repo_path
    code_base = _resolved_side(
        code_repository, contract.code_base_commit, "code base", "code.base_commit"
    )
    landed, landing_source = _landed_code(contract)
    code_candidate = _resolved_side(code_repository, landed, "code candidate", landing_source)
    memory_base = _resolved_side(
        memory_repository, contract.memory_base_commit, "memory base", "memory.base_commit"
    )
    delivered, delivery_source = _delivered_memory(contract)
    memory_candidate = _resolved_side(
        memory_repository, delivered, "memory candidate", delivery_source
    )
    for side in (code_base, code_candidate, memory_base, memory_candidate):
        if isinstance(side, ReviewRefusal):
            return side
    # The loop above refuses any side it could not establish; the type checker does not narrow a
    # tuple of unions through it, so the four established sides are named once here.
    code_base, code_candidate, memory_base, memory_candidate = cast(
        tuple[ReviewTreeSide, ReviewTreeSide, ReviewTreeSide, ReviewTreeSide],
        (code_base, code_candidate, memory_base, memory_candidate),
    )
    assert code_repository is not None and memory_repository is not None  # both sides resolved
    # A delivered memory side with no layout marker holds no converted knowledge, so there are no
    # trees to compare and no base to convert: the pair is established for its source identities
    # only, and the default read answers it as the legacy/unavailable review it is (F03).
    converted: ReviewConvertedBase | None = None
    if _has_marker(memory_repository, memory_candidate.tree):
        reading = _conversion_of(
            contract,
            memory_repository,
            memory_base,
            memory_candidate,
            (code_repository, code_base.commit or ""),
        )
        if isinstance(reading, ReviewRefusal):
            return reading
        converted = reading
    return DeliveredPair(
        code_base=code_base,
        code_candidate=code_candidate,
        memory_base=memory_base,
        memory_candidate=memory_candidate,
        code_landing=landing_source,
        memory_delivery=delivery_source,
        converted_base=converted,
    )


def _conversion_of(
    contract: WorktreeContract,
    memory_repository: Path,
    memory_base: ReviewTreeSide,
    memory_candidate: ReviewTreeSide,
    code: tuple[Path, str],
) -> ReviewConvertedBase | ReviewRefusal | None:
    """K_B's converted base when the recorded base predates conversion, else ``None`` or the refusal.

    A base the memory repository already converted is compared as it is, which is the ordinary case.
    A base that predates the conversion is read through the shipped owner, exactly as a live
    comparison of this pair reads it (MIK-R24 rule 7), so a landed leaf's before side is the same
    knowledge its live review had.

    A conversion this owner cannot write is *refused by name*, never left to the side-state machinery
    (F03): the recorded base tree still exists as raw unconverted files, so opening it in place would
    index no knowledge and publish an available, complete, zero-change before side -- a delivered
    comparison that compares nothing with nothing.
    """

    if _has_marker(memory_repository, memory_base.tree):
        return None
    try:
        converted = converted_base_side(
            contract.coordination_root,
            memory_repository,
            memory_base.commit or "",
            trees=(memory_base.tree, memory_candidate.tree),
            code=code,
        )
    except (TreeSideUnreadable, OSError, RuntimeError) as error:
        return _unconverted_base(str(error))
    if converted is None:
        return _unconverted_base("the conversion owner produced no converted base for it")
    return converted


def _unconverted_base(reason: str) -> ReviewRefusal:
    return refusal(
        "candidate_dataset_absent",
        "the delivered memory base is unconverted and cannot be converted into the knowledge its "
        f"candidate side compares ({reason}), so the delivered comparison is not established; its "
        "raw unconverted tree is never indexed as an empty before side",
        next_action=_RESTORE_ACTION,
        offending_input="memory base",
    )


def _memory_is_converted(side: ReviewTreeSide) -> bool:
    """Whether one delivered memory side holds converted knowledge (its layout marker is a blob)."""

    return _has_marker(Path(side.repository), side.tree)


def _records_a_delivery(contract: WorktreeContract) -> bool:
    """Whether this enclosure claims a delivery: a completed closeout or a completed landing.

    A commit cell on its own is not a claim. A leaf closed out before this feature recorded a code
    commit and no closeout at all, and a lifecycle cell that has not reached its terminal value
    records work in progress; neither is a delivery, and neither overrides the review's default.
    """

    return (
        contract.closeout_status in _LANDED_STATUSES
        or contract.integration_status in _LANDED_STATUSES
    )


def _landed_code(contract: WorktreeContract) -> tuple[str, str]:
    """The successful code landing's commit and the record it came from.

    A replay may produce a commit other than the closeout's, so the landing record is read first and
    is never silently replaced by ``closeout.code_commit``. The closeout commit answers only for a
    leaf that was closed out and not landed, which is the delivery it published.
    """

    if contract.integration_status in _LANDED_STATUSES:
        return (
            contract.integrated_code_commit,
            "the integration landing record (integrated_code_commit)",
        )
    return contract.code_commit, "the closeout record (code_commit)"


def _delivered_memory(contract: WorktreeContract) -> tuple[str, str]:
    """The delivered memory content commit and the record it came from -- the closeout, and only it.

    MIK-R195 rule 1 names the closeout's ``memory_content_commit`` as the memory content the leaf
    delivered. The landing record's own memory cell is not a second answer to that question: a
    landing may carry the memory a replay merged, and substituting it here would publish a delivered
    pair the closeout never named (F01). A closeout that records no memory cell therefore refuses by
    naming that cell and stays refused until the record is repaired.
    """

    return contract.memory_content_commit, "the closeout record (memory_content_commit)"


#: A recorded commit is a full commit id: a branch, tag, ``HEAD``, tree or blob id is never one.
_COMMIT_ID = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


def _resolved_side(
    repository: Path | None, commit: str, side: str, source: str
) -> ReviewTreeSide | ReviewRefusal:
    """One delivered side as its exact commit and tree, or the refusal that names what is missing.

    The cell must name a full commit id *and* that id must be a commit in the repository the record
    names: a tree or blob id peeled happily to a tree, so accepting one published a "delivered
    commit" that was never a commit at all (F02). Neither check substitutes anything -- a side Git
    cannot establish is refused by name.
    """

    if repository is None:
        return _missing(side, f"the enclosure records no repository for {source}")
    if not commit:
        return _missing(side, f"{source} records no commit")
    if _COMMIT_ID.fullmatch(commit) is None:
        return _missing(
            side,
            f"{source} records {commit}, which is not a full commit id; a branch, tag, HEAD or "
            "abbreviated id is never read as the commit the record names",
        )
    kind = _object_kind(repository, commit)
    if kind != "commit":
        return _missing(
            side,
            f"{source} names {commit}, which {repository} holds as "
            f"{'nothing it can read' if not kind else 'a ' + kind} rather than a commit",
        )
    tree = _tree_of(repository, commit)
    if tree is None:
        return _missing(
            side, f"{source} names {commit}, which {repository} cannot resolve to a commit's tree"
        )
    return ReviewTreeSide(repository=str(repository), tree=tree, commit=commit)


def _object_kind(repository: Path, revision: str) -> str:
    """What ``repository`` holds under ``revision`` (``commit``, ``tree``, ``blob`` or ``""``)."""

    result = run_git(
        repository,
        ["--no-replace-objects", "cat-file", "-t", revision],
        GitRunnerOptions(timeout=GIT_METADATA_TIMEOUT_SECONDS),
    )
    value = result.stdout.strip()
    return value if result.returncode == 0 else ""


def _tree_of(repository: Path, revision: str) -> str | None:
    result = run_git(
        repository,
        ["--no-replace-objects", "rev-parse", "--verify", "--quiet", f"{revision}^{{tree}}"],
        GitRunnerOptions(timeout=GIT_METADATA_TIMEOUT_SECONDS),
    )
    value = result.stdout.strip()
    return value if result.returncode == 0 and value else None


def _missing(side: str, detail: str) -> ReviewRefusal:
    return refusal(
        "candidate_unresolved",
        f"the delivered {side} side cannot be established: {detail}",
        next_action=_RESTORE_ACTION,
        offending_input=side,
    )


# -- the delivered comparison --------------------------------------------------------------------


def delivered_comparison(
    contract: WorktreeContract, *, record: bool
) -> DeliveredComparison | ReviewRefusal | None:
    """The delivered pair as a comparison: an existing exact record, or a new one (``record``).

    ``record=False`` asks only what the leaf already holds: an exact record's number, or none. That
    is the notice's question, and it writes nothing on a reader's behalf.
    """

    pair = delivered_pair(contract)
    if pair is None or isinstance(pair, ReviewRefusal):
        return pair
    return _comparison_of(contract, pair, record=record)


def _comparison_of(
    contract: WorktreeContract, pair: DeliveredPair, *, record: bool
) -> DeliveredComparison | ReviewRefusal:
    """That established pair as the comparison the surface opens, recording it when asked."""

    draft = pair.draft(contract)
    existing = _exact_record(contract, draft)
    if existing is not None:
        return DeliveredComparison(pair, existing, _opened(contract, existing))
    if not record:
        return DeliveredComparison(pair, None, None)
    made = _record(contract, draft)
    if isinstance(made, ReviewRefusal):
        return made
    recorded, path = made
    return DeliveredComparison(pair, recorded, _opened(contract, recorded, path.parent))


def _exact_record(
    contract: WorktreeContract, draft: ReviewTreeComparisonRecord
) -> ReviewTreeComparisonRecord | None:
    """The leaf's record whose four trees are exactly this pair, whatever its number, or ``None``.

    The oldest such record is reused unchanged, so a delivered pair recorded before the last review
    record keeps that record and its number.
    """

    for candidate in comparison_records(contract.task_root, draft.leaf_id):
        if candidate.same_trees(draft):
            return candidate
    return None


def _opened(
    contract: WorktreeContract, record: ReviewTreeComparisonRecord, directory: Path | None = None
) -> ReviewTrees:
    """That comparison opened from its tree ids, with each side's state named as Git answers now."""

    where = directory or comparison_directory(contract.task_root, record.leaf_id)
    return reopened_trees(contract.coordination_root, record, where)


def delivered_default(
    repository_id: str, contract: WorktreeContract
) -> ReviewCandidateResolution | ReviewRefusal | None:
    """The resolution a landed leaf's review opens by default: its delivered comparison.

    ``None`` says this leaf records no delivery and keeps the recorded default it had.

    A delivered memory content commit that holds no converted knowledge is not compared as trees at
    all (F03): the leaf's review is then source-only, with both knowledge sides named
    ``legacy-unavailable`` -- the same answer its live review gave before the closeout, and the one
    the legacy route gives -- rather than an available before/after pair with no knowledge in it.
    """

    pair = delivered_pair(contract)
    if pair is None or isinstance(pair, ReviewRefusal):
        return pair
    if not _memory_is_converted(pair.memory_candidate):
        return _unconverted_delivery(repository_id, contract, pair)
    comparison = _comparison_of(contract, pair, record=True)
    if isinstance(comparison, ReviewRefusal):
        return comparison
    assert comparison.trees is not None  # record=True opens what it holds or records
    return tree_resolution(repository_id, contract, comparison.trees)


def _unconverted_delivery(
    repository_id: str, contract: WorktreeContract, pair: DeliveredPair
) -> ReviewCandidateResolution:
    """A landed leaf whose delivered memory is unconverted: source identities, no knowledge sides.

    The construction is the live resolution's own for unconverted memory (MIK-R25 rule 2): both
    knowledge sides are ``legacy-unavailable`` with the canonical datasets retired, and no comparison
    record is created, because there is no knowledge comparison to record.
    """

    absent = contract.task_root / ".review-knowledge-unavailable"
    return ReviewCandidateResolution(
        repository_id=repository_id,
        leaf_id=contract.leaf_id,
        baseline_database=absent / "before",
        candidate_database=absent / "after",
        baseline_code_root=contract.code_repo_path,
        candidate_code_root=contract.code_repo_path,
        baseline_code_tree_id=pair.code_base.commit,
        candidate_code_tree_id=pair.code_candidate.tree,
        contract=contract,
        knowledge_unavailable=tuple(
            (
                side,
                "legacy-unavailable",
                "the delivered memory content is unconverted; canonical datasets are retired, so "
                "only the delivered source identities are reviewed",
            )
            for side in ("before", "after")
        ),
    )


# -- the notice ----------------------------------------------------------------------------------


def delivered_notice(
    contract: WorktreeContract, shown: ReviewTreeComparisonRecord | None
) -> ReviewDeliveredComparison | None:
    """Whether ``shown`` is the delivered pair, as the one line the page states; ``None`` if no pair.

    Nothing is recorded here: a reader looking at another pair is told the identity of the delivered
    one, and the delivered view itself is the page's own default (MIK-R195 rule 2).
    """

    comparison = delivered_comparison(contract, record=False)
    if comparison is None:
        return None
    if isinstance(comparison, ReviewRefusal):
        return ReviewDeliveredComparison(
            state="unavailable",
            statement=(
                "The delivery this leaf's enclosure records cannot be established, so this "
                "comparison cannot be compared with it."
            ),
            shown=None if shown is None else shown.number,
            detail=comparison.detail,
        )
    if not _memory_is_converted(comparison.pair.memory_candidate):
        # The delivered memory holds no converted knowledge, so there is no delivered comparison to
        # be looking away from: this leaf's review is source-only, not a pair that differs.
        return None
    pair = comparison.pair
    state = _state_of(comparison, shown)
    return ReviewDeliveredComparison(
        state=state,
        statement=_statement(state, comparison, shown),
        comparison=comparison.number,
        shown=None if shown is None else shown.number,
        code_base_tree=pair.code_base.tree,
        code_candidate_tree=pair.code_candidate.tree,
        code_candidate_commit=pair.code_candidate.commit,
        memory_base_tree=pair.memory_base.tree,
        memory_candidate_tree=pair.memory_candidate.tree,
        memory_candidate_commit=pair.memory_candidate.commit,
    )


def _state_of(
    comparison: DeliveredComparison, shown: ReviewTreeComparisonRecord | None
) -> DeliveredComparisonState:
    """``delivered`` only when the shown comparison's four trees are exactly the delivered pair."""

    if shown is not None and comparison.record is not None and shown.same_trees(comparison.record):
        return "delivered"
    return "differs"


def _statement(
    state: DeliveredComparisonState,
    comparison: DeliveredComparison,
    shown: ReviewTreeComparisonRecord | None,
) -> str:
    """The one concise line: identities, never a verdict about a historical count or statement."""

    if state == "delivered":
        return "This comparison is the pair the leaf delivered."
    code = comparison.pair.code_candidate.tree[:8]
    memory = comparison.pair.memory_candidate.tree[:8]
    number = "" if shown is None else f"Comparison #{shown.number} is not that pair: "
    return (
        f"{number}the leaf delivered code {code} and memory {memory}. "
        "Open the delivered comparison to read it."
    )


# -- the numbers a menu lists --------------------------------------------------------------------


def recorded_latest(contract: WorktreeContract) -> int | None:
    """The highest comparison number this leaf recorded, or ``None`` when it recorded none."""

    records = comparison_records(contract.task_root, contract.leaf_id or contract.task_name)
    return None if not records else records[-1].number
