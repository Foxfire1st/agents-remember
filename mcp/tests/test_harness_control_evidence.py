"""Contract tests for the native evidence and resume substrate."""

from __future__ import annotations

import asyncio
import base64
import json
import tempfile
import unittest
from collections.abc import AsyncIterator, Mapping, Sequence
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import cast
from unittest import mock

from agents_remember.errors import (
    CodexAppServerError,
    HarnessAdapterDisconnectedError,
    HarnessBridgeEpochMismatchError,
    HarnessControlError,
)
from agents_remember.serving.claude_stream_limits import ClaudeAdapterLimits
from agents_remember.serving.codex_app_server_adapter import (
    CodexAppServerAdapter,
    CodexAppServerSettings,
)
from agents_remember.serving.harness_capabilities import CapabilitySnapshot, SetResult
from agents_remember.serving.harness_control_bridge import HarnessControlBridge
from agents_remember.serving.harness_control_claude import ClaudeStreamJsonAdapter
from agents_remember.serving.harness_control_client import (
    read_control_evidence,
    read_control_native_page,
    read_control_snapshot,
    read_submission_authority,
    read_submission_provenance,
    submit_control_prompt,
)
from agents_remember.serving.harness_control_factories import create_harness_protocol_adapter
from agents_remember.serving.harness_control_ipc import HarnessControlServer, LocalControlEndpoint
from agents_remember.serving.harness_control_models import (
    AR_EVIDENCE_KEY,
    CONTROL_PROTOCOL_VERSION,
    REQUIRED_ADAPTER_CAPABILITIES,
    AdapterEvent,
    AdapterHandshake,
    AdapterSnapshot,
    ControlIdentity,
    ControlOperationRef,
    EvidenceFrame,
    InteractionResponse,
    LaunchSpec,
    NativeEvidenceFrame,
    NativeEvidencePage,
    PromptRequest,
    ReconciliationResult,
    ShutdownMode,
    SubmissionReceipt,
    TranscriptEntry,
    clip_evidence_payload,
)
from agents_remember.serving.harness_control_runner import (
    RunnerConfig,
    control_runner_command,
    parse_runner_config,
)
from agents_remember.serving.hosted_control_projection import control_snapshot_entry
from agents_remember.serving.pi_rpc_adapter import PiRpcAdapter
from agents_remember.serving.terminal import TerminalSessionBinding
from agents_remember.serving.terminal_catalog import TerminalCatalog, TerminalCatalogEntry
from agents_remember.serving.terminal_opener import open_terminal_session

NOW = "2026-07-19T08:00:00+00:00"
CODEX_FIXTURE = Path(__file__).parent / "fixtures" / "codex_app_server_0_144_3.json"


def _identity(session: str = "ar-evidence-1") -> ControlIdentity:
    return ControlIdentity(
        ar_session_id=session,
        tmux_name=f"ar-{session}",
        created_at="2026-07-19T07:00:00+00:00",
    )


def _launch(identity: ControlIdentity, *, harness_id: str = "fake") -> LaunchSpec:
    return LaunchSpec(
        identity=identity,
        harness_id=harness_id,
        cwd=Path("/workspace"),
        argv=("fake-harness", "protocol-mode"),
        env={"PRESERVE_INSTALLED_AUTH": "1"},
    )


async def _settle() -> None:
    await asyncio.sleep(0)
    await asyncio.sleep(0)


async def _wait_for_evidence(bridge: HarnessControlBridge, sequence: int) -> None:
    for _ in range(200):
        if bridge.evidence().latest_sequence >= sequence:
            return
        await asyncio.sleep(0)
    raise AssertionError(f"evidence sequence {sequence} never reached the bridge buffer")


def _obj(value: object) -> Mapping[str, object]:
    assert isinstance(value, Mapping)
    return cast(Mapping[str, object], value)


async def _wait_for_failure(bridge: HarnessControlBridge) -> None:
    for _ in range(200):
        if bridge.snapshot().control == "failed":
            return
        await asyncio.sleep(0)
    raise AssertionError("bridge never surfaced the expected failure")


class _EvidenceAdapter:
    """Minimal protocol adapter with an event queue the test drives directly."""

    def __init__(self) -> None:
        self.current: AdapterSnapshot | None = None
        self.events: asyncio.Queue[AdapterEvent | None] = asyncio.Queue()
        self.event_sequence = 0
        self.submissions: list[PromptRequest] = []
        self.stop_modes: list[ShutdownMode] = []

    async def start(self, launch: LaunchSpec) -> AdapterHandshake:
        self.current = AdapterSnapshot(
            identity=launch.identity,
            control="ready",
            activity="idle",
            acceptance="immediate",
            vendor_session_id="vendor-1",
            raw={"fake": True},
        )
        return AdapterHandshake(
            protocol_version=CONTROL_PROTOCOL_VERSION,
            adapter_id="fake",
            identity=launch.identity,
            capabilities=REQUIRED_ADAPTER_CAPABILITIES,
            snapshot=self.current,
        )

    async def snapshot(self) -> AdapterSnapshot:
        assert self.current is not None
        return self.current

    def advertise(self) -> CapabilitySnapshot:
        return CapabilitySnapshot(models=(), selected_model_key=None, selected_effort=None)

    async def set_model(
        self, model_key: str, *, operation: ControlOperationRef | None = None
    ) -> SetResult:
        del model_key, operation
        raise HarnessControlError("unused in evidence tests")

    async def set_effort(
        self, effort: str, *, operation: ControlOperationRef | None = None
    ) -> SetResult:
        del effort, operation
        raise HarnessControlError("unused in evidence tests")

    async def preflight_operation(self, operation: ControlOperationRef) -> None:
        del operation

    def subscribe(self) -> AsyncIterator[AdapterEvent]:
        return self._stream()

    async def _stream(self) -> AsyncIterator[AdapterEvent]:
        while True:
            event = await self.events.get()
            if event is None:
                return
            yield event

    async def submit(self, request: PromptRequest) -> SubmissionReceipt:
        self.submissions.append(request)
        return SubmissionReceipt(
            request_id=request.request_id,
            acceptance="immediate",
            submitted_at=request.submitted_at,
            vendor_correlation_id=f"vendor-{request.request_id}",
            accepted_at=request.submitted_at,
        )

    async def respond(self, response: InteractionResponse) -> None:
        del response

    async def reconcile(self, request_id: str) -> ReconciliationResult:
        return ReconciliationResult(
            request_id=request_id,
            state="unresolved",
            reconciled_at=NOW,
        )

    async def stop(self, mode: ShutdownMode) -> None:
        self.stop_modes.append(mode)

    def complete(self, request_id: str) -> None:
        """Emit the terminal completion the ordinary lane needs before its next dispatch."""

        request = next(item for item in self.submissions if item.request_id == request_id)
        assert request.operation is not None
        assert self.current is not None
        self.event_sequence += 1
        completed = replace(self.current, activity="idle", acceptance="immediate")
        self.events.put_nowait(
            AdapterEvent(
                sequence=self.event_sequence,
                kind="completed",
                identity=completed.identity,
                created_at=NOW,
                snapshot=completed,
                operation=request.operation,
            )
        )

    def complete_with_codex_turn(self, request_id: str, params: Mapping[str, object]) -> None:
        """Complete the ordinary lane as the codex adapter's ``turn/completed`` does.

        Mirrors ``codex_app_server_adapter._handle_turn_completed``: a ``completed`` event bound
        to the exact operation ref, carrying the native turn params (``turn.id``/``turn.status`` +
        the large items body) under the reserved evidence key so the buffer clip is exercised.
        """

        request = next(item for item in self.submissions if item.request_id == request_id)
        assert request.operation is not None
        assert self.current is not None
        turn = params.get("turn")
        assert isinstance(turn, Mapping)
        turn_id = turn.get("id")
        assert isinstance(turn_id, str)
        self.event_sequence += 1
        completed = replace(self.current, activity="idle", acceptance="immediate")
        self.events.put_nowait(
            AdapterEvent(
                sequence=self.event_sequence,
                kind="completed",
                identity=completed.identity,
                created_at=NOW,
                snapshot=completed,
                operation=request.operation,
                raw={
                    "codexMethod": "turn/completed",
                    "turnId": turn_id,
                    AR_EVIDENCE_KEY: dict(params),
                },
            )
        )

    def emit(self, kind: str, raw: Mapping[str, object], *, sequence: int | None = None) -> None:
        assert self.current is not None
        self.event_sequence += 1
        # Like the real mappers, the reserved key never enters the adapter's own snapshot raw.
        snapshot_raw = {key: value for key, value in raw.items() if key != AR_EVIDENCE_KEY}
        self.events.put_nowait(
            AdapterEvent(
                sequence=sequence if sequence is not None else self.event_sequence,
                kind=kind,
                identity=self.current.identity,
                created_at=NOW,
                snapshot=(
                    replace(self.current, raw={**self.current.raw, **snapshot_raw})
                    if kind in {"state", "completed", "disconnected", "failed"}
                    else None
                ),
                raw=dict(raw),
            )
        )

    def emit_pi_content_ful_message_end(self, stop_reason: str, *, filler_chars: int) -> None:
        """Emit a content-ful pi ``message_end`` exactly as ``pi_rpc_events._message_event`` does.

        A content-ful message_end crosses as event kind ``transcript`` with a minted transcript
        entry; the full native frame (``type``/``message``/``stopReason`` + content) rides the
        reserved evidence key. ``filler_chars`` inflates the message content so the serialized
        frame crosses the bridge's per-frame evidence clip budget when large.
        """

        assert self.current is not None
        self.event_sequence += 1
        frame: dict[str, object] = {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "z" * filler_chars}],
                "stopReason": stop_reason,
            },
        }
        entry = TranscriptEntry(
            sequence=self.event_sequence,
            role="assistant",
            text="final answer",
            created_at=NOW,
            raw={"piMessageEnd": stop_reason},
        )
        self.events.put_nowait(
            AdapterEvent(
                sequence=self.event_sequence,
                kind="transcript",
                identity=self.current.identity,
                created_at=NOW,
                transcript=(entry,),
                raw={"piEvent": dict(frame), AR_EVIDENCE_KEY: dict(frame)},
            )
        )


