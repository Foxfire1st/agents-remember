"""A live leaf's whole reviews and catalogue are kept on the witness of the comparison they name.

The review and the subject catalogue of a live leaf read its working views. They are kept while the
working views still capture to the comparison the answer names and every input it read compares
identical: the witness's own inputs, the refs the answers resolve, the persisted worklist, the
index cache's placement, and each task document the computation recorded. Any one of them moving
computes the answer again. Real Git repositories and the real producers throughout.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable, Iterator
from functools import partial
from pathlib import Path

import pytest
from agents_remember.application.knowledge_review import (
    list_comparison_review_entries,
    read_complete_knowledge_review,
)
from agents_remember.application.knowledge_worklist.leaf import CandidateTrees, leaf_worklist
from agents_remember.application.review_answer_preparation import RecordedReviewAnswers
from agents_remember.application.review_candidate_resolution import (
    ReviewCandidateResolution,
    resolve_review_candidate,
)
from agents_remember.application.review_comparison_preparation import (
    SharedComparisonPreparation,
    live_answer_witness,
)
from agents_remember.application.review_intent_summary import read_review_intent_summary
from agents_remember.application.review_tree_comparison import (
    comparison_directory,
    reopen_review_trees,
)
from agents_remember.application.worktree_services import build_default_worktree_services
from agents_remember.kernel.memory_ledger import create_initial_ledger, write_ledger
from agents_remember.kernel.recorded_reads import record_read, recorded_reads
from agents_remember.memory.knowledge_index import text_uuid
from agents_remember.models.knowledge.read import InvariantIdentitySeed
from agents_remember.models.knowledge.review import ReviewEntryListResult
from agents_remember.models.knowledge.review_trees import ReviewTreeComparisonRecord, ReviewTrees
from agents_remember.serving.review_answer_reuse import RecordedAnswerReuse
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.sync import sync_result
from agents_remember.worktrees.services import bind_worktree_services, reset_worktree_services
from agents_remember.worktrees.worktree_contract import load_contract
from agents_remember_test_support.testing.review_git_trees import (
    CODE_FILE,
    CODE_V1,
    INVARIANT,
    LEAF,
    MASTER,
    REPO,
    World,
    build_world,
    commit,
    git,
)


class _Live:
    def __init__(self, world: World) -> None:
        self.world = world
        self.produced: Counter[str] = Counter()
        self.owner = RecordedReviewAnswers(world.config, RecordedAnswerReuse())
        comparisons = SharedComparisonPreparation(world.config, self.owner)
        self.owner.bind_live_witness(partial(live_answer_witness, comparisons))

    def intent(self, **fields: object) -> object:
        request = self.world.review(**fields)

        def produce() -> object:
            self.produced["intent"] += 1
            return read_complete_knowledge_review(self.world.config, request)

        return self.owner.intent(request, produce)

    def entries(
        self, alter: Callable[[ReviewEntryListResult], ReviewEntryListResult] = lambda v: v
    ) -> ReviewEntryListResult:
        def produce() -> ReviewEntryListResult:
            self.produced["entries"] += 1
            listed = list_comparison_review_entries(self.world.config, REPO, MASTER, LEAF, None)
            assert isinstance(listed, ReviewEntryListResult)
            return alter(listed)

        return self.owner.entries(REPO, MASTER, LEAF, None, produce)


@pytest.fixture
def live(tmp_path):
    reset_worktree_services()
    world = build_world(tmp_path)
    world.edit()
    yield _Live(world)
    reset_worktree_services()


def test_a_live_task_context_answer_is_computed_once_while_its_inputs_are_identical(live):
    first, listed = live.intent(), live.entries()
    assert getattr(first, "state", None) == "review" and listed.state == "entries"
    assert live.intent() == first and live.entries() == listed
    assert live.produced == Counter(intent=1, entries=1)


@pytest.fixture
def sync_world(tmp_path: Path) -> Iterator[World]:
    world = build_world(tmp_path)
    write_ledger(
        world.memory / "memory.md", create_initial_ledger(REPO, world.code_base, world.memory_base)
    )
    world.memory_base = commit(world.memory, {})
    git(world.memory_worktree, "merge", "--ff-only", "main")
    commit(world.code_worktree, {"no-rule.py": "answer = 42\n"})
    world.contract()
    task_path = world.task_root / "task.json"
    task = json.loads(task_path.read_text())
    task.update(
        schema="ar-task-document/v1",
        slug="task",
        title="Review and sync fixture",
        kind="master",
        repo=REPO,
        status="inProgress",
        createdAt="2026-11-23T00:00:00Z",
    )
    task_path.write_text(json.dumps(task))
    bind_worktree_services(build_default_worktree_services())
    yield world
    reset_worktree_services()


def _live_record(world: World) -> ReviewTreeComparisonRecord:
    resolved = resolve_review_candidate(world.config, REPO, MASTER, LEAF)
    assert isinstance(resolved, ReviewCandidateResolution) and resolved.trees is not None
    return resolved.trees.record


def _same_tree_sync(world: World, contract_path: Path) -> str:
    new_base = commit(world.memory, {})
    assert new_base != world.memory_base
    for repository in (world.code, world.memory):
        git(repository, "update-ref", "refs/remotes/origin/main", "refs/heads/main")
        git(repository, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    result = sync_result(
        WorktreeArgs(contract_path=contract_path, memory_sync_choice="merge-memory")
    )
    assert result.payload["state"] == "synced", result.payload
    return new_base


def test_same_tree_sync_updates_the_live_worklist_binding_and_keeps_the_numbered_record(
    sync_world: World,
) -> None:
    world = sync_world
    contract_path = world.contract()
    first = _live_record(world)
    pair = CandidateTrees(first.code_candidate.tree, first.memory_candidate.tree)
    assert leaf_worklist(load_contract(contract_path), candidate=pair)
    worklist_path = contract_path.parent / "knowledge-worklist.json"
    prior_worklist = worklist_path.read_bytes()
    before = read_review_intent_summary(world.config, REPO, MASTER, LEAF)
    assert before.unexplained_open == 1
    first_path = comparison_directory(world.task_root, LEAF) / f"{first.number}.json"
    prior_record = first_path.read_bytes()

    new_base = _same_tree_sync(world, contract_path)
    synced = load_contract(contract_path)
    assert synced.memory_base_commit == new_base
    current_worklist = worklist_path.read_bytes()
    pairing = json.loads(current_worklist)["pairing"]
    assert pairing["memoryBase"]["commit"] == new_base
    assert pairing["memoryCandidate"]["tree"] == pair.memory
    assert pairing["candidate"]["tree"] == pair.code

    after = read_review_intent_summary(world.config, REPO, MASTER, LEAF)
    assert after.unexplained_open == 1
    current = _live_record(world)
    assert current.memory_base.commit == new_base and current.same_trees(first)
    assert current.number == first.number + 1

    worklist_path.write_bytes(prior_worklist)
    stale = read_review_intent_summary(world.config, REPO, MASTER, LEAF)
    assert stale.unexplained_open is None
    worklist_path.write_bytes(current_worklist)
    (world.code_worktree / "no-rule.py").write_text("answer = 43\n")
    different_tree = read_review_intent_summary(world.config, REPO, MASTER, LEAF)
    assert different_tree.unexplained_open is None

    reopened = reopen_review_trees(world.config.coordination_root, synced, first.number)
    assert isinstance(reopened, ReviewTrees) and reopened.record == first
    assert first_path.read_bytes() == prior_record


def _edit_worktree(world: World) -> None:
    (world.code_worktree / CODE_FILE).write_text(CODE_V1.replace("value", "given"))


def _move_source_branch(world: World) -> None:
    commit(world.code, {"pkg/b.py": "LANDED = True\n"})  # the leaf's code source branch, main


def _move_memory_work_branch(world: World) -> None:
    git(world.memory_worktree, "commit", "-q", "--allow-empty", "-m", "memory work")


def _write_worklist(world: World) -> None:
    contract = world.task_root / "enclosures" / LEAF.lower() / "knowledge-worklist.json"
    contract.write_text(json.dumps({"state": "incomplete"}))


def _rewrite_comparison_record(world: World) -> None:
    (record,) = comparison_directory(world.task_root, LEAF).glob("*.json")
    record.write_text(record.read_text() + "\n")  # the same comparison, other bytes


def _switch_the_code_worktree_to_another_branch(world: World) -> None:
    git(world.code_worktree, "checkout", "-q", "-b", "another")  # the same commit and trees


def test_the_review_names_the_code_worktrees_head_among_its_reads(live):
    """The branch check's two file reads are the answer's inputs (D570).

    The comparison witness also follows the worktree's ``HEAD``, so a branch switch is caught
    there too; this pins that the review's own read of it is named, whatever else guards it.
    """

    dotgit = live.world.code_worktree / ".git"
    pointer = dotgit.read_text().strip()
    head = (dotgit / "HEAD") if dotgit.is_dir() else Path(pointer.removeprefix("gitdir: ")) / "HEAD"
    with recorded_reads() as reads:
        read_complete_knowledge_review(live.world.config, live.world.review())
    named = {Path(path) for path in reads}
    assert head in named, "the review read the worktree's HEAD without naming it"
    if not dotgit.is_dir():
        assert dotgit in named, "the review read the worktree's .git pointer without naming it"


def _place_index_cache_in_a_work_tree(world: World) -> None:
    (world.config.coordination_root / ".git").mkdir()


@pytest.mark.parametrize(
    "move",
    [
        _edit_worktree,
        _move_source_branch,
        _move_memory_work_branch,
        _write_worklist,
        _rewrite_comparison_record,
        _place_index_cache_in_a_work_tree,
        _switch_the_code_worktree_to_another_branch,
    ],
    ids=lambda move: move.__name__.lstrip("_"),
)
def test_each_named_input_that_moves_computes_the_live_answers_again(live, move):
    live.intent()
    live.entries()
    move(live.world)
    live.intent()
    live.entries()
    assert live.produced == Counter(intent=2, entries=2)


def test_a_read_the_live_answer_made_beyond_its_named_inputs_is_compared_before_reuse(
    live, tmp_path
):
    requirement = tmp_path / "requirement.json"  # say, a requirement file a worklist child read
    requirement.write_text("before")

    def reading(listed: ReviewEntryListResult) -> ReviewEntryListResult:
        record_read(requirement)
        return listed

    live.entries(reading)

    live.entries(reading)
    requirement.write_text("after")
    live.entries(reading)
    assert live.produced["entries"] == 2


def test_a_working_view_that_moved_while_the_answer_was_made_keeps_nothing(live):
    path = live.world.code_worktree / CODE_FILE
    before = path.read_text()

    def moving(listed: ReviewEntryListResult) -> ReviewEntryListResult:
        path.write_text(before + "# mid-computation\n")
        return listed

    live.entries(moving)
    path.write_text(before)  # the same trees again, yet the first answer saw them move
    live.entries()
    assert live.produced["entries"] == 2


def test_a_live_answer_naming_another_comparison_is_never_kept(live):
    def elsewhere(listed: ReviewEntryListResult) -> ReviewEntryListResult:
        assert listed.comparison is not None
        return listed.model_copy(update={"comparison": listed.comparison + 1})

    live.entries(elsewhere)

    live.entries(elsewhere)
    assert live.produced["entries"] == 2


@pytest.mark.parametrize(
    "move",
    [
        _edit_worktree,
        _move_source_branch,
        _move_memory_work_branch,
        _write_worklist,
        _rewrite_comparison_record,
        _place_index_cache_in_a_work_tree,
        _switch_the_code_worktree_to_another_branch,
    ],
    ids=lambda move: move.__name__.lstrip("_"),
)
def test_a_live_subject_review_is_kept_until_a_named_input_moves(live, move):
    """One subject's review is kept on the same witness as the task context's (D574)."""

    seed = InvariantIdentitySeed(invariant_id=text_uuid("identity", INVARIANT))
    first = live.intent(selector=seed)
    assert getattr(first, "state", None) == "review"
    assert live.intent(selector=seed) == first
    assert live.produced["intent"] == 1, "an unchanged subject review was computed again"
    move(live.world)
    live.intent(selector=seed)
    assert live.produced["intent"] == 2


def test_a_page_of_a_collection_is_computed_every_time(live):
    seed = InvariantIdentitySeed(invariant_id=text_uuid("identity", INVARIANT))
    live.intent(selector=seed, page_of="records")
    live.intent(selector=seed, page_of="records")
    assert live.produced["intent"] == 2
