"""The starter retires the agent in its leaf seat (MIK-R100 rule 8, evidence 3)."""

import shutil
import unittest
import uuid
from unittest.mock import patch

from agents_remember.cli import role_launch_receipts as receipts
from agents_remember.cli.role_launch_receipts import _read_receipt, _receipt_path
from agents_remember.cli.role_retire import retire_native_seat
from agents_remember.mcp.tools.structural_agent import retire_child_payload
from agents_remember.models.role_launcher import RoleSelection
from agents_remember.models.structural.agent import RetireChildRequest
from test_paseo_launch import LEAF_REF, MASTER_REF, SPRINT_REF
from test_paseo_role_tools import RoleToolsTestCase, binding, selection_of

REASON = "the seat is replaced"


def retire(
    test: RoleToolsTestCase,
    caller,
    agent_id: str | None,
    reason: str = REASON,
    **fields,
) -> dict:
    """One native retirement call by the given caller for the named agent."""

    request = RetireChildRequest(
        task_document_ref=LEAF_REF,
        role="worker",
        reason=reason,
        agent_id=agent_id,
        **fields,
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
    """Rule 8: only the starter retires the seat's agent, without giving it a turn."""

    def receipt_path_for_worker(self):
        """The worker seat's current receipt path for this line's leaf selection."""

        selection = RoleSelection(
            role="worker",
            sprintDocumentRef=SPRINT_REF,
            masterDocumentRef=MASTER_REF,
            taskDocumentRef=LEAF_REF,
        )
        return _receipt_path(self.config, selection)

    def test_the_starter_retires_its_seat_agent(self) -> None:
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
            {
                key: record["retiredBy"][key]
                for key in ("caller", "callerAgentId", "reason")
            },
            {
                "caller": f"manager agent {manager.agent_id}",
                "callerAgentId": manager.agent_id,
                "reason": REASON,
            },
        )
        self.assertTrue(record["retiredBy"]["at"])
        # A repeat changes nothing and says so.
        again = retire(self, manager, worker)
        self.assertEqual(
            (again["ok"], again["status"]), (True, "already-retired")
        )
        self.assertIn("nothing changed", again["detail"])

    def test_another_role_agent_is_refused_naming_the_starter(self) -> None:
        manager = binding("manager")
        worker = self.start(manager, "worker")["agentId"]
        other = binding("manager")

        result = retire(self, other, worker)
        self.assertEqual((result["ok"], result["status"]), (False, "not-starter"))
        self.assertIn(manager.agent_id, result["detail"])
        self.assertIn(other.agent_id, result["detail"])
        # Nothing was archived.
        self.assertFalse(self.runtime.agents[worker]["archivedAt"])

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
        self.assertEqual(
            retire(self, manager, worker)["status"], "retired"
        )

        by_id = self.message(manager, agent_id=worker)
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

        other = binding("manager")
        result = retire(self, other, worker)

        self.assertEqual((result["ok"], result["status"]), (False, "not-starter"))
        self.assertIn(manager.agent_id, result["detail"])

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
        duplicate = retire_child_payload(
            self.config, request, environ=manager.environment()
        )
        self.assertEqual(duplicate["status"], "ambiguous-agent")
        self.assertIn(worker, duplicate["detail"])

        (path.parent / "duplicate.json").unlink()
        self.assertEqual(
            retire_child_payload(self.config, request, environ=manager.environment())[
                "status"
            ],
            "retired",
        )
        other = binding("manager")
        repeat = retire_child_payload(
            self.config, request, environ=other.environment()
        )
        self.assertEqual(repeat["status"], "not-starter")
        self.assertIn(manager.agent_id, repeat["detail"])

    def test_a_failed_record_reports_truthfully_and_retry_converges(self) -> None:
        manager = binding("manager")
        worker = self.start(manager, "worker")["agentId"]
        self.runtime.agents[worker].update(status="idle")
        with patch.object(
            receipts, "_write_receipt", side_effect=OSError("probe disk full")
        ):
            result = retire(self, manager, worker)

        self.assertEqual((result["ok"], result["status"]), (False, "retire-record-failed"))
        self.assertTrue(self.runtime.agents[worker]["archivedAt"])
        self.assertNotIn("retiredBy", receipt_of(self))
        self.assertIn("does not name the", result["detail"])
        self.assertEqual(
            result["retireIntent"],
            {
                "caller": f"manager agent {manager.agent_id}",
                "callerAgentId": manager.agent_id,
                "reason": REASON,
            },
        )
        # The identical retry converges with the original caller and reason.
        again = retire(self, manager, worker)
        self.assertEqual((again["ok"], again["status"]), (True, "already-retired"))
        record = receipt_of(self)
        self.assertEqual(
            {
                key: record["retiredBy"][key]
                for key in ("caller", "callerAgentId", "reason")
            },
            {
                "caller": f"manager agent {manager.agent_id}",
                "callerAgentId": manager.agent_id,
                "reason": REASON,
            },
        )


if __name__ == "__main__":
    unittest.main()