class _NativePageAdapter(_EvidenceAdapter):
    """Adds a structural native page read returning a preset page."""

    def __init__(self, page: NativeEvidencePage) -> None:
        super().__init__()
        self.page = page
        self.calls: list[tuple[str | None, int, int]] = []

    async def read_native_page(
        self,
        *,
        cursor: str | None,
        limit: int,
        byte_budget: int,
    ) -> NativeEvidencePage:
        self.calls.append((cursor, limit, byte_budget))
        return self.page


def _codex_item_event(sequence: int, item: Mapping[str, object]) -> tuple[str, dict[str, object]]:
    params = {"threadId": "thread-1", "turnId": "turn-1", "item": dict(item)}
    return (
        "codex-notification",
        {"codexMethod": "item/completed", AR_EVIDENCE_KEY: params},
    )


class EvidenceBufferTests(unittest.IsolatedAsyncioTestCase):
    async def test_reserved_key_round_trip_and_no_leak(self) -> None:
        identity = _identity()
        adapter = _EvidenceAdapter()
        bridge = HarnessControlBridge(identity, adapter, clock=lambda: NOW)
        await bridge.start(_launch(identity))
        try:
            item = {"id": "item-1", "type": "commandExecution", "command": "ls -la", "exit": 0}
            kind, raw = _codex_item_event(1, item)
            adapter.emit(kind, raw)
            adapter.emit(
                "pi:message_end",
                {
                    "piEvent": {"type": "message_end", "message": {"role": "assistant"}},
                    AR_EVIDENCE_KEY: {"type": "message_end", "message": {"role": "assistant"}},
                },
            )
            await _settle()
            page = bridge.evidence()
            self.assertEqual(len(page.frames), 2)
            self.assertEqual(_obj(_obj(page.frames[0].raw)["item"])["command"], "ls -la")
            self.assertEqual(page.frames[0].kind, "codex-notification")
            self.assertEqual(page.frames[1].kind, "pi:message_end")
            self.assertEqual(page.latest_sequence, 2)
            self.assertEqual(page.evicted_before_sequence, 0)
            # No leak: the bridge snapshot carries only the status-quo keys, byte-identical.
            snapshot_raw = bridge.snapshot().raw
            self.assertNotIn(AR_EVIDENCE_KEY, snapshot_raw)
            self.assertEqual(snapshot_raw["codexMethod"], "item/completed")
            self.assertEqual(
                snapshot_raw["piEvent"], {"type": "message_end", "message": {"role": "assistant"}}
            )
            # The catalog projection of that snapshot is equally free of evidence payloads.
            projected = control_snapshot_entry(
                _catalog_entry(identity),
                bridge.snapshot(),
            )
            serialized = json.dumps(projected.control_raw)
            self.assertNotIn(AR_EVIDENCE_KEY, serialized)
            self.assertNotIn("commandExecution", serialized)
            # Subscribers see the same redacted shape.
            seen: list[AdapterSnapshot] = []
            subscription = bridge.subscribe()
            seen.append(await anext(subscription))
            self.assertNotIn(AR_EVIDENCE_KEY, seen[0].raw)
            await subscription.aclose()
            # The transcript path is untouched by evidence forwarding.
            self.assertEqual(bridge.transcript(), ())
        finally:
            await bridge.stop("forced")

    async def test_unknown_vendor_pass_through_preserves_raw_without_guessing(self) -> None:
        identity = _identity()
        adapter = _EvidenceAdapter()
        bridge = HarnessControlBridge(identity, adapter, clock=lambda: NOW)
        await bridge.start(_launch(identity))
        try:
            payload = {"unrecognizedShape": {"nested": [1, 2, 3]}, "vendor": "x"}
            adapter.emit("vendor-x/y", {"xMethod": "x/y", AR_EVIDENCE_KEY: payload})
            await _settle()
            page = bridge.evidence()
            self.assertEqual(page.frames[0].kind, "vendor-x/y")
            self.assertEqual(page.frames[0].raw, payload)
            snapshot_raw = bridge.snapshot().raw
            self.assertNotIn(AR_EVIDENCE_KEY, snapshot_raw)
            self.assertEqual(snapshot_raw["xMethod"], "x/y")
        finally:
            await bridge.stop("forced")

    async def test_native_method_is_carried_onto_the_frame_and_stripped_from_snapshot(self) -> None:
        # The Codex adapter carries the notification method out of band under
        # AR_EVIDENCE_METHOD_KEY so the projector switches on the real method instead of re-guessing
        # from the params shape. The bridge must preserve it as typed EvidenceFrame.native_method and
        # strip it from the republished snapshot exactly like AR_EVIDENCE_KEY (byte-identical).
        from agents_remember.serving.harness_control_client import _evidence_page  # noqa: PLC0415
        from agents_remember.serving.harness_control_models import (  # noqa: PLC0415
            AR_EVIDENCE_METHOD_KEY,
            evidence_page_json,
        )

        identity = _identity()
        adapter = _EvidenceAdapter()
        bridge = HarnessControlBridge(identity, adapter, clock=lambda: NOW)
        await bridge.start(_launch(identity))
        try:
            params = {"threadId": "thread-1", "name": "atlassian", "status": "running"}
            adapter.emit(
                "codex-notification",
                {
                    "codexMethod": "mcpServer/startupStatus/updated",
                    AR_EVIDENCE_METHOD_KEY: "mcpServer/startupStatus/updated",
                    AR_EVIDENCE_KEY: dict(params),
                },
            )
            await _settle()
            page = bridge.evidence()
            self.assertEqual(len(page.frames), 1)
            frame = page.frames[0]
            self.assertEqual(frame.native_method, "mcpServer/startupStatus/updated")
            self.assertEqual(frame.raw, params)
            # The reserved method key never leaks into the redacted snapshot.
            snapshot_raw = bridge.snapshot().raw
            self.assertNotIn(AR_EVIDENCE_KEY, snapshot_raw)
            self.assertNotIn(AR_EVIDENCE_METHOD_KEY, snapshot_raw)
            self.assertEqual(snapshot_raw["codexMethod"], "mcpServer/startupStatus/updated")
            # The method survives the IPC serialize -> deserialize round trip.
            restored = _evidence_page(
                evidence_page_json(page), expected_bridge_epoch=page.bridge_epoch
            )
            self.assertEqual(
                restored.frames[0].native_method, "mcpServer/startupStatus/updated"
            )
        finally:
            await bridge.stop("forced")

    async def test_buffer_count_eviction_reports_honest_gap_floor_at_two_sizes(self) -> None:
        for limit, emitted, expected_floor, expected_kept in ((4, 6, 2, 4), (2, 4, 2, 2)):
            with self.subTest(limit=limit):
                identity = _identity(f"evict-{limit}")
                adapter = _EvidenceAdapter()
                bridge = HarnessControlBridge(
                    identity, adapter, evidence_limit=limit, clock=lambda: NOW
                )
                await bridge.start(_launch(identity))
                try:
                    for index in range(1, emitted + 1):
                        adapter.emit("state", {"n": index, AR_EVIDENCE_KEY: {"n": index}})
                    await _wait_for_evidence(bridge, emitted)
                    page = bridge.evidence()
                    self.assertEqual(len(page.frames), expected_kept)
                    self.assertEqual(page.evicted_before_sequence, expected_floor)
                    self.assertEqual(
                        [frame.sequence for frame in page.frames],
                        list(range(expected_floor + 1, emitted + 1)),
                    )
                    self.assertEqual(page.latest_sequence, emitted)
                finally:
                    await bridge.stop("forced")

    async def test_frame_byte_clip_is_visible_and_bounded(self) -> None:
        identity = _identity()
        adapter = _EvidenceAdapter()
        bridge = HarnessControlBridge(
            identity, adapter, evidence_frame_bytes=128, clock=lambda: NOW
        )
        await bridge.start(_launch(identity))
        try:
            adapter.emit("state", {"big": "x" * 4096, AR_EVIDENCE_KEY: {"big": "x" * 4096}})
            await _settle()
            frame = bridge.evidence().frames[0]
            self.assertEqual(frame.raw["arEvidenceTruncated"], True)
            original_bytes = frame.raw["originalBytes"]
            assert isinstance(original_bytes, int)
            self.assertGreater(original_bytes, 4096)
            preview = frame.raw["preview"]
            assert isinstance(preview, str)
            self.assertTrue(preview.endswith("…[truncated]"))
            self.assertLessEqual(
                len(json.dumps(frame.raw, separators=(",", ":")).encode("utf-8")), 128
            )
        finally:
            await bridge.stop("forced")

    async def test_evidence_page_byte_budget_truncates_without_overlap_or_gap(self) -> None:
        identity = _identity()
        adapter = _EvidenceAdapter()
        bridge = HarnessControlBridge(identity, adapter, clock=lambda: NOW)
        await bridge.start(_launch(identity))
        try:
            for index in range(1, 5):
                adapter.emit("state", {"n": index, AR_EVIDENCE_KEY: {"n": "y" * 200 + str(index)}})
            await _wait_for_evidence(bridge, 4)
            chained: list[int] = []
            after = 0
            truncated_pages = 0
            for _ in range(10):
                page = bridge.evidence(after_sequence=after, byte_budget=300)
                chained.extend(frame.sequence for frame in page.frames)
                if not page.truncated:
                    break
                truncated_pages += 1
                after = page.frames[-1].sequence
            else:
                self.fail("byte-budgeted paging never terminated")
            self.assertGreaterEqual(truncated_pages, 1)
            self.assertEqual(chained, [1, 2, 3, 4])
        finally:
            await bridge.stop("forced")

    async def test_non_monotonic_evidence_sequence_fails_the_bridge_visibly(self) -> None:
        identity = _identity()
        adapter = _EvidenceAdapter()
        bridge = HarnessControlBridge(identity, adapter, clock=lambda: NOW)
        await bridge.start(_launch(identity))
        try:
            adapter.emit("state", {"a": 1, AR_EVIDENCE_KEY: {"a": 1}}, sequence=5)
            adapter.emit("state", {"a": 2, AR_EVIDENCE_KEY: {"a": 2}}, sequence=5)
            await _wait_for_failure(bridge)
            self.assertEqual(bridge.snapshot().control, "failed")
        finally:
            await bridge.stop("forced")

    async def test_non_object_evidence_payload_fails_the_bridge_visibly(self) -> None:
        identity = _identity()
        adapter = _EvidenceAdapter()
        bridge = HarnessControlBridge(identity, adapter, clock=lambda: NOW)
        await bridge.start(_launch(identity))
        try:
            adapter.emit("state", {AR_EVIDENCE_KEY: "not-an-object"})
            await _wait_for_failure(bridge)
            self.assertEqual(bridge.snapshot().control, "failed")
            self.assertIn("must be an object", str(bridge.snapshot().raw["bridgeError"]))
        finally:
            await bridge.stop("forced")


