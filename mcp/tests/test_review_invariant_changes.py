"""Which invariants differ between the comparison a page shows and a newer one (D576).

The rule is one function: in one comparison and not the other, the record's bytes changed, or the
overview row changed in any field. Every field of the row is compared unless a test shows it
volatile; nothing positional enters; and what cannot be read is ``unavailable``, never an empty
list. Then the same rule on real Git trees through the dashboard's own port.
"""

from __future__ import annotations

from typing import Any

import pytest
from agents_remember.application import review_invariant_changes
from agents_remember.application.review_candidate_resolution import (
    ReviewCandidateResolution,
    resolve_review_candidate,
)
from agents_remember.application.review_invariant_changes import invariants_that_differ
from agents_remember.cli.dashboard import serving_collaborators
from agents_remember.memory.knowledge_index import text_uuid
from agents_remember.models.knowledge.review_tree_overview import (
    ReviewOverview,
    ReviewSubjectSummary,
)
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

# --- the rule ------------------------------------------------------------------------------------

_ROW: dict[str, Any] = {
    "changed": 1,
    "unchanged": 2,
    "not_comparable": 0,
    "changed_span": 3,
    "changed_paths": ("pkg/a.py",),
    "entry_currentness": "current",
    "proofs": {"current": 1},
    "content": "sha256:" + "a" * 64,
}
# One different value for each compared field.
_OTHER: dict[str, Any] = {
    "changed": 2,
    "unchanged": 3,
    "not_comparable": 1,
    "changed_span": 4,
    "changed_paths": ("pkg/b.py",),
    "entry_currentness": "stale",
    "proofs": {"stale": 1},
    "content": "sha256:" + "b" * 64,
}


def _overview(*rows: ReviewSubjectSummary) -> ReviewOverview:
    return ReviewOverview(state="ready", subjects=rows)


def _row(key: str, **fields: Any) -> ReviewSubjectSummary:
    return ReviewSubjectSummary(invariant_key=key, **{**_ROW, **fields})


def test_every_field_of_the_row_is_named_here_and_compared_unless_shown_volatile() -> None:
    compared = set(ReviewSubjectSummary.model_fields) - {"invariant_key"}
    assert set(_OTHER) == compared, "a row field this test does not change is a field unchecked"
    assert not review_invariant_changes.VOLATILE_ROW_FIELDS, (
        "an exclusion goes in only with the test that shows the field volatile"
    )


@pytest.mark.parametrize("field", sorted(_OTHER))
def test_a_change_of_any_one_row_field_marks_that_invariant_only(field: str) -> None:
    before = _overview(_row("one"), _row("two"), _row("three"))
    after = _overview(_row("one"), _row("two", **{field: _OTHER[field]}), _row("three"))
    assert invariants_that_differ(before, after, {}, {}) == ("two",)


def test_a_pure_reorder_marks_nothing() -> None:
    rows = (_row("one"), _row("two", content="sha256:" + "c" * 64), _row("three"))
    records = {"one": "1" * 40, "two": "2" * 40}
    reordered = dict(reversed(list(records.items())))
    assert (
        invariants_that_differ(_overview(*rows), _overview(*reversed(rows)), records, reordered)
        == ()
    )


def test_the_records_bytes_and_presence_mark_by_themselves() -> None:
    rows = _overview(_row("one"), _row("two"))
    assert invariants_that_differ(rows, rows, {"one": "1" * 40}, {"one": "9" * 40}) == ("one",)
    assert invariants_that_differ(_overview(_row("one")), rows, {}, {}) == ("two",)  # added
    assert invariants_that_differ(rows, _overview(_row("one")), {}, {}) == ("two",)  # removed
    assert invariants_that_differ(rows, rows, {}, {"three": "3" * 40}) == ("three",)


# --- on real trees, through the dashboard's port ------------------------------------------------


@pytest.fixture
def world(tmp_path):
    reset_worktree_services()
    made = build_world(tmp_path)
    yield made
    reset_worktree_services()


def _comparison(world: World) -> int:
    resolved = resolve_review_candidate(world.config, REPO, MASTER, LEAF)
    assert isinstance(resolved, ReviewCandidateResolution) and resolved.trees is not None
    return resolved.trees.record.number


def _shut(collaborators: Any) -> None:
    shutdown = collaborators.review_trees_shutdown
    assert shutdown is not None
    shutdown()


def _changes(world: World, shown: int, newer: int) -> Any:
    collaborators = serving_collaborators(world.config)
    try:
        assert collaborators.review_invariant_changes is not None
        return collaborators.review_invariant_changes(REPO, MASTER, LEAF, shown, newer)
    finally:
        _shut(collaborators)


KEY = text_uuid("identity", INVARIANT)


def test_the_same_trees_differ_in_nothing_and_two_computations_of_them_agree(world: World) -> None:
    shown = _comparison(world)
    unchanged = _changes(world, shown, shown)
    assert (unchanged.state, unchanged.invariants) == ("named", ())
    # Two owners computing the same comparison's overview from nothing: a field that changed
    # between two computations of the same entries would mark the invariant here.
    overviews = []
    for _ in range(2):
        collaborators = serving_collaborators(world.config)
        query = ReviewTreesQuery(
            repository_id=REPO, master=MASTER, leaf_id=LEAF, number=shown, view="overview"
        )
        trees = collaborators.review_trees
        assert trees is not None
        answer = trees(query)
        _shut(collaborators)
        assert answer.overview is not None and answer.overview.state == "ready"
        overviews.append(answer.overview)
    assert overviews[0].subjects, "the overview names no invariant to compare"
    assert invariants_that_differ(overviews[0], overviews[1], {}, {}) == ()


def _edit_under_the_invariant(world: World) -> None:
    (world.code_worktree / CODE_FILE).write_text(
        CODE_V1.replace("return value", "return value + 1")
    )


def _edit_elsewhere(world: World) -> None:
    (world.code_worktree / "pkg" / "other.py").write_text("UNRELATED = True\n")


def _restate_the_invariant(world: World) -> None:
    (world.memory_worktree / INVARIANT_PATH).write_text(invariant(2, "Values land as given."))


@pytest.mark.parametrize(
    ("move", "marked"),
    [(_edit_under_the_invariant, (KEY,)), (_edit_elsewhere, ()), (_restate_the_invariant, (KEY,))],
    ids=["code-under-it", "a-file-no-invariant-covers", "its-statement"],
)
def test_a_move_marks_exactly_the_invariants_it_changed(world: World, move, marked) -> None:
    shown = _comparison(world)
    move(world)
    newer = _comparison(world)
    assert newer != shown
    changes = _changes(world, shown, newer)
    assert (changes.state, changes.invariants) == ("named", marked)


def test_a_comparison_that_cannot_be_read_is_unavailable_never_an_empty_list(world: World) -> None:
    shown = _comparison(world)
    changes = _changes(world, shown, shown + 41)
    assert changes.state == "unavailable" and changes.invariants == ()
    assert changes.reason is not None and f"#{shown + 41}" in changes.reason


def test_an_edit_inside_an_already_changed_location_marks_by_the_rows_content(world: World) -> None:
    """Same paths, counts and span: only the identity of the entries tells the rows apart."""

    world.edit()  # `land` already changed: return value + 0
    shown = _comparison(world)
    (world.code_worktree / CODE_FILE).write_text(
        CODE_V1.replace("return value", "return value + 2")
    )
    newer = _comparison(world)
    changes = _changes(world, shown, newer)
    assert (changes.state, changes.invariants) == ("named", (KEY,))
