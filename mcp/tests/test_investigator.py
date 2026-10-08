"""Investigator selection, request cardinality and preserved legacy execution reads."""

from __future__ import annotations

import json
import uuid

from agents_remember.application.agent_binding import read_agent_binding
from agents_remember.application.role_launch_context import resolve_role_launch_context
from agents_remember.cli import role_document_chats, role_launch_routes
from agents_remember.cli.investigator_receipts import (
    investigator_listing,
    open_investigator_receipts,
)
from agents_remember.cli.paseo_launch import StartingAgent
from agents_remember.cli.paseo_role_tools import start_role
from agents_remember.cli.role_launch_preparation import _role_assignment, _role_report_path
from agents_remember.cli.role_launch_receipts import _receipt_path, _request_digest
from agents_remember.cli.role_report import RoleReportRequest, read_role_report
from agents_remember.models.role_agents import RoleMessageResponse, RoleStartCall
from agents_remember.models.role_launcher import (
    RoleDispatchRequest,
    RoleResultRequest,
    RoleSelection,
)
from agents_remember.tasks.document_refs import ResolvedTaskDocument
from test_paseo_launch import LEAF_REF, MASTER_REF, SPRINT_REF
from test_paseo_role_tools import OTHER_MASTER, OTHER_SPRINT, RoleToolsTestCase, binding