def _catalog_entry(identity: ControlIdentity) -> TerminalCatalogEntry:
    return TerminalCatalogEntry(
        id=identity.ar_session_id,
        label="evidence-test",
        kind="harness",
        harness="fake",
        lifecycle_id=None,
        cwd=Path("/workspace"),
        tmux_name=identity.tmux_name,
        command=("fake",),
        created_at=identity.created_at,
        last_attached_at=identity.created_at,
        status="running",
        leaf_key=None,
    )


class _ControlledEntry:
    def __init__(self, identity: ControlIdentity, endpoint: Path) -> None:
        self.id = identity.ar_session_id
        self.tmux_name = identity.tmux_name
        self.created_at = identity.created_at
        self.control_endpoint = endpoint


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

    async def test_submission_provenance_all_sources_epoch_and_bounds(self) -> None:
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
                    source="cockpit",
                    request_id="prov-cockpit",
                    expected_bridge_epoch=epoch,
                )
                self.assertEqual(cockpit.acceptance, "immediate")
                await asyncio.to_thread(
                    submit_control_prompt,
                    entry,
                    "terminal prompt",
                    source="terminal",
                    request_id="prov-terminal",
                )
                await asyncio.to_thread(
                    submit_control_prompt,
                    entry,
                    "durable prompt",
                    source="durable",
                    request_id="prov-durable",
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


# ---------------------------------------------------------------------------
# Per-harness mapper round-trips through the production seam
# ---------------------------------------------------------------------------


def _codex_fixture() -> dict[str, object]:
    return json.loads(CODEX_FIXTURE.read_text(encoding="utf-8"))


def _fixture_object(data: Mapping[str, object], *path: str) -> dict[str, object]:
    value: object = data
    for key in path:
        assert isinstance(value, dict)
        value = value[key]
    assert isinstance(value, dict)
    return value


class _FakeCodexTransport:
    def __init__(self) -> None:
        self.responses: dict[str, list[dict[str, object]]] = {}
        self.requests: list[tuple[str, dict[str, object]]] = []
        self.incoming: asyncio.Queue[dict[str, object] | None] = asyncio.Queue()
        self.stop_modes: list[ShutdownMode] = []

    def queue(self, method: str, response: Mapping[str, object]) -> None:
        self.responses.setdefault(method, []).append(deepcopy(dict(response)))

    async def start(self, launch: LaunchSpec) -> None:
        del launch

    async def request(self, method, params, *, before_write=None):
        if before_write is not None:
            before_write()
        self.requests.append((method, dict(params)))
        return deepcopy(self.responses[method].pop(0))

    async def notify(self, method, params) -> None:
        del method, params

    def messages(self) -> AsyncIterator[dict[str, object]]:
        return self._stream()

    async def _stream(self) -> AsyncIterator[dict[str, object]]:
        while True:
            message = await self.incoming.get()
            if message is None:
                return
            yield message

    async def respond(self, request_id, result) -> None:
        del request_id, result

    async def respond_error(self, request_id, *, code, message) -> None:
        del request_id, code, message

    async def stop(self, mode: ShutdownMode) -> None:
        self.stop_modes.append(mode)
        self.incoming.put_nowait(None)

    def emit(self, message: Mapping[str, object]) -> None:
        self.incoming.put_nowait(deepcopy(dict(message)))


def _codex_adapter(transport: _FakeCodexTransport):
    settings = CodexAppServerSettings(
        reasoning_effort="xhigh",
        model="gpt-5.6-sol",
        ephemeral=True,
    )
    return CodexAppServerAdapter(
        settings,
        transport_factory=lambda: transport,
        clock=lambda: NOW,
    )


def _prime_codex_start(transport: _FakeCodexTransport) -> None:
    data = _codex_fixture()
    transport.queue("initialize", _fixture_object(data, "initializeResult"))
    transport.queue("model/list", _fixture_object(data, "modelListResult"))
    transport.queue("thread/start", _fixture_object(data, "threadStartResult"))


def _thread_read_result(items: Sequence[Mapping[str, object]], *, turn_id: str = "turn-1"):
    return {
        "thread": {
            "id": "thread-1",
            "turns": [{"id": turn_id, "items": [dict(item) for item in items]}],
        }
    }


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
        for _ in range(4):
            transport.queue("thread/read", _thread_read_result(items))
        adapter = _codex_adapter(transport)
        await adapter.start(_launch(_identity("codex-page"), harness_id="codex"))
        try:
            first = await adapter.read_native_page(cursor=None, limit=2, byte_budget=48 * 1024)
            self.assertEqual([f.native_id for f in first.frames], ["item-1", "item-2"])
            self.assertEqual(first.frames[1].native_type, "reasoning")
            self.assertEqual(first.frames[1].native_parent_id, "turn-1")
            self.assertEqual(first.next_cursor, "item-2")
            self.assertTrue(first.truncated)
            self.assertEqual(first.bridge_epoch, "")
            second = await adapter.read_native_page(
                cursor=first.next_cursor, limit=2, byte_budget=48 * 1024
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
        transport.queue("thread/read", _thread_read_result([duplicate, duplicate]))
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
            {"id": "item-next", "type": "userMessage", "text": "next"},
        ]
        transport.queue("thread/read", _thread_read_result(items))
        transport.queue("thread/read", _thread_read_result(items))
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
            cursor = page.next_cursor or chained[-1]
            rest = await adapter.read_native_page(cursor=cursor, limit=10, byte_budget=1024)
            self.assertFalse({f.native_id for f in rest.frames} & set(chained))
            self.assertEqual(
                chained + [f.native_id for f in rest.frames], ["item-big", "item-next"]
            )
        finally:
            await adapter.stop("forced")


class _FakePiTransport:
    def __init__(self, entries: list[dict[str, object]] | None = None) -> None:
        self.entries = entries or []
        self.commands: list[dict[str, object]] = []
        self.event_queue: asyncio.Queue[Mapping[str, object] | None] = asyncio.Queue()
        self.stop_modes: list[ShutdownMode] = []

    async def start(self, launch: LaunchSpec) -> None:
        del launch

    @property
    def event_token(self) -> int:
        return 0

    async def request(self, command, *, before_write=None):
        if before_write is not None:
            before_write()
        copied = dict(command)
        self.commands.append(copied)
        request_id = copied["id"]
        command_type = copied["type"]
        if command_type == "get_state":
            return _pi_success(
                request_id,
                "get_state",
                {
                    "sessionId": "pi-session-1",
                    "sessionFile": "/sessions/pi-session-1.jsonl",
                    "isStreaming": False,
                    "isCompacting": False,
                    "pendingMessageCount": 0,
                    "thinkingLevel": "high",
                    "model": {"provider": "anthropic", "id": "claude-test"},
                },
            )
        if command_type == "get_available_models":
            return _pi_success(
                request_id,
                "get_available_models",
                {
                    "models": [
                        {
                            "id": "claude-test",
                            "name": "Claude Test",
                            "provider": "anthropic",
                            "reasoning": True,
                            "thinkingLevelMap": {"high": "high"},
                        }
                    ]
                },
            )
        if command_type == "get_entries":
            entries = self.entries
            since = copied.get("since")
            if since is not None:
                index = next(
                    (
                        position
                        for position, entry in enumerate(entries)
                        if entry.get("id") == since
                    ),
                    None,
                )
                if index is None:
                    return {
                        "id": request_id,
                        "type": "response",
                        "command": "get_entries",
                        "success": False,
                        "error": f"Entry not found: {since}",
                    }
                entries = entries[index + 1 :]
            return _pi_success(
                request_id,
                "get_entries",
                {"entries": entries, "leafId": entries[-1]["id"] if entries else None},
            )
        raise AssertionError(f"unexpected fake pi command: {command_type}")

    async def send(self, command, *, before_write=None) -> None:
        del command, before_write

    def events(self) -> AsyncIterator[Mapping[str, object]]:
        return self._stream()

    async def _stream(self) -> AsyncIterator[Mapping[str, object]]:
        while True:
            frame = await self.event_queue.get()
            if frame is None:
                raise HarnessAdapterDisconnectedError("fake pi stopped", may_have_sent=False)
            yield frame

    async def stop(self, mode: ShutdownMode) -> None:
        self.stop_modes.append(mode)
        self.event_queue.put_nowait(None)

    def emit(self, frame: Mapping[str, object]) -> None:
        self.event_queue.put_nowait(frame)


def _pi_success(request_id: str, command: str, data: object) -> dict[str, object]:
    return {"id": request_id, "type": "response", "command": command, "success": True, "data": data}


class PiEvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_message_end_and_unknown_frames_reach_evidence_with_full_payload(self) -> None:
        transport = _FakePiTransport()
        adapter = PiRpcAdapter(transport_factory=lambda: transport, clock=lambda: NOW)
        await adapter.start(_launch(_identity("pi-live"), harness_id="pi"))
        events = adapter.subscribe()
        try:
            transport.emit(
                {
                    "type": "message_update",
                    "message": {"role": "assistant", "content": [{"type": "text", "text": "hel"}]},
                    "delta": {"type": "text_delta", "text": "hel"},
                }
            )
            transport.emit(
                {
                    "type": "message_end",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "thinking", "text": "plan"},
                            {"type": "text", "text": "hello"},
                        ],
                    },
                }
            )
            delta = await asyncio.wait_for(anext(events), timeout=1.0)
            self.assertEqual(delta.kind, "pi:message_update")
            self.assertEqual(_obj(delta.raw["piEvent"])["type"], "message_update")
            self.assertEqual(_obj(_obj(delta.raw[AR_EVIDENCE_KEY])["delta"])["text"], "hel")
            end = await asyncio.wait_for(anext(events), timeout=1.0)
            self.assertEqual(end.kind, "transcript")
            self.assertEqual(_obj(end.raw[AR_EVIDENCE_KEY])["type"], "message_end")
            # Status-quo piEvent and flattened transcript entry stay byte-identical.
            self.assertEqual(_obj(end.raw["piEvent"])["type"], "message_end")
            self.assertEqual(end.transcript[0].text, "planhello")
            snapshot = await adapter.snapshot()
            self.assertNotIn(AR_EVIDENCE_KEY, snapshot.raw)
        finally:
            await adapter.stop("forced")

    async def test_native_page_typed_identity_and_durable_since_continuation(self) -> None:
        entries = [
            {
                "id": f"entry-{index}",
                "parentId": f"entry-{index - 1}" if index > 1 else None,
                "type": "message",
                "timestamp": f"2026-07-19T07:0{index}:00+00:00",
                "message": {
                    "role": "user" if index % 2 else "assistant",
                    "content": [{"type": "text", "text": f"text {index}"}],
                },
            }
            for index in (1, 2, 3)
        ]
        transport = _FakePiTransport(entries=entries)
        adapter = PiRpcAdapter(transport_factory=lambda: transport, clock=lambda: NOW)
        await adapter.start(_launch(_identity("pi-page"), harness_id="pi"))
        try:
            first = await adapter.read_native_page(cursor=None, limit=2, byte_budget=48 * 1024)
            self.assertEqual(
                [(f.native_id, f.native_parent_id, f.native_type) for f in first.frames],
                [("entry-1", None, "message"), ("entry-2", "entry-1", "message")],
            )
            self.assertEqual(first.frames[0].created_at, "2026-07-19T07:01:00+00:00")
            self.assertEqual(first.next_cursor, "entry-2")
            self.assertTrue(first.truncated)
            second = await adapter.read_native_page(
                cursor=first.next_cursor, limit=2, byte_budget=48 * 1024
            )
            self.assertEqual([f.native_id for f in second.frames], ["entry-3"])
            self.assertIsNone(second.next_cursor)
            self.assertFalse(second.truncated)
            since_commands = [
                command for command in transport.commands if command.get("since") is not None
            ]
            self.assertEqual(since_commands[-1]["since"], "entry-2")
            with self.assertRaises(HarnessControlError):
                await adapter.read_native_page(
                    cursor="entry-missing", limit=2, byte_budget=48 * 1024
                )
        finally:
            await adapter.stop("forced")

    async def test_native_page_duplicate_entry_identity_fails_closed(self) -> None:
        duplicate = {"id": "entry-1", "parentId": None, "type": "message"}
        transport = _FakePiTransport(entries=[dict(duplicate), dict(duplicate)])
        adapter = PiRpcAdapter(transport_factory=lambda: transport, clock=lambda: NOW)
        await adapter.start(_launch(_identity("pi-dup"), harness_id="pi"))
        try:
            with self.assertRaises(HarnessControlError):
                await adapter.read_native_page(cursor=None, limit=10, byte_budget=48 * 1024)
        finally:
            await adapter.stop("forced")


