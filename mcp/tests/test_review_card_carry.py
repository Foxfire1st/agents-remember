"""An invariant's cards carried to a newer comparison when every read they were made from holds (D574).

On real Git trees through the dashboard's own port: an edit no entry of the invariant names carries
its cards, and the carried answer is the one the newer comparison computes; an edit of a file an
entry names, or of the memory, computes them again. The invariant's review is not carried: it names
its comparison's trees.
"""

from __future__ import annotations

from typing import Any

import pytest
from agents_remember.application.review_candidate_resolution import (
    ReviewCandidateResolution,
    resolve_review_candidate,
)
from agents_remember.application.review_card_carry import CardCarry, carried_entries
from agents_remember.application.review_tree_knowledge import read_review_trees
from agents_remember.cli.dashboard import serving_collaborators
from agents_remember.memory.knowledge_index import text_uuid
from agents_remember.models.knowledge.review_trees import ReviewTreesResult
from agents_remember.serving.review_trees import ReviewTreesQuery
from agents_remember.worktrees.services import reset_worktree_services
from agents_remember_test_support.testing.review_git_trees import (
    CODE_FILE,
    CODE_V1,
    INVARIANT,
    INVARIANT_PATH,
    LEAF,
    MASTER,
    REPO,
    World,
    build_world,
    invariant,
)

LOGGER = "agents_remember.application.review_card_carry"
KEY = text_uuid("identity", INVARIANT)


@pytest.fixture
def world(tmp_path):
    reset_worktree_services()
    made = build_world(tmp_path)
    made.edit()
    yield made
    reset_worktree_services()


def _comparison(world: World) -> int:
    resolved = resolve_review_candidate(world.config, REPO, MASTER, LEAF)
    assert isinstance(resolved, ReviewCandidateResolution) and resolved.trees is not None
    return resolved.trees.record.number


def _cards(number: int) -> ReviewTreesQuery:
    return ReviewTreesQuery(
        repository_id=REPO, master=MASTER, leaf_id=LEAF, number=number, invariants=(KEY,)
    )


def _dump(result: ReviewTreesResult) -> dict[str, Any]:
    return result.model_dump(mode="json")


def _edit_a_file_no_entry_names(world: World) -> None:
    (world.code_worktree / "pkg" / "other.py").write_text("UNRELATED = True\n")


def _edit_the_file_its_entries_name(world: World) -> None:
    (world.code_worktree / CODE_FILE).write_text(
        CODE_V1.replace("return value", "return value + 2")
    )


def _restate_the_invariant(world: World) -> None:
    (world.memory_worktree / INVARIANT_PATH).write_text(invariant(3, "Values land as written."))


@pytest.mark.parametrize(
    ("move", "carried", "reason"),
    [
        (_edit_a_file_no_entry_names, True, None),
        (_edit_the_file_its_entries_name, False, f"the after side's {CODE_FILE} differs"),
        (_restate_the_invariant, False, "the memory trees differ"),
    ],
    ids=["a-file-no-entry-names", "the-file-its-entries-name", "its-statement"],
)
def test_the_cards_are_carried_exactly_when_every_read_they_were_made_from_holds(
    world: World, caplog: pytest.LogCaptureFixture, move, carried: bool, reason: str | None
) -> None:
    collaborators = serving_collaborators(world.config)
    trees = collaborators.review_trees
    assert trees is not None
    try:
        made = _comparison(world)
        first = trees(_cards(made))
        assert first.state == "trees" and first.entries, "the invariant has no cards to carry"
        move(world)
        newer = _comparison(world)
        assert newer != made
        with caplog.at_level("INFO", logger=LOGGER):
            answer = trees(_cards(newer))
        # Carried or computed, the answer is the newer comparison's own: what it computes afresh.
        assert _dump(answer) == _dump(read_review_trees(world.config, _cards(newer)))
        assert answer.comparison is not None and answer.comparison.number == newer
        if carried:
            assert f"carried the cards of {KEY} from comparison #{made} to #{newer}" in caplog.text
        else:
            assert f"computed the cards of {KEY} for comparison #{newer}" in caplog.text
            assert f"not carried from #{made}: {reason})" in caplog.text
    finally:
        shutdown = collaborators.review_trees_shutdown
        assert shutdown is not None
        shutdown()


def test_nothing_is_carried_from_nothing_or_onto_the_comparison_it_was_made_for(
    world: World,
) -> None:
    number = _comparison(world)
    made = read_review_trees(world.config, _cards(number))
    assert made.comparison is not None
    refused = ReviewTreesResult(state="refused", repository_id=REPO, master=MASTER, leaf_id=LEAF)
    assert carried_entries(refused, made.comparison) == "nothing kept to carry"
    carry = CardCarry()
    leaf = (REPO, MASTER, LEAF)
    assert carry.carry(leaf, (KEY,), made.comparison, lambda kept: kept) is None  # nothing held
    carry.made(leaf, (KEY,), made)
    assert carry.carry(leaf, (KEY,), made.comparison, lambda kept: kept) is None  # the same number
