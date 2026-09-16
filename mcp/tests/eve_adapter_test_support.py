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

import json
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field

from agents_remember.errors import HarnessAdapterDisconnectedError, HarnessControlError
from agents_remember.serving.eve_protocol import EveStreamEvent, parse_event_frame


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