CLAUDE_SESSION = "11111111-1111-4111-8111-111111111111"


class _FakeClaudeTransport:
    def __init__(self) -> None:
        self.frames: asyncio.Queue[dict[str, object] | None] = asyncio.Queue()
        self.writes: list[dict[str, object]] = []
        self.stop_modes: list[ShutdownMode] = []
        self.returncode: int | None = None

    async def start(self, argv, *, cwd, env) -> None:
        del argv, cwd, env

    async def read_frame(self):
        return await self.frames.get()

    async def write_frame(self, frame, *, before_write=None) -> None:
        if before_write is not None:
            before_write()
        self.writes.append(dict(frame))

    async def stop(self, mode: ShutdownMode) -> None:
        self.stop_modes.append(mode)
        self.frames.put_nowait(None)

    def feed(self, frame: Mapping[str, object]) -> None:
        self.frames.put_nowait(deepcopy(dict(frame)))


def _claude_adapter(transport: _FakeClaudeTransport, correlations: list[str]):
    values = iter(correlations)
    return ClaudeStreamJsonAdapter(
        transport_factory=lambda: transport,
        clock=lambda: NOW,
        correlation_factory=lambda: next(values),
        limits=ClaudeAdapterLimits(),
    )


def _claude_init_frames() -> list[dict[str, object]]:
    fixture = (
        Path(__file__).parent
        / "fixtures"
        / "claude_stream_json"
        / "2.1.210"
        / "initialization.jsonl"
    )
    return [json.loads(line) for line in fixture.read_text().splitlines()]


class ClaudeEvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_assistant_blocks_and_unknown_frames_forward_full_payload_without_leak(
        self,
    ) -> None:
        transport = _FakeClaudeTransport()
        adapter = _claude_adapter(transport, ["corr-1"])
        for frame in _claude_init_frames():
            transport.feed(frame)
        await adapter.start(_launch(_identity("claude-live"), harness_id="claude"))
        events = adapter.subscribe()
        try:
            assistant_frame = {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "reasoning"},
                        {
                            "type": "tool_use",
                            "id": "toolu-1",
                            "name": "Bash",
                            "input": {"cmd": "ls"},
                        },
                        {"type": "text", "text": "visible text"},
                    ],
                },
                "session_id": CLAUDE_SESSION,
                "uuid": "uuid-assistant-1",
                "timestamp": NOW,
            }
            transport.feed(assistant_frame)
            transport.feed({"type": "vendor_future", "subtype": "x1", "payload": {"k": 1}})
            assistant = await asyncio.wait_for(anext(events), timeout=1.0)
            self.assertEqual(assistant.raw["claudeEventType"], "assistant")
            blocks = _obj(_obj(assistant.raw[AR_EVIDENCE_KEY])["message"])["content"]
            assert isinstance(blocks, list)
            self.assertEqual(
                [_obj(block)["type"] for block in blocks], ["thinking", "tool_use", "text"]
            )
            # Text-only transcript extraction is byte-preserved.
            self.assertEqual(assistant.transcript[0].text, "visible text")
            unknown = await asyncio.wait_for(anext(events), timeout=1.0)
            self.assertEqual(unknown.raw["claudeEventType"], "vendor_future")
            self.assertEqual(_obj(unknown.raw[AR_EVIDENCE_KEY])["payload"], {"k": 1})
            # The adapter's own snapshot merge excludes the reserved key (no leak to projections).
            snapshot_raw = (await adapter.snapshot()).raw
            self.assertNotIn(AR_EVIDENCE_KEY, snapshot_raw)
            self.assertEqual(snapshot_raw["claudeEventType"], "vendor_future")
        finally:
            await adapter.stop("forced")

    async def test_result_usage_and_cost_forward_as_evidence(self) -> None:
        transport = _FakeClaudeTransport()
        adapter = _claude_adapter(transport, ["corr-result"])
        for frame in _claude_init_frames():
            transport.feed(frame)
        await adapter.start(_launch(_identity("claude-result"), harness_id="claude"))
        events = adapter.subscribe()
        try:
            operation = ControlOperationRef(
                bridge_epoch="claude-test-epoch",
                sequence=1,
                operation_id="req-result",
                kind="prompt",
            )
            await adapter.preflight_operation(operation)
            task = asyncio.create_task(
                adapter.submit(
                    PromptRequest(
                        request_id="req-result",
                        source="durable",
                        text="do work",
                        submitted_at=NOW,
                        operation=operation,
                    )
                )
            )
            while len(transport.writes) < 4:
                await asyncio.sleep(0)
            written = transport.writes[3]
            self.assertEqual(written["type"], "user")
            transport.feed(
                {
                    **written,
                    "isReplay": True,
                    "session_id": CLAUDE_SESSION,
                    "timestamp": NOW,
                }
            )
            receipt = await asyncio.wait_for(task, timeout=1.0)
            self.assertEqual(receipt.acceptance, "immediate")
            transport.feed(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "result": "done",
                    "session_id": CLAUDE_SESSION,
                    "uuid": "uuid-result-1",
                    "timestamp": NOW,
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                    "modelUsage": {"sonnet": {"input_tokens": 10}},
                    "total_cost_usd": 0.0042,
                    "duration_ms": 1200,
                }
            )
            # Drain the acceptance state event first, then the completed event.
            completed = None
            for _ in range(4):
                event = await asyncio.wait_for(anext(events), timeout=1.0)
                if event.kind == "completed":
                    completed = event
                    break
            assert completed is not None
            self.assertEqual(completed.raw["terminalOutcome"], "completed")
            usage = _obj(completed.raw[AR_EVIDENCE_KEY])
            self.assertEqual(_obj(usage["usage"])["input_tokens"], 10)
            self.assertEqual(usage["modelUsage"], {"sonnet": {"input_tokens": 10}})
            self.assertEqual(usage["total_cost_usd"], 0.0042)
            self.assertEqual(usage["duration_ms"], 1200)
            self.assertNotIn(AR_EVIDENCE_KEY, (await adapter.snapshot()).raw)
        finally:
            await adapter.stop("forced")


