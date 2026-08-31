"""Retention boundary for topology-valid master and sprint reviewer reports."""

from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from shutil import rmtree
from typing import Literal
from unittest import mock

from agents_remember.application.task_docs import task_execution_registration
from agents_remember.application.task_docs.task_execution_registration import (
    register_operator_inbox_execution_evidence,
    register_task_execution_evidence,
)
from agents_remember.controlplane.interaction_retention import INBOX_MAX_CURRENT_ROWS
from agents_remember.controlplane.operator_inbox_records import OperatorInboxEntry
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.models.task_document import MasterExecutionNature
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import TaskDocument, TaskExecutionRegistration, read_task_doc

REPOSITORY = "agents-remember"
T0 = datetime(2026, 1, 1, tzinfo=UTC)


class NonLeafReviewerEvidenceRetentionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.coordination_root = Path(tempfile.mkdtemp())
        self.addCleanup(rmtree, self.coordination_root, True)
        self.observer_root = self.coordination_root / "observer"
        self.master_ref = self._write_task(
            "master",
            execution_nature="organizational",
        )
        self.sprint_ref = self._write_task(
            "sprint",
            orchestrates=["master"],
        )
        self.orphan_ref = self._write_task(
            "orphan",
            execution_nature="organizational",
        )

    def _write_task(
        self,
        name: str,
        *,
        execution_nature: MasterExecutionNature | None = None,
        orchestrates: list[str] | None = None,
    ) -> TaskDocumentRef:
        path = self.coordination_root / "tasks" / REPOSITORY / name / "task.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        document = TaskDocument(
            id=name,
            slug=name,
            title=name.title(),
            kind="master",
            repo=REPOSITORY,
            createdAt=T0.isoformat(),
            executionNature=execution_nature,
            orchestrates=orchestrates or [],
        )
        path.write_text(document.model_dump_json(by_alias=True), encoding="utf-8")
        return TaskDocumentRef(repository=REPOSITORY, path=f"{name}/task.json")

    @staticmethod
    def _report(
        entry_id: str,
        ref: TaskDocumentRef,
        *,
        created_at: datetime,
    ) -> OperatorInboxEntry:
        timestamp = created_at.isoformat()
        return OperatorInboxEntry(
            id=entry_id,
            ts=timestamp,
            state="landed",
            taskDocumentRef=ref,
            senderRole="reviewer",
            messageKind="turn-report",
            subjectTaskDocumentRef=ref,
            seatRole="reviewer",
            ask="review complete",
            response="review complete",
            createdAt=timestamp,
            createdBy="reviewer",
            createdVia="cli",
            terminalAt=timestamp,
            terminalReason="landed",
        )

    @staticmethod
    def _ordinary_terminal(entry_id: str, *, created_at: datetime) -> OperatorInboxEntry:
        timestamp = created_at.isoformat()
        return OperatorInboxEntry(
            id=entry_id,
            ts=timestamp,
            state="landed",
            senderRole="architect",
            messageKind="message",
            ask="status",
            response="done",
            createdAt=timestamp,
            createdBy="architect",
            createdVia="cli",
            terminalAt=timestamp,
            terminalReason="landed",
        )

    def test_master_and_sprint_reports_enter_ttl_without_task_mutation(self) -> None:
        store = OperatorInboxStore(self.observer_root)
        master = self._report("master-review", self.master_ref, created_at=T0)
        sprint = self._report("sprint-review", self.sprint_ref, created_at=T0)
        orphan = self._report("orphan-review", self.orphan_ref, created_at=T0)
        for entry in (master, sprint, orphan):
            store.append(entry)

        reclaimable = register_operator_inbox_execution_evidence(
            self.coordination_root,
            tuple(store.current().values()),
        )

        self.assertEqual(reclaimable, {master.id, sprint.id})
        store.compact(now=T0 + timedelta(days=3), registered_execution_ids=reclaimable)
        self.assertEqual(set(store.current()), {orphan.id})
        self.assertEqual(
            read_task_doc(
                self.coordination_root / "tasks" / REPOSITORY / "master" / "task.json"
            ).executionRegistrations,
            [],
        )
        self.assertEqual(
            read_task_doc(
                self.coordination_root / "tasks" / REPOSITORY / "sprint" / "task.json"
            ).executionRegistrations,
            [],
        )

    def test_reviewer_only_non_leaf_addressing_is_fail_closed(self) -> None:
        def registration(
            role: Literal["worker", "reviewer", "curator"],
        ) -> TaskExecutionRegistration:
            return TaskExecutionRegistration(
                sourceKind="operator-inbox-turn-report",
                role=role,
                sourceId=f"{role}-report",
                observedAt=T0.isoformat(),
            )

        for ref in (self.master_ref, self.sprint_ref):
            result = register_task_execution_evidence(
                self.coordination_root,
                ref,
                registration("reviewer"),
            )
            self.assertEqual(result.status, "reviewer-non-leaf")
            self.assertTrue(result.durable_or_irrelevant)

        orphan = register_task_execution_evidence(
            self.coordination_root,
            self.orphan_ref,
            registration("reviewer"),
        )
        self.assertEqual(orphan.status, "blocked")
        self.assertFalse(orphan.durable_or_irrelevant)

        missing = register_task_execution_evidence(
            self.coordination_root,
            TaskDocumentRef(repository=REPOSITORY, path="missing/task.json"),
            registration("reviewer"),
        )
        self.assertEqual(missing.status, "blocked")
        self.assertEqual(missing.detail, "reviewer non-leaf task source is missing")

        worker = register_task_execution_evidence(
            self.coordination_root,
            self.master_ref,
            registration("worker"),
        )
        self.assertEqual(worker.status, "not-leaf")

        malformed_ref = TaskDocumentRef(
            repository=REPOSITORY,
            path="malformed/task.json",
        )
        malformed_path = self.coordination_root / "tasks" / REPOSITORY / malformed_ref.path
        malformed_path.parent.mkdir(parents=True)
        malformed = TaskDocument(
            id="malformed",
            slug="task",
            title="Malformed",
            kind="subTask",
            repo=REPOSITORY,
            createdAt=T0.isoformat(),
        )
        malformed_path.write_text(
            malformed.model_dump_json(by_alias=True),
            encoding="utf-8",
        )
        malformed_markdown = malformed_path.with_suffix(".md")
        malformed_markdown.write_text("must remain unchanged\n", encoding="utf-8")
        json_before = malformed_path.read_bytes()
        markdown_before = malformed_markdown.read_bytes()

        malformed_result = register_task_execution_evidence(
            self.coordination_root,
            malformed_ref,
            registration("reviewer"),
        )

        self.assertEqual(malformed_result.status, "blocked")
        self.assertFalse(malformed_result.durable_or_irrelevant)
        self.assertEqual(malformed_path.read_bytes(), json_before)
        self.assertEqual(malformed_markdown.read_bytes(), markdown_before)
        self.assertEqual(read_task_doc(malformed_path).executionRegistrations, [])

    def test_loaded_registration_address_and_unexpected_altitude_fail_closed(self) -> None:
        master_path = self.coordination_root / "tasks" / REPOSITORY / "master" / "task.json"
        loaded = task_execution_registration._RegistrationSource(
            root=self.coordination_root / "tasks" / REPOSITORY,
            path=master_path,
            document=read_task_doc(master_path),
            source=mock.Mock(),
        )

        not_leaf = task_execution_registration._classify_loaded_registration_address(
            master_path.with_name("misaddressed-leaf.json"),
            loaded,
            self.master_ref,
        )
        self.assertIsNotNone(not_leaf)
        assert not_leaf is not None
        self.assertEqual(not_leaf.status, "not-leaf")

        topology = mock.Mock()
        topology.validate_role.return_value = "leaf"
        with mock.patch.object(
            task_execution_registration,
            "TaskDocumentTopology",
            return_value=topology,
        ):
            unexpected = task_execution_registration._classify_non_leaf_reviewer(
                loaded,
                self.master_ref,
            )
        self.assertEqual(unexpected.status, "blocked")
        self.assertIn("unexpected altitude 'leaf'", unexpected.detail or "")

    def test_master_and_sprint_reports_participate_in_the_hard_cap(self) -> None:
        store = OperatorInboxStore(self.observer_root)
        reports = (
            self._report("master-review", self.master_ref, created_at=T0),
            self._report("sprint-review", self.sprint_ref, created_at=T0),
        )
        for entry in reports:
            store.append(entry)
        for index in range(INBOX_MAX_CURRENT_ROWS):
            store.append(
                self._ordinary_terminal(
                    f"ordinary-{index:04d}",
                    created_at=T0 + timedelta(minutes=1, seconds=index),
                )
            )

        reclaimable = register_operator_inbox_execution_evidence(
            self.coordination_root,
            reports,
        )
        store.compact(
            now=T0 + timedelta(hours=1),
            registered_execution_ids=reclaimable,
        )

        current = store.current()
        self.assertEqual(len(current), INBOX_MAX_CURRENT_ROWS)
        self.assertTrue({entry.id for entry in reports}.isdisjoint(current))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
