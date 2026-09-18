"""Deterministic eve runtime double for the native-adapter conformance suites.

This is a *transport* double, not an adapter double: it implements the same
``EveRuntimeTransport`` seam the real HTTP client does, so every test drives the actual
adapter, event mapper, cursor arithmetic and event translation. Only the eve process and its
socket are replaced.

Two behaviors are deliberately modeled rather than simplified, because they are what make the
cursor and reconnect assertions meaningful:

* the stream is served from a durable per-session record addressed by an absolute index;
* every read ends when the record's current tail is reached, exactly like a bounded catch-up
  read, so a test must reconnect to see more.
"""

from __future__ import annotations

import functools
import json
import tempfile
import threading
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from agents_remember.errors import HarnessAdapterDisconnectedError, HarnessControlError
from agents_remember.serving.eve_protocol import EveStreamEvent, parse_event_frame
from eve_capsule_test_support import (
    FixtureCarrierRequest,
    binding_env,
    fixture_carrier_for,
    repository_with_commit,
)

EVE_APPLICATION_ROOT = Path(__file__).resolve().parents[2] / "eve_runtime"
"""The AR-owned eve application the runtime launches from. Its ``node_modules`` is not committed."""

EVE_INSTALL_COMMAND = "cd eve_runtime && PATH=<node 24 bin>:$PATH npm install --no-audit --no-fund"
"""The one-command install ``eve_runtime/README.md`` documents for one checkout."""


def require_installed_eve_application() -> None:
    """Skip, by name, when the machine-local eve dependency install is absent.

    A case that starts the real runtime stages a per-epoch application directory whose
    ``node_modules`` is a link to ``eve_runtime/node_modules``; without that install the launch
    refuses by design, so such a case is not failing -- it cannot run on this machine. The skip
    names what is missing and the exact command that fixes it, because a silent skip would let a
    fresh checkout claim coverage it never ran, and a bare failure would read as a product defect
    instead of a missing machine-local install. Nothing here installs the runtime: a suite whose
    verdict depends on which machine ran it is the defect this guard exists to remove.
    """

    if (EVE_APPLICATION_ROOT / "node_modules").is_dir():
        return
    pytest.skip(
        "the eve runtime application's dependencies are not installed in this checkout: "
        f"{EVE_APPLICATION_ROOT}/node_modules is missing, and this case starts the real runtime. "
        f"Install them once per checkout with: {EVE_INSTALL_COMMAND} (eve_runtime/README.md)."
    )


@dataclass(frozen=True)
class FakeTurn:
    """One scripted native turn: the events a real eve turn emits, in the documented order."""

    number: int
    deltas: Sequence[str] = ()
    message: str | None = None
    reasoning: Sequence[str] = ()
    actions: Sequence[Mapping[str, object]] = ()
    results: Sequence[Mapping[str, object]] = ()
    requests: Sequence[Mapping[str, object]] = ()
    boundary: str = "session.waiting"


@dataclass
class FakeEveSession:
    """One durable session's record and the controls a test can observe."""

    session_id: str
    events: list[Mapping[str, object]] = field(default_factory=list)
    cancelled_turns: list[str | None] = field(default_factory=list)
    cancel_requests: list[tuple[str, str | None]] = field(default_factory=list)
    """Every ``(session_id, requested turn_id)`` the adapter asked the runtime to cancel.

    Recorded separately from ``cancelled_turns`` so a case can assert the request the adapter made
    rather than the bookkeeping this fake performed in response to it.
    """
    input_responses: list[Mapping[str, object]] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)
    message_ids: list[str] = field(default_factory=list)
    compactions: list[str] = field(default_factory=list)
    """Every summary this session was compacted into, in order (see ``compact_session``)."""

    def emit(
        self,
        event_type: str,
        data: Mapping[str, object],
        *,
        delivery_ids: Sequence[str] = (),
    ) -> None:
        """Append one event with the deterministic envelope id eve would mint for it.

        ``delivery_ids`` is the envelope field eve uses to name the accepted messages that own a
        turn, which is what makes a durable acceptance checkable after a lost response.
        """

        index = len(self.events)
        meta: dict[str, object] = {
            "id": f"evt_{self.session_id}_{index}",
            "at": "2026-09-16T00:00:00.000Z",
        }
        if delivery_ids:
            meta["deliveryIds"] = list(delivery_ids)
        self.events.append({"type": event_type, "data": dict(data), "meta": meta})


