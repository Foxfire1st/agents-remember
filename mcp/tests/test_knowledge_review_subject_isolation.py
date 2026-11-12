"""Judgments about a sibling never become judgments about the selected subject."""

from __future__ import annotations

from pathlib import Path

import pytest
from agents_remember_test_support.testing.review_git_trees import INVARIANT, LEAF, _write
from test_review_meaning_effects import (
    OTHER_INVARIANT,
    _history,
    _invariant_seed,
    _meaning_world,
    _resolve,
    _review,
)

pytestmark = pytest.mark.evidence_unit


def test_same_leaf_sibling_rows_are_not_selected_judgments(tmp_path: Path) -> None:
    world = _meaning_world(tmp_path)
    _write(
        world.memory_worktree,
        {
            f"knowledge/history/{LEAF}.json": _history(
                [
                    {
                        "id": "ROW-S11111",
                        "subject": OTHER_INVARIANT,
                        "disposition": "no_impact",
                        "reason": "A judgment about the sibling only.",
                        "items": [],
                        "covers": [],
                        "revision": 2,
                    }
                ]
            )
        },
    )
    payload = _review(world, _resolve(world), selector=_invariant_seed())
    assert payload.judgment.state == "rows"
    assert {row.subject for row in payload.judgment.rows} == {INVARIANT}
    assert "ROW-S11111" not in {row.row_id for row in payload.judgment.rows}
