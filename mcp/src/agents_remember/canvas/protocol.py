"""The narrow host protocol one admitted page speaks (MIK-R104 rules 5, 6).

One registered version, a closed operation set, and an envelope every request carries. The frame
receives exactly four operations:

``ready``
    the page has loaded; it takes no payload.
``status``
    a bounded display status for the trusted panel -- one of ready, error or size.
``open-evidence``
    a reference the page believes is admitted; the *host* resolves it against the revision's own
    admitted evidence and answers an exact destination or an honest limitation.
``request-explanation``
    a question the page would like to put to the trusted conversation control. It records a
    request; it starts no model, and the trusted side decides whether to act on it.

Everything else is refused before anything happens. A message is bound to the host-created session,
the revision and the comparison it was served with; a request that names another revision, another
comparison or another session is a forgery attempt, not a message. Repeats are refused by request
identity, so a replayed message has no effect twice.

This module validates bytes and envelopes; it neither reads the frame store nor performs an effect.
"""

from __future__ import annotations

import json
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

__all__ = [
    "FRAME_OPS",
    "HOST_OPS",
    "MAX_MESSAGE_BYTES",
    "MAX_QUESTION_LENGTH",
    "MAX_REQUESTS_PER_SESSION",
    "MAX_STATUS_DETAIL",
    "MESSAGE_SCHEMA",
    "PROTOCOL_VERSION",
    "REFUSAL_REASONS",
    "AcceptedMessage",
    "Binding",
    "FrameMessage",
    "ProtocolRefusal",
    "RequestLedger",
    "parse_frame_message",
]

PROTOCOL_VERSION = 1
MESSAGE_SCHEMA = "ar-canvas-message/v1"

#: What the frame may send, and what the host may answer. Both are closed.
FRAME_OPS: tuple[str, ...] = ("ready", "status", "open-evidence", "request-explanation")
HOST_OPS: tuple[str, ...] = ("accepted", "refused", "unavailable")

#: The finite bounds the envelope and the payload obey (rule 6, rule 8).
MAX_MESSAGE_BYTES = 4096
MAX_REQUESTS_PER_SESSION = 240
MAX_STATUS_DETAIL = 512
MAX_QUESTION_LENGTH = 1024
MAX_REFERENCE_LENGTH = 512
MAX_CLAIM_ID_LENGTH = 128
MAX_RATE = 20
MAX_BURST = 40
#: How deep a legal envelope may nest. The envelope and every payload field it admits are flat, so a
#: deeper document is refused before a validator walks it (L104-B07).
MAX_JSON_DEPTH = 12

#: Every refusal the host can name. A refusal is a value, never a silent no-op.
REFUSAL_REASONS: tuple[str, ...] = (
    "malformed",
    "oversized",
    "wrong-version",
    "unknown-op",
    "foreign-session",
    "stale-revision",
    "foreign-comparison",
    "replayed",
    "rate-limited",
    "exhausted",
    "revoked",
    "wrong-window",
)


class ProtocolRefusal(ValueError):
    """A message refused with one reason from :data:`REFUSAL_REASONS`."""

    def __init__(self, reason: str, detail: str) -> None:
        if reason not in REFUSAL_REASONS:
            raise ValueError(f"{reason!r} is not a named refusal")
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}")