class FakeEveRuntime:
    """One AR-owned eve runtime as the adapter sees it: routes in, durable record out."""

    def __init__(self, *, endpoint: str = "http://127.0.0.1:4757") -> None:
        self.endpoint = endpoint
        self.sessions: dict[str, FakeEveSession] = {}
        self.created: list[str] = []
        self.health_calls = 0
        self.started = False
        self.stop_modes: list[str] = []
        self.health_error: HarnessControlError | None = None
        self.create_error: Exception | None = None
        self.send_error: Exception | None = None
        self.response_error: Exception | None = None
        self.cancel_error: HarnessControlError | None = None
        self.cancel_status = "accepted"
        self.stream_refusals = 0
        #: The two session controls ``eve_runtime_client.EveRuntimeTransport`` declares. They are
        #: recorded here because a fake that silently accepts a control the adapter never calls is
        #: how this fake fell out of the protocol in the first place (D19): a member the fake does
        #: not implement cannot be observed, so only a case that exercises it keeps it honest.
        self.compacted: list[str] = []
        self.cleared: list[str] = []
        self.compact_error: Exception | None = None
        self.clear_error: Exception | None = None
        self._session_counter = 0
        self._delivery_counter = 0

    # -- lifecycle ------------------------------------------------------------------------

    async def start(self) -> None:
        self.started = True

    async def health(self) -> Mapping[str, object]:
        self.health_calls += 1
        if self.health_error is not None:
            raise self.health_error
        return {"ok": True, "status": "ready", "workflowId": "workflow//eve//workflowEntry"}

    async def stop(self, mode: str) -> None:
        self.stop_modes.append(mode)

    # -- session routes -------------------------------------------------------------------

    async def create_session(self, message: str) -> tuple[str, str | None]:
        if self.create_error is not None:
            raise self.create_error
        self._session_counter += 1
        # (a lost create response is modeled by create_error_after_write below)
        session_id = f"wrun_fixture_{self._session_counter}"
        session = FakeEveSession(session_id=session_id)
        self.sessions[session_id] = session
        self.created.append(session_id)
        session.emit("session.started", {"runtime": {"agentId": "ar-eve-runtime"}})
        delivery_id = self._next_delivery(session, message)
        return session_id, delivery_id

    async def send_message(self, session_id: str, message: str) -> tuple[str, str | None]:
        session = self._require_session(session_id)
        # The durable write happens first: a lost *response* means eve accepted the message and
        # the caller never learned the delivery id, which is exactly the ambiguous case that
        # reconciliation must answer from evidence.
        delivery_id = self._next_delivery(session, message)
        if self.send_error is not None:
            raise self.send_error
        return session_id, delivery_id

    async def send_input_responses(
        self, session_id: str, responses: Sequence[Mapping[str, object]]
    ) -> None:
        if self.response_error is not None:
            raise self.response_error
        session = self._require_session(session_id)
        session.input_responses.extend(dict(entry) for entry in responses)

    async def cancel_turn(self, session_id: str, *, turn_id: str | None) -> Mapping[str, object]:
        session = self._require_session(session_id)
        session.cancel_requests.append((session_id, turn_id))
        if self.cancel_error is not None:
            raise self.cancel_error
        session.cancelled_turns.append(turn_id)
        if self.cancel_status != "accepted":
            return {"ok": True, "status": self.cancel_status}
        return {"ok": True, "sessionId": session_id, "status": "accepted"}

    # -- session controls (the two protocol members no adapter case calls directly) ---------

    async def compact_session(self, session_id: str) -> Mapping[str, object]:
        """Summarize this session's model-message history in place, as the compact route does.

        The durable session keeps its identity, and the recorded summary is what a case can observe:
        compaction represents the history rather than dropping it, which is exactly the difference
        between this control and ``clear_session``.
        """

        session = self._require_session(session_id)
        if self.compact_error is not None:
            raise self.compact_error
        self.compacted.append(session_id)
        summary = f"summary-of-{len(session.messages)}-messages"
        session.compactions.append(summary)
        session.emit("session.compacted", {"summary": summary, "messages": len(session.messages)})
        return {"ok": True, "sessionId": session_id, "summary": summary}

    async def clear_session(self, session_id: str) -> Mapping[str, object]:
        """Drop this session's model-message history in place, keeping its identity."""

        session = self._require_session(session_id)
        if self.clear_error is not None:
            raise self.clear_error
        self.cleared.append(session_id)
        removed = len(session.messages)
        session.messages.clear()
        session.message_ids.clear()
        session.emit("session.cleared", {"removed": removed})
        return {"ok": True, "sessionId": session_id, "removed": removed}

    async def stream(self, session_id: str, *, start_index: int) -> AsyncIterator[EveStreamEvent]:
        """Read the durable record from ``start_index`` up to the tail this read pinned.

        The record is decoded through the production frame parser, so a fixture that emits a
        malformed frame fails the same way a real server response would.
        """

        if self.stream_refusals:
            self.stream_refusals -= 1
            raise HarnessAdapterDisconnectedError(
                "fixture stream refused once", may_have_sent=False
            )
        session = self._require_session(session_id)
        tail = len(session.events)
        for index in range(start_index, tail):
            yield parse_event_frame(json.dumps(session.events[index]), index=index)

    # -- test controls --------------------------------------------------------------------

    def emit(
        self,
        session_id: str,
        event_type: str,
        data: Mapping[str, object],
        *,
        delivery_ids: Sequence[str] = (),
    ) -> None:
        """Append one native event to a session's durable record."""

        self._require_session(session_id).emit(event_type, data, delivery_ids=delivery_ids)

    def turn_events(self, session_id: str, turn: FakeTurn) -> None:
        """Append one complete, well-formed eve turn in the order the protocol documents."""

        session = self._require_session(session_id)
        turn_id = f"turn_{turn.number}"
        step = {"sequence": turn.number, "stepIndex": 0, "turnId": turn_id}
        delivery_ids = tuple(session.message_ids[-1:])
        session.emit(
            "turn.started",
            {"sequence": turn.number, "turnId": turn_id},
            delivery_ids=delivery_ids,
        )
        # A model reasons before it answers, so the scripted order is reasoning then message. The
        # order matters to the transcript sequence a case asserts, not merely to realism.
        for delta in turn.reasoning:
            session.emit("reasoning.appended", {**step, "reasoningDelta": delta})
        for delta in turn.deltas:
            session.emit("message.appended", {**step, "messageDelta": delta})
        if turn.message is not None:
            session.emit(
                "message.completed",
                {**step, "finishReason": "stop", "message": turn.message},
            )
        if turn.actions:
            session.emit(
                "actions.requested",
                {**step, "actions": [dict(action) for action in turn.actions]},
            )
        for result in turn.results:
            session.emit(
                "action.result",
                {**step, "result": dict(result), "status": "completed"},
            )
        if turn.requests:
            session.emit(
                "input.requested",
                {**step, "requests": [dict(request) for request in turn.requests]},
            )
        session.emit("turn.completed", {"sequence": turn.number, "turnId": turn_id})
        session.emit(turn.boundary, _boundary_data(turn.boundary))

    def complete_requested_input(self, session_id: str, request_ids: Sequence[str]) -> None:
        """Append the ``input.resolved`` batch eve writes once a response is accepted."""

        session = self._require_session(session_id)
        turn_id = _current_turn_id(session)
        session.emit(
            "input.resolved",
            {
                "sequence": 0,
                "stepIndex": 0,
                "turnId": turn_id,
                "resolutions": [
                    {"outcome": "answered", "requestId": request_id} for request_id in request_ids
                ],
            },
        )

    def last_event_index(self, session_id: str) -> int:
        return len(self._require_session(session_id).events) - 1

    def truncate_record(self, session_id: str, *, to_index: int) -> None:
        """Drop the durable tail past an index, as a test arming a 'lost response' would."""

        session = self._require_session(session_id)
        del session.events[to_index:]

    def _next_delivery(self, session: FakeEveSession, message: str) -> str:
        """Record one accepted message the way eve's durable record does.

        eve writes the durable acceptance marker as part of accepting the message, not as part of
        the caller's response, and stamps the envelope with the delivery id. Modeling that order is
        what makes a lost response reconcilable from evidence.
        """

        self._delivery_counter += 1
        delivery = f"delivery-{self._delivery_counter}"
        session.messages.append(message)
        session.message_ids.append(delivery)
        session.emit(
            "message.received",
            {
                "message": message,
                "parts": [{"text": message, "type": "text"}],
                "sequence": len(session.messages) - 1,
                "turnId": f"turn_{len(session.messages) - 1}",
            },
            delivery_ids=(delivery,),
        )
        return delivery

    def _require_session(self, session_id: str) -> FakeEveSession:
        session = self.sessions.get(session_id)
        if session is None:
            raise HarnessControlError(
                "eve refused the request because the durable session is unknown or terminal; "
                "no replacement session is created"
            )
        return session


