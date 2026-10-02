"""PNT-R07: the status table, the read-only refresh and revive, against a fake bridge."""

from __future__ import annotations

import copy
import json
import unittest
from typing import Any
from unittest.mock import patch

from agents_remember.cli import (
    orca_task_liveness,
    orca_task_preparation,
    orca_task_receipts,
    orca_task_routes,
    paseo_catalog,
    paseo_status,
)
from agents_remember.cli.paseo_catalog import forget_launcher_catalogs
from agents_remember.cli.paseo_status import SUMMARY_LIMIT, AgentReading, status_row
from agents_remember.models.orca_launcher import (
    OrcaDispatchRequest,
    OrcaLauncherOptionsRequest,
    OrcaResultRequest,
)
from fastapi import HTTPException
from test_paseo_launch import LEAF_REF, ROLE_REFS, PaseoLaunchTestCase, runtime_config

READ_COMMANDS = {"agent-state", "catalog"}
REPLIED = {"state": "replied", "text": "The first turn is done."}
NO_ROLLOUT = "Failed to resume Codex thread 01a0: no rollout found for thread id 01a0"


class StatusTestCase(PaseoLaunchTestCase):
    """A launched execution whose agent a test moves from state to state."""

    def setUp(self) -> None:
        super().setUp()
        # The launch records the leaf's task scope for the agent's readers, as the build does.
        self.replace(orca_task_preparation, "_ar_mcp_context", self.reader_context)

    @staticmethod
    def reader_context(_config: Any, context: Any, workspace: dict[str, str]) -> dict[str, Any]:
        if context.task is None:
            return {"scopeKind": "configured-projects"}
        scope = {
            "task_document_ref": LEAF_REF.model_dump(mode="json"),
            "contract_path": workspace["contractPath"],
        }
        return {"scopeKind": "canonical-leaf", "taskContext": scope}

    def launched(self, role: str = "worker", **state: Any) -> OrcaDispatchRequest:
        """Start a role agent and put its agent in the given state."""

        request = self.request(role)
        self.assertEqual(self.dispatch(request)[1]["status"], "running")
        self.agent_of(request).update(state)
        self.runtime.calls.clear()
        return request

    def saved(self, request: OrcaDispatchRequest) -> tuple[bytes, int, int]:
        """The receipt file as it is: its bytes, and the inode and time any rewrite would change."""

        path = self.receipt_path(request)
        stat = path.stat()
        return path.read_bytes(), stat.st_ino, stat.st_mtime_ns

    @staticmethod
    def revival(request: OrcaDispatchRequest) -> OrcaDispatchRequest:
        payload = request.model_dump(mode="json", by_alias=True, exclude_none=True)
        return OrcaDispatchRequest.model_validate({**payload, "action": "revive"})

    def revive(self, request: OrcaDispatchRequest) -> tuple[int, dict[str, Any]]:
        """Press Revive: the dispatch route with the revive action."""

        return self.dispatch(self.revival(request))

    def commands(self) -> list[str]:
        return [command for command, _payload in self.runtime.calls]

    def assert_row(
        self,
        public: dict[str, Any],
        request: OrcaDispatchRequest,
        *,
        status: str,
        detail: str,
        can_revive: bool,
    ) -> None:
        """The public execution and the receipt on disk both say what the row says."""

        receipt = self.receipt(request)
        for holder in (public, receipt):
            self.assertEqual(
                (holder["status"], holder["detail"], holder["canRevive"]),
                (status, detail, can_revive),
            )
        self.assertNotIn("hostUnreachable", public)
        self.assertEqual(public["execution"]["agentId"], receipt["agentId"])


