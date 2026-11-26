"""Scoped native retirement against the role tool and fake Paseo runtime."""

import json
import shutil
import unittest
import uuid
from unittest.mock import patch

import test_paseo_bridge_agents
from agents_remember.application.agent_binding import AgentBinding
from agents_remember.cli import (
    paseo_role_tools,
    role_launch_first_run,
    role_receipt_files,
    role_retire,
)
from agents_remember.cli.role_launch_receipts import _read_receipt, _receipt_path
from agents_remember.cli.role_retire import retire_native_seat
from agents_remember.controlplane.held_store_layout import HeldMessage, hold_message, waiting_total
from agents_remember.mcp.tools.role_agents import retire_child_payload
from agents_remember.models.role_launcher import LauncherRole, RoleSelection
from agents_remember.models.structural.agent import RetireChildRequest
from agents_remember.tasks import write_task_doc
from agents_remember.tasks.document import SubTaskRef
from paseo_role_tools_test_support import OTHER_MASTER, OTHER_SPRINT
from test_paseo_launch import LEAF_REF, MASTER_REF, ROLE_REFS, SPRINT_REF
from test_paseo_role_tools import RoleToolsTestCase, binding, selection_of

REASON = "the seat is replaced"


def retire(
    test: RoleToolsTestCase,
    caller: AgentBinding,
    agent_id: str,
    *,
    role: LauncherRole = "worker",
    interrupt_turn: bool = False,
) -> dict:
    """One native retirement call by the given caller for the named agent."""

    request = RetireChildRequest(
        role=role,
        reason=REASON,
        agent_id=agent_id,
        interrupt_turn=interrupt_turn,
    )
    return retire_native_seat(test.config, request, environment=caller.environment())


def receipt_of(test: RoleToolsTestCase) -> dict:
    """The worker seat's current receipt for this line's leaf selection."""

    selection = RoleSelection(
        role="worker",
        sprintDocumentRef=SPRINT_REF,
        masterDocumentRef=MASTER_REF,
        taskDocumentRef=LEAF_REF,
    )
    receipt = _read_receipt(_receipt_path(test.config, selection))
    assert receipt is not None
    return receipt


