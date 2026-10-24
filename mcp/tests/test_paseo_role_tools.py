"""PNT-R06: role start by role agents, against a fake bridge; the fixture of both role tools.

The cases of role messaging are in ``test_paseo_role_messages.py``; the parentless chat
starts and the role-start cases are in ``test_paseo_role_tools_start.py``, and the fixture
both modules share is in ``paseo_role_tools_test_support.py``.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import unittest
import uuid
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

from agents_remember.application.agent_binding import (
    AGENT_ID_VARIABLE,
    read_agent_binding,
)
from agents_remember.application.role_launch_context import (
    resolve_role_launch_context,
    selection_binding,
)
from agents_remember.application.tool_answer_log import tool_call_context
from agents_remember.cli import (
    paseo_role_tools,
    role_launch_preparation,
    role_launch_receipts,
    role_launch_routes,
)
from agents_remember.cli.paseo_role_tools import (
    MAY_START,
    start_rule_violation,
)
from agents_remember.cli.role_launch_preparation import (
    RoleHandoverRequest,
)
from agents_remember.cli.role_launch_receipts import _message_binding_projection_reference
from agents_remember.mcp.registration.role_agents import register_role_agent_tools
from agents_remember.mcp.tools import role_agents as role_agent_payloads
from agents_remember.models.role_launcher import RoleDispatchRequest, RoleSelection
from agents_remember.models.task_document_ref import TaskDocumentRef
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from paseo_role_tools_test_support import (
    CALL_REFS,
    OTHER_LEAF,
    OTHER_MASTER,
    OTHER_SPRINT,
    ROLES,
    RoleToolsTestCase,
    binding,
    selection_of,
)
from test_paseo_launch import (
    LEAF_REF,
    MASTER_REF,
    ROLE_REFS,
    SPRINT_REF,
)


class MayStartRuleTests(unittest.TestCase):
    def test_each_role_may_start_exactly_the_roles_the_rule_names(self) -> None:
        allowed = {
            "architect": set(ROLES) - {"architect"},
            "orchestrator": {"manager", "worker", "reviewer", "curator", "investigator"},
            "manager": {"worker", "reviewer", "curator", "investigator"},
        }
        self.assertEqual({role: set(started) for role, started in MAY_START.items()}, allowed)
        for caller in ROLES:
            for role in ROLES:
                with self.subTest(caller=caller, starts=role):
                    refs = ROLE_REFS[role]
                    if role == "investigator" and caller in {"orchestrator", "manager"}:
                        refs = ROLE_REFS[caller]
                    selection = RoleSelection.model_validate({"role": role, **refs})
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
            self.start(self.architect, "investigator", request_id=taskless)
            before = self.state()
            result = self.refusal(
                self.start(self.architect, "reviewer", request_id=taskless), "launch-refused"
            )
            self.assertIn("another AR role selection (investigator)", result["detail"])
            self.assertEqual((self.compiled, self.state()), (2, before))


class RegisteredRoleToolTests(RoleToolsTestCase):
    """The two tools as a harness reaches them: registered, typed, and off the event loop."""

    def test_r71_mode_is_absent_from_tool_schema_and_rejected_by_the_http_route(self) -> None:
        async def schema() -> dict[str, Any]:
            server = FastMCP("no-mode")
            register_role_agent_tools(server, self.config)
            tools = await server.list_tools()
            return next(tool.inputSchema for tool in tools if tool.name == "role_start")

        self.assertNotIn("mode", asyncio.run(schema())["properties"])
        app = FastAPI()
        role_launch_routes.register_role_launch_routes(app, self.config)
        with TestClient(app) as client:
            for extra in ({"mode": "full-access"}, {"agentOverride": {"mode": "full-access"}}):
                response = client.post(
                    "/api/role-launch/dispatch",
                    json={"role": "architect", "requestId": str(uuid.uuid4()), **extra},
                )
                self.assertEqual(response.status_code, 422)
        self.assertFalse(self.runtime.launch_calls())

    def call(self, tool: str, arguments: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        async def invoke() -> tuple[int, dict[str, Any]]:
            server = FastMCP("role-tools-test")
            register_role_agent_tools(server, self.config)
            with tool_call_context(tool, arguments, config=self.config):
                _content, structured = await server.call_tool(tool, arguments)
            return threading.get_ident(), cast(dict[str, Any], structured)

        return asyncio.run(invoke())

    def test_the_tools_are_registered_under_their_names_and_answer_in_the_envelope(self) -> None:
        async def names() -> list[str]:
            server = FastMCP("role-tools-names")
            register_role_agent_tools(server, self.config)
            return [tool.name for tool in await server.list_tools()]

        self.assertEqual(
            asyncio.run(names()),
            ["role_start", "role_start_set", "user_settings_init", "role_message"],
        )
        leaf = {key: ref.model_dump(mode="json") for key, ref in selection_of("worker").items()}
        start = {"role": "worker", "request_id": str(uuid.uuid4()), **leaf}
        # This process was not started by a launch: a canonical selection starts with
        # no parent (MIK-R113 rule 6), whatever the settings hold.
        _loop, started = self.call("role_start", start)
        whole = json.loads(Path(started["log"]).read_text(encoding="utf-8"))
        self.assertEqual(
            (started["ok"], whole["status"]),
            (True, "running"),
        )
        created = next(p for command, p in self.runtime.calls if command == "agent-create")
        self.assertNotIn("parentAgentId", created)
        message = {"text": "hello", "agent_id": str(uuid.uuid4())}
        # MIK-R108: the answer carries no envelope field; the operation repeats the tool.
        _loop, refused = self.call("role_message", message)
        self.assertEqual((refused["ok"], refused["refusal"]), (False, "caller-has-no-binding"))
        _loop, refused = self.call("role_start_set", {"request_id": str(uuid.uuid4()), **leaf})
        self.assertEqual(
            (refused["ok"], refused["refusal"]),
            (False, "caller-has-no-binding"),
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
            loop_thread, started = self.call(
                "role_start", {"role": "investigator", "request_id": str(uuid.uuid4())}
            )
            whole = json.loads(Path(started["log"]).read_text(encoding="utf-8"))
            self.assertEqual(whole["agentId"], started["agentId"])
            self.assertEqual(
                (started["ok"], whole["status"], whole["parentAgentId"]),
                (True, "running", self.architect.agent_id),
            )
            investigator_request = RoleDispatchRequest.model_validate(
                {"role": "investigator", "requestId": whole["requestId"]}
            )
            self.assertEqual(
                self.receipt(investigator_request).get("parentAgentId"),
                self.architect.agent_id,
            )
            self.runtime.agents[started["agentId"]]["status"] = "idle"
            _loop, sent = self.call(
                "role_message", {"text": "hello", "agent_id": started["agentId"]}
            )
            self.assertEqual(sent["status"], "accepted")
            self.assertEqual(len(threads), 2)
            self.assertNotIn(loop_thread, threads)

    def unbound(self) -> None:
        """This process as a chat without a role has it: its tool server names no agent."""

        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop(AGENT_ID_VARIABLE, None)
        self.assertIsNone(read_agent_binding())

    def test_a_chat_without_a_role_starts_an_architect_through_the_registered_tool(self) -> None:
        """MIK-R113 rule 6: no binding and no document reference start a parentless Architect."""

        self.unbound()
        handovers: list[RoleHandoverRequest] = []

        def compiled(request: RoleHandoverRequest) -> dict[str, Any]:
            handovers.append(request)
            return self.compile_handover(request)

        self.replace(role_launch_preparation, "_compile_handover", compiled)
        request_id = uuid.uuid4()
        _loop, started = self.call(
            "role_start", {"role": "architect", "request_id": str(request_id)}
        )
        whole = json.loads(Path(started["log"]).read_text(encoding="utf-8"))
        self.assertEqual(whole["agentId"], started["agentId"])
        self.assertEqual(
            (started["ok"], whole["status"], whole["role"]), (True, "running", "architect")
        )
        self.assertIsNone(whole.get("parentAgentId"))
        # The launch record is compiled from this: a chat without a role started the agent.
        self.assertEqual(
            [(handover.chat_started, handover.started_by) for handover in handovers],
            [(True, None)],
        )
        request = RoleDispatchRequest.model_validate({"role": "architect", "requestId": request_id})
        receipt = self.receipt(request)
        self.assertEqual(receipt["agentId"], started["agentId"])
        self.assertEqual(
            receipt["selection"],
            {
                "role": "architect",
                "sprintDocumentRef": None,
                "masterDocumentRef": None,
                "taskDocumentRef": None,
            },
        )
        self.assertNotIn("parentAgentId", receipt)
        created = [p for command, p in self.runtime.calls if command == "agent-create"]
        self.assertEqual([payload["agentId"] for payload in created], [started["agentId"]])
        self.assertNotIn("parentAgentId", created[0])
        # The same chat may not message the agent it started; the developer speaks with it
        # in the dashboard.
        _loop, refused = self.call(
            "role_message", {"text": "hello", "agent_id": started["agentId"]}
        )
        self.assertEqual(
            (refused["ok"], refused["status"], refused["refusal"]),
            (False, "refused", "caller-has-no-binding"),
        )
        self.assertIn("the developer speaks with the agent in the dashboard", refused["nextAction"])
        self.assertEqual(self.received(started["agentId"]), [])

    def test_the_registered_tool_refuses_what_the_dashboard_refuses_in_the_same_words(self) -> None:
        """MIK-R113 rule 6: a selection the launcher's routes refuse, the tool refuses alike."""

        self.unbound()
        # The table is read before any document is, so the real resolver answers the
        # launcher's routes here in place of the fixture's stand-in.
        self.replace(role_launch_routes, "resolve_role_launch_context", resolve_role_launch_context)
        app = FastAPI()
        role_launch_routes.register_role_launch_routes(app, self.config)
        sprint = {"sprintDocumentRef": SPRINT_REF}
        master = {**sprint, "masterDocumentRef": MASTER_REF}
        leaf = {**master, "taskDocumentRef": LEAF_REF}
        cases: dict[str, tuple[str, dict[str, TaskDocumentRef]]] = {
            "architect does not accept a sprint selection.": ("architect", sprint),
            "manager does not accept a task selection.": ("manager", leaf),
            "worker requires a canonical task selection.": ("worker", master),
        }
        with TestClient(app) as client:
            for reason, (role, refs) in cases.items():
                with self.subTest(role=role, refs=sorted(refs)):
                    selection = {key: ref.model_dump(mode="json") for key, ref in refs.items()}
                    options = client.post(
                        "/api/role-launch/options", json={"role": role, **selection}
                    )
                    dispatch = client.post(
                        "/api/role-launch/dispatch",
                        json={"role": role, "requestId": str(uuid.uuid4()), **selection},
                    )
                    for answer in (options, dispatch):
                        self.assertEqual(
                            (answer.status_code, answer.json()), (409, {"detail": reason})
                        )
                    _loop, refused = self.call(
                        "role_start",
                        {
                            "role": role,
                            "request_id": str(uuid.uuid4()),
                            **{CALL_REFS[key]: ref for key, ref in selection.items()},
                        },
                    )
                    self.assertEqual(
                        (refused["ok"], refused["status"], refused["refusal"]),
                        (False, "refused", "launch-refused"),
                    )
                    self.assertEqual(refused["detail"], dispatch.json()["detail"])
        self.assertEqual(self.receipt_files(), [])
        self.assertEqual(self.runtime.launch_calls(), [])


if __name__ == "__main__":
    unittest.main()