class StatusTableTests(StatusTestCase):
    """PNT-R07 item 1: one case per row, in table order, through the result route."""

    def test_row_01_a_host_that_cannot_be_reached_leaves_the_receipt_as_it_is(self) -> None:
        request = self.launched(status="idle", lastTurn=REPLIED)
        self.assertEqual(self.refresh(request)["status"], "completed")
        saved = self.saved(request)
        no_answers = (
            "paseo_daemon_unreachable",
            "paseo_bridge_timeout",
            "paseo_bridge_invalid_reply",
            "paseo_bridge_unavailable",
            "paseo_agent_lookup_failed",
            "paseo_runtime_mismatch",
            "paseo_call_failed",
        )
        for code in no_answers:
            with self.subTest("the call fails", code=code):
                self.runtime.fail("agent-state", code, "the daemon does not answer")
                public = self.refresh(request)
                self.assertIs(public["hostUnreachable"], True)
                self.assertEqual(
                    public["hostUnreachableReason"], f"{code}: the daemon does not answer"
                )
                self.assertEqual((public["status"], public["canRevive"]), ("completed", False))
                self.assertEqual(public["result"], {"summary": REPLIED["text"]})
                self.assertEqual(self.saved(request), saved)
        agent = self.agent_of(request)
        state = self.runtime.state
        unreadable: dict[str, dict[str, Any]] = {
            "an agent under another id": {"id": "another-agent"},
            "no status": {"status": None},
            "an idle agent whose last turn was not read": {"lastTurn": None},
        }
        for label, change in unreadable.items():
            with (
                self.subTest("the answer cannot be read", answer=label),
                patch.object(
                    self.runtime, "state", lambda agent, change=change: {**state(agent), **change}
                ),
            ):
                public = self.refresh(request)
                self.assertIs(public["hostUnreachable"], True)
                self.assertTrue(public["hostUnreachableReason"])
                self.assertEqual(public["status"], "completed")
                self.assertEqual(self.saved(request), saved)
        self.assertEqual(agent["status"], "idle")

    def test_row_02_an_agent_the_host_does_not_have_is_stopped(self) -> None:
        request = self.launched()
        self.runtime.agents.clear()

        public = self.refresh(request)

        self.assert_row(
            public, request, status="stopped", detail="the host has no such agent", can_revive=False
        )
        self.assertEqual((public["canStart"], public["canRetry"]), (True, False))
        self.assertIn("terminalObservedAt", public)

    def test_row_03_an_archived_agent_is_stopped(self) -> None:
        request = self.launched(status="idle", lastTurn=REPLIED)
        self.refresh(request)
        self.agent_of(request).update(status="closed", archivedAt="2026-10-02T01:00:00.000Z")

        public = self.refresh(request)

        self.assert_row(public, request, status="stopped", detail="archived", can_revive=False)
        self.assertTrue(public["canStart"])
        # The reply of the last finished turn stays with the execution.
        self.assertEqual(public["result"], {"summary": REPLIED["text"]})

    def test_row_04_a_session_closed_under_an_open_turn_is_interrupted(self) -> None:
        request = self.launched(status="closed")

        public = self.refresh(request)

        self.assert_row(
            public,
            request,
            status="interrupted",
            detail="session closed while running",
            can_revive=True,
        )
        # An interrupted execution is still open: no second agent may start on the selection.
        self.assertEqual((public["canStart"], public["canRetry"]), (False, False))
        self.assertNotIn("terminalObservedAt", public)

    def test_row_05_a_closed_session_keeps_the_previous_status_and_is_revivable(self) -> None:
        closed_by = {
            "completed": {"status": "idle", "lastTurn": REPLIED},
            "failed": {"status": "error", "lastError": "The model refused the turn."},
            "stopped": {"status": "idle", "lastTurn": {"state": "unreplied"}},
            "interrupted": {"status": "closed"},
        }
        for previous, state in closed_by.items():
            with self.subTest(previous=previous):
                request = self.launched(**state)
                before = self.refresh(request)
                self.assertEqual(before["status"], previous)
                self.agent_of(request).update(status="closed", lastError=None)

                public = self.refresh(request)

                self.assert_row(
                    public, request, status=previous, detail="session closed", can_revive=True
                )
                # Nothing about the last turn can be read from a closed session: the summary stays.
                self.assertEqual(public.get("result"), before.get("result"))
                self.assertEqual(public["canStart"], previous in {"completed", "failed", "stopped"})

    def test_row_06_a_pending_permission_request_is_running_and_names_the_tool(self) -> None:
        request = self.launched(pendingPermissions=[{"name": "Bash", "kind": "tool"}])

        public = self.refresh(request)

        self.assert_row(
            public,
            request,
            status="running",
            detail="waiting for permission: Bash",
            can_revive=False,
        )

    def test_row_07_a_turn_in_progress_is_running(self) -> None:
        request = self.launched()

        public = self.refresh(request)

        self.assert_row(
            public, request, status="running", detail="a turn is in progress", can_revive=False
        )
        self.assertFalse(public["canStart"])

    def test_row_08_an_error_state_or_a_failed_last_turn_is_failed(self) -> None:
        message = "The 'gpt-z' model is not supported."
        document = {
            "type": "error",
            "status": 400,
            "error": {"type": "invalid_request_error", "message": message},
        }
        failures: dict[str, tuple[dict[str, Any], str]] = {
            "the agent is in an error state": ({"status": "error", "lastError": message}, message),
            "the last turn failed and the agent is idle": (
                {"status": "idle", "lastTurn": REPLIED, "lastError": message},
                f"last turn failed: {message}",
            ),
            "an error state without a message": (
                {"status": "error"},
                "the agent is in an error state",
            ),
            # After a restart the error text is gone; the runtime's error mark is not.
            "an idle agent marked with an error whose last turn left no reply": (
                {
                    "status": "idle",
                    "lastTurn": {"state": "unreplied"},
                    "attentionReason": "error",
                },
                "last turn failed",
            ),
            "an error document: its message is shown": (
                {"status": "error", "lastError": json.dumps(document)},
                message,
            ),
            "a text that only looks like a document is shown as it is": (
                {"status": "error", "lastError": '{"type": "error"'},
                '{"type": "error"',
            ),
        }
        for label, (state, detail) in failures.items():
            with self.subTest(label):
                request = self.launched(**state)

                public = self.refresh(request)

                self.assert_row(public, request, status="failed", detail=detail, can_revive=False)
                self.assertTrue(public["canStart"])
        with self.subTest(
            "the error mark alone, on a turn that ended with a reply, is not a failure"
        ):
            request = self.launched(status="idle", lastTurn=REPLIED, attentionReason="error")
            self.assertEqual(self.refresh(request)["status"], "completed")

    def test_row_09_a_cancelled_last_turn_is_stopped(self) -> None:
        # A cancelled turn carries the same mark as a finished one.
        request = self.launched(
            status="idle", lastTurn={"state": "unreplied"}, attentionReason="finished"
        )

        public = self.refresh(request)

        self.assert_row(
            public,
            request,
            status="stopped",
            detail="last turn cancelled; the agent is idle",
            can_revive=False,
        )
        self.assertNotIn("result", public)

    def test_row_10_idle_after_a_finished_turn_is_completed_with_the_final_text(self) -> None:
        final_text = "Bericht für den Worker · 役割 — " * 200
        self.assertGreater(len(final_text), SUMMARY_LIMIT)
        request = self.launched(status="idle", lastTurn={"state": "replied", "text": final_text})

        public = self.refresh(request)

        self.assert_row(
            public,
            request,
            status="completed",
            detail="last turn finished; the agent is idle",
            can_revive=False,
        )
        self.assertEqual(public["result"], {"summary": final_text[:3000]})
        self.assertEqual(self.receipt(request)["result"], public["result"])
        self.assertEqual((public["canStart"], public["canRetry"]), (True, False))

    def test_row_11_idle_without_a_turn_is_running(self) -> None:
        request = self.launched(status="idle", lastTurn={"state": "none"})

        public = self.refresh(request)

        self.assert_row(
            public, request, status="running", detail="started; no turn yet", can_revive=False
        )
        self.assertNotIn("result", public)

    def test_row_12_an_agent_that_is_starting_keeps_the_status_and_says_so(self) -> None:
        # Not a row of the packet's table: ruled for the runtime's `initializing`.
        for previous, state in (
            ("running", {}),
            ("completed", {"status": "idle", "lastTurn": REPLIED}),
            # Revive was on offer for the closed session; a session that is starting is not closed.
            ("interrupted", {"status": "closed"}),
        ):
            with self.subTest(previous=previous):
                request = self.launched(**state)
                before = self.refresh(request)
                self.assertEqual(before["status"], previous)
                self.agent_of(request)["status"] = "initializing"

                public = self.refresh(request)

                self.assert_row(
                    public,
                    request,
                    status=previous,
                    detail="the agent is starting",
                    can_revive=False,
                )
                self.assertEqual(public.get("result"), before.get("result"))
                # Only the detail changed; a second refresh of the same state writes nothing.
                saved = self.saved(request)
                self.assertEqual(self.refresh(request), public)
                self.assertEqual(self.saved(request), saved)
                self.runtime.agents.clear()
                self.receipt_path(request).unlink()

    def test_row_13_an_unrecognised_agent_state_keeps_the_status_and_names_it(self) -> None:
        # Not a row of the packet's table: ruled for a status word this build does not know.
        request = self.launched(status="idle", lastTurn=REPLIED)
        before = self.refresh(request)
        self.agent_of(request)["status"] = "hibernating"

        public = self.refresh(request)

        self.assert_row(
            public,
            request,
            status="completed",
            detail="unrecognised agent state: hibernating",
            can_revive=False,
        )
        self.assertEqual(public["result"], before["result"])
        self.assertEqual(public["canStart"], before["canStart"])
        with self.subTest("an execution that was revivable is not while the state is unknown"):
            closed = self.launched("manager", status="closed")
            self.assertIs(self.refresh(closed)["canRevive"], True)
            self.agent_of(closed)["status"] = "hibernating"
            self.assert_row(
                self.refresh(closed),
                closed,
                status="interrupted",
                detail="unrecognised agent state: hibernating",
                can_revive=False,
            )

    def test_the_first_matching_row_decides(self) -> None:
        with self.subTest("closed and archived together is row 3, not row 4"):
            archived = {"status": "closed", "archivedAt": "2026-10-02T01:00:00.000Z"}
            request = self.launched(**archived)
            public = self.refresh(request)
            self.assert_row(public, request, status="stopped", detail="archived", can_revive=False)
        # Each reading also satisfies the condition of every later row it is listed before.
        idle_replied: dict[str, Any] = {"lifecycle": "idle", "last_turn": "replied"}
        ordered: list[tuple[int, AgentReading]] = [
            (1, AgentReading(reachable=False, found=False, archived=True, lifecycle="closed")),
            (2, AgentReading(found=False, archived=True, lifecycle="closed")),
            (3, AgentReading(archived=True, lifecycle="closed", pending_permission="Bash")),
            (4, AgentReading(lifecycle="closed", pending_permission="Bash", turn_active=True)),
            (6, AgentReading(lifecycle="running", pending_permission="Bash", turn_active=True)),
            (7, AgentReading(lifecycle="running", turn_active=True, error="failed earlier")),
            (8, AgentReading(error="the turn failed", **idle_replied)),
            (10, AgentReading(**idle_replied)),
        ]
        for number, reading in ordered:
            with self.subTest(row=number):
                self.assertEqual(status_row(reading, "running").number, number)
        self.assertEqual(status_row(AgentReading(lifecycle="closed"), "completed").number, 5)
        # The two ruled rows come last: a starting agent with a pending permission is row 6.
        starting = AgentReading(lifecycle="initializing", pending_permission="Bash")
        self.assertEqual(status_row(starting, "running").number, 6)
        self.assertEqual(status_row(AgentReading(lifecycle="initializing"), "running").number, 12)