# ---------------------------------------------------------------------------
# Codex resume launch channel
# ---------------------------------------------------------------------------


class ResumeChannelTests(unittest.TestCase):
    def _config(self, **overrides: object) -> RunnerConfig:
        base: dict[str, object] = {
            "identity": _identity("resume-1"),
            "harness_id": "codex",
            "cwd": Path("/workspace"),
            "argv": ("codex", "app-server"),
            "endpoint_root": Path("/tmp/endpoints"),
        }
        base.update(overrides)
        return RunnerConfig(**base)  # type: ignore[arg-type]

    def test_runner_payload_round_trips_resume_thread_id(self) -> None:
        config = self._config(resume_thread_id="thread-9")
        command = control_runner_command(config)
        self.assertEqual(parse_runner_config(command[3]), config)
        self.assertEqual(parse_runner_config(command[3]).resume_thread_id, "thread-9")

    def test_runner_payload_without_the_field_parses_to_none(self) -> None:
        # The older payload shape (no resumeThreadId key) must keep parsing unchanged.
        config = self._config()
        command = control_runner_command(config)
        raw = json.loads(base64.urlsafe_b64decode(command[3].encode("ascii")))
        self.assertIn("resumeThreadId", raw)  # new payloads carry the additive key as null
        del raw["resumeThreadId"]
        legacy = base64.urlsafe_b64encode(
            json.dumps(raw, separators=(",", ":")).encode("utf-8")
        ).decode("ascii")
        parsed = parse_runner_config(legacy)
        self.assertIsNone(parsed.resume_thread_id)

    def test_runner_payload_rejects_malformed_resume_thread_id(self) -> None:
        config = self._config()
        command = control_runner_command(config)
        raw = json.loads(base64.urlsafe_b64decode(command[3].encode("ascii")))
        for bad in ("", "  ", " padded ", 7):
            with self.subTest(bad=bad):
                raw["resumeThreadId"] = bad
                encoded = base64.urlsafe_b64encode(
                    json.dumps(raw, separators=(",", ":")).encode("utf-8")
                ).decode("ascii")
                with self.assertRaises(HarnessControlError):
                    parse_runner_config(encoded)

    def test_factory_sets_codex_resume_and_refuses_non_codex_before_any_spawn(self) -> None:
        captured: dict[str, object] = {}

        class _CaptureAdapter:
            def __init__(self, settings: object) -> None:
                captured["settings"] = settings

        with mock.patch(
            "agents_remember.serving.harness_control_factories.CodexAppServerAdapter",
            _CaptureAdapter,
        ):
            create_harness_protocol_adapter("codex", env={}, resume_thread_id="thread-9")
            settings = captured["settings"]
            self.assertEqual(settings.resume_thread_id, "thread-9")  # type: ignore[attr-defined]
            create_harness_protocol_adapter("codex", env={})
            settings = captured["settings"]
            self.assertIsNone(settings.resume_thread_id)  # type: ignore[attr-defined]
        with self.assertRaises(HarnessControlError):
            create_harness_protocol_adapter("claude", env={}, resume_thread_id="thread-9")
        with self.assertRaises(HarnessControlError):
            create_harness_protocol_adapter("pi", env={}, resume_thread_id="thread-9")
        with self.assertRaises(HarnessControlError):
            create_harness_protocol_adapter("codex", env={}, resume_thread_id="  ")
        with self.assertRaises(HarnessControlError):
            create_harness_protocol_adapter("codex", env={}, resume_thread_id=" padded ")


class _FakeHost:
    def __init__(self) -> None:
        self.ensured: list[dict[str, object]] = []
        self.known: set[str] = set()

    def has_session(self, tmux_name: str) -> bool:
        return tmux_name in self.known

    def ensure(
        self, sid, *, cwd, command, lifecycle_id=None, name=None, suspend_unsafe=False, env=None
    ):
        del env
        tmux_name = name or f"ar-{sid}"
        self.ensured.append({"sid": sid, "command": tuple(command)})
        self.known.add(tmux_name)
        return TerminalSessionBinding(
            sid=sid,
            tmux_name=tmux_name,
            cwd=Path(cwd),
            command=tuple(command),
            lifecycle_id=lifecycle_id,
            suspend_unsafe=suspend_unsafe,
        )


def _detected(_command: str) -> str | None:
    return "/usr/bin/harness"


class ResumeOpenerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.catalog = TerminalCatalog(self.tmp / "terminal-sessions.json")
        self.host = _FakeHost()

    def _open(self, **kwargs: object):
        base: dict[str, object] = {
            "catalog": self.catalog,
            "host": self.host,
            "session_id": "resume-worker-1",
            "kind": "harness",
            "workspace_root": self.tmp,
            "shell": "/bin/bash",
            "harness": "codex",
            "which": _detected,
        }
        base.update(kwargs)
        return open_terminal_session(**base)  # type: ignore[arg-type]

    def test_codex_resume_rides_opener_to_runner_payload(self) -> None:
        result = self._open(resume_thread_id="thread-9")
        self.assertEqual(result.status, "opened")
        command = self.host.ensured[0]["command"]
        assert isinstance(command, tuple)
        config = parse_runner_config(command[3])
        self.assertEqual(config.resume_thread_id, "thread-9")
        self.assertEqual(config.harness_id, "codex")

    def test_absent_resume_preserves_current_payload(self) -> None:
        result = self._open()
        self.assertEqual(result.status, "opened")
        command = self.host.ensured[0]["command"]
        assert isinstance(command, tuple)
        config = parse_runner_config(command[3])
        self.assertIsNone(config.resume_thread_id)

    def test_non_codex_resume_fails_closed_before_any_spawn(self) -> None:
        result = self._open(harness="claude", resume_thread_id="thread-9")
        self.assertEqual(result.status, "bad-kind")
        self.assertIn("only supported for the codex harness", result.detail or "")
        result = self._open(harness="pi", resume_thread_id="thread-9")
        self.assertEqual(result.status, "bad-kind")
        self.assertEqual(self.host.ensured, [])

    def test_malformed_resume_fails_closed_before_any_spawn(self) -> None:
        result = self._open(resume_thread_id=" padded ")
        self.assertEqual(result.status, "bad-kind")
        self.assertEqual(self.host.ensured, [])


