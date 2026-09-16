#!/usr/bin/env python3
"""Live native fixture: the real eve runtime, driven by the real AR eve adapter.

Unlike the unit conformance suites, nothing here is doubled. This script starts the AR-owned eve
application as a child process, points it at a deterministic local model provider, and then drives
:class:`EveSessionAdapter` through eve's production HTTP transport — health, session create,
follow-up, structured input response, turn cancel, the durable NDJSON stream, cursor reconnect,
reconciliation and a bridge restart.

Requirements, checked before anything starts:

* Node.js >= 24, either on ``PATH`` or in an nvm install under ``$HOME/.nvm``, or named by
  ``AR_EVE_NODE``; eve 0.56.0 refuses to start below it;
* ``eve_runtime/node_modules`` installed (``cd eve_runtime && npm install``).

Run it from the repository root::

    mcp/.venv/bin/python mcp/tests/live_eve_native_fixture.py --report-dir <dir>
    mcp/.venv/bin/python mcp/tests/live_eve_native_fixture.py --report-dir <dir> --real-model

It writes one JSON artifact per scenario plus a transcript of every native event it observed, and
exits non-zero when a scenario's own assertion fails. A run that cannot start reports that as a
blocked scenario with the exact reason rather than a pass. ``--real-model`` instead attempts one
bounded native turn per hosted provider in :data:`REAL_PROVIDER_ATTEMPTS` and exits ``3`` when none
completed, recording each provider's own refusal.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import subprocess
import sys
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "mcp" / "src"))
sys.path.insert(0, str(REPO_ROOT / "mcp" / "tests"))

from agents_remember.errors import (
    HarnessAdapterDisconnectedError,
    HarnessControlError,
)
from agents_remember.models.conversations.control_wire import (
    ControlIdentity,
    ControlOperationRef,
    LaunchSpec,
    SubmissionReceipt,
)
from agents_remember.serving.eve_adapter import (
    EveAdapterLimits,
    EveSessionAdapter,
)
from agents_remember.serving.eve_protocol import EveRuntimeLaunch
from agents_remember.serving.eve_runtime_client import EveRuntimeProcess
from agents_remember.serving.eve_runtime_launch import (
    EFFORT_ENV,
    MODEL_ENV,
    NODE_EXECUTABLE_ENV,
    PROVIDER_API_KEY_ENV,
    PROVIDER_BASE_URL_ENV,
    RUNTIME_ROOT_ENV,
    STATE_ROOT_ENV,
    WORKSPACE_ROOT_ENV,
    resolve_node_executable,
)
from agents_remember.serving.harness_control_models import (
    AdapterEvent,
    PromptRequest,
)
from eve_fixture_model import serve

FIXTURE_MODEL = "fixture-deterministic-1"
START_FAILURES = (
    HarnessControlError,
    TimeoutError,
    OSError,
    subprocess.SubprocessError,
    RuntimeError,
)
"""Everything a start attempt can raise, so no start failure exits without an artifact.