class NativeRetireTests(RoleToolsTestCase):
    """The caller's scope governs admission and the recorded reason."""

    def setUp(self) -> None:
        super().setUp()
        master = self.master.document.model_copy(
            update={"subTasks": [SubTaskRef(number="01_LEAF", name="Leaf", file="01_leaf.json")]}
        )
        write_task_doc(self.master.path.parent, master)
        self.attach_master()

    def attach_master(self, *, attached: bool = True) -> None:
        sprint = self.sprint.document.model_copy(
            update={"orchestrates": [self.master.document.id] if attached else []}
        )
        write_task_doc(self.sprint.path.parent, sprint)

    def receipt_path_for_worker(self):
        """The worker seat's current receipt path for this line's leaf selection."""

        selection = RoleSelection(
            role="worker",
            sprintDocumentRef=SPRINT_REF,
            masterDocumentRef=MASTER_REF,
            taskDocumentRef=LEAF_REF,
        )
        return _receipt_path(self.config, selection)

    def test_a_manager_retires_its_worker_and_can_repeat(self) -> None:
        manager = binding("manager")
        started = self.start(manager, "worker")
        worker = started["agentId"]
        self.assertEqual(started["parentAgentId"], manager.agent_id)
        self.runtime.agents[worker].update(status="idle")

        result = retire(self, manager, worker)
        self.assertEqual(
            (result["ok"], result["operation"], result["status"], result["role"]),
            (True, "retire_child", "retired", "worker"),
        )
        self.assertIn(manager.agent_id, result["detail"])
        # The host archived the agent: it never took a turn for this.
        self.assertTrue(self.runtime.agents[worker]["archivedAt"])
        # The launch record names the caller and the reason.
        record = receipt_of(self)
        self.assertEqual(record["status"], "stopped")
        self.assertEqual(
            {key: record["retiredBy"][key] for key in ("caller", "callerAgentId", "reason")},
            {
                "caller": f"manager agent {manager.agent_id}",
                "callerAgentId": manager.agent_id,
                "reason": REASON,
            },
        )
        self.assertTrue(record["retiredBy"]["at"])
        # A repeat changes nothing and says so.
        again = retire(self, manager, worker)
        self.assertEqual((again["ok"], again["status"]), (True, "already-retired"))
        self.assertIn("Nothing changed", again["detail"])

    def test_a_message_attachment_is_not_read_as_a_launch_record(self) -> None:
        """A JSON document filed beside the receipts is not a receipt and blocks no retirement.

        Message attachments are filed under the same report folders, so an enumeration that took
        every ``*.json`` as a launch record refused any retirement in the root with "the role
        execution receipt is malformed".
        """

        manager = binding("manager")
        worker = self.start(manager, "worker")["agentId"]
        self.runtime.agents[worker].update(status="idle")
        messages = (
            self.config.coordination_root
            / "notes"
            / "reports"
            / "paseo-native-executions"
            / "manager"
            / "messages"
        )
        messages.mkdir(parents=True, exist_ok=True)
        (messages / "20261113T000000Z_manager-to-worker.attach-1-report.json").write_text(
            json.dumps({"schema": "ar-some-other-document/v1", "rows": []}), encoding="utf-8"
        )

        result = retire(self, manager, worker)
        self.assertEqual(
            (result["ok"], result["operation"], result["status"]),
            (True, "retire_child", "retired"),
        )
        self.assertTrue(self.runtime.agents[worker]["archivedAt"])

    def test_scope_refusals_name_the_caller_and_the_recorded_selection(self) -> None:
        manager = binding("manager")
        worker = self.start(manager, "worker")["agentId"]
        reviewer = self.start(manager, "reviewer")["agentId"]
        cases: tuple[tuple[AgentBinding, str, LauncherRole, str], ...] = (
            (binding("manager", master_ref=OTHER_MASTER), worker, "worker", OTHER_MASTER.key),
            (binding("orchestrator", sprint_ref=OTHER_SPRINT), worker, "worker", OTHER_SPRINT.key),
            (binding("worker", agent_id=worker), reviewer, "reviewer", LEAF_REF.key),
            (binding("investigator"), worker, "worker", "investigator"),
            (binding("worker", agent_id=worker), worker, "worker", "itself"),
        )
        for caller, target, role, explanation in cases:
            with self.subTest(caller=caller.role, reason=explanation):
                calls = list(self.runtime.calls)
                result = retire(self, caller, target, role=role)
                self.assertEqual((result["ok"], result["status"]), (False, "outside-scope"))
                for value in (caller.agent_id, target, LEAF_REF.path, explanation):
                    self.assertIn(value, result["detail"])
                self.assertEqual(list(self.runtime.calls), calls)
                self.assertFalse(self.runtime.agents[target]["archivedAt"])

    def test_a_manager_retires_a_worker_another_manager_started(self) -> None:
        worker = self.start(binding("manager"), "worker")["agentId"]
        self.runtime.agents[worker].update(status="idle")
        manager = binding("manager")
        path = self.receipt_path_for_worker()
        held_id = self.hold_fixture_message(path, worker, manager)
        self.assertEqual(retire(self, manager, worker)["status"], "retired")
        self.assert_retirement_effects(path, worker, manager, held_id)

    def test_an_orchestrator_retires_a_manager_after_its_master_is_attached(self) -> None:
        self.attach_master(attached=False)
        request = self.request("manager").model_copy(update={"sprint_document_ref": None})
        self.assertEqual(self.dispatch(request)[1]["status"], "running")
        record = self.receipt(request)
        self.assertIsNone(record["selection"]["sprintDocumentRef"])
        self.runtime.agents[record["agentId"]].update(status="idle")
        caller = binding("orchestrator")
        self.assertEqual(
            retire(self, caller, record["agentId"], role="manager")["status"], "outside-scope"
        )
        self.attach_master()
        path = self.receipt_path(request)
        held_id = self.hold_fixture_message(path, record["agentId"], caller)
        result = retire(self, caller, record["agentId"], role="manager")
        self.assertEqual(result["status"], "retired")
        self.assertEqual(result["masterDocumentRef"], MASTER_REF.model_dump(mode="json"))
        self.assert_retirement_effects(path, record["agentId"], caller, held_id)

    def test_a_manager_retires_an_investigator_started_by_the_architect(self) -> None:
        with patch.dict(ROLE_REFS, investigator=ROLE_REFS["manager"]):
            started = self.start(self.architect, "investigator")
        self.assertTrue(started["ok"], started)
        self.assertEqual(started["parentAgentId"], self.architect.agent_id)
        agent_id = started["agentId"]
        self.runtime.agents[agent_id].update(status="idle")
        selection = RoleSelection(role="investigator", **ROLE_REFS["manager"])
        path = _receipt_path(self.config, selection, uuid.UUID(started["requestId"]))
        manager = binding("manager")
        held_id = self.hold_fixture_message(path, agent_id, manager)
        self.assertEqual(retire(self, manager, agent_id, role="investigator")["status"], "retired")
        self.assert_retirement_effects(path, agent_id, manager, held_id)

    def test_a_nonleaf_turn_and_subagents_need_interrupt_permission(self) -> None:
        _request, agent_id = self.started("investigator", status="running")
        caller = self.architect
        self.assertEqual(
            retire(self, caller, agent_id, role="investigator")["status"], "agent-in-turn"
        )
        self.runtime.agents[agent_id].update(status="idle")
        with patch.object(role_retire, "running_subagents", return_value=2):
            result = retire(self, caller, agent_id, role="investigator")
            self.assertEqual(result["status"], "agent-in-turn")
            self.assertIn("2 running provider subagent(s)", result["detail"])
            self.assertFalse(self.runtime.agents[agent_id]["archivedAt"])
            self.assertEqual(
                retire(self, caller, agent_id, role="investigator", interrupt_turn=True)["status"],
                "retired",
            )
        _request, agent_id = self.started("investigator", status="running")
        self.assertEqual(
            retire(self, caller, agent_id, role="investigator", interrupt_turn=True)["status"],
            "retired",
        )

    def test_retirement_names_every_open_child_then_succeeds_after_they_retire(self) -> None:
        _request, manager = self.started("manager", status="idle")
        parent = binding("manager", agent_id=manager)
        worker = self.start(parent, "worker")["agentId"]
        reviewer = self.start(parent, "reviewer")["agentId"]
        self.runtime.agents[worker].update(status="idle")
        self.runtime.agents[reviewer].update(status="closed")
        request = RetireChildRequest(agent_id=manager, role="manager", reason=REASON)
        blocked = retire_child_payload(self.config, request, environ=self.architect.environment())
        self.assertEqual(blocked["status"], "open-children")
        self.assertEqual({row["agentId"] for row in blocked["openChildren"]}, {worker, reviewer})
        self.assertFalse(any(command == "agent-archive" for command, _ in self.runtime.calls))
        self.assertEqual(retire(self, self.architect, worker)["status"], "retired")
        self.assertEqual(
            retire(self, self.architect, reviewer, role="reviewer")["status"], "retired"
        )
        self.assertEqual(retire(self, self.architect, manager, role="manager")["status"], "retired")

    def test_an_agent_in_a_turn_is_not_retired_without_the_word(self) -> None:
        manager = binding("manager")
        worker = self.start(manager, "worker")["agentId"]
        self.runtime.agents[worker].update(status="running")

        refused = retire(self, manager, worker)
        self.assertEqual((refused["ok"], refused["status"]), (False, "agent-in-turn"))
        self.assertFalse(self.runtime.agents[worker]["archivedAt"])

        result = retire(self, manager, worker, interrupt_turn=True)
        self.assertEqual((result["ok"], result["status"]), (True, "retired"))

    def test_an_unknown_agent_id_is_refused_by_name(self) -> None:
        manager = binding("manager")
        self.start(manager, "worker")

        unknown = retire(self, manager, str(uuid.uuid4()))
        self.assertEqual((unknown["ok"], unknown["status"]), (False, "unknown-agent"))

        not_an_id = retire(self, manager, "no-such-agent")
        self.assertEqual((not_an_id["ok"], not_an_id["status"]), (False, "unknown-agent"))

    def test_after_retirement_a_message_is_refused_and_the_role_finds_nothing(self) -> None:
        manager = binding("manager")
        worker = self.start(manager, "worker")["agentId"]
        self.runtime.agents[worker].update(status="idle")
        self.assertEqual(retire(self, manager, worker)["status"], "retired")

        with patch.object(paseo_role_tools, "provider_limit_hold") as hold:
            by_id = self.message(manager, agent_id=worker)
        hold.assert_not_called()
        self.assertEqual(
            (by_id["ok"], by_id["status"], by_id["refusal"]),
            (False, "refused", "recipient-archived"),
        )
        by_role = self.message(manager, role="worker", **selection_of("worker"))
        self.assertEqual(
            (by_role["ok"], by_role["status"], by_role["refusal"]),
            (False, "refused", "recipient-archived"),
        )
        self.assertIn("archived", by_role["detail"])

    def test_a_duplicate_record_is_refused_without_host_or_disk_effects(self) -> None:
        manager = binding("manager")
        worker = self.start(manager, "worker")["agentId"]
        self.runtime.agents[worker].update(status="idle")
        path = self.receipt_path_for_worker()
        shutil.copyfile(path, path.parent / "duplicate.json")
        calls_before = list(self.runtime.calls)

        result = retire(self, manager, worker)

        self.assertEqual((result["ok"], result["status"]), (False, "ambiguous-agent"))
        self.assertIn(worker, result["detail"])
        self.assertFalse(self.runtime.agents[worker]["archivedAt"])
        self.assertEqual(list(self.runtime.calls), calls_before)

    def test_an_unauthorized_repeat_after_retirement_is_refused(self) -> None:
        manager = binding("manager")
        worker = self.start(manager, "worker")["agentId"]
        self.runtime.agents[worker].update(status="idle")
        self.assertEqual(retire(self, manager, worker)["status"], "retired")

        other = binding("manager", master_ref=OTHER_MASTER)
        result = retire(self, other, worker)

        self.assertEqual((result["ok"], result["status"]), (False, "outside-scope"))
        self.assertIn(OTHER_MASTER.key, result["detail"])

    def test_a_current_plus_history_record_is_refused_without_effects(self) -> None:
        # The sealed mixed-record boundary: the same agent id in one current and
        # one archived record is ambiguous, with no host call and no disk write.
        manager = binding("manager")
        worker = self.start(manager, "worker")["agentId"]
        self.runtime.agents[worker]["status"] = "idle"
        path = self.receipt_path_for_worker()
        history = path.parent / "history"
        history.mkdir(exist_ok=True)
        shutil.copyfile(path, history / "same-agent.json")
        before = path.read_bytes()
        calls_before = list(self.runtime.calls)

        request = RetireChildRequest(
            task_document_ref=LEAF_REF, role="worker", reason=REASON, agent_id=worker
        )
        answer = retire_child_payload(self.config, request, environ=manager.environment())

        self.assertEqual((answer["ok"], answer["status"]), (False, "ambiguous-agent"))
        self.assertIn(worker, answer["detail"])
        self.assertFalse(self.runtime.agents[worker]["archivedAt"])
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(list(self.runtime.calls), calls_before)

    def test_entry_point_reports_duplicate_and_unauthorized_repeat(self) -> None:
        # Rule 8 through the MCP entry point: the envelope carries the refusal.
        manager = binding("manager")
        worker = self.start(manager, "worker")["agentId"]
        self.runtime.agents[worker].update(status="idle")
        path = self.receipt_path_for_worker()
        shutil.copyfile(path, path.parent / "duplicate.json")

        request = RetireChildRequest(
            task_document_ref=LEAF_REF, role="worker", reason=REASON, agent_id=worker
        )
        duplicate = retire_child_payload(self.config, request, environ=manager.environment())
        self.assertEqual(duplicate["status"], "ambiguous-agent")
        self.assertIn(worker, duplicate["detail"])

        (path.parent / "duplicate.json").unlink()
        self.assertEqual(
            retire_child_payload(self.config, request, environ=manager.environment())["status"],
            "retired",
        )
        other = binding("manager", master_ref=OTHER_MASTER)
        repeat = retire_child_payload(self.config, request, environ=other.environment())
        self.assertEqual(repeat["status"], "outside-scope")
        self.assertIn(OTHER_MASTER.key, repeat["detail"])

    def test_a_reserved_agent_cannot_be_retired_during_its_first_launch(self) -> None:
        request = self.request("manager")

        def while_creating(command, payload):
            if command != "agent-create":
                return
            before = self.receipt(request)
            self.assertEqual(before["status"], "starting")
            self.assertEqual(before["agentId"], payload["agentId"])
            self.assert_retirement_waits_for_launch(
                request, before, expect_absent="prompt" not in payload
            )

        self.runtime.observer = while_creating
        try:
            self.assertEqual(self.dispatch(request)[1]["status"], "running")
        finally:
            self.runtime.observer = None
        self.assert_confirmed_retirement_survives_replay(request)

    def test_a_reserved_agent_cannot_be_retired_before_an_uncertain_launch_replays(self) -> None:
        request = self.request("manager")
        self.runtime.fail("agent-create", "paseo_bridge_timeout")
        self.assertEqual(self.dispatch(request)[1]["status"], "unknown")
        before = self.receipt(request)
        self.assert_retirement_waits_for_launch(request, before)
        self.assertEqual(self.dispatch(request)[1]["status"], "running")
        self.assertEqual(self.receipt(request)["agentId"], before["agentId"])
        self.assert_confirmed_retirement_survives_replay(request)

    def assert_retirement_waits_for_launch(
        self, request, before, *, expect_absent: bool = True
    ) -> None:
        agent_id = before["agentId"]
        if expect_absent:
            self.assertNotIn(agent_id, self.runtime.agents)
        result = retire(self, self.architect, agent_id, role="manager")
        self.assertEqual((result["ok"], result["status"]), (False, "launch-unresolved"))
        self.assertIn(str(request.request_id), result["detail"])
        self.assertEqual(self.receipt(request), before)
        self.assertFalse(any(command == "agent-archive" for command, _ in self.runtime.calls))

    def assert_confirmed_retirement_survives_replay(self, request) -> None:
        agent_id = self.receipt(request)["agentId"]
        self.runtime.agents[agent_id]["status"] = "idle"
        self.assertEqual(
            retire(self, self.architect, agent_id, role="manager")["status"], "retired"
        )
        retired_by = self.receipt(request)["retiredBy"]
        self.assertEqual(retired_by["callerAgentId"], self.architect.agent_id)
        calls = self.runtime.launch_calls()
        self.assertEqual(self.dispatch(request)[1]["status"], "stopped")
        self.assertEqual(self.runtime.launch_calls(), calls)
        self.assertTrue(self.runtime.agents[agent_id]["archivedAt"])
        self.assertEqual(self.receipt(request)["retiredBy"], retired_by)

    @unittest.skipUnless(shutil.which("node"), "the bridge script needs Node.js")
    def test_a_confirmed_archive_refusal_leaves_future_host_archive_attribution(self) -> None:
        request, agent_id = self.started("worker", status="idle")
        bridge = test_paseo_bridge_agents.AgentCommandScriptTests()
        bridge.setUp()
        self.addCleanup(bridge.doCleanups)

        def refuse_archive(_config, command, payload):
            return bridge.call(
                command,
                payload,
                agents={agent_id: dict(self.runtime.agents[agent_id])},
                archiveError="The host could not archive.",
            )

        caller = binding("manager")
        with patch.object(role_retire, "bridge_call", side_effect=refuse_archive):
            answer = retire(self, caller, agent_id)
        self.assertEqual(bridge.recorded(), [{"via": "archive", "id": agent_id}])
        self.assertEqual((answer["ok"], answer["status"]), (False, "archive-refused"))
        self.assertNotIn("retireIntent", self.receipt(request))
        self.assertNotIn("retiredBy", self.receipt(request))
        self.assertFalse(self.runtime.agents[agent_id]["archivedAt"])
        self.assertTrue(self.message(caller, role="worker", **selection_of("worker"))["ok"])

        held_id = self.hold_fixture_message(self.receipt_path(request), agent_id, caller)
        self.runtime.agents[agent_id]["archivedAt"] = "2026-10-02T00:59:00.000Z"
        self.assertEqual(self.refresh(request)["status"], "stopped")
        after = self.receipt(request)
        self.assertEqual(
            (after["retiredBy"]["caller"], after["retiredBy"]["reason"]), ("host", "host archive")
        )
        self.assertNotIn("retireIntent", after)
        self.assertEqual(waiting_total(after["workspace"]["path"], agent_id), 0)
        self.assertEqual([line["messageId"] for line in after["heldMessageLines"]], [held_id])

    def test_an_uncertain_archive_keeps_its_original_attribution_for_retry(self) -> None:
        request, agent_id = self.started("worker", status="idle")
        self.runtime.fail("agent-archive", "paseo_daemon_unreachable", after_effect=True)
        caller = binding("manager")
        answer = retire(self, caller, agent_id)
        self.assertEqual((answer["ok"], answer["status"]), (False, "host-unreachable"))
        intent = self.receipt(request)["retireIntent"]
        self.assertTrue(self.runtime.agents[agent_id]["archivedAt"])
        self.refresh(request)
        self.assertEqual(self.receipt(request)["retireIntent"], intent)
        self.assertNotIn("retiredBy", self.receipt(request))
        archive_calls = [call for call in self.runtime.calls if call[0] == "agent-archive"]
        self.assertEqual(retire(self, caller, agent_id)["status"], "retired")
        self.assertEqual(
            [call for call in self.runtime.calls if call[0] == "agent-archive"], archive_calls
        )
        after = self.receipt(request)
        self.assertEqual(after["retiredBy"]["callerAgentId"], caller.agent_id)
        self.assertEqual(after["retiredBy"]["at"], intent["at"])
        self.assertNotIn("retireIntent", after)

    def test_a_failed_record_reports_truthfully_and_retry_converges(self) -> None:
        manager = binding("manager")
        started = self.start(manager, "worker")
        worker = started["agentId"]
        launched = self.request("worker", uuid.UUID(started["requestId"]))
        self.runtime.agents[worker].update(status="idle")
        workspace = receipt_of(self)["workspace"]["path"]
        held = hold_message(
            workspace, worker, HeldMessage("Manager", manager.agent_id, "held evidence", "busy")
        )
        replace = role_receipt_files._replace_receipt_bytes

        def fail_settlement(path, value):
            if value.get("retiredBy"):
                raise OSError("probe disk full")
            replace(path, value)

        def archive_while_refresh_reads(config, command, payload):
            result = self.runtime(config, command, payload)
            self.assertEqual(command, "agent-archive")
            self.refresh(launched)
            current = receipt_of(self)
            self.assertNotIn("retiredBy", current)
            self.assertIn("retireIntent", current)
            return result

        request = RetireChildRequest(agent_id=worker, role="worker", reason=REASON)
        with (
            patch.object(role_retire, "bridge_call", side_effect=archive_while_refresh_reads),
            patch.object(role_receipt_files, "_replace_receipt_bytes", side_effect=fail_settlement),
        ):
            result = retire_child_payload(self.config, request, environ=manager.environment())

        self.assertEqual((result["ok"], result["status"]), (False, "retire-record-failed"))
        self.assertTrue(self.runtime.agents[worker]["archivedAt"])
        self.assertNotIn("retiredBy", receipt_of(self))
        self.assertIn("could not be recorded", result["detail"])
        self.assertEqual(
            result["retireIntent"],
            {
                "caller": f"manager agent {manager.agent_id}",
                "callerAgentId": manager.agent_id,
                "reason": REASON,
                "at": receipt_of(self)["retireIntent"]["at"],
            },
        )
        # The identical retry converges with the original caller and reason.
        again = retire(self, manager, worker)
        self.assertEqual((again["ok"], again["status"]), (True, "retired"))
        record = receipt_of(self)
        self.assertNotIn("retireIntent", record)
        self.assertEqual(waiting_total(workspace, worker), 0)
        self.assertEqual(
            [line["messageId"] for line in record["heldMessageLines"]],
            [held["heldMessageId"]],
        )
        self.assertEqual(
            {key: record["retiredBy"][key] for key in ("caller", "callerAgentId", "reason")},
            {
                "caller": f"manager agent {manager.agent_id}",
                "callerAgentId": manager.agent_id,
                "reason": REASON,
            },
        )

    def hold_fixture_message(self, path, agent_id, sender) -> str:
        record = _read_receipt(path)
        assert record is not None
        workspace = record["workspace"]["path"]
        message = HeldMessage(sender.role, sender.agent_id, "held evidence", "busy")
        return str(hold_message(workspace, agent_id, message)["heldMessageId"])

    def assert_retirement_effects(self, path, agent_id, caller, held_id) -> None:
        record = _read_receipt(path)
        assert record is not None
        self.assertEqual(record["retiredBy"]["callerAgentId"], caller.agent_id)
        self.assertEqual(record["retiredBy"]["reason"], REASON)
        self.assertTrue(record["retiredBy"]["at"])
        self.assertTrue(self.runtime.agents[agent_id]["archivedAt"])
        self.assertEqual(waiting_total(record["workspace"]["path"], agent_id), 0)
        self.assertEqual(record["heldMessageLines"][-1]["messageId"], held_id)
        self.assertEqual(record["heldMessageLines"][-1]["how"], "not-delivered-archived-or-gone")
        self.refusal(self.message(caller, agent_id=agent_id), "recipient-archived")

    def assert_architect_retires(self, role) -> None:
        request, agent_id = self.started(role, status="idle")
        path = self.receipt_path(request)
        held_id = self.hold_fixture_message(path, agent_id, self.architect)

        result = retire_child_payload(
            self.config,
            RetireChildRequest(agent_id=agent_id, role=role, reason=REASON),
            environ=self.architect.environment(),
        )

        self.assertEqual((result["status"], result["role"]), ("retired", role))
        self.assert_retirement_effects(path, agent_id, self.architect, held_id)

    def test_the_architect_retires_an_architect(self) -> None:
        self.assert_architect_retires("architect")

    def test_the_architect_retires_an_investigator(self) -> None:
        self.assert_architect_retires("investigator")

    def test_retirement_canonicalizes_an_existing_system_specialist_receipt(self) -> None:
        request, agent_id = self.started("investigator", status="idle")
        current = self.receipt_path(request)
        previous = current.parent.parent.with_name("system-specialist") / "sessions" / current.name
        previous.parent.mkdir(parents=True)
        record = self.receipt(request)
        record["role"] = record["selection"]["role"] = "system-specialist"
        current.rename(previous)
        previous.write_text(json.dumps(record), encoding="utf-8")
        held_id = self.hold_fixture_message(previous, agent_id, self.architect)

        result = retire_child_payload(
            self.config,
            RetireChildRequest(agent_id=agent_id, role="investigator", reason=REASON),
            environ=self.architect.environment(),
        )

        self.assertEqual((result["status"], result["role"]), ("retired", "investigator"))
        self.assert_retirement_effects(previous, agent_id, self.architect, held_id)
        self.assertFalse(current.exists())
        persisted = json.loads(previous.read_text(encoding="utf-8"))
        self.assertEqual(persisted["selection"]["role"], "system-specialist")

    def test_the_architect_retires_an_orchestrator(self) -> None:
        self.assert_architect_retires("orchestrator")

    def test_the_architect_retires_a_manager(self) -> None:
        self.assert_architect_retires("manager")

    def test_the_architect_retires_a_worker(self) -> None:
        self.assert_architect_retires("worker")

    def test_the_architect_retires_a_reviewer(self) -> None:
        self.assert_architect_retires("reviewer")

    def test_the_architect_retires_a_curator(self) -> None:
        self.assert_architect_retires("curator")

    def test_the_architect_retires_bootstrap(self) -> None:
        with (
            patch.dict(ROLE_REFS, bootstrap={}),
            patch.object(role_launch_first_run, "prepare_first_run"),
        ):
            self.assert_architect_retires("bootstrap")


if __name__ == "__main__":
    unittest.main()
