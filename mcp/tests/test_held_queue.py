"""Held-queue fault-injection proofs (MIK-R100 rules 4a/4b, L100-CODE-03/04/05/06/13/14).

Each test stops the world at a disk-write or interleaving boundary the Reviewer's
probes named, then proves the queue recovers without a sender retry, duplicate
delivery, lost end, or unbounded sweep work. The fake bridge stands in for the host.
"""

from __future__ import annotations

import json
import shutil
import unittest
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import patch

import agents_remember.cli.paseo_role_tools as tools
import agents_remember.cli.role_held_messages as held
from agents_remember.cli import role_launch_receipts as receipts_module
from agents_remember.cli.role_held_messages import (
    EXECUTIONS_DIRECTORY,
    HeldMessage,
    _reset_sweep_cursor,
)
from agents_remember.cli.role_launch_receipts import RECEIPT_SCHEMA
from agents_remember.observer.host_activity import HOST_ACTIVITY_FILE
from agents_remember.serving.projections import host_activity as host_activity_module
from agents_remember.serving.projections.host_activity import (
    activity_directory,
    turn_ended_agents,
)

read_receipt = receipts_module._read_receipt
"""Read one execution receipt in these proofs."""
from test_native_retire import retire
from test_paseo_role_tools import ARCHIVED_AT, RoleToolsTestCase, binding, selection_of


