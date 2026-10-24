"""The launch's outcomes, refusals under a repeat and reply checks, against the fake bridge."""

from __future__ import annotations

import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from unittest.mock import patch

from agents_remember.cli import (
    role_launch_receipts,
    role_launch_routes,
)
from agents_remember.models.role_launcher import (
    RoleDispatchRequest,
)
from test_paseo_launch import (
    NO_ANSWER_CODES,
    PaseoLaunchTestCase,
)


class LaunchOutcomeTests(PaseoLaunchTestCase):
    def test_each_answer_of_the_runtime_is_written_to_the_receipt(self) -> None:
        with self.subTest("created: what the runtime applied is recorded"):
            request = self.request("architect", agentId="eve")
            status, public = self.dispatch(request)
            created = self.runtime.launch_calls()[-1][1]
            self.assertEqual((status, public["status"]), (200, "running"))
            self.assertNotIn("model", created)
            self.assertNotIn("thinkingOptionId", created)
            self.assertEqual(self.receipt(request)["agent"], {"id": "eve", "model": "eve-default"})
            # The runtime reports that this provider takes no tool servers: the definition is
            # left out, the receipt says so, and note and artifact line are sent all the same.
            self.assertNotIn("mcpServers", created)
            self.assertEqual(
                self.receipt(request)["toolServer"],
                {
                    "name": "agents-remember-task",
                    "applied": False,
                    "detail": "tool server not applied: not supported by provider",
                },
            )
            self.assertEqual(created["systemPrompt"], self.receipt(request)["recoveryNote"])
            self.assertEqual(created["prompt"], self.first_message(request))
            self.assertEqual(
                (public["canStart"], public["canRetry"], public["canRevive"]), (True, False, False)
            )

        refusals: dict[str, tuple[str, str, int]] = {
            "the creation is refused": ("agent-create", "Provider codex is not configured", 2),
            "the workspace is refused": ("workspace-open", "Directory not found: /gone", 1),
        }
        for label, (command, message, calls) in refusals.items():
            with self.subTest(label):
                self.runtime.calls.clear()
                self.runtime.fail(command, "paseo_call_failed", message)
                request = self.request("orchestrator", uuid.uuid4())
                status, public = self.dispatch(request)
                receipt = self.receipt(request)
                self.assertEqual((status, public["status"]), (502, "rejected"))
                self.assertIn(message, public["detail"])
                self.assertEqual(len(self.runtime.launch_calls()), calls)
                self.assertEqual(str(uuid.UUID(receipt["agentId"])), receipt["agentId"])
                self.assertNotIn(receipt["agentId"], self.runtime.agents)
                self.assertIs(receipt["hostAgentExists"], False)
                self.assertEqual((receipt["execution"], public["execution"]), ({}, {}))
                self.assertNotIn("replayRequest", receipt)
                self.assertEqual((public["canStart"], public["canRetry"]), (True, False))

        with self.subTest("a workspace of another folder is not used"):
            self.runtime.calls.clear()
            self.runtime.opened_directory = (self.root / "elsewhere").as_posix()
            status, public = self.dispatch(self.request("architect"))
            self.runtime.opened_directory = None
            self.assertEqual((status, public["status"]), (502, "rejected"))
            self.assertEqual([call[0] for call in self.runtime.launch_calls()], ["workspace-open"])

        with self.subTest("an agent that exists although its creation reported an error"):
            self.runtime.creation_error = "the first prompt was not accepted"
            request = self.request("architect")
            status, public = self.dispatch(request)
            self.assertEqual((status, public["status"]), (200, "running"))
            self.assertIn("the first prompt was not accepted", public["warning"])
            self.assertIn(self.receipt(request)["agentId"], self.runtime.agents)

    def test_a_launch_without_a_usable_answer_stays_retryable(self) -> None:
        no_answers = [("agent-create", code) for code in NO_ANSWER_CODES]
        for command, code in [*no_answers, ("workspace-open", "paseo_daemon_unreachable")]:
            with self.subTest("no usable answer", command=command, code=code):
                self.runtime.fail(command, code)
                request = self.request("architect", agentId="codex", modelId="gpt-a")
                status, public = self.dispatch(request)
                receipt = self.receipt(request)
                self.assertEqual((status, public["status"]), (202, "unknown"))
                self.assertIn(code, public["detail"])
                self.assertEqual(receipt["replayRequest"]["agent"]["agentId"], receipt["agentId"])
                self.assertEqual(
                    receipt["replayRequest"]["agent"]["prompt"], self.first_message(request)
                )
                self.assertNotIn("hostAgentExists", receipt)
                self.assertEqual((public["canStart"], public["canRetry"]), (False, True))
                self.assertEqual(
                    public["retryPayload"],
                    {
                        "role": "architect",
                        "agentOverride": {"agentId": "codex", "modelId": "gpt-a"},
                    },
                )

    def test_a_300_000_byte_first_message_launches_as_data(self) -> None:
        filler = "Rollenauftrag für den Worker · 役割 — "
        self.prompt = filler * (300_000 // len(filler.encode("utf-8")))
        self.prompt += "x" * (300_000 - len(self.prompt.encode("utf-8")))
        self.assertEqual(len(self.prompt.encode("utf-8")), 300_000)
        request = self.request("worker")
        self.runtime.fail("agent-create", "paseo_bridge_timeout")

        self.assertEqual(self.dispatch(request)[0], 202)
        receipt = self.receipt(request)
        self.assertEqual(receipt["replayRequest"]["agent"]["prompt"], self.first_message(request))
        self.assertEqual(receipt["handoverArtifact"]["bytes"], 300_000)
        self.assertEqual(
            Path(receipt["handoverArtifact"]["path"]).read_bytes(), self.prompt.encode("utf-8")
        )
        status, public = self.dispatch(request)

        self.assertEqual((status, public["status"]), (200, "running"))
        first, second = (call[1] for call in self.runtime.calls if call[0] == "agent-create")
        self.assertEqual(first["prompt"], self.first_message(request))
        self.assertEqual(second, first)


class RepeatAndConflictTests(PaseoLaunchTestCase):
    def test_a_repeated_request_converges_on_the_same_agent(self) -> None:
        with self.subTest("the answer was lost after the agent was created"):
            request = self.request("worker")
            self.runtime.fail("agent-create", "paseo_bridge_timeout", after_effect=True)
            self.assertEqual(self.dispatch(request)[1]["status"], "unknown")
            agent_id = self.receipt(request)["agentId"]
            self.assertEqual(list(self.runtime.agents), [agent_id])

            status, public = self.dispatch(request)

            self.assertEqual((status, public["status"]), (200, "running"))
            self.assertEqual(public["execution"]["agentId"], agent_id)
            self.assertEqual(list(self.runtime.agents), [agent_id])
            first, second = (call[1] for call in self.runtime.calls if call[0] == "agent-create")
            self.assertEqual(second, first)
            self.assertEqual(len(self.enclosures.start_calls), 1)
            # A resolved request is answered by a refresh of its execution: a read, no launch.
            launches = len(self.runtime.launch_calls())
            status, again = self.dispatch(request)
            self.assertEqual((status, again["status"]), (200, "running"))
            self.assertEqual(again["execution"], public["execution"])
            self.assertEqual(self.runtime.calls[-1], ("agent-state", {"agentId": agent_id}))
            self.assertEqual(len(self.runtime.launch_calls()), launches)
        with self.subTest("the process ended between the receipt and the call"):
            request = self.request("architect")
            crash = RuntimeError("the backend process ended here")
            with (
                patch.object(role_launch_receipts, "run_launch_call", side_effect=crash),
                self.assertRaises(RuntimeError),
            ):
                self.dispatch(request)
            saved = self.receipt(request)
            self.assertEqual(saved["status"], "starting")
            self.assertNotIn(saved["agentId"], self.runtime.agents)
            self.assertTrue(role_launch_receipts._public_execution(saved)["canRetry"])

            status, public = self.dispatch(request)

            self.assertEqual((status, public["status"]), (200, "running"))
            self.assertEqual(public["execution"]["agentId"], saved["agentId"])
            self.assertEqual(len(self.runtime.agents), 2)

    def test_a_conflicting_or_second_request_is_refused_and_nothing_changes(self) -> None:
        running = self.request("worker")
        unresolved = self.request("manager")
        self.dispatch(running)
        self.runtime.fail("agent-create", "paseo_daemon_unreachable")
        self.dispatch(unresolved)
        # The receipt already says what the agent is doing, so a further refresh changes nothing.
        self.assertEqual(self.refresh(running)["status"], "running")
        files = self.receipt_files()
        saved = {path: (self.root / path).read_bytes() for path in files}
        calls = len(self.runtime.calls)
        refused: dict[str, tuple[Any, str]] = {
            "same request id, another override": (
                self.request("worker", running.request_id, agentId="codex", effortId="high"),
                "already bound to different AR task or agent-selection content",
            ),
            "same request id, another selection": (
                self.request("reviewer", running.request_id),
                f"Request id {running.request_id} is already bound to another AR role selection "
                "(worker); nothing was prepared for this request.",
            ),
            "new request id while the execution runs": (
                self.request("worker"),
                f"held by agent {self.receipt(running)['agentId']}",
            ),
            "new request id while the execution is unresolved": (
                self.request("manager"),
                f"open execution (request {unresolved.request_id}, status unknown)",
            ),
        }
        for label, (request, reason) in refused.items():
            with self.subTest(label):
                error = self.refused(request)
                self.assertEqual(error.status_code, 409)
                self.assertIn(reason, str(error.detail))
        self.assertEqual(self.receipt_files(), files)
        self.assertEqual({path: (self.root / path).read_bytes() for path in files}, saved)
        # The one call a refusal makes is the read of the open execution's agent.
        self.assertEqual(
            self.runtime.calls[calls:],
            [("agent-state", {"agentId": self.receipt(running)["agentId"]})],
        )
        self.assertEqual(len(self.runtime.agents), 1)

    def test_a_new_start_on_a_closed_leaf_execution_is_refused_naming_the_holder(
        self,
    ) -> None:
        # Rule 9: a leaf seat is held by its agent until that agent is archived, even
        # when its execution closed. A new request id is refused naming the holder; a
        # repeat with the holder's own request id reconciles it.
        previous = self.request("worker")
        self.dispatch(previous)
        for status in ("completed", "failed", "stopped"):
            with self.subTest(status):
                # One seat keeps one agent: clear the previous close's error marks so
                # each status closes the same holder from a clean turn.
                self.agent_of(previous).update(lastError=None, attentionReason=None)
                closed = self.close_execution(previous, status)
                old_agent = closed["execution"]["agentId"]
                successor = self.request("worker")
                self.runtime.calls.clear()
                error = self.refused(successor)
                self.assertEqual(error.status_code, 409)
                self.assertIn(f"held by agent {old_agent}", str(error.detail))
                # A start by hand from the launcher follows the same rule, and the
                # refusal offers the replacing start on this line.
                self.assertIn("replacesAgentId naming it", str(error.detail))
                # Nothing was archived or created: one read of the holder, receipt kept.
                self.assertEqual([call[0] for call in self.runtime.calls], ["agent-state"])
                self.assertEqual(self.runtime.calls[0][1], {"agentId": old_agent})
                self.assertIsNone(self.runtime.agents[old_agent]["archivedAt"])
                self.assertEqual(self.receipt(successor)["requestId"], str(previous.request_id))
                self.assertEqual(self.receipt(successor)["status"], status)
                # The holder's own request id still reconciles it.
                self.assertEqual(self.dispatch(previous)[1]["status"], status)
        with self.subTest("the holder cannot be read"):
            old_agent = self.close_execution(previous, "completed")["execution"]["agentId"]
            successor = self.request("worker")
            self.runtime.fail("agent-state", "paseo_daemon_unreachable")
            self.runtime.calls.clear()
            # The endpoint answers host-unreachable; nothing was started or archived.
            status_code, public = self.dispatch(successor)
            self.assertEqual(status_code, 409)
            self.assertTrue(public["hostUnreachable"])
            self.assertIn(old_agent, public["detail"])
            self.assertIsNone(self.runtime.agents[old_agent]["archivedAt"])
        with self.subTest("a rejected execution left no agent to archive"):
            self.runtime.fail("agent-create", "paseo_call_failed", "refused")
            rejected = self.request("orchestrator")
            self.assertEqual(self.dispatch(rejected)[1]["status"], "rejected")
            self.runtime.calls.clear()
            self.assertEqual(self.dispatch(self.request("orchestrator"))[1]["status"], "running")
            self.assertEqual(
                [call[0] for call in self.runtime.calls], ["workspace-open", "agent-create"]
            )

    def test_a_replacing_start_retires_the_holder_and_starts_in_one_call(self) -> None:
        # Rule 9: a start that names the holder retires it first, in the same call,
        # by rule 8. The successor's handover names the earlier report paths.
        previous = self.request("worker")
        self.dispatch(previous)
        old_agent = self.receipt(previous)["agentId"]
        self.agent_of(previous).update(status="idle")
        payload = self.request("worker").model_dump(mode="json", by_alias=True, exclude_none=True)
        replacing = RoleDispatchRequest.model_validate({**payload, "replacesAgentId": old_agent})
        self.runtime.calls.clear()

        status_code, public = self.dispatch(replacing)

        self.assertEqual((status_code, public["status"]), (200, "running"))
        new_agent = public["execution"]["agentId"]
        self.assertNotEqual(new_agent, old_agent)
        # The holder was retired first, in the same call.
        self.assertIsNotNone(self.runtime.agents[old_agent]["archivedAt"])
        self.assertIsNone(self.runtime.agents[new_agent]["archivedAt"])
        self.assertEqual(
            [call[0] for call in self.runtime.calls],
            ["agent-state", "agent-archive", "workspace-open", "agent-create"],
        )
        self.assertEqual(self.runtime.calls[1][1], {"agentId": old_agent})
        receipt = self.receipt(replacing)
        self.assertEqual(receipt["predecessorReports"]["requestId"], str(previous.request_id))
        artifact = receipt["handoverArtifact"]
        text = Path(artifact.get("canonicalPath") or artifact["path"]).read_text(encoding="utf-8")
        self.assertIn(f"Successor start: this seat replaces agent {old_agent}", text)
        self.assertIn("its earlier report paths are", text)
        self.assertIn("Take over from what is written there.", text)
        # The archived predecessor receipt carries the replacement record (rule 8).
        history = self.receipt_path(previous).parent / "history"
        archived = json.loads((history / f"{previous.request_id}.json").read_text(encoding="utf-8"))
        self.assertIn("replaced by a new start", archived["retiredBy"]["reason"])
        self.assertEqual(archived["replaceAuthorizedBy"], "the launcher")

    def test_a_replacing_start_refuses_a_running_holder_without_the_word(self) -> None:
        # Rule 8, as in retirement: a holder in a turn is replaced only with the
        # word that its turn may be interrupted. The refusal leaves everything intact.
        previous = self.request("worker")
        self.dispatch(previous)
        old_agent = self.receipt(previous)["agentId"]
        self.runtime.agents[old_agent].update(status="running")
        payload = self.request("worker").model_dump(mode="json", by_alias=True, exclude_none=True)
        replacing = RoleDispatchRequest.model_validate({**payload, "replacesAgentId": old_agent})
        self.runtime.calls.clear()

        error = self.refused(replacing)

        self.assertEqual(error.status_code, 409)
        self.assertIn(f"Agent {old_agent} is in a turn", str(error.detail))
        self.assertIsNone(self.runtime.agents[old_agent]["archivedAt"])
        current = self.receipt(replacing)
        self.assertNotIn("retiredBy", current)
        self.assertNotIn("replaceAuthorizedBy", current)
        # With the word, the same replacement retires then starts once.
        interrupt_payload = self.request("worker").model_dump(
            mode="json", by_alias=True, exclude_none=True
        )
        interrupting = RoleDispatchRequest.model_validate(
            {
                **interrupt_payload,
                "replacesAgentId": old_agent,
                "replacesInterruptTurn": True,
            }
        )
        status_code, public = self.dispatch(interrupting)
        self.assertEqual((status_code, public["status"]), (200, "running"))
        self.assertIsNotNone(self.runtime.agents[old_agent]["archivedAt"])

    def test_a_task_bound_receipt_is_created_exclusively(self) -> None:
        with self.subTest("of many simultaneous creations exactly one succeeds"):
            path = self.root / "race" / "worker-selection.json"
            receipts = [{"schema": "ar-role-execution/v1", "requestId": str(n)} for n in range(24)]
            with ThreadPoolExecutor(max_workers=24) as pool:
                created = list(
                    pool.map(
                        lambda receipt: role_launch_receipts._create_receipt(path, receipt),
                        receipts,
                    )
                )
            self.assertEqual(created.count(True), 1)
            winner = receipts[created.index(True)]
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), winner)
            self.assertEqual([entry.name for entry in path.parent.iterdir()], [path.name])
        # The other process's receipt is still open, or already closed, when this launch loses.
        losing = {
            "starting": "still in flight (request {theirs}, status starting)",
            "rejected": "started at the same moment",
        }
        for status, reason in losing.items():
            with self.subTest("the launch that loses is refused and calls nothing", status=status):
                mine = self.request("worker")
                theirs = self.request("worker")
                path = self.receipt_path(mine)
                competitor = {
                    "schema": "ar-role-execution/v1",
                    "requestId": str(theirs.request_id),
                    "role": "worker",
                    "status": status,
                    "execution": {},
                }
                write_binding = role_launch_routes._place_message_binding_projection

                def another_process_starts_first(
                    *args: Any,
                    path: Path = path,
                    competitor: dict[str, Any] = competitor,
                    write_binding: Any = write_binding,
                ) -> bool:
                    # This launch has found no receipt; the other process creates its own now.
                    self.assertTrue(role_launch_receipts._create_receipt(path, competitor))
                    return write_binding(*args)

                with patch.object(
                    role_launch_routes,
                    "_place_message_binding_projection",
                    side_effect=another_process_starts_first,
                ):
                    error = self.refused(mine)
                self.assertEqual(error.status_code, 409)
                self.assertIn(reason.format(theirs=theirs.request_id), str(error.detail))
                self.assertEqual(self.runtime.launch_calls(), [])
                history = path.parent / "history" / f"{theirs.request_id}.json"
                kept = path if status == "starting" else history
                self.assertEqual(json.loads(kept.read_text(encoding="utf-8")), competitor)
                kept.unlink()
