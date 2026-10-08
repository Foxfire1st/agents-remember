"""PNT-R06: role start by role agents, against a fake bridge; the fixture of both role tools.

The cases of role messaging are in ``test_paseo_role_messages.py``.
"""

from __future__ import annotations

import asyncio
import json
import threading
import unittest
import uuid
from typing import Any, cast
from unittest.mock import patch

from agents_remember.application.agent_binding import (
    AGENT_ID_VARIABLE,
    ROLE_VARIABLE,
    SPRINT_REF_VARIABLE,
    TOOL_SERVER_NAME,
    AgentBinding,
)
from agents_remember.application.role_launch_context import selection_binding
from agents_remember.cli import (
    paseo_catalog,
    paseo_role_tools,
    paseo_role_wait,
    role_launch_preparation,
    role_launch_receipts,
    role_launch_routes,
    role_launch_workspace,
)
from agents_remember.cli.paseo_launch import StartingAgent
from agents_remember.cli.paseo_role_tools import (
    MAY_START,
    send_role_message,
    start_role,
    start_rule_violation,
)
from agents_remember.cli.paseo_status import AgentReading
from agents_remember.cli.role_launch_preparation import RoleHandoverRequest
from agents_remember.cli.role_launch_receipts import _message_binding_projection_reference
from agents_remember.mcp.registration.role_agents import register_role_agent_tools
from agents_remember.mcp.tools import role_agents as role_agent_payloads
from agents_remember.models.role_agents import (
    RoleMessageCall,
    RoleStartCall,
)
from agents_remember.models.role_launcher import RoleDispatchRequest, RoleSelection
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks.document_refs import ResolvedTaskDocument, TaskDocumentRefError
from agents_remember_test_support.testing.waits import HANG_GUARD_SECONDS
from fastapi.responses import JSONResponse
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from test_paseo_launch import (
    CLOSING_STATES,
    LEAF_REF,
    MASTER_REF,
    ROLE_REFS,
    SERVER_ID,
    SPRINT_REF,
    runtime_config,
)
from test_paseo_status import StatusTestCase

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
OTHER_LEAF = TaskDocumentRef(repository="agents-remember", path="master/02_leaf.json")
ARCHIVED_AT = "2026-10-02T02:00:00.000Z"
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
        self.replace(paseo_role_wait, "bridge_call", self.runtime)
        self.replace(paseo_role_tools, "resolve_role_launch_context", side_effect=self.context)
        self.replace(paseo_role_tools, "TaskDocumentTopology", self.topology)
        self.runtime._agent_send = self.agent_send
        self.runtime._agent_wait = self.agent_wait
        # What each wait call answers, in order; the clock moves by `waitMs` with every call.
        self.waits: list[dict[str, Any]] = []
        self.clock = 0.0
        self.replace(paseo_role_wait, "_monotonic", lambda: self.clock)
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
            else ("busy", "the agent waits for a permission decision")
            if agent.get("pendingPermissions")
            else ("busy", "the agent is mid-turn and cannot take a message up")
            if agent["status"] == "running" and agent.get("steers") is False
            else None
        )
        if refusal is not None:
            delivery: dict[str, Any] = {
                "delivered": False,
                "refused": refusal[0],
                "detail": refusal[1],
            }
            if refusal[1].endswith("permission decision"):
                name = agent["pendingPermissions"][0].get("name") if agent else None
                delivery.update(permissionPending=True, **({"permission": name} if name else {}))
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
        # Without a queued answer the time is up and the turn the caller named still runs.
        still = {"state": "running", "turnId": payload.get("turnId")}
        return {"serverId": SERVER_ID, "wait": self.waits.pop(0) if self.waits else still}

    def waited(self) -> list[dict[str, Any]]:
        return [payload for command, payload in self.runtime.calls if command == "agent-wait"]

    def message_ids(self, agent_id: str) -> list[str]:
        return [message_id for message_id, _text in self.runtime.agents[agent_id]["received"]]

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

    def started(self, role: str = "worker", **state: Any) -> tuple[RoleDispatchRequest, str]:
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
                    selection = RoleSelection.model_validate({"role": role, **ROLE_REFS[role]})
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
                    start_rule_violation(caller, RoleSelection.model_validate(worker)),
                    ("selection-outside-callers-scope", rule),
                )
        for caller in ("orchestrator", "manager"):
            with self.subTest(f"a {caller} under its own scope"):
                self.assertIsNone(
                    start_rule_violation(binding(caller), RoleSelection.model_validate(worker))
                )


