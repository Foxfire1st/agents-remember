"""Task-page skeleton projection fields (MIK-R127 rule 9 + rule 1 form)."""

from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agents_remember.models.knowledge_files.canonical import canonical_text
from agents_remember.models.knowledge_files.documents import history_path, record_path
from agents_remember.serving.projections.snapshots_impl._task_documents import (
    _SUMMARY_HEADER_NOTES,
    _SUMMARY_REVIEW_FINDINGS,
    _SUMMARY_TEXT_PREVIEW_CHARS,
    _task_doc_body_revision,
    read_task_document_body,
    read_task_documents,
)
from agents_remember.tasks import write_task_doc
from agents_remember.tasks.document import TaskDocument
from agents_remember.worktrees.worktree_contract import WorktreeContract, write_contract

REPO = "repo-a"
NOW = datetime(2026, 10, 11, tzinfo=UTC)


def _base(doc_id: str, kind: str, **over: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema": "ar-task-document/v1",
        "id": doc_id,
        "slug": "task",
        "title": f"{doc_id} title",
        "kind": kind,
        "status": "inProgress",
        "repo": REPO,
        "createdAt": "2026-10-01T00:00:00+00:00",
    }
    payload.update(over)
    return payload


class TaskPageSkeletonProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.coord = Path(self._dir.name)

        write_task_doc(
            self.coord / "tasks" / REPO / "sprint",
            TaskDocument.model_validate(
                _base(
                    "SPRINT",
                    "master",
                    type="Orchestration (sprint)",
                    integrationBranch="ar/sprint",
                    statusNote="Sprint status note.",
                    headerNotes=[
                        {"label": "Portfolio gate", "value": "Each master keeps its own state."}
                    ],
                    orchestrates=["master-a"],
                )
            ),
        )
        write_task_doc(
            self.coord / "tasks" / REPO / "master-a",
            TaskDocument.model_validate(
                _base(
                    "MASTER-A",
                    "master",
                    type="Feature",
                    executionNature="atomic",
                    statusNote="Master status note.",
                    headerNotes=[{"label": "Authority", "value": "D1"}],
                    objective="Master objective.",
                    subTasks=[
                        {
                            "number": "L1",
                            "name": "First",
                            "file": "01_first.md",
                            "status": "inProgress",
                        },
                        {
                            "number": "L2",
                            "name": "Second",
                            "file": "02_second.md",
                            "status": "planning",
                        },
                    ],
                )
            ),
        )
        write_task_doc(
            self.coord / "tasks" / REPO / "master-a",
            TaskDocument.model_validate(
                _base(
                    "TASK-L1",
                    "subTask",
                    slug="01_first",
                    type="SubTask (role instructions)",
                    statusNote="Task status note.",
                    headerNotes=[
                        {"label": "Master", "value": "task.md"},
                        {"label": "Primary requirement", "value": "[MIK-R1@v1](requirements/r.md)"},
                    ],
                    objective="Task objective.",
                    steps=[
                        {
                            "id": "S1",
                            "title": "Done step",
                            "status": "done",
                            "outcome": "The deliverable line.",
                            "note": "Done-step prose.",
                            "substeps": [
                                {
                                    "id": "S1.1",
                                    "title": "Done sub",
                                    "status": "done",
                                    "note": "Sub prose.",
                                }
                            ],
                        },
                        {"id": "S2", "title": "Open step", "status": "pending"},
                    ],
                    reviewState={
                        "round": 4,
                        "pending": True,
                        "baselineFindings": [
                            {"findingId": "F01", "description": "Still open."},
                            {"findingId": "F02", "description": "Already closed."},
                        ],
                        "remainingFindingIds": ["F01"],
                    },
                )
            ),
        )
        self.nodes = {node.id: node for node in read_task_documents(self.coord, now=NOW)}

    def test_form_follows_the_model_rule(self) -> None:
        self.assertEqual(self.nodes["SPRINT"].form, "sprint")
        self.assertEqual(self.nodes["MASTER-A"].form, "master")
        self.assertEqual(self.nodes["TASK-L1"].form, "task")

    def test_head_fields_reach_the_summary(self) -> None:
        sprint = self.nodes["SPRINT"]
        self.assertEqual(sprint.type, "Orchestration (sprint)")
        self.assertEqual(sprint.integrationBranch, "ar/sprint")
        self.assertEqual(sprint.statusNote, "Sprint status note.")
        self.assertEqual(
            [(note.label, note.value) for note in sprint.headerNotes],
            [("Portfolio gate", "Each master keeps its own state.")],
        )
        task = self.nodes["TASK-L1"]
        self.assertEqual(task.type, "SubTask (role instructions)")
        self.assertIsNone(task.integrationBranch)
        self.assertEqual(task.statusNote, "Task status note.")
        self.assertEqual(len(task.headerNotes), 2)

    def test_step_note_and_outcome_are_projected(self) -> None:
        steps = self.nodes["TASK-L1"].steps
        self.assertEqual(steps[0].outcome, "The deliverable line.")
        self.assertEqual(steps[0].note, "Done-step prose.")
        self.assertEqual(steps[0].substeps[0].note, "Sub prose.")
        self.assertIsNone(steps[1].outcome)
        self.assertIsNone(steps[1].note)

    def test_review_state_carries_findings_with_open_flags(self) -> None:
        state = self.nodes["TASK-L1"].reviewState
        self.assertIsNotNone(state)
        assert state is not None
        self.assertEqual(state.round, 4)
        self.assertTrue(state.pending)
        self.assertEqual(state.totalFindings, 2)
        self.assertEqual(state.openFindings, 1)
        self.assertEqual(
            [(item.findingId, item.description, item.open) for item in state.findings],
            [("F01", "Still open.", True), ("F02", "Already closed.", False)],
        )

    def test_documents_without_review_state_project_none(self) -> None:
        self.assertIsNone(self.nodes["SPRINT"].reviewState)
        self.assertIsNone(self.nodes["TASK-L1"].consolidation)
        self.assertIsNone(self.nodes["MASTER-A"].reviewState)

    def test_body_revision_changes_with_each_displayed_field(self) -> None:
        """L127-C-F01: every newly displayed field invalidates a cached body."""
        base_over: dict[str, Any] = {
            "statusNote": "note",
            "headerNotes": [{"label": "L", "value": "V"}],
            "type": "T",
            "steps": [{"id": "S1", "title": "Step", "status": "pending", "note": "n"}],
            "reviewState": {"round": 1, "baselineFindings": [], "remainingFindingIds": []},
            "objective": "objective",
        }
        before = _task_doc_body_revision(
            TaskDocument.model_validate(_base("REV", "subTask", **base_over))
        )
        mutations: list[dict[str, Any]] = [
            {"statusNote": "changed"},
            {"headerNotes": [{"label": "L", "value": "changed"}]},
            {"type": "changed"},
            {"steps": [{"id": "S1", "title": "Step", "status": "pending", "note": "changed"}]},
            {
                "reviewState": {
                    "round": 2,
                    "baselineFindings": [],
                    "remainingFindingIds": [],
                }
            },
            {"objective": "changed"},
        ]
        for mutate in mutations:
            over = dict(base_over)
            over.update(mutate)
            self.assertNotEqual(
                _task_doc_body_revision(
                    TaskDocument.model_validate(_base("REV", "subTask", **over))
                ),
                before,
            )

    def test_summary_bounds_two_sizes_keep_totals(self) -> None:
        """L127-C-F06: summaries carry previews + counts, never whole prose."""
        for count in (10, 100):
            with self.subTest(notes=count):
                long_text = "x" * 10000
                write_task_doc(
                    self.coord / "tasks" / REPO / f"big-{count}",
                    TaskDocument.model_validate(
                        _base(
                            f"BIG-{count}",
                            "subTask",
                            statusNote=long_text,
                            headerNotes=[
                                {"label": f"L{i}", "value": f"V{i}"} for i in range(count)
                            ],
                            steps=[
                                {
                                    "id": "S1",
                                    "title": "Step",
                                    "status": "pending",
                                    "note": long_text,
                                    "outcome": long_text,
                                }
                            ],
                            reviewState={
                                "round": 1,
                                "baselineFindings": [
                                    {"findingId": f"F{i}", "description": "y" * 8000}
                                    for i in range(count)
                                ],
                                "remainingFindingIds": [f"F{i}" for i in range(count)],
                            },
                        )
                    ),
                )
        nodes = {node.id: node for node in read_task_documents(self.coord, now=NOW)}
        for count in (10, 100):
            with self.subTest(notes=count):
                node = nodes[f"BIG-{count}"]
                self.assertEqual(len(node.headerNotes), _SUMMARY_HEADER_NOTES)
                self.assertEqual(node.headerNotesTotal, count)
                self.assertEqual(len(node.statusNote or ""), _SUMMARY_TEXT_PREVIEW_CHARS)
                self.assertTrue(node.statusNoteTruncated)
                self.assertEqual(len(node.steps[0].note or ""), _SUMMARY_TEXT_PREVIEW_CHARS)
                self.assertTrue(node.steps[0].noteTruncated)
                self.assertTrue(node.steps[0].outcomeTruncated)
                assert node.reviewState is not None
                self.assertEqual(node.reviewState.totalFindings, count)
                # Every finding of the round is open: the count names them all,
                # also where the summary carries only the first ones.
                self.assertEqual(node.reviewState.openFindings, count)
                self.assertLessEqual(len(node.reviewState.findings), _SUMMARY_REVIEW_FINDINGS)
                self.assertTrue(
                    all(item.descriptionTruncated for item in node.reviewState.findings)
                )

    def test_body_carries_whole_strings_with_clear_flags(self) -> None:
        """L127-C-F06: the on-demand body keeps every note and whole string."""
        long_text = "x" * 10000
        write_task_doc(
            self.coord / "tasks" / REPO / "bigbody",
            TaskDocument.model_validate(
                _base(
                    "BIGBODY",
                    "subTask",
                    statusNote=long_text,
                    headerNotes=[{"label": f"L{i}", "value": f"V{i}"} for i in range(10)],
                    steps=[{"id": "S1", "title": "Step", "status": "pending", "note": long_text}],
                    reviewState={
                        "round": 1,
                        "baselineFindings": [{"findingId": "F1", "description": "y" * 8000}],
                        "remainingFindingIds": ["F1"],
                    },
                )
            ),
        )
        node = read_task_document_body(
            self.coord, doc_path=f"tasks/{REPO}/bigbody/task.json", now=NOW
        )
        self.assertIsNotNone(node)
        assert node is not None
        self.assertEqual(node.statusNote, long_text)
        self.assertFalse(node.statusNoteTruncated)
        self.assertEqual(len(node.headerNotes), 10)
        self.assertEqual(node.headerNotesTotal, 10)
        self.assertEqual(node.steps[0].note, long_text)
        self.assertFalse(node.steps[0].noteTruncated)
        assert node.reviewState is not None
        self.assertEqual(node.reviewState.findings[0].description, "y" * 8000)
        self.assertFalse(node.reviewState.findings[0].descriptionTruncated)

    def test_legacy_completion_uses_latest_valid_record_in_summary_and_body(self) -> None:
        old, new = "2026-10-01T00:00:00+00:00", "2026-10-09T00:00:00+00:00"
        for stamps, explicit, expected in [
            ([new, old], None, new),
            ([old, new], None, new),
            ([new, "invalid", old], None, new),
            (["invalid"], None, None),
            ([new, old], "2026-10-12T00:00:00+00:00", "2026-10-12T00:00:00+00:00"),
        ]:
            with self.subTest(stamps=stamps, explicit=explicit):
                root = self.coord / "tasks" / REPO / "legacy-time"
                write_task_doc(
                    root,
                    TaskDocument.model_validate(
                        _base(
                            "LEGACY-TIME",
                            "subTask",
                            status="Completed",
                            completedAt=explicit,
                            decisions=[
                                {
                                    "at": stamp,
                                    "decision": "Finalize task lifecycle.",
                                    "rationale": "Retained landing.",
                                }
                                for stamp in stamps
                            ]
                            + [
                                {
                                    "at": "2026-10-13T00:00:00+00:00",
                                    "decision": "Authored decision.",
                                    "rationale": "Not a landing.",
                                }
                            ],
                        )
                    ),
                )
                before = (root / "task.json").read_bytes()
                summary = next(
                    node
                    for node in read_task_documents(self.coord, now=NOW)
                    if node.id == "LEGACY-TIME"
                )
                body = read_task_document_body(
                    self.coord, doc_path=str(root / "task.json"), now=NOW
                )
                self.assertIsNotNone(body)
                assert body is not None
                self.assertEqual(summary.completedAt, expected)
                self.assertEqual(body.completedAt, expected)
                self.assertEqual((root / "task.json").read_bytes(), before)