class HeldQueueRecoveryTests(RoleToolsTestCase):
    """The queue survives crashes, races and host limits with its ends recorded."""

    def setUp(self) -> None:
        super().setUp()
        _reset_sweep_cursor()

    def hold_open_worker(self, text: str = "held work"):
        """A live worker with one held message: permission pins it until cleared."""

        manager = binding("manager")
        worker = self.start(manager, "worker")["agentId"]
        self.runtime.agents[worker]["pendingPermissions"] = [{"name": "Bash"}]
        answer = self.message(manager, agent_id=worker, text=text)
        self.assertEqual(answer["status"], "held")
        located = tools._locate_recipient_store(
            self.config, tools.RoleMessageCall(text="lookup", agent_id=worker), worker
        )
        assert located is not None
        return manager, worker, located

    def end_turn(self, worker) -> None:
        """The recipient's turn ends, so its next message can be sent (MIK-R171 rule 2)."""

        self.runtime.agents[worker]["status"] = "idle"

    def drain_all(self, worker, located, *, limit: int = 12) -> list[dict[str, Any]]:
        """Drain one recipient, each delivery's turn ending before the next one starts.

        A message never joins a running turn (MIK-R171 rule 2), so a queue of several
        messages, or one message the host splits, takes one delivery per turn end.
        """

        results: list[dict[str, Any]] = []
        for _ in range(limit):
            outcome = held.drain_held(self.config, located[0], located[1])
            results.append(outcome)
            held.apply_drain_to_receipt(located[0], outcome)
            if outcome.get("outcome") != "partially-delivered":
                return results
            self.end_turn(worker)
        raise AssertionError(f"the queue did not drain in {limit} deliveries: {results}")

    def open_worker(self, worker) -> None:
        """Let the host take messages for one worker again, its turn ended (MIK-R171 rule 1).

        A message is never sent into a running turn (rule 2), so a recipient is only sent to
        while it runs none.
        """

        self.runtime.agents[worker].update(pendingPermissions=[], status="idle")

    def test_a_restart_after_store_admission_delivers_without_a_retry(self) -> None:
        manager = binding("manager")
        worker = self.start(manager, "worker")["agentId"]
        self.runtime.agents[worker]["pendingPermissions"] = [{"name": "Bash"}]
        with (
            patch.object(
                tools, "_note_waiting", side_effect=RuntimeError("simulated stop after store fsync")
            ),
            self.assertRaises(RuntimeError),
        ):
            self.message(manager, agent_id=worker, text="durable orphan")
        located = tools._locate_recipient_store(
            self.config, tools.RoleMessageCall(text="lookup", agent_id=worker), worker
        )
        assert located is not None
        # The store holds the message but no counter was ever published.
        self.assertNotIn("heldWaiting", read_receipt(located[0]) or {})
        self.assertEqual(held.waiting_total(located[2], worker), 1)
        self.open_worker(worker)

        swept = held.sweep_held_messages(self.config)

        self.assertGreaterEqual(swept["swept"], 1)
        received = self.received(worker)
        self.assertTrue(any(text.endswith("durable orphan") for text in received))
        self.assertEqual(held.waiting_total(located[2], worker), 0)
        lines = (read_receipt(located[0]) or {}).get("heldMessageLines") or []
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["how"], "delivered")

    def test_a_failed_end_record_is_staged_and_a_later_flush_writes_it(self) -> None:
        _manager, worker, located = self.hold_open_worker("terminal gap")
        self.open_worker(worker)
        outcome = held.drain_held(self.config, located[0], located[1])
        self.assertEqual(outcome["outcome"], "delivered")
        with patch.object(held, "_write_receipt", side_effect=OSError("disk full at end record")):
            recorded = held.apply_drain_to_receipt(located[0], outcome)

        self.assertFalse(recorded)
        self.assertEqual(held.waiting_total(located[2], worker), 0)
        self.assertEqual((read_receipt(located[0]) or {}).get("heldMessageLines") or [], [])

        held.sweep_held_messages(self.config)

        lines = (read_receipt(located[0]) or {}).get("heldMessageLines") or []
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["how"], "delivered")
        self.assertEqual(sum("terminal gap" in text for text in self.received(worker)), 1)

    def test_a_recovery_reuses_its_batch_id_and_a_deduping_host_delivers_once(self) -> None:
        manager, worker, located = self.hold_open_worker("first batch")
        self.open_worker(worker)
        original_send = self.runtime._agent_send
        seen: set[str] = set()

        def dedup(payload: dict[str, Any]) -> dict[str, Any]:
            if payload["messageId"] in seen:
                return {"delivery": {"delivered": True, "taken": "started", "turnId": "turn-7"}}
            seen.add(payload["messageId"])
            return original_send(payload)

        with patch.object(self.runtime, "_agent_send", side_effect=dedup):
            with (
                patch.object(
                    held, "_remove_ids", side_effect=RuntimeError("stop after host delivered")
                ),
                self.assertRaises(RuntimeError),
            ):
                held.drain_held(self.config, located[0], located[1])
            self.end_turn(worker)
            self.message(manager, agent_id=worker, text="later batch member")
            with patch.object(held, "STALE_CLAIM_SECONDS", 0):
                outcome = held.drain_held(self.config, located[0], located[1])

        self.assertEqual(outcome["outcome"], "delivered")
        sent_ids = [
            payload["messageId"]
            for command, payload in self.runtime.calls
            if command == "agent-send"
        ]
        # The refused direct attempt first, then the in-flight batch under its
        # stable id, then the same id again after recovery: later work stays
        # ordered behind it and goes separately.
        self.assertEqual(len(sent_ids), 3)
        self.assertNotEqual(sent_ids[0], sent_ids[1])
        self.assertEqual(sent_ids[1], sent_ids[2])
        self.assertTrue(sent_ids[1].startswith("held-batch:"))
        received = self.received(worker)
        self.assertEqual(sum("first batch" in text for text in received), 1)
        held.apply_drain_to_receipt(located[0], outcome)
        resumed = held.drain_held(self.config, located[0], located[1])
        held.apply_drain_to_receipt(located[0], resumed)
        self.assertEqual(
            [text.split("\n")[-1] for text in self.received(worker)],
            ["first batch", "later batch member"],
        )
        self.assertEqual(held.waiting_total(located[2], worker), 0)

    def test_a_restart_with_a_stale_zero_counter_delivers_without_a_retry(self) -> None:
        # The sealed existing-counter boundary: the first cycle publishes its
        # zero count, then the next admission stops before the counter write.
        # Restart discovers the store's message through the stale zero counter.
        manager, worker, located = self.hold_open_worker("completed first cycle")
        self.open_worker(worker)
        result = held.drain_held(self.config, located[0], located[1])
        held.apply_drain_to_receipt(located[0], result)
        self.assertEqual((read_receipt(located[0]) or {}).get("heldWaiting"), 0)
        self.runtime.agents[worker]["pendingPermissions"] = [{"name": "Bash"}]
        with (
            patch.object(tools, "_note_waiting", side_effect=RuntimeError("stop after admission")),
            self.assertRaises(RuntimeError),
        ):
            self.message(manager, agent_id=worker, text="second-cycle orphan")
        self.open_worker(worker)

        swept = held.sweep_held_messages(self.config)

        self.assertGreaterEqual(swept["swept"], 1)
        self.assertEqual(swept["delivered"], 1)
        received = self.received(worker)
        self.assertTrue(any(text.endswith("second-cycle orphan") for text in received))
        self.assertEqual(held.waiting_total(located[2], worker), 0)
        lines = (read_receipt(located[0]) or {}).get("heldMessageLines") or []
        self.assertTrue(any(line.get("how") == "delivered" for line in lines))

    def test_a_stop_after_store_deletion_still_records_its_terminal_end(self) -> None:
        # The sealed deletion-to-staging boundary: the host took the text and the
        # store deleted it, but staging never ran. Restart flushes the staged end.
        _manager, worker, located = self.hold_open_worker("audit must survive deletion")
        self.open_worker(worker)
        original = held._remove_ids

        class Stopped(BaseException):
            pass

        def stop_after_delete(*args, **kwargs):
            original(*args, **kwargs)
            raise Stopped("stop after store deletion, before staging terminal metadata")

        with (
            patch.object(held, "_remove_ids", side_effect=stop_after_delete),
            self.assertRaises(Stopped),
        ):
            held.drain_held(self.config, located[0], located[1])

        held.sweep_held_messages(self.config)

        self.assertEqual(held.waiting_total(located[2], worker), 0)
        lines = (read_receipt(located[0]) or {}).get("heldMessageLines") or []
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["how"], "delivered")
        store = json.loads(held.store_path_for(located[2], worker).read_text())
        self.assertEqual(store.get("pendingLines", []), [])
        self.assertEqual(sum("audit must survive deletion" in t for t in self.received(worker)), 1)

    def test_a_split_recovery_keeps_the_part_id_on_a_deduping_host(self) -> None:
        # The sealed split boundary: a delivery goes out, its removal never runs, and
        # recovery resends the survivor under its original part id so a deduping host
        # takes it once. A message never joins a running turn (MIK-R171 rule 2), so the
        # queue delivers one part per turn end.
        _manager, worker, located = self.hold_open_worker("A" * 120)
        self.open_worker(worker)
        real_send = held._send_once
        accepted: list[dict[str, Any]] = []
        seen: set[str] = set()

        class Stopped(BaseException):
            pass

        def dedup_limit(config, payload):
            accepted.append({"id": payload["messageId"], "text": payload["text"]})
            if payload["messageId"] in seen:
                return {"delivered": True, "taken": "started", "turnId": "dedup"}
            seen.add(payload["messageId"])
            return real_send(config, payload)

        def stop_first(*args, **kwargs):
            raise Stopped("stop after the host took the part, before removing its text")

        with patch.object(held, "_send_once", side_effect=dedup_limit):
            with (
                patch.object(held, "_remove_ids", side_effect=stop_first),
                self.assertRaises(Stopped),
            ):
                held.drain_held(self.config, located[0], located[1])
            with patch.object(held, "STALE_CLAIM_SECONDS", 0):
                result = held.drain_held(self.config, located[0], located[1])
            held.apply_drain_to_receipt(located[0], result)

        received = [text.split("\n")[-1] for text in self.received(worker)]
        self.assertEqual(received, ["A" * 120])
        part_a = [entry["id"] for entry in accepted if entry["text"].split("\n")[-1] == "A" * 120]
        self.assertEqual(len(part_a), 2)
        self.assertEqual(part_a[0], part_a[1])

    def test_six_messages_split_into_the_minimum_three_deliveries(self) -> None:
        # The sealed minimum-partition boundary: six equal messages on a host
        # that admits two per delivery go out in exactly three, in order.
        manager, worker, located = self.hold_open_worker("A")
        for text in ["B", "C", "D", "E", "F"]:
            self.message(manager, agent_id=worker, text=text)
        rows = json.loads(held.store_path_for(located[2], worker).read_text())["messages"]
        limit_bytes = len(held.combined_text(rows[:2]).encode())
        self.open_worker(worker)
        original = held._send_once
        accepted: list[str] = []

        def limited(config, payload):
            if len(payload["text"].encode()) > limit_bytes:
                return {
                    "delivered": False,
                    "refused": "busy",
                    "detail": "declared fixture size limit",
                }
            answer = original(config, payload)
            if answer.get("delivered"):
                accepted.append(payload["text"])
            return answer

        with patch.object(held, "_send_once", side_effect=limited):
            results = self.drain_all(worker, located)
        result = results[-1]

        self.assertEqual(result["outcome"], "delivered")
        self.assertEqual(len(accepted), 3)
        self.assertEqual(
            [text.split("\n")[-1] for text in "\n\n".join(accepted).split("\n\n")],
            ["A", "B", "C", "D", "E", "F"],
        )

    def test_variable_lengths_split_into_the_minimum_two_deliveries(self) -> None:
        # The sealed variable-length boundary: a 214-byte monotone cap admits
        # the FIFO parts [A,B] (191) and [C,D,E] (214). A learned budget is a
        # lower bound, never an upper bound: the whole remainder is attempted
        # before the budget chunks anything.
        manager, worker, located = self.hold_open_worker("A" * 50)
        for text in ["B", "C", "D", "E"]:
            self.message(manager, agent_id=worker, text=text)
        rows = json.loads(held.store_path_for(located[2], worker).read_text())["messages"]
        limit_bytes = len(held.combined_text(rows[2:]).encode())
        self.assertTrue(len(held.combined_text(rows[:2]).encode()) <= limit_bytes)
        self.open_worker(worker)
        original = held._send_once
        accepted: list[str] = []

        def limited(config, payload):
            if len(payload["text"].encode()) > limit_bytes:
                return {
                    "delivered": False,
                    "refused": "busy",
                    "detail": "declared fixture size limit",
                }
            answer = original(config, payload)
            if answer.get("delivered"):
                accepted.append(payload["text"])
            return answer

        with patch.object(held, "_send_once", side_effect=limited):
            results = self.drain_all(worker, located)
        result = results[-1]

        self.assertEqual(result["outcome"], "delivered")
        self.assertEqual(len(accepted), 2)
        self.assertEqual(
            [text.split("\n")[-1] for text in "\n\n".join(accepted).split("\n\n")],
            ["A" * 50, "B", "C", "D", "E"],
        )

    def test_a_withdrawal_during_a_live_claim_is_refused_and_sends_nothing_new(self) -> None:
        manager = binding("manager")
        folder = str(self.config.coordination_root / "fleet" / "withdraw")
        agent = str(uuid.uuid4())
        sender = manager.agent_id
        first = held.hold_message(
            folder,
            agent,
            HeldMessage(
                sender_line="manager",
                sender_agent_id=sender,
                text="will be withdrawn",
                reason="test",
            ),
        )
        batch = held._claim_batch(folder, agent)
        assert batch is not None
        claimed, _claim = batch
        self.assertEqual(len(claimed), 1)

        with self.assertRaises(held.HeldMessageCommitted):
            held.withdraw_message(folder, agent, first["heldMessageId"], sender)
        held._release_claim(folder, agent, _claim)
        ended = held.withdraw_message(folder, agent, first["heldMessageId"], sender)
        self.assertIsNotNone(ended)
        self.assertEqual(held.waiting_total(folder, agent), 0)

    def test_a_withdrawal_racing_a_delivery_has_one_winner(self) -> None:
        manager, worker, located = self.hold_open_worker("will be withdrawn")
        first_id = held.waiting_total(located[2], worker) and self.message_id_of(located, 0)
        self.open_worker(worker)
        original = held._send_once

        def concurrent(config, payload):
            replacement = self.message(
                manager,
                agent_id=worker,
                text="replacement",
                withdraw_held_message_id=first_id,
            )
            # The committed message is not reported withdrawn; the replacement
            # is still held behind it, with the refusal named in the detail.
            self.assertNotIn("withdrawnHeldMessageId", replacement)
            self.assertEqual(replacement["heldReason"], "held-behind-waiting")
            self.assertIn("committed to delivery", replacement["detail"])
            # The replacement started a turn; it ends before this delivery goes out
            # (MIK-R171 rule 2: no message joins a running turn).
            self.end_turn(worker)
            return original(config, payload)

        with patch.object(held, "_send_once", side_effect=concurrent):
            outcome = held.drain_held(self.config, located[0], located[1])

        self.assertEqual(outcome["outcome"], "delivered")
        # The committed batch delivered once; the replacement never overtook it.
        self.assertEqual(
            [text.split("\n")[-1] for text in self.received(worker)], ["will be withdrawn"]
        )
        self.assertEqual(held.waiting_total(located[2], worker), 1)
        held.apply_drain_to_receipt(located[0], outcome)
        self.end_turn(worker)
        again = held.drain_held(self.config, located[0], located[1])
        held.apply_drain_to_receipt(located[0], again)
        self.assertEqual(
            [text.split("\n")[-1] for text in self.received(worker)],
            ["will be withdrawn", "replacement"],
        )

    def message_id_of(self, located, index: int) -> str:
        """The waiting message id at one position of a recipient's store."""

        path = held.store_path_for(located[2], located[1]["execution"]["agentId"])
        store = json.loads(path.read_text(encoding="utf-8"))
        return store["messages"][index]["id"]

    def test_an_archived_recipient_still_answers_the_sender_its_outcome(self) -> None:
        manager, worker, _located = self.hold_open_worker("archived message")
        retire(self, manager, worker, interrupt_turn=True)

        by_id = self.message(manager, agent_id=worker, text="read state")

        self.assertEqual(
            (by_id["ok"], by_id["status"], by_id["refusal"]),
            (False, "refused", "recipient-archived"),
        )
        terminal = by_id.get("recentTerminal") or []
        self.assertEqual(len(terminal), 1)
        self.assertEqual(terminal[0]["how"], "not-delivered-archived-or-gone")
        by_role = self.message(manager, role="worker", **selection_of("worker"), text="read state")
        self.assertEqual(by_role["status"], "refused")
        self.assertEqual((by_role.get("recentTerminal") or []), terminal)
        # Sender isolation: another sender reads none of this sender's ends.
        stranger = binding("manager")
        foreign = self.message(stranger, agent_id=worker, text="read state")
        self.assertNotIn("recentTerminal", foreign)
        # Disappearance answers without readback and without new work.
        missing = self.message(manager, agent_id=str(uuid.uuid4()), text="read state")
        self.assertEqual(missing["refusal"], "recipient-not-found")
        self.assertNotIn("recentTerminal", missing)

    def test_an_oversized_batch_splits_into_admitted_deliveries_in_order(self) -> None:
        manager, worker, located = self.hold_open_worker("A" * 120)
        self.message(manager, agent_id=worker, text="B" * 120)
        store_path = held.store_path_for(located[2], worker)
        rows = json.loads(store_path.read_text(encoding="utf-8"))["messages"]
        combined = len(held.combined_text(rows).encode())
        single = max(len(held.combined_text([row]).encode()) for row in rows)
        self.assertGreater(combined, 256)
        self.assertLess(single, 256)
        self.open_worker(worker)
        calls: list[dict[str, Any]] = []
        original = held._send_once

        def limit(config, payload):
            calls.append(payload)
            if len(payload["text"].encode()) > 256:
                return {
                    "delivered": False,
                    "refused": "busy",
                    "detail": "fake host size limit 256",
                }
            return original(config, payload)

        with patch.object(held, "_send_once", side_effect=limit):
            outcomes = self.drain_all(worker, located)
        outcome = outcomes[-1]

        self.assertEqual(outcome["outcome"], "delivered")
        # One refused combined delivery, then one admitted part per message, the second
        # waiting for the first part's turn to end (MIK-R171 rule 2).
        self.assertEqual(len(calls), 4)
        # The combined delivery is refused by the host's size limit; each part then goes out
        # in a turn of its own, so the second part waits for the first part's turn to end and
        # its retry is refused as busy in between (MIK-R171 rule 2).
        self.assertNotEqual(calls[1]["messageId"], calls[3]["messageId"])
        self.assertEqual(calls[2]["messageId"], calls[3]["messageId"])
        self.assertEqual(
            [text.split("\n")[-1] for text in self.received(worker)], ["A" * 120, "B" * 120]
        )
        held.apply_drain_to_receipt(located[0], outcome)
        self.assertEqual(held.waiting_total(located[2], worker), 0)

    def test_a_batch_exactly_at_the_limit_goes_in_one_delivery(self) -> None:
        manager, worker, located = self.hold_open_worker("C" * 120)
        self.message(manager, agent_id=worker, text="D" * 120)
        store_path = held.store_path_for(located[2], worker)
        rows = json.loads(store_path.read_text(encoding="utf-8"))["messages"]
        self.assertEqual(len(rows), 2)
        limit_bytes = len(held.combined_text(rows).encode())
        self.open_worker(worker)
        calls: list[dict[str, Any]] = []
        original = held._send_once

        def limit(config, payload):
            calls.append(payload)
            if len(payload["text"].encode()) > limit_bytes:
                return {"delivered": False, "refused": "busy", "detail": "fake host size limit"}
            return original(config, payload)

        with patch.object(held, "_send_once", side_effect=limit):
            outcome = held.drain_held(self.config, located[0], located[1])

        self.assertEqual(outcome["outcome"], "delivered")
        self.assertEqual(len(calls), 1)
        received = self.received(worker)
        self.assertEqual(len(received), 1)
        self.assertIn("C" * 120, received[0])
        self.assertTrue(received[0].endswith("D" * 120))

    def test_an_interrupted_multi_part_delivery_resumes_without_loss_or_duplication(self) -> None:
        manager, worker, located = self.hold_open_worker("E" * 120)
        self.message(manager, agent_id=worker, text="F" * 120)
        self.open_worker(worker)
        original = held._send_once
        sends = {"count": 0}

        def flaky(config, payload):
            sends["count"] += 1
            if sends["count"] == 2:
                return {"delivered": False, "refused": "busy", "detail": "host busy mid-batch"}
            return original(config, payload)

        def limit(config, payload):
            if len(payload["text"].encode()) > 256:
                return {"delivered": False, "refused": "busy", "detail": "fake host size limit"}
            return flaky(config, payload)

        with patch.object(held, "_send_once", side_effect=limit):
            outcome = held.drain_held(self.config, located[0], located[1])

        self.assertEqual(outcome["outcome"], "partially-delivered")
        self.assertEqual([text.split("\n")[-1] for text in self.received(worker)], ["E" * 120])
        held.apply_drain_to_receipt(located[0], outcome)
        self.assertEqual(held.waiting_total(located[2], worker), 1)

        self.end_turn(worker)
        resumed = held.drain_held(self.config, located[0], located[1])
        held.apply_drain_to_receipt(located[0], resumed)

        self.assertEqual(resumed["outcome"], "delivered")
        self.assertEqual(
            [text.split("\n")[-1] for text in self.received(worker)], ["E" * 120, "F" * 120]
        )
        self.assertEqual(held.waiting_total(located[2], worker), 0)
        lines = (read_receipt(located[0]) or {}).get("heldMessageLines") or []
        self.assertEqual([line["how"] for line in lines], ["delivered", "delivered"])

    def test_a_queue_with_a_host_unreachable_row_behind_an_ordinary_one_drains(self) -> None:
        """L171-F01: the part ends before a row the host may already hold.

        The order a drain can face is [ordinary, attempted]: the ordinary rows go in the
        order they were held, and the attempted row goes alone under its own id, with no
        assertion between them stopping the drain.
        """

        manager, worker, located = self.hold_open_worker("ordinary")
        sender = manager.agent_id
        held.hold_message(
            located[2],
            worker,
            HeldMessage(
                sender_line="manager",
                sender_agent_id=sender,
                text="the host may already hold this one",
                reason="host-unreachable",
                attempted_message_id="attempted-1",
            ),
        )
        self.open_worker(worker)

        results = self.drain_all(worker, located)

        self.assertEqual(results[-1]["outcome"], "delivered")
        self.assertEqual(
            [text.split("\n")[-1] for text in self.received(worker)],
            ["ordinary", "the host may already hold this one"],
        )
        sends = [payload for command, payload in self.runtime.calls if command == "agent-send"]
        # The attempted row travelled alone, under the id its unanswered send used.
        self.assertEqual(sends[-1]["messageId"], "attempted-1")
        self.assertNotIn("ordinary", sends[-1]["text"])
        self.assertEqual(held.waiting_total(located[2], worker), 0)

    def test_a_ready_drain_that_pauses_writes_one_line_with_the_recipient_and_reason(self) -> None:
        """L171-F04: a paused drain is a failed drain and writes its line."""

        _manager, worker, located = self.hold_open_worker("paused by an unanswered host")

        paused = held._DrainPause(
            {"outcome": "still-held", "detail": "the host did not answer the readiness check"}
        )
        with (
            patch.object(held, "_check_drain_readiness", side_effect=paused),
            self.assertLogs("agents_remember.cli.role_held_messages", level="WARNING") as logs,
        ):
            swept = held.sweep_held_messages(self.config)

        written = " ".join(logs.output)
        self.assertIn(worker, written)
        self.assertIn("the host did not answer the readiness check", written)
        self.assertEqual((swept["delivered"], swept["stillHeld"]), (0, 1))
        self.assertEqual(held.waiting_total(located[2], worker), 1)


