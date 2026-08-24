"""L3 source-census purity, drift fencing, and terminal-empty forcing."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from agents_remember.controlplane.closeout_queue_store import CloseoutQueueStore
from agents_remember.models.closeout_projection import CloseoutQueueState
from agents_remember.models.queue.closeout_queue import CloseoutQueueRequest
from agents_remember.tasks import read_task_doc, write_task_doc
from agents_remember.worktrees.queue.closeout_queue import QueueActor, closeout_queue_tool
from test_closeout_queue import LEAF_A, MASTER_A, MASTER_B, NOW, SPRINT, QueueFixture


class CloseoutProjectionCensusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.fixture = QueueFixture(Path(self.temporary.name), memory_mode="internal")
        self.actor = QueueActor(role="orchestrator", task_document_ref=SPRINT)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _rebuild(self) -> dict[str, Any]:
        return closeout_queue_tool(
            self.fixture.cfg,
            CloseoutQueueRequest(action="rebuild", sprint_task_document_ref=SPRINT),
            actor=self.actor,
            now=NOW,
        )

    def test_rebuild_uses_only_current_waiting_doors_not_old_rows(self) -> None:
        self.fixture.declare(MASTER_A)
        store = CloseoutQueueStore(self.fixture.coord, SPRINT)
        current = store.read_raw(timestamp=NOW)
        malicious = current.members[0].model_copy(
            update={
                "generationId": "9" * 64,
                "taskDocumentRef": self.fixture.leaf_refs[MASTER_B],
                "owningMaster": MASTER_B,
            }
        )
        store.state_path.write_text(
            current.model_copy(update={"members": [malicious]}).model_dump_json(indent=2),
            encoding="utf-8",
        )
        self.assertEqual(self.fixture.status()["state"], "invalid-empty")
        self.assertEqual(self.fixture.status()["members"], [])
        rebuilt = self._rebuild()
        members = rebuilt["members"]
        assert isinstance(members, list)
        self.assertEqual(len(members), 1)
        self.assertEqual(members[0]["generationId"], current.members[0].generationId)
        self.assertEqual(members[0]["taskDocumentRef"], LEAF_A.model_dump())

    def test_old_valid_row_becomes_invalid_empty_immediately_after_task_change(self) -> None:
        self.fixture.declare(MASTER_A)
        before = self.fixture.status()
        master_path = self.fixture.tasks / "master-a" / "task.json"
        master = read_task_doc(master_path)
        write_task_doc(master_path.parent, master.model_copy(update={"title": "changed"}))
        after = self.fixture.status()
        self.assertEqual(before["state"], "valid-built")
        self.assertEqual(after["state"], "invalid-empty")
        self.assertEqual(after["members"], [])
        self.assertIsNotNone(after["nextAction"])

    def test_unrelated_sibling_change_requires_refresh_but_not_redeclaration(self) -> None:
        self.fixture.declare(MASTER_A)
        generation = self.fixture.status()["members"][0]["generationId"]
        sibling_path = self.fixture.tasks / "master-b" / "task.json"
        sibling = read_task_doc(sibling_path)
        write_task_doc(sibling_path.parent, sibling.model_copy(update={"title": "new sibling"}))
        self.assertEqual(self.fixture.status()["state"], "invalid-empty")
        rebuilt = self._rebuild()
        self.assertEqual(rebuilt["members"][0]["generationId"], generation)
        self.assertEqual(rebuilt["members"][0]["classification"], "ready")

    def test_review_evidence_drift_changes_source_identity_and_blocks_member(self) -> None:
        self.fixture.declare(MASTER_A)
        contract = self.fixture.contracts[MASTER_A]
        report = contract.task_root / "notes" / "reports" / "leaf-a-review.md"
        report.write_text("# Review\n\nChanged after declaration.\n", encoding="utf-8")
        self.assertEqual(self.fixture.status()["state"], "invalid-empty")
        rebuilt = self._rebuild()
        member = rebuilt["members"][0]
        self.assertEqual(member["classification"], "blocked")
        self.assertIn("door-review-provenance-stale", member["reasons"])

    def test_grade_evidence_drift_is_part_of_the_source_fingerprint(self) -> None:
        self.fixture.declare(MASTER_A)
        grade = self.fixture.tasks / "sprint" / "grade.md"
        grade.write_text("# Replaced grade evidence\n", encoding="utf-8")
        self.assertEqual(self.fixture.status()["state"], "invalid-empty")
        rebuilt = self._rebuild()
        self.assertIn(
            "door-scheduling-provenance-stale",
            rebuilt["members"][0]["reasons"],
        )

    def test_present_nonregular_series_authority_is_not_projected_as_absent(self) -> None:
        self.fixture.declare(MASTER_A)
        path = self.fixture.tasks / "master-b" / "series-contract.md"
        path.unlink()
        path.symlink_to(self.fixture.tasks / "missing-series-contract.md")

        status = self.fixture.status()
        rebuilt = self._rebuild()

        self.assertEqual(status["state"], "invalid-empty")
        self.assertEqual(rebuilt["state"], "invalid-empty")
        self.assertTrue(any(problem["kind"] == "series" for problem in rebuilt["sourceProblems"]))

    def test_present_nonregular_leaf_door_is_not_projected_as_absent(self) -> None:
        self.fixture.declare(MASTER_A)
        path = self.fixture.contracts[MASTER_A].contract_path
        path.unlink()
        path.symlink_to(self.fixture.tasks / "missing-leaf-contract.md")

        status = self.fixture.status()
        rebuilt = self._rebuild()

        self.assertEqual(status["state"], "invalid-empty")
        self.assertEqual(rebuilt["state"], "invalid-empty")
        self.assertTrue(
            any(
                problem["kind"] == "door"
                and problem["errorType"] == "enclosure-contract-nonregular"
                for problem in rebuilt["sourceProblems"]
            )
        )

    def test_leaf_door_ancestor_symlink_cannot_redirect_projection_authority(self) -> None:
        self.fixture.declare(MASTER_A)
        contract_path = self.fixture.contracts[MASTER_A].contract_path
        outside = self.fixture.root / "outside-enclosure"
        outside.mkdir()
        redirected_contract = outside / "series-contract.md"
        redirected_contract.write_bytes(contract_path.read_bytes())
        redirect_parent = self.fixture.coord / "redirected-enclosure"
        redirect_parent.symlink_to(outside, target_is_directory=True)
        leaf_path = self.fixture.tasks / "master-a" / "leaf-a.json"
        leaf = read_task_doc(leaf_path)
        redirected = leaf.model_copy(
            update={
                "enclosures": [
                    enclosure.model_copy(
                        update={
                            "enclosurePath": (redirect_parent / "series-contract.md").as_posix()
                        }
                    )
                    for enclosure in leaf.enclosures
                ]
            }
        )
        write_task_doc(leaf_path.parent, redirected)

        status = self.fixture.status()
        rebuilt = self._rebuild()

        self.assertEqual(status["state"], "invalid-empty")
        self.assertEqual(rebuilt["state"], "invalid-empty")
        self.assertEqual(rebuilt["members"], [])
        self.assertTrue(
            any(
                problem["kind"] == "door"
                and problem["errorType"] == "enclosure-contract-ancestor-noncanonical"
                for problem in rebuilt["sourceProblems"]
            )
        )

    def test_completed_sprint_rebuilds_as_valid_terminal_empty(self) -> None:
        self.fixture.declare(MASTER_A)
        sprint_path = self.fixture.tasks / "sprint" / "task.json"
        sprint = read_task_doc(sprint_path)
        write_task_doc(sprint_path.parent, sprint.model_copy(update={"status": "Completed"}))
        rebuilt = self._rebuild()
        self.assertEqual(rebuilt["state"], "valid-built")
        self.assertEqual(rebuilt["sourceClassification"], "terminal")
        self.assertEqual(rebuilt["members"], [])
        stored = CloseoutQueueState.model_validate_json(
            CloseoutQueueStore(self.fixture.coord, SPRINT).state_path.read_text(encoding="utf-8")
        )
        self.assertEqual(stored.serviceCondition, "valid-built")
        self.assertEqual(stored.sourceClassification, "terminal")
