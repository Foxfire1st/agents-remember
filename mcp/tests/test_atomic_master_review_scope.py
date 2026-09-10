"""Focused ownership and altitude checks for the atomic-master review boundary."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from agents_remember.tasks import read_task_doc, write_task_doc
from agents_remember.worktrees.route_review import RouteReviewError
from agents_remember.worktrees.route_review_scope import (
    require_current_route_review,
    resolve_atomic_master_scope,
)
from agents_remember.worktrees.worktree_contract import load_contract
from test_closeout_queue import MASTER_A, MASTER_B, QueueFixture


class AtomicMasterReviewScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.fixture = QueueFixture(self.root, atomic_b=True, memory_mode="internal")
        self.leaf = self.fixture.contracts[MASTER_B]
        assert self.leaf.parent_contract_path is not None
        self.series = load_contract(self.leaf.parent_contract_path)
        leaf_doc = read_task_doc(self.leaf.task_root / "leaf-b.json")
        write_task_doc(self.leaf.task_root, leaf_doc.model_copy(update={"routeReview": None}))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_atomic_child_review_is_deferred_and_leaf_integration_has_no_master_gate(self) -> None:
        scope = resolve_atomic_master_scope(self.leaf)

        assert scope is not None
        self.assertEqual(len(scope.children), 1)
        self.assertIsNone(read_task_doc(self.leaf.task_root / "leaf-b.json").routeReview)
        status = require_current_route_review(self.leaf)
        self.assertEqual(status["status"], "deferred-atomic-child")

    def test_missing_canonical_master_is_an_ownership_error(self) -> None:
        (self.series.task_root / "task.json").unlink()

        with self.assertRaises(RouteReviewError) as raised:
            resolve_atomic_master_scope(self.leaf)

        self.assertEqual(raised.exception.status, "route-review-atomic-owner-missing")

    def test_parented_leaf_without_parent_contract_is_not_treated_as_standalone(self) -> None:
        broken = replace(self.leaf, parent_contract_path=None)

        with self.assertRaises(RouteReviewError) as raised:
            resolve_atomic_master_scope(broken)

        self.assertEqual(raised.exception.status, "route-review-atomic-owner-invalid")

    def test_organizational_leaf_keeps_the_existing_leaf_review_boundary(self) -> None:
        fixture = QueueFixture(self.root / "organizational", memory_mode="internal")
        leaf = fixture.contracts[MASTER_A]

        status = require_current_route_review(leaf)

        self.assertTrue(status["required"])
        self.assertEqual(status["status"], "current")


if __name__ == "__main__":
    unittest.main()
