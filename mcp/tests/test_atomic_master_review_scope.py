"""Focused ownership and altitude checks for the atomic-master review boundary."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from agents_remember.tasks import read_task_doc, write_task_doc
from agents_remember.tasks.document_refs import ResolvedTaskDocument
from agents_remember.worktrees.integration.master_review_gate import master_route_review_block
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.route_review import RouteReviewError, document_ref
from agents_remember.worktrees.route_review_scope import (
    build_master_route_review,
    require_current_master_route_review,
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
        self.assertIsNone(master_route_review_block(self.leaf, WorktreeArgs()))

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

    def test_master_integration_requires_published_master_review(self) -> None:
        with self.assertRaises(RouteReviewError) as raised:
            require_current_master_route_review(self.series)

        self.assertEqual(raised.exception.status, "route-review-master-required")

    def _publish_master_review(self):
        master_path = self.series.task_root / "task.json"
        master = read_task_doc(master_path)
        evidence = self.series.task_root / "notes" / "reports" / "master-review.md"
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text("Independent master review.\n", encoding="utf-8")
        review = build_master_route_review(
            self.series,
            ResolvedTaskDocument(
                ref=document_ref(self.series, master_path),
                path=master_path,
                document=master,
            ),
            {
                "verdict": "pass",
                "verdictRef": "notes/reports/master-review.md",
                "routes": [
                    {
                        "route": "atomic-master",
                        "verdict": "pass",
                        "evidenceRef": "notes/reports/master-review.md",
                    }
                ],
            },
            now=datetime(2026, 9, 8, tzinfo=UTC),
        )
        write_task_doc(self.series.task_root, master.model_copy(update={"routeReview": review}))
        return review

    def test_published_master_review_is_current_until_evidence_changes(self) -> None:
        self._publish_master_review()

        current = require_current_master_route_review(self.series)
        self.assertEqual(current["status"], "current")

        evidence = self.series.task_root / "notes" / "reports" / "master-review.md"
        evidence.write_text("Review evidence changed after publication.\n", encoding="utf-8")
        with self.assertRaises(RouteReviewError) as raised:
            require_current_master_route_review(self.series)

        self.assertEqual(raised.exception.status, "route-review-evidence-stale")

    def test_child_membership_change_stales_published_master_review(self) -> None:
        self._publish_master_review()
        master_path = self.series.task_root / "task.json"
        master = read_task_doc(master_path)
        child = read_task_doc(self.series.task_root / "leaf-b.json")
        child_two = child.model_copy(update={"id": "LEAF-B2", "slug": "leaf-b2"})
        write_task_doc(self.series.task_root, child_two)
        rows = [
            *master.subTasks,
            master.subTasks[0].model_copy(
                update={"number": "C2", "name": "leaf-b2", "file": "leaf-b2.md"}
            ),
        ]
        write_task_doc(self.series.task_root, master.model_copy(update={"subTasks": rows}))

        with self.assertRaises(RouteReviewError) as raised:
            require_current_master_route_review(self.series)

        self.assertEqual(raised.exception.status, "route-review-master-scope-stale")

    def test_master_status_bookkeeping_does_not_stale_published_review(self) -> None:
        self._publish_master_review()
        master_path = self.series.task_root / "task.json"
        master = read_task_doc(master_path)
        write_task_doc(
            self.series.task_root,
            master.model_copy(update={"status": "Completed", "statusNote": "Landed."}),
        )

        current = require_current_master_route_review(self.series)
        self.assertEqual(current["status"], "current")

    def test_organizational_leaf_keeps_the_existing_leaf_review_boundary(self) -> None:
        fixture = QueueFixture(self.root / "organizational", memory_mode="internal")
        leaf = fixture.contracts[MASTER_A]

        status = require_current_route_review(leaf)

        self.assertTrue(status["required"])
        self.assertEqual(status["status"], "current")


if __name__ == "__main__":
    unittest.main()