``OSError`` covers the operator-facing ``AR_EVE_NODE`` naming a path that does not exist and a
runtime entrypoint that is missing or not executable — the shapes that reach
``asyncio.create_subprocess_exec``. ``RuntimeError``/``SubprocessError`` cover the event-loop and
subprocess failures around them. The fixture's contract is that a run which cannot start reports a
``blocked`` scenario with the exact reason, and a raw traceback with no artifact breaks it.
"""
SCENARIO_TIMEOUT_SECONDS = 180.0
START_TIMEOUT_SECONDS = 240.0


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class TracingEveRuntime(EveRuntimeProcess):
    """The production transport, recording its native stream connections and request bodies.

    The stream lines let an operator see the reconnect cadence the bounded read produces. The
    request record is the wire proof the unit suites cannot give: it captures what the production
    client actually put on the socket, so a follow-up's turn policy and a cancel's turn id are
    asserted from the real request rather than from a fixture's own bookkeeping.
    """

    def __init__(
        self,
        launch: EveRuntimeLaunch,
        *,
        recorder: list[dict[str, Any]],
        health_timeout_seconds: float,
    ) -> None:
        super().__init__(
            launch,
            health_timeout_seconds=health_timeout_seconds,
            client_factory=lambda: self._recording_client(recorder),
        )

    def _recording_client(self, recorder: list[dict[str, Any]]) -> httpx.AsyncClient:
        async def record(request: httpx.Request) -> None:
            raw = request.content.decode("utf-8")
            recorder.append(
                {
                    "method": request.method,
                    "path": request.url.path,
                    "query": request.url.query.decode("utf-8"),
                    "body": json.loads(raw) if raw else {},
                }
            )

        return httpx.AsyncClient(
            base_url=self.endpoint,
            timeout=httpx.Timeout(60.0),
            event_hooks={"request": [record]},
        )

    async def stream(self, session_id: str, *, start_index: int):
        print(f"[stream] open {session_id} from index {start_index}", flush=True)
        count = 0
        async for event in super().stream(session_id, start_index=start_index):
            count += 1
            yield event
        print(f"[stream] closed after {count} events", flush=True)


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class ScenarioResult:
    name: str
    status: str
    detail: str
    observations: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "scenario": self.name,
            "status": self.status,
            "detail": self.detail,
            "observations": self.observations,
        }


class LiveFixture:
    """One live eve runtime plus the AR adapter that owns it."""

    def __init__(self, report_dir: Path, plan: list[dict[str, Any]]) -> None:
        self.report_dir = report_dir
        self.report_dir.mkdir(parents=True, exist_ok=True)
        self.plan = plan
        self.workspace = report_dir / "workspace"
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.model_port = _free_port()
        self.model_server = None
        self.model_state = report_dir / "model-state.json"
        self.model_trace = report_dir / "model-trace.ndjson"
        self.adapter: EveSessionAdapter | None = None
        self.launch: LaunchSpec | None = None
        self.native_events: list[dict[str, Any]] = []
        self.notes: list[str] = []
        self.node: str | None = None
        self.wire_requests: list[dict[str, Any]] = []
        """Every HTTP request the production client sent, in order, with its parsed body."""

    def wire_posts(self, path_suffix: str) -> list[dict[str, Any]]:
        """Recorded POST requests whose path ends with ``path_suffix``, in send order."""

        return [
            request
            for request in self.wire_requests
            if request["method"] == "POST" and request["path"].endswith(path_suffix)
        ]

    def durable_frame(self, message_text: str) -> dict[str, Any] | None:
        """The raw ``message.received`` frame the stream carried for one accepted message.

        Retained as vendor detail by the mapper, so this reads the real durable envelope -- and its
        ``meta.deliveryIds``, when eve sends one -- without a second protocol implementation.
        """

        for event in self.native_events:
            frame = event.get("raw", {}).get("eveEvent")
            if not isinstance(frame, dict) or frame.get("type") != "message.received":
                continue
            if frame.get("data", {}).get("message") == message_text:
                return frame
        return None

    def resolve_node(self) -> str:
        """Resolve the interpreter once, at the moment the runtime is really started."""

        if self.node is None:
            self.node = resolve_node_executable(dict(os.environ))
            self.notes.append(f"node: {self.node}")
        return self.node

    # -- lifecycle ------------------------------------------------------------------------

    def start_model(self) -> None:
        self.model_server = serve(
            plan=self.plan,
            state_path=self.model_state,
            port=self.model_port,
            trace_path=self.model_trace,
        )
        threading.Thread(target=self.model_server.serve_forever, daemon=True).start()
        self.notes.append(f"model fixture on http://127.0.0.1:{self.model_port}/v1")

    def stop_model(self) -> None:
        if self.model_server is not None:
            self.model_server.shutdown()
            self.model_server.server_close()
            self.model_server = None

    def _launch_spec(self, *, runtime_root: Path, epoch_root: Path | None = None) -> LaunchSpec:
        return LaunchSpec(
            identity=ControlIdentity(
                ar_session_id="live-eve-fixture",
                tmux_name="live-eve-fixture",
                created_at=_now(),
            ),
            harness_id="eve",
            cwd=self.workspace,
            argv=("eve",),
            env={
                MODEL_ENV: FIXTURE_MODEL,
                EFFORT_ENV: "provider-default",
                PROVIDER_BASE_URL_ENV: f"http://127.0.0.1:{self.model_port}/v1",
                PROVIDER_API_KEY_ENV: "live-fixture-key",
                WORKSPACE_ROOT_ENV: str(self.workspace),
                RUNTIME_ROOT_ENV: str(runtime_root),
                NODE_EXECUTABLE_ENV: self.resolve_node(),
                # One staged application directory per epoch: eve keeps one development server per
                # application root, so the restarted bridge and the second concurrent session each
                # need their own.
                STATE_ROOT_ENV: str(epoch_root),
            },
        )

    async def start_adapter(
        self, *, runtime_root: Path, epoch: str = "epoch-1"
    ) -> EveSessionAdapter:
        adapter = EveSessionAdapter(
            runtime_factory=lambda spec: TracingEveRuntime(
                spec.launch,
                recorder=self.wire_requests,
                health_timeout_seconds=START_TIMEOUT_SECONDS,
            ),
            limits=EveAdapterLimits(health_timeout_seconds=START_TIMEOUT_SECONDS),
            clock=_now,
        )
        self.adapter = adapter
        self.launch = self._launch_spec(
            runtime_root=runtime_root, epoch_root=self.report_dir / "epochs" / epoch
        )
        await asyncio.wait_for(adapter.start(self.launch), timeout=START_TIMEOUT_SECONDS)
        return adapter

    async def submit(self, request_id: str, text: str, *, sequence: int = 1) -> SubmissionReceipt:
        assert self.adapter is not None
        operation = ControlOperationRef(
            bridge_epoch="live-epoch",
            sequence=sequence,
            operation_id=request_id,
            kind="prompt",
        )
        await self.adapter.preflight_operation(operation)
        return await self.adapter.submit(
            PromptRequest(
                request_id=request_id,
                source="cockpit",
                text=text,
                submitted_at=_now(),
                operation=operation,
            )
        )

    async def pump(
        self, *, until: str | None = None, timeout: float = SCENARIO_TIMEOUT_SECONDS
    ) -> list[AdapterEvent]:
        """Read translated events under one standing subscriber."""

        assert self.adapter is not None
        adapter = self.adapter
        collected: list[AdapterEvent] = []

        async def consume() -> None:
            async for event in adapter.subscribe():
                collected.append(event)
                self.native_events.append(
                    {
                        "sequence": event.sequence,
                        "kind": event.kind,
                        "raw": _jsonable(event.raw),
                        "transcript": [
                            {
                                "sequence": entry.sequence,
                                "role": entry.role,
                                "text": entry.text,
                                "vendorCorrelationId": entry.vendor_correlation_id,
                            }
                            for entry in event.transcript
                        ],
                        "operation": event.operation.operation_id if event.operation else None,
                    }
                )
                if until is not None and event.kind == until:
                    return
                if until is None and event.kind in {"completed", "failed", "cancelled"}:
                    return
                if len(collected) >= 400:
                    return

        task = asyncio.ensure_future(consume())
        try:
            await asyncio.wait_for(task, timeout=timeout)
        except TimeoutError:
            task.cancel()
        return collected

    async def aclose(self) -> None:
        if self.adapter is not None:
            await self.adapter.stop("graceful")
            self.adapter = None

    def write(self, name: str, payload: dict[str, Any]) -> Path:
        path = self.report_dir / name
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return path


def _raw_payload(raw: Mapping[str, object], key: str) -> Mapping[str, object]:
    """The nested object payload under one ``raw`` key, or an empty mapping when it is absent.

    ``AdapterEvent.raw`` is ``Mapping[str, object]`` by contract, so one level of it is typed and
    the next is not. Reading the nested level through this function keeps the fixture honest about
    that: a missing key reads as empty instead of raising, which is what the assertions below want.
    """

    value = raw.get(key)
    return value if isinstance(value, Mapping) else {}


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


REAL_PROVIDER_ATTEMPTS: tuple[dict[str, str], ...] = (
    {
        "label": "vercel-ai-gateway",
        "base_url": "https://ai-gateway.vercel.sh/v1",
        "model": "openai/gpt-5.6-luna-fast",
        "key_env": "AI_GATEWAY_API_KEY",
    },
    {
        "label": "direct-openai",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-5.6-luna-fast",
        "key_env": "OPENAI_API_KEY",
    },
    {
        "label": "direct-anthropic-openai-shim",
        "base_url": "https://api.anthropic.com/v1",
        "model": "claude-sonnet-4-5",
        "key_env": "ANTHROPIC_API_KEY",
    },
)


async def run_real_provider_probe(report_dir: Path, runtime_root: Path) -> int:
    """Attempt exactly one bounded run against a real hosted model provider.

    The result is evidence either way. A refusal is recorded with the provider's own error text and
    the missing credential named; nothing here fabricates a hosted result, and nothing here turns a
    refusal into a pass.
    """

    report_dir.mkdir(parents=True, exist_ok=True)
    attempts: list[dict[str, Any]] = []
    for candidate in REAL_PROVIDER_ATTEMPTS:
        credential = os.environ.get(candidate["key_env"])
        # The runtime is started and driven either way: without a credential the provider's own
        # refusal is the evidence, which is a stronger record than "the variable was absent".
        attempt = await _run_real_attempt(
            report_dir=report_dir,
            runtime_root=runtime_root,
            candidate=candidate,
            credential=credential,
            credential_present=bool(credential),
        )
        attempts.append(attempt)
        if attempt["outcome"] == "accepted":
            break
    payload = {
        "scenario": "real-hosted-model",
        "attempts": attempts,
        "conclusion": (
            "at least one hosted provider accepted a native eve turn"
            if any(item["outcome"] == "accepted" for item in attempts)
            else (
                "no hosted model provider completed a native eve turn in this environment; every "
                "attempt is recorded above with the provider's own refusal"
            )
        ),
    }
    (report_dir / "scenario-real-hosted-model.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(payload, indent=2), flush=True)
    return 0 if any(item["outcome"] == "accepted" for item in attempts) else 3


async def _run_real_attempt(
    *,
    report_dir: Path,
    runtime_root: Path,
    candidate: dict[str, str],
    credential: str | None,
    credential_present: bool,
) -> dict[str, Any]:
    """One bounded native turn against a real provider, with the provider's own answer recorded."""

    workspace = report_dir / "workspace-real-model"
    workspace.mkdir(parents=True, exist_ok=True)
    port = _free_port()
    launch = LaunchSpec(
        identity=ControlIdentity(
            ar_session_id="live-eve-real-model",
            tmux_name="live-eve-real-model",
            created_at=_now(),
        ),
        harness_id="eve",
        cwd=workspace,
        argv=("eve",),
        env={
            MODEL_ENV: candidate["model"],
            EFFORT_ENV: "low",
            PROVIDER_BASE_URL_ENV: candidate["base_url"],
            PROVIDER_API_KEY_ENV: credential or "no-credential-in-this-environment",
            WORKSPACE_ROOT_ENV: str(workspace),
            RUNTIME_ROOT_ENV: str(runtime_root),
            NODE_EXECUTABLE_ENV: resolve_node_executable(dict(os.environ)),
            STATE_ROOT_ENV: str(report_dir / "epochs" / "epoch-real-model"),
        },
    )
    adapter = EveSessionAdapter(
        limits=EveAdapterLimits(health_timeout_seconds=START_TIMEOUT_SECONDS), clock=_now
    )
    result: dict[str, Any] = {
        "provider": candidate["label"],
        "baseUrl": candidate["base_url"],
        "model": candidate["model"],
        "credentialEnv": candidate["key_env"],
        "credentialPresent": credential_present,
        "runtimePort": port,
    }
    try:
        await asyncio.wait_for(adapter.start(launch), timeout=START_TIMEOUT_SECONDS)
        operation = ControlOperationRef(
            bridge_epoch="real-epoch", sequence=1, operation_id="real-1", kind="prompt"
        )
        await adapter.preflight_operation(operation)
        receipt = await adapter.submit(
            PromptRequest(
                request_id="real-1",
                source="cockpit",
                text="Reply with exactly: native eve real model ok",
                submitted_at=_now(),
                operation=operation,
            )
        )
        result["receiptAcceptance"] = receipt.acceptance
        events: list[AdapterEvent] = []

        async def consume() -> None:
            async for event in adapter.subscribe():
                events.append(event)
                if event.kind in {"completed", "failed", "cancelled"}:
                    return

        await asyncio.wait_for(consume(), timeout=SCENARIO_TIMEOUT_SECONDS)
        transcript = [entry.text for event in events for entry in event.transcript]
        outcome = _raw_payload(events[-1].raw, "terminalResult").get("outcome") if events else None
        result.update(
            {
                "sessionId": (await adapter.snapshot()).vendor_session_id,
                "transcript": transcript,
                "terminalOutcome": outcome,
                "eventKinds": [event.kind for event in events],
            }
        )
        if outcome == "completed" and any(
            "native eve real model ok" in text for text in transcript
        ):
            result["outcome"] = "accepted"
            result["detail"] = "the hosted provider completed a native eve turn"
        else:
            result["outcome"] = "refused"
            result["detail"] = "; ".join(transcript[-2:]) or "no terminal evidence was produced"
    except (HarnessControlError, TimeoutError) as exc:
        result["outcome"] = "refused"
        result["detail"] = f"{type(exc).__name__}: {exc}"
    finally:
        await adapter.stop("forced")
    return result


