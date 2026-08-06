from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agents_remember.errors import (
    CodexAppServerError,
    HarnessBridgeEpochMismatchError,
    HarnessControlError,
)
from agents_remember.serving.harness_control_bridge import HarnessControlBridge
from agents_remember.serving.harness_control_client import (
    ControlSubmission,
    read_control_evidence,
    read_control_native_page,
    read_control_snapshot,
    read_submission_authority,
    read_submission_provenance,
    submit_control_prompt,
)
from agents_remember.serving.harness_control_ipc import HarnessControlServer, LocalControlEndpoint
from agents_remember.serving.harness_control_models import (
    AR_EVIDENCE_KEY,
    ControlIdentity,
    NativeEvidenceFrame,
    NativeEvidencePage,
)
from test_harness_control_evidence import (
    NOW,
    _codex_adapter,
    _ControlledEntry,
    _EvidenceAdapter,
    _FakeCodexTransport,
    _identity,
    _launch,
    _NativePageAdapter,
    _obj,
    _prime_codex_start,
    _settle,
    _thread_item_page,
    _ThreadAwareNativePageAdapter,
)


class EvidenceIpcTests(unittest.IsolatedAsyncioTestCase):
    async def _serve(self, adapter: _EvidenceAdapter, identity: ControlIdentity, tmp: str):
        bridge = HarnessControlBridge(identity, adapter, clock=lambda: NOW)
        await bridge.start(_launch(identity))
        endpoint = LocalControlEndpoint.for_session(Path(tmp), identity)
        server = HarnessControlServer(endpoint, bridge)
        await server.start()
        return bridge, server, _ControlledEntry(identity, endpoint.path)

    async def test_evidence_action_round_trip_with_epoch_and_paging(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            identity = _identity("ipc-evidence")
            adapter = _EvidenceAdapter()
            bridge, server, entry = await self._serve(adapter, identity, tmp)
            try:
                for index in range(1, 4):
                    adapter.emit("state", {"n": index, AR_EVIDENCE_KEY: {"n": index}})
                await _settle()
                descriptor = await asyncio.to_thread(read_submission_authority, entry)
                page = await asyncio.to_thread(
                    read_control_evidence,
                    entry,
                    expected_bridge_epoch=descriptor.bridge_epoch,
                )
                self.assertEqual([frame.sequence for frame in page.frames], [1, 2, 3])
                self.assertEqual(page.bridge_epoch, descriptor.bridge_epoch)
                continuation = await asyncio.to_thread(
                    read_control_evidence, entry, after_sequence=2
                )
                self.assertEqual([frame.sequence for frame in continuation.frames], [3])
                # Snapshot raw over IPC never carries the diverted payloads.
                snapshot = await asyncio.to_thread(read_control_snapshot, entry)
                self.assertNotIn(AR_EVIDENCE_KEY, snapshot.raw)
                with self.assertRaises(HarnessBridgeEpochMismatchError):
                    await asyncio.to_thread(
                        read_control_evidence,
                        entry,
                        expected_bridge_epoch="not-the-epoch",
                    )
            finally:
                await server.close()
                await bridge.stop("forced")

    async def test_evidence_thread_id_round_trips_over_ipc(self) -> None:
        """The multiplexed demux key crosses the control socket: pre-L7 the evidence
        frame JSON had no threadId, so a dashboard-side projector received every
        frame as thread-less and bound all agent content to the parent conversation.
        """
        with tempfile.TemporaryDirectory() as tmp:
            identity = _identity("ipc-thread-id")
            adapter = _EvidenceAdapter()
            bridge, server, entry = await self._serve(adapter, identity, tmp)
            try:
                adapter.emit(
                    "state", {"n": 1, AR_EVIDENCE_KEY: {"n": 1, "threadId": "agent-thread-9"}}
                )
                adapter.emit("state", {"n": 2, AR_EVIDENCE_KEY: {"n": 2}})
                await _settle()
                descriptor = await asyncio.to_thread(read_submission_authority, entry)
                page = await asyncio.to_thread(
                    read_control_evidence,
                    entry,
                    expected_bridge_epoch=descriptor.bridge_epoch,
                )
                self.assertEqual(page.frames[0].thread_id, "agent-thread-9")
                # Absent on parent frames: the pre-multiplex wire stays identical.
                self.assertIsNone(page.frames[1].thread_id)
            finally:
                await server.close()
                await bridge.stop("forced")

    async def test_cross_domain_coordinates_fail_typed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            identity = _identity("ipc-domain")
            adapter = _EvidenceAdapter()
            bridge, server, entry = await self._serve(adapter, identity, tmp)
            try:
                with self.assertRaises(HarnessControlError):
                    await asyncio.to_thread(
                        read_control_evidence,
                        entry,
                        after_sequence="native-cursor-9",  # type: ignore[arg-type]
                    )
                with self.assertRaises(HarnessControlError):
                    await asyncio.to_thread(
                        read_control_native_page,
                        entry,
                        cursor=42,  # type: ignore[arg-type]
                    )
            finally:
                await server.close()
                await bridge.stop("forced")

    async def test_native_page_unsupported_fails_closed_with_adapter_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            identity = _identity("ipc-unsupported")
            adapter = _EvidenceAdapter()
            bridge, server, entry = await self._serve(adapter, identity, tmp)
            try:
                with self.assertRaises(HarnessControlError) as caught:
                    await asyncio.to_thread(read_control_native_page, entry)
                self.assertIn("_EvidenceAdapter", str(caught.exception))
                self.assertIn("does not support native evidence pages", str(caught.exception))
            finally:
                await server.close()
                await bridge.stop("forced")

    async def test_native_page_bridge_epoch_stamped_and_frames_validated(self) -> None:
        preset = NativeEvidencePage(
            frames=(
                NativeEvidenceFrame(
                    native_id="entry-1",
                    native_parent_id=None,
                    native_type="message",
                    created_at=None,
                    raw={"id": "entry-1", "type": "message"},
                ),
                NativeEvidenceFrame(
                    native_id="entry-2",
                    native_parent_id="entry-1",
                    native_type="message",
                    created_at=None,
                    raw={"id": "entry-2", "type": "message"},
                ),
            ),
            next_cursor="entry-2",
            truncated=True,
            bridge_epoch="",
        )
        with tempfile.TemporaryDirectory() as tmp:
            identity = _identity("ipc-native")
            adapter = _NativePageAdapter(preset)
            bridge, server, entry = await self._serve(adapter, identity, tmp)
            try:
                descriptor = await asyncio.to_thread(read_submission_authority, entry)
                page = await asyncio.to_thread(
                    read_control_native_page,
                    entry,
                    expected_bridge_epoch=descriptor.bridge_epoch,
                )
                self.assertEqual(page.bridge_epoch, descriptor.bridge_epoch)
                self.assertEqual(
                    [(f.native_id, f.native_parent_id, f.native_type) for f in page.frames],
                    [("entry-1", None, "message"), ("entry-2", "entry-1", "message")],
                )
                self.assertEqual(page.next_cursor, "entry-2")
                self.assertTrue(page.truncated)
                self.assertEqual(adapter.calls[0][1], 200)
            finally:
                await server.close()
                await bridge.stop("forced")

    async def test_native_page_thread_id_is_additive_over_ipc(self) -> None:
        # The evidence-native-page action carries an optional threadId
        # end-to-end; absent keeps the exact single-thread adapter call, and non-text
        # or empty selectors fail typed before any adapter call.
        preset = NativeEvidencePage(frames=(), next_cursor=None, truncated=False, bridge_epoch="")
        with tempfile.TemporaryDirectory() as tmp:
            identity = _identity("ipc-thread")
            adapter = _ThreadAwareNativePageAdapter(preset)
            bridge, server, entry = await self._serve(adapter, identity, tmp)
            try:
                page = await asyncio.to_thread(
                    read_control_native_page, entry, thread_id="agent-thread-1"
                )
                self.assertEqual(page.frames, ())
                self.assertEqual(adapter.thread_calls[-1][3], "agent-thread-1")

                parent = await asyncio.to_thread(read_control_native_page, entry)
                self.assertEqual(parent.frames, ())
                # No threadId on the wire => the bridge keeps the single-thread call shape.
                self.assertIsNone(adapter.thread_calls[-1][3])

                with self.assertRaises(HarnessControlError):
                    await asyncio.to_thread(read_control_native_page, entry, thread_id="")
                self.assertEqual(len(adapter.thread_calls), 2)
            finally:
                await server.close()
                await bridge.stop("forced")

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_harness_control_evidence_ipc.py:228).
    async def test_submission_provenance_all_sources_epoch_and_bounds(
        self,
    ) -> None:  # pragma: no cover
        with tempfile.TemporaryDirectory() as tmp:
            identity = _identity("ipc-provenance")
            adapter = _EvidenceAdapter()
            bridge, server, entry = await self._serve(adapter, identity, tmp)
            try:
                descriptor = await asyncio.to_thread(read_submission_authority, entry)
                epoch = descriptor.bridge_epoch
                cockpit = await asyncio.to_thread(
                    submit_control_prompt,
                    entry,
                    "cockpit prompt",
                    ControlSubmission(
                        source="cockpit", request_id="prov-cockpit", expected_bridge_epoch=epoch
                    ),
                )
                self.assertEqual(cockpit.acceptance, "immediate")
                await asyncio.to_thread(
                    submit_control_prompt,
                    entry,
                    "terminal prompt",
                    ControlSubmission(source="terminal", request_id="prov-terminal"),
                )
                await asyncio.to_thread(
                    submit_control_prompt,
                    entry,
                    "durable prompt",
                    ControlSubmission(source="durable", request_id="prov-durable"),
                )
                ids = ("prov-cockpit", "prov-terminal", "prov-durable", "prov-missing")
                batch = None
                completed: set[str] = set()
                for _ in range(400):
                    for rid in ids[:3]:
                        if rid not in completed and any(
                            item.request_id == rid for item in adapter.submissions
                        ):
                            adapter.complete(rid)
                            completed.add(rid)
                    batch = await asyncio.to_thread(
                        read_submission_provenance,
                        entry,
                        expected_bridge_epoch=epoch,
                        request_ids=ids,
                    )
                    if all(item.state == "delivered" for item in batch.provenance[:3]):
                        break
                    await asyncio.sleep(0)
                assert batch is not None
                sources = {item.request_id: item.source for item in batch.provenance}
                self.assertEqual(
                    sources,
                    {
                        "prov-cockpit": "cockpit",
                        "prov-terminal": "terminal",
                        "prov-durable": "durable",
                        "prov-missing": None,
                    },
                )
                self.assertEqual(batch.provenance[3].outcome, "not-found")
                states = {item.request_id: item.state for item in batch.provenance[:3]}
                self.assertEqual(set(states.values()), {"delivered"})
                self.assertTrue(all(item.submitted_at for item in batch.provenance[:3]))
                self.assertTrue(all(item.accepted_at for item in batch.provenance[:3]))
                self.assertTrue(all(item.vendor_correlation_id for item in batch.provenance[:3]))
                with self.assertRaises(HarnessControlError):
                    await asyncio.to_thread(
                        read_submission_provenance,
                        entry,
                        expected_bridge_epoch=epoch,
                        request_ids=("prov-cockpit", "prov-cockpit"),
                    )
                with self.assertRaises(HarnessControlError):
                    await asyncio.to_thread(
                        read_submission_provenance,
                        entry,
                        expected_bridge_epoch=epoch,
                        request_ids=tuple(f"too-many-{index}" for index in range(65)),
                    )
            finally:
                await server.close()
                await bridge.stop("forced")

    async def test_fixture_shaped_response_without_live_epoch_fails_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            identity = _identity("ipc-authority")
            adapter = _EvidenceAdapter()
            bridge, server, entry = await self._serve(adapter, identity, tmp)
            try:
                # A canned fixture shape missing the live bridgeEpoch can never pass as a page.
                canned = {
                    "frames": [],
                    "latestSequence": 0,
                    "evictedBeforeSequence": 0,
                    "truncated": False,
                }
                with (
                    mock.patch(
                        "agents_remember.serving.harness_control_client.request_control",
                        return_value=canned,
                    ),
                    self.assertRaises(HarnessControlError),
                ):
                    await asyncio.to_thread(read_control_evidence, entry)
            finally:
                await server.close()
                await bridge.stop("forced")


class CodexEvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_dropped_item_and_token_usage_frames_reach_evidence_with_native_ids(self) -> None:
        transport = _FakeCodexTransport()
        _prime_codex_start(transport)
        adapter = _codex_adapter(transport)
        await adapter.start(_launch(_identity("codex-live"), harness_id="codex"))
        events = adapter.subscribe()
        try:
            transport.emit(
                {
                    "method": "item/completed",
                    "params": {
                        "threadId": "thread-1",
                        "turnId": "turn-1",
                        "completedAtMs": 1784023200000,
                        "item": {
                            "id": "item-9",
                            "type": "commandExecution",
                            "command": "ls -la",
                            "aggregatedOutput": "ok",
                            "exitCode": 0,
                            "status": "completed",
                        },
                    },
                }
            )
            transport.emit(
                {
                    "method": "thread/tokenUsage/updated",
                    "params": {
                        "threadId": "thread-1",
                        "tokenUsage": {"total": 42, "last": 7},
                    },
                }
            )
            dropped = await asyncio.wait_for(anext(events), timeout=1.0)
            self.assertEqual(dropped.kind, "codex-notification")
            self.assertEqual(dropped.raw["codexMethod"], "item/completed")
            payload = _obj(dropped.raw[AR_EVIDENCE_KEY])
            dropped_item = _obj(payload["item"])
            self.assertEqual(dropped_item["id"], "item-9")
            self.assertEqual(dropped_item["type"], "commandExecution")
            # The dropped path still produces no transcript entries (byte-preserved).
            self.assertEqual(dropped.transcript, ())
            usage = await asyncio.wait_for(anext(events), timeout=1.0)
            self.assertEqual(usage.raw["codexMethod"], "thread/tokenUsage/updated")
            usage_payload = _obj(usage.raw[AR_EVIDENCE_KEY])
            self.assertEqual(_obj(usage_payload["tokenUsage"])["total"], 42)
            # The adapter's own snapshot never carries the reserved key.
            self.assertNotIn(AR_EVIDENCE_KEY, (await adapter.snapshot()).raw)
        finally:
            await adapter.stop("forced")

    async def test_native_page_continuation_no_overlap_no_gap_and_fail_closed_cursor(self) -> None:
        transport = _FakeCodexTransport()
        _prime_codex_start(transport)
        items = [
            {"id": f"item-{index}", "type": item_type, "text": f"text {index}"}
            for index, item_type in enumerate(
                ("userMessage", "reasoning", "commandExecution", "agentMessage", "fileChange"),
                start=1,
            )
        ]
        for index, item in enumerate(items):
            next_cursor = f"source-{index + 1}" if index + 1 < len(items) else None
            transport.queue(
                "thread/items/list",
                _thread_item_page(item, next_cursor=next_cursor),
            )
        adapter = _codex_adapter(transport)
        await adapter.start(_launch(_identity("codex-page"), harness_id="codex"))
        try:
            first = await adapter.read_native_page(cursor=None, limit=2, byte_budget=48 * 1024)
            self.assertEqual([f.native_id for f in first.frames], ["item-1", "item-2"])
            self.assertEqual(first.frames[1].native_type, "reasoning")
            self.assertEqual(first.frames[1].native_parent_id, "turn-1")
            self.assertIsNotNone(first.next_cursor)
            first_cursor = first.next_cursor
            assert first_cursor is not None
            self.assertTrue(first_cursor.startswith("ar-cnh1."))
            self.assertTrue(first.truncated)
            self.assertEqual(first.bridge_epoch, "")
            second = await adapter.read_native_page(
                cursor=first_cursor, limit=2, byte_budget=48 * 1024
            )
            self.assertEqual([f.native_id for f in second.frames], ["item-3", "item-4"])
            third = await adapter.read_native_page(
                cursor=second.next_cursor, limit=2, byte_budget=48 * 1024
            )
            self.assertEqual([f.native_id for f in third.frames], ["item-5"])
            self.assertIsNone(third.next_cursor)
            self.assertFalse(third.truncated)
            chained = [f.native_id for page in (first, second, third) for f in page.frames]
            self.assertEqual(chained, [f"item-{index}" for index in range(1, 6)])
            with self.assertRaises(CodexAppServerError):
                await adapter.read_native_page(
                    cursor="item-does-not-exist", limit=2, byte_budget=48 * 1024
                )
        finally:
            await adapter.stop("forced")

    async def test_native_page_duplicate_item_identity_fails_closed(self) -> None:
        transport = _FakeCodexTransport()
        _prime_codex_start(transport)
        duplicate = {"id": "item-1", "type": "userMessage", "text": "dup"}
        transport.queue(
            "thread/items/list",
            _thread_item_page(duplicate, next_cursor="source-1"),
        )
        transport.queue(
            "thread/items/list",
            _thread_item_page(duplicate, next_cursor=None),
        )
        adapter = _codex_adapter(transport)
        await adapter.start(_launch(_identity("codex-dup"), harness_id="codex"))
        try:
            with self.assertRaises(CodexAppServerError):
                await adapter.read_native_page(cursor=None, limit=10, byte_budget=48 * 1024)
        finally:
            await adapter.stop("forced")

    async def test_native_page_oversized_single_frame_is_clipped_and_progresses(self) -> None:
        transport = _FakeCodexTransport()
        _prime_codex_start(transport)
        items = [
            {"id": "item-big", "type": "agentMessage", "text": "z" * 65536},
            {"id": "item-next", "type": "userMessage", "text": "n" * 65536},
        ]
        transport.queue(
            "thread/items/list",
            _thread_item_page(items[0], next_cursor="source-1"),
        )
        transport.queue(
            "thread/items/list",
            _thread_item_page(items[1], next_cursor=None),
        )
        # The first page observes item-next but cannot fit it after the clipped
        # large item. The reader-owned opaque continuation retains that unconsumed
        # frame; resuming must not request or decode the source page again.
        adapter = _codex_adapter(transport)
        await adapter.start(_launch(_identity("codex-clip"), harness_id="codex"))
        try:
            page = await adapter.read_native_page(cursor=None, limit=10, byte_budget=1024)
            self.assertGreaterEqual(len(page.frames), 1)
            self.assertEqual(page.frames[0].native_id, "item-big")
            # The oversized item clips content-first: the frame still parses as its exact
            # native shape (id/type survive) with the giant text visibly truncated in place.
            self.assertEqual(page.frames[0].raw["arEvidenceContentTruncated"], True)
            self.assertEqual(page.frames[0].raw["type"], "agentMessage")
            text = page.frames[0].raw["text"]
            assert isinstance(text, str)
            self.assertIn("…[truncated]", text)
            self.assertLess(len(text), 4096)
            # Continuation from the last returned frame never overlaps and never gaps.
            chained = [f.native_id for f in page.frames]
            cursor = page.next_cursor
            self.assertIsNotNone(cursor)
            rest = await adapter.read_native_page(cursor=cursor, limit=10, byte_budget=1024)
            self.assertFalse({f.native_id for f in rest.frames} & set(chained))
            self.assertEqual(
                chained + [f.native_id for f in rest.frames], ["item-big", "item-next"]
            )
            self.assertEqual(
                [method for method, _params in transport.requests].count("thread/items/list"),
                2,
            )
        finally:
            await adapter.stop("forced")
