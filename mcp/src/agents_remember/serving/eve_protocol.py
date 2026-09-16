"""The eve wire contract: routes, NDJSON event envelopes, and the absolute stream cursor.

eve's session API is ID-addressed and its stream is a durable, newline-delimited JSON record.
An event's position in that record is an absolute event index, and that index -- not the event's
``meta.id`` -- is the only lossless cursor: two events emitted in the same millisecond by
different durable steps may sort either way, so an ID-ordered cursor can skip events. The ID is
used here for exactly what the protocol guarantees it for: recognising an event that a reconnect
already delivered.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import cast

from agents_remember.errors import HarnessControlError

EVE_ROUTE_PREFIX = "/eve/v1"
"""The one HTTP surface this adapter speaks; eve's default channel owns every route below."""

EVE_HEALTH_PATH = f"{EVE_ROUTE_PREFIX}/health"
EVE_SESSION_PATH = f"{EVE_ROUTE_PREFIX}/session"

EVE_SESSION_ID_HEADER = "x-eve-session-id"
EVE_STREAM_FORMAT_HEADER = "x-eve-stream-format"
EVE_STREAM_TAIL_INDEX_HEADER = "x-eve-stream-tail-index"
EVE_STREAM_VERSION_HEADER = "x-eve-stream-version"

EVE_STREAM_FORMAT = "ndjson"
EVE_SUPPORTED_STREAM_VERSIONS = frozenset({"21", "22", "23", "24", "25"})
"""Versions this reader accepts. 25 is the delta-only contract; lower versions are accepted
because eve normalises their cumulative appends on replay, not because this adapter reads them
differently -- the delta fields it consumes are present in every accepted version."""

EVE_SESSION_HEADER = "eve"

TURN_POLICY_QUEUE = "queue"
"""Ordinary AR deliveries are queued. eve's default is cancellation-backed ``steer``, which would
cancel an active turn when a second ordinary delivery arrives; AR never inherits that default."""

CREATE_ACCEPTED_STATUS = 202
ACCEPTED_STATUS = 202
NO_ACTIVE_TURN_STATUS = 200
SESSION_NOT_ACTIVE_STATUS = 409
SESSION_NOT_ACTIVE_CODE = "session_not_active"

TURN_BOUNDARY_EVENT_TYPES = frozenset({"session.waiting", "session.completed", "session.failed"})
"""The three events eve documents as a turn boundary. ``session.waiting`` completes a turn while
leaving the session usable; the other two are terminal for the session."""

SESSION_TERMINAL_EVENT_TYPES = frozenset({"session.completed", "session.failed"})

TURN_FAILURE_EVENT_TYPES = frozenset({"turn.failed", "turn.cancelled", "step.failed"})

ACTION_REQUEST_EVENT_TYPES = frozenset({"actions.requested", "action.partial"})

INTERACTION_REQUEST_EVENT_TYPE = "input.requested"
INTERACTION_RESOLVED_EVENT_TYPE = "input.resolved"

AUTHORIZATION_REQUIRED_EVENT_TYPE = "authorization.required"
AUTHORIZATION_COMPLETED_EVENT_TYPE = "authorization.completed"


@dataclass(frozen=True)
class EveStreamEvent:
    """One event read from the durable stream, with the index it was read at.

    ``index`` is the absolute event count the adapter must persist as its resume cursor. ``id`` is
    the stable envelope identity used to recognise an overlapping replay; it is absent on records
    written before stream version 20 and is therefore optional by contract, not by laxity.
    """

    index: int
    type: str
    data: Mapping[str, object]
    event_id: str | None
    emitted_at: str | None
    delivery_ids: tuple[str, ...] = ()

    @property
    def turn_id(self) -> str | None:
        turn_id = self.data.get("turnId")
        return turn_id if isinstance(turn_id, str) and turn_id else None

    def raw_frame(self) -> dict[str, object]:
        """The event as the documented envelope: what an evidence buffer retains verbatim."""

        meta: dict[str, object] = {}
        if self.event_id is not None:
            meta["id"] = self.event_id
        if self.emitted_at is not None:
            meta["at"] = self.emitted_at
        if self.delivery_ids:
            meta["deliveryIds"] = list(self.delivery_ids)
        return {"type": self.type, "data": dict(self.data), "meta": meta}


@dataclass(frozen=True)
class EveRuntimeLaunch:
    """Everything needed to start one AR-owned eve application.

    Declared here, beside the wire contract it serves, so the runtime launch spec and the
    transport client can both depend on it without importing each other.
    """

    runtime_root: str
    port: int
    host: str = "127.0.0.1"
    env: Mapping[str, str] = field(default_factory=dict)
    node_executable: str | None = None
    """``None`` resolves a compatible node at start; an explicit value is honored verbatim."""

    state_root: str | None = None
    """Where eve may keep its development state (``.eve/``).

    eve treats one application directory as one agent and refuses to start a second development
    server for it, so an epoch that must not collide with another epoch points its state at its own
    directory. ``None`` keeps the state beside the application, which is right for a checkout that
    runs one runtime at a time.
    """


def eve_session_route(session_id: str) -> str:
    """The ID-addressed follow-up route for one durable session; never a create route."""

    return f"{EVE_SESSION_PATH}/{_encode_segment(session_id)}"


def eve_session_control_route(session_id: str, control: str) -> str:
    """One ID-addressed session control (``cancel``, ``compact``, ``clear``, ``reset``)."""

    return f"{eve_session_route(session_id)}/{_encode_segment(control)}"