def _plan() -> list[dict[str, Any]]:
    """The deterministic provider script the whole fixture shares, one entry per model call.

    Entry 1 asks for the runtime's own workspace tool, so the round-trip below proves the agent
    loop ran inside the admitted worktree rather than merely that a model answered. Entry 4 holds
    its response open, which is what gives the cancellation scenario a turn to cancel.
    """

    return [
        # 1. accepted submit -> deltas -> tool request -> response -> turn boundary
        {
            "toolCalls": [
                {
                    "callId": "call_fixture_1",
                    "name": "ar_workspace_write",
                    "arguments": {"path": "note.txt", "text": "written-by-eve-fixture"},
                }
            ]
        },
        {"text": "note.txt written"},
        # 2/3. reconnect and reconciliation
        {"text": "second turn complete"},
        {"text": "third turn complete"},
        # 4. cancel: held open so the fixture can cancel the exact observed turn
        {"text": "cancelled turn text", "delaySeconds": 30},
        {"text": "after-cancel turn complete"},
        # 5. restart
        {"text": "restart turn complete"},
        # 6. two concurrent sessions
        {"text": "session a complete"},
        {"text": "session b complete"},
        {"text": "session a follow-up complete"},
        {"text": "session b follow-up complete"},
        {"text": "spare one"},
        {"text": "spare two"},
    ]