class InvestigatorTests(RoleToolsTestCase):
    def setUp(self) -> None:
        super().setUp()
        sprint_doc = self.sprint.document.model_copy(
            update={"kind": "master", "orchestrates": ["master"]}
        )
        master_doc = self.master.document.model_copy(update={"kind": "master", "subTasks": []})
        self.sprint = ResolvedTaskDocument(SPRINT_REF, self.sprint.path, sprint_doc)
        self.master = ResolvedTaskDocument(MASTER_REF, self.master.path, master_doc)
        for document in (self.sprint, self.master):
            document.path.parent.mkdir(parents=True, exist_ok=True)
            document.path.write_text(document.document.model_dump_json(by_alias=True))

    def context(self, config, selection):
        if selection.role == "investigator":
            return resolve_role_launch_context(config, selection)
        return super().context(config, selection)

    def compile_handover(self, request):
        result = super().compile_handover(request)
        report = _role_report_path(
            request.context, request.workspace, request_id=request.request_id
        )
        result.update(taskReportPath=report, canonicalTaskReportPath=report)
        return result

    def selected(self, *, master=True):
        return {
            "sprint_document_ref": SPRINT_REF,
            **({"master_document_ref": MASTER_REF} if master else {}),
        }

    def test_selections_parent_assignments_and_refusals_leave_no_launch(self):
        for fields in ({}, self.selected(master=False), self.selected()):
            request = RoleSelection(role="investigator", **fields)
            context = resolve_role_launch_context(self.config, request)
            no_parent = _role_assignment(context, "/report.md")
            with_parent = _role_assignment(
                context,
                "/report.md",
                started_by=StartingAgent(self.architect.agent_id, "architect", "Projects"),
            )
            self.assertIn("Ask the developer", no_parent)
            self.assertIn("parent's first role_message", with_parent)
            self.assertNotIn("Ask the developer", with_parent)
        for caller, fields in (
            (binding("worker"), self.selected()),
            (binding("reviewer"), self.selected()),
            (binding("curator"), self.selected()),
            (binding("manager"), {}),
            (binding("manager"), self.selected(master=False)),
        ):
            result = self.start(caller, "investigator", **fields)
            self.assertEqual(result["status"], "refused")
            self.assertFalse(self.runtime.agents)
        leaf = self.start(
            self.architect, "investigator", **self.selected(), task_document_ref=LEAF_REF
        )
        self.assertEqual(leaf["status"], "refused")
        self.assertIn("start it on the master", leaf["detail"])
        self.assertFalse(self.runtime.agents)

    def test_two_master_requests_replay_and_role_ambiguity(self):
        manager = binding("manager")
        first_id = uuid.uuid4()
        first = self.start(manager, "investigator", request_id=first_id, **self.selected())
        second = self.start(manager, "investigator", **self.selected())
        self.assertEqual((first["status"], second["status"]), ("running", "running"))
        self.assertNotEqual(first["agentId"], second["agentId"])
        self.assertNotEqual(first["reportPath"], second["reportPath"])
        self.assertIn("master/notes/reports", first["reportPath"])
        request = RoleDispatchRequest.model_validate(
            {"role": "investigator", "requestId": first_id, **self.selected()}
        )
        path = _receipt_path(self.config, request, first_id)
        self.assertIn(
            "master/notes/reports/paseo-native-executions/investigator/sessions", str(path)
        )
        replay = self.start(manager, "investigator", request_id=first_id, **self.selected())
        self.assertEqual(replay["agentId"], first["agentId"])
        result = self.message(
            manager,
            role="investigator",
            sprint_document_ref=SPRINT_REF,
            master_document_ref=MASTER_REF,
        )
        self.assertEqual(result["refusal"], "recipient-ambiguous")
        self.assertEqual(set(result["candidateAgentIds"]), {first["agentId"], second["agentId"]})

    def test_public_start_admits_the_six_scopes_and_refuses_foreign_and_leaf_scopes(self):
        for caller, fields in (
            (self.architect, {}),
            (self.architect, self.selected(master=False)),
            (self.architect, self.selected()),
            (binding("orchestrator"), self.selected(master=False)),
            (binding("orchestrator"), self.selected()),
            (binding("manager"), self.selected()),
        ):
            result = self.start(caller, "investigator", **fields)
            self.assertEqual(result["status"], "running", result)
        count = len(self.runtime.agents)
        for caller in (
            binding("orchestrator", sprint_ref=OTHER_SPRINT),
            binding("manager", master_ref=OTHER_MASTER),
        ):
            result = self.start(caller, "investigator", **self.selected())
            self.assertEqual(result["refusal"], "selection-outside-callers-scope")
        for role in ("architect", "orchestrator", "manager", "worker", "reviewer", "curator"):
            result = self.start(
                binding(role), "investigator", **self.selected(), task_document_ref=LEAF_REF
            )
            self.assertEqual(result["status"], "refused", result)
        self.assertEqual(len(self.runtime.agents), count)

    def test_ninth_refuses_exact_ids_and_archiving_frees_capacity(self):
        manager = binding("manager")
        requests = [self.start(manager, "investigator", **self.selected()) for _ in range(8)]
        self.assertTrue(all(row["status"] == "running" for row in requests))
        refused = self.start(manager, "investigator", **self.selected())
        self.assertEqual(refused["status"], "refused")
        for row in requests:
            self.assertIn(row["requestId"], refused["detail"])
        self.assertIn("Close or archive one", refused["detail"])
        self.assertEqual(len(self.runtime.agents), 8)
        self.runtime.agents[requests[0]["agentId"]]["archivedAt"] = "today"
        admitted = self.start(manager, "investigator", **self.selected())
        self.assertEqual(admitted["status"], "running")
        self.assertEqual(len(self.runtime.agents), 9)

    def test_task_listing_is_bounded_task_owned_and_result_exact(self):
        for master in (False, True):
            fields = self.selected(master=master)
            selection = RoleSelection(role="investigator", **fields)
            manager = binding("manager") if master else binding("orchestrator")
            result = self.start(manager, "investigator", **fields)
            first_id = uuid.UUID(result["requestId"])
            open_requests = [result] + [
                self.start(manager, "investigator", **fields) for _ in range(7)
            ]
            for number in range(60):
                request = RoleDispatchRequest.model_validate(
                    {"role": "investigator", "requestId": uuid.uuid4(), **fields}
                )
                path = _receipt_path(self.config, request, request.request_id)
                original = json.loads(_receipt_path(self.config, selection, first_id).read_text())
                original.update(
                    requestId=str(request.request_id),
                    status="completed",
                    createdAt=f"2026-10-{number:02d}",
                )
                path.write_text(json.dumps(original))
            listing = investigator_listing(self.config, selection)
            self.assertEqual((len(listing.records), listing.omitted_count), (50, 18))
            self.assertTrue(all(row[1]["status"] == "completed" for row in listing.records))
            refused = self.start(manager, "investigator", **fields)
            self.assertEqual(refused["status"], "refused")
            for row in open_requests:
                self.assertIn(row["requestId"], refused["detail"])
            replay = self.start(manager, "investigator", request_id=first_id, **fields)
            self.assertEqual(replay["agentId"], result["agentId"])
            ambiguous = self.message(
                manager,
                role="investigator",
                sprint_document_ref=SPRINT_REF,
                master_document_ref=MASTER_REF if master else None,
            )
            self.assertEqual(ambiguous["refusal"], "recipient-ambiguous")
            self.assertEqual(
                set(ambiguous["candidateAgentIds"]),
                {row["agentId"] for row in open_requests},
            )
            exact = RoleResultRequest(role="investigator", requestId=first_id, **fields)
            self.assertEqual(
                json.loads(
                    bytes(role_launch_routes._role_launch_result_endpoint(self.config, exact).body)
                )["requestId"],
                str(first_id),
            )
            primary = RoleSelection(role="manager" if master else "orchestrator", **fields)
            self.assertEqual(role_document_chats._records(self.config, primary), [])
            self.assertEqual(
                role_document_chats._records(self.config, selection, first_id)[0]["requestId"],
                str(first_id),
            )

    def blocked_capacity_message(self, caller, agent_id, opened, selection, state):
        before = len(self.runtime.calls)
        refused = self.message(caller, agent_id=agent_id)
        self.assertEqual(refused["refusal"], "investigator-capacity", refused)
        self.assertEqual(RoleMessageResponse.model_validate(refused).status, "refused")
        for row in opened:
            self.assertIn(row["requestId"], refused["detail"])
        self.assertIn("Close or archive one", refused["detail"])
        calls = [name for name, _ in self.runtime.calls[before:]]
        self.assertNotIn("agent-send", calls)
        self.assertNotIn("agent-resume", calls)
        self.assertEqual(self.runtime.agents[agent_id]["status"], state)
        self.assertEqual(len(open_investigator_receipts(self.config, selection)), 8)
        return refused, calls

    def test_message_new_turn_obeys_capacity_before_delivery_and_resume(self):
        for master in (False, True):
            fields = self.selected(master=master)
            caller = binding("manager" if master else "orchestrator")
            selection = RoleSelection(role="investigator", **fields)
            for state in ("idle", "closed"):
                with self.subTest(master=master, state=state):
                    completed = self.start(caller, "investigator", **fields)
                    request = RoleDispatchRequest.model_validate(
                        {"role": "investigator", "requestId": completed["requestId"], **fields}
                    )
                    result_request = RoleResultRequest(
                        role="investigator", requestId=request.request_id, **fields
                    )
                    target = self.runtime.agents[completed["agentId"]]
                    target.update(status="idle", lastTurn={"state": "replied", "text": "done"})
                    role_launch_routes._role_launch_result_endpoint(self.config, result_request)
                    target["status"] = state
                    role_launch_routes._role_launch_result_endpoint(self.config, result_request)
                    # The earlier ID shares admission but its stored path/labels are preserved.
                    path = _receipt_path(self.config, request, request.request_id)
                    raw = json.loads(path.read_text())
                    raw["role"] = raw["selection"]["role"] = "system-specialist"
                    path.write_text(json.dumps(raw))
                    target["labels"]["ar.role"] = "system-specialist"
                    opened = [self.start(caller, "investigator", **fields) for _ in range(8)]
                    self.assertTrue(all(row["status"] == "running" for row in opened))
                    refused, calls = self.blocked_capacity_message(
                        caller, completed["agentId"], opened, selection, state
                    )
                    # A running recipient consumes its existing slot, as does exact Start replay.
                    steered = self.message(caller, agent_id=opened[1]["agentId"])
                    self.assertEqual(steered["taken"], "steered")
                    replay = self.start(
                        caller,
                        "investigator",
                        request_id=uuid.UUID(opened[1]["requestId"]),
                        **fields,
                    )
                    self.assertEqual(replay["agentId"], opened[1]["agentId"])
                    released = self.runtime.agents[opened[0]["agentId"]]
                    if state == "closed":
                        released["archivedAt"] = "today"
                    else:
                        released.update(
                            status="idle", lastTurn={"state": "replied", "text": "done"}
                        )
                        role_launch_routes._role_launch_result_endpoint(
                            self.config,
                            RoleResultRequest(
                                role="investigator", requestId=opened[0]["requestId"], **fields
                            ),
                        )
                        released["status"] = "closed"
                    admitted = self.message(caller, agent_id=completed["agentId"])
                    self.assertEqual(admitted["status"], "accepted", admitted)
                    self.assertEqual(admitted["taken"], "started")
                    self.assertEqual(admitted.get("resumed", False), state == "closed")
                    self.assertEqual(len(open_investigator_receipts(self.config, selection)), 8)
                    ninth = self.start(caller, "investigator", **fields)
                    self.assertEqual(ninth["status"], "refused")
                    self.assertIn(completed["requestId"], ninth["detail"])
                    self.assertEqual(_receipt_path(self.config, request, request.request_id), path)
                    self.assertEqual(json.loads(path.read_text())["role"], "system-specialist")
                    self.assertEqual(target["labels"]["ar.role"], "system-specialist")
                    print(
                        json.dumps(
                            {
                                "scope": "master" if master else "sprint",
                                "priorState": state,
                                "refused": refused,
                                "refusalNativeCalls": calls,
                                "admittedAfterRelease": admitted,
                                "persistedOpenCount": 8,
                                "followingStartRefused": ninth,
                                "existingOpenSteering": steered,
                            }
                        )
                    )
                    # Archive this cohort so the next sibling exercises a fresh eight-slot set.
                    for agent in self.runtime.agents.values():
                        agent["archivedAt"] = "today"

        # Projects keeps its existing uncapped behavior, including a completed recipient.
        taskless = [self.start(self.architect, "investigator") for _ in range(10)]
        self.assertTrue(all(row["status"] == "running" for row in taskless))
        target = self.runtime.agents[taskless[0]["agentId"]]
        target.update(status="idle", lastTurn={"state": "replied", "text": "done"})
        result = self.message(self.architect, agent_id=taskless[0]["agentId"])
        self.assertEqual((result["status"], result["taken"]), ("accepted", "started"))

    def test_server_primary_stays_coordinator_and_exact_request_reads_investigator(self):
        for master in (False, True):
            primary_role = "manager" if master else "orchestrator"
            primary = self.launched(primary_role)
            primary_id = self.receipt(primary)["agentId"]
            fields = self.selected(master=master)
            created = self.start(binding(primary_role), "investigator", **fields)
            self.assertEqual(created["status"], "running")
            selection = RoleSelection(role=primary_role, **fields)
            normal = role_document_chats.document_chats(self.config, selection, 0)
            self.assertEqual([row["agentId"] for row in normal["agents"]], [primary_id])
            exact = RoleSelection(role="investigator", **fields)
            requested = role_document_chats.document_chats(
                self.config, exact, 0, uuid.UUID(created["requestId"])
            )
            self.assertEqual(requested["agents"][0]["agentId"], created["agentId"])

    def test_old_start_binding_address_report_and_replay_preserve_recorded_paths(self):
        request_id = uuid.uuid4()
        call = RoleStartCall(role="system-specialist", request_id=request_id)
        result = start_role(self.config, call, environment=self.architect.environment())
        self.assertEqual(result["role"], "investigator")
        self.assertEqual(result["detail"].count("Role 'system-specialist' was supplied"), 1)
        request = RoleDispatchRequest(role="investigator", requestId=request_id)
        path = _receipt_path(self.config, request, request_id)
        old_path = path.parent.parent.parent / "system-specialist" / "sessions" / path.name
        raw = json.loads(path.read_text())
        raw["role"] = raw["selection"]["role"] = "system-specialist"
        raw["requestDigest"] = _request_digest(request, request, role_name="system-specialist")
        old_report = (
            self.config.workspace_root
            / ".agents-remember/reports/role-launch/system-specialist"
            / f"{request_id}.md"
        )
        old_report.parent.mkdir(parents=True)
        old_report.write_text("legacy report")
        raw["report"] = {"path": str(old_report), "canonicalPath": str(old_report)}
        old_path.parent.mkdir(parents=True)
        old_path.write_text(json.dumps(raw))
        path.unlink()
        self.runtime.agents[result["agentId"]]["labels"]["ar.role"] = "system-specialist"
        before = old_path.read_bytes()
        response = self.message(self.architect, role="system-specialist")
        self.assertEqual(response["recipientAgentId"], result["agentId"])
        self.assertIn("investigator", response["warning"])
        self.assertEqual(
            self.message(self.architect, role="investigator")["recipientAgentId"],
            result["agentId"],
        )
        binding_env = binding("investigator").environment()
        binding_env["AR_ROLE"] = "system-specialist"
        parsed = read_agent_binding(binding_env)
        assert parsed is not None
        self.assertEqual(parsed.role, "investigator")
        report_request = RoleReportRequest(role="investigator", requestId=request_id)
        report = read_role_report(self.config, report_request)
        self.assertEqual(report.status_code, 200)
        self.assertEqual(json.loads(bytes(report.body))["content"], "legacy report")
        self.assertEqual(_receipt_path(self.config, request, request_id), old_path)
        self.assertEqual(old_path.read_bytes(), before)
        replay = start_role(self.config, call, environment=self.architect.environment())
        self.assertEqual(replay["agentId"], result["agentId"])
        saved = json.loads(old_path.read_text())
        self.assertEqual(
            (saved["role"], saved["selection"]["role"]), ("system-specialist", "system-specialist")
        )
        self.assertEqual(
            self.runtime.agents[result["agentId"]]["labels"]["ar.role"], "system-specialist"
        )
        self.runtime.agents[result["agentId"]]["status"] = "closed"
        refreshed = self.refresh(request)
        self.assertTrue(refreshed["canRevive"])
        status, revived = self.revive(request)
        self.assertEqual(status, 200)
        self.assertEqual(revived["execution"]["agentId"], result["agentId"])
        self.assertEqual(_receipt_path(self.config, request, request_id), old_path)
        self.assertFalse(path.exists())
        self.assertEqual(json.loads(old_path.read_text())["role"], "system-specialist")
        self.assertEqual(old_report.read_text(), "legacy report")
