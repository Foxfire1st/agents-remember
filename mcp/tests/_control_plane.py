"""Shared service/route test topology for control suites.

One full running seam: a structural fake adapter (interrupt- and asset-capable)
behind a real ``HarnessControlBridge`` + ``HarnessControlServer`` on a real
user-private Unix socket, a real ``TerminalCatalog`` row, and the L0
``register_conversation_routes`` composition. The only double is the harness
adapter at the far edge (no PTY, no runner log, no fixture authority); the
real submission authority owns dispatch, provenance, withdrawal, the timeline,
and the L2E recovery payload.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from collections.abc import AsyncIterator, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Literal, cast

from agents_remember.errors import HarnessControlError
from agents_remember.serving.conversation.authorization import (
    LocalOperatorAuthorizationResolver,
)
from agents_remember.serving.conversation.control import service as control_service_module
from agents_remember.serving.conversation.models import AuthorizationBinding
from agents_remember.serving.conversation.router import register_conversation_routes
from agents_remember.serving.conversation.runtime import (
    ConversationRuntime,
    ConversationScope,
)
from agents_remember.serving.harness_capabilities import CapabilitySnapshot, SetResult
from agents_remember.serving.harness_capability_catalog import HarnessCapabilityCatalog
from agents_remember.serving.harness_control_bridge import HarnessControlBridge
from agents_remember.serving.harness_control_client import read_submission_authority
from agents_remember.serving.harness_control_ipc import (
    HarnessControlServer,
    LocalControlEndpoint,
)
from agents_remember.serving.harness_control_models import (
    AR_EVIDENCE_KEY,
    AR_TERMINAL_OUTCOME_KEY,
    CONTROL_PROTOCOL_VERSION,
    REQUIRED_ADAPTER_CAPABILITIES,
    AcceptanceState,
    AdapterEvent,
    AdapterHandshake,
    AdapterSnapshot,
    ControlIdentity,
    ControlOperationRef,
    InteractionResponse,
    InterruptResult,
    LaunchSpec,
    PromptRequest,
    ReconciliationResult,
    ShutdownMode,
    SubmissionReceipt,
    TranscriptEntry,
)
from agents_remember.serving.terminal_catalog import TerminalCatalog, TerminalCatalogEntry
from agents_remember.serving.terminal_liveness import (
    TerminalCatalogLivenessConfig,
    utc_now,
)
from fastapi import FastAPI

NOW = "2026-07-20T08:00:00+00:00"
OPERATOR = AuthorizationBinding(
    principal_id="local-operator:1000",
    tenant_id="/tenant",
)
TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c626001000000ffff03000006000557bfabd40000000049454e44ae426082"
)


class LiveHost:
    def has_session(self, tmux_name: str) -> bool:
        del tmux_name
        return True


def empty_registry() -> list:
    return []


class FakeControlAdapter:
    """Harness-edge double with structural interrupt + asset submit capability."""

    def __init__(
        self,
        *,
        vendor_id: str = "thread-1",
        harness: Literal["codex", "pi", "claude"] = "codex",
        interrupt_capable: bool = True,
    ) -> None:
        self.vendor_id = vendor_id
        self.harness = harness
        self.interrupt_capable = interrupt_capable
        self.current: AdapterSnapshot | None = None
        self.events: asyncio.Queue[AdapterEvent | None] = asyncio.Queue()
        self.event_sequence = 0
        self.operations: list[ControlOperationRef | None] = []
        self.submit_requests: list[PromptRequest] = []
        self.interrupt_calls: list[dict[str, str | None]] = []
        self.active_turn: str | None = None
        self.transcript_sequence = 0
        self.interrupt_error: Exception | None = None
        self.last_interrupt: tuple[tuple[str, str], InterruptResult] | None = None
        self.auto_release = False
        self.submit_gate: asyncio.Event | None = None
        self.next_acceptance: str | None = None

    async def start(self, launch: LaunchSpec) -> AdapterHandshake:
        self.current = AdapterSnapshot(
            identity=launch.identity,
            control="ready",
            activity="idle",
            acceptance="immediate",
            vendor_session_id=self.vendor_id,
            raw={},
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
        del operation
        return SetResult(ok=True, acceptance="immediate", requested_value=model_key, detail="fake")

    async def set_effort(
        self, effort: str, *, operation: ControlOperationRef | None = None
    ) -> SetResult:
        del operation
        return SetResult(ok=True, acceptance="immediate", requested_value=effort, detail="fake")

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
        if self.submit_gate is not None:
            await self.submit_gate.wait()
        self.operations.append(request.operation)
        self.submit_requests.append(request)
        self._begin_turn(request)
        if self.auto_release and request.operation is not None and self.current is not None:
            self.emit(
                "completed",
                {},
                snapshot=replace(self.current, activity="idle"),
                operation=request.operation,
            )
        if self.next_acceptance is not None:
            acceptance = self.next_acceptance
            self.next_acceptance = None
            return SubmissionReceipt(
                request_id=request.request_id,
                acceptance=cast(AcceptanceState, acceptance),
                submitted_at=request.submitted_at,
                detail=f"fake {acceptance} knob",
            )
        return SubmissionReceipt(
            request_id=request.request_id,
            acceptance="immediate",
            submitted_at=request.submitted_at,
            accepted_at=request.submitted_at,
        )

    async def submit_with_assets(self, request: PromptRequest) -> SubmissionReceipt:
        receipt = await self.submit(request)
        return replace(receipt, raw={"assetIds": [asset.asset_id for asset in request.assets]})

    async def interrupt(
        self,
        *,
        turn_id: str | None,
        expected_operation_id: str | None,
    ) -> InterruptResult:
        if not self.interrupt_capable:
            raise AssertionError("interrupt must not be called on a non-capable double")
        if self.interrupt_error is not None:
            raise self.interrupt_error
        if self.harness == "codex":
            if self.active_turn is None:
                raise HarnessControlError("no active Codex turn to interrupt")
            if turn_id is not None and turn_id != self.active_turn:
                raise HarnessControlError("interrupt turn id does not match the active Codex turn")
            active = self.active_turn
        else:
            active_operation = self.operations[-1] if self.operations else None
            if active_operation is None or (
                expected_operation_id is not None
                and expected_operation_id != active_operation.operation_id
            ):
                raise HarnessControlError("no active Pi operation matches the expected identity")
            active = expected_operation_id or "unknown"
        pair = (turn_id or expected_operation_id or "", active)
        if self.last_interrupt is not None and self.last_interrupt[0] == pair:
            return self.last_interrupt[1]
        self.interrupt_calls.append(
            {"turn_id": turn_id, "expected_operation_id": expected_operation_id}
        )
        result = InterruptResult(
            acknowledgement="accepted",
            bridge_epoch="",
            operation=self.operations[-1] if self.operations else None,
            vendor_correlation_id=active,
            detail="fake native interrupt acknowledged",
        )
        self.last_interrupt = (pair, result)
        return result

    async def respond(self, response: InteractionResponse) -> None:
        del response

    async def reconcile(self, request_id: str) -> ReconciliationResult:
        return ReconciliationResult(request_id=request_id, state="unresolved", reconciled_at=NOW)

    async def stop(self, mode: ShutdownMode) -> None:
        del mode

    def _begin_turn(self, request: PromptRequest) -> None:
        if self.harness == "codex":
            self.active_turn = f"turn-{request.request_id}"
            if self.current is not None:
                self.current = replace(
                    self.current,
                    activity="running",
                    raw={**self.current.raw, "activeTurnId": self.active_turn},
                )

    def set_activity(self, activity: str, *, acceptance: str | None = None) -> None:
        """Move the bridge's reduced snapshot (dispatch gating) with a state event."""

        assert self.current is not None
        self.current = replace(
            self.current,
            activity=activity,
            **({"acceptance": acceptance} if acceptance is not None else {}),
        )
        self.emit("state", {}, snapshot=self.current)

    def emit(
        self,
        kind: str,
        raw: Mapping[str, object],
        *,
        transcript: tuple = (),
        snapshot: AdapterSnapshot | None = None,
        operation: ControlOperationRef | None = None,
    ) -> None:
        assert self.current is not None
        self.event_sequence += 1
        if snapshot is None and kind in {"state", "completed", "disconnected", "failed"}:
            snapshot_raw = {key: value for key, value in raw.items() if key != AR_EVIDENCE_KEY}
            snapshot = replace(self.current, raw={**self.current.raw, **snapshot_raw})
        if snapshot is not None:
            self.current = replace(snapshot, last_event_sequence=self.event_sequence)
        self.events.put_nowait(
            AdapterEvent(
                sequence=self.event_sequence,
                kind=kind,
                identity=self.current.identity,
                created_at=NOW,
                snapshot=snapshot,
                transcript=transcript,
                raw=dict(raw),
                operation=operation,
            )
        )

    def settle_turn(self, outcome: str = "interrupted") -> None:
        """Emit the codex turn/completed settlement for the active turn."""

        turn = self.active_turn or "turn-unknown"
        self.active_turn = None
        operation = self.operations[-1] if self.operations else None
        self.emit(
            "completed",
            {
                "codexMethod": "turn/completed",
                AR_EVIDENCE_KEY: {
                    "threadId": self.vendor_id,
                    "turn": {"id": turn, "status": outcome, "items": []},
                },
            },
            snapshot=replace(self.current, activity="idle", raw={}) if self.current else None,
            operation=operation,
        )

    def pi_settle(self, stop_reason: str = "aborted") -> None:
        """Emit the pi message_end settlement evidence + completion release."""

        operation = self.operations[-1] if self.operations else None
        self.emit(
            "pi:message_end",
            {
                "piEvent": {"type": "message_end"},
                AR_EVIDENCE_KEY: {
                    "type": "message_end",
                    "message": {"role": "assistant", "content": [], "stopReason": stop_reason},
                },
            },
            snapshot=replace(self.current, activity="idle") if self.current else None,
        )
        self.emit(
            "completed",
            {"piEvent": {"type": "agent_end"}},
            snapshot=replace(self.current, activity="idle") if self.current else None,
            operation=operation,
        )

    def pi_emit_message_end(self, *, text: str, stop_reason: str) -> None:
        """Emit ONE production content-FUL pi message_end, with no completion release.

        Mirrors ``pi_rpc_events._message_event`` line 241 exactly: a message_end that
        finishes with text content crosses as kind ``transcript`` (NOT ``pi:message_end``),
        with a transcript entry minted and the *full* frame — ``stopReason`` included —
        riding the reserved evidence key. Compose several of these plus one ``pi_release``
        to drive a multi-message turn; pass an oversized ``text`` (> 32 KiB serialized) to
        exercise the REAL bridge's evidence clip — the frame crosses ``AR_EVIDENCE_KEY`` and
        is clipped by ``clip_evidence_payload`` exactly as in production, so the L3E
        identity-preservation path is what is under test.
        """

        self.transcript_sequence += 1
        frame = {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": text}],
                "stopReason": stop_reason,
            },
        }
        entry = TranscriptEntry(
            sequence=self.transcript_sequence,
            role="assistant",
            text=text,
            created_at=NOW,
            raw=dict(frame),
        )
        self.emit(
            "transcript",
            {
                "piEvent": {"type": "message_end"},
                AR_EVIDENCE_KEY: dict(frame),
            },
            transcript=(entry,),
            snapshot=replace(self.current, activity="idle") if self.current else None,
        )

    def pi_release(self) -> None:
        """Emit the pi completion release (``agent_end``) for the active operation."""

        operation = self.operations[-1] if self.operations else None
        self.emit(
            "completed",
            {"piEvent": {"type": "agent_end"}},
            snapshot=replace(self.current, activity="idle") if self.current else None,
            operation=operation,
        )

    def pi_settle_with_content(self, stop_reason: str = "stop") -> None:
        """Emit the ordinary content-FUL message_end settlement + completion release.

        The content-less ``pi_settle`` above is only reachable on an interrupted-before-text
        turn; this is the ordinary pi turn completion (one finished message, then release).
        """

        self.pi_emit_message_end(text="final answer", stop_reason=stop_reason)
        self.pi_release()

    def claude_settle(self, outcome: str = "cancelled") -> None:
        """Emit the claude result settlement evidence with the adapter-correlated stamp.

        Mirrors ``claude_stream_state._handle_result``: the completed event carries the native
        result frame plus the adapter-attributed ``arTerminalOutcome`` classification — the
        accepted-interrupt correlation the settlement ledger reads. ``cancelled`` reproduces
        the probe-locked interrupted shape (error_during_execution/is_error +
        aborted_streaming), ``failed`` the unprovoked error, ``completed`` natural completion.
        """

        operation = self.operations[-1] if self.operations else None
        self.emit(
            "completed",
            {
                "terminalOutcome": outcome,
                AR_EVIDENCE_KEY: {
                    "type": "result",
                    "subtype": "success" if outcome == "completed" else "error_during_execution",
                    "is_error": outcome != "completed",
                    "terminal_reason": "aborted_streaming" if outcome == "cancelled" else None,
                    "session_id": self.vendor_id,
                    "uuid": f"claude-result-{outcome}",
                    AR_TERMINAL_OUTCOME_KEY: outcome,
                },
            },
            snapshot=replace(self.current, activity="idle") if self.current else None,
            operation=operation,
        )


