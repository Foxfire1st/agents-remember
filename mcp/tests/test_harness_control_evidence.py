"""Contract tests for the native evidence and resume substrate."""

import asyncio
import json
import unittest
from collections.abc import (
    AsyncIterator,
    Mapping,
    Sequence,
)
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import cast

from agents_remember.errors import (
    HarnessAdapterDisconnectedError,
    HarnessControlError,
)
from agents_remember.models.conversations.control_wire import (
    AdapterSnapshot,
    ControlIdentity,
    ControlOperationRef,
    LaunchSpec,
    SubmissionReceipt,
)
from agents_remember.models.conversations.evidence import (
    AR_EVIDENCE_KEY,
    NativeEvidencePage,
)
from agents_remember.models.terminal_catalog import (
    TerminalCatalogEntry,
)
from agents_remember.serving.claude_stream_limits import ClaudeAdapterLimits
from agents_remember.serving.codex_app_server_adapter import (
    CodexAppServerAdapter,
    CodexAppServerSettings,
)
from agents_remember.serving.harness_capabilities import (
    CapabilitySnapshot,
    SetResult,
)
from agents_remember.serving.harness_control_bridge import (
    BridgeLimits,
    HarnessControlBridge,
)
from agents_remember.serving.harness_control_claude import ClaudeStreamJsonAdapter
from agents_remember.serving.harness_control_models import (
    CONTROL_PROTOCOL_VERSION,
    REQUIRED_ADAPTER_CAPABILITIES,
    AdapterEvent,
    AdapterHandshake,
    InteractionResponse,
    PromptRequest,
    ReconciliationResult,
    ShutdownMode,
    TranscriptEntry,
)
from agents_remember.serving.hosted_control_projection import control_snapshot_entry
from agents_remember.serving.terminal import (
    TerminalSessionBinding,
    TerminalSessionSpec,
)

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


# 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_harness_control_evidence.py:86).
async def _wait_for_evidence(
    bridge: HarnessControlBridge, sequence: int
) -> None:  # pragma: no cover
    for _ in range(200):
        if bridge.evidence().latest_sequence >= sequence:
            return
        await asyncio.sleep(0)
    raise AssertionError(f"evidence sequence {sequence} never reached the bridge buffer")


def _obj(value: object) -> Mapping[str, object]:
    assert isinstance(value, Mapping)
    return cast(Mapping[str, object], value)


# 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_harness_control_evidence.py:99).
async def _wait_for_failure(bridge: HarnessControlBridge) -> None:  # pragma: no cover
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

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_harness_control_evidence.py:134).
    async def snapshot(self) -> AdapterSnapshot:  # pragma: no cover
        assert self.current is not None
        return self.current

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_harness_control_evidence.py:138).
    def advertise(self) -> CapabilitySnapshot:  # pragma: no cover
        return CapabilitySnapshot(models=(), selected_model_key=None, selected_effort=None)

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_harness_control_evidence.py:141).
    async def set_model(  # pragma: no cover
        self, model_key: str, *, operation: ControlOperationRef | None = None
    ) -> SetResult:
        del model_key, operation
        raise HarnessControlError("unused in evidence tests")

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_harness_control_evidence.py:147).
    async def set_effort(  # pragma: no cover
        self, effort: str, *, operation: ControlOperationRef | None = None
    ) -> SetResult:
        del effort, operation
        raise HarnessControlError("unused in evidence tests")

    async def preflight_operation(self, operation: ControlOperationRef) -> None:
        del operation

    def subscribe(self) -> AsyncIterator[AdapterEvent]:
        return self._stream()

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_harness_control_evidence.py:159).
    async def _stream(self) -> AsyncIterator[AdapterEvent]:  # pragma: no cover
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

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_harness_control_evidence.py:176).
    async def respond(self, response: InteractionResponse) -> None:  # pragma: no cover
        del response

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_harness_control_evidence.py:179).
    async def reconcile(self, request_id: str) -> ReconciliationResult:  # pragma: no cover
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


