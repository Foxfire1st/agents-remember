"""Compact projection preserves the full read's counts and gate authority."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from agents_remember.application.review_tree_overview import (
    summarize_inventory,
    summarize_worklist,
)
from agents_remember.models.knowledge.item_verdict import ItemVerdict
from agents_remember.models.knowledge.review_tree_entries import (
    ReviewTreeEntry,
    ReviewTreeEntrySide,
)
from agents_remember.models.knowledge.review_trees import (
    ReviewEntryInventory,
    ReviewWorklistView,
)
from agents_remember.serving.review_trees import ReviewTreesSelection


def test_inventory_unknown_is_not_measured_zero() -> None:
    summary = summarize_inventory(
        ReviewEntryInventory(state="unavailable", reason="missing object")
    )
    assert summary.state == "unavailable"
    assert summary.reason == "missing object"
    assert summary.subjects == ()


def test_removed_entry_uses_before_range_and_only_recorded_currentness() -> None:
    before = ReviewTreeEntrySide(
        recorded=True,
        state="resolved",
        path="old.py",
        start_line=7,
        end_line=9,
        currentness="current",
    )
    after = ReviewTreeEntrySide(
        recorded=False,
        state="resolved",
        path="new.py",
        start_line=1,
        end_line=1,
        currentness="stale",
    )
    entry = ReviewTreeEntry(
        id="PRF-A",
        invariant="INV-A",
        invariant_key="key",
        kind="proof",
        change="changed",
        before=before,
        after=after,
    )
    summary = summarize_inventory(
        ReviewEntryInventory(state="ready", entries=(entry, entry))
    ).subjects[0]
    assert (summary.changed, summary.changed_span, summary.changed_paths) == (
        1,
        3,
        ("old.py",),
    )
    assert summary.entry_currentness == "current"
    assert summary.proofs == {"current": 1}


@pytest.mark.parametrize("name,total", [("l181", 74), ("l104", 96)])
def test_captured_inventory_preserves_every_count(name: str, total: int) -> None:
    source = (
        Path(__file__).parents[2]
        / "dashboard/src/panels/review"
        / f"l89.{name}-cards.captured.json"
    )
    inventory = ReviewEntryInventory.model_validate(
        {"state": "ready", "entries": json.loads(source.read_text())["entries"]}
    )
    summary = summarize_inventory(inventory)
    assert (
        sum(row.changed + row.unchanged + row.not_comparable for row in summary.subjects) == total
    )
    if name == "l181":
        # Three same-region entries remain counted separately, although only one row draws.
        assert len(summary.subjects) == 1
        row = summary.subjects[0]
        assert (row.changed, row.unchanged, row.not_comparable) == (15, 31, 28)


def worklist() -> ReviewWorklistView:
    return ReviewWorklistView(
        source="computed",
        bound=True,
        state="complete",
        items=(
            {
                "id": "a",
                "kind": "unexplained_hunk",
                "subject": "file",
                "facts": {"path": "a.py"},
            },
            {
                "id": "b",
                "kind": "changed_invariant",
                "subject": "INV-A",
                "planning": "planned",
                "facts": {},
            },
        ),
        item_states={
            "a": ItemVerdict(state="open", reason="no row"),
            "b": ItemVerdict(state="answered", witness="row"),
        },
    )


def test_worklist_uses_gate_answers_and_keeps_planning_marks() -> None:
    summary = summarize_worklist(worklist())
    assert summary.open_count == 1
    assert summary.file_open_counts == {"a.py": 1}
    assert summary.planning == {"INV-A": "changed invariant · planned"}


@pytest.mark.parametrize(
    "field,value",
    [
        ("bound", False),
        ("source", "absent"),
        ("state", "incomplete"),
        ("incomplete", ({"reason": "unread"},)),
    ],
)
def test_partial_worklist_cannot_claim_answered(field: str, value: object) -> None:
    summary = summarize_worklist(worklist().model_copy(update={field: value}))
    assert summary.open_count is None
    assert summary.file_open_counts == {}


def test_undecided_worklist_keeps_unrelated_file_answer() -> None:
    original = worklist()
    original.item_states["b"] = ItemVerdict(state="undecided", reason="unread")
    summary = summarize_worklist(original)
    assert summary.open_count is None
    assert summary.file_open_counts == {"a.py": 1}


def test_overview_is_explicit_and_cannot_mask_a_focused_question() -> None:
    assert ReviewTreesSelection().view == "complete"
    assert ReviewTreesSelection(view="overview").problem() is None
    assert (
        ReviewTreesSelection(view="overview", invariants="INV-A").problem()
        == "overview cannot be combined with a focused selector"
    )
    assert ReviewTreesSelection(view="unknown").problem() == "view may only be complete or overview"
