"""Focused document-rail reads and the shared first-start preparation seam."""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from unittest.mock import patch

from agents_remember.cli import (
    paseo_role_tools,
    role_document_chats,
    role_launch_progress,
    role_launch_workspace,
)
from agents_remember.errors import RolePreparationError
from agents_remember.models.role_launcher import RoleSelection
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_paseo_role_tools import RoleToolsTestCase
from test_paseo_status import StatusTestCase


class DocumentChatReadTests(StatusTestCase):
    def test_old_id_route_answers_canonically_with_one_notice(self) -> None:
        request = self.launched("investigator")
        app = FastAPI()
        role_document_chats.register_document_chat_route(app, self.config)
        response = TestClient(app).post(
            "/api/role-launch/document-chats",
            json={"role": "system-specialist", "requestId": str(request.request_id)},
        )
        self.assertEqual(response.status_code, 200)
        answer = response.json()
        self.assertEqual(answer["role"], "investigator")
        self.assertEqual(answer["warning"].count("Role 'system-specialist' was supplied"), 1)
        self.assertEqual(answer["agents"][0]["agentId"], self.receipt(request)["agentId"])

    def test_exact_started_projects_request_is_read_without_substituting_an_architect(self) -> None:
        request = self.launched("investigator")
        selection = RoleSelection(role="investigator")
        result = role_document_chats.document_chats(self.config, selection, 0, request.request_id)
        self.assertEqual(result["agents"][0]["agentId"], self.receipt(request)["agentId"])
        self.assertEqual(result["agents"][0]["labels"]["ar.role"], "investigator")
        self.runtime.agents[self.receipt(request)["agentId"]]["archivedAt"] = "today"
        self.assertEqual(
            role_document_chats.document_chats(self.config, selection, 0, request.request_id)[
                "agents"
            ],
            [],
        )

    def test_read_filters_archived_and_missing_agents_and_keeps_document_workspaces(self) -> None:
        worker = self.launched("worker")
        reviewer = self.launched("reviewer")
        selection = RoleSelection.model_validate(
            worker.model_dump(exclude={"request_id", "action", "agent_override"})
        )
        answer = role_document_chats.document_chats(self.config, selection, 0)
        self.assertEqual(len(answer["agents"]), 2)
        self.assertEqual(
            {row["workspaceId"] for row in answer["agents"]},
            {self.receipt(worker)["execution"]["workspaceId"]},
        )
        self.runtime.agents[self.receipt(worker)["agentId"]]["archivedAt"] = "today"
        self.runtime.agents.pop(self.receipt(reviewer)["agentId"])
        self.assertEqual(
            role_document_chats.document_chats(self.config, selection, 0)["agents"], []
        )

    def test_projects_uses_last_started_live_architect_and_pages_old_archived_records(self) -> None:
        requests = [self.launched("architect") for _ in range(5)]
        receipts = [self.receipt(request) for request in requests]
        records = list(reversed(receipts))
        with patch.object(role_document_chats, "_records", return_value=records):
            for receipt in records[:3]:
                self.runtime.agents[receipt["agentId"]]["archivedAt"] = "today"
            selection = RoleSelection(role="architect")
            first = role_document_chats.document_chats(self.config, selection, 0)
            self.assertEqual(first, {"agents": [], "nextOffset": 3})
            second = role_document_chats.document_chats(self.config, selection, 3)
            self.assertEqual(second["agents"][0]["agentId"], records[3]["agentId"])


class FirstStartPreparationTests(RoleToolsTestCase):
    def test_start_reads_recorded_master_parent_and_same_request_reuses_the_enclosure(self) -> None:
        series = self.master.path.parent / "series-contract.md"
        series.parent.mkdir(parents=True, exist_ok=True)
        series.write_text("contract selected by the production reader\n", encoding="utf-8")
        request_id = uuid.uuid4()
        with patch.object(
            role_launch_workspace,
            "load_contract",
            return_value=SimpleNamespace(parent_task_name="master"),
        ) as read:
            first = self.start(self.architect, "worker", request_id=request_id)
            self.assertTrue(first["ok"], first)
            self.assertEqual(first["preparation"], {"enclosure": "created", "workspace": "created"})
            self.assertEqual(self.enclosures.start_calls[0].parent_task, "master")
            read.assert_called_once_with(series)
            repeat = self.start(self.architect, "worker", request_id=request_id)
        self.assertEqual(repeat["agentId"], first["agentId"])
        self.assertEqual(len(self.enclosures.start_calls), 1)

    def test_preparation_refusals_keep_exact_status_reason_and_repair_on_role_start(self) -> None:
        cases = {
            "atomic-series-contract-edge-mismatch": "reconcile",
            "choose_stale_base_recovery": "source branch",
            "reopen-required": "task_reopen",
            "leaf_enclosure_start_timeout": "worktree_status",
            "missing-integration-branch": "integrationBranch",
        }
        for status, remedy in cases.items():
            with self.subTest(status=status):
                self.enclosures.start_result = {
                    "ok": False,
                    "state": status,
                    "summary": "integrationBranch missing"
                    if status == "missing-integration-branch"
                    else "exact refusal",
                }
                result = self.start(self.architect, "worker")
                self.assertEqual(result["preparationStatus"], status)
                self.assertIn(remedy, result["nextAction"])
                self.assertIn(self.enclosures.start_result["summary"], result["detail"])
                self.assertFalse(self.enclosures.started)
                self.assertIsNone(
                    json.loads(bytes(role_launch_progress.read(uuid.uuid4()).body))["phase"]
                )
        error = role_launch_workspace._preparation_refusal(
            {
                "ok": False,
                "state": "x",
                "summary": "own words",
                "nextTool": "repair",
                "nextArgs": {"exact": "args"},
            },
            self.leaf.path,
            "agents-remember",
        )
        self.assertIsInstance(error, RolePreparationError)
        self.assertIn('repair({"exact": "args"})', str(error))

    def test_masterless_starts_report_opened_without_a_creation_guess(self) -> None:
        for role in ("investigator", "orchestrator"):
            with self.subTest(role=role):
                result = self.start(self.architect, role)
                self.assertTrue(result["ok"], result)
                self.assertEqual(
                    result["preparation"], {"enclosure": "not-applicable", "workspace": "opened"}
                )
                self.assertIn("Projects workspace was opened", result["detail"])
                self.assertIn("host does not report whether it was created", result["detail"])
                self.assertEqual(self.enclosures.start_calls, [])
        request = self.launched("architect")
        result = paseo_role_tools._started(self.config, request)
        self.assertEqual(result["preparation"]["workspace"], "opened")
        self.assertIn("host does not report whether it was created", result["detail"])

    def test_progress_has_one_request_and_is_reclaimed_when_dispatch_ends(self) -> None:
        request_id = uuid.uuid4()
        role_launch_progress.begin(request_id)
        self.assertEqual(
            json.loads(bytes(role_launch_progress.read(request_id).body))["phase"], "preparing"
        )
        role_launch_progress.starting()
        self.assertEqual(
            json.loads(bytes(role_launch_progress.read(request_id).body))["phase"], "starting"
        )
        role_launch_progress.finish()
        self.assertIsNone(json.loads(bytes(role_launch_progress.read(request_id).body))["phase"])