class _ThreadAwareNativePageAdapter(_NativePageAdapter):
    """Multiplexed adapter accepting the additive per-thread selector.

    Records the exact call shape so the IPC test proves ``thread_id`` is forwarded only
    when the wire carries it — an absent ``threadId`` keeps the single-thread call.
    """

    def __init__(self, page: NativeEvidencePage) -> None:
        super().__init__(page)
        self.thread_calls: list[tuple[str | None, int, int, str | None]] = []

    async def read_native_page(  # type: ignore[override] - additive multiplex kwarg
        self,
        *,
        cursor: str | None,
        limit: int,
        byte_budget: int,
        thread_id: str | None = None,
    ) -> NativeEvidencePage:
        self.thread_calls.append((cursor, limit, byte_budget, thread_id))
        return await super().read_native_page(cursor=cursor, limit=limit, byte_budget=byte_budget)


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
        from agents_remember.models.conversations.evidence import (  # noqa: PLC0415
            AR_EVIDENCE_METHOD_KEY,
            evidence_page_json,
        )
        from agents_remember.serving.harness_control_client import _evidence_page  # noqa: PLC0415

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
            self.assertEqual(restored.frames[0].native_method, "mcpServer/startupStatus/updated")
        finally:
            await bridge.stop("forced")

    async def test_buffer_count_eviction_reports_honest_gap_floor_at_two_sizes(self) -> None:
        for limit, emitted, expected_floor, expected_kept in ((4, 6, 2, 4), (2, 4, 2, 2)):
            with self.subTest(limit=limit):
                identity = _identity(f"evict-{limit}")
                adapter = _EvidenceAdapter()
                bridge = HarnessControlBridge(
                    identity, adapter, clock=lambda: NOW, limits=BridgeLimits(evidence=limit)
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
            identity, adapter, clock=lambda: NOW, limits=BridgeLimits(evidence_frame_bytes=128)
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

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_harness_control_evidence.py:510).
    async def test_evidence_page_byte_budget_truncates_without_overlap_or_gap(
        self,
    ) -> None:  # pragma: no cover
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

    async def test_a_present_but_unusable_native_method_fails_the_bridge_visibly(self) -> None:
        # The projector switches on ``native_method`` to decide what a frame IS. An empty string or
        # a non-string would be carried as a method that matches nothing, so every frame behind it
        # would be silently misclassified. Absent is fine; present-and-unusable is not.
        from agents_remember.models.conversations.evidence import (  # noqa: PLC0415
            AR_EVIDENCE_METHOD_KEY,
        )

        for method in ("", 7, {"method": "x"}):
            identity = _identity()
            adapter = _EvidenceAdapter()
            bridge = HarnessControlBridge(identity, adapter, clock=lambda: NOW)
            await bridge.start(_launch(identity))
            try:
                adapter.emit(
                    "state",
                    {AR_EVIDENCE_METHOD_KEY: method, AR_EVIDENCE_KEY: {"a": 1}},
                )
                await _wait_for_failure(bridge)
                self.assertIn(
                    "adapter evidence method must be non-empty text when present",
                    str(bridge.snapshot().raw["bridgeError"]),
                )
                # The frame is refused outright rather than buffered without its method.
                self.assertEqual(bridge.evidence().frames, ())
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
    )


class _ControlledEntry:
    def __init__(self, identity: ControlIdentity, endpoint: Path) -> None:
        self.id = identity.ar_session_id
        self.tmux_name = identity.tmux_name
        self.created_at = identity.created_at
        self.control_endpoint = endpoint


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

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_harness_control_evidence.py:647).
    async def request(self, method, params, *, before_write=None):  # pragma: no cover
        if before_write is not None:
            before_write()
        self.requests.append((method, dict(params)))
        return deepcopy(self.responses[method].pop(0))

    async def notify(self, method, params) -> None:
        del method, params

    def messages(self) -> AsyncIterator[dict[str, object]]:
        return self._stream()

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_harness_control_evidence.py:659).
    async def _stream(self) -> AsyncIterator[dict[str, object]]:  # pragma: no cover
        while True:
            message = await self.incoming.get()
            if message is None:
                return
            yield message

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_harness_control_evidence.py:666).
    async def respond(self, request_id, result) -> None:  # pragma: no cover
        del request_id, result

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_harness_control_evidence.py:669).
    async def respond_error(self, request_id, *, code, message) -> None:  # pragma: no cover
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


# 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_harness_control_evidence.py:700).
def _thread_read_result(
    items: Sequence[Mapping[str, object]], *, turn_id: str = "turn-1"
):  # pragma: no cover
    return {
        "thread": {
            "id": "thread-1",
            "turns": [{"id": turn_id, "items": [dict(item) for item in items]}],
        }
    }


def _thread_item_page(
    item: Mapping[str, object],
    *,
    next_cursor: str | None,
    turn_id: str = "turn-1",
):
    return {
        "data": [{"turnId": turn_id, "item": dict(item)}],
        "nextCursor": next_cursor,
        "backwardsCursor": None,
    }


class _FakePiTransport:
    def __init__(self, entries: list[dict[str, object]] | None = None) -> None:
        self.entries = entries or []
        self.commands: list[dict[str, object]] = []
        self.event_queue: asyncio.Queue[Mapping[str, object] | None] = asyncio.Queue()
        self.stop_modes: list[ShutdownMode] = []

    async def start(self, launch: LaunchSpec) -> None:
        del launch

    @property
    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_harness_control_evidence.py:733).
    def event_token(self) -> int:  # pragma: no cover
        return 0

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_harness_control_evidence.py:736).
    async def request(self, command, *, before_write=None):  # pragma: no cover
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

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_harness_control_evidence.py:801).
    async def send(self, command, *, before_write=None) -> None:  # pragma: no cover
        del command, before_write

    def events(self) -> AsyncIterator[Mapping[str, object]]:
        return self._stream()

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_harness_control_evidence.py:807).
    async def _stream(self) -> AsyncIterator[Mapping[str, object]]:  # pragma: no cover
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


# ---------------------------------------------------------------------------
# Codex resume launch channel
# ---------------------------------------------------------------------------


class _FakeHost:
    def __init__(self) -> None:
        self.ensured: list[dict[str, object]] = []
        self.known: set[str] = set()

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_harness_control_evidence.py:886).
    def has_session(self, tmux_name: str) -> bool:  # pragma: no cover
        return tmux_name in self.known

    def ensure(self, sid: str, spec: TerminalSessionSpec) -> TerminalSessionBinding:
        tmux_name = spec.tmux_name_for(sid)
        self.ensured.append({"sid": sid, "command": spec.command})
        self.known.add(tmux_name)
        return TerminalSessionBinding(
            sid=sid,
            tmux_name=tmux_name,
            cwd=spec.cwd,
            command=spec.command,
            lifecycle_id=spec.lifecycle_id,
            suspend_unsafe=spec.suspend_unsafe,
        )


def _detected(_command: str) -> str | None:
    return "/usr/bin/harness"
