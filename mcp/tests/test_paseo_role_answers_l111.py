"""MIK-R111 role answer tests (rules 4-5, evidence item 5).

What role_start and role_message answer. Extracted from
test_paseo_role_tools.py and test_paseo_role_messages.py under the
file-size rail (F01.19).
"""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from agents_remember.application import tool_response as response
from agents_remember.application.tool_answer_log import tool_call_context
from agents_remember.application.tool_response import complete_tool_response
from agents_remember.cli import role_launch_preparation as preparation
from agents_remember.mcp.tools.role_agents import role_message_payload
from agents_remember.models.role_agents import RoleMessageCall
from agents_remember.models.tokens import count_response_tokens
from agents_remember.models.tokens import count_response_tokens as count
from agents_remember.models.tools.tool_answer import LOG_ERROR_LINE_UNWRITABLE
from agents_remember.tasks import read_task_doc, write_task_doc
from agents_remember.tasks.document_refs import ResolvedTaskDocument
from test_paseo_role_tools import RoleToolsTestCase, binding, selection_of
from tool_answer_l111_test_support import (
    distinct_location_values,
    separate_locations,
)


class RoleStartAnswerTests(RoleToolsTestCase):
    """MIK-R111 rule 4, evidence item 5: what role_start answers.

    A carried-out start answers with ok, the agent's id and the location of
    the handover; the rest (status line, report path, choices, preparation)
    stays in the whole result the answer's ``log`` names. Anything the start
    did not carry out keeps its reasoned shape.
    """

    def _answer(self, whole: dict[str, Any]) -> dict[str, Any]:

        with tool_call_context("role_start", {"role": whole.get("role")}, config=self.config):
            return complete_tool_response("role_start", dict(whole), config=self.config)

    def test_a_running_start_answers_with_ok_agent_and_handover(self) -> None:
        whole = self.start(self.architect, "worker")
        self.assertEqual((whole["ok"], whole["status"]), (True, "running"))
        answer = self._answer(whole)
        self.assertTrue(answer["ok"])
        self.assertEqual(answer["agentId"], whole["agentId"])
        self.assertEqual(answer["handoverArtifactPath"], whole["handoverArtifactPath"])
        for dropped in (
            "status",
            "detail",
            "requestId",
            "role",
            "parentAgentId",
            "reportPath",
            "executionStatus",
            "preparation",
            "choices",
        ):
            self.assertNotIn(dropped, answer)
        self.assertIn("log", answer)
        whole_back = json.loads(Path(answer["log"]).read_text(encoding="utf-8"))
        self.assertEqual(whole_back["agentId"], whole["agentId"])
        self.assertEqual(whole_back["reportPath"], whole["reportPath"])

    def test_start_with_missing_requirement_file_names_it_and_succeeds(self) -> None:

        leaf_path = Path(self.leaf.path)
        document = read_task_doc(leaf_path).model_copy(
            update={"requirements": ["[Absent](requirements/ABSENT.md) \u2014 missing."]}
        )
        write_task_doc(leaf_path.parent, document)
        # The launch context serves the in-memory document, as the fake
        # enclosure recording does: rebind it, or the rewrite drops the edit.
        self.leaf = ResolvedTaskDocument(ref=self.leaf.ref, path=self.leaf.path, document=document)
        # The harness stubs handover compilation; merge the real document-file
        # metadata back in so the receipt and answer path stays production.
        stub_compile = preparation._compile_handover

        def compiling_with_files(request: Any) -> dict[str, Any]:
            handover = stub_compile(request)
            context = request.context
            rows = [
                {"taskDocumentRef": doc.ref.model_dump(mode="json")}
                for doc in (context.sprint, context.master, context.task)
                if doc is not None
            ]
            metadata, _first = preparation._task_document_read_metadata(
                request.config, context, request.workspace, rows
            )
            handover["taskDocumentFiles"] = metadata
            return handover

        with patch.object(preparation, "_compile_handover", compiling_with_files):
            whole = self.start(self.architect, "worker")
        self.assertTrue(whole["ok"])
        self.assertIn("requirements/ABSENT.md", str(whole.get("warning", "")))
        answer = self._answer(whole)
        self.assertTrue(answer["ok"])
        self.assertIn("requirements/ABSENT.md", answer["warning"])
        self.assertEqual(answer["handoverArtifactPath"], whole["handoverArtifactPath"])
        indexes = list(Path(self.root).rglob("task-documents/INDEX.json"))
        self.assertTrue(indexes)
        rows = json.loads(indexes[0].read_text(encoding="utf-8"))["documents"]
        missing = [row for row in rows if row.get("missing") is True]
        self.assertEqual(len(missing), 1)
        self.assertIn("requirements/ABSENT.md", str(missing[0].get("reference")))

    def test_what_a_start_did_not_carry_out_keeps_its_reason(self) -> None:
        refused = self.refusal(self.start(self.architect, "architect"), "role-may-not-start-role")
        answer = self._answer(refused)
        self.assertEqual(
            (answer["ok"], answer["status"], answer["refusal"]),
            (False, "refused", "role-may-not-start-role"),
        )
        self.assertTrue(answer["detail"].strip() and answer["nextAction"].strip())
        self.runtime.fail("agent-create", "paseo_call_failed", "Provider codex is not configured")
        rejected = self.start(self.architect, "manager")
        self.assertEqual((rejected["ok"], rejected["status"]), (False, "rejected"))
        answer = self._answer(rejected)
        self.assertEqual((answer["ok"], answer["status"]), (False, "rejected"))
        self.assertIn("new request id", answer["nextAction"])


