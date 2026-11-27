"""Closed launch receipts fence held message writers and preserve waiting text."""

from __future__ import annotations

import json
import shutil
import threading
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from agents_remember.cli import developer_delivery, paseo_role_tools, provider_limit_drain
from agents_remember.cli import role_held_messages as held
from agents_remember.cli import role_launch_receipts as receipts
from agents_remember.models.role_agents import RoleStartSetCall
from agents_remember.serving import _app_lifespan
from agents_remember.serving.projections import host_activity
from agents_remember.tasks import write_task_doc
from agents_remember.tasks.document import SubTaskRef
from agents_remember_test_support.testing.waits import HANG_GUARD_SECONDS
from paseo_role_tools_test_support import ARCHIVED_AT, RoleToolsTestCase, binding, selection_of
from test_native_retire import retire

pytestmark = pytest.mark.integration
LIMIT = {"limitedUntil": "2099-01-01T00:00:00Z", "detail": "test provider limit"}


class ClosedHeldStoreTests(RoleToolsTestCase):
    def setUp(self) -> None:
        super().setUp()
        master = self.master.document.model_copy(
            update={"subTasks": [SubTaskRef(number="01_LEAF", name="Leaf", file="01_leaf.json")]}
        )
        write_task_doc(self.master.path.parent, master)

    def _worker(self):
        manager = binding("manager")
        worker = self.start(manager, "worker")["agentId"]
        located = paseo_role_tools._locate_recipient_store(
            self.config, paseo_role_tools.RoleMessageCall(text="lookup", agent_id=worker), worker
        )
        assert located is not None
        path, receipt, folder = located
        return manager, worker, path, receipt, Path(folder)

    def _closed_worker(self):
        manager, worker, path, receipt, folder = self._worker()
        receipt["leafArchive"] = {
            "closing": True,
            "taskDocumentRef": receipt["selection"]["taskDocumentRef"],
        }
        receipt["heldWaiting"] = 1
        receipts._write_receipt(path, receipt, archive_update=True)
        shutil.rmtree(folder)
        return manager, worker, path, receipt, folder

    def test_role_message_by_id_refuses_closed_seat_before_store_creation(self) -> None:
        manager, worker, _, _, folder = self._closed_worker()
        with patch.object(paseo_role_tools, "provider_limit_hold", return_value=LIMIT):
            result = self.message(manager, agent_id=worker, text="late role message")
        assert not folder.exists(), "late role_message recreated the closed seat's workspace"
        assert result["refusal"] == "recipient-archived"
        assert worker in result["detail"]

    def test_developer_message_refuses_closed_seat_before_store_creation(self) -> None:
        _, worker, path, receipt, folder = self._closed_worker()
        delivery = developer_delivery.DeveloperDelivery(
            self.config, worker, path, receipt, "Developer", "late developer text", {}
        )
        try:
            with patch.object(developer_delivery, "provider_limit_hold", return_value=LIMIT):
                developer_delivery.deliver_or_hold(delivery)
        except developer_delivery._Refused as error:
            assert error.refusal == "recipient-archived"
            assert worker in error.detail
        else:
            pytest.fail("developer Send did not name the closed seat")
        finally:
            assert not folder.exists(), "late developer Send recreated the closed seat's workspace"

    def test_turn_end_tick_refuses_closed_queued_seat_without_creating_workspace(self) -> None:
        _, worker, path, _, folder = self._closed_worker()
        held.note_held_receipt(self.config, path)
        with patch.object(host_activity, "turn_ended_agents", return_value=[worker]):
            _app_lifespan._held_delivery_tick(self.config, {}, sweep=False)
        assert not folder.exists(), "the turn-end tick recreated the closed seat's workspace"
        assert path not in held.held_queue_receipts(self.config)

    def test_held_sweep_refuses_closed_queued_seat_without_creating_workspace(self) -> None:
        _, _, path, _, folder = self._closed_worker()
        held.note_held_receipt(self.config, path)
        held._reset_sweep_cursor()
        held.sweep_held_messages(self.config)
        assert not folder.exists(), "the periodic held sweep recreated the closed seat's workspace"
        assert path not in held.held_queue_receipts(self.config)

    def test_late_retire_does_not_recreate_closed_seat_workspace(self) -> None:
        manager, worker, _, _, folder = self._closed_worker()
        self.runtime.agents[worker]["archivedAt"] = ARCHIVED_AT
        result = retire(self, manager, worker)
        assert not folder.exists(), "a repeat seat close recreated the closed seat's workspace"
        assert result["status"] == "already-retired"
        assert worker in result["detail"]

    def test_provider_limit_stamp_refuses_closed_inflight_batch(self) -> None:
        _, worker, path, receipt, folder = self._closed_worker()
        batch = held._DrainBatch(self.config, path, receipt, str(folder), worker, [], "claim-1")
        try:
            provider_limit_drain.claim_ended_stamp(batch, "ended")
        except ValueError as error:
            assert worker in str(error)
        else:
            pytest.fail("provider-limit drain did not name the closed seat")
        finally:
            assert not folder.exists(), "provider-limit drain recreated the closed seat's workspace"

    def test_missing_store_reads_and_counts_create_no_files(self) -> None:
        _, worker, _, receipt, folder = self._worker()
        assert held.waiting_total(folder, worker) == 0
        held.recount_waiting_for(receipt)
        assert receipt["heldWaiting"] == 0
        assert held._read_store_counts(folder, worker, "sender") == (0, 0)
        assert not (folder / ".ar-product" / "held-messages").exists(), (
            "a count created a held-message lock for the absent store"
        )

    def test_cancelled_turn_preserves_waiting_text_and_accepts_the_next_send(self) -> None:
        request, worker = self.started(status="idle")
        folder = Path(self.receipt(request)["workspace"]["path"])
        _take(folder, worker)
        store = held.store_path_for(folder, worker)
        waiting = store.read_bytes()

        stopped = self.close_execution(request, "stopped")

        assert store.exists(), "cancelled turn settled a live recipient's held store"
        assert store.read_bytes() == waiting
        assert "heldStoreClosures" not in stopped
        result = self.message(
            self.architect, role="worker", text="next message", **selection_of("worker")
        )
        assert (result["status"], result["recipientAgentId"]) == ("accepted", worker), result
        received = self.received(worker)
        assert len(received) == 1
        assert "exact waiting text" in received[0] and "next message" in received[0]
        assert received[0].index("exact waiting text") < received[0].index("next message")

    def test_stopped_receipt_alone_allows_a_held_write(self) -> None:
        manager, worker, path, receipt, folder = self._worker()
        receipts._write_receipt(path, {**receipt, "status": "stopped"})
        with patch.object(paseo_role_tools, "provider_limit_hold", return_value=LIMIT):
            result = self.message(manager, agent_id=worker, text="hold for the live seat")

        assert result.get("status") == "held", f"stopped receipt fenced a live held write: {result}"
        assert held.waiting_total(folder, worker) == 1
        assert "heldStoreClosures" not in json.loads(path.read_text())

    def test_replacement_settles_the_predecessor_after_host_archive(self) -> None:
        request, predecessor = self.started("manager", status="idle")
        path = self.receipt_path(request)
        receipt = self.receipt(request)
        folder = Path(receipt["workspace"]["path"])
        store = held.store_path_for(folder, predecessor)
        _take(folder, predecessor)
        self.close_execution(request, "stopped")
        archive = self.runtime._agent_archive

        def observe_archive(payload: dict[str, Any]) -> dict[str, Any]:
            assert payload["agentId"] == predecessor
            assert store.is_file(), "replacement settled a live seat before host archive"
            return archive(payload)

        with patch.object(self.runtime, "_agent_archive", side_effect=observe_archive) as observed:
            status, successor = self.dispatch(self.request("manager"))

        assert (status, successor["status"]) == (200, "running")
        observed.assert_called_once()
        history = json.loads((path.parent / "history" / f"{request.request_id}.json").read_text())
        assert [row["text"] for row in history["heldStoreClosures"]["0"]["waitingMessages"]] == [
            "exact waiting text"
        ]
        assert not store.exists()

    def test_first_send_waits_for_a_prepared_launch_to_register(self) -> None:
        request = self.request("worker")
        self.runtime.fail("agent-create", "paseo_bridge_timeout")
        status, result = self.dispatch(request)
        prepared = self.receipt(request)
        agent_id = prepared["agentId"]
        folder = Path(prepared["workspace"]["path"])
        assert (status, result["status"]) == (202, "unknown")
        assert agent_id not in self.runtime.agents
        message = "keep this message until the launch registers"

        pending = self.message(self.architect, agent_id=agent_id, text=message)

        assert pending["status"] == "held", "the first send closed a still-prepared launch"
        assert pending["heldReason"] == "start-unfinished"
        waiting = self.receipt(request)
        assert waiting["status"] == "unknown"
        assert held.waiting_total(folder, agent_id) == 1
        status, result = self.dispatch(request)
        registered = self.receipt(request)
        assert (status, result["status"]) == (200, "running")
        assert registered["agentId"] == agent_id
        assert registered.get("heldStoreGeneration", 0) == prepared.get("heldStoreGeneration", 0)

        self.runtime.agents[agent_id]["status"] = "idle"
        accepted = self.message(self.architect, agent_id=agent_id, text="next message")

        assert accepted["status"] == "accepted"
        assert held.waiting_total(folder, agent_id) == 0
        assert len(self.received(agent_id)) == 1
        assert self.received(agent_id)[0].count(message) == 1

    def test_public_send_refuses_its_generation_after_same_id_restart(self) -> None:
        manager = binding("manager")
        call = RoleStartSetCall(request_id=uuid.uuid4(), **selection_of("reviewer"))

        def start_set() -> dict[str, Any]:
            response = paseo_role_tools.start_leaf_set(
                self.config, call, environment=manager.environment()
            )
            assert response["ok"], response
            return response["seats"]["reviewer"]

        first = start_set()
        assert first["status"] == "held", first
        reviewer = first["agentId"]
        opened = self.message(manager, agent_id=reviewer, text="open initial generation")
        assert (opened["status"], opened["recipientAgentId"]) == ("accepted", reviewer), opened
        assert opened["openedHeldSeat"] is True
        self.runtime.agents[reviewer]["status"] = "idle"
        request = self.request("reviewer", uuid.UUID(first["requestId"]))
        path = self.receipt_path(request)
        receipt = self.receipt(request)
        assert isinstance(receipt.get("replayRequest"), dict)
        folder = Path(receipt["workspace"]["path"])
        generation = receipt.get("heldStoreGeneration", 0)
        successor_receipt = b""

        def restart(*_args: Any) -> None:
            nonlocal successor_receipt
            del self.runtime.agents[reviewer]
            assert self.refresh(request)["status"] == "stopped"
            restarted = start_set()
            assert (restarted["status"], restarted["agentId"]) == ("held", reviewer), restarted
            assert self.receipt(request)["heldStoreGeneration"] == generation + 1
            successor_receipt = path.read_bytes()
            self.runtime.calls.clear()

        with patch.object(paseo_role_tools, "_collect_own_reports", side_effect=restart):
            old = self.message(manager, agent_id=reviewer, text="the old send")

        assert old.get("refusal") == "recipient-archived", (
            f"stale send reached the restarted generation: {old}"
        )
        assert reviewer in old["detail"] and "generation" in old["detail"], old
        assert not any(
            command in {"agent-send", "agent-create"} for command, _ in self.runtime.calls
        )
        assert path.read_bytes() == successor_receipt
        assert not held.store_path_for(folder, reviewer).exists()

        fresh = self.message(manager, agent_id=reviewer, text="the fresh send")
        assert (fresh["status"], fresh["recipientAgentId"]) == ("accepted", reviewer), fresh
        assert fresh["openedHeldSeat"] is True
        prompts = [
            payload["prompt"]
            for command, payload in self.runtime.calls
            if command == "agent-create"
            and payload.get("agentId") == reviewer
            and isinstance(payload.get("prompt"), str)
        ]
        assert len(prompts) == 1 and prompts[0].count("the fresh send") == 1

    def test_close_serializes_with_an_admitted_writer(self) -> None:
        from agents_remember.cli.role_held_store import (  # noqa: PLC0415
            HeldSeatClosed,
            held_seat_ref,
            open_held_store,
        )

        request, worker = self.started()
        path = self.receipt_path(request)
        ref = held_seat_ref(path, self.receipt(request))
        folder = ref.workspace
        assert folder is not None
        ready, release, closing = threading.Event(), threading.Event(), threading.Event()
        failures: list[BaseException] = []

        def write() -> None:
            with open_held_store(ref):
                ready.set()
                assert release.wait(HANG_GUARD_SECONDS)
                _take(folder, worker)

        def close() -> None:
            self.runtime.agents[worker]["archivedAt"] = ARCHIVED_AT
            closing.set()
            assert self.refresh(request)["status"] == "stopped"

        def run(action: Any) -> None:
            try:
                action()
            except BaseException as error:
                failures.append(error)

        writer = threading.Thread(target=run, args=(write,))
        closer = threading.Thread(target=run, args=(close,))
        writer.start()
        try:
            assert ready.wait(HANG_GUARD_SECONDS)
            closer.start()
            assert closing.wait(HANG_GUARD_SECONDS)
            assert "heldStoreClosures" not in json.loads(path.read_text())
        finally:
            release.set()
            writer.join(HANG_GUARD_SECONDS)
            if closer.ident is not None:
                closer.join(HANG_GUARD_SECONDS)
        assert not writer.is_alive() and not closer.is_alive()
        assert failures == []
        closed = json.loads(path.read_text())
        assert (
            closed["heldStoreClosures"]["0"]["waitingMessages"][0]["text"] == "exact waiting text"
        )
        assert not held.store_path_for(folder, worker).exists()
        with pytest.raises(HeldSeatClosed, match=worker), open_held_store(ref):
            _take(folder, worker)

    def test_same_request_restart_refuses_the_previous_generation(self) -> None:
        from agents_remember.cli.role_held_store import (  # noqa: PLC0415
            HeldSeatClosed,
            begin_held_generation,
            held_seat_ref,
            open_held_store,
            reconcile_held_store,
        )

        request, worker = self.started()
        path = self.receipt_path(request)
        prior = held_seat_ref(path, self.receipt(request))
        folder = prior.workspace
        assert folder is not None
        self.runtime.agents[worker]["archivedAt"] = ARCHIVED_AT
        assert self.refresh(request)["status"] == "stopped"
        current = begin_held_generation(prior, receipt_updates={"status": "starting"})
        with pytest.raises(HeldSeatClosed, match=worker) as caught, open_held_store(prior):
            _take(folder, worker)
        assert caught.value.reason == "generation-changed"
        assert not held.store_path_for(folder, worker).exists()
        with open_held_store(current):
            _take(folder, worker)
        store = held.store_path_for(folder, worker)
        saved_store, saved_receipt = store.read_bytes(), path.read_bytes()
        with pytest.raises(HeldSeatClosed, match=worker) as stale:
            reconcile_held_store(prior, host_state="archived")
        assert stale.value.reason == "generation-changed"
        assert store.read_bytes() == saved_store and path.read_bytes() == saved_receipt
        assert held.waiting_total(folder, worker) == 1
        assert current.request_id == prior.request_id


def _take(folder: Path, worker: str) -> None:
    held.hold_message(
        folder,
        worker,
        held.HeldMessage("Manager", "sender", "exact waiting text", "recipient-in-turn"),
    )