class RoleStartTests(RoleToolsTestCase):
    def test_terminal_enclosures_refuse_both_entry_points_before_roots_or_launch(self) -> None:
        for state in ("terminal-cleanup-completed", "terminal-archive-ready"):
            for roots in (
                {},
                {
                    "worktree_group": "/reclaimed/group",
                    "code_worktree": "/reclaimed/code",
                    "memory_worktree": "/reclaimed/memory",
                },
            ):
                with self.subTest(state=state, roots=bool(roots)):
                    summary = "Terminal cleanup is complete and the external enclosure archive remains proven."
                    self.replace(
                        role_launch_workspace,
                        "worktree_status_tool",
                        return_value={
                            "ok": True,
                            "state": state,
                            "status": state,
                            "summary": summary,
                            **roots,
                        },
                    )
                    button = self.refused(self.request("worker"))
                    tool = self.refusal(self.start(self.architect, "worker"), "launch-refused")
                    self.assertIn(summary, str(button.detail))
                    self.assertIn("reopen-required", str(button.detail))
                    self.assertEqual(tool["preparationStatus"], "reopen-required")
                    self.assertIn(summary, tool["detail"])
                    self.assertIn("task_reopen", tool["nextAction"])
                    self.assertEqual(self.enclosures.start_calls, [])
                    self.assertEqual(self.runtime.launch_calls(), [])
                    self.assertEqual(self.receipt_files(), [])

    def test_a_start_runs_the_launchers_path_with_the_caller_as_parent(self) -> None:
        request_id = uuid.uuid4()
        locked: list[bool] = []
        # The tool takes the backend's launch lock around preparation and launch, as the route does.
        self.runtime.observer = lambda _command, _payload: locked.append(
            role_launch_routes._DISPATCH_LOCK.locked()
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
                "preparation": {"enclosure": "created", "workspace": "created"},
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
            self.assertFalse(role_launch_routes._DISPATCH_LOCK.locked())
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
                    runtime_config(self.root / "unconfigured", configured=False),
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
        with self.subTest("the host refuses a call before anything is recorded"):
            # The host answered, so it is not unreachable; what it refused is in the detail.
            self.runtime.fail("catalog", "paseo_call_failed", "the catalog is not served")
            result = self.refusal(self.start(self.architect, "worker"), "launch-refused")
            self.assertIn("the catalog is not served", result["detail"])
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
        with self.subTest("a receipt that is still starting is not answered as running"):
            # Another process of this request wrote the receipt and has not launched yet.
            request = self.request("manager")
            crash = RuntimeError("the process ended here")
            with (
                patch.object(role_launch_receipts, "run_launch_call", side_effect=crash),
                self.assertRaises(RuntimeError),
            ):
                role_launch_routes._role_launch_dispatch_endpoint(
                    self.config,
                    request,
                    started_by=StartingAgent(self.architect.agent_id, "architect", "Projects"),
                )
            self.assertEqual(self.receipt(request)["status"], "starting")
            self.replace(
                paseo_role_tools, "_role_launch_dispatch_endpoint", return_value=JSONResponse({})
            )
            starting = self.start(self.architect, "manager", request_id=request.request_id)
            self.assertEqual(
                (starting["ok"], starting["status"], starting["executionStatus"]),
                (False, "unknown", "starting"),
            )
            self.assertIn("same request id", starting["nextAction"])

    def test_a_request_id_is_repeated_by_its_starter_and_the_launcher_and_no_other_agent(
        self,
    ) -> None:
        request_id = uuid.uuid4()
        self.runtime.fail("agent-create", "paseo_bridge_timeout")
        unknown = self.start(self.architect, "worker", request_id=request_id)
        self.assertEqual((unknown["status"], self.runtime.agents), ("unknown", {}))
        request = self.request("worker", request_id)
        stored = self.receipt(request)["replayRequest"]["agent"]
        self.assertEqual(stored["parentAgentId"], self.architect.agent_id)
        with self.subTest("another agent is refused and nothing is created"):
            other = binding("architect")
            self.runtime.calls.clear()
            refused = self.refusal(
                self.start(other, "worker", request_id=request_id), "launch-refused"
            )
            self.assertEqual(
                refused["detail"],
                f"This request id belongs to an execution that agent {self.architect.agent_id} "
                f"started; agent {other.agent_id} did not start it and cannot repeat it.",
            )
            self.assertEqual((self.runtime.calls, self.runtime.agents), ([], {}))
            self.assertEqual(self.receipt(request)["status"], "unknown")
        with self.subTest("the launcher's Retry replays the stored call with the stored parent"):
            self.runtime.calls.clear()
            status, public = self.dispatch(request)
            self.assertEqual((status, public["status"]), (200, "running"))
            created = [
                payload for command, payload in self.runtime.calls if command == "agent-create"
            ]
            self.assertEqual([{key: call[key] for key in stored} for call in created], [stored])
            self.assertEqual(self.receipt(request)["parentAgentId"], self.architect.agent_id)
            self.assertEqual(list(self.runtime.agents), [unknown["agentId"]])
            # The launcher's bar offers this Retry: the result route says so with the same payload.
            self.assertEqual(self.dispatch(request)[1]["execution"]["agentId"], unknown["agentId"])
        with self.subTest("the starter's own repeat reconciles the same agent"):
            again = self.start(self.architect, "worker", request_id=request_id)
            self.assertEqual(
                (again["status"], again["agentId"], again["parentAgentId"]),
                ("running", unknown["agentId"], self.architect.agent_id),
            )
        with self.subTest("an execution the launcher started is not an agent's to repeat"):
            dashboard = self.request("manager")
            self.dispatch(dashboard)
            agents = list(self.runtime.agents)
            self.runtime.calls.clear()
            refused = self.refusal(
                self.start(self.architect, "manager", request_id=dashboard.request_id),
                "launch-refused",
            )
            self.assertEqual(
                refused["detail"],
                "This request id belongs to an execution that the launcher started; agent "
                f"{self.architect.agent_id} did not start it and cannot repeat it.",
            )
            self.assertEqual((self.runtime.calls, list(self.runtime.agents)), ([], agents))
            self.assertNotIn("parentAgentId", self.receipt(dashboard))
            self.assertEqual(self.dispatch(dashboard)[1]["status"], "running")
        with self.subTest("a taskless execution of another agent"):
            taskless = uuid.uuid4()
            self.start(self.architect, "system-specialist", request_id=taskless)
            refused = self.refusal(
                self.start(binding("architect"), "system-specialist", request_id=taskless),
                "launch-refused",
            )
            self.assertIn("did not start it and cannot repeat it", refused["detail"])

    def test_starts_run_one_at_a_time_and_a_start_waits_for_the_one_before_it(self) -> None:
        inside, proceed, contended = threading.Event(), threading.Event(), threading.Event()
        real_lock = role_launch_routes._DISPATCH_LOCK

        class ObservedLock:
            def acquire(self, **kwargs):
                if real_lock.acquire(blocking=False):
                    return True
                contended.set()
                return real_lock.acquire(**kwargs)

            def release(self):
                real_lock.release()

            def locked(self):
                return real_lock.locked()

        order: list[str] = []
        results: dict[str, dict[str, Any]] = {}

        def observed(_command: str, _payload: dict[str, Any]) -> None:
            name = threading.current_thread().name
            order.append(name)
            if name == "first" and not inside.is_set():
                inside.set()
                self.assertTrue(proceed.wait(HANG_GUARD_SECONDS))

        def run(role: str) -> None:
            results[threading.current_thread().name] = self.start(self.architect, role)

        self.runtime.observer = observed
        first = threading.Thread(target=run, args=("worker",), name="first")
        second = threading.Thread(target=run, args=("manager",), name="second")
        dispatch_lock = patch.object(role_launch_routes, "_DISPATCH_LOCK", ObservedLock())
        dispatch_lock.start()
        self.addCleanup(dispatch_lock.stop)
        first.start()
        self.assertTrue(inside.wait(HANG_GUARD_SECONDS))
        second.start()
        self.assertTrue(
            contended.wait(HANG_GUARD_SECONDS), "second start did not reach the held lock"
        )
        # The second start is neither refused nor begun: it waits for the first to end.
        self.assertTrue(second.is_alive())
        self.assertEqual((results, set(order)), ({}, {"first"}))
        proceed.set()
        first.join(HANG_GUARD_SECONDS)
        second.join(HANG_GUARD_SECONDS)
        self.runtime.observer = None
        self.assertEqual(
            {name: result["status"] for name, result in results.items()},
            {"first": "running", "second": "running"},
        )
        self.assertEqual(order, sorted(order), "the two launches did not interleave")
        self.assertEqual(len(self.runtime.agents), 2)
        self.assertFalse(role_launch_routes._DISPATCH_LOCK.locked())

    def test_a_start_that_waited_its_time_out_says_to_call_again(self) -> None:
        self.assertEqual(paseo_role_tools.LOCK_WAIT_SECONDS, 60)
        # load-independent: the held dispatch lock must expire and return launch-refused.
        self.replace(paseo_role_tools, "LOCK_WAIT_SECONDS", 0.5)
        self.assertTrue(role_launch_routes._DISPATCH_LOCK.acquire(blocking=False))
        try:
            refused = self.refusal(self.start(self.architect, "curator"), "launch-refused")
            # The launcher's route does not wait at all.
            busy = self.refused(self.request("curator"))
        finally:
            role_launch_routes._DISPATCH_LOCK.release()
        self.assertIsInstance(busy, role_launch_routes.LaunchLockBusy)
        self.assertEqual(busy.status_code, 409)
        self.assertEqual(
            refused["detail"],
            "Another start of this tool server was still running after 0.5 seconds, so this one "
            "was not begun. Nothing was recorded for this request.",
        )
        self.assertEqual(
            refused["nextAction"],
            "Call role_start again with the same arguments; starts run one at a time.",
        )
        self.assertEqual((self.runtime.calls, self.receipt_files()), ([], []))
        self.assertEqual(self.start(self.architect, "curator")["status"], "running")
        with self.subTest("the tool's description says so"):
            described = asyncio.run(self.tool_descriptions())["role_start"]
            self.assertIn("Starts run one at a time.", described)
            self.assertIn("waits up to 60 seconds", described)
            self.assertIn("nextAction says to call again with the same arguments", described)

    async def tool_descriptions(self) -> dict[str, str]:
        server = FastMCP("role-tools-descriptions")
        register_role_agent_tools(server, self.config)
        return {
            tool.name: " ".join(line.strip() for line in (tool.description or "").split("\n"))
            for tool in await server.list_tools()
        }

    def test_a_repeat_whose_agent_is_archived_or_gone_is_refused_with_what_to_do(self) -> None:
        gone: dict[str, Any] = {
            "is archived": lambda agent_id: self.runtime.agents[agent_id].update(
                status="closed", archivedAt=ARCHIVED_AT
            ),
            "is unknown to the host": self.runtime.agents.pop,
        }
        for state, leave in gone.items():
            with self.subTest(state):
                request_id = uuid.uuid4()
                first = self.start(self.architect, "system-specialist", request_id=request_id)
                leave(first["agentId"])
                self.runtime.calls.clear()

                result = self.refusal(
                    self.start(self.architect, "system-specialist", request_id=request_id),
                    "launch-refused",
                )

                self.assertEqual(
                    (result["agentId"], result["executionStatus"], result["nextAction"]),
                    (first["agentId"], "stopped", "Start again with a new request id."),
                )
                self.assertIn(f"Agent {first['agentId']} of this request {state}", result["detail"])
                # Nothing but reads: the repeat neither creates, resumes nor un-archives.
                self.assertEqual({command for command, _p in self.runtime.calls}, {"agent-state"})
        with self.subTest("a live agent whose last turn was cancelled is answered as before"):
            request_id = uuid.uuid4()
            first = self.start(self.architect, "system-specialist", request_id=request_id)
            self.runtime.agents[first["agentId"]].update(CLOSING_STATES["stopped"])
            repeat = self.start(self.architect, "system-specialist", request_id=request_id)
            self.assertEqual(
                (repeat["ok"], repeat["status"], repeat["executionStatus"], repeat["agentId"]),
                (True, "running", "stopped", first["agentId"]),
            )
            with self.subTest("the host cannot say whether the agent is still there"):
                unread = AgentReading(reachable=False, unreachable_reason="connection refused")
                self.replace(paseo_role_tools, "read_agent", return_value=unread)
                result = self.refusal(
                    self.start(self.architect, "system-specialist", request_id=request_id),
                    "host-unreachable",
                )
                self.assertIn("connection refused", result["detail"])
                self.assertIn(first["agentId"], result["detail"])

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


class OtherStarterTests(RoleToolsTestCase):
    """What another starter's execution means for a start: its request id is not taken over."""

    def test_a_new_start_on_a_selection_whose_closed_execution_another_began_is_admitted(
        self,
    ) -> None:
        self.runtime.fail("agent-create", "paseo_call_failed", "parent agent is not loaded")
        rejected = self.start(self.architect, "worker")
        self.assertEqual((rejected["status"], self.runtime.agents), ("rejected", {}))
        other = binding("architect")

        fresh = self.start(other, "worker")

        self.assertEqual(
            (fresh["ok"], fresh["status"], fresh["parentAgentId"]),
            (True, "running", other.agent_id),
        )
        self.assertNotEqual(fresh["requestId"], rejected["requestId"])
        self.assertEqual(list(self.runtime.agents), [fresh["agentId"]])
        with self.subTest("a closed execution the launcher began"):
            dashboard, old_agent = self.started("manager", status="idle")
            self.close_execution(dashboard, "completed")
            fresh = self.start(self.architect, "manager")
            self.assertEqual(
                (fresh["status"], fresh["parentAgentId"]), ("running", self.architect.agent_id)
            )
            self.assertNotEqual(fresh["agentId"], old_agent)

    def test_a_receipt_of_another_starter_that_appears_during_a_start_is_not_taken_over(
        self,
    ) -> None:
        other = binding("architect")

        def competitor(request_id: uuid.UUID) -> dict[str, Any]:
            """The receipt another process wrote for the same request id on behalf of ``other``."""

            return {
                "schema": "ar-role-execution/v1",
                "requestId": str(request_id),
                "role": "manager",
                "status": "starting",
                "parentAgentId": other.agent_id,
                "execution": {},
            }

        def refused_start(request_id: uuid.UUID) -> None:
            refused = self.refusal(
                self.start(self.architect, "manager", request_id=request_id), "launch-refused"
            )
            self.assertEqual(
                refused["detail"],
                f"This request id belongs to an execution that agent {other.agent_id} started; "
                f"agent {self.architect.agent_id} did not start it and cannot repeat it.",
            )
            path = self.receipt_path(self.request("manager", request_id))
            self.assertEqual(json.loads(path.read_text("utf-8")), competitor(request_id))
            self.assertEqual((self.runtime.launch_calls(), self.runtime.agents), ([], {}))
            path.unlink()

        with self.subTest("while the handover is compiled"):
            request_id = uuid.uuid4()
            path = self.receipt_path(self.request("manager", request_id))
            compile_handover = self.compile_handover

            def appears_first(request: RoleHandoverRequest) -> dict[str, Any]:
                self.assertTrue(role_launch_receipts._create_receipt(path, competitor(request_id)))
                return compile_handover(request)

            with patch.object(role_launch_preparation, "_compile_handover", appears_first):
                refused_start(request_id)
        with self.subTest("when the creation of the receipt is lost to it"):
            request_id = uuid.uuid4()
            path = self.receipt_path(self.request("manager", request_id))
            place = role_launch_routes._place_message_binding_projection

            def created_first(*args: Any) -> bool:
                self.assertTrue(role_launch_receipts._create_receipt(path, competitor(request_id)))
                return place(*args)

            with patch.object(
                role_launch_routes, "_place_message_binding_projection", side_effect=created_first
            ):
                refused_start(request_id)


class ReusedRequestIdTests(RoleToolsTestCase):
    """A start whose request id belongs to another selection: refused before anything is prepared."""

    def setUp(self) -> None:
        self.compiled = 0
        super().setUp()

    def compile_handover(self, request: RoleHandoverRequest) -> dict[str, Any]:
        """The compiled handover with the binding the build writes: it names the selection."""

        assert request.request_id is not None
        self.compiled += 1
        prepared = super().compile_handover(request)
        bound = {
            **prepared["messageBindingProjection"]["binding"],
            "selection": selection_binding(request.context),
        }
        prepared["messageBindingProjection"] = {
            "requestId": str(request.request_id),
            "binding": bound,
            **_message_binding_projection_reference(self.config, request.request_id, bound),
        }
        return prepared

    def state(self) -> dict[str, bytes]:
        return {
            path.relative_to(self.root).as_posix(): path.read_bytes()
            for path in sorted(self.root.rglob("*"))
            if path.is_file()
        }

    def test_the_request_id_of_one_leaf_reused_on_another_prepares_nothing(self) -> None:
        request_id = uuid.uuid4()
        first = self.start(self.architect, "worker", request_id=request_id)
        self.assertEqual((first["status"], self.compiled), ("running", 1))
        # The second leaf has no enclosure yet: preparing a start for it would create one.
        self.enclosures.started = False
        before = self.state()
        self.runtime.calls.clear()

        result = self.refusal(
            self.start(
                self.architect, "worker", request_id=request_id, task_document_ref=OTHER_LEAF
            ),
            "launch-refused",
        )

        self.assertIn(
            f"Request id {request_id} is already bound to another AR role selection (worker); "
            "nothing was prepared for this request.",
            result["detail"],
        )
        self.assertEqual((self.compiled, len(self.enclosures.start_calls)), (1, 1))
        self.assertFalse(self.enclosures.started)
        self.assertEqual(self.state(), before)
        self.assertEqual(self.runtime.launch_calls(), [])
        self.assertEqual(list(self.runtime.agents), [first["agentId"]])
        with self.subTest("the id of a taskless execution reused on a leaf"):
            taskless = uuid.uuid4()
            self.start(self.architect, "system-specialist", request_id=taskless)
            before = self.state()
            result = self.refusal(
                self.start(self.architect, "reviewer", request_id=taskless), "launch-refused"
            )
            self.assertIn("another AR role selection (system-specialist)", result["detail"])
            self.assertEqual((self.compiled, self.state()), (2, before))


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