async def _scenario_protocol(fixture: LiveFixture) -> ScenarioResult:
    """Accepted submit -> deltas -> tool request -> response -> turn boundary."""

    receipt = await fixture.submit("live-req-1", "run the fixture tool round-trip")
    events = await fixture.pump(until="completed")
    kinds = [event.kind for event in events]
    transcript = [entry for event in events for entry in event.transcript]
    note = fixture.workspace / "note.txt"
    observations = {
        "receipt": {
            "acceptance": receipt.acceptance,
            "sessionId": receipt.raw.get("sessionId"),
            "turnPolicy": receipt.raw.get("turnPolicy"),
        },
        "eventKinds": kinds,
        "deltaCount": kinds.count("delta"),
        "toolRequests": [entry.text for entry in transcript if entry.role == "interaction"],
        "toolResults": [entry.text for entry in transcript if entry.role == "result"],
        "finalMessage": [entry.text for entry in transcript if entry.role == "assistant"],
        "workspaceFileWritten": note.is_file(),
        "workspaceFileContent": note.read_text(encoding="utf-8") if note.is_file() else None,
        "terminal": events[-1].raw.get("terminalResult") if events else None,
    }
    problems: list[str] = []
    if receipt.acceptance != "immediate":
        problems.append(f"acceptance was {receipt.acceptance!r}")
    if receipt.raw.get("turnPolicy") != "queue":
        problems.append("the delivery did not select the queued turn policy")
    if "delta" not in kinds:
        problems.append("no message delta was translated")
    if not any(entry.role == "interaction" for entry in transcript):
        problems.append("no tool request was translated")
    if not any(entry.role == "result" for entry in transcript):
        problems.append("no tool result was translated")
    if not note.is_file() or note.read_text(encoding="utf-8") != "written-by-eve-fixture":
        problems.append("the runtime's workspace write did not land in the admitted worktree")
    creates = fixture.wire_posts("/eve/v1/session")
    observations["createRequestBodies"] = [request["body"] for request in creates]
    if not any(request["body"].get("turnPolicy") == "queue" for request in creates):
        problems.append(
            "the create request did not spell the queued turn policy on the wire: "
            f"{[request['body'] for request in creates]}"
        )
    if kinds[-1:] != ["completed"]:
        problems.append(f"the turn did not end on a boundary: {kinds[-3:]}")
    return ScenarioResult(
        "protocol-round-trip",
        "failed" if problems else "passed",
        "; ".join(problems) or "accepted submit, streamed deltas, tool round-trip, turn boundary",
        observations,
    )