class ClipHelperTests(unittest.TestCase):
    def test_clip_preserves_small_payloads_and_marks_large_ones(self) -> None:
        small = {"a": 1}
        self.assertEqual(clip_evidence_payload(small, max_bytes=1024), small)
        clipped = clip_evidence_payload({"a": "x" * 4096}, max_bytes=128)
        self.assertEqual(_obj(clipped)["arEvidenceTruncated"], True)
        self.assertLessEqual(len(json.dumps(clipped, separators=(",", ":")).encode("utf-8")), 128)

    def test_clip_rejects_non_serializable_payloads(self) -> None:
        with self.assertRaises(HarnessControlError):
            clip_evidence_payload({"bad": object()}, max_bytes=128)

    def test_clip_preserves_pi_message_end_stop_reason_without_content(self) -> None:
        # A clipped pi message_end keeps its type and stopReason enum
        # at their original paths, and nothing else (no role, no content blocks, no message text).
        frame = {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "A" * 40000 + "LEAK_TAIL"}],
                "stopReason": "aborted",
            },
        }
        clipped = _obj(clip_evidence_payload(frame, max_bytes=512))
        self.assertEqual(
            set(clipped),
            {"arEvidenceTruncated", "originalBytes", "preview", "type", "message"},
        )
        self.assertEqual(clipped["type"], "message_end")
        self.assertEqual(clipped["message"], {"stopReason": "aborted"})
        self.assertEqual(set(_obj(clipped["message"])), {"stopReason"})
        serialized = json.dumps(clipped, ensure_ascii=False, separators=(",", ":"))
        # The body never crosses: the tail sentinel is gone and only a bounded preview prefix
        # of the content survives (the truncation-notice field), never the whole message text.
        self.assertNotIn("LEAK_TAIL", serialized)
        self.assertLess(serialized.count("A"), 512)
        original_bytes = clipped["originalBytes"]
        assert isinstance(original_bytes, int)
        self.assertGreater(original_bytes, 40000)
        self.assertLessEqual(len(serialized.encode("utf-8")), 512)

    def test_clip_preserves_codex_turn_identity_and_status_without_content(self) -> None:
        # A clipped codex turn/completed keeps turn.id + turn.status
        # (both are read by _codex_terminal_outcome) and drops the large items body.
        params = {
            "turn": {
                "id": "turn-oversized-1",
                "status": "interrupted",
                "items": [
                    {"id": f"item-{index}", "type": "agentMessage", "text": "B" * 400 + "LEAK_TAIL"}
                    for index in range(200)
                ],
            }
        }
        clipped = _obj(clip_evidence_payload(params, max_bytes=512))
        self.assertEqual(
            set(clipped), {"arEvidenceTruncated", "originalBytes", "preview", "turn"}
        )
        self.assertEqual(clipped["turn"], {"id": "turn-oversized-1", "status": "interrupted"})
        self.assertEqual(set(_obj(clipped["turn"])), {"id", "status"})
        serialized = json.dumps(clipped, ensure_ascii=False, separators=(",", ":"))
        self.assertNotIn("LEAK_TAIL", serialized)
        self.assertLess(serialized.count("B"), 512)
        self.assertLessEqual(len(serialized.encode("utf-8")), 512)

    def test_clip_never_invents_absent_terminal_identity(self) -> None:
        # A large frame with no terminal-identity fields keeps only the truncation-notice fields.
        blob = _obj(clip_evidence_payload({"blob": "q" * 40000}, max_bytes=256))
        self.assertEqual(set(blob), {"arEvidenceTruncated", "originalBytes", "preview"})
        # A message_end with content but no stopReason keeps the type only: absent stays absent,
        # the stopReason is never invented.
        no_reason = _obj(
            clip_evidence_payload(
                {
                    "type": "message_end",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "C" * 40000}],
                    },
                },
                max_bytes=256,
            )
        )
        self.assertEqual(set(no_reason), {"arEvidenceTruncated", "originalBytes", "preview", "type"})
        self.assertEqual(no_reason["type"], "message_end")
        self.assertNotIn("message", no_reason)

    def test_clip_bounds_giant_identity_scalar_without_raising_or_leaking(self) -> None:
        # A wire-reachable valid frame
        # with an over-length string in a preserved path must never raise (a raise in the bridge
        # event loop is session-fatal) and its full value must never cross. The content-first
        # clip truncates the scalar WITH the visible marker, so settlement equality can only
        # fail closed — a truncated id can never equal a real retained id because real ids never
        # contain the marker. When the clip degrades to the legacy envelope (structure itself
        # over budget), over-length preserved scalars still drop whole at the 256-char boundary.
        budget = 32 * 1024
        giant = "z" * 40000 + "GIANT_TAIL"
        content_clipped = _obj(
            clip_evidence_payload(  # must not raise
                {
                    "type": "message_end",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "hi"}],
                        "stopReason": giant,
                    },
                },
                max_bytes=budget,
            )
        )
        self.assertEqual(content_clipped["arEvidenceContentTruncated"], True)
        serialized = json.dumps(content_clipped, ensure_ascii=False, separators=(",", ":"))
        self.assertLessEqual(len(serialized.encode("utf-8")), budget)
        # The oversized scalar's full value never crosses (its tail sentinel is dropped) and the
        # kept prefix is visibly marked, so it can never satisfy an exact-equality correlation.
        self.assertNotIn("GIANT_TAIL", serialized)
        stop_reason = _obj(content_clipped["message"])["stopReason"]
        assert isinstance(stop_reason, str)
        self.assertIn("…[truncated]", stop_reason)
        # Envelope regime: many short strings put the STRUCTURE over budget, so the ladder cannot
        # help and the legacy envelope applies — over-length preserved scalars drop whole there.
        structure_pad = {"items": [{"i": "x"} for _ in range(4000)]}
        envelope = _obj(
            clip_evidence_payload(
                {"turn": {"id": giant, "status": "interrupted"}, **structure_pad},
                max_bytes=budget,
            )
        )
        self.assertEqual(envelope["arEvidenceTruncated"], True)
        self.assertNotIn("GIANT_TAIL", json.dumps(envelope, ensure_ascii=False))
        self.assertEqual(envelope.get("turn"), {"status": "interrupted"})
        # Boundary at the envelope: exactly 256 chars is preserved; 257 is dropped whole.
        kept_256 = _obj(
            clip_evidence_payload({"type": "t" * 256, **structure_pad}, max_bytes=budget)
        )
        self.assertEqual(kept_256["arEvidenceTruncated"], True)
        self.assertEqual(kept_256.get("type"), "t" * 256)
        dropped_257 = _obj(
            clip_evidence_payload({"type": "t" * 257, **structure_pad}, max_bytes=budget)
        )
        self.assertNotIn("type", dropped_257)