class HeldSweepBoundsTests(RoleToolsTestCase):
    """The delivering actor's per-tick work stays bounded at any fleet size."""

    def setUp(self) -> None:
        super().setUp()
        _reset_sweep_cursor()

    def craft_recipient(self, name: str, *, live: bool = True) -> tuple[str, Any, str]:
        """One open recipient with one waiting message, without launching anything."""

        agent_id = str(uuid.uuid4())
        folder = self.config.coordination_root / "fleet" / name
        folder.mkdir(parents=True, exist_ok=True)
        self.runtime.agents[agent_id] = {
            "id": agent_id,
            "status": "idle",
            "archivedAt": None if live else ARCHIVED_AT,
            "received": [],
        }
        sender = binding("manager").agent_id
        held.hold_message(
            str(folder),
            agent_id,
            HeldMessage(
                sender_line="manager",
                sender_agent_id=sender,
                text=f"work for {name}",
                reason="test",
            ),
        )
        root = self.config.coordination_root / "notes" / "reports" / EXECUTIONS_DIRECTORY
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"{name}.json"
        path.write_text(
            json.dumps(
                {
                    "schema": RECEIPT_SCHEMA,
                    "execution": {"agentId": agent_id},
                    "workspace": {"path": str(folder)},
                    "status": "running",
                    "heldWaiting": 1,
                    "heldMessageLines": [],
                }
            ),
            encoding="utf-8",
        )
        return agent_id, path, str(folder)

    def test_a_queued_receipt_past_the_first_slice_is_reached_within_bounded_ticks(self) -> None:
        """L171-F03: the queue is visited in a rotating slice, not a pinned prefix."""

        for size in (33, 65):
            with self.subTest(queue=size):
                _reset_sweep_cursor()
                targets: list[tuple[str, Any, str]] = []
                for index in range(size):
                    crafted = self.craft_recipient(f"q{size}-{index:03d}")
                    targets.append(crafted)
                    if index < held.SWEEP_MAX_DRAINS_PER_TICK:
                        # The receipts ahead of the target stay held on a permission.
                        self.runtime.agents[crafted[0]]["pendingPermissions"] = [{"name": "Bash"}]
                target_agent, _target_path, _folder = targets[-1]
                ticks = 0
                while not self.received(target_agent) and ticks < 4:
                    held.sweep_held_messages(self.config)
                    ticks += 1
                self.assertEqual(len(self.received(target_agent)), 1)
                self.assertLessEqual(ticks, 2 + size // held.SWEEP_MAX_DRAINS_PER_TICK)
                # The receipts ahead of the slice stay held on their permission; only the
                # ones the sweep reached after it could take their message.
                for position, (created, _path, _dir) in enumerate(targets):
                    if position < held.SWEEP_MAX_DRAINS_PER_TICK:
                        self.assertEqual(self.received(created), [])

    def test_zero_input_cycles_do_no_drain_work(self) -> None:
        for _ in range(3):
            swept = held.sweep_held_messages(self.config)
            self.assertEqual((swept["swept"], swept["delivered"], swept["stillHeld"]), (0, 0, 0))

    def test_idle_fleet_reads_stay_bounded_at_two_sizes(self) -> None:
        # The sealed census boundary: an idle fleet costs a bounded rotation per
        # tick, never a full census; several zero-input cycles stay bounded too.
        # Enumeration is measured separately from reads, on every cycle.
        original_rglob = Path.rglob
        agent, path, _folder = self.craft_recipient("seed")
        held.sweep_held_messages(self.config)
        template = json.loads(path.read_text(encoding="utf-8"))
        root = path.parent
        for count in [64, 256]:
            for index in range(count - 1):
                (root / f"empty-{index:04d}.json").write_text(json.dumps(template))
            for _cycle in range(3):
                yielded: list[str] = []

                def counted(this, *args, _out=yielded, **kwargs):
                    for found in original_rglob(this, *args, **kwargs):
                        _out.append(str(found))
                        yield found

                with (
                    patch.object(Path, "rglob", counted),
                    patch.object(held, "_read_receipt", wraps=held._read_receipt) as reader,
                ):
                    result = held.sweep_held_messages(self.config)
                self.assertEqual(len(yielded), 0)
                self.assertLessEqual(reader.call_count, held.SWEEP_MAX_RECEIPT_READS_PER_TICK)
                self.assertEqual(result["swept"], 0)
            for target in root.glob("empty-*.json"):
                target.unlink()
        self.assertEqual(len(self.received(agent)), 1)

    def test_a_small_fleet_drains_in_one_tick(self) -> None:
        agents = [self.craft_recipient(f"small-{index}")[0] for index in range(3)]

        swept = held.sweep_held_messages(self.config)

        self.assertEqual((swept["swept"], swept["delivered"], swept["stillHeld"]), (3, 3, 0))
        for agent_id in agents:
            self.assertEqual(len(self.received(agent_id)), 1)

    def test_a_large_backlog_rotates_under_the_cap_with_dead_and_history(self) -> None:
        live = [self.craft_recipient(f"recipient-{index:02d}")[0] for index in range(40)]
        dead = [self.craft_recipient(f"gone-{index}")[0] for index in range(3)]
        for agent_id in dead:
            self.runtime.agents[agent_id]["archivedAt"] = ARCHIVED_AT
        history_root = (
            self.config.coordination_root / "notes" / "reports" / EXECUTIONS_DIRECTORY / "history"
        )
        history_root.mkdir(parents=True, exist_ok=True)
        historic_id, historic_path, historic_folder = self.craft_recipient("historic")
        moved = history_root / "historic.json"
        historic_path.rename(moved)

        first = held.sweep_held_messages(self.config)

        self.assertEqual(first["swept"], held.SWEEP_MAX_DRAINS_PER_TICK)
        second = held.sweep_held_messages(self.config)
        self.assertLessEqual(second["swept"], held.SWEEP_MAX_DRAINS_PER_TICK)
        third = held.sweep_held_messages(self.config)
        for agent_id in live:
            texts = self.received(agent_id)
            self.assertEqual(len(texts), 1)
            self.assertTrue(texts[0].endswith(f"work for {self.name_of(agent_id)}"))
        # Dead recipients end as not delivered with a terminal line each.
        for agent_id in dead:
            self.assertEqual(self.received(agent_id), [])
        # The history receipt never drains: its store is untouched.
        self.assertEqual(self.received(historic_id), [])
        self.assertEqual(held.waiting_total(historic_folder, historic_id), 1)
        self.assertEqual((third["swept"], third["delivered"], third["stillHeld"]), (0, 0, 0))

    def name_of(self, agent_id: str) -> str:
        """The fleet name behind one crafted agent, read back from its receipt."""

        root = self.config.coordination_root / "notes" / "reports" / EXECUTIONS_DIRECTORY
        for path in sorted(root.glob("*.json")):
            receipt = json.loads(path.read_text(encoding="utf-8"))
            if (receipt.get("execution") or {}).get("agentId") == agent_id:
                return path.stem
        raise AssertionError(f"no receipt for {agent_id}")

    def test_working_folder_removal_reclaims_the_store_and_terminates_cleanly(self) -> None:
        agent_id, path, folder = self.craft_recipient("reclaimed")

        shutil.rmtree(folder)
        first = held.sweep_held_messages(self.config)

        self.assertEqual(self.received(agent_id), [])
        receipt = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(receipt.get("heldWaiting"), 0)
        second = held.sweep_held_messages(self.config)
        self.assertEqual((second["swept"], second["delivered"], second["stillHeld"]), (0, 0, 0))
        self.assertEqual(first["delivered"], 0)


class TurnEndDeliveryTests(HeldQueueRecoveryTests):
    """MIK-R171 rules 1 and 5: the host's own record of a turn end delivers the queue."""

    def publish_activity(self, agents: dict[str, str], *, state: str = "idle") -> None:
        """Write the host's activity record for the given agents at the given times."""

        directory = activity_directory(self.config)
        assert directory is not None, "the fixture host has a Paseo home"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / HOST_ACTIVITY_FILE).write_text(
            json.dumps(
                {
                    "bootId": "boot-1",
                    "complete": True,
                    "agents": {
                        agent_id: {"state": state, "eventAt": event_at}
                        for agent_id, event_at in agents.items()
                    },
                    "ignoredEvents": 0,
                }
            ),
            encoding="utf-8",
        )

    def folder_of(self, agent_id: str) -> tuple[Path, Path]:
        """The receipt path and working folder of one recipient with a store."""

        located = tools._locate_recipient_store(
            self.config, tools.RoleMessageCall(text="lookup", agent_id=agent_id), agent_id
        )
        assert located is not None
        return located[0], Path(located[2])

    def test_an_unchanged_publication_is_parsed_once_across_ticks(self) -> None:
        """The tick's own input is the host's file: while it holds, nothing is read again."""

        manager = binding("manager")
        worker = self.start(manager, "worker")["agentId"]
        self.publish_activity({worker: "2026-10-17T05:27:09+00:00"})
        reads: list[str] = []
        real_read = host_activity_module._read_publication

        def counted(path: Path) -> Any:
            reads.append(str(path))
            return real_read(path)

        with patch.object(host_activity_module, "_read_publication", counted):
            seen: dict[str, str] = {}
            # One tick reads it; the next three find the same file and only stat it.
            self.assertEqual(turn_ended_agents(self.config, seen), [worker])
            for _ in range(3):
                self.assertEqual(turn_ended_agents(self.config, seen), [])
            # A host write is new input again.
            self.publish_activity({worker: "2026-10-17T05:27:10+00:00"})
            self.assertEqual(turn_ended_agents(self.config, seen), [worker])

        self.assertEqual(len(reads), 2, reads)

    def test_a_turn_end_delivers_what_waits_without_another_message(self) -> None:
        manager = binding("manager")
        worker = self.start(manager, "worker")["agentId"]
        self.runtime.agents[worker]["pendingPermissions"] = [{"name": "Bash"}]
        answer = self.message(manager, agent_id=worker, text="held until the turn ends")
        self.assertEqual(answer["status"], "held")
        self.assertEqual(self.received(worker), [])
        receipt_path, folder = self.folder_of(worker)

        # The prompt is answered and the turn ends. Nothing is sent to the worker: only the
        # host's own record of the turn end drives the delivery.
        self.runtime.agents[worker].update(pendingPermissions=[], status="idle")
        self.publish_activity({worker: "2026-10-17T05:27:09+00:00"})

        seen: dict[str, str] = {}
        self.assertEqual(turn_ended_agents(self.config, seen), [worker])
        # One turn end is handed over once, not on every look.
        self.assertEqual(turn_ended_agents(self.config, seen), [])
        delivered = held.deliver_ended_turns(self.config, [worker])

        self.assertEqual((delivered["swept"], delivered["delivered"]), (1, 1))
        received = self.received(worker)
        self.assertEqual(len(received), 1)
        self.assertTrue(received[0].endswith("held until the turn ends"))
        self.assertEqual(held.waiting_total(folder, worker), 0)
        self.assertEqual(held.held_queue_receipts(self.config), [])
        lines = (read_receipt(receipt_path) or {}).get("heldMessageLines") or []
        self.assertEqual([line["how"] for line in lines], ["delivered"])

    def test_a_turn_end_holds_the_message_while_a_permission_still_waits(self) -> None:
        manager = binding("manager")
        worker = self.start(manager, "worker")["agentId"]
        self.runtime.agents[worker]["pendingPermissions"] = [{"name": "Bash"}]
        self.assertEqual(
            self.message(manager, agent_id=worker, text="still waiting")["status"], "held"
        )
        self.publish_activity({worker: "2026-10-17T05:27:09+00:00"})

        delivered = held.deliver_ended_turns(self.config, [worker])

        self.assertEqual((delivered["delivered"], delivered["stillHeld"]), (0, 1))
        self.assertEqual(self.received(worker), [])
        self.assertEqual(held.waiting_total(self.folder_of(worker)[1], worker), 1)

    def test_a_failed_drain_writes_one_line_with_the_recipient_and_the_reason(self) -> None:
        _manager, worker, located = self.hold_open_worker("the store cannot be drained")
        self.open_worker(worker)

        with (
            patch.object(held, "drain_held", side_effect=RuntimeError("the store is unreadable")),
            self.assertLogs("agents_remember.cli.role_held_messages", level="WARNING") as logs,
        ):
            swept = held.sweep_held_messages(self.config)

        self.assertEqual((swept["swept"], swept["stillHeld"], swept["delivered"]), (1, 1, 0))
        written = " ".join(logs.output)
        self.assertIn(worker, written)
        self.assertIn("the store is unreadable", written)
        self.assertEqual(held.waiting_total(located[2], worker), 1)

    def test_a_receipt_count_equals_the_messages_its_store_holds(self) -> None:
        manager, worker, located = self.hold_open_worker("counted once")
        self.assertEqual((read_receipt(located[0]) or {}).get("heldWaiting"), 1)

        self.assertEqual(
            self.message(manager, agent_id=worker, text="counted twice")["status"], "held"
        )
        self.assertEqual((read_receipt(located[0]) or {}).get("heldWaiting"), 2)
        self.assertEqual(held.waiting_total(located[2], worker), 2)

        self.open_worker(worker)
        held.apply_drain_to_receipt(
            located[0], held.drain_held(self.config, located[0], located[1])
        )
        self.assertEqual((read_receipt(located[0]) or {}).get("heldWaiting"), 0)
        self.assertEqual(held.waiting_total(located[2], worker), 0)


if __name__ == "__main__":
    unittest.main()
