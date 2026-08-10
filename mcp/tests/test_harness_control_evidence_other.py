from __future__ import annotations

import asyncio
import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agents_remember.errors import HarnessControlError
from agents_remember.models.conversations.control_wire import (
    ControlIdentity,
    ControlOperationRef,
)
from agents_remember.models.conversations.evidence import (
    AR_EVIDENCE_KEY,
    EvidenceFrame,
    clip_evidence_payload,
)
from agents_remember.serving.harness_control_bridge import HarnessControlBridge
from agents_remember.serving.harness_control_client import (
    ControlSubmission,
    read_control_evidence,
    read_submission_authority,
    submit_control_prompt,
)
from agents_remember.serving.harness_control_factories import create_harness_protocol_adapter
from agents_remember.serving.harness_control_ipc import HarnessControlServer, LocalControlEndpoint
from agents_remember.serving.harness_control_models import (
    PromptRequest,
)
from agents_remember.serving.harness_control_runner import (
    RunnerConfig,
    control_runner_command,
    parse_runner_config,
)
from agents_remember.serving.hosted_session_runtime import HostedSessionRuntime
from agents_remember.serving.pi_rpc_adapter import PiRpcAdapter
from agents_remember.serving.terminal_catalog import (
    TerminalCatalog,
)
from agents_remember.serving.terminal_opener import (
    ControlRunnerRequest,
    TerminalLaunchRequest,
    open_terminal_session,
)
from test_harness_control_evidence import (
    CLAUDE_SESSION,
    NOW,
    _claude_adapter,
    _claude_init_frames,
    _ControlledEntry,
    _detected,
    _EvidenceAdapter,
    _FakeClaudeTransport,
    _FakeHost,
    _FakePiTransport,
    _identity,
    _launch,
    _obj,
    _wait_for_evidence,
)


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

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_harness_control_evidence_other.py:207).
    async def test_result_usage_and_cost_forward_as_evidence(self) -> None:  # pragma: no cover
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


class ResumeOpenerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.catalog = TerminalCatalog(self.tmp / "terminal-sessions.json")
        self.host = _FakeHost()

    def _open(self, *, harness: str = "codex", resume_thread_id: str | None = None):
        return open_terminal_session(
            runtime=HostedSessionRuntime(catalog=self.catalog, host=self.host),  # type: ignore[arg-type]
            session_id="resume-worker-1",
            launch=TerminalLaunchRequest(
                kind="harness",
                workspace_root=self.tmp,
                shell="/bin/bash",
                harness=harness,
                which=_detected,
                control=ControlRunnerRequest(resume_thread_id=resume_thread_id),
            ),
        )

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
        self.assertEqual(set(clipped), {"arEvidenceTruncated", "originalBytes", "preview", "turn"})
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
        self.assertEqual(
            set(no_reason), {"arEvidenceTruncated", "originalBytes", "preview", "type"}
        )
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

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_harness_control_evidence_other.py:564).
    async def _read_all_evidence(
        self, entry: _ControlledEntry
    ) -> list[EvidenceFrame]:  # pragma: no cover
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

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_harness_control_evidence_other.py:581).
    async def _dispatch_operation(  # pragma: no cover
        self, entry: _ControlledEntry, adapter: _EvidenceAdapter, epoch: str, request_id: str
    ) -> None:
        """Submit and land one cockpit prompt so a bound operation ref exists for the completion."""

        await asyncio.to_thread(
            submit_control_prompt,
            entry,
            "drive one turn",
            ControlSubmission(source="cockpit", request_id=request_id, expected_bridge_epoch=epoch),
        )
        for _ in range(400):
            if any(item.request_id == request_id for item in adapter.submissions):
                return
            await asyncio.sleep(0)
        raise AssertionError("submission never reached the adapter")

    @staticmethod
    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_harness_control_evidence_other.py:599).
    def _pi_latest_stop_reason(frames: list[EvidenceFrame]) -> str | None:  # pragma: no cover
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
    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_harness_control_evidence_other.py:615).
    def _codex_terminal_status(
        frames: list[EvidenceFrame], turn_id: str
    ) -> str | None:  # pragma: no cover
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

    async def test_oversized_codex_turn_completed_identity_survives_to_settlement_read(
        self,
    ) -> None:
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
