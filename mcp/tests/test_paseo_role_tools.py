"""PNT-R06: role start and role messaging by role agents, against a fake bridge."""

from __future__ import annotations

import asyncio
import copy
import json
import threading
import unittest
import uuid
from pathlib import Path
from typing import Any, cast, get_args

from agents_remember.application.agent_binding import (
    AGENT_ID_VARIABLE,
    ROLE_VARIABLE,
    SPRINT_REF_VARIABLE,
    TOOL_SERVER_NAME,
    AgentBinding,
)
from agents_remember.cli import (
    orca_task_receipts,
    orca_task_routes,
    paseo_catalog,
    paseo_role_tools,
)
from agents_remember.cli.paseo_role_tools import (
    MAY_START,
    send_role_message,
    sender_line,
    start_role,
    start_rule_violation,
    starting_agent,
)
from agents_remember.mcp.registration.role_agents import register_role_agent_tools
from agents_remember.mcp.tools import role_agents as role_agent_payloads
from agents_remember.models.orca_launcher import OrcaDispatchRequest, OrcaSelection
from agents_remember.models.role_agents import (
    RoleMessageCall,
    RoleMessageRefusal,
    RoleMessageStatus,
    RoleStartCall,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks.document_refs import ResolvedTaskDocument, TaskDocumentRefError
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from test_paseo_launch import (
    LEAF_REF,
    MASTER_REF,
    ROLE_REFS,
    SERVER_ID,
    SPRINT_REF,
    runtime_config,
)
from test_paseo_status import NO_ROLLOUT, StatusTestCase

ROLES = (
    "architect",
    "system-specialist",
    "orchestrator",
    "manager",
    "worker",
    "reviewer",
    "curator",
)
OTHER_SPRINT = TaskDocumentRef(repository="agents-remember", path="other-sprint/task.json")
OTHER_MASTER = TaskDocumentRef(repository="agents-remember", path="other-master/task.json")
REFS = {
    "sprintDocumentRef": "sprint_ref",
    "masterDocumentRef": "master_ref",
    "taskDocumentRef": "task_ref",
}
CALL_REFS = {
    "sprintDocumentRef": "sprint_document_ref",
    "masterDocumentRef": "master_document_ref",
    "taskDocumentRef": "task_document_ref",
}
TURN = "turn-7"


def binding(role: str, agent_id: str | None = None, **refs: TaskDocumentRef) -> AgentBinding:
    """The binding a launch gives the tool server of a role agent."""

    selected = {REFS[key]: ref for key, ref in ROLE_REFS[role].items()}
    return AgentBinding(
        agent_id=agent_id or str(uuid.uuid4()),
        role=role,
        request_id=str(uuid.uuid4()),
        report_path="/reports/caller.md",
        **cast(Any, {**selected, **refs}),
    )


def selection_of(role: str) -> dict[str, Any]:
    """The task references of a role's class, as the keyword arguments of a tool call."""

    return {CALL_REFS[key]: ref for key, ref in ROLE_REFS[role].items()}


class RoleToolsTestCase(StatusTestCase):
    """Both tools against the fake runtime, which here also takes messages and waits."""

    def setUp(self) -> None:
        super().setUp()
        self.replace(paseo_role_tools, "bridge_call", self.runtime)
        self.replace(paseo_role_tools, "resolve_orca_role_context", side_effect=self.context)
        self.replace(paseo_role_tools, "TaskDocumentTopology", self.topology)
        self.runtime._agent_send = self.agent_send
        self.runtime._agent_wait = self.agent_wait
        # What each wait call answers, in order; the clock moves by `waitMs` with every call.
        self.waits: list[dict[str, Any]] = []
        self.clock = 0.0
        self.replace(paseo_role_tools, "_monotonic", lambda: self.clock)
        self.architect = binding("architect")

    def topology(self, _root: Any) -> Any:
        documents = {SPRINT_REF: self.sprint, MASTER_REF: self.master, LEAF_REF: self.leaf}

        class Topology:
            @staticmethod
            def resolve(reference: TaskDocumentRef) -> ResolvedTaskDocument:
                if reference not in documents:
                    raise TaskDocumentRefError("task-document-not-found", reference.key)
                return documents[reference]

        return Topology()

    def agent_send(self, payload: dict[str, Any]) -> dict[str, Any]:
        """The bridge's delivery rules: only a live agent with an open session is sent to."""

        agent = self.runtime.agents.get(payload["agentId"])
        refusal = (
            ("not-found", "the host has no such agent")
            if agent is None
            else ("archived", "the agent is archived")
            if agent["archivedAt"]
            else ("closed", "the session of the agent is closed")
            if agent["status"] == "closed"
            else ("busy", "the agent is mid-turn and cannot take a message up")
            if agent["status"] == "running" and agent.get("steers") is False
            else None
        )
        if refusal is not None:
            delivery = {"delivered": False, "refused": refusal[0], "detail": refusal[1]}
            return {"serverId": SERVER_ID, "delivery": delivery}
        assert agent is not None
        running = agent["status"] == "running"
        agent.setdefault("received", []).append((payload["messageId"], payload["text"]))
        agent["status"] = "running"
        taken = agent.get("taken") or ("steered" if running else "started")
        return {
            "serverId": SERVER_ID,
            "delivery": {"delivered": True, "taken": taken, "turnId": TURN},
        }

    def agent_wait(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.clock += payload["waitMs"] / 1000
        answer = self.waits.pop(0) if self.waits else {"state": "running"}
        return {"serverId": SERVER_ID, "wait": answer}

    def start(self, caller: AgentBinding | None, role: str, **fields: Any) -> dict[str, Any]:
        call = RoleStartCall(
            role=role,
            request_id=fields.pop("request_id", None) or uuid.uuid4(),
            **{**selection_of(role), **fields},
        )
        environment = caller.environment() if caller is not None else {}
        return start_role(self.config, call, environment=environment)

    def message(
        self, caller: AgentBinding | None, text: str = "report your plan", **to: Any
    ) -> dict[str, Any]:
        environment = caller.environment() if caller is not None else {}
        return send_role_message(
            self.config, RoleMessageCall(text=text, **to), environment=environment
        )

    def started(self, role: str = "worker", **state: Any) -> tuple[OrcaDispatchRequest, str]:
        """A role agent the dashboard started, in the given state; its request and agent id."""

        request = self.launched(role, **state)
        return request, self.receipt(request)["agentId"]

    def received(self, agent_id: str) -> list[str]:
        return [text for _id, text in self.runtime.agents[agent_id].get("received", [])]

    def refusal(self, result: dict[str, Any], name: str) -> dict[str, Any]:
        self.assertEqual(
            (result["ok"], result["status"], result["refusal"]), (False, "refused", name)
        )
        self.assertTrue(result["detail"].strip() and result["nextAction"].strip())
        return result


class MayStartRuleTests(unittest.TestCase):
    def test_each_role_may_start_exactly_the_roles_the_rule_names(self) -> None:
        allowed = {
            "architect": set(ROLES) - {"architect"},
            "orchestrator": {"manager", "worker", "reviewer", "curator"},
            "manager": {"worker", "reviewer", "curator"},
        }
        self.assertEqual({role: set(started) for role, started in MAY_START.items()}, allowed)
        for caller in ROLES:
            for role in ROLES:
                with self.subTest(caller=caller, starts=role):
                    selection = OrcaSelection.model_validate({"role": role, **ROLE_REFS[role]})
                    violated = start_rule_violation(binding(caller), selection)
                    if role in allowed.get(caller, set()):
                        self.assertIsNone(violated)
                        continue
                    assert violated is not None
                    self.assertEqual(violated[0], "role-may-not-start-role")
                    self.assertIn(f"it may not start a {role}", violated[1])
                    self.assertIn("An architect may start every role except architect", violated[1])

    def test_a_task_bound_caller_starts_only_under_its_own_sprint_or_master(self) -> None:
        worker = {"role": "worker", **ROLE_REFS["worker"]}
        cases = {
            "an orchestrator of another sprint": (
                binding("orchestrator", sprint_ref=OTHER_SPRINT),
                "An orchestrator may start roles only on selections under its own sprint "
                f"({OTHER_SPRINT.key}); this selection names sprint {SPRINT_REF.key}.",
            ),
            "a manager of another master": (
                binding("manager", master_ref=OTHER_MASTER),
                "A manager may start roles only on selections under its own master "
                f"({OTHER_MASTER.key}); this selection names master {MASTER_REF.key} under "
                f"sprint {SPRINT_REF.key}.",
            ),
            "a manager of the same master under another sprint": (
                binding("manager", sprint_ref=OTHER_SPRINT),
                "A manager may start roles only on selections under its own master "
                f"({MASTER_REF.key}); this selection names master {MASTER_REF.key} under "
                f"sprint {SPRINT_REF.key}.",
            ),
        }
        for label, (caller, rule) in cases.items():
            with self.subTest(label):
                self.assertEqual(
                    start_rule_violation(caller, OrcaSelection.model_validate(worker)),
                    ("selection-outside-callers-scope", rule),
                )
        for caller in ("orchestrator", "manager"):
            with self.subTest(f"a {caller} under its own scope"):
                self.assertIsNone(
                    start_rule_violation(binding(caller), OrcaSelection.model_validate(worker))
                )


class RoleStartTests(RoleToolsTestCase):
    def test_a_start_runs_the_launchers_path_with_the_caller_as_parent(self) -> None:
        request_id = uuid.uuid4()
        locked: list[bool] = []
        # The tool takes the backend's launch lock around preparation and launch, as the route does.
        self.runtime.observer = lambda _command, _payload: locked.append(
            orca_task_routes._DISPATCH_LOCK.locked()
        )

        result = self.start(
            self.architect, "worker", request_id=request_id, agent="codex", model="gpt-a"
        )

        self.runtime.observer = None
        self.assertEqual(locked, [True, True])
        request = self.request("worker", request_id)
        receipt = self.receipt(request)
        create = next(
            payload for command, payload in self.runtime.calls if command == "agent-create"
        )
        self.assertEqual(
            result,
            {
                "ok": True,
                "status": "running",
                "detail": receipt["detail"],
                "requestId": str(request_id),
                "role": "worker",
                "agentId": receipt["agentId"],
                "parentAgentId": self.architect.agent_id,
                "reportPath": receipt["report"]["path"],
                "handoverArtifactPath": receipt["handoverArtifact"]["path"],
                "executionStatus": "running",
            },
        )
        # The same preparation, launch, binding and receipt as a start from the launcher, plus
        # the parent: named to the runtime and recorded in the receipt.
        self.assertEqual(create["parentAgentId"], self.architect.agent_id)
        self.assertEqual(receipt["parentAgentId"], self.architect.agent_id)
        self.assertEqual(create["agentId"], receipt["agentId"])
        bound = create["mcpServers"][TOOL_SERVER_NAME]["env"]
        self.assertEqual(
            (bound[AGENT_ID_VARIABLE], bound[ROLE_VARIABLE]), (receipt["agentId"], "worker")
        )
        self.assertEqual(len(self.enclosures.start_calls), 1)
        self.assertEqual(
            [command for command, _payload in self.runtime.calls],
            ["catalog", "workspace-open", "agent-create"],
        )
        with self.subTest("the process keeps no catalog between calls and frees the launch lock"):
            self.assertEqual(paseo_catalog._CATALOGS, {})
            self.assertFalse(orca_task_routes._DISPATCH_LOCK.locked())
            self.runtime.calls.clear()
            self.assertEqual(
                self.start(self.architect, "architect")["refusal"], "role-may-not-start-role"
            )
            self.assertEqual(self.start(self.architect, "orchestrator")["status"], "running")
            self.assertEqual(self.runtime.calls[0][0], "catalog")
        with self.subTest("a start from the launcher names no parent"):
            self.runtime.calls.clear()
            dashboard = self.request("architect")
            self.dispatch(dashboard)
            created = next(p for command, p in self.runtime.calls if command == "agent-create")
            self.assertNotIn("parentAgentId", created)
            self.assertNotIn("parentAgentId", self.receipt(dashboard))

    def test_a_start_that_is_not_carried_out_names_the_rule_or_the_state(self) -> None:
        cases: dict[str, tuple[Any, str, str]] = {
            "a caller without a binding": (
                lambda: self.start(None, "worker"),
                "caller-has-no-binding",
                "was not started by an AR role launch",
            ),
            "a binding without its role": (
                lambda: start_role(
                    self.config,
                    RoleStartCall(role="worker", request_id=uuid.uuid4(), **selection_of("worker")),
                    environment={AGENT_ID_VARIABLE: str(uuid.uuid4())},
                ),
                "caller-has-no-binding",
                "was not started by an AR role launch",
            ),
            # The binding reader refuses a binding that does not fit its role's class; the tool
            # passes that refusal on and adds no check of its own.
            "a binding whose references do not fit its role": (
                lambda: start_role(
                    self.config,
                    RoleStartCall(role="worker", request_id=uuid.uuid4(), **selection_of("worker")),
                    environment={
                        **self.architect.environment(),
                        SPRINT_REF_VARIABLE: json.dumps(SPRINT_REF.model_dump(mode="json")),
                    },
                ),
                "caller-has-no-binding",
                "does not carry the task references of a architect",
            ),
            "no Paseo runtime configured": (
                lambda: start_role(
                    runtime_config(self.root, configured=False),
                    RoleStartCall(role="worker", request_id=uuid.uuid4(), **selection_of("worker")),
                    environment=self.architect.environment(),
                ),
                "no-paseo-runtime-configured",
                "no Paseo runtime configured",
            ),
            "a worker starts a role": (
                lambda: self.start(binding("worker"), "reviewer"),
                "role-may-not-start-role",
                "A worker may start no role; it may not start a reviewer.",
            ),
            "an orchestrator starts outside its sprint": (
                lambda: self.start(binding("orchestrator", sprint_ref=OTHER_SPRINT), "worker"),
                "selection-outside-callers-scope",
                "only on selections under its own sprint",
            ),
            "a model the host does not offer": (
                lambda: self.start(self.architect, "worker", agent="codex", model="gpt-z"),
                "launch-refused",
                "does not offer model 'gpt-z'",
            ),
            "a model without its agent": (
                lambda: self.start(self.architect, "worker", model="gpt-a"),
                "launch-refused",
                "a model or an effort is given without the agent it belongs to",
            ),
        }
        for label, (call, refusal, said) in cases.items():
            with self.subTest(label):
                result = self.refusal(call(), refusal)
                self.assertIn(said, result["detail"])
                self.assertEqual(self.receipt_files(), [])
                self.assertEqual(self.runtime.launch_calls(), [])
                self.assertEqual(self.runtime.agents, {})
        with self.subTest("the daemon cannot be reached before anything is recorded"):
            self.runtime.fail("catalog", "paseo_daemon_unreachable", "connection refused")
            result = self.refusal(self.start(self.architect, "worker"), "host-unreachable")
            self.assertIn("paseo_daemon_unreachable", result["detail"])
            self.assertEqual((self.receipt_files(), self.runtime.agents), ([], {}))
        with self.subTest("a second start on an open task-bound execution"):
            first = self.start(self.architect, "worker")
            # While the host cannot say what the open execution's agent is doing, nothing starts.
            self.runtime.fail("agent-state", "paseo_daemon_unreachable", "connection refused")
            unread = self.refusal(self.start(self.architect, "worker"), "host-unreachable")
            self.assertIn("connection refused", unread["detail"])
            self.assertEqual(list(self.runtime.agents), [first["agentId"]])
            again = self.refusal(self.start(self.architect, "worker"), "launch-refused")
            self.assertIn(
                f"already has an open execution (request {first['requestId']}", again["detail"]
            )
            self.assertEqual(list(self.runtime.agents), [first["agentId"]])

    def test_each_launch_status_and_the_same_request_id(self) -> None:
        with self.subTest("no usable answer, then the same request id"):
            request_id = uuid.uuid4()
            self.runtime.fail("agent-create", "paseo_bridge_timeout", after_effect=True)
            unknown = self.start(self.architect, "worker", request_id=request_id)
            self.assertEqual((unknown["ok"], unknown["status"]), (False, "unknown"))
            self.assertEqual(unknown["executionStatus"], "unknown")
            self.assertIn("same request id", unknown["nextAction"])
            again = self.start(self.architect, "worker", request_id=request_id)
            self.assertEqual((again["ok"], again["status"]), (True, "running"))
            self.assertEqual(again["agentId"], unknown["agentId"])
            self.assertEqual(list(self.runtime.agents), [unknown["agentId"]])
        with self.subTest("the host refuses the creation"):
            self.runtime.fail(
                "agent-create", "paseo_call_failed", "Provider codex is not configured"
            )
            rejected = self.start(self.architect, "manager")
            self.assertEqual((rejected["ok"], rejected["status"]), (False, "rejected"))
            self.assertIn("Provider codex is not configured", rejected["detail"])
            self.assertNotIn(rejected["agentId"], self.runtime.agents)
            self.assertIn("new request id", rejected["nextAction"])
        with self.subTest(
            "a taskless role: the same request id is the same agent, a new id another"
        ):
            request_id = uuid.uuid4()
            first = self.start(self.architect, "system-specialist", request_id=request_id)
            self.master = self.changed(self.master)
            repeat = self.start(self.architect, "system-specialist", request_id=request_id)
            other = self.start(self.architect, "system-specialist")
            self.assertEqual(repeat["agentId"], first["agentId"])
            self.assertNotEqual(other["agentId"], first["agentId"])
        with self.subTest("the same request id from another caller is another request"):
            refused = self.refusal(
                self.start(binding("architect"), "system-specialist", request_id=request_id),
                "launch-refused",
            )
            self.assertIn("already bound to different", refused["detail"])

    @staticmethod
    def changed(document: ResolvedTaskDocument) -> ResolvedTaskDocument:
        """The same task document after an edit that changes its content."""

        edited = document.document.model_copy(
            update={"title": document.document.title + " (edited)"}
        )
        return ResolvedTaskDocument(ref=document.ref, path=document.path, document=edited)

    def test_a_task_bound_repeat_after_a_changed_task_document_reconciles_the_same_agent(
        self,
    ) -> None:
        request_id = uuid.uuid4()
        first = self.start(self.architect, "worker", request_id=request_id)
        self.leaf = self.changed(self.leaf)

        self.runtime.calls.clear()

        repeat = self.start(self.architect, "worker", request_id=request_id)

        self.assertEqual((repeat["status"], repeat.get("agentId")), ("running", first["agentId"]))
        self.assertEqual(list(self.runtime.agents), [first["agentId"]])
        # The repeat is answered from its receipt: nothing is compiled or launched again, and the
        # agent is read once.
        self.assertEqual(self.runtime.calls, [("agent-state", {"agentId": first["agentId"]})])
        self.assertEqual(len(self.enclosures.start_calls), 1)


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
                "resolve_orca_role_context",
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
            "neither an id nor a role": ({}, "not both and not neither"),
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
            self.runtime.fail("agent-send", "paseo_call_failed", "Active turn changed")
            self.assertEqual(self.message(self.architect, agent_id=worker)["status"], "accepted")
            self.assertEqual(len(self.received(worker)), 1)

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
            orca_task_receipts._write_receipt(self.receipt_path(request), receipt)
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
            orca_task_receipts._write_receipt(self.receipt_path(request), receipt)
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
                self.waits = [{"state": "running"}, {"state": "running"}, answer]
                self.clock = 0.0

                result = self.message(self.architect, agent_id=worker, wait=True)

                self.assertEqual((result["ok"], result["status"]), (True, status))
                self.assertEqual({key: result.get(key) for key in fields}, fields)
                self.assertEqual(result["waitedSeconds"], 120)
                waits = [
                    payload for command, payload in self.runtime.calls if command == "agent-wait"
                ]
                # Each wait names the turn that took the message, as the send returned it.
                self.assertEqual(waits, [{"agentId": worker, "turnId": TURN, "waitMs": 40000}] * 3)
        self.assertIn(
            "usage limit", self.message_with([outcomes["turn-failed"][0]], worker)["detail"]
        )
        self.assertIn(
            "Bash", self.message_with([outcomes["permission-pending"][0]], worker)["detail"]
        )
        self.assertIn(
            "not AR acceptance", self.message_with([outcomes["turn-finished"][0]], worker)["detail"]
        )
        with self.subTest("the recipient became unavailable during the wait"):
            result = self.message_with([{"state": "unavailable", "reason": "archived"}], worker)
            self.assertEqual(result["status"], "turn-cancelled")
            self.assertIn("archived", result["detail"])

    def message_with(self, waits: list[dict[str, Any]], worker: str, **call: Any) -> dict[str, Any]:
        self.runtime.agents[worker].update(status="idle", received=[])
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


class RegisteredRoleToolTests(RoleToolsTestCase):
    """The two tools as a harness reaches them: registered, typed, and off the event loop."""

    def call(self, tool: str, arguments: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        async def invoke() -> tuple[int, dict[str, Any]]:
            server = FastMCP("role-tools-test")
            register_role_agent_tools(server, self.config)
            _content, structured = await server.call_tool(tool, arguments)
            return threading.get_ident(), cast(dict[str, Any], structured)

        return asyncio.run(invoke())

    def test_the_tools_are_registered_under_their_names_and_answer_in_the_envelope(self) -> None:
        async def names() -> list[str]:
            server = FastMCP("role-tools-names")
            register_role_agent_tools(server, self.config)
            return [tool.name for tool in await server.list_tools()]

        self.assertEqual(asyncio.run(names()), ["role_start", "role_message"])
        leaf = {key: ref.model_dump(mode="json") for key, ref in selection_of("worker").items()}
        start = {"role": "worker", "request_id": str(uuid.uuid4()), **leaf}
        # This process was not started by a launch: no binding, whatever the settings hold.
        _loop, refused = self.call("role_start", start)
        self.assertEqual(
            (refused["ok"], refused["operation"], refused["refusal"]),
            (False, "role_start", "caller-has-no-binding"),
        )
        message = {"text": "hello", "agent_id": str(uuid.uuid4())}
        _loop, refused = self.call("role_message", message)
        self.assertEqual(
            (refused["operation"], refused["refusal"]), ("role_message", "caller-has-no-binding")
        )
        with (
            self.subTest("a wait longer than the maximum is refused by the argument model"),
            self.assertRaises(ToolError),
        ):
            self.call("role_message", {**message, "timeout_seconds": 1801})
        with self.subTest("both tools run off the event loop"):
            threads: list[int] = []

            def recorded(original: Any) -> Any:
                def run(*args: Any, **kwargs: Any) -> Any:
                    threads.append(threading.get_ident())
                    return original(*args, **kwargs)

                return run

            self.replace(
                paseo_role_tools, "read_agent_binding", lambda _environment=None: self.architect
            )
            self.replace(role_agent_payloads, "start_role", recorded(paseo_role_tools.start_role))
            self.replace(
                role_agent_payloads,
                "send_role_message",
                recorded(paseo_role_tools.send_role_message),
            )
            loop_thread, started = self.call("role_start", start)
            self.assertEqual(
                (started["status"], started["parentAgentId"]),
                ("running", self.architect.agent_id),
            )
            _loop, sent = self.call(
                "role_message", {"text": "hello", "agent_id": started["agentId"]}
            )
            self.assertEqual(sent["status"], "accepted")
            self.assertEqual(len(threads), 2)
            self.assertNotIn(loop_thread, threads)


if __name__ == "__main__":
    unittest.main()
