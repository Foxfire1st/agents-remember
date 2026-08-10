"""Production-route tests for the active conversation API.

Every test drives the REAL composition on one event loop: a per-session
``HarnessControlBridge`` + ``HarnessControlServer`` on a real user-private
Unix socket (the L0E seam), a real ``TerminalCatalog`` row, the L0
``register_conversation_routes`` composition, and HTTP over ASGI. The only
double is the harness adapter at the far edge (no PTY, no runner log, no
fixture authority), which emits native frames exactly as the production
mappers do and lets the real submission authority own dispatch/provenance.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import tempfile
import unittest
from collections.abc import AsyncIterator, Mapping, MutableMapping
from dataclasses import replace
from itertools import pairwise
from pathlib import Path
from typing import Any
from unittest import mock
from urllib.parse import urlencode

import httpx
import uvicorn
from agents_remember.models.conversations.control_wire import (
    AdapterSnapshot,
    ControlIdentity,
    ControlOperationRef,
    LaunchSpec,
    SubmissionReceipt,
)
from agents_remember.models.conversations.evidence import (
    AR_EVIDENCE_KEY,
    NativeEvidenceFrame,
    NativeEvidencePage,
)
from agents_remember.models.conversations.identity import (
    ActiveConversationRef,
    AuthorizationBinding,
)
from agents_remember.models.conversations.status import (
    CANONICAL_TURN_STATE_BY_EVIDENCE,
)
from agents_remember.models.terminal_catalog import (
    TerminalCatalogEntry,
)
from agents_remember.serving.conversation.active.cursor import (
    mint_event_cursor,
    mint_page_cursor,
)
from agents_remember.serving.conversation.active.service import (
    active_conversation_service,
)
from agents_remember.serving.conversation.active.status import (
    classify_snapshot,
    seat_turn_state_for,
)
from agents_remember.serving.conversation.authorization import (
    LocalOperatorAuthorizationResolver,
)
from agents_remember.serving.conversation.router import register_conversation_routes
from agents_remember.serving.conversation.runtime import (
    ConversationRuntime,
    ConversationScope,
)
from agents_remember.serving.harness_capabilities import CapabilitySnapshot, SetResult
from agents_remember.serving.harness_capability_catalog import HarnessCapabilityCatalog
from agents_remember.serving.harness_control_bridge import HarnessControlBridge
from agents_remember.serving.harness_control_client import (
    ControlPlaneClient,
    ControlSubmission,
    read_submission_authority,
    submit_control_prompt,
)
from agents_remember.serving.harness_control_ipc import (
    HarnessControlServer,
    LocalControlEndpoint,
)
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
from agents_remember.serving.hosted_control_projection import snapshot_turn_state
from agents_remember.serving.terminal_catalog import (
    TerminalCatalog,
)
from agents_remember.serving.terminal_liveness import (
    TerminalCatalogLivenessConfig,
    utc_now,
)
from fastapi import FastAPI
from starlette.middleware.gzip import GZipMiddleware

NOW = "2026-07-19T08:00:00+00:00"
OPERATOR = AuthorizationBinding(
    principal_id="local-operator:1000",
    tenant_id="/tenant",
)
REMOTE_PEER = ("198.51.100.7", 4433)


class _LiveHost:
    def has_session(self, tmux_name: str) -> bool:
        del tmux_name
        return True


def _empty_registry() -> list:
    return []


class _FakeAdapter:
    """Harness-edge double emitting production-shaped events through the seam."""

    def __init__(self, *, vendor_id: str = "thread-1") -> None:
        self.vendor_id = vendor_id
        self.current: AdapterSnapshot | None = None
        self.events: asyncio.Queue[AdapterEvent | None] = asyncio.Queue()
        self.event_sequence = 0
        self.native_error: Exception | None = None
        self.operations: list[ControlOperationRef | None] = []

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
        del model_key, operation
        raise AssertionError("unused")

    async def set_effort(
        self, effort: str, *, operation: ControlOperationRef | None = None
    ) -> SetResult:
        del effort, operation
        raise AssertionError("unused")

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
        self.operations.append(request.operation)
        return SubmissionReceipt(
            request_id=request.request_id,
            acceptance="immediate",
            submitted_at=request.submitted_at,
            accepted_at=request.submitted_at,
        )

    async def respond(self, response: InteractionResponse) -> None:
        del response

    async def reconcile(self, request_id: str) -> ReconciliationResult:
        return ReconciliationResult(request_id=request_id, state="unresolved", reconciled_at=NOW)

    async def stop(self, mode: ShutdownMode) -> None:
        del mode

    def emit(
        self,
        kind: str,
        raw: Mapping[str, object],
        *,
        transcript: tuple[TranscriptEntry, ...] = (),
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

    def complete_turn(self, raw: Mapping[str, object]) -> None:
        """Emit the terminal settlement the ordinary lane requires (real op ref)."""

        operation = self.operations[-1] if self.operations else None
        self.emit("completed", raw, operation=operation)


class _NativePageAdapter(_FakeAdapter):
    """Adds a structural native page read over scripted frames."""

    def __init__(self, frames: list[NativeEvidenceFrame], **kwargs) -> None:
        super().__init__(**kwargs)
        self.frames = frames

    async def read_native_page(
        self,
        *,
        cursor: str | None,
        limit: int,
        byte_budget: int,
    ) -> NativeEvidencePage:
        del byte_budget
        if self.native_error is not None:
            raise self.native_error
        start = 0
        if cursor is not None:
            for index, frame in enumerate(self.frames):
                if frame.native_id == cursor:
                    start = index + 1
                    break
        selected = self.frames[start : start + limit]
        truncated = start + limit < len(self.frames)
        return NativeEvidencePage(
            frames=tuple(selected),
            next_cursor=selected[-1].native_id if truncated and selected else None,
            truncated=truncated,
            bridge_epoch="",
        )


class _ControlledEntry:
    def __init__(self, session: str, endpoint: Path) -> None:
        self.id = session
        self.tmux_name = f"tmux-{session}"
        self.created_at = NOW
        self.control_endpoint = endpoint


class _Harness:
    """One full running topology: bridge + IPC server + app + ASGI client."""

    def __init__(self, root: Path, adapter: _FakeAdapter, session: str, *, harness: str) -> None:
        self.session = session
        self.adapter = adapter
        self.identity = ControlIdentity(
            ar_session_id=session, tmux_name=f"tmux-{session}", created_at=NOW
        )
        self.endpoint = LocalControlEndpoint.for_session(root / "ctl", self.identity)
        self.bridge = HarnessControlBridge(self.identity, adapter, clock=lambda: NOW)
        self.server = HarnessControlServer(self.endpoint, self.bridge)
        catalog = TerminalCatalog(root / f"terminal-sessions-{session}.json")
        catalog.upsert(
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
        runtime = ConversationRuntime(
            scope=ConversationScope(workspace_root=root, coordination_root=root),
            catalog=catalog,
            control_plane=ControlPlaneClient(),
            host=_LiveHost(),
            harness_registry=_empty_registry,
            liveness_clock=utc_now,
            liveness_config=TerminalCatalogLivenessConfig(),
            capability_catalog=HarnessCapabilityCatalog(root),
            authorization=LocalOperatorAuthorizationResolver.for_workspace(root),
        )
        self.runtime = runtime
        self.app = FastAPI()
        register_conversation_routes(self.app, runtime)
        # Mirror serving/app.py: the production stack gzips JSON GETs, and its
        # responder holds http.response.start until the first body chunk even
        # for the gzip-excluded SSE channels.
        self.app.add_middleware(GZipMiddleware, compresslevel=6)
        self.control_entry = _ControlledEntry(session, self.endpoint.path)
        self._replacement: tuple | None = None
        self._auth_patcher = mock.patch(
            "agents_remember.serving.conversation.active.api.resolve_conversation_authorization",
            lambda request: OPERATOR,
        )

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
        self._auth_patcher.start()
        # A real uvicorn wire: SSE needs true TCP (httpx's ASGITransport
        # buffers whole responses and can never stream).
        self._uvicorn = uvicorn.Server(
            uvicorn.Config(
                self.app,
                host="127.0.0.1",
                port=0,
                log_level="warning",
                access_log=False,
            )
        )
        self._uvicorn_task = asyncio.create_task(self._uvicorn.serve())
        while not self._uvicorn.started:
            await asyncio.sleep(0.05)
        port = self._uvicorn.servers[0].sockets[0].getsockname()[1]
        self.client = httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}")

    async def stop(self) -> None:
        self._auth_patcher.stop()
        await self.client.aclose()
        self._uvicorn.should_exit = True
        await self._uvicorn_task
        await self.server.close()
        await self.bridge.stop("forced")


def _codex_params(item: Mapping[str, object], *, turn: str) -> dict[str, object]:
    return {"threadId": "thread-1", "turnId": turn, "item": dict(item), "completedAtMs": 1}


def _sse_frames(text: str) -> list[dict[str, str]]:
    frames: list[dict[str, str]] = []
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        parsed: dict[str, str] = {}
        for line in block.splitlines():
            if line.startswith(":"):
                continue  # SSE comment (the priming line); EventSource ignores these natively
            key, _, value = line.partition(": ")
            parsed[key] = value
        if parsed:
            frames.append(parsed)
    return frames


class ProductionRouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="ar-active-api-")
        self.root = Path(self._tmp.name)
        self.adapter = _FakeAdapter()
        self.harness = _Harness(self.root, self.adapter, "ar-api-1", harness="codex")
        await self.harness.start()
        self.epoch = self.harness.epoch
        self.client = self.harness.client

    async def asyncTearDown(self) -> None:
        replacement = getattr(self.harness, "_replacement", None)
        if replacement is not None:
            bridge, server = replacement
            await server.close()
            await bridge.stop("forced")
        await self.harness.stop()
        self._tmp.cleanup()

    async def _page(self, session: str = "ar-api-1", epoch: str | None = None, **params) -> dict:
        response = await self.client.get(
            f"/api/terminal/{session}/conversation",
            params={"expectedBridgeEpoch": epoch or self.epoch, **params},
        )
        assert response.status_code == 200, response.text
        return response.json()

    def _events_path(self, session: str = "ar-api-1") -> str:
        return f"/api/terminal/{session}/conversation/events"

    async def _drive_codex_turn(self, turn: str = "turn-1", *, client_id: str | None = None) -> str:
        request_id = client_id or f"req-{turn}"
        expected_operations = len(self.adapter.operations) + 1
        receipt = await asyncio.to_thread(
            submit_control_prompt,
            self.harness.control_entry,
            f"prompt for {turn}",
            ControlSubmission(
                source="cockpit", request_id=request_id, expected_bridge_epoch=self.epoch
            ),
        )
        self.assertIn(receipt.acceptance, {"immediate", "queued"})
        await self._await_operations(expected_operations)
        self.adapter.emit(
            "codex-notification",
            {
                "codexMethod": "item/completed",
                AR_EVIDENCE_KEY: _codex_params(
                    {
                        "id": f"{turn}-user",
                        "type": "userMessage",
                        "clientId": request_id,
                        "content": [{"type": "text", "text": "hello"}],
                    },
                    turn=turn,
                ),
            },
        )
        self.adapter.emit(
            "codex-notification",
            {
                "codexMethod": "item/completed",
                AR_EVIDENCE_KEY: _codex_params(
                    {"id": f"{turn}-agent", "type": "agentMessage", "text": "answer"},
                    turn=turn,
                ),
            },
        )
        self.adapter.complete_turn(
            {
                "codexMethod": "turn/completed",
                AR_EVIDENCE_KEY: {
                    "threadId": "thread-1",
                    "turn": {"id": turn, "status": "completed", "items": []},
                },
            },
        )
        self.adapter.emit(
            "codex-notification",
            {
                "codexMethod": "item/completed",
                AR_EVIDENCE_KEY: _codex_params(
                    {
                        "id": f"{turn}-cmd",
                        "type": "commandExecution",
                        "command": "ls -la",
                        "status": "completed",
                        "aggregatedOutput": "total 0",
                    },
                    turn=turn,
                ),
            },
        )
        return request_id

    async def _await_operations(self, count: int) -> None:
        deadline = asyncio.get_running_loop().time() + 10.0
        while len(self.adapter.operations) < count:
            if asyncio.get_running_loop().time() > deadline:
                self.fail(f"adapter never dispatched operation #{count}")
            await asyncio.sleep(0.05)

    # -- authorization / identity / error mapping ---------------------------

    async def test_remote_peer_fails_closed_typed_403(self) -> None:
        self.harness._auth_patcher.stop()
        # The real L0 resolver reads only the ASGI peer; uvicorn's default
        # loopback-trusted proxy handling rewrites the peer from XFF here,
        # converting this request into a remote-class refusal.
        response = await self.client.get(
            "/api/terminal/ar-api-1/conversation",
            params={"expectedBridgeEpoch": self.epoch},
            headers={"X-Forwarded-For": "8.8.8.8"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["status"], "authorization-failed")

    async def test_page_serves_native_identity_items_status_capabilities(self) -> None:
        await self._drive_codex_turn()
        page = await self._page()
        identity = page["identity"]
        self.assertEqual(identity["harnessId"], "codex")
        self.assertEqual(identity["vendorConversationId"], "thread-1")
        self.assertEqual(identity["bridgeEpoch"], self.epoch)
        self.assertTrue(identity["identityDigest"])
        ids = [item["itemId"] for item in page["items"]]
        self.assertEqual(ids[0], "turn-1-user")
        self.assertIn("turn-1-cmd", ids)
        kinds = {item["itemId"]: item["kind"] for item in page["items"]}
        self.assertEqual(kinds["turn-1-cmd"], "tool-call")
        self.assertEqual(kinds["turn-result:turn-1"], "turn-result")
        self.assertTrue(page["eventCursor"].startswith("ar-aec1."))
        self.assertTrue(page["hydrationId"])
        self.assertEqual(page["status"]["identity"]["arSessionId"], "ar-api-1")
        self.assertGreaterEqual(page["status"]["revision"], 1)
        self.assertEqual(page["capabilities"]["history"]["toolCompleteness"]["state"], "partial")
        self.assertIn("lossy", page["capabilities"]["history"]["toolCompleteness"]["reason"])
        # The stop/steer gating reads ONLY this L1 view: interrupt carries the landed L3 gate
        # verdict (supported, runtime-fixture); steer stays unavailable (no ordinary submit action).
        interrupt = page["capabilities"]["controls"]["interrupt"]
        self.assertEqual(interrupt["state"], "supported")
        self.assertEqual(interrupt["evidenceTier"], "runtime-fixture")
        self.assertEqual(interrupt["evidence"]["fixtureId"], "codex-0.144.5-installed-20260718")
        self.assertEqual(page["capabilities"]["controls"]["steer"]["state"], "unavailable")

    async def test_agent_history_route_targets_one_selected_child_without_replacing_parent(
        self,
    ) -> None:
        await self._page()
        response = await self.client.post(
            "/api/terminal/ar-api-1/conversation/agents/agent-1/history",
            params={"expectedBridgeEpoch": self.epoch},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"status": "not-eligible", "agentId": "agent-1"},
        )

    async def test_page_user_item_provenance_via_real_authority(self) -> None:
        await self._drive_codex_turn(client_id="req-page-1")
        page = await self._page()
        user = page["items"][0]
        self.assertEqual(user["lane"], "operator")
        self.assertEqual(user["source"], "cockpit-composer")
        self.assertEqual(user["provenance"]["strength"], "exact")
        self.assertEqual(user["provenance"]["producer"], "operator")

    async def test_epoch_mismatch_maps_409_with_expected_and_actual(self) -> None:
        response = await self.client.get(
            "/api/terminal/ar-api-1/conversation",
            params={"expectedBridgeEpoch": "wrong-epoch"},
        )
        self.assertEqual(response.status_code, 409)
        body = response.json()
        self.assertEqual(body["status"], "bridge-epoch-mismatch")
        self.assertEqual(body["expectedBridgeEpoch"], "wrong-epoch")
        self.assertEqual(body["actualBridgeEpoch"], self.epoch)

    async def test_unknown_session_404_and_unsupported_409(self) -> None:
        response = await self.client.get(
            "/api/terminal/ar-missing/conversation",
            params={"expectedBridgeEpoch": self.epoch},
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["status"], "unknown-session")
        self.harness.runtime.catalog.upsert(
            TerminalCatalogEntry(
                id="ar-raw",
                label="ar-raw",
                kind="terminal",
                harness=None,
                lifecycle_id=None,
                cwd=Path("/workspace"),
                tmux_name="tmux-ar-raw",
                command=("bash",),
                created_at=NOW,
                last_attached_at=NOW,
                status="running",
            )
        )
        response = await self.client.get(
            "/api/terminal/ar-raw/conversation",
            params={"expectedBridgeEpoch": self.epoch},
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["status"], "unsupported")

    # -- cursor authority ----------------------------------------------------

    async def test_before_paging_walks_back_with_minted_cursor(self) -> None:
        await self._drive_codex_turn("turn-1")
        await self._drive_codex_turn("turn-2")
        first = await self._page(limit=2)
        self.assertEqual(len(first["items"]), 2)
        self.assertTrue(first["page"]["hasOlder"])
        second = await self._page(before=first["page"]["olderCursor"], limit=2)
        first_ids = [item["itemId"] for item in first["items"]]
        second_ids = [item["itemId"] for item in second["items"]]
        self.assertFalse(set(first_ids) & set(second_ids))
        second_ordinals = [item["globalOrdinal"] for item in second["items"]]
        self.assertEqual(second_ordinals, sorted(second_ordinals))
        self.assertLess(second_ordinals[-1], first["items"][0]["globalOrdinal"])

    async def test_tampered_and_foreign_cursors_fail_typed(self) -> None:
        await self._drive_codex_turn()
        page = await self._page()
        cursor = page["eventCursor"]
        forged = cursor[:-4] + ("aaaa" if not cursor.endswith("aaaa") else "bbbb")
        response = await self.client.get(
            self._events_path(), params={"expectedBridgeEpoch": self.epoch, "after": forged}
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["status"], "cursor-invalid")
        service = active_conversation_service(self.harness.runtime)
        foreign_identity = ActiveConversationRef(
            harness_id="codex",
            vendor_conversation_id="thread-1",
            project_scope="/workspace",
            identity_digest="x",
            ar_session_id="ar-other",
            bridge_epoch=self.epoch,
        )
        foreign = mint_event_cursor(
            service.secret, OPERATOR, foreign_identity, generation="g", sequence=1
        )
        response = await self.client.get(
            self._events_path(), params={"expectedBridgeEpoch": self.epoch, "after": str(foreign)}
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["status"], "cursor-authorization")
        page_cursor = mint_page_cursor(service.secret, OPERATOR, foreign_identity, ordinal=1)
        response = await self.client.get(
            self._events_path(),
            params={"expectedBridgeEpoch": self.epoch, "after": str(page_cursor)},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["status"], "cursor-invalid")

    async def test_events_requires_cursor_and_rejects_dual_conflict(self) -> None:
        response = await self.client.get(
            self._events_path(), params={"expectedBridgeEpoch": self.epoch}
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["status"], "cursor-invalid")
        response = await self.client.get(
            self._events_path(),
            params={"expectedBridgeEpoch": self.epoch, "after": "ar-aec1.one"},
            headers={"Last-Event-ID": "ar-aec1.two"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["status"], "cursor-conflict")

    async def test_generation_mismatch_resets_typed(self) -> None:
        await self._drive_codex_turn()
        page = await self._page()
        service = active_conversation_service(self.harness.runtime)
        identity = ActiveConversationRef.model_validate(page["identity"])
        stale = mint_event_cursor(
            service.secret, OPERATOR, identity, generation="dead-generation", sequence=1
        )
        response = await self.client.get(
            self._events_path(), params={"expectedBridgeEpoch": self.epoch, "after": str(stale)}
        )
        self.assertEqual(response.status_code, 409)
        body = response.json()
        self.assertEqual(body["status"], "cursor-reset-required")
        self.assertEqual(body["reason"], "generation-changed")

    # -- SSE resume / live / gap ----------------------------------------------

    async def test_events_resume_replays_in_order_with_cursor_ids(self) -> None:
        await self._drive_codex_turn()
        page = await self._page()
        frames_text = ""
        async with self.client.stream(
            "GET",
            self._events_path(),
            params={"expectedBridgeEpoch": self.epoch, "after": page["eventCursor"]},
        ) as response:
            self.assertEqual(response.status_code, 200)
            await self._drive_codex_turn("turn-2")

            async def _collect() -> str:
                collected = ""
                async for chunk in response.aiter_text():
                    collected += chunk
                    if "turn-2-agent" in collected:
                        return collected
                return collected

            frames_text = await asyncio.wait_for(_collect(), timeout=20.0)
        frames = _sse_frames(frames_text)
        self.assertTrue(all(frame["id"].startswith("ar-aec1.") for frame in frames))
        self.assertTrue(all(frame["event"] == "conversation" for frame in frames))
        data = [json.loads(frame["data"]) for frame in frames]
        sequences = [envelope["sequence"] for envelope in data]
        self.assertEqual(sequences, sorted(sequences))
        deliveries = {envelope["delivery"] for envelope in data}
        self.assertEqual(deliveries, {"live"})

    async def test_events_replay_from_earlier_cursor_marks_resume_replay(self) -> None:
        await self._drive_codex_turn()
        page = await self._page()
        await self._drive_codex_turn("turn-2")
        await asyncio.sleep(2.5)
        frames_text = ""
        async with self.client.stream(
            "GET",
            self._events_path(),
            params={"expectedBridgeEpoch": self.epoch, "after": page["eventCursor"]},
            headers={"Last-Event-ID": page["eventCursor"]},
        ) as response:
            self.assertEqual(response.status_code, 200)

            async def _collect() -> str:
                collected = ""
                async for chunk in response.aiter_text():
                    collected += chunk
                    if "turn-2-agent" in collected:
                        return collected
                return collected

            frames_text = await asyncio.wait_for(_collect(), timeout=20.0)
        frames = _sse_frames(frames_text)
        deliveries = {json.loads(frame["data"])["delivery"] for frame in frames}
        self.assertEqual(deliveries, {"resume-replay"})

    async def test_established_stream_gaps_and_closes_on_epoch_flip(self) -> None:
        await self._drive_codex_turn()
        page = await self._page()
        frames_text = ""
        async with self.client.stream(
            "GET",
            self._events_path(),
            params={"expectedBridgeEpoch": self.epoch, "after": page["eventCursor"]},
        ) as response:
            self.assertEqual(response.status_code, 200)
            await self.harness.server.close()
            await self.harness.bridge.stop("forced")
            replacement_adapter = _FakeAdapter()
            replacement = HarnessControlBridge(
                self.harness.identity, replacement_adapter, clock=lambda: NOW
            )
            replacement_server = HarnessControlServer(self.harness.endpoint, replacement)
            launch = LaunchSpec(
                identity=self.harness.identity,
                harness_id="fake",
                cwd=Path("/workspace"),
                argv=("fake",),
                env={},
            )
            await replacement.start(launch)
            await replacement_server.start()

            async def _collect() -> str:
                collected = ""
                async for chunk in response.aiter_text():
                    collected += chunk
                    if '"op":"gap"' in collected:
                        return collected
                return collected

            frames_text = await asyncio.wait_for(_collect(), timeout=25.0)
            new_epoch = await asyncio.to_thread(
                read_submission_authority, self.harness.control_entry
            )
            self.harness._replacement = (replacement, replacement_server)
        self.assertIn('"op":"gap"', frames_text)
        gap = next(
            json.loads(frame["data"])
            for frame in _sse_frames(frames_text)
            if '"op":"gap"' in frame.get("data", "")
        )
        self.assertEqual(gap["mutation"]["reason"], "generation-changed")
        self.assertTrue(gap["mutation"]["requiresRepage"])
        self.assertTrue(gap["mutation"]["closeAfterEvent"])
        self.assertNotEqual(new_epoch.bridge_epoch, self.epoch)
        page2 = await self._page(epoch=new_epoch.bridge_epoch)
        self.assertEqual(page2["identity"]["bridgeEpoch"], new_epoch.bridge_epoch)
        refused = await self.client.get(
            "/api/terminal/ar-api-1/conversation",
            params={"expectedBridgeEpoch": self.epoch},
        )
        self.assertEqual(refused.status_code, 409)

    async def test_fresh_page_cursor_chains_the_first_live_frame(self) -> None:
        # A fresh page names event cursor 0 (the stream origin);
        # frame 1 must carry it as previousCursor, or the browser chain guard
        # (previousCursor !== null) re-pages on every fresh chat.
        page = await self._page()
        frames_text = ""
        async with self.client.stream(
            "GET",
            self._events_path(),
            params={"expectedBridgeEpoch": self.epoch, "after": page["eventCursor"]},
        ) as response:
            self.assertEqual(response.status_code, 200)
            await self._drive_codex_turn("turn-1")

            async def _collect() -> str:
                collected = ""
                async for chunk in response.aiter_text():
                    collected += chunk
                    if "turn-1-agent" in collected:
                        return collected
                return collected

            frames_text = await asyncio.wait_for(_collect(), timeout=20.0)
        data = [json.loads(frame["data"]) for frame in _sse_frames(frames_text)]
        self.assertEqual(data[0]["sequence"], 1)
        self.assertEqual(data[0]["previousCursor"], page["eventCursor"])
        for earlier, later in pairwise(data):
            self.assertEqual(later["previousCursor"], earlier["cursor"])

    async def test_caught_up_stream_flushes_headers_via_priming_comment(self) -> None:
        # The GZipMiddleware responder holds http.response.start
        # until the first body chunk even for the gzip-excluded SSE media type, so a
        # caught-up subscription used to sit headerless until the next mutation (the
        # constant ~14s "connecting…" on an idle fresh chat). One SSE comment line
        # primes the stream: headers flush at connect and EventSource ignores the
        # comment natively. Raw ASGI, same reason as GzipSseFlowTests: an infinite
        # stream cannot go through a response-collecting client.
        page = await self._page()
        query = urlencode({"expectedBridgeEpoch": self.epoch, "after": page["eventCursor"]})
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": self._events_path(),
            "raw_path": self._events_path().encode(),
            "query_string": query.encode(),
            "root_path": "",
            "headers": [(b"host", b"testserver"), (b"accept-encoding", b"gzip")],
            "client": ("127.0.0.1", 12345),
            "server": ("127.0.0.1", 80),
        }
        messages: list[MutableMapping[str, Any]] = []
        started = asyncio.Event()
        domain_frame = asyncio.Event()
        requested = False

        async def receive() -> dict[str, object]:
            nonlocal requested
            if not requested:
                requested = True
                return {"type": "http.request", "body": b"", "more_body": False}
            await asyncio.Event().wait()  # no further client input; ends with the task
            raise AssertionError("unreachable")

        async def send(message: MutableMapping[str, Any]) -> None:
            messages.append(message)
            if message["type"] == "http.response.start":
                started.set()
            elif message["type"] == "http.response.body" and b"event: conversation" in message.get(
                "body", b""
            ):
                domain_frame.set()

        task = asyncio.create_task(self.harness.app(scope, receive, send))
        try:
            # Without the priming comment this wait expires: no body chunk exists
            # until the next mutation, so the middleware never releases the headers.
            await asyncio.wait_for(started.wait(), timeout=2)
            bodies = [
                message.get("body", b"")
                for message in messages
                if message["type"] == "http.response.body"
            ]
            # The first (and so far only) chunk is the comment: no event field, no data.
            self.assertEqual(bodies, [b": connected\n\n"])
            self.adapter.emit(
                "codex-notification",
                {
                    "codexMethod": "item/completed",
                    AR_EVIDENCE_KEY: _codex_params(
                        {"id": "prime-agent", "type": "agentMessage", "text": "after the comment"},
                        turn="turn-prime",
                    ),
                },
            )
            # A real domain frame still flows on the same stream afterwards.
            await asyncio.wait_for(domain_frame.wait(), timeout=15)
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        start = next(message for message in messages if message["type"] == "http.response.start")
        headers = {k.decode().lower(): v.decode() for k, v in start["headers"]}
        self.assertEqual(start["status"], 200)
        self.assertTrue(headers["content-type"].startswith("text/event-stream"))
        self.assertNotIn("content-encoding", headers)  # SSE stays gzip-excluded
        first_domain = next(
            index
            for index, message in enumerate(messages)
            if message["type"] == "http.response.body"
            and b"event: conversation" in message.get("body", b"")
        )
        self.assertLess(messages.index(start), first_domain)

    async def test_orchestration_parity_with_canonical_status(self) -> None:
        await self._drive_codex_turn()
        page = await self._page()
        snapshot = self.adapter.current
        assert snapshot is not None
        classification = classify_snapshot(snapshot, "codex")
        expected = seat_turn_state_for(
            classification.process,
            CANONICAL_TURN_STATE_BY_EVIDENCE[classification.turn.evidence]
            if classification.turn is not None
            else None,
        )
        self.assertEqual(snapshot_turn_state(snapshot), expected)
        self.assertIn(page["status"]["turn"]["state"], {"ready", "working", "waiting", "settling"})

    def test_no_pty_runner_log_or_fixture_production_authority(self) -> None:
        root = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "agents_remember"
            / "serving"
            / "conversation"
        )
        forbidden = (
            "PtySurface",
            "tmux_pane",
            "capture_pane",
            "runner log",
            "runner-log",
            "tests/fixtures",
            "FIXTURE_ROOT",
            "fixtures/conversation_runtime",
        )
        hosted = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "agents_remember"
            / "serving"
            / "hosted_control_projection.py"
        )
        for path in [*root.rglob("*.py"), hosted]:
            source = path.read_text(encoding="utf-8")
            for token in forbidden:
                self.assertNotIn(token, source, f"{path.name} references {token}")
            self.assertNotIn("harness_terminal_surface", source)


class PiProductionRouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="ar-active-api-pi-")
        self.root = Path(self._tmp.name)
        frames = [
            NativeEvidenceFrame(
                native_id="entry-1",
                native_parent_id=None,
                native_type="message",
                created_at=NOW,
                raw={
                    "id": "entry-1",
                    "type": "message",
                    "message": {"role": "user", "content": "hello", "timestamp": 1},
                },
            ),
            NativeEvidenceFrame(
                native_id="entry-2",
                native_parent_id="entry-1",
                native_type="message",
                created_at=NOW,
                raw={
                    "id": "entry-2",
                    "type": "message",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": "hi"},
                            {
                                "type": "toolCall",
                                "id": "tc-1",
                                "name": "bash",
                                "arguments": {"command": "ls"},
                            },
                        ],
                        "stopReason": "toolUse",
                        "timestamp": 2,
                    },
                },
            ),
        ]
        self.adapter = _NativePageAdapter(frames, vendor_id="pi-session-1")
        self.harness = _Harness(self.root, self.adapter, "ar-pi-1", harness="pi")
        await self.harness.start()
        self.epoch = self.harness.epoch
        self.client = self.harness.client

    async def asyncTearDown(self) -> None:
        await self.harness.stop()
        self._tmp.cleanup()

    async def test_pi_native_hydration_tools_and_capabilities(self) -> None:
        self.adapter.emit(
            "pi:tool_execution_end",
            {
                "piEvent": {"type": "tool_execution_end"},
                AR_EVIDENCE_KEY: {
                    "type": "tool_execution_end",
                    "toolCallId": "tc-1",
                    "toolName": "bash",
                    "result": {"content": [{"type": "text", "text": "done"}]},
                    "isError": False,
                },
            },
        )
        response = await self.client.get(
            "/api/terminal/ar-pi-1/conversation",
            params={"expectedBridgeEpoch": self.epoch},
        )
        assert response.status_code == 200, response.text
        page = response.json()
        self.assertEqual(page["identity"]["harnessId"], "pi")
        self.assertEqual(page["identity"]["vendorConversationId"], "pi-session-1")
        ids = [item["itemId"] for item in page["items"]]
        self.assertEqual(ids[:2], ["entry-1", "entry-2"])
        self.assertIn("tc-1", ids)
        user = page["items"][0]
        self.assertEqual(user["lane"], "unknown-input")
        self.assertEqual(user["provenance"]["strength"], "native-only")
        tool = next(item for item in page["items"] if item["itemId"] == "tc-1")
        self.assertEqual(tool["kind"], "tool-call")
        self.assertEqual(tool["phase"], "completed")
        self.assertEqual(page["capabilities"]["history"]["read"]["state"], "supported")
        interrupt = page["capabilities"]["controls"]["interrupt"]
        self.assertEqual(interrupt["state"], "supported")
        self.assertEqual(interrupt["evidenceTier"], "runtime-fixture")
        self.assertEqual(interrupt["evidence"]["fixtureId"], "pi-0.80.7-installed-20260718")
        self.assertEqual(page["page"]["totalItems"], 3)


if __name__ == "__main__":
    unittest.main()
