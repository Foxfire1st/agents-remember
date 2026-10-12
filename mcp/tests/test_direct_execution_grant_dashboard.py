"""MIK-R94 rule 2: the developer's grant control behind the dashboard route.

Exercises ``serving.direct_execution_grants`` on a synthetic coordination root:
give and take-back recording who gave the grant, refusals for masters and
unknown actions, and the started-leaf refusal naming its evidence -- with
parity against the architect's function on the same evidence.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agents_remember.application.agent_binding import AgentBinding
from agents_remember.application.direct_execution_grants import direct_execution_grant_tool
from agents_remember.kernel.direct_execution_grants import read_grant
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.serving.direct_execution_grants import (
    DeveloperGrantRequest,
    developer_grant_tool,
)
from agents_remember.tasks import SubTaskRef, TaskDocument, read_task_doc, write_task_doc

LEAF_ID = "TEST-DIRECT-LEAF"
LEAF_SLUG = "test-direct-leaf"
MASTER_NAME = "direct-master"


def _fixture(root: Path) -> SimpleNamespace:
    coord = root / "coord"
    task_root = coord / "tasks" / "repo-a" / MASTER_NAME
    task_root.mkdir(parents=True)
    write_task_doc(
        task_root,
        TaskDocument.model_validate(
            {
                "id": "TEST-DIRECT-MASTER",
                "slug": MASTER_NAME,
                "title": "Direct master",
                "kind": "master",
                "status": "planning",
                "repo": "repo-a",
                "createdAt": "2026-10-06T00:00:00+00:00",
                "executionNature": "atomic",
                "subTasks": [
                    SubTaskRef(
                        number=LEAF_ID,
                        name="Test direct leaf",
                        file=f"{LEAF_SLUG}.md",
                        status="planning",
                    ).model_dump(mode="json"),
                ],
            }
        ),
    )
    write_task_doc(
        task_root,
        TaskDocument.model_validate(
            {
                "id": LEAF_ID,
                "slug": LEAF_SLUG,
                "title": "Test direct leaf",
                "kind": "subTask",
                "status": "planning",
                "repo": "repo-a",
                "createdAt": "2026-10-06T00:00:00+00:00",
                "objective": "Exercise the dashboard control.",
                "requirements": ["The fixture leaf takes a grant from the control."],
            }
        ),
    )
    config = McpRuntimeConfig(
        config_path=root / "settings.json",
        coordination_root=coord,
        workspace_root=root,
        transcript_root=coord / "logs" / "mcp",
    )
    return SimpleNamespace(
        config=config,
        coord=coord,
        task_root=task_root,
        leaf_ref=TaskDocumentRef(
            repository="repo-a", path=f"{MASTER_NAME}/{LEAF_SLUG}.json"
        ),
        master_ref=TaskDocumentRef(repository="repo-a", path=f"{MASTER_NAME}/task.json"),
    )


def _architect_binding() -> AgentBinding:
    return AgentBinding(
        agent_id="11111111-1111-4111-8111-111111111111",
        role="architect",
        request_id="22222222-2222-4222-8222-222222222222",
        report_path="/reports/architect",
    )


class DeveloperGrantControlTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.fixture = _fixture(Path(self._tmp.name))

    def _give(self, ref: TaskDocumentRef | None = None) -> dict:
        ref = ref or self.fixture.leaf_ref
        return developer_grant_tool(self.fixture.config, ref.repository, ref.path, "give")

    def test_request_model_rejects_extra_fields(self) -> None:
        with self.assertRaises(ValueError):
            DeveloperGrantRequest(
                repository="repo-a",
                path=f"{MASTER_NAME}/{LEAF_SLUG}.json",
                action="give",
                extra="nope",
            )

    def test_give_records_developer_grant_and_second_give_is_unchanged(self) -> None:
        given = self._give()
        self.assertTrue(given["ok"])
        self.assertEqual(given["state"], "granted")
        self.assertTrue(given["changed"])
        self.assertEqual(given["grant"]["grantedBy"], "developer")
        self.assertIn("developer", given["summary"])
        stored = read_grant(self.fixture.coord, self.fixture.leaf_ref)
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored.granted_by, "developer")
        again = self._give()
        self.assertTrue(again["ok"])
        self.assertEqual(again["state"], "unchanged")
        self.assertFalse(again["changed"])
        self.assertIn("nothing changed", again["summary"])

    def test_revoke_and_absent_revoke(self) -> None:
        self.assertEqual(
            developer_grant_tool(
                self.fixture.config,
                self.fixture.leaf_ref.repository,
                self.fixture.leaf_ref.path,
                "revoke",
            )["state"],
            "unchanged",
        )
        self._give()
        revoked = developer_grant_tool(
            self.fixture.config,
            self.fixture.leaf_ref.repository,
            self.fixture.leaf_ref.path,
            "revoke",
        )
        self.assertTrue(revoked["ok"])
        self.assertEqual(revoked["state"], "revoked")
        self.assertIsNone(read_grant(self.fixture.coord, self.fixture.leaf_ref))

    def test_master_grant_is_refused_and_unknown_action_is_refused(self) -> None:
        refused = self._give(self.fixture.master_ref)
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["status"], "direct-grant-leaf-only")
        self.assertIn("leaf only", refused["detail"])
        unknown = developer_grant_tool(
            self.fixture.config,
            self.fixture.leaf_ref.repository,
            self.fixture.leaf_ref.path,
            "delete",
        )
        self.assertFalse(unknown["ok"])
        self.assertEqual(unknown["status"], "direct-grant-action-unknown")

    def test_missing_leaf_is_refused(self) -> None:
        missing = TaskDocumentRef(repository="repo-a", path=f"{MASTER_NAME}/nope.json")
        refused = self._give(missing)
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["status"], "direct-grant-leaf-missing")

    def _mark_started(self) -> None:
        leaf = read_task_doc(self.fixture.task_root / f"{LEAF_SLUG}.json")
        write_task_doc(
            self.fixture.task_root, leaf.model_copy(update={"status": "inProgress"})
        )

    def test_started_leaf_refuses_give_and_names_evidence(self) -> None:
        self._mark_started()
        refused = self._give()
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["status"], "direct-grant-leaf-started")
        self.assertIn("status=inProgress", refused["detail"])
        self.assertIn("Finish the leaf as it is", refused["detail"])
        self.assertIsNone(read_grant(self.fixture.coord, self.fixture.leaf_ref))

    def test_started_leaf_parity_with_architect_function(self) -> None:
        self._mark_started()
        control = self._give()
        architect = direct_execution_grant_tool(
            self.fixture.config, self.fixture.leaf_ref, "give", binding=_architect_binding()
        )
        self.assertFalse(control["ok"])
        self.assertFalse(architect["ok"])
        self.assertEqual(control["status"], architect["status"])
        self.assertEqual(control["status"], "direct-grant-leaf-started")

    def test_revoke_after_start_is_refused(self) -> None:
        self._give()
        self._mark_started()
        refused = developer_grant_tool(
            self.fixture.config,
            self.fixture.leaf_ref.repository,
            self.fixture.leaf_ref.path,
            "revoke",
        )
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["status"], "direct-grant-leaf-started")
        self.assertIsNotNone(read_grant(self.fixture.coord, self.fixture.leaf_ref))

    def test_enclosure_file_refuses_give(self) -> None:
        enclosure = (
            self.fixture.task_root / "enclosures" / "test-direct-leaf" / "series-contract.md"
        )
        enclosure.parent.mkdir(parents=True)
        enclosure.write_text("not a contract\n", encoding="utf-8")
        refused = self._give()
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["status"], "direct-grant-leaf-started")
        self.assertIn("enclosure", refused["detail"])


if __name__ == "__main__":
    unittest.main()