class RefreshTests(StatusTestCase):
    def test_a_refresh_issues_only_read_commands_and_changes_no_agent(self) -> None:
        states: dict[str, dict[str, Any]] = {
            "a closed session": {"status": "closed"},
            "an archived agent": {"status": "closed", "archivedAt": "2026-10-02T01:00:00.000Z"},
            "an idle agent": {"status": "idle", "lastTurn": REPLIED},
            "a running agent": {"status": "running"},
            "an agent waiting for a permission": {"pendingPermissions": [{"name": "Bash"}]},
        }
        options = OrcaLauncherOptionsRequest.model_validate(
            {"role": "worker", **ROLE_REFS["worker"]}
        )
        for label, state in states.items():
            with self.subTest(label):
                request = self.launched(**state)
                agents = copy.deepcopy(self.runtime.agents)

                # Result, the options load, and a repeat of the resolved request all refresh.
                self.refresh(request)
                orca_task_routes._orca_options_endpoint(self.config, options)
                written = self.saved(request)
                self.dispatch(request)

                # A refresh that finds nothing new does not write the receipt again.
                self.assertEqual(self.saved(request), written)
                self.assertEqual(self.commands().count("agent-state"), 3)
                self.assertLessEqual(set(self.commands()), READ_COMMANDS)
                # Nothing was sent, resumed or un-archived: the runtime holds what it held.
                self.assertEqual(self.runtime.agents, agents)
                self.runtime.agents.clear()
                self.receipt_path(request).unlink()

    def test_a_closed_execution_follows_the_agent_into_a_further_turn(self) -> None:
        request = self.launched()
        agent = self.agent_of(request)
        agent_id = agent["id"]
        endings: dict[str, dict[str, Any]] = {
            "completed": {"status": "idle", "lastTurn": REPLIED},
            "failed": {"status": "error", "lastError": "The turn failed."},
            "stopped": {"status": "idle", "lastTurn": {"state": "unreplied"}},
        }
        for closed, state in endings.items():
            with self.subTest(closed=closed):
                agent.update(state)
                self.assertEqual(self.refresh(request)["status"], closed)
                # A follow-up message in the chat starts another turn of the same agent.
                agent.update(status="running", lastError=None)
                again = self.refresh(request)
                self.assertEqual(
                    (again["status"], again["detail"], again["canStart"]),
                    ("running", "a turn is in progress", False),
                )
        agent.update(status="idle", lastTurn={"state": "replied", "text": "The second reply."})
        done = self.refresh(request)
        self.assertEqual(
            (done["status"], done["result"]["summary"]), ("completed", "The second reply.")
        )
        self.assertEqual(done["execution"]["agentId"], agent_id)
        self.assertEqual(list(self.runtime.agents), [agent_id])
        self.assertLessEqual(set(self.commands()), READ_COMMANDS)

    def test_a_refresh_holds_the_launch_lock_for_one_bridge_call_only(self) -> None:
        request = self.launched(status="idle", lastTurn=REPLIED)
        held: list[bool] = []
        read_agent = orca_task_liveness.read_agent

        def reading(*args: Any) -> AgentReading:
            held.append(orca_task_routes._DISPATCH_LOCK.locked())
            return read_agent(*args)

        with patch.object(orca_task_liveness, "read_agent", side_effect=reading):
            self.refresh(request)
            options = OrcaLauncherOptionsRequest.model_validate(
                {"role": "worker", **ROLE_REFS["worker"]}
            )
            orca_task_routes._orca_options_endpoint(self.config, options)
        self.assertEqual(held, [True, True])
        self.assertEqual(self.commands().count("agent-state"), 2)
        self.assertFalse(orca_task_routes._DISPATCH_LOCK.locked())
        with self.subTest("the options route loads the catalog before it takes the lock"):
            forget_launcher_catalogs()
            self.runtime.calls.clear()
            during: list[tuple[str, bool]] = []
            bridge = self.runtime.__call__

            def watched(config: Any, command: str, payload: dict[str, Any]) -> dict[str, Any]:
                during.append((command, orca_task_routes._DISPATCH_LOCK.locked()))
                return bridge(config, command, payload)

            self.replace(paseo_catalog, "bridge_call", watched)
            self.replace(paseo_status, "bridge_call", watched)
            orca_task_routes._orca_options_endpoint(self.config, options)
            self.assertEqual(during, [("catalog", False), ("agent-state", True)])
            self.assertFalse(orca_task_routes._DISPATCH_LOCK.locked())
            # A failed catalog load leaves the lock free as well.
            forget_launcher_catalogs()
            self.runtime.fail("catalog", "paseo_daemon_unreachable")
            with self.assertRaises(HTTPException):
                orca_task_routes._orca_options_endpoint(self.config, options)
            self.assertFalse(orca_task_routes._DISPATCH_LOCK.locked())
        # While another launch or check holds the lock, a refresh and the options route refuse
        # and call nothing. Both mark the refusal as a launch in progress, which the launcher
        # shows as such and not as an error; a Start is refused as before.
        forget_launcher_catalogs()
        self.runtime.calls.clear()
        result = OrcaResultRequest.model_validate(
            {"role": "worker", "requestId": request.request_id, **ROLE_REFS["worker"]}
        )
        with orca_task_routes._DISPATCH_LOCK:
            answers = [
                orca_task_routes._orca_result_endpoint(self.config, result),
                orca_task_routes._orca_options_endpoint(self.config, options),
            ]
            with self.assertRaises(orca_task_routes.LaunchLockBusy):
                orca_task_routes._orca_dispatch_endpoint(self.config, self.request("worker"))
            self.assertTrue(orca_task_routes._DISPATCH_LOCK.locked())
        for answer in answers:
            self.assertEqual(
                (answer.status_code, json.loads(bytes(answer.body))),
                (
                    409,
                    {
                        "detail": "An AR-to-Orca launch or result check is already in progress.",
                        "launchInProgress": True,
                    },
                ),
            )
        self.assertEqual(self.commands(), ["catalog"])
        self.assertFalse(orca_task_routes._DISPATCH_LOCK.locked())

    def test_a_launch_that_is_unresolved_or_rejected_has_no_agent_to_read(self) -> None:
        with self.subTest("an unresolved launch stays retryable"):
            self.runtime.fail("agent-create", "paseo_bridge_timeout", after_effect=True)
            unresolved = self.request("worker")
            self.assertEqual(self.dispatch(unresolved)[1]["status"], "unknown")
            saved = self.saved(unresolved)
            self.runtime.calls.clear()
            public = self.refresh(unresolved)
            self.assertEqual((public["status"], public["canRetry"]), ("unknown", True))
            self.assertEqual((self.runtime.calls, self.saved(unresolved)), ([], saved))
        with self.subTest("a rejected launch created no agent"):
            self.runtime.fail("agent-create", "paseo_call_failed", "refused")
            rejected = self.request("orchestrator")
            self.assertEqual(self.dispatch(rejected)[1]["status"], "rejected")
            saved = self.saved(rejected)
            self.runtime.calls.clear()
            self.assertEqual(self.refresh(rejected)["status"], "rejected")
            self.assertEqual((self.runtime.calls, self.saved(rejected)), ([], saved))
            with self.assertRaises(HTTPException) as nothing:
                self.revive(rejected)
            self.assertIn("nothing to revive", str(nothing.exception.detail))

    def test_the_options_route_marks_an_unreachable_host_and_keeps_the_receipt(self) -> None:
        request = self.launched(status="idle", lastTurn=REPLIED)
        options = OrcaLauncherOptionsRequest.model_validate(
            {"role": "worker", **ROLE_REFS["worker"]}
        )
        first = json.loads(
            bytes(orca_task_routes._orca_options_endpoint(self.config, options).body)
        )
        self.assertEqual(first["execution"]["status"], "completed")
        saved = self.saved(request)
        self.runtime.calls.clear()
        # The catalog is cached; the daemon is gone.
        self.runtime.fail("catalog", "paseo_daemon_unreachable")
        self.runtime.fail("agent-state", "paseo_daemon_unreachable", "The daemon is down.")

        response = orca_task_routes._orca_options_endpoint(self.config, options)

        answered = json.loads(bytes(response.body))
        self.assertEqual(self.commands(), ["agent-state"])
        self.assertEqual(len(answered["agents"]), 2)
        self.assertIs(answered["execution"]["hostUnreachable"], True)
        self.assertEqual(answered["execution"]["status"], "completed")
        self.assertEqual(self.saved(request), saved)

    def test_a_new_start_applies_the_open_execution_rule_to_the_agents_current_state(self) -> None:
        with self.subTest("the saved status is running, the agent has finished"):
            previous = self.launched(status="idle", lastTurn=REPLIED)
            old_agent = self.receipt(previous)["agentId"]
            self.assertEqual(self.receipt(previous)["status"], "running")

            status, public = self.dispatch(self.request("worker"))

            self.assertEqual((status, public["status"]), (200, "running"))
            self.assertEqual(
                self.commands(), ["agent-state", "agent-archive", "workspace-open", "agent-create"]
            )
            self.assertIsNotNone(self.runtime.agents[old_agent]["archivedAt"])
            self.assertNotEqual(public["execution"]["agentId"], old_agent)
            history = self.receipt_path(previous).parent / "history"
            archived = json.loads(
                (history / f"{previous.request_id}.json").read_text(encoding="utf-8")
            )
            self.assertEqual((archived["status"], archived["agentId"]), ("completed", old_agent))
        with self.subTest("the saved status is completed, the agent runs a further turn"):
            previous = self.launched("manager", status="idle", lastTurn=REPLIED)
            self.assertEqual(self.refresh(previous)["status"], "completed")
            self.agent_of(previous).update(status="running")
            self.runtime.calls.clear()

            error = self.refused(self.request("manager"))

            self.assertEqual(error.status_code, 409)
            self.assertIn(
                f"open execution (request {previous.request_id}, status running)",
                str(error.detail),
            )
            self.assertEqual(self.commands(), ["agent-state"])
            self.assertEqual(self.receipt(previous)["status"], "running")

    def test_a_new_start_is_refused_while_the_execution_s_agent_cannot_be_read(self) -> None:
        previous = self.launched(status="idle", lastTurn=REPLIED)
        self.assertEqual(self.refresh(previous)["status"], "completed")
        saved = self.saved(previous)
        bindings = self.config.coordination_root / "notes/reports/paseo-native-executions"
        binding_files = sorted((bindings / "message-bindings").glob("*.json"))
        # The saved status is closed, but the agent may be in a further turn: nobody can tell.
        self.agent_of(previous)["status"] = "running"
        self.runtime.calls.clear()
        self.runtime.fail("agent-state", "paseo_bridge_timeout", "The bridge call ran out of time.")

        status, body = self.dispatch(self.request("worker"))

        self.assertEqual(status, 409)
        self.assertIs(body["hostUnreachable"], True)
        self.assertIn("the open-execution rule cannot be applied", body["detail"])
        self.assertIn(f"request {previous.request_id}, last known status completed", body["detail"])
        self.assertIn("paseo_bridge_timeout: The bridge call ran out of time.", body["detail"])
        # Nothing was archived or created, in the runtime or on disk.
        self.assertEqual(self.commands(), ["agent-state"])
        self.assertEqual(self.saved(previous), saved)
        self.assertFalse((self.receipt_path(previous).parent / "history").exists())
        self.assertEqual(sorted((bindings / "message-bindings").glob("*.json")), binding_files)
        self.assertEqual(len(self.runtime.agents), 1)
        with self.subTest("a repeat of the saved request still converges on its execution"):
            # The refusal is for a new request id only: the saved one is answered as last known.
            self.runtime.calls.clear()
            self.runtime.fail(
                "agent-state", "paseo_bridge_timeout", "The bridge call ran out of time."
            )

            status, repeated = self.dispatch(previous)

            self.assertEqual(status, 200)
            self.assertEqual(
                repeated,
                {
                    **orca_task_receipts._public_execution(self.receipt(previous)),
                    "hostUnreachable": True,
                    "hostUnreachableReason": "paseo_bridge_timeout: The bridge call ran out of time.",
                },
            )
            self.assertEqual(repeated["status"], "completed")
            self.assertEqual(self.commands(), ["agent-state"])
            self.assertEqual(self.saved(previous), saved)
        with self.subTest("once the host answers, the rule is applied to what the agent is doing"):
            error = self.refused(self.request("worker"))
            self.assertIn("status running", str(error.detail))

    def test_without_a_configured_runtime_refresh_and_revive_refuse_naming_that(self) -> None:
        request = self.launched(status="closed")
        saved = self.saved(request)
        unconfigured = runtime_config(self.root, configured=False)

        with self.assertRaises(HTTPException) as refreshed:
            self.refresh(request, unconfigured)
        revived = self.refused(self.revival(request), config=unconfigured)

        for error, status_code in ((refreshed.exception, 503), (revived, 409)):
            self.assertEqual(error.status_code, status_code)
            self.assertTrue(str(error.detail).startswith("no Paseo runtime configured: "))
        self.assertEqual((self.runtime.calls, self.saved(request)), ([], saved))