async def _scenario_reconnect(fixture: LiveFixture) -> ScenarioResult:
    """Drop the stream, reconnect from the cursor, duplicate nothing."""

    assert fixture.adapter is not None
    before = await fixture.adapter.snapshot()
    cursor = before.raw.get("streamCursor")
    session_id = before.vendor_session_id
    # A fresh subscriber opens a new connection from the persisted absolute index.
    events = await fixture.pump(until=None, timeout=5.0)
    after = await fixture.adapter.snapshot()
    observations = {
        "sessionId": session_id,
        "cursorBeforeReconnect": cursor,
        "cursorAfterReconnect": after.raw.get("streamCursor"),
        "eventsOnReconnect": [event.kind for event in events],
        "transcriptOnReconnect": [entry.text for event in events for entry in event.transcript],
        "durableSessionStillLive": (await fixture.adapter.snapshot()).control,
    }
    problems: list[str] = []
    if observations["transcriptOnReconnect"]:
        problems.append("a reconnect replayed transcript content that was already delivered")
    if observations["eventsOnReconnect"] and any(
        kind in {"completed", "failed", "cancelled"} for kind in observations["eventsOnReconnect"]
    ):
        problems.append("a reconnect re-delivered a terminal notification")
    # The session must still accept work after the reconnect.
    await fixture.submit("live-req-reconnect", "second turn after reconnect", sequence=2)
    follow = await fixture.pump(until="completed")
    observations["afterReconnectTerminal"] = (
        follow[-1].raw.get("terminalResult") if follow else None
    )
    if not follow:
        problems.append("the session did not accept work after the reconnect")
    # The follow-up is the one delivery eve would otherwise steer, so its policy is asserted from
    # the request that actually crossed the wire, addressed to the durable id.
    follow_ups = fixture.wire_posts(f"/eve/v1/session/{session_id}")
    observations["followUpRequestBodies"] = [request["body"] for request in follow_ups]
    if not any(request["body"].get("turnPolicy") == "queue" for request in follow_ups):
        problems.append(
            "the follow-up request did not spell the queued turn policy on the wire: "
            f"{[request['body'] for request in follow_ups]}"
        )
    return ScenarioResult(
        "reconnect-from-cursor",
        "failed" if problems else "passed",
        "; ".join(problems) or "reconnect from the absolute cursor duplicated nothing",
        observations,
    )


async def _scenario_reconcile(fixture: LiveFixture) -> ScenarioResult:
    """A lost submit response reconciles as accepted/rejected/unresolved with no second write."""

    assert fixture.adapter is not None
    submission_calls: list[str] = []
    real_send = fixture.adapter._require_runtime().send_message

    async def counting_send(session_id: str, message: str):
        submission_calls.append(message)
        result = await real_send(session_id, message)
        # Simulate a response lost after the durable write landed.
        if len(submission_calls) == 1:
            raise HarnessAdapterDisconnectedError(
                "simulated lost response after the durable write", may_have_sent=True
            )
        return result

    fixture.adapter._require_runtime().send_message = counting_send  # type: ignore[method-assign]
    request_id = "live-req-lost"
    operation = ControlOperationRef(
        bridge_epoch="live-epoch", sequence=3, operation_id=request_id, kind="prompt"
    )
    await fixture.adapter.preflight_operation(operation)
    lost = False
    try:
        await fixture.adapter.submit(
            PromptRequest(
                request_id=request_id,
                source="cockpit",
                text="third turn complete",
                submitted_at=_now(),
                operation=operation,
            )
        )
    except HarnessAdapterDisconnectedError:
        lost = True
    result = await fixture.adapter.reconcile(request_id)
    await fixture.pump(until="completed")
    # Which proof actually resolved it matters: the delivery id is the stronger evidence and is
    # preferred when the response carried one, so the frame's own envelope is recorded here rather
    # than assuming either path.
    frame = fixture.durable_frame("third turn complete")
    delivery_ids = list((frame or {}).get("meta", {}).get("deliveryIds") or [])
    observations = {
        "responseWasLost": lost,
        "submissionCalls": len(submission_calls),
        "reconciledState": result.state,
        "reconciledDetail": result.detail,
        "requestIds": [result.request_id],
        # What the record proves, and what the adapter could know. The durable frames *do* carry
        # delivery ids, but the lost response never delivered this request's own id to the adapter,
        # so acceptance can only have been proved by the exact accepted message.
        "durableFrameCarriesDeliveryIds": bool(delivery_ids),
        "durableFrameDeliveryIds": delivery_ids,
        "durableFrame": frame,
        "adapterHeldDeliveryId": not lost,
    }
    problems = _reconcile_problems(observations, result, frame=frame, lost=lost)
    return ScenarioResult(
        "lost-response-reconcile",
        "failed" if problems else "passed",
        "; ".join(problems) or f"reconciled {result.state} with no second write",
        observations,
    )