CONSOLIDATION_LEAF = "261126-SIM-L1"
FAMILY = "FAM-2M8HXR"
FAMILY_MEMBERS = ["INV-7K3F9Q", "INV-ZZZZZZ", "INV-0QYX99"]


def _cover() -> dict[str, Any]:
    def anchor(content: str, blob: str) -> dict[str, Any]:
        return {
            "path": "mcp/src/module.py",
            "locator": {"kind": "symbol", "name": "rule"},
            "blob": blob * 40,
            "content": "sha256:" + content * 64,
        }

    return {"id": "RLZ-0QYX992Q", "before": anchor("a", "0"), "after": anchor("b", "1")}


def _rule_row(row_id: str, subject: str, disposition: str, reason: str) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": row_id,
        "subject": subject,
        "disposition": disposition,
        "reason": reason,
        "items": [],
        "covers": [_cover()],
        "revision": 2,
    }
    if disposition == "extended":
        row["covers"] = [{**_cover(), "before": "absent"}]
    return row


class ConsolidationProjectionTests(unittest.TestCase):
    """The list of a consolidation pass on its task page, counted from the task's history."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.coord = Path(self._dir.name)
        self.memory = self.coord / "memory-worktree"
        self.task_root = self.coord / "tasks" / REPO / "master"
        self.contract = self.task_root / "enclosures" / "sim-l1" / "series-contract.md"
        self.contract.parent.mkdir(parents=True)
        write_contract(
            self.contract,
            WorktreeContract(
                task_id="master",
                task_name="master",
                repo_name=REPO,
                workflow_kind="light-task",
                memory_mode="external",
                coordination_root=self.coord,
                task_root=self.task_root,
                contract_path=self.contract,
                task_artifact=self.task_root / "task.md",
                worktree_group=self.coord,
                code_repo_path=self.coord / "code",
                code_source_branch="master",
                code_work_branch="work",
                code_base_commit="0" * 40,
                code_worktree=self.coord / "code-worktree",
                memory_repo_path=self.coord / "memory",
                memory_source_branch="master",
                memory_work_branch="work",
                memory_base_commit="1" * 40,
                memory_worktree=self.memory,
                ledger_path=self.memory / "memory.md",
                leaf_id=CONSOLIDATION_LEAF,
            ),
        )
        self.doc_path, _ = write_task_doc(
            self.task_root,
            TaskDocument.model_validate(
                _base(
                    CONSOLIDATION_LEAF,
                    "subTask",
                    slug="01_pass",
                    seriesContractPath=str(self.contract),
                    consolidationList={
                        "families": [FAMILY],
                        "rules": ["INV-1A2B3C", "INV-7K3F9Q"],
                    },
                )
            ),
        )

    def _write(self, path: str, document: dict[str, Any]) -> None:
        target = self.memory / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(canonical_text(document), encoding="utf-8")

    def _write_memory(self) -> None:
        self._write(
            record_path("family", FAMILY, "pass-family"),
            {
                "schema": "ar-family/v1",
                "id": FAMILY,
                "origin": {"task": "261126-SIM", "leaf": "261126-SIM-L0"},
                "revision": 1,
                "status": "accepted",
                "title": "A family the pass reads",
                "guarantee": "Its members hold together.",
                "members": FAMILY_MEMBERS,
                "routes": ["mcp/src"],
                "admission": "legacy-unassessed",
            },
        )
        rows = [
            {
                "id": "ROW-9Q7K3F",
                "subject": FAMILY,
                "disposition": "stands",
                "reason": "its members still give the guarantee",
                "items": [],
                "examined": [{"id": member, "revision": 2} for member in FAMILY_MEMBERS],
                "revision": 1,
            },
            _rule_row("ROW-3F9Q7K", "INV-1A2B3C", "no_impact", "read; the text holds"),
            _rule_row("ROW-7K3F9Q", "INV-7K3F9Q", "extended", "linked its realization"),
            _rule_row("ROW-ZZZZZZ", "INV-ZZZZZZ", "unresolved", "the named proof fails"),
        ]
        self._write(
            history_path(CONSOLIDATION_LEAF),
            {"schema": "ar-history/v1", "leaf": CONSOLIDATION_LEAF, "closed": False, "rows": rows},
        )

    def _summary(self) -> Any:
        return next(
            node
            for node in read_task_documents(self.coord, now=NOW)
            if node.id == CONSOLIDATION_LEAF
        )

    def test_counts_each_outcome_of_the_expanded_list_and_serves_rows_in_the_body(self) -> None:
        self._write_memory()
        summary = self._summary().consolidation
        body = read_task_document_body(self.coord, doc_path=str(self.doc_path), now=NOW)
        assert summary is not None and body is not None and body.consolidation is not None
        counts = (summary.total, summary.stands, summary.corrected, summary.unresolved)
        self.assertEqual(counts, (5, 2, 1, 1))
        self.assertEqual(summary.open, 1)
        self.assertTrue(summary.memoryRead)
        self.assertIsNone(summary.rows)
        rows = [(row.subject, row.outcome, row.family) for row in body.consolidation.rows or []]
        self.assertEqual(
            rows,
            [
                (FAMILY, "stands", None),
                ("INV-1A2B3C", "stands", None),
                ("INV-7K3F9Q", "corrected", None),
                ("INV-ZZZZZZ", "unresolved", FAMILY),
                ("INV-0QYX99", "open", FAMILY),
            ],
        )
        self.assertEqual((body.consolidation.rows or [])[3].reason, "the named proof fails")
        self.assertEqual(self._summary().bodyRevision, body.bodyRevision)

    def test_without_the_memory_the_ids_stand_as_listed_and_no_outcome_is_read(self) -> None:
        summary = self._summary().consolidation
        assert summary is not None
        self.assertFalse(summary.memoryRead)
        self.assertEqual((summary.total, summary.open), (3, 3))
        self.assertEqual(
            [summary.families, summary.rules], [[FAMILY], ["INV-1A2B3C", "INV-7K3F9Q"]]
        )


if __name__ == "__main__":
    unittest.main()