class ReviveTests(StatusTestCase):
    def test_revive_resumes_the_recorded_agent_without_a_message_and_creates_none(self) -> None:
        sessions: dict[str, tuple[str, dict[str, Any], str]] = {
            # role: the status before the session closed, the state after the resume, the row.
            "architect": ("completed", {"lastTurn": REPLIED}, "completed"),
            "worker": ("running", {"lastTurn": {"state": "unreplied"}}, "stopped"),
        }
        for role, (previous, resumed_state, after) in sessions.items():
            with self.subTest(role=role):
                request = self.launched(role, status="idle", **resumed_state)
                if previous == "completed":
                    self.refresh(request)
                agent = self.agent_of(request)
                agent["status"] = "closed"
                closed = self.refresh(request)
                self.assertIs(closed["canRevive"], True)
                starts = len(self.enclosures.start_calls)
                agents = list(self.runtime.agents)
                self.runtime.calls.clear()

                status, public = self.revive(request)

                self.assertEqual(
                    (status, public["status"], public["canRevive"]), (200, after, False)
                )
                # One read, then the resume of exactly the recorded agent; no message, no creation.
                self.assertEqual(
                    self.runtime.calls,
                    [
                        ("agent-state", {"agentId": agent["id"]}),
                        ("agent-resume", {"agentId": agent["id"]}),
                    ],
                )
                self.assertEqual(agent["status"], "idle")
                self.assertEqual(list(self.runtime.agents), agents)
                self.assertEqual(public["execution"]["agentId"], agent["id"])
                self.assertEqual(self.receipt(request)["revivedAt"], public["revivedAt"])
                # The scope check of a leaf-bound role creates no enclosure.
                self.assertEqual(len(self.enclosures.start_calls), starts)

    def test_revive_on_an_open_session_changes_nothing_and_returns_the_refreshed_execution(
        self,
    ) -> None:
        request = self.launched(status="closed")
        self.agent_of(request)["lastTurn"] = REPLIED
        self.assertEqual(self.revive(request)[1]["status"], "completed")
        saved = self.saved(request)
        agents = copy.deepcopy(self.runtime.agents)
        self.runtime.calls.clear()

        status, again = self.revive(request)

        self.assertEqual(
            (status, again), (200, orca_task_receipts._public_execution(self.receipt(request)))
        )
        self.assertEqual(self.commands(), ["agent-state"])
        self.assertEqual((self.saved(request), self.runtime.agents), (saved, agents))
        with self.subTest("an agent that is gone or archived is not revivable"):
            self.agent_of(request).update(status="closed", archivedAt="2026-10-02T01:00:00.000Z")
            self.runtime.calls.clear()
            error = self.refused(self.revival(request))
            self.assertEqual(
                (error.status_code, error.detail),
                (409, "This execution cannot be revived: archived."),
            )
            self.assertEqual(self.commands(), ["agent-state"])
            self.assertIsNotNone(self.agent_of(request)["archivedAt"])

    def test_revive_of_a_leaf_bound_role_refuses_a_changed_task_scope_with_both_values(
        self,
    ) -> None:
        request = self.launched(status="closed")
        receipt = self.receipt(request)
        current = receipt["arMcpContext"]["taskContext"]
        self.assertEqual(current["task_document_ref"], LEAF_REF.model_dump(mode="json"))
        recorded_elsewhere = {**current, "contract_path": "/enclosures/moved/contract.json"}
        changed: dict[str, tuple[dict[str, Any], str]] = {
            "the recorded contract path is another": (
                {"scopeKind": "canonical-leaf", "taskContext": recorded_elsewhere},
                json.dumps(recorded_elsewhere, sort_keys=True),
            ),
            "the launch recorded no task scope": ({"scopeKind": "canonical-leaf"}, "null"),
        }
        for label, (reader_context, recorded) in changed.items():
            with self.subTest(label):
                receipt["arMcpContext"] = reader_context
                orca_task_receipts._write_receipt(self.receipt_path(request), receipt)
                saved = self.saved(request)

                error = self.refused(self.revival(request))

                self.assertEqual(error.status_code, 409)
                self.assertIn(f"Recorded at launch: {recorded}.", str(error.detail))
                self.assertIn(
                    f"Computed now: {json.dumps(current, sort_keys=True)}.", str(error.detail)
                )
                self.assertEqual((self.runtime.calls, self.saved(request)), ([], saved))
                self.assertEqual(self.agent_of(request)["status"], "closed")
        self.assertEqual(len(self.enclosures.start_calls), 1)

    def test_an_agent_the_host_cannot_resume_is_failed_and_stays_unrevivable(self) -> None:
        request = self.launched(status="closed", resumeRefusal=NO_ROLLOUT)
        old_agent = self.receipt(request)["agentId"]
        self.assertEqual(self.refresh(request)["status"], "interrupted")

        status, public = self.revive(request)

        self.assertEqual(status, 200)
        self.assert_row(public, request, status="failed", detail=NO_ROLLOUT, can_revive=False)
        self.assertEqual(self.receipt(request)["resumeRefused"]["reason"], NO_ROLLOUT)
        self.assertEqual(list(self.runtime.agents), [old_agent])
        with self.subTest("a later refresh keeps the recorded reason"):
            again = self.refresh(request)
            self.assert_row(again, request, status="failed", detail=NO_ROLLOUT, can_revive=False)
        with self.subTest("a second revive is refused without another resume"):
            self.runtime.calls.clear()
            with self.assertRaises(HTTPException) as refused:
                self.revive(request)
            self.assertIn(NO_ROLLOUT, str(refused.exception.detail))
            self.assertEqual(self.commands(), ["agent-state"])
        with self.subTest("a new agent comes only from a new start, which archives this one"):
            self.runtime.calls.clear()
            started = self.dispatch(self.request("worker"))[1]
            self.assertEqual(
                self.commands(), ["agent-state", "agent-archive", "workspace-open", "agent-create"]
            )
            self.assertNotEqual(started["execution"]["agentId"], old_agent)
            self.assertIsNotNone(self.runtime.agents[old_agent]["archivedAt"])

    def test_a_recorded_refusal_ends_once_the_session_is_seen_open(self) -> None:
        request = self.launched(status="closed", resumeRefusal=NO_ROLLOUT)
        self.assertEqual(self.revive(request)[1]["status"], "failed")
        # The developer opened the agent in the chat and it answered; later its session closed.
        agent = self.agent_of(request)
        agent.update(status="idle", lastTurn=REPLIED, resumeRefusal=None)
        self.assertEqual(self.refresh(request)["status"], "completed")
        self.assertNotIn("resumeRefused", self.receipt(request))
        agent["status"] = "closed"

        public = self.refresh(request)

        self.assert_row(
            public, request, status="completed", detail="session closed", can_revive=True
        )

    def test_revive_without_a_reachable_host_refuses_and_leaves_the_receipt(self) -> None:
        request = self.launched(status="closed")
        for command in ("agent-state", "agent-resume"):
            with self.subTest(unreachable_at=command):
                saved = self.saved(request)
                self.runtime.fail(command, "paseo_daemon_unreachable", "The daemon is down.")

                status, body = self.revive(request)

                self.assertEqual(status, 409)
                self.assertIs(body["hostUnreachable"], True)
                self.assertIn("paseo_daemon_unreachable: The daemon is down.", body["detail"])
                self.assertNotIn("revivedAt", self.receipt(request))
                self.assertEqual(self.agent_of(request)["status"], "closed")
                if command == "agent-state":
                    self.assertIn("the execution is unchanged", body["detail"])
                    self.assertEqual(self.saved(request), saved)
                else:
                    # The read was answered and its row is written; only the resume is in doubt,
                    # and the answer does not call the execution unchanged.
                    self.assertIn(
                        "the resume was not confirmed; refresh to see the agent's state",
                        body["detail"],
                    )
                    self.assertNotIn("unchanged", body["detail"])
                    self.assertEqual(self.receipt(request)["status"], "interrupted")
                    self.assertIs(self.receipt(request)["canRevive"], True)

    def test_a_session_opened_between_the_read_and_the_resume_is_not_recorded_as_revived(
        self,
    ) -> None:
        request = self.launched(status="closed", lastTurn=REPLIED)
        agent = self.agent_of(request)
        read = self.runtime._agent_state

        def read_then_opened_in_the_chat(payload: dict[str, Any]) -> dict[str, Any]:
            reply = read(payload)
            agent["status"] = "idle"
            return reply

        self.runtime._agent_state = read_then_opened_in_the_chat  # type: ignore[method-assign]

        status, public = self.revive(request)

        self.assertEqual((status, public["status"], public["canRevive"]), (200, "completed", False))
        self.assertEqual(self.commands(), ["agent-state", "agent-resume"])
        # The runtime resumed nothing for this call, so no revival is recorded.
        self.assertNotIn("revivedAt", public)
        self.assertNotIn("revivedAt", self.receipt(request))


if __name__ == "__main__":
    unittest.main()
