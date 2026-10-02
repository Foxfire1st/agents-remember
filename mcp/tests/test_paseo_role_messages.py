"""PNT-R06: role messaging by role agents, against a fake bridge."""

from __future__ import annotations

import copy
import unittest
import uuid
from pathlib import Path
from typing import Any, get_args

from agents_remember.cli import paseo_role_tools, role_launch_receipts
from agents_remember.cli.paseo_role_tools import send_role_message, sender_line, starting_agent
from agents_remember.models.role_agents import (
    RoleMessageCall,
    RoleMessageRefusal,
    RoleMessageStatus,
)
from test_paseo_launch import runtime_config
from test_paseo_role_tools import (
    ARCHIVED_AT,
    OTHER_SPRINT,
    TURN,
    RoleToolsTestCase,
    binding,
    selection_of,
)
from test_paseo_status import NO_ROLLOUT


class RoleMessageTests(RoleToolsTestCase):
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
                self.assertEqual(sender_line(starting_agent(self.config, caller)), line)
                # Accepted means the host took the message; nothing waits for the turn.
                self.assertEqual([command for command, _p in self.runtime.calls], ["agent-send"])
        with self.subTest("nothing is stored by AR and no receipt changes"):
            after = sorted(path.as_posix() for path in self.root.rglob("*") if path.is_file())
            self.assertEqual(after, before)
            self.assertEqual({path: Path(path).read_bytes() for path in after}, saved)
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
        with self.subTest("failing that, the most recent execution whose agent is live"):
            self.close_execution(first, "completed")
            # The next start is refused by the host before it archived the closed execution's agent.
            self.runtime.fail("agent-archive", "paseo_call_failed", "archive refused")
            second = self.request("worker")
            self.assertEqual(self.dispatch(second)[1]["status"], "rejected")
            self.assertNotEqual(self.receipt(second)["agentId"], old_agent)
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
                paseo_role_tools,
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

    def test_a_recipient_whose_start_has_not_finished_is_refused_as_busy(self) -> None:
        # Until its start has answered, an agent may not have its first message yet.
        request, worker = self.started(status="idle")
        saved = self.receipt(request)
        for status in ("starting", "unknown"):
            for address in ({"agent_id": worker}, {"role": "worker", **selection_of("worker")}):
                with self.subTest(status=status, by=sorted(address)[0]):
                    role_launch_receipts._write_receipt(
                        self.receipt_path(request), {**saved, "status": status}
                    )
                    self.runtime.calls.clear()
                    result = self.refusal(self.message(self.architect, **address), "recipient-busy")
                    self.assertEqual(
                        result["detail"],
                        f"Agent {worker} is not ready for a message: its start has not finished "
                        f"(execution status {status}).",
                    )
                    self.assertIn("answered running", result["nextAction"])
                    self.assertEqual(result["recipientAgentId"], worker)
                    self.assertNotIn("permission", result)
                    # Nothing was sent, resumed or waited for.
                    self.assertEqual(
                        {command for command, _p in self.runtime.calls} - {"agent-state"}, set()
                    )
                    self.assertEqual(self.received(worker), [])
        with self.subTest("once the start has answered, the message is delivered"):
            role_launch_receipts._write_receipt(self.receipt_path(request), saved)
            result = self.message(self.architect, agent_id=worker)
            self.assertEqual((result["status"], len(self.received(worker))), ("accepted", 1))

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

    def test_a_task_bound_selection_has_one_recipient_however_many_agents_are_live(self) -> None:
        to_worker = {"role": "worker", **selection_of("worker")}
        agents: list[str] = []
        for _execution in range(3):
            request, agent_id = self.started(status="idle")
            agents.append(agent_id)
            self.close_execution(request, "completed")
        first, second, current = agents
        # Two earlier executions and the current one; the launches archived the earlier agents.
        self.assertEqual(
            [self.runtime.agents[agent]["archivedAt"] is not None for agent in agents],
            [True, True, False],
        )
        for agent_id in (first, second):
            self.runtime.agents[agent_id].update(status="idle", archivedAt=None, received=[])
        with self.subTest("the current execution's agent is the recipient and no other is asked"):
            self.runtime.calls.clear()
            result = self.message(self.architect, **to_worker)
            self.assertEqual((result["status"], result["recipientAgentId"]), ("accepted", current))
            self.assertEqual(self.asked_about(), [current])
        with self.subTest("failing that, the most recent earlier execution that is live"):
            self.runtime.agents[current].update(status="closed", archivedAt=ARCHIVED_AT)
            self.runtime.calls.clear()
            result = self.message(self.architect, **to_worker)
            self.assertEqual((result["status"], result["recipientAgentId"]), ("accepted", second))
            self.assertEqual(self.asked_about(), [current, second])
            self.assertEqual(self.received(first), [])

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

    def test_a_running_turn_is_never_cancelled(self) -> None:
        _request, worker = self.started(status="running")
        with self.subTest("the running turn takes the message up"):
            result = self.message(self.architect, agent_id=worker)
            self.assertEqual((result["status"], result["taken"]), ("accepted", "steered"))
            self.assertEqual(
                result["detail"],
                "The recipient's running turn took the message up; nothing was cancelled.",
            )
            self.assertNotIn("warning", result)
            self.assertEqual(len(self.received(worker)), 1)
        with self.subTest("the host cannot hand a message to this recipient's running turn"):
            self.runtime.agents[worker].update(steers=False, received=[])
            result = self.refusal(self.message(self.architect, agent_id=worker), "recipient-busy")
            self.assertIn("the message was not delivered", result["detail"])
            self.assertEqual(
                (self.received(worker), self.runtime.agents[worker]["status"]), ([], "running")
            )
        with self.subTest("the host cancelled the turn all the same: the result says so"):
            self.runtime.agents[worker].update(steers=True, taken="replaced")
            result = self.message(self.architect, agent_id=worker)
            self.assertEqual(result["status"], "accepted")
            self.assertIn("cancelled the recipient's running turn", result["warning"])
        with self.subTest("the host does not accept the message"):
            self.runtime.calls.clear()
            for _attempt in range(2):
                self.runtime.fail("agent-send", "paseo_call_failed", "Active turn changed")
            result = self.refusal(self.message(self.architect, agent_id=worker), "recipient-busy")
            self.assertIn("Active turn changed", result["detail"])
            self.assertEqual([command for command, _p in self.runtime.calls], ["agent-send"] * 2)
        with self.subTest("a turn that changed under the send is tried once more"):
            self.runtime.agents[worker].update(taken=None, received=[])
            self.runtime.calls.clear()
            self.runtime.fail("agent-send", "paseo_call_failed", "Active turn changed")
            self.assertEqual(self.message(self.architect, agent_id=worker)["status"], "accepted")
            self.assertEqual(len(self.received(worker)), 1)
            # Both attempts carry the one id of the message: the host records a message once.
            attempts = [
                payload for command, payload in self.runtime.calls if command == "agent-send"
            ]
            self.assertEqual(len(attempts), 2)
            self.assertEqual(attempts[0], attempts[1])
            self.assertEqual(self.message_ids(worker), [attempts[0]["messageId"]])
        with self.subTest("each message has an id of its own"):
            self.message(self.architect, agent_id=worker)
            first, second = self.message_ids(worker)
            self.assertNotEqual(first, second)
            self.assertEqual(str(uuid.UUID(second)), second)

    def test_a_recipient_that_waits_for_a_permission_decision_is_not_sent_to(self) -> None:
        _request, worker = self.started(status="running")
        pending = [{"id": "perm-1", "name": "Bash", "kind": "tool"}]
        self.runtime.agents[worker].update(pendingPermissions=pending)
        self.runtime.calls.clear()

        result = self.refusal(
            self.message(self.architect, agent_id=worker, wait=True), "recipient-busy"
        )

        # A message would deny what the developer was asked: it is not sent, and not tried again.
        self.assertEqual(
            result["detail"],
            f"Agent {worker} waits for a permission decision (Bash). A message would answer it "
            "with a denial, so the message was not delivered.",
        )
        self.assertEqual(
            result["nextAction"],
            "The developer answers the permission in the recipient's chat; send the message "
            "again afterwards.",
        )
        self.assertEqual((result["recipientAgentId"], result["permission"]), (worker, "Bash"))
        self.assertEqual([command for command, _p in self.runtime.calls], ["agent-send"])
        self.assertEqual(self.received(worker), [])
        self.assertEqual(self.runtime.agents[worker]["pendingPermissions"], pending)
        with self.subTest("the host names no permission"):
            self.runtime.agents[worker].update(pendingPermissions=[{"id": "perm-2"}])
            result = self.refusal(self.message(self.architect, agent_id=worker), "recipient-busy")
            self.assertIn("waits for a permission decision. A message would", result["detail"])
            self.assertNotIn("permission", result)
        with self.subTest("once the permission is answered the message is delivered"):
            self.runtime.agents[worker].update(pendingPermissions=[])
            self.assertEqual(self.message(self.architect, agent_id=worker)["status"], "accepted")
        with self.subTest("another busy recipient is told to send again later"):
            self.runtime.agents[worker].update(steers=False, received=[])
            result = self.refusal(self.message(self.architect, agent_id=worker), "recipient-busy")
            self.assertIn("left running", result["nextAction"])
            self.assertNotIn("permission", result["detail"])

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
            self.assertEqual(result["detail"], NO_ROLLOUT)
            self.assertEqual(
                [command for command, _p in self.runtime.calls], ["agent-send", "agent-resume"]
            )
            self.assertEqual((list(self.runtime.agents), self.received(worker)), (agents, []))
        with self.subTest("the host cannot be reached for the resume"):
            self.runtime.fail("agent-resume", "paseo_daemon_unreachable", "connection refused")
            self.refusal(self.message(self.architect, agent_id=worker), "host-unreachable")
        with self.subTest("a taskless recipient has no task scope to check"):
            _architect, other = self.started("architect", status="closed")
            result = self.message(binding("worker"), agent_id=other)
            self.assertEqual((result["status"], result["resumed"]), ("accepted", True))

    def test_a_caller_without_a_binding_or_a_runtime_and_an_unreachable_host_are_refused(
        self,
    ) -> None:
        _request, worker = self.started(status="idle")
        self.runtime.calls.clear()
        self.refusal(self.message(None, agent_id=worker), "caller-has-no-binding")
        unconfigured = send_role_message(
            runtime_config(self.root, configured=False),
            RoleMessageCall(text="report your plan", agent_id=worker),
            environment=self.architect.environment(),
        )
        self.assertIn(
            "no Paseo runtime configured",
            self.refusal(unconfigured, "no-paseo-runtime-configured")["detail"],
        )
        self.assertEqual(self.runtime.calls, [])
        failures = {
            "paseo_daemon_unreachable": "cannot be reached",
            "paseo_bridge_timeout": "It is not known whether the message was delivered.",
            "paseo_bridge_invalid_reply": "paseo_bridge_invalid_reply",
        }
        for code, said in failures.items():
            with self.subTest(code):
                self.runtime.fail("agent-send", code)
                result = self.refusal(
                    self.message(self.architect, agent_id=worker), "host-unreachable"
                )
                self.assertIn(said, result["detail"])
        self.assertEqual(self.received(worker), [])
        uncertain = "not known whether the message was delivered"
        with self.subTest("the connection was lost while the message was being sent"):
            # The host may have taken the message: the refusal says that this is not known.
            lost = f"The connection was lost while the message was being sent; it is {uncertain}."
            for message, after in ((lost, False), ("", True)):
                self.runtime.agents[worker].update(status="idle", received=[])
                self.runtime.fail("agent-send", "paseo_send_outcome_unknown", message, after)
                result = self.refusal(
                    self.message(self.architect, agent_id=worker), "host-unreachable"
                )
                self.assertEqual(result["detail"].count(uncertain), 1)
            self.assertEqual(len(self.received(worker)), 1)
        with self.subTest("a host that was not reached at all delivered nothing"):
            self.runtime.fail("agent-send", "paseo_daemon_unreachable", "connection refused")
            result = self.refusal(self.message(self.architect, agent_id=worker), "host-unreachable")
            self.assertNotIn(uncertain, result["detail"])

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
        _request, worker = self.started(status="running")
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
            ("turn-finished", "steered", "DONE-C", 160),
        )
        (message_id,) = self.message_ids(worker)
        self.assertEqual(
            [(wait["turnId"], wait["steered"], wait["messageId"]) for wait in self.waited()],
            [(turn, True, message_id) for turn in (TURN, TURN, "turn-8", "turn-8")],
        )
        # The wait followed a later turn: the text is that turn's, with no reservation.
        self.assertEqual(
            result["detail"],
            "The recipient's turn finished; text is its final text. A finished turn is not AR "
            "acceptance of any requirement.",
        )
        with self.subTest("the text is the running turn's: the result says what it rests on"):
            running = {**done, "text": "The plan.", "laterTurn": False}
            result = self.message_with([running], worker, status="running")
            self.assertEqual(
                (result["ok"], result["status"], result["taken"], result["text"]),
                (True, "turn-finished", "steered", "The plan."),
            )
            self.assertEqual(
                result["detail"],
                "The recipient's turn finished; text is its final text. The recipient was "
                "mid-turn when the message arrived, and this is the text of that turn: if it does "
                "not answer the message, the reply comes in the recipient's next turn and must be "
                "read later. A finished turn is not AR acceptance of any requirement.",
            )
            # A turn that failed or was cancelled returned no text to qualify.
            failed = {**running, "outcome": "failed", "text": None, "error": "usage limit"}
            result = self.message_with([failed], worker, status="running")
            self.assertEqual(result["status"], "turn-failed")
            self.assertNotIn("mid-turn", result["detail"])
        with self.subTest("the turn that consumed the message cannot be told: no text"):
            undecided = {"state": "undecided", "reason": "no turn followed"}
            for waits in ([undecided], [{"state": "running", "turnId": "turn-8"}, undecided]):
                self.runtime.agents[worker].update(status="running", received=[])
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
        with self.subTest("a turn the host began for the message is not a turn it was handed to"):
            self.runtime.agents[worker].update(taken="replaced")
            self.message_with([done], worker, status="running")
            self.assertEqual([("steered" in wait) for wait in self.waited()], [False])
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
                "scope-check-failed",
                "caller-has-no-binding",
                "no-paseo-runtime-configured",
                "host-unreachable",
            },
        )


if __name__ == "__main__":
    unittest.main()