def eve_session_stream_route(session_id: str, *, start_index: int) -> str:
    """The durable stream route from one absolute event index."""

    if start_index < 0:
        raise HarnessControlError("eve stream cursor must be a nonnegative absolute event index")
    if start_index == 0:
        return f"{eve_session_route(session_id)}/stream"
    return f"{eve_session_route(session_id)}/stream?startIndex={start_index}"


def parse_stream_version(headers: Mapping[str, str]) -> str:
    """Require the declared stream version instead of treating unknown JSON as current."""

    version = _header(headers, EVE_STREAM_VERSION_HEADER)
    if version is None:
        raise HarnessControlError(
            f"eve stream response is missing the {EVE_STREAM_VERSION_HEADER} header"
        )
    if version not in EVE_SUPPORTED_STREAM_VERSIONS:
        raise HarnessControlError(f"unsupported eve stream version: {version!r}")
    return version


def parse_stream_tail_index(headers: Mapping[str, str]) -> int | None:
    """Read the optional durable-tail bound; ``None`` when the server did not report one."""

    raw = _header(headers, EVE_STREAM_TAIL_INDEX_HEADER)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise HarnessControlError(
            f"invalid {EVE_STREAM_TAIL_INDEX_HEADER} header: {raw!r}"
        ) from exc


def parse_session_acceptance(body: object, headers: Mapping[str, str]) -> tuple[str, str | None]:
    """Read ``(session_id, delivery_id)`` from an accepted message response.

    eve returns the durable id in the body and in a header; a body that carries neither is
    accepted only when the header does, and an acceptance with no identity at all is a protocol
    failure rather than a silently unbound session.
    """

    payload = body if isinstance(body, Mapping) else {}
    session_id = payload.get("sessionId")
    if not isinstance(session_id, str) or not session_id:
        session_id = _header(headers, EVE_SESSION_ID_HEADER)
    if not session_id:
        raise HarnessControlError("eve session acceptance carried no durable session id")
    delivery_id = payload.get("deliveryId")
    return session_id, delivery_id if isinstance(delivery_id, str) and delivery_id else None


def parse_event_frame(line: str, *, index: int) -> EveStreamEvent:
    """Parse one NDJSON frame by its declared schema; never by scanning the text."""

    try:
        raw = json.loads(line)
    except json.JSONDecodeError as exc:
        raise HarnessControlError("eve stream line is not valid JSON") from exc
    if not isinstance(raw, Mapping):
        raise HarnessControlError("eve stream line must be a JSON object")
    event_type = raw.get("type")
    if not isinstance(event_type, str) or not event_type:
        raise HarnessControlError("eve stream event requires a non-empty type")
    data = raw.get("data")
    if data is not None and not isinstance(data, Mapping):
        raise HarnessControlError(f"eve {event_type} event data must be an object")
    meta = raw.get("meta")
    meta_map = meta if isinstance(meta, Mapping) else {}
    event_id = meta_map.get("id")
    emitted_at = meta_map.get("at")
    delivery_ids = meta_map.get("deliveryIds")
    return EveStreamEvent(
        index=index,
        type=event_type,
        data=cast(Mapping[str, object], data) if data is not None else {},
        event_id=event_id if isinstance(event_id, str) and event_id else None,
        emitted_at=emitted_at if isinstance(emitted_at, str) and emitted_at else None,
        delivery_ids=tuple(item for item in delivery_ids if isinstance(item, str))
        if isinstance(delivery_ids, Iterable) and not isinstance(delivery_ids, (str, bytes))
        else (),
    )


EVE_REPLAY_WINDOW = 2048
"""How many recent event ids one reader remembers for replay protection.

A reconnect from an earlier cursor overlaps events that were already handled, and eve may also
serve the same event twice across a rewind; the same event always carries the same ``meta.id``.
The bound is a fixed window rather than a growing set, because eve promises overlap at the tail of
the record, never an arbitrarily deep rewind.
"""


@dataclass
class EveEventDeduplicator:
    """Bounded replay protection keyed on the stable event envelope id.

    This is the single owner of that window: the adapter holds one instance and asks it about every
    identified event, so the component this module documents is the one under test.
    """

    window: int = EVE_REPLAY_WINDOW
    _seen: dict[str, None] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.window < 1:
            raise HarnessControlError("eve replay window must be positive")

    def is_replay(self, event: EveStreamEvent) -> bool:
        """Record one event id and answer whether it was already delivered in this window."""

        if event.event_id is None:
            # Pre-version-20 records carry no identity; the absolute index is then the only
            # ordering we have, and it is the caller's cursor that protects against replay.
            return False
        if event.event_id in self._seen:
            return True
        self._seen[event.event_id] = None
        while len(self._seen) > self.window:
            self._seen.pop(next(iter(self._seen)))
        return False

    @property
    def retained(self) -> int:
        """How many ids the window currently holds, for the bounded-growth assertion."""

        return len(self._seen)


def _header(headers: Mapping[str, str], name: str) -> str | None:
    direct = headers.get(name)
    if direct is not None:
        return direct.strip() or None
    lowered = name.lower()
    for key, value in headers.items():
        if key.lower() == lowered:
            return value.strip() or None
    return None


def _encode_segment(value: str) -> str:
    if not value:
        raise HarnessControlError("eve route requires a non-empty identity segment")
    return value.replace("/", "%2F")