class FakeRuntimeFactory:
    """Hand out one deterministic runtime per launch, recording every spec it was asked for."""

    def __init__(self, runtime: FakeEveRuntime | None = None) -> None:
        self.runtime = runtime or FakeEveRuntime()
        self.specs: list[object] = []

    def __call__(self, spec: object) -> FakeEveRuntime:
        self.specs.append(spec)
        return self.runtime


def raw_payload(raw: Mapping[str, object], key: str) -> Mapping[str, object]:
    """The nested object payload under one ``raw`` key, or an empty mapping when it is absent."""

    value = raw.get(key)
    return value if isinstance(value, Mapping) else {}


def _current_turn_id(session: FakeEveSession) -> str:
    for event in reversed(session.events):
        data = event.get("data")
        if isinstance(data, Mapping):
            turn_id = data.get("turnId")
            if isinstance(turn_id, str) and turn_id:
                return turn_id
    return "turn_0"


def _boundary_data(boundary: str) -> Mapping[str, object]:
    if boundary == "session.waiting":
        return {"wait": "next-user-message"}
    if boundary == "session.failed":
        return {"code": "SESSION_FAILED", "message": "fixture session failure"}
    return {}


@functools.lru_cache(maxsize=1)
def fixture_launch_binding() -> dict[str, str]:
    """A complete, verifiable capsule binding for the adapter's faked-transport launches.

    The transport is a double here, but the launch path is the real one: it verifies the declared
    carrier, its digest and the admitted worktree before a process would exist. A launch that
    declared half a binding would therefore be refused before any protocol behaviour could be
    observed, so the fixture builds the same four values a real launch carries.
    """

    root = Path(tempfile.mkdtemp(prefix="ar-eve-adapter-binding-"))
    workspace = root / "workspace"
    branch = "ar/unit-fixture"
    base_commit = repository_with_commit(workspace, branch=branch)
    carrier_path, digest = fixture_carrier_for(
        FixtureCarrierRequest(
            workspace=workspace,
            carrier_directory=root / "capsule",
            branch=branch,
            base_commit=base_commit,
            instructions=("UNIT FIXTURE CAPSULE instruction.\n",),
            binding_ref="ar-binding:leaf-test",
        )
    )
    return binding_env(
        carrier_path=carrier_path,
        digest=digest,
        workspace_root=workspace,
        binding_ref="ar-binding:leaf-test",
    )