class RoleMessageAnswerTests(RoleToolsTestCase):
    """MIK-R111 rule 5, evidence item 5: what role_message answers.

    Without wait the answer is ok, status and how the recipient took the
    message; the recipient's id rides only a role-addressed call and detail
    only when the caller has to do something. With wait it adds the reply; a
    reply over the exception bound rides as its beginning, whole in the
    detail file. The 40-token ordinary target is the shaped row content: the
    framework log location rides beside it per MIK-R108.
    """

    def _answer(self, whole: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
        with tool_call_context("role_message", arguments, config=self.config):
            return complete_tool_response("role_message", dict(whole), config=self.config)

    def _wait_answer(self, waits: list[dict[str, Any]], worker: str) -> dict[str, Any]:
        self.runtime.agents[worker].update(status="idle", received=[])
        self.runtime.calls.clear()
        self.waits = list(waits)
        self.clock = 0.0
        whole = self.message(self.architect, agent_id=worker, wait=True)
        return self._answer(whole, {"text": "go", "agent_id": worker, "wait": True})

    def _content_tokens(self, answer: dict[str, Any]) -> int:
        # The 40-token packet target counts the answer without its locations,
        # removed at every depth like every other location-free target; the
        # ordinary message carries only the framework log location.
        without, locations = separate_locations(answer)
        distinct = distinct_location_values(locations)
        self.assertLessEqual(len(distinct), 2, f"ordinary message carries locations: {distinct}")
        return count_response_tokens(without)

    def test_ordinary_id_addressed_answer_is_a_line(self) -> None:
        _request, worker = self.started(status="idle")
        _reviewer_request, reviewer = self.started("reviewer", status="idle")
        sender = binding("worker", worker)
        whole = self.message(sender, "report your plan", agent_id=reviewer)
        self.assertEqual(
            (whole["ok"], whole["status"], whole["taken"]), (True, "accepted", "started")
        )
        answer = self._answer(whole, {"text": "report your plan", "agent_id": reviewer})
        self.assertEqual(
            (answer["ok"], answer["status"], answer["taken"]), (True, "accepted", "started")
        )
        for dropped in ("detail", "senderLine", "recipientAgentId", "resumed", "reportCollection"):
            self.assertNotIn(dropped, answer)
        self.assertIn("log", answer)
        # The 40-token packet target constrains the shaped row content; the
        # framework log location rides beside it per MIK-R108 (accepted
        # Manager reading). Both are measured: the row fits 40, the complete
        # delivered answer is exactly the row plus the log, and the log
        # location reads back the whole result.
        self.assertLessEqual(self._content_tokens(answer), 40)
        self.assertEqual(set(answer), {"ok", "status", "taken", "log"})
        whole_back = json.loads(Path(answer["log"]).read_text(encoding="utf-8"))
        self.assertEqual(whole_back["senderLine"], whole["senderLine"])
        self.assertEqual(whole_back["recipientAgentId"], reviewer)

    def test_role_addressed_answer_names_the_recipient_and_a_cancelled_turn_warns(self) -> None:
        # The 40-token ordinary target is the id-addressed answer above: a
        # resolved agent UUID alone costs ~25 product tokens, so a
        # role-addressed answer cannot guarantee it. This case pins the shape
        # (id present, no FYI detail); the packet reading is with the Manager.
        _request, worker = self.started(status="idle")
        to_worker = {"role": "worker", **selection_of("worker")}
        whole = self.message(self.architect, **to_worker)
        answer = self._answer(whole, {"text": "report your plan", **to_worker})
        self.assertEqual(answer["recipientAgentId"], worker)
        self.assertNotIn("detail", answer)
        self.assertNotIn("senderLine", answer)
        with self.subTest("the host cancelled the running turn to deliver"):
            self.runtime.agents[worker].update(status="idle", received=[], taken="replaced")
            whole = self.message(self.architect, **to_worker)
            self.assertIn("warning", whole)
            answer = self._answer(whole, {"text": "report your plan", **to_worker})
            self.assertIn("cancelled", answer["warning"])

    def test_wait_answers_carry_the_reply_and_cut_an_over_bound_one(self) -> None:
        _request, worker = self.started(status="idle")
        with self.subTest("a finished turn answers its final text and no detail"):
            ended = {
                "state": "ended",
                "outcome": "finished",
                "text": "done",
                "textTruncated": False,
            }
            answer = self._wait_answer([ended], worker)
            self.assertEqual((answer["ok"], answer["status"]), (True, "turn-finished"))
            self.assertEqual(answer["text"], "done")
            self.assertNotIn("detail", answer)
            self.assertNotIn("waitedSeconds", answer)
        with self.subTest("a failed turn keeps what the caller must handle"):
            ended = {
                "state": "ended",
                "outcome": "failed",
                "text": None,
                "textTruncated": False,
                "error": "usage limit",
            }
            answer = self._wait_answer([ended], worker)
            self.assertEqual(answer["status"], "turn-failed")
            self.assertIn("usage limit", answer["detail"])
        with self.subTest("a reply over the exception bound rides as its beginning"):
            big = "".join(f"{index:08d}-abcdefgh;" for index in range(900))
            ended = {"state": "ended", "outcome": "finished", "text": big, "textTruncated": False}
            self.assertGreater(len(big), 15000)
            answer = self._wait_answer([ended], worker)

            self.assertTrue(answer["textTruncated"])
            self.assertLessEqual(count_response_tokens(answer), 4000)
            self.assertTrue(big.startswith(str(answer["text"])))
            self.assertIn("log", answer)
            whole_back = json.loads(Path(answer["log"]).read_text(encoding="utf-8"))
            self.assertEqual(whole_back["text"], big)

    def test_held_answers_keep_their_hold_facts_through_delivery(self) -> None:
        # L100 rules 4a/4b through the delivered answer. The master 4a/4b
        # tests prove the product holds with reason, permission, issued id
        # and waiting counts, but they call the product directly, so the
        # lossy row projection passed them too. This pins the delivery end
        # to end through the adapter: the explicit held override carries
        # the hold facts past the shaped row; the long why-narrative rides
        # the whole detail file.
        _request, worker = self.started(status="running")
        self.runtime.agents[worker].update(
            pendingPermissions=[{"id": "perm-1", "name": "Bash", "kind": "tool"}]
        )
        self.runtime.calls.clear()
        call = {"text": "report your plan", "agent_id": worker}
        with (
            tool_call_context("role_message", dict(call), config=self.config),
            patch.dict(os.environ, self.architect.environment()),
        ):
            answer = role_message_payload(
                self.config, RoleMessageCall(text=call["text"], agent_id=worker)
            )
        self.assertEqual((answer["ok"], answer["status"]), (True, "held"))
        self.assertEqual(answer["heldReason"], "permission-pending")
        self.assertEqual(answer["permission"], "Bash")
        self.assertTrue(answer["heldMessageId"])
        self.assertEqual((answer["waitingMine"], answer["waitingTotal"]), (1, 1))
        self.assertEqual(answer["recipientAgentId"], worker)
        self.assertIn("log", answer)
        whole_back = json.loads(Path(answer["log"]).read_text(encoding="utf-8"))
        self.assertEqual(whole_back["heldMessageId"], answer["heldMessageId"])


if __name__ == "__main__":
    unittest.main()


class RoleWaitPrefixTests(RoleMessageAnswerTests):
    """F01.15: a waited reply keeps its required beginning after all attachments.

    The prefix retention is measured on the final mapping, after the log
    location (whose length grows with the root) is attached -- never delegated
    to the generic overflow guard, which would drop the text entirely.
    """

    def test_long_wait_reply_keeps_its_beginning_within_bound(self) -> None:

        _request, worker = self.started(status="idle")
        long_text = "".join(
            f"Reply part {index}: the waited turn finished the review. " for index in range(400)
        )
        self.assertGreater(count({"text": long_text}), 4000)
        answer = self._wait_answer(
            [
                {
                    "state": "ended",
                    "outcome": "finished",
                    "text": long_text,
                    "textTruncated": False,
                }
            ],
            worker,
        )
        self.assertTrue(answer["text"])
        self.assertTrue(long_text.startswith(answer["text"]))
        self.assertTrue(answer["textTruncated"])
        self.assertLessEqual(count(answer), 4000)
        self.assertIn("log", answer)
        whole_back = json.loads(Path(answer["log"]).read_text(encoding="utf-8"))
        self.assertEqual(whole_back["text"], long_text)

    def test_giant_reply_without_log_retention_stays_bounded(self) -> None:
        _request, worker = self.started(status="idle")
        huge = "Finding 0: " + "x" * 9000000
        answer = self._wait_answer(
            [
                {
                    "state": "ended",
                    "outcome": "finished",
                    "text": huge,
                    "textTruncated": False,
                }
            ],
            worker,
        )
        # The whole result is too large to retain: the answer says so and
        # still keeps the reply's beginning within the exception bound.
        self.assertIn("logError", answer)
        self.assertNotIn("log", answer)
        self.assertTrue(answer["text"])
        self.assertTrue(huge.startswith(answer["text"]))
        self.assertLessEqual(count(answer), 4000)

    def test_wait_prefix_survives_late_line_write_failure(self) -> None:
        _request, worker = self.started(status="idle")
        text = "".join(
            f"Finding {index:08d}: abcdefghijklmnopqrstuvwxyz; " for index in range(2000)
        )
        outcomes = [{"state": "ended", "outcome": "finished", "text": text, "textTruncated": False}]
        good = self._wait_answer(outcomes, worker)
        self.assertTrue(good.get("text") and text.startswith(good["text"]))
        with patch.object(response, "_append_answer_line", return_value=LOG_ERROR_LINE_UNWRITABLE):
            bad = self._wait_answer(outcomes, worker)
        # Positive control passed; the late failure keeps the exact prefix,
        # the logError, and the whole truth within the exception bound.
        self.assertTrue(bad.get("text") and text.startswith(bad["text"]))
        self.assertIn("logError", bad)
        self.assertIn("log", bad)
        self.assertLessEqual(count(bad), 4000)
        whole_back = json.loads(Path(bad["log"]).read_text(encoding="utf-8"))
        self.assertEqual(whole_back["text"], text)
