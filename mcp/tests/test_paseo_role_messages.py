"""PNT-R06: role messaging by role agents, against a fake bridge."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import unittest
import uuid
from pathlib import Path
from typing import Any, get_args
from unittest.mock import patch

from agents_remember.cli import (
    paseo_role_recipients,
    paseo_role_tools,
    role_held_messages,
    role_launch_receipts,
)
from agents_remember.cli.paseo_role_tools import send_role_message, sender_line, starting_agent
from agents_remember.cli.role_held_messages import HeldMessage
from agents_remember.models.role_agents import (
    RoleMessageCall,
    RoleMessageRefusal,
    RoleMessageResponse,
    RoleMessageStatus,
)
from agents_remember.observer.host_activity import HOST_ACTIVITY_FILE
from agents_remember.serving.projections.host_activity import activity_directory
from agents_remember.worktrees import role_reports
from paseo_role_tools_test_support import ARCHIVED_AT, TURN
from test_paseo_launch import runtime_config
from test_paseo_role_tools import (
    OTHER_SPRINT,
    RoleToolsTestCase,
    binding,
    selection_of,
)
from test_paseo_status import NO_ROLLOUT


class RoleMessageTests(RoleToolsTestCase):
    def test_r71_messages_deliver_despite_collection_errors_and_isolate_agents(self) -> None:
        worker_request, worker = self.started("worker", status="idle")
        reviewer_request, reviewer = self.started("reviewer", status="idle")
        worker_record = self.receipt(worker_request)
        reviewer_record = self.receipt(reviewer_request)
        local = Path(worker_record["workspace"]["taskReportAccessRoot"])
        other = Path(reviewer_record["workspace"]["taskReportAccessRoot"])
        self.assertNotEqual(local, other)
        local.mkdir(parents=True, exist_ok=True)
        other.mkdir(parents=True, exist_ok=True)
        (other / "report.md").write_text("reviewer result")
        for failure in ("link", "budget"):
            with self.subTest(failure=failure):
                path = local / "problem"
                if failure == "link":
                    path.symlink_to(self.root)
                else:
                    path.write_text("over budget")
                self.runtime.agents[reviewer]["status"] = "idle"
                with patch.object(role_reports, "MAX_COLLECTION_BYTES", 1):
                    result = self.message(binding("worker", worker), agent_id=reviewer)
                self.assertEqual(result["status"], "accepted")
                self.assertEqual(result["reportCollection"]["state"], "failed")
                self.assertIn(
                    "link" if failure == "link" else "budget", result["reportCollection"]["detail"]
                )
                self.assertTrue(self.received(reviewer))
                self.assertEqual(
                    self.receipt(worker_request)["reportCollection"]["state"], "failed"
                )
                self.runtime.agents[worker]["status"] = "idle"
                unaffected = self.message(binding("reviewer", reviewer), agent_id=worker)
                self.assertEqual(unaffected["status"], "accepted")
                canonical = Path(reviewer_record["workspace"]["taskReportRoot"])
                self.assertEqual((canonical / "report.md").read_text(), "reviewer result")
                self.runtime.agents[worker]["status"] = "idle"
                with patch.object(
                    role_reports,
                    "_file_digest",
                    side_effect=AssertionError(
                        "a repeated message must skip unchanged report bytes"
                    ),
                ):
                    self.assertEqual(
                        self.message(binding("reviewer", reviewer), agent_id=worker)["status"],
                        "accepted",
                    )
                path.unlink()

    def test_the_delivered_text_opens_with_the_line_that_names_the_sender(self) -> None:
        _request, worker = self.started(status="idle")
        before = sorted(path.as_posix() for path in self.root.rglob("*") if path.is_file())
        saved = {path: Path(path).read_bytes() for path in before}
        senders = {
            "architect": (self.architect, "Projects"),
            "orchestrator": (binding("orchestrator"), "SPRINT"),
            "manager": (binding("manager"), "MASTER"),
            "reviewer": (binding("reviewer"), "01_LEAF"),
        }
        for role, (caller, subject) in senders.items():
            with self.subTest(sender=role):
                self.runtime.agents[worker].update(status="idle", received=[])
                self.runtime.calls.clear()
                line = f"From {role} · {subject} · agent {caller.agent_id}"

                result = self.message(caller, "report your plan", agent_id=worker)

                self.assertEqual(self.received(worker), [f"{line}\nreport your plan"])
                message_file = result.pop("messageFile")
                message_digest = result.pop("messageFileSha256")
                self.assertEqual(
                    result,
                    {
                        "ok": True,
                        "status": "accepted",
                        "detail": "The recipient started a turn with the message.",
                        "recipientAgentId": worker,
                        "senderLine": line,
                        "taken": "started",
                    },
                )
                self.assertTrue(message_file.endswith(".md"))
                self.assertEqual(
                    hashlib.sha256(Path(message_file).read_bytes()).hexdigest(),
                    message_digest,
                )
                self.assertEqual(sender_line(starting_agent(self.config, caller)), line)
                # Accepted means the host took the message; nothing waits for the turn.
                self.assertEqual([command for command, _p in self.runtime.calls], ["agent-send"])
        with self.subTest("one file per message, and no receipt changes"):
            after = sorted(path.as_posix() for path in self.root.rglob("*") if path.is_file())
            added = [path for path in after if path not in before]
            # One file per accepted message, named by time, sender and recipient.
            self.assertEqual(len(added), 4)
            for path in added:
                name = Path(path).name
                self.assertRegex(
                    name,
                    r"^\d{8}T\d{6}Z_[a-z]+-[0-9a-f]{8}_to_worker-[0-9a-f]{8}\.md$",
                )
                content = Path(path).read_text()
                self.assertIn("report your plan", content)
                self.assertIn("messageId:", content)
            self.assertEqual(
                {path: Path(path).read_bytes() for path in after if path in saved}, saved
            )
        with self.subTest("a task document that is gone still names the work by its reference"):
            caller = binding("orchestrator", sprint_ref=OTHER_SPRINT)
            self.assertEqual(
                sender_line(starting_agent(self.config, caller)),
                f"From orchestrator · {OTHER_SPRINT.key} · agent {caller.agent_id}",
            )

    def test_a_role_resolves_through_the_receipts_to_its_live_agent(self) -> None:
        to_worker = {"role": "worker", **selection_of("worker")}
        first, old_agent = self.started(status="idle")
        with self.subTest("the open execution"):
            self.runtime.calls.clear()
            result = self.message(self.architect, **to_worker)
            self.assertEqual(
                (result["status"], result["recipientAgentId"]), ("accepted", old_agent)
            )
            self.assertEqual(
                [command for command, _p in self.runtime.calls], ["agent-state", "agent-send"]
            )
            self.assertEqual(self.runtime.calls[0][1], {"agentId": old_agent})
        with self.subTest("a completed holder still holds the seat until archived"):
            self.close_execution(first, "completed")
            # A finished turn does not free the seat (MIK-R100 rule 9): the new
            # request id is refused naming the holder, and nothing is archived.
            second = self.request("worker")
            refusal = self.refused(second)
            self.assertEqual(refusal.status_code, 409)
            self.assertIn(old_agent, str(refusal.detail))
            self.assertIn("held by agent", str(refusal.detail))
            self.assertEqual(self.receipt(second)["agentId"], old_agent)
            self.runtime.agents[old_agent].update(status="idle", received=[])
            result = self.message(self.architect, **to_worker)
            self.assertEqual(
                (result["status"], result["recipientAgentId"]), ("accepted", old_agent)
            )
        with self.subTest("an archived agent is refused and stays archived"):
            self.runtime.agents[old_agent].update(
                status="closed", archivedAt="2026-10-02T02:00:00.000Z", received=[]
            )
            self.runtime.calls.clear()
            for address in (to_worker, {"agent_id": old_agent}):
                result = self.refusal(self.message(self.architect, **address), "recipient-archived")
                self.assertEqual(result["recipientAgentId"], old_agent)
            self.assertNotIn("agent-resume", [command for command, _p in self.runtime.calls])
            self.assertEqual(
                self.runtime.agents[old_agent]["archivedAt"], "2026-10-02T02:00:00.000Z"
            )
            self.assertEqual(self.received(old_agent), [])
        with self.subTest("a selection without an execution"):
            curator = {"role": "curator", **selection_of("curator")}
            result = self.refusal(self.message(self.architect, **curator), "recipient-not-found")
            self.assertIn("No live curator agent", result["detail"])
        with self.subTest("a selection that cannot be resolved"):
            self.replace(
                paseo_role_recipients,
                "resolve_role_launch_context",
                side_effect=ValueError("worker requires a canonical task selection."),
            )
            result = self.refusal(
                self.message(self.architect, role="worker"), "recipient-not-found"
            )
            self.assertIn("worker requires a canonical task selection.", result["detail"])

    def test_several_live_agents_of_a_role_are_refused_with_their_ids_never_chosen_among(
        self,
    ) -> None:
        _first, one = self.started("architect", status="idle")
        _second, two = self.started("architect", status="idle")
        _third, gone = self.started(
            "architect", status="closed", archivedAt="2026-10-02T02:00:00.000Z"
        )
        worker = binding("worker")

        result = self.refusal(self.message(worker, role="architect"), "recipient-ambiguous")

        self.assertEqual(sorted(result["candidateAgentIds"]), sorted([one, two]))
        self.assertIn(one, result["detail"])
        self.assertIn(two, result["detail"])
        self.assertNotIn(gone, result["detail"])
        self.assertEqual([self.received(agent) for agent in (one, two, gone)], [[], [], []])
        with self.subTest("addressed by agent id it is delivered"):
            result = self.message(worker, agent_id=two)
            self.assertEqual((result["status"], result["recipientAgentId"]), ("accepted", two))
            self.assertEqual(self.received(one), [])
        with self.subTest("one live agent of the role is the recipient"):
            self.runtime.agents[one].update(status="closed", archivedAt="2026-10-02T03:00:00.000Z")
            self.runtime.agents[two]["status"] = "idle"
            result = self.message(worker, role="architect")
            self.assertEqual((result["status"], result["recipientAgentId"]), ("accepted", two))
        with self.subTest("the host cannot be asked which agents are live"):
            self.runtime.fail("agent-state", "paseo_daemon_unreachable", "connection refused")
            self.refusal(self.message(worker, role="architect"), "host-unreachable")

    def test_the_caller_is_no_candidate_of_its_own_role_address(self) -> None:
        _first, one = self.started("architect", status="idle")
        _second, two = self.started("architect", status="idle")
        caller = binding("architect", agent_id=one)
        self.runtime.calls.clear()

        result = self.message(caller, role="architect")

        # The other architect is the one recipient; the caller's own agent is not even read.
        self.assertEqual((result["status"], result["recipientAgentId"]), ("accepted", two))
        self.assertEqual(self.asked_about(), [two])
        self.assertEqual((self.received(one), len(self.received(two))), ([], 1))
        with self.subTest("alone in its role, it has no recipient"):
            self.runtime.agents[two].update(status="closed", archivedAt=ARCHIVED_AT)
            _third, gone = self.started("architect", status="idle")
            self.runtime.agents.pop(gone)
            result = self.refusal(self.message(caller, role="architect"), "recipient-archived")
            self.assertEqual(result["recipientAgentId"], two)
            self.runtime.agents.pop(two)
            result = self.refusal(self.message(caller, role="architect"), "recipient-not-found")
            self.assertEqual(
                result["detail"],
                "No live architect agent other than the calling agent is recorded for this "
                "selection.",
            )
        with self.subTest("by its own agent id it is still told that it names itself"):
            result = self.refusal(self.message(caller, agent_id=one), "recipient-not-found")
            self.assertIn("is the calling agent itself", result["detail"])

    def test_a_recipient_whose_start_has_not_finished_is_held(self) -> None:
        # Until its start has answered, an agent may not have its first message yet.
        # The product holds the message and delivers it once the start has answered running.
        request, worker = self.started(status="idle")
        saved = self.receipt(request)
        for status in ("starting", "unknown"):
            for address in ({"agent_id": worker}, {"role": "worker", **selection_of("worker")}):
                with self.subTest(status=status, by=sorted(address)[0]):
                    role_launch_receipts._write_receipt(
                        self.receipt_path(request), {**saved, "status": status}
                    )
                    self.runtime.calls.clear()
                    result = self.message(self.architect, **address)
                    self.assertEqual(result["status"], "held")
                    self.assertIn("start has not finished", result["detail"])
                    self.assertIn("nothing has to be sent again", result["detail"])
                    self.assertEqual(result["heldReason"], "start-unfinished")
                    self.assertEqual(result["recipientAgentId"], worker)
                    self.assertNotIn("permission", result["detail"])
                    # Nothing was sent, resumed or waited for.
                    self.assertEqual(
                        {command for command, _p in self.runtime.calls} - {"agent-state"}, set()
                    )
                    self.assertEqual(self.received(worker), [])
        with self.subTest("once the start has answered, the held messages deliver"):
            role_launch_receipts._write_receipt(self.receipt_path(request), saved)
            result = self.message(self.architect, agent_id=worker)
            self.assertEqual(result["status"], "accepted")
            # Both held messages and the new one go in one delivery, in order.
            self.assertEqual(len(self.received(worker)), 1)

    def asked_about(self) -> list[str]:
        return [
            payload["agentId"]
            for command, payload in self.runtime.calls
            if command == "agent-state"
        ]

    def test_every_execution_of_a_taskless_role_that_is_not_stopped_is_asked_about(self) -> None:
        architects = [self.started("architect", status="idle") for _ in range(13)]
        oldest, newest = architects[0][1], architects[-1][1]
        for _request, agent_id in architects[1:-1]:
            self.runtime.agents[agent_id].update(status="closed", archivedAt=ARCHIVED_AT)
        worker = binding("worker")
        self.runtime.calls.clear()

        result = self.refusal(self.message(worker, role="architect"), "recipient-ambiguous")

        # The oldest of thirteen is live as the newest is: neither is chosen.
        self.assertEqual(sorted(result["candidateAgentIds"]), sorted([oldest, newest]))
        self.assertEqual(
            sorted(self.asked_about()), sorted(agent for _request, agent in architects)
        )
        self.assertNotIn("the list was cut", result["detail"])
        self.assertEqual((self.received(oldest), self.received(newest)), ([], []))
        with self.subTest("an execution already stopped or rejected is not asked about"):
            for request, _agent in architects[1:-1]:
                self.assertEqual(self.refresh(request)["status"], "stopped")
            self.runtime.fail(
                "agent-create", "paseo_call_failed", "Provider codex is not configured"
            )
            self.assertEqual(self.dispatch(self.request("architect"))[1]["status"], "rejected")
            self.runtime.calls.clear()
            result = self.refusal(self.message(worker, role="architect"), "recipient-ambiguous")
            self.assertEqual(sorted(self.asked_about()), sorted([oldest, newest]))
            self.assertEqual(sorted(result["candidateAgentIds"]), sorted([oldest, newest]))
        with self.subTest("the one live agent among them is the recipient"):
            self.runtime.agents[newest].update(status="closed", archivedAt=ARCHIVED_AT)
            result = self.message(worker, role="architect")
            self.assertEqual((result["status"], result["recipientAgentId"]), ("accepted", oldest))

    def test_more_executions_than_are_asked_about_are_refused_and_the_cut_is_said(self) -> None:
        architects = [self.started("architect", status="idle") for _ in range(25)]
        oldest, newest = architects[0][1], architects[-1][1]
        for _request, agent_id in architects[1:]:
            self.runtime.agents[agent_id].update(status="closed", archivedAt=ARCHIVED_AT)
        worker = binding("worker")
        self.runtime.calls.clear()

        result = self.refusal(self.message(worker, role="architect"), "recipient-ambiguous")

        self.assertIn(
            "25 architect executions are not known to be stopped; only the 24 most recent were "
            "asked about, so the list was cut. Of those, 0 are live. The tool never picks one.",
            result["detail"],
        )
        self.assertEqual(result["candidateAgentIds"], [])
        self.assertEqual(len(self.asked_about()), 24)
        self.assertNotIn(oldest, self.asked_about())
        with self.subTest("one live agent among those asked about is not chosen either"):
            self.runtime.agents[newest].update(status="idle", archivedAt=None)
            result = self.refusal(self.message(worker, role="architect"), "recipient-ambiguous")
            self.assertEqual(result["candidateAgentIds"], [newest])
            self.assertIn(f"Of those, 1 are live: {newest}.", result["detail"])
            self.assertEqual((self.received(oldest), self.received(newest)), ([], []))
        with self.subTest("with one of them known to be stopped, every other is asked about"):
            self.assertEqual(self.refresh(architects[1][0])["status"], "stopped")
            self.runtime.calls.clear()
            result = self.refusal(self.message(worker, role="architect"), "recipient-ambiguous")
            self.assertEqual(sorted(result["candidateAgentIds"]), sorted([oldest, newest]))
            self.assertEqual(len(self.asked_about()), 24)
            self.assertNotIn("the list was cut", result["detail"])

    def test_a_leaf_seat_with_two_live_agents_refuses_by_role(self) -> None:
        # One seat holds one agent (MIK-R100 rule 9): where two live agents hold one
        # leaf seat, the address by role is refused with the candidates. The tool never
        # picks one.
        to_worker = {"role": "worker", **selection_of("worker")}
        agents: list[str] = []
        for _execution in range(3):
            request, agent_id = self.started(status="idle")
            agents.append(agent_id)
            self.close_execution(request, "completed")
            # A finished turn does not free the seat (MIK-R100 rule 9): archive the
            # holder before the next start, as the launch does for a freed seat.
            if _execution < 2:
                self.runtime.agents[agent_id].update(archivedAt=ARCHIVED_AT)
        first, second, current = agents
        # Two earlier executions and the current one; the launches archived the earlier agents.
        self.assertEqual(
            [self.runtime.agents[agent]["archivedAt"] is not None for agent in agents],
            [True, True, False],
        )
        for agent_id in (first, second):
            self.runtime.agents[agent_id].update(status="idle", archivedAt=None, received=[])
        with self.subTest("two live agents in one leaf seat are refused with both ids"):
            self.runtime.calls.clear()
            result = self.refusal(self.message(self.architect, **to_worker), "recipient-ambiguous")
            self.assertEqual(sorted(result["candidateAgentIds"]), sorted(agents))
            self.assertIn("The tool never picks one", result["detail"])
            self.assertEqual(self.received(current), [])
        with self.subTest("one live agent is still the recipient"):
            self.runtime.agents[first].update(archivedAt=ARCHIVED_AT)
            self.runtime.agents[second].update(archivedAt=ARCHIVED_AT)
            self.runtime.calls.clear()
            result = self.message(self.architect, **to_worker)
            self.assertEqual((result["status"], result["recipientAgentId"]), ("accepted", current))
        with self.subTest("a non-leaf seat still takes the newest live agent"):
            launched: list[tuple[Any, str]] = []
            for _execution in range(2):
                manager_request = self.launched("manager", status="idle")
                launched.append((manager_request, self.receipt(manager_request)["agentId"]))
                self.close_execution(manager_request, "completed")
            (_first_request, first), (_second_request, second) = launched
            self.runtime.agents[first].update(status="idle", archivedAt=None, received=[])
            self.runtime.calls.clear()
            result = self.message(self.architect, role="manager", **selection_of("manager"))
            self.assertEqual((result["status"], result["recipientAgentId"]), ("accepted", second))

    def test_only_an_agent_id_of_this_lines_receipts_is_a_recipient(self) -> None:
        _request, worker = self.started(status="idle")
        caller_request, caller_agent = self.started("architect", status="running")
        caller = binding("architect", caller_agent)
        self.runtime.calls.clear()
        not_ids = (
            "architect",
            "Worker · 01_LEAF",
            worker[:8],
            worker.upper(),
            f"{{{worker}}}",
            "",
        )
        for value in not_ids:
            with self.subTest(not_an_id=value):
                result = self.refusal(self.message(caller, agent_id=value), "recipient-not-found")
                self.assertIn("AR agent ids are UUIDs", result["detail"])
        cases = {
            "an id no receipt records": (
                {"agent_id": str(uuid.uuid4())},
                "No role execution of this AR line",
            ),
            "an id and a role together": (
                {"agent_id": worker, "role": "worker"},
                "not both and not neither",
            ),
            # The refusal names the arguments that address a recipient: a call that named it
            # under another argument name arrives here without one.
            "neither an id nor a role": (
                {},
                "Name the recipient either with agent_id, or with role plus the task references "
                "its class requires (sprint_document_ref, master_document_ref, "
                "task_document_ref); not both and not neither. No other argument names a "
                "recipient.",
            ),
            "the caller itself": ({"agent_id": caller_agent}, "is the calling agent itself"),
        }
        for label, (address, said) in cases.items():
            with self.subTest(label):
                result = self.refusal(self.message(caller, **address), "recipient-not-found")
                self.assertIn(said, result["detail"])
        # None of these reached the host: the runtime resolves prefixes and titles.
        self.assertEqual(self.runtime.calls, [])
        self.assertEqual(self.received(worker), [])
        with self.subTest("an agent the host no longer has"):
            del self.runtime.agents[worker]
            self.refusal(self.message(caller, agent_id=worker), "recipient-not-found")
        del caller_request

    def test_a_running_turn_is_steered_or_held_by_its_provider_and_never_cut_off(self) -> None:
        """MIK-R171@v2 rule 2: a steer only into the turn of a provider known to take one."""

        for role, steers, expected in (("worker", True, "steered"), ("reviewer", False, "held")):
            with self.subTest(f"the provider takes a steer: {steers}"):
                _request, worker = self.started(role, status="running")
                self.runtime.agents[worker].update(steers=steers, status="running", received=[])
                result = self.message(self.architect, agent_id=worker, text="held-in-turn")
                self.assertEqual(result["status"], expected if expected == "held" else "accepted")
                self.assertNotIn("cancelled", json.dumps(result))
                if expected == "steered":
                    self.assertEqual(result["taken"], "steered")
                    self.assertIn("nothing was replaced", result["detail"])
                    self.assertEqual(len(self.received(worker)), 1)
                    continue
                self.assertEqual(result["heldReason"], "recipient-in-turn")
                self.assertIn("nothing has to be sent again", result["detail"])
                self.assertEqual(
                    (self.received(worker), self.runtime.agents[worker]["status"]),
                    ([], "running"),
                )

    def test_a_refused_send_is_held_and_no_answer_names_a_cancellation(self) -> None:
        """MIK-R171 rules 2 and 3: no answer names a cancellation, and a refusal is held."""

        _request, worker = self.started(status="idle")
        with self.subTest("the held message and the new one go in one delivery, in order"):
            self.runtime.agents[worker].update(status="running", received=[])
            self.message(self.architect, agent_id=worker, text="held-in-turn")
            self.runtime.agents[worker].update(status="idle", received=[])
            result = self.message(self.architect, agent_id=worker, text="after-turn")
            self.assertEqual(result["status"], "accepted")
            self.assertEqual(result["taken"], "started")
            self.assertNotIn("cancelled", json.dumps(result))
            (combined,) = self.received(worker)
            self.assertTrue(combined.index("held-in-turn") < combined.index("after-turn"))
            self.assertEqual(combined.count("after-turn"), 1)
        with self.subTest("the host does not accept the message"):
            self.runtime.agents[worker].update(status="idle", received=[])
            self.runtime.calls.clear()
            for _attempt in range(4):
                self.runtime.fail("agent-send", "paseo_call_failed", "Active turn changed")
            result = self.message(self.architect, agent_id=worker, text="still-held")
            self.assertEqual(result["status"], "held")
            self.assertIn("Active turn changed", result["detail"])
            self.assertNotIn("cancelled", json.dumps(result))
            self.assertEqual(
                [command for command, _p in self.runtime.calls],
                ["agent-send", "agent-send", "agent-state", "agent-send", "agent-send"],
            )
        with self.subTest("a turn that changed under the send is tried once more"):
            self.runtime.agents[worker].update(status="idle", received=[])
            self.runtime.calls.clear()
            self.runtime.fail("agent-send", "paseo_call_failed", "Active turn changed")
            self.assertEqual(self.message(self.architect, agent_id=worker)["status"], "accepted")
            self.assertEqual(len(self.received(worker)), 1)
            # Both attempts carry the one id of the delivery: the host records it once.
            attempts = [
                payload for command, payload in self.runtime.calls if command == "agent-send"
            ]
            self.assertEqual(len(attempts), 2)
            self.assertEqual(attempts[0], attempts[1])
            self.assertEqual(self.message_ids(worker), [attempts[0]["messageId"]])
        with self.subTest("each message has an id of its own"):
            _request, worker = self.started("reviewer", status="idle")
            self.message(self.architect, agent_id=worker)
            self.runtime.agents[worker]["status"] = "idle"
            self.message(self.architect, agent_id=worker)
            first, second = self.message_ids(worker)
            self.assertNotEqual(first, second)
            self.assertEqual(str(uuid.UUID(second)), second)

    def test_a_recipient_that_waits_for_a_permission_decision_is_held_not_sent_to(self) -> None:
        _request, worker = self.started(status="running")
        pending = [{"id": "perm-1", "name": "Bash", "kind": "tool"}]
        self.runtime.agents[worker].update(pendingPermissions=pending)
        self.runtime.calls.clear()

        result = self.message(self.architect, agent_id=worker, text="first")

        # A message would deny what the developer was asked: it is not sent to the host.
        # The product holds it and says so, naming the permission; it does not say accepted.
        self.assertEqual(result["status"], "held")
        self.assertIn("waits for a permission decision (Bash)", result["detail"])
        self.assertNotIn("nextAction", result)
        self.assertEqual(
            (result["recipientAgentId"], result["permission"], result["heldReason"]),
            (worker, "Bash", "permission-pending"),
        )
        self.assertTrue(result["heldMessageId"])
        self.assertEqual((result["waitingMine"], result["waitingTotal"]), (1, 1))
        # The message itself goes to the host once and is refused; the drain's one
        # read changes nothing: it confirms the prompt still waits.
        self.assertEqual(
            [command for command, _p in self.runtime.calls], ["agent-send", "agent-state"]
        )
        self.assertEqual(self.received(worker), [])
        self.assertEqual(self.runtime.agents[worker]["pendingPermissions"], pending)
        with self.subTest("the host names no permission"):
            self.runtime.agents[worker].update(pendingPermissions=[{"id": "perm-2"}])
            result = self.message(self.architect, agent_id=worker, text="second")
            self.assertEqual(result["status"], "held")
            self.assertIn("waits for a permission decision (not supplied)", result["detail"])
            self.assertIsNone(result["permission"])
            self.assertEqual((result["waitingMine"], result["waitingTotal"]), (2, 2))
        with self.subTest("a wait on the held message answers permission-pending"):
            result = self.message(self.architect, agent_id=worker, wait=True, text="third")
            self.assertEqual(result["status"], "permission-pending")
            self.assertEqual(result["permission"], "a tool")
        with self.subTest("once the permission is answered the held messages deliver in order"):
            self.runtime.agents[worker].update(pendingPermissions=[], status="idle")
            result = self.message(self.architect, agent_id=worker, text="fourth")
            self.assertEqual(result["status"], "accepted")
            received = self.received(worker)
            self.assertEqual(len(received), 1)
            self.assertEqual(
                [received[0].count(text) for text in ("first", "second", "third", "fourth")],
                [1, 1, 1, 1],
            )
            self.assertTrue(
                received[0].index("first")
                < received[0].index("second")
                < received[0].index("third")
                < received[0].index("fourth")
            )
        with self.subTest("a recipient in a turn is held, not refused"):
            self.runtime.agents[worker].update(steers=False, status="running", received=[])
            result = self.message(self.architect, agent_id=worker)
            self.assertEqual(result["status"], "held")
            self.assertEqual(result["heldReason"], "recipient-in-turn")
            self.assertIn("nothing has to be sent again", result["detail"])

    def test_a_task_bound_execution_that_says_stopped_is_found_while_its_agent_is_live(
        self,
    ) -> None:
        # The agent's last turn was cancelled: the execution is closed, the agent is not gone.
        request, worker = self.started(status="idle")
        self.assertEqual(self.close_execution(request, "stopped")["status"], "stopped")
        self.runtime.calls.clear()

        result = self.message(self.architect, role="worker", **selection_of("worker"))

        self.assertEqual((result["status"], result["recipientAgentId"]), ("accepted", worker))
        self.assertEqual(len(self.received(worker)), 1)
        self.assertEqual(
            [command for command, _p in self.runtime.calls], ["agent-state", "agent-send"]
        )

    def test_the_send_after_a_resume_waits_for_a_recipients_tool_server(self) -> None:
        request, worker = self.started(status="closed")
        receipt = self.receipt(request)
        applied = receipt["toolServer"]
        self.assertTrue(applied["applied"])
        self.runtime.calls.clear()

        self.message(self.architect, agent_id=worker)

        # The send after the resume asks the bridge to give the recipient's tool server its
        # time first; the send that found the session closed does not.
        first, _resume, second = (payload for _command, payload in self.runtime.calls)
        self.assertNotIn("afterResume", first)
        self.assertEqual(second, {**first, "afterResume": True})
        for label, without in {"not applied": {**applied, "applied": False}, "none": None}.items():
            with self.subTest("a recipient without a tool server is not waited for", case=label):
                receipt["toolServer"] = without
                role_launch_receipts._write_receipt(self.receipt_path(request), receipt)
                self.runtime.agents[worker].update(status="closed", received=[])
                self.runtime.calls.clear()
                result = self.message(self.architect, agent_id=worker)
                self.assertEqual((result["status"], result["resumed"]), ("accepted", True))
                sends = [p for command, p in self.runtime.calls if command == "agent-send"]
                self.assertEqual(len(sends), 2)
                self.assertFalse(any("afterResume" in payload for payload in sends))
        with self.subTest("an open session is sent to at once"):
            self.runtime.agents[worker].update(status="idle", received=[])
            self.runtime.calls.clear()
            self.message(self.architect, agent_id=worker)
            self.assertEqual([p.get("afterResume") for _c, p in self.runtime.calls], [None])

    def test_a_closed_session_is_resumed_first_under_the_scope_check_of_revive(self) -> None:
        request, worker = self.started(status="closed")
        self.runtime.calls.clear()
        agents = list(self.runtime.agents)

        result = self.message(self.architect, agent_id=worker)

        self.assertEqual(
            (result["status"], result["resumed"], result["taken"]), ("accepted", True, "started")
        )
        self.assertEqual(
            [command for command, _p in self.runtime.calls],
            ["agent-send", "agent-resume", "agent-send"],
        )
        self.assertEqual(len(self.received(worker)), 1)
        self.assertEqual((list(self.runtime.agents), len(self.enclosures.start_calls)), (agents, 1))
        with self.subTest("a changed task scope refuses with both values and resumes nothing"):
            self.runtime.agents[worker].update(status="closed", received=[])
            receipt = self.receipt(request)
            current = copy.deepcopy(receipt["arMcpContext"]["taskContext"])
            receipt["arMcpContext"]["taskContext"]["contract_path"] = (
                "/enclosures/moved/contract.json"
            )
            role_launch_receipts._write_receipt(self.receipt_path(request), receipt)
            self.runtime.calls.clear()
            result = self.refusal(
                self.message(self.architect, agent_id=worker), "scope-check-failed"
            )
            self.assertIn("/enclosures/moved/contract.json", result["detail"])
            self.assertIn(current["contract_path"], result["detail"])
            self.assertEqual([command for command, _p in self.runtime.calls], ["agent-send"])
            self.assertEqual(
                (self.runtime.agents[worker]["status"], self.received(worker)), ("closed", [])
            )
            receipt["arMcpContext"]["taskContext"] = current
            role_launch_receipts._write_receipt(self.receipt_path(request), receipt)
        with self.subTest("an agent the host cannot resume: its reason, and nothing is relaunched"):
            self.runtime.agents[worker]["resumeRefusal"] = NO_ROLLOUT
            self.runtime.calls.clear()
            result = self.refusal(
                self.message(self.architect, agent_id=worker), "recipient-cannot-be-resumed"
            )
            self.assertTrue(result["detail"].startswith(NO_ROLLOUT))
            self.assertTrue(Path(result["messageFile"]).name.endswith(".undelivered.md"))
            self.assertEqual(
                [command for command, _p in self.runtime.calls], ["agent-send", "agent-resume"]
            )
            self.assertEqual((list(self.runtime.agents), self.received(worker)), (agents, []))
        with self.subTest("the host cannot be reached for the resume"):
            # MIK-R171 rule 3: the message is held with the reason, not refused and lost.
            self.runtime.fail("agent-resume", "paseo_daemon_unreachable", "connection refused")
            self.runtime.fail("agent-resume", "paseo_daemon_unreachable", "connection refused")
            result = self.message(self.architect, agent_id=worker)
            self.assertEqual((result["status"], result["heldReason"]), ("held", "host-unreachable"))
            self.assertIn("held, not lost", result["detail"])
            self.assertEqual(self.received(worker), [])
        with self.subTest("a taskless recipient has no task scope to check"):
            _architect, other = self.started("architect", status="closed")
            result = self.message(binding("worker"), agent_id=other)
            self.assertEqual((result["status"], result["resumed"]), ("accepted", True))

    def test_a_caller_without_a_binding_or_a_runtime_is_refused(self) -> None:
        _request, worker = self.started(status="idle")
        self.runtime.calls.clear()
        self.refusal(self.message(None, agent_id=worker), "caller-has-no-binding")
        with patch.dict(
            os.environ,
            {"XDG_CONFIG_HOME": str((self.root / "unconfigured-user-config").resolve())},
        ):
            unconfigured = send_role_message(
                runtime_config(self.root / "unconfigured", configured=False),
                RoleMessageCall(text="report your plan", agent_id=worker),
                environment=self.architect.environment(),
            )
        self.assertIn(
            "no Paseo runtime configured",
            self.refusal(unconfigured, "no-paseo-runtime-configured")["detail"],
        )
        self.assertEqual(self.runtime.calls, [])

    def test_a_host_that_does_not_answer_holds_the_message_and_delivers_it_once(self) -> None:
        """MIK-R171 rules 3 and 7: a delay, not a loss; and the waiting copy keeps its id."""

        _request, worker = self.started(status="idle")
        for code in (
            "paseo_daemon_unreachable",
            "paseo_bridge_timeout",
            "paseo_bridge_invalid_reply",
        ):
            with self.subTest(code):
                self.runtime.agents[worker].update(status="idle", received=[])
                # The attempt fails, and so does the drain behind it: the host is still down.
                self.runtime.fail("agent-send", code)
                self.runtime.fail("agent-send", code)

                result = self.message(self.architect, agent_id=worker)

                self.assertEqual(
                    (result["status"], result["heldReason"]), ("held", "host-unreachable")
                )
                self.assertIn("held, not lost", result["detail"])
                self.assertIn(code, result["detail"])
                self.assertTrue(result["heldMessageId"])
                self.assertEqual(self.received(worker), [])
                # The waiting message is the attempt's own id: an answering host records
                # one copy of it, not two.
                located = paseo_role_tools._locate_recipient_store(
                    self.config, RoleMessageCall(text="lookup", agent_id=worker), worker
                )
                assert located is not None
                store = json.loads(
                    (
                        Path(located[2]) / ".ar-product" / "held-messages" / f"{worker}.json"
                    ).read_text()
                )
                self.assertEqual(
                    [row["attemptedMessageId"] for row in store["messages"]],
                    [result["heldMessageId"]],
                )

                with self.subTest("the message arrives once when the host answers"):
                    self.runtime.agents[worker].update(status="idle", received=[])
                    sent: list[str] = []

                    def record(
                        payload: dict[str, Any],
                        _sent: list[str] = sent,
                        _original: Any = self.runtime._agent_send,
                    ) -> dict[str, Any]:
                        _sent.append(payload["messageId"])
                        return _original(payload)

                    with patch.object(self.runtime, "_agent_send", side_effect=record):
                        role_held_messages.sweep_held_messages(self.config)

                    self.assertEqual(len(self.received(worker)), 1)
                    self.assertIn("held, not lost", result["detail"])
                    self.assertEqual(sent, [result["heldMessageId"]])

    def test_an_unknown_send_outcome_is_not_delivered_twice(self) -> None:
        """MIK-R171 rules 3 and 7: the host may have the message; the resend reuses its id."""

        request, worker = self.started(status="idle")
        sent: set[str] = set()
        copies: list[str] = []

        def dedup(payload: dict[str, Any]) -> dict[str, Any]:
            if payload["messageId"] in sent:
                # The host records one message id once: the resend adds no second copy.
                return {"delivery": {"delivered": True, "taken": "started", "turnId": TURN}}
            sent.add(payload["messageId"])
            copies.append(payload["text"])
            self.runtime.agents[worker].setdefault("received", []).append(
                (payload["messageId"], payload["text"])
            )
            return {"delivery": {"delivered": True, "taken": "started", "turnId": TURN}}

        with patch.object(self.runtime, "_agent_send", side_effect=dedup):
            # The host took the message twice over -- a timeout and the connection lost
            # while sending -- and neither answer came back.
            for _attempt in (1, 2):
                self.runtime.fail(
                    "agent-send",
                    "paseo_send_outcome_unknown",
                    "The connection to the Paseo daemon was lost while the message was sent.",
                    True,
                )
            result = self.message(self.architect, agent_id=worker, text="unknown outcome")

            self.assertEqual((result["status"], result["heldReason"]), ("held", "host-unreachable"))
            self.assertIn("not known whether the message was delivered", result["detail"])
            self.assertEqual(len(copies), 1)

            with self.subTest("the host answers and the waiting copy is the one id"):
                role_held_messages.sweep_held_messages(self.config)

        self.assertEqual(len(self.received(worker)), 1)
        self.assertEqual(sorted(sent), [result["heldMessageId"]])
        workspace = Path(self.receipt(request)["workspace"]["path"])
        self.assertEqual(role_held_messages.waiting_total(workspace, worker), 0)

    def test_a_delivery_that_takes_held_messages_answers_ok(self) -> None:
        """MIK-R171 rules 4 and 7: no validation error follows a delivery.

        A call that takes held messages with it once answered with the turn id, which the
        response model forbids, so the call raised after the host had taken everything.
        """

        _request, worker = self.started(status="idle")
        self.runtime.agents[worker]["pendingPermissions"] = [{"name": "Bash"}]
        self.assertEqual(
            self.message(self.architect, agent_id=worker, text="first")["status"], "held"
        )
        self.runtime.agents[worker]["pendingPermissions"] = []

        result = self.message(self.architect, agent_id=worker, text="second")

        self.assertEqual(result["status"], "accepted")
        self.assertNotIn("turnId", result)
        # The registered tool's own model accepts the answer: a delivery always answers ok.
        answer = RoleMessageResponse.model_validate(result)
        self.assertTrue(answer.ok)
        self.assertEqual((answer.status, answer.recipientAgentId), ("accepted", worker))
        received = self.received(worker)
        self.assertEqual(len(received), 1)
        self.assertEqual([received[0].count(text) for text in ("first", "second")], [1, 1])

    def test_a_held_batch_preserves_the_reported_replacement_and_warning(self) -> None:
        _request, worker = self.started(status="idle")
        self.runtime.agents[worker]["pendingPermissions"] = [{"name": "fixture-tool"}]
        first = self.message(self.architect, agent_id=worker, text="earlier held message")
        self.assertEqual(first["status"], "held")
        self.runtime.agents[worker].update(pendingPermissions=[], status="running", steers=True)

        def replacing_send(payload: dict[str, Any]) -> dict[str, Any]:
            reply = self.agent_send(payload)
            if reply["delivery"].get("delivered"):
                reply["delivery"].update(taken="replaced", turnId="replacement-turn")
            return reply

        self.runtime._agent_send = replacing_send
        self.runtime.calls.clear()
        result = self.message(self.architect, agent_id=worker, text="trigger message")

        answer = RoleMessageResponse.model_validate(result)
        self.assertEqual((answer.ok, answer.status, answer.taken), (True, "accepted", "replaced"))
        self.assertIn("replaced the recipient's running turn", result["warning"])
        self.assertIn("that turn did not continue", result["warning"])
        line = sender_line(starting_agent(self.config, self.architect))
        self.assertEqual(
            self.received(worker),
            [f"{line}\nearlier held message\n\n{line}\ntrigger message"],
        )
        self.assertEqual([cmd for cmd, _payload in self.runtime.calls].count("agent-send"), 1)

    def test_the_hosts_own_activity_record_holds_a_message_the_snapshot_calls_idle(self) -> None:
        """L171-F07: the second view decides too - a published turn is never sent into."""

        _request, worker = self.started(status="idle")
        directory = activity_directory(self.config)
        assert directory is not None
        directory.mkdir(parents=True, exist_ok=True)
        (directory / HOST_ACTIVITY_FILE).write_text(
            json.dumps(
                {
                    "bootId": "boot-1",
                    "complete": True,
                    "agents": {
                        worker: {"state": "in-turn", "eventAt": "2026-10-17T05:27:00+00:00"}
                    },
                    "ignoredEvents": 0,
                }
            ),
            encoding="utf-8",
        )

        result = self.message(self.architect, agent_id=worker, text="held by the host record")

        self.assertEqual((result["status"], result["heldReason"]), ("held", "recipient-in-turn"))
        self.assertEqual(self.received(worker), [])
        self.assertNotIn("cancelled", json.dumps(result))
        with self.subTest("a provider known to steer gets the steer the record asks for"):
            _other, steerer = self.started("reviewer", status="idle")
            self.runtime.agents[steerer].update(steers=True, received=[])
            (directory / HOST_ACTIVITY_FILE).write_text(
                json.dumps(
                    {
                        "bootId": "boot-1",
                        "complete": True,
                        "agents": {
                            steerer: {"state": "in-turn", "eventAt": "2026-10-17T05:27:00+00:00"}
                        },
                        "ignoredEvents": 0,
                    }
                ),
                encoding="utf-8",
            )
            steered = self.message(self.architect, agent_id=steerer, text="into the turn")
            self.assertEqual((steered["status"], steered["taken"]), ("accepted", "steered"))
            self.assertEqual(len(self.received(steerer)), 1)
        with self.subTest("the same record says the turn ended: the queue delivers it"):
            (directory / HOST_ACTIVITY_FILE).write_text(
                json.dumps(
                    {
                        "bootId": "boot-1",
                        "complete": True,
                        "agents": {
                            worker: {"state": "idle", "eventAt": "2026-10-17T05:28:00+00:00"}
                        },
                        "ignoredEvents": 0,
                    }
                ),
                encoding="utf-8",
            )
            located = paseo_role_tools._locate_recipient_store(
                self.config, RoleMessageCall(text="lookup", agent_id=worker), worker
            )
            assert located is not None
            outcome = role_held_messages.drain_held(self.config, located[0], located[1])
            self.assertEqual(outcome["outcome"], "delivered")
            self.assertEqual(len(self.received(worker)), 1)

    def test_a_copy_the_recipient_already_holds_is_not_sent_again(self) -> None:
        """L171-F02: the recipient's own timeline settles at-most-once, on any host."""

        request, worker = self.started(status="idle")
        # The host took an earlier send but never answered it: the copy is in the
        # recipient's timeline, and the store keeps the id that send used.
        self.runtime.agents[worker].update(timeline=["unanswered-id"], received=[])
        folder = str(self.receipt(request)["workspace"]["path"])
        held_answer = role_held_messages.hold_message(
            folder,
            worker,
            HeldMessage(
                sender_line="manager",
                sender_agent_id=self.architect.agent_id,
                text="the host may already hold this",
                reason="host-unreachable",
                attempted_message_id="unanswered-id",
            ),
        )
        self.assertEqual(held_answer["heldMessageId"], "unanswered-id")
        located = paseo_role_tools._locate_recipient_store(
            self.config, RoleMessageCall(text="lookup", agent_id=worker), worker
        )
        assert located is not None
        self.runtime.calls.clear()

        result = role_held_messages.drain_held(self.config, located[0], located[1])

        self.assertEqual(result["outcome"], "delivered")
        # No send happened: the timeline already carried the message.
        self.assertEqual([c for c, _p in self.runtime.calls if c == "agent-send"], [])
        self.assertEqual(self.received(worker), [])
        wrote = [entry for entry in (result.get("deliveries") or [])]
        self.assertEqual([entry.get("taken") for entry in wrote], ["already-there"])

    def test_a_wait_returns_the_outcome_of_the_turn_that_consumed_the_message(self) -> None:
        _request, worker = self.started(status="idle")
        ended = {"state": "ended", "text": "The plan: read, change, test.", "textTruncated": False}
        outcomes: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {
            "turn-finished": (
                {**ended, "outcome": "finished"},
                {"text": "The plan: read, change, test.", "textTruncated": False},
            ),
            "turn-failed": (
                {
                    "state": "ended",
                    "outcome": "failed",
                    "text": None,
                    "textTruncated": False,
                    "error": "usage limit",
                },
                {"text": None, "textTruncated": False},
            ),
            "turn-cancelled": (
                {"state": "ended", "outcome": "cancelled", "text": None, "textTruncated": False},
                {"text": None, "textTruncated": False},
            ),
            "permission-pending": (
                {"state": "permission", "permission": "Bash"},
                {"permission": "Bash"},
            ),
        }
        for status, (answer, fields) in outcomes.items():
            with self.subTest(status):
                self.runtime.agents[worker].update(status="idle", received=[])
                self.runtime.calls.clear()
                still = {"state": "running", "turnId": TURN}
                self.waits = [still, still, answer]
                self.clock = 0.0

                result = self.message(self.architect, agent_id=worker, wait=True)

                self.assertEqual((result["ok"], result["status"]), (True, status))
                self.assertEqual({key: result.get(key) for key in fields}, fields)
                self.assertEqual(result["waitedSeconds"], 120)
                # Each wait names the message by the id it was sent with and the turn that took
                # it, as the send returned it. The message began that turn.
                (message_id,) = self.message_ids(worker)
                named = {"agentId": worker, "messageId": message_id, "turnId": TURN}
                self.assertEqual(self.waited(), [{**named, "waitMs": 40000}] * 3)
        self.assertIn(
            "usage limit", self.message_with([outcomes["turn-failed"][0]], worker)["detail"]
        )
        self.assertIn(
            "Bash", self.message_with([outcomes["permission-pending"][0]], worker)["detail"]
        )
        # The message began the recipient's turn: the text is that turn's, with no reservation.
        self.assertEqual(
            self.message_with([outcomes["turn-finished"][0]], worker)["detail"],
            "The recipient's turn finished; text is its final text. A finished turn is not AR "
            "acceptance of any requirement.",
        )
        with self.subTest("the recipient became unavailable during the wait"):
            result = self.message_with([{"state": "unavailable", "reason": "archived"}], worker)
            self.assertEqual(result["status"], "turn-cancelled")
            self.assertIn("archived", result["detail"])

    def test_a_wait_follows_the_message_into_the_turn_that_consumes_it(self) -> None:
        # A message starts a turn only when the recipient runs none (MIK-R171 rule 2), so the
        # wait follows the turn this message started.
        _request, worker = self.started(status="idle")
        done = {
            "state": "ended",
            "outcome": "finished",
            "text": "DONE-C",
            "textTruncated": False,
            "laterTurn": True,
        }
        # The turn that was running ends and the recipient runs the message in the next one; an
        # answer that names no turn keeps the one named before.
        self.waits = [
            {"state": "running", "turnId": TURN},
            {"state": "running", "turnId": "turn-8"},
            {"state": "running", "turnId": None},
            done,
        ]

        result = self.message(self.architect, agent_id=worker, wait=True)

        self.assertEqual(
            (result["status"], result["taken"], result["text"], result["waitedSeconds"]),
            ("turn-finished", "started", "DONE-C", 160),
        )
        (message_id,) = self.message_ids(worker)
        self.assertEqual(
            [(wait["turnId"], wait.get("steered"), wait["messageId"]) for wait in self.waited()],
            [(turn, None, message_id) for turn in (TURN, TURN, "turn-8", "turn-8")],
        )
        # The wait followed a later turn: the text is that turn's, with no reservation.
        self.assertEqual(
            result["detail"],
            "The recipient's turn finished; text is its final text. A finished turn is not AR "
            "acceptance of any requirement.",
        )
        with self.subTest("the turn that consumed the message cannot be told: no text"):
            undecided = {"state": "undecided", "reason": "no turn followed"}
            for waits in ([undecided], [{"state": "running", "turnId": "turn-8"}, undecided]):
                self.runtime.agents[worker].update(status="idle", received=[])
                self.runtime.calls.clear()
                self.waits = list(waits)
                self.clock = 0.0
                result = self.message(self.architect, agent_id=worker, wait=True)
                self.assertEqual((result["ok"], result["status"]), (True, "accepted"))
                self.assertEqual(result["waitedSeconds"], 40 * len(waits))
                self.assertIn("could not be told", result["detail"])
                self.assertIn("(no turn followed)", result["detail"])
                self.assertIn(
                    "The message stays delivered; the reply must be read later", result["detail"]
                )
                self.assertNotIn("text", result)
                self.assertEqual(len(self.received(worker)), 1)
        with self.subTest("the send could not name the turn: the wait names none"):
            self.runtime.agents[worker].update(taken=None)
            self.replace(paseo_role_tools, "bridge_call", self.runtime_that_names_no_turn)
            self.message_with([{"state": "running", "turnId": "turn-9"}, done], worker)
            self.assertEqual([wait.get("turnId") for wait in self.waited()], [None, "turn-9"])

    def runtime_that_names_no_turn(
        self, config: Any, command: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        reply = self.runtime(config, command, payload)
        if "delivery" in reply:
            reply["delivery"]["turnId"] = None
        return reply

    def message_with(
        self, waits: list[dict[str, Any]], worker: str, status: str = "idle", **call: Any
    ) -> dict[str, Any]:
        self.runtime.agents[worker].update(status=status, received=[])
        self.runtime.calls.clear()
        self.waits = list(waits)
        self.clock = 0.0
        return self.message(self.architect, agent_id=worker, wait=True, **call)

    def test_a_wait_that_times_out_leaves_the_message_delivered(self) -> None:
        _request, worker = self.started(status="idle")
        cases = {
            "the default": ({}, 300),
            "the caller's": ({"timeout_seconds": 50}, 50),
            "the maximum": ({"timeout_seconds": 5000}, 1800),
        }
        for label, (call, seconds) in cases.items():
            with self.subTest(label):
                result = self.message_with([], worker, **call)

                self.assertEqual((result["ok"], result["status"]), (True, "timeout"))
                self.assertEqual(result["waitedSeconds"], seconds)
                self.assertIn(f"did not end within {seconds} seconds", result["detail"])
                self.assertIn(
                    "The message stays delivered; the reply must be read later", result["detail"]
                )
                self.assertEqual(len(self.received(worker)), 1)
                slices = [
                    payload["waitMs"]
                    for command, payload in self.runtime.calls
                    if command == "agent-wait"
                ]
                # A sequence of bridge calls, each well inside the limit of one bridge call.
                self.assertTrue(all(0 < value <= 40000 for value in slices), slices)
                self.assertEqual(sum(slices), seconds * 1000)
        with self.subTest("the host gives no answer during the wait"):
            self.runtime.fail("agent-wait", "paseo_daemon_unreachable", "connection refused")
            result = self.message_with([], worker)
            self.assertEqual(result["status"], "timeout")
            self.assertIn("the host gave no answer (paseo_daemon_unreachable)", result["detail"])
            self.assertEqual(len(self.received(worker)), 1)
        with self.subTest("without wait nothing waits"):
            self.runtime.agents[worker].update(status="idle", received=[])
            self.runtime.calls.clear()
            self.assertEqual(self.message(self.architect, agent_id=worker)["status"], "accepted")
            self.assertNotIn("agent-wait", [command for command, _p in self.runtime.calls])

    def test_the_results_and_refusals_are_exactly_the_named_ones(self) -> None:
        self.assertEqual(
            set(get_args(RoleMessageStatus)),
            {
                "accepted",
                "held",
                "turn-finished",
                "turn-failed",
                "turn-cancelled",
                "permission-pending",
                "timeout",
                "refused",
            },
        )
        self.assertEqual(
            set(get_args(RoleMessageRefusal)),
            {
                "recipient-busy",
                "recipient-not-found",
                "recipient-archived",
                "recipient-ambiguous",
                "recipient-cannot-be-resumed",
                "investigator-capacity",
                "scope-check-failed",
                "caller-has-no-binding",
                "no-paseo-runtime-configured",
                "host-unreachable",
                "message-too-long",
                "message-file-failed",
                "settings-invalid",
            },
        )


if __name__ == "__main__":
    unittest.main()
