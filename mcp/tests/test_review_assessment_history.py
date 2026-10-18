"""Unreadable after-tree history remains unavailable with bounded, complete diagnostics."""

from __future__ import annotations

from pathlib import Path

import pytest
from agents_remember.application.review_candidate_resolution import (
    ReviewCandidateResolution,
    resolve_review_candidate,
)
from agents_remember.application.review_judgment import review_judgment
from agents_remember.memory.knowledge_index import KnowledgeIndex, text_uuid
from agents_remember.models.knowledge.base import PROSE_MAX_LENGTH
from agents_remember.models.knowledge.read import InvariantIdentitySeed
from agents_remember.models.knowledge.review_judgment import ReviewJudgmentPane
from agents_remember_test_support.testing.review_git_trees import (
    INVARIANT,
    LEAF,
    MASTER,
    REPO,
    build_world,
)

pytestmark = pytest.mark.evidence_unit


def _unreadable_histories(root: Path, count: int) -> tuple[ReviewJudgmentPane, tuple[str, ...]]:
    world = build_world(root)
    paths = tuple(f"knowledge/history/{LEAF}-attempt-{10000 + n}.json" for n in range(count))
    for relative in paths:
        path = world.memory_worktree / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{broken", encoding="utf-8")
    resolved = resolve_review_candidate(world.config, REPO, MASTER, LEAF)
    assert isinstance(resolved, ReviewCandidateResolution), resolved
    with KnowledgeIndex(resolved.candidate_database) as index:
        answer = index.history_rows_of(LEAF)
        assert not answer.index.complete
        # The current pane samples the detail, but the native answer keeps every unreadable path.
        assert tuple(path for path, _ in answer.index.problems) == paths
    pane = review_judgment(
        resolved, InvariantIdentitySeed(invariant_id=text_uuid("identity", INVARIANT))
    )
    assert pane.state == "unavailable" and pane.rows == ()
    return pane, paths


def test_a_leaf_binding_many_missing_curator_artifacts_reads_as_unavailable(tmp_path: Path) -> None:
    """Preserve L25 Q8/L31's overflow regression on R57's staying history/judgment interface.

    No retired external-artifact channel is restored: actual failed history reads remain
    unavailable, every failure stays in the native answer, and the current short wording is exact.
    """
    count = PROSE_MAX_LENGTH // len(f"knowledge/history/{LEAF}-attempt-10000.json, ") + 1
    pane, paths = _unreadable_histories(tmp_path / "many", count)
    assert len(", ".join(paths)) > PROSE_MAX_LENGTH
    assert len(pane.detail) <= PROSE_MAX_LENGTH
    assert pane.detail.startswith(f"the after memory index is incomplete ({count} file(s) failed: ")
    assert all(path in pane.detail for path in paths[:3])
    assert f"and {count - 3} more" in pane.detail and paths[-1] not in pane.detail

    # Keep the old ten-file boundary: more than the sample, but its complete paths fit the bound.
    fits, few = _unreadable_histories(tmp_path / "few", 10)
    assert len(", ".join(few)) <= PROSE_MAX_LENGTH
    expected = (
        f"the after memory index is incomplete (10 file(s) failed: {', '.join(few[:3])}, "
        "and 7 more); "
        "history it did not read cannot be reported as absent"
    )
    assert len(expected) <= PROSE_MAX_LENGTH
    assert fits.detail == expected