class ControlledEntry:
    def __init__(self, session: str, endpoint: Path) -> None:
        self.id = session
        self.tmux_name = f"tmux-{session}"
        self.created_at = NOW
        self.control_endpoint = endpoint


class ControlHarness:
    """Bridge + IPC server + catalog + composition for one fake session."""

    def __init__(
        self,
        root: Path,
        adapter: FakeControlAdapter,
        session: str,
        *,
        harness: str,
    ) -> None:
        self.session = session
        self.adapter = adapter
        self.identity = ControlIdentity(
            ar_session_id=session, tmux_name=f"tmux-{session}", created_at=NOW
        )
        self.endpoint = LocalControlEndpoint.for_session(root / "ctl", self.identity)
        self.bridge = HarnessControlBridge(self.identity, adapter, clock=lambda: NOW)
        self.server = HarnessControlServer(self.endpoint, self.bridge)
        self.catalog = TerminalCatalog(root / f"terminal-sessions-{session}.json")
        self.catalog.upsert(
            TerminalCatalogEntry(
                id=session,
                label=session,
                kind="harness",
                harness=harness,
                lifecycle_id=None,
                cwd=Path("/workspace"),
                tmux_name=self.identity.tmux_name,
                command=("fake",),
                created_at=NOW,
                last_attached_at=NOW,
                status="running",
                control_endpoint=self.endpoint.path,
            )
        )
        self.runtime = ConversationRuntime(
            scope=ConversationScope(workspace_root=root, coordination_root=root),
            catalog=self.catalog,
            host=LiveHost(),
            harness_registry=empty_registry,
            liveness_clock=utc_now,
            liveness_config=TerminalCatalogLivenessConfig(),
            capability_catalog=HarnessCapabilityCatalog(root),
            authorization=LocalOperatorAuthorizationResolver.for_workspace(root),
        )
        self.app = FastAPI()
        register_conversation_routes(self.app, self.runtime)
        # Anchor the control service's lease clock to the fake topology's fixed NOW.
        # Every record is stamped at NOW (the bridge clock is ``lambda: NOW``), so a
        # 900 s recovery/staged-asset lease (NOW + 900 s) must be measured against NOW
        # too. The production default (real ``utc_clock``) would treat every lease as
        # expired once the suite runs more than 900 s after NOW, spuriously disposing
        # recovery content and failing the happy-path recovery tests by wall-clock
        # time-of-day. Seeding the per-runtime service cache makes the registered routes
        # resolve this same NOW-anchored service; direct-call suites read it as
        # ``harness.service``. Lease-EXPIRY tests deliberately build their own advancing
        # clock on a separate service instance, so they still prove expiry by advancing
        # the frozen clock, not by real time passing.
        self.service = control_service_module.ConversationControlService(
            self.runtime, clock=lambda: NOW
        )
        control_service_module._SERVICES[self.runtime] = self.service
        self.control_entry = ControlledEntry(session, self.endpoint.path)
        self.epoch = ""

    async def start(self) -> None:
        launch = LaunchSpec(
            identity=self.identity,
            harness_id="fake",
            cwd=Path("/workspace"),
            argv=("fake",),
            env={},
        )
        await self.bridge.start(launch)
        await self.server.start()
        self.epoch = (
            await asyncio.to_thread(read_submission_authority, self.control_entry)
        ).bridge_epoch

    async def stop(self) -> None:
        await self.server.close()
        await self.bridge.stop("forced")


def make_harness(
    test: unittest.TestCase,
    adapter: FakeControlAdapter,
    session: str,
    *,
    harness: str,
) -> ControlHarness:
    assert isinstance(test, unittest.TestCase)
    tmp = tempfile.TemporaryDirectory(prefix=f"ar-ctl-{session}-")
    root = Path(tmp.name)
    test.addCleanup(tmp.cleanup)
    return ControlHarness(root, adapter, session, harness=harness)


async def drive_activity(harness: ControlHarness, activity: str, timeout: float = 5.0) -> None:
    """Set the adapter activity and wait until the bridge reduces it visibly.

    A bare ``set_activity`` races the submit path: a submission admitted while
    the bridge still reads idle takes the wait-for-dispatch path and blocks
    forever once the busy state lands before dispatch.
    """

    harness.adapter.set_activity(activity)
    deadline = asyncio.get_running_loop().time() + timeout
    while harness.bridge.snapshot().activity != activity:
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"bridge never reduced activity {activity!r}")
        await asyncio.sleep(0.01)