class EvidenceTruncationSettlementIpcTests(unittest.IsolatedAsyncioTestCase):
    """Oversized (>32 KiB) production terminal frames driven through the
    REAL evidence path (real bridge clip at the production budget + the real ``read_control_evidence``
    IPC surface that interrupt settlement consumes) keep the tiny identity/status enums the
    settlement consumers read. The scan helpers mirror ``control.operations`` verbatim so a green
    run here is the acceptance link for ``_pi_stop_reason`` / ``_codex_terminal_outcome``.
    """

    async def _serve(
        self, adapter: _EvidenceAdapter, identity: ControlIdentity, tmp: str
    ) -> tuple[HarnessControlBridge, HarnessControlServer, _ControlledEntry]:
        bridge = HarnessControlBridge(identity, adapter, clock=lambda: NOW)
        await bridge.start(_launch(identity))
        endpoint = LocalControlEndpoint.for_session(Path(tmp), identity)
        server = HarnessControlServer(endpoint, bridge)
        await server.start()
        return bridge, server, _ControlledEntry(identity, endpoint.path)

    async def _read_all_evidence(self, entry: _ControlledEntry) -> list[EvidenceFrame]:
        descriptor = await asyncio.to_thread(read_submission_authority, entry)
        frames: list[EvidenceFrame] = []
        after = 0
        for _ in range(64):
            page = await asyncio.to_thread(
                read_control_evidence,
                entry,
                after_sequence=after,
                expected_bridge_epoch=descriptor.bridge_epoch,
            )
            frames.extend(page.frames)
            if not page.truncated:
                return frames
            after = page.frames[-1].sequence
        raise AssertionError("evidence paging never terminated")

    async def _dispatch_operation(
        self, entry: _ControlledEntry, adapter: _EvidenceAdapter, epoch: str, request_id: str
    ) -> None:
        """Submit and land one cockpit prompt so a bound operation ref exists for the completion."""

        await asyncio.to_thread(
            submit_control_prompt,
            entry,
            "drive one turn",
            source="cockpit",
            request_id=request_id,
            expected_bridge_epoch=epoch,
        )
        for _ in range(400):
            if any(item.request_id == request_id for item in adapter.submissions):
                return
            await asyncio.sleep(0)
        raise AssertionError("submission never reached the adapter")

    @staticmethod
    def _pi_latest_stop_reason(frames: list[EvidenceFrame]) -> str | None:
        """Mirror ``control.operations._pi_stop_reason``'s latest-wins scan verbatim."""

        stop_reason: str | None = None
        for frame in frames:
            if frame.raw.get("type") != "message_end":
                continue
            message = frame.raw.get("message")
            if not isinstance(message, dict):
                continue
            candidate = message.get("stopReason")
            if isinstance(candidate, str) and candidate:
                stop_reason = candidate
        return stop_reason

    @staticmethod
    def _codex_terminal_status(frames: list[EvidenceFrame], turn_id: str) -> str | None:
        """Mirror ``control.operations._codex_terminal_outcome``'s frame scan verbatim."""

        for frame in frames:
            if frame.kind != "completed":
                continue
            turn = frame.raw.get("turn")
            if not isinstance(turn, dict) or turn.get("id") != turn_id:
                continue
            status = turn.get("status")
            if status in {"interrupted", "completed", "failed"}:
                return status
        return None

    async def test_oversized_pi_message_end_stop_survives_to_settlement_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            identity = _identity("l3e-pi-stop")
            adapter = _EvidenceAdapter()
            bridge, server, entry = await self._serve(adapter, identity, tmp)
            try:
                adapter.emit_pi_content_ful_message_end("stop", filler_chars=40000)
                await _wait_for_evidence(bridge, 1)
                frames = await self._read_all_evidence(entry)
                self.assertEqual(len(frames), 1)
                # The frame really did exceed the 32 KiB budget and was clipped content-first:
                # the native shape survives whole with its giant text visibly truncated ...
                self.assertEqual(frames[0].raw.get("arEvidenceContentTruncated"), True)
                # ... so its stopReason survives to the exact read settlement settles "already-settled" on.
                self.assertEqual(self._pi_latest_stop_reason(frames), "stop")
            finally:
                await server.close()
                await bridge.stop("forced")

    async def test_oversized_pi_message_end_aborted_survives_to_settlement_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            identity = _identity("l3e-pi-aborted")
            adapter = _EvidenceAdapter()
            bridge, server, entry = await self._serve(adapter, identity, tmp)
            try:
                adapter.emit_pi_content_ful_message_end("aborted", filler_chars=40000)
                await _wait_for_evidence(bridge, 1)
                frames = await self._read_all_evidence(entry)
                self.assertEqual(len(frames), 1)
                self.assertEqual(frames[0].raw.get("arEvidenceContentTruncated"), True)
                # The clipped abort settles "interrupted" instead of stalling pending forever.
                self.assertEqual(self._pi_latest_stop_reason(frames), "aborted")
            finally:
                await server.close()
                await bridge.stop("forced")

    async def test_clipped_final_abort_after_small_tool_use_frame_wins_at_settlement_read(
        self,
    ) -> None:
        # Finding 2 facet (b): a small mid-turn frame precedes the oversized final abort. The
        # latest-wins scan must decide on the clipped abort, never mis-settle "already-settled".
        with tempfile.TemporaryDirectory() as tmp:
            identity = _identity("l3e-pi-mixed")
            adapter = _EvidenceAdapter()
            bridge, server, entry = await self._serve(adapter, identity, tmp)
            try:
                adapter.emit_pi_content_ful_message_end("toolUse", filler_chars=50)
                adapter.emit_pi_content_ful_message_end("aborted", filler_chars=40000)
                await _wait_for_evidence(bridge, 2)
                frames = await self._read_all_evidence(entry)
                self.assertEqual(len(frames), 2)
                # The small mid-turn frame crossed whole; only the final abort was clipped.
                self.assertNotIn("arEvidenceTruncated", frames[0].raw)
                self.assertNotIn("arEvidenceContentTruncated", frames[0].raw)
                self.assertEqual(frames[1].raw.get("arEvidenceContentTruncated"), True)
                self.assertEqual(self._pi_latest_stop_reason(frames), "aborted")
            finally:
                await server.close()
                await bridge.stop("forced")

    async def test_oversized_codex_turn_completed_identity_survives_to_settlement_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            identity = _identity("l3e-codex")
            adapter = _EvidenceAdapter()
            bridge, server, entry = await self._serve(adapter, identity, tmp)
            try:
                descriptor = await asyncio.to_thread(read_submission_authority, entry)
                await self._dispatch_operation(
                    entry, adapter, descriptor.bridge_epoch, "l3e-codex-turn"
                )
                turn_id = "turn-oversized-7"
                params = {
                    "turn": {
                        "id": turn_id,
                        "status": "interrupted",
                        "items": [
                            {"id": f"item-{index}", "type": "agentMessage", "text": "y" * 400}
                            for index in range(200)
                        ],
                    }
                }
                adapter.complete_with_codex_turn("l3e-codex-turn", params)
                await _wait_for_evidence(bridge, 1)
                frames = await self._read_all_evidence(entry)
                completed = [frame for frame in frames if frame.kind == "completed"]
                self.assertEqual(len(completed), 1)
                # The turn's large items body pushed it over the budget and was clipped ...
                self.assertEqual(completed[0].raw.get("arEvidenceTruncated"), True)
                # ... yet turn.id (the correlation key) and turn.status both survive the read.
                self.assertEqual(self._codex_terminal_status(frames, turn_id), "interrupted")
            finally:
                await server.close()
                await bridge.stop("forced")