@dataclass
class RecordedModelRequest:
    """One request body a direct provider received, kept verbatim.

    The value under measurement for the effort axis IS the request body, so this keeps the parsed
    object rather than a projection of it: a test that asserted on four selected keys could pass
    while the runtime sent something else in the field it names.
    """

    index: int
    body: dict[str, object]

    @property
    def reasoning_effort(self) -> object:
        """The ``reasoning_effort`` the provider received, or ``None`` when there was no key.

        ``None`` for "absent" is a deliberate collapse for readable assertions; a case that must
        distinguish "not sent" from "sent as null" asks ``"reasoning_effort" in body`` directly.
        """

        return self.body.get("reasoning_effort")

    def top_level_keys(self) -> list[str]:
        """Every request key except the two large framework fields, in the body's own order."""

        return [key for key in self.body if key not in {"messages", "tools"}]


def serve_recording_provider(
    record: list[RecordedModelRequest],
    *,
    drop_reasoning_effort: bool = False,
) -> tuple[ThreadingHTTPServer, int]:
    """A direct OpenAI-compatible provider that records every raw request body it receives.

    Not the product's deterministic *fixture model*: that one answers a scripted plan and traces a
    normalized projection, and the projection is exactly what must not be trusted here. This answers
    every request with one small streamed completion and keeps the body verbatim.

    ``drop_reasoning_effort`` is the *instrument's* own way to model a boundary that loses the value:
    the key is removed from the request this provider RECEIVES, so the recorded body and the answer
    describe a request that never carried it. Substituting the absence in an assertion instead would
    prove nothing about the boundary -- the recorded bytes are the value under test.
    """

    lock = threading.Lock()

    class _Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, _format: str, *_args: object) -> None:
            return

        def do_GET(self) -> None:
            payload = {"object": "list", "data": [{"id": "fixture-deterministic-1"}]}
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            length = int(self.headers.get("content-length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                decoded = json.loads(raw)
            except json.JSONDecodeError:
                decoded = {"__unparsed__": raw.decode("utf-8", errors="replace")}
            parsed: dict[str, object] = dict(decoded)
            if drop_reasoning_effort:
                del parsed["reasoning_effort"]
            with lock:
                index = len(record)
                record.append(RecordedModelRequest(index=index, body=parsed))
            model = str(parsed.get("model") or "fixture-deterministic-1")
            if parsed.get("stream"):
                frames = [
                    _sse_frame(
                        {
                            "id": f"chatcmpl-recorded-{index}",
                            "object": "chat.completion.chunk",
                            "created": 0,
                            "model": model,
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {"role": "assistant", "content": f"recorded {index}"},
                                    "finish_reason": None,
                                }
                            ],
                        }
                    ),
                    _sse_frame(
                        {
                            "id": f"chatcmpl-recorded-{index}",
                            "object": "chat.completion.chunk",
                            "created": 0,
                            "model": model,
                            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                        }
                    ),
                    b"data: [DONE]\n\n",
                ]
                body = b"".join(frames)
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            payload = {
                "id": f"chatcmpl-recorded-{index}",
                "object": "chat.completion",
                "created": 0,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": f"recorded {index}"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
            encoded = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, int(server.server_address[1])


def _sse_frame(payload: Mapping[str, object]) -> bytes:
    return f"data: {json.dumps(dict(payload))}\n\n".encode()


def staged_runtime_root(scratch: Path, *, authored: Path | None = None) -> Path:
    """An application root the production staging path can copy: the authored tree + a link.

    ``eve_runtime/node_modules`` is machine-local and not committed, and the production stager
    requires the source it copies to have its dependencies installed. This assembles the one
    combination that keeps the bytes under test the checkout's own: the ``agent`` tree is a link to
    the authored directory (so a case compiles what the repository ships, recorded edits and all),
    while ``node_modules`` is a link to the installed one. Nothing is installed and nothing is
    copied into the worktree.
    """

    runtime_root = authored if authored is not None else EVE_APPLICATION_ROOT
    scratch.mkdir(parents=True, exist_ok=True)
    (scratch / "agent").symlink_to(runtime_root / "agent")
    (scratch / "node_modules").symlink_to(EVE_APPLICATION_ROOT / "node_modules")
    for name in ("package.json", "package-lock.json", "tsconfig.json"):
        origin = EVE_APPLICATION_ROOT / name
        if origin.exists():
            (scratch / name).write_bytes(origin.read_bytes())
    return scratch