def _reconcile_problems(
    observations: dict[str, Any],
    result: Any,
    *,
    frame: dict[str, Any] | None,
    lost: bool,
) -> list[str]:
    """What a dishonest or repeated reconciliation would look like in this scenario's evidence."""

    problems: list[str] = []
    if not lost:
        problems.append("the fixture never produced a lost response")
    if result.state not in {"accepted", "rejected", "unresolved"}:
        problems.append(f"reconciliation answered {result.state!r}")
    if observations["submissionCalls"] != 1:
        problems.append("reconciliation repeated a possibly accepted write")
    if frame is None:
        problems.append(
            "the durable record carried no message.received frame for the lost request, so the "
            "acceptance claim has no recorded evidence"
        )
    if result.state == "accepted":
        detail = result.detail or ""
        # The adapter must describe the proof it had, and it cannot have had a delivery id here.
        if "delivery id" in detail:
            problems.append(
                "reconciliation claimed a delivery-id proof for a request whose response was lost"
            )
        if "holds this exact message verbatim" not in detail:
            problems.append(f"reconciliation did not name the durable-record proof: {detail!r}")
    return problems


async def _scenario_cancel(fixture: LiveFixture) -> ScenarioResult:
    """Cancel an exact observed turn, then send another message on the same live session."""

    assert fixture.adapter is not None
    before = await fixture.adapter.snapshot()
    session_id = before.vendor_session_id
    await fixture.submit("live-req-cancel", "held open for cancellation", sequence=4)

    observed = await _await_turn(fixture)
    if observed is None:
        return ScenarioResult(
            "cancel-exact-turn",
            "failed",
            "no turn identity was observed within the bound",
            {"sessionId": session_id},
        )

    acknowledgement = await fixture.adapter.interrupt(
        turn_id=observed, expected_operation_id="live-req-cancel"
    )
    settled = await fixture.pump(until="completed")
    outcomes = [
        _raw_payload(event.raw, "terminalResult").get("outcome")
        for event in settled
        if "terminalResult" in event.raw
    ]
    same_session = (await fixture.adapter.snapshot()).vendor_session_id
    await fixture.submit("live-req-after-cancel", "after-cancel turn complete", sequence=5)
    follow = await fixture.pump(until="completed")
    cancels = fixture.wire_posts(f"/eve/v1/session/{session_id}/cancel")
    observations = {
        "sessionIdBefore": session_id,
        "cancelledTurnId": observed,
        "acknowledgement": acknowledgement.acknowledgement,
        "acknowledgementDetail": acknowledgement.detail,
        "settledOutcomes": outcomes,
        "sessionIdAfterCancel": same_session,
        "afterCancelTerminal": follow[-1].raw.get("terminalResult") if follow else None,
        "cancelRequestBodies": [request["body"] for request in cancels],
    }
    problems: list[str] = []
    if acknowledgement.acknowledgement != "accepted":
        problems.append(f"cancel was {acknowledgement.acknowledgement!r}")
    if not any(request["body"].get("turnId") == observed for request in cancels):
        problems.append(
            "no cancel request carried the observed turn id on the wire: "
            f"{[request['body'] for request in cancels]}"
        )
    if "cancelled" not in outcomes:
        problems.append("the stream never carried a native cancelled boundary")
    if same_session != session_id:
        problems.append("the session identity changed across the cancel")
    if not follow:
        problems.append("the live session refused the next message after cancellation")
    return ScenarioResult(
        "cancel-exact-turn",
        "failed" if problems else "passed",
        "; ".join(problems) or "exact turn cancelled, session kept and reused",
        observations,
    )


async def _await_turn(fixture: LiveFixture, *, timeout: float = 30.0) -> str | None:
    """Wait until the adapter has observed a turn id, reading the stream while it waits."""

    assert fixture.adapter is not None
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        await fixture.pump(until=None, timeout=1.0)
        observed = (await fixture.adapter.snapshot()).raw.get("observedTurnId")
        if observed is not None:
            return str(observed)
    return None