class FrameMessage(BaseModel):
    """The envelope one frame request carries, before it is bound to a session.

    Validation is strict: a field's type is the type the schema names, so ``"1"`` is not a protocol
    version and ``true`` is not a size. A lax coercion would let a wrong-typed request through as if
    it were canonical (L104-B07).
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True, strict=True)

    schema_: Literal["ar-canvas-message/v1"] = Field(default=MESSAGE_SCHEMA, alias="schema")
    protocol_version: int = Field(alias="protocolVersion")
    type: str = Field(min_length=1, max_length=64)
    request_id: str = Field(alias="requestId", min_length=8, max_length=64)
    session: str = Field(min_length=16, max_length=128)
    revision: int = Field(ge=1)
    comparison: str = Field(min_length=1, max_length=MAX_REFERENCE_LENGTH)
    payload: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class Binding:
    """What one session is bound to: its own value, revision and comparison."""

    session: str
    revision: int
    comparison: str


@dataclass(frozen=True)
class AcceptedMessage:
    """A validated request: its operation, request identity and the payload that was let through."""

    operation: str
    request_id: str
    payload: dict[str, Any] = field(default_factory=dict)


def _bounded(payload: dict[str, Any], *, allowed: set[str]) -> dict[str, Any]:
    unknown = set(payload) - allowed
    if unknown:
        raise ProtocolRefusal(
            "malformed", f"the payload names unknown field(s): {', '.join(sorted(unknown))}"
        )
    return payload


def _text(value: Any, *, limit: int, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProtocolRefusal("malformed", f"{name} is a non-empty string")
    if len(value) > limit:
        raise ProtocolRefusal("oversized", f"{name} exceeds its {limit}-character bound")
    return value


def _status(payload: dict[str, Any]) -> dict[str, Any]:
    _bounded(payload, allowed={"kind", "detail", "bytes"})
    kind = payload.get("kind")
    # `in` on an unhashable JSON value raises TypeError, which would escape the refusal contract as a
    # 500 instead of a named refusal (L104-B07).
    if not isinstance(kind, str) or kind not in {"ready", "error", "size"}:
        raise ProtocolRefusal("malformed", "a status kind is a string: ready, error or size")
    result: dict[str, Any] = {"kind": kind}
    if "detail" in payload:
        result["detail"] = _text(payload["detail"], limit=MAX_STATUS_DETAIL, name="detail")
    if "bytes" in payload:
        size = payload["bytes"]
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise ProtocolRefusal("malformed", "a size is a non-negative integer")
        if size > 2**31:
            raise ProtocolRefusal("oversized", "a size beyond 2 GiB is not a display size")
        result["bytes"] = size
    return result


def _open_evidence(payload: dict[str, Any]) -> dict[str, Any]:
    _bounded(payload, allowed={"reference", "claimId"})
    result: dict[str, Any] = {
        "reference": _text(
            payload.get("reference"), limit=MAX_REFERENCE_LENGTH, name="the evidence reference"
        )
    }
    if "claimId" in payload:
        result["claimId"] = _text(
            payload["claimId"], limit=MAX_CLAIM_ID_LENGTH, name="the claim identity"
        )
    return result


def _request_explanation(payload: dict[str, Any]) -> dict[str, Any]:
    _bounded(payload, allowed={"question"})
    return {
        "question": _text(
            payload.get("question"), limit=MAX_QUESTION_LENGTH, name="the explanation question"
        )
    }


_PAYLOAD_RULES: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "ready": lambda payload: _bounded(payload, allowed=set()),
    "status": _status,
    "open-evidence": _open_evidence,
    "request-explanation": _request_explanation,
}


def _nests_too_deep(value: object) -> bool:
    """Whether any value in this document is deeper than :data:`MAX_JSON_DEPTH`.

    The walk is an explicit stack, not recursion: a hostile document must not be able to make the
    guard itself overflow (L104-B07).
    """

    stack: list[tuple[object, int]] = [(value, 1)]
    while stack:
        item, depth = stack.pop()
        if depth > MAX_JSON_DEPTH:
            return True
        if isinstance(item, dict):
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)
    return False


def _decoded(raw: bytes) -> dict[str, Any]:
    """The one JSON object these bytes are, or a named refusal.

    The size is checked on the bytes before any parse, so an oversized body costs no parse, and the
    decoded document is depth-checked before a validator walks it. A body that is not UTF-8, not
    JSON, not an object, or too deeply nested is ``malformed`` -- never an exception the route would
    answer as a 500 (L104-B07).
    """

    if len(raw) > MAX_MESSAGE_BYTES:
        raise ProtocolRefusal(
            "oversized", f"the message is {len(raw)} bytes, beyond {MAX_MESSAGE_BYTES}"
        )
    try:
        decoded = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise ProtocolRefusal("malformed", f"the message is not JSON: {error}") from error
    if not isinstance(decoded, dict):
        raise ProtocolRefusal("malformed", "the message is a JSON object")
    if _nests_too_deep(decoded):
        raise ProtocolRefusal(
            "malformed",
            f"the message nests deeper than {MAX_JSON_DEPTH} levels; every envelope and payload "
            "field this protocol admits is flat",
        )
    return decoded


def parse_frame_message(raw: bytes, *, binding: Binding) -> AcceptedMessage:
    """Validate one raw request against one session's binding, or refuse it by name.

    The envelope's own identity fields are checked against the binding: a message that names another
    session, revision or comparison is refused as the forgery it is.
    """

    decoded = _decoded(raw)
    try:
        message = FrameMessage.model_validate(decoded)
    except ValidationError as error:
        raise ProtocolRefusal(
            "malformed", f"the envelope is invalid: {error.errors()[0]}"
        ) from error
    if message.protocol_version != PROTOCOL_VERSION:
        raise ProtocolRefusal(
            "wrong-version",
            f"protocol version {message.protocol_version} is not the registered {PROTOCOL_VERSION}",
        )
    if message.type not in FRAME_OPS:
        raise ProtocolRefusal(
            "unknown-op", f"{message.type!r} is not one of the registered operations"
        )
    if message.session != binding.session:
        raise ProtocolRefusal("foreign-session", "the message names another session")
    if message.revision != binding.revision:
        raise ProtocolRefusal(
            "stale-revision",
            f"the message names revision {message.revision}; this channel serves "
            f"{binding.revision}",
        )
    if message.comparison != binding.comparison:
        raise ProtocolRefusal(
            "foreign-comparison",
            "the message names another comparison than the revision it was served with",
        )
    return AcceptedMessage(
        operation=message.type,
        request_id=message.request_id,
        payload=_PAYLOAD_RULES[message.type](message.payload),
    )


class RequestLedger:
    """The bounded identity memory one session keeps: accepted, refused and rate state.

    It is a ledger, not a log: it keeps the request identities it has seen (bounded, oldest out
    first), a token bucket, the accepted count against the session ceiling, and the last refusals
    for bounded diagnosis. Nothing here grows with traffic.
    """

    def __init__(
        self,
        *,
        capacity: int = MAX_REQUESTS_PER_SESSION,
        rate: int = MAX_RATE,
        burst: int = MAX_BURST,
        diagnostics: int = 20,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.capacity = capacity
        self._rate = rate
        self._burst = burst
        self._clock = clock
        self._tokens = float(burst)
        self._last = clock()
        self._seen: deque[str] = deque(maxlen=capacity)
        self._known: set[str] = set()
        self._refusals: deque[dict[str, str]] = deque(maxlen=diagnostics)
        self.accepted = 0
        self.refused = 0
        self.last_status: dict[str, Any] | None = None
        self.explanation_requests: deque[str] = deque(maxlen=20)

    def _refill(self) -> None:
        now = self._clock()
        elapsed = max(0.0, now - self._last)
        self._last = now
        self._tokens = min(float(self._burst), self._tokens + elapsed * self._rate)

    def charge(self) -> None:
        """Debit one request from the bucket *before* the caller parses it (L104-B08).

        A refusal is work too: a malformed body still costs a parse and a diagnostic entry. The route
        charges every request that reaches it, so a flood meets the bucket rather than the parser,
        and the excess is refused ``rate-limited`` without any parse at all.
        """

        self._refill()
        if self._tokens < 1.0:
            raise ProtocolRefusal(
                "rate-limited", f"more than {self._rate} request(s) per second reached the host"
            )
        self._tokens -= 1.0

    def admit(self, message: AcceptedMessage) -> None:
        """Count one already-validated, already-charged request, or refuse it by name.

        The token was taken by :meth:`charge` before the parse; this records the request identity and
        the session ceiling, and refuses a repeat of an identity it has already taken.
        """

        if message.request_id in self._known:
            raise ProtocolRefusal(
                "replayed",
                f"request {message.request_id} was already received on this channel",
            )
        if self.accepted >= self.capacity:
            raise ProtocolRefusal(
                "exhausted",
                f"this channel took its {self.capacity} admitted request(s); reopen the frame",
            )
        if len(self._seen) == self._seen.maxlen and self._seen:
            self._known.discard(self._seen[0])
        self._seen.append(message.request_id)
        self._known.add(message.request_id)
        self.accepted += 1

    def note_refusal(self, reason: str, detail: str) -> None:
        self.refused += 1
        self._refusals.append({"reason": reason, "detail": detail[:200]})

    def diagnostics(self) -> dict[str, Any]:
        """The bounded state a panel or a reviewer may read. It holds no message content."""

        return {
            "accepted": self.accepted,
            "refused": self.refused,
            "refusals": list(self._refusals),
            "lastStatus": self.last_status,
            "explanationRequests": len(self.explanation_requests),
        }