async def _scenario_restart(fixture: LiveFixture, *, runtime_root: Path) -> ScenarioResult:
    """Restart the bridge and attach to the existing durable session; unknown ids fail."""

    assert fixture.adapter is not None
    before = await fixture.adapter.snapshot()
    session_id = before.vendor_session_id
    assert session_id is not None, "the stopped epoch must carry the durable session id"
    cursor = before.raw.get("streamCursor")
    await fixture.adapter.stop("graceful")

    restarted = await fixture.start_adapter(runtime_root=runtime_root, epoch="epoch-2")
    # Re-attach explicitly to the durable id the previous epoch proved.
    restarted.attach_durable_session(session_id)
    events = await fixture.pump(until=None, timeout=8.0)
    observations = {
        "sessionId": session_id,
        "cursorBeforeRestart": cursor,
        "eventsAfterRestart": [event.kind for event in events],
        "transcriptAfterRestart": [entry.text for event in events for entry in event.transcript],
        "sessionStillKnown": (await restarted.snapshot()).vendor_session_id,
        "sessionBeforeRestart": session_id,
    }
    problems: list[str] = []
    if observations["sessionStillKnown"] != session_id:
        problems.append("the restarted bridge did not re-attach to the same durable session")
    if observations["transcriptAfterRestart"]:
        problems.append("the restarted bridge replayed an already delivered transcript")
    unknown_failed = False
    try:
        await restarted._require_runtime().send_message("wrun_does_not_exist", "hello")
    except HarnessControlError:
        unknown_failed = True
    observations["unknownSessionRefused"] = unknown_failed
    if not unknown_failed:
        problems.append("an unknown durable id did not fail")
    return ScenarioResult(
        "restart-attach",
        "failed" if problems else "passed",
        "; ".join(problems) or "restarted bridge attached to the same durable session",
        observations,
    )


@dataclass(frozen=True)
class _ConcurrentSession:
    """One isolation-scenario session's staging inputs."""

    label: str
    workspace_name: str
    plan_index: int


async def _start_concurrent_session(
    fixture: LiveFixture,
    session: _ConcurrentSession,
    *,
    runtime_root: Path,
    providers: list[Any],
) -> EveSessionAdapter:
    """Start one independently served session for the isolation scenario."""

    label = session.label
    workspace = fixture.report_dir / session.workspace_name
    workspace.mkdir(parents=True, exist_ok=True)
    # One provider per session, so an interleaved transcript cannot come from a shared model script
    # handing the other session's answer to this one.
    port = _free_port()
    provider = serve(
        plan=[fixture.plan[session.plan_index]],
        state_path=fixture.report_dir / f"model-state-{label}.json",
        port=port,
    )
    threading.Thread(target=provider.serve_forever, daemon=True).start()
    providers.append(provider)
    base = fixture._launch_spec(
        runtime_root=runtime_root,
        epoch_root=fixture.report_dir / "epochs" / f"epoch-concurrent-{label}",
    )
    launch = LaunchSpec(
        identity=ControlIdentity(
            ar_session_id=f"live-eve-concurrent-{label}",
            tmux_name=f"live-eve-concurrent-{label}",
            created_at=_now(),
        ),
        harness_id="eve",
        cwd=workspace,
        argv=("eve",),
        env={**dict(base.env), PROVIDER_BASE_URL_ENV: f"http://127.0.0.1:{port}/v1"},
    )
    adapter = EveSessionAdapter(
        limits=EveAdapterLimits(health_timeout_seconds=START_TIMEOUT_SECONDS), clock=_now
    )
    await asyncio.wait_for(adapter.start(launch), timeout=START_TIMEOUT_SECONDS)
    return adapter


class _EventMark:
    """A read position on one session's subscription, opened before its turn is delivered."""

    def __init__(self, adapter: EveSessionAdapter) -> None:
        self._adapter = adapter
        self.events: list[AdapterEvent] = []

    async def read_turn(self) -> None:
        async def consume() -> None:
            async for event in self._adapter.subscribe():
                self.events.append(event)
                if event.kind == "completed":
                    return

        await asyncio.wait_for(consume(), timeout=SCENARIO_TIMEOUT_SECONDS)

    def texts(self) -> list[str]:
        return [entry.text for event in self.events for entry in event.transcript]


async def _deliver_concurrently(
    first: EveSessionAdapter, second: EveSessionAdapter
) -> tuple[Any, Any]:
    """Deliver one turn to each session so their durable records overlap in time."""

    async def submit_on(adapter: EveSessionAdapter, request_id: str, text: str) -> Any:
        operation = ControlOperationRef(
            bridge_epoch="live-epoch", sequence=6, operation_id=request_id, kind="prompt"
        )
        await adapter.preflight_operation(operation)
        return await adapter.submit(
            PromptRequest(
                request_id=request_id,
                source="cockpit",
                text=text,
                submitted_at=_now(),
                operation=operation,
            )
        )

    tasks = [
        asyncio.ensure_future(submit_on(first, "live-req-a", "session a complete")),
        asyncio.ensure_future(submit_on(second, "live-req-b", "session b complete")),
    ]
    await asyncio.sleep(0.2)
    return tuple(await asyncio.gather(*tasks))


def _isolation_problems(observations: dict[str, Any]) -> list[str]:
    """What cross-session contamination would look like in the two transcripts."""

    problems: list[str] = []
    session_ids = observations["sessionIds"]
    first_texts = observations["firstTranscript"]
    second_texts = observations["secondTranscript"]
    if session_ids[0] == session_ids[1]:
        problems.append("both deliveries landed on one durable session")
    if any("session b" in text for text in first_texts):
        problems.append("session b's content appeared in session a's transcript")
    if any("session a" in text for text in second_texts):
        problems.append("session a's content appeared in session b's transcript")
    if "session a complete" not in first_texts:
        problems.append("session a's own answer was missing from its transcript")
    if "session b complete" not in second_texts:
        problems.append("session b's own answer was missing from its transcript")
    return problems


async def _scenario_concurrent(fixture: LiveFixture, *, runtime_root: Path) -> ScenarioResult:
    """Two concurrent sessions with interleaved events stay isolated.

    Two genuinely independent runtimes are started for this scenario rather than reusing an
    earlier epoch, because an epoch already bound to a durable session is exactly what this
    scenario must not share.
    """

    adapters: list[EveSessionAdapter] = []
    providers: list[Any] = []
    try:
        for session in (
            _ConcurrentSession("a", "workspace-concurrent-a", 7),
            _ConcurrentSession("b", "workspace-concurrent-b", 8),
        ):
            adapters.append(
                await _start_concurrent_session(
                    fixture, session, runtime_root=runtime_root, providers=providers
                )
            )

        first, second = adapters
        first_mark, second_mark = _EventMark(first), _EventMark(second)
        receipts = await _deliver_concurrently(first, second)
        await asyncio.gather(first_mark.read_turn(), second_mark.read_turn())
        observations = {
            "sessionIds": [receipt.raw.get("sessionId") for receipt in receipts],
            "firstTranscript": first_mark.texts(),
            "secondTranscript": second_mark.texts(),
        }
        problems = _isolation_problems(observations)
        return ScenarioResult(
            "concurrent-sessions",
            "failed" if problems else "passed",
            "; ".join(problems) or "two live sessions stayed isolated under interleaving",
            observations,
        )
    finally:
        for adapter in adapters:
            await adapter.stop("graceful")
        for provider in providers:
            provider.shutdown()
            provider.server_close()


async def run(report_dir: Path) -> int:
    fixture = LiveFixture(report_dir, _plan())
    results: list[ScenarioResult] = []
    runtime_root = REPO_ROOT / "eve_runtime"
    try:
        fixture.start_model()
        await fixture.start_adapter(runtime_root=runtime_root)
        results.append(await _scenario_protocol(fixture))
        results.append(await _scenario_reconnect(fixture))
        results.append(await _scenario_reconcile(fixture))
        results.append(await _scenario_cancel(fixture))
        results.append(await _scenario_restart(fixture, runtime_root=runtime_root))
        results.append(await _scenario_concurrent(fixture, runtime_root=runtime_root))
    except START_FAILURES as exc:
        # Every way a run can fail to start is a `blocked` scenario with its reason written out,
        # including an OS-level spawn failure: `AR_EVE_NODE` naming a path that does not exist, a
        # directory that is not executable, or a missing runtime entrypoint all arrive here as
        # OSError rather than as this module's own typed error. A raw traceback with no artifact
        # would leave an operator with nothing to act on, which is what this contract exists to
        # prevent.
        results.append(
            ScenarioResult(
                "fixture-startup",
                "blocked",
                f"the live fixture could not run: {type(exc).__name__}: {exc}",
                {
                    "runtimeRoot": str(runtime_root),
                    "node": fixture.node,
                    "failureType": type(exc).__name__,
                },
            )
        )
    finally:
        await fixture.aclose()
        fixture.stop_model()

    for result in results:
        fixture.write(f"scenario-{result.name}.json", result.to_json())
    fixture.write(
        "native-events.json",
        {
            "scenarios": [result.to_json() for result in results],
            "translatedEvents": fixture.native_events,
            "notes": fixture.notes,
            "modelTrace": _read_trace(fixture.model_trace),
            "wireRequests": fixture.wire_requests,
            "pins": {"eve": "0.56.0", "modelFixture": FIXTURE_MODEL},
        },
    )
    failed = [result for result in results if result.status != "passed"]
    print(json.dumps({"results": [result.to_json() for result in results]}, indent=2))
    return 1 if failed else 0


def _read_trace(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-dir", required=True)
    parser.add_argument(
        "--real-model",
        action="store_true",
        help="attempt one bounded native turn against a real hosted model provider",
    )
    args = parser.parse_args()
    report_dir = Path(args.report_dir)
    if args.real_model:
        return asyncio.run(run_real_provider_probe(report_dir, REPO_ROOT / "eve_runtime"))
    return asyncio.run(run(report_dir))


if __name__ == "__main__":
    raise SystemExit(main())
