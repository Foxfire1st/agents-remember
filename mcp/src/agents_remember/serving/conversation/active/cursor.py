"""Active page/event cursor authority (260718-CHATS-L1, R4).

Active cursors are signed, purpose-branded opaque tokens binding the caller
authorization (principal/tenant), the exact AR session and bridge epoch, the
native conversation identity, the projector generation (event cursors), the
cursor schema version, and the page/event purpose. The four cursor brands in
:mod:`conversation.models` are non-interchangeable: a token minted for one
purpose fails validation for any other before any lookup happens.

The signature is tamper-evidence minted with an app-scoped secret held by the
active service; the *binding checks* are the actual authorization mechanism —
every decoded field is re-compared against the authorized request context on
every wire. Pre-stream failures are typed HTTP errors; established-stream
continuity failures are gap events, never HTTP resets.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Literal

from agents_remember.errors import AgentsRememberError
from agents_remember.models.conversations.cursors import (
    ActiveEventCursor,
    ActivePageCursor,
)
from agents_remember.models.conversations.identity import (
    ActiveConversationRef,
    AuthorizationBinding,
)

CURSOR_SCHEMA_VERSION = 1

CursorPurpose = Literal["active-page", "active-event"]


class ConversationCursorError(AgentsRememberError):
    """Base for typed cursor failures; each names its wire status and slug."""

    http_status: int = 400
    status_slug: str = "cursor-invalid"

    def __init__(self, detail: str, *, reason: str | None = None) -> None:
        super().__init__(detail)
        self.reason = reason


class CursorInvalidError(ConversationCursorError):
    """Malformed, forged, wrong-purpose, or wrong-route cursor."""

    http_status = 400
    status_slug = "cursor-invalid"


class CursorAuthorizationError(ConversationCursorError):
    """A cursor naming another principal, tenant, or AR session."""

    http_status = 403
    status_slug = "cursor-authorization"


class CursorEpochMismatchError(ConversationCursorError):
    """A cursor minted under another bridge epoch."""

    http_status = 409
    status_slug = "bridge-epoch-mismatch"


class CursorResetRequiredError(ConversationCursorError):
    """A cursor whose continuation authority is gone (generation/retention)."""

    http_status = 409
    status_slug = "cursor-reset-required"


class CursorConflictError(ConversationCursorError):
    """Two resume sources naming different event cursors."""

    http_status = 400
    status_slug = "cursor-conflict"


@dataclass(frozen=True)
class DecodedPageCursor:
    ordinal: int


@dataclass(frozen=True)
class DecodedEventCursor:
    generation: str
    sequence: int


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _canonical(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )


def _sign(secret: bytes, body: str) -> str:
    return hmac.new(secret, body.encode("ascii"), hashlib.sha256).hexdigest()[:32]


def _binding_payload(
    purpose: CursorPurpose,
    authorization: AuthorizationBinding,
    identity: ActiveConversationRef,
    generation: str,
    position: int,
) -> dict[str, object]:
    return {
        "v": CURSOR_SCHEMA_VERSION,
        "purpose": purpose,
        "principal": authorization.principal_id,
        "tenant": authorization.tenant_id,
        "session": identity.ar_session_id,
        "epoch": identity.bridge_epoch,
        "harness": identity.harness_id,
        "vendor": identity.vendor_conversation_id,
        "scope": identity.project_scope,
        "generation": generation,
        "position": position,
    }


def _mint(
    secret: bytes,
    prefix: str,
    payload: dict[str, object],
) -> str:
    body = _b64encode(_canonical(payload))
    return f"{prefix}{body}.{_sign(secret, body)}"


def _decode(
    secret: bytes,
    token: str,
    prefix: str,
    expected_purpose: CursorPurpose,
) -> dict[str, object]:
    if not token.startswith(prefix):
        raise CursorInvalidError("cursor purpose does not match this route")
    body, separator, signature = token[len(prefix) :].rpartition(".")
    if not separator or not body or not signature:
        raise CursorInvalidError("cursor is malformed")
    if not hmac.compare_digest(_sign(secret, body), signature):
        raise CursorInvalidError("cursor failed integrity verification")
    try:
        payload = json.loads(_b64decode(body))
    except (ValueError, UnicodeDecodeError) as exc:
        raise CursorInvalidError("cursor payload is not decodable") from exc
    if not isinstance(payload, dict):
        raise CursorInvalidError("cursor payload is not an object")
    if payload.get("v") != CURSOR_SCHEMA_VERSION:
        raise CursorInvalidError("cursor schema version is unsupported")
    if payload.get("purpose") != expected_purpose:
        raise CursorInvalidError("cursor purpose does not match this route")
    return payload


def _require_binding(
    payload: dict[str, object],
    authorization: AuthorizationBinding,
    identity: ActiveConversationRef,
) -> None:
    if (
        payload.get("principal") != authorization.principal_id
        or payload.get("tenant") != authorization.tenant_id
    ):
        raise CursorAuthorizationError("cursor does not belong to this caller")
    if payload.get("session") != identity.ar_session_id:
        raise CursorAuthorizationError("cursor does not belong to this AR session")
    if payload.get("epoch") != identity.bridge_epoch:
        raise CursorEpochMismatchError(
            "cursor was minted under a different bridge epoch",
            reason="epoch-mismatch",
        )
    if (
        payload.get("harness") != identity.harness_id
        or payload.get("vendor") != identity.vendor_conversation_id
        or payload.get("scope") != identity.project_scope
    ):
        raise CursorInvalidError("cursor names a different native conversation")


def mint_page_cursor(
    secret: bytes,
    authorization: AuthorizationBinding,
    identity: ActiveConversationRef,
    *,
    ordinal: int,
) -> ActivePageCursor:
    """Mint a page cursor over an ordinal boundary; survives projector restarts."""

    return ActivePageCursor(
        _mint(
            secret,
            ActivePageCursor.token_prefix,
            _binding_payload("active-page", authorization, identity, "native", ordinal),
        )
    )


def decode_page_cursor(
    secret: bytes,
    token: ActivePageCursor,
    authorization: AuthorizationBinding,
    identity: ActiveConversationRef,
) -> DecodedPageCursor:
    payload = _decode(secret, token.root, ActivePageCursor.token_prefix, "active-page")
    _require_binding(payload, authorization, identity)
    ordinal = payload.get("position")
    if not isinstance(ordinal, int) or isinstance(ordinal, bool) or ordinal < 0:
        raise CursorInvalidError("page cursor position is invalid")
    return DecodedPageCursor(ordinal=ordinal)


def mint_event_cursor(
    secret: bytes,
    authorization: AuthorizationBinding,
    identity: ActiveConversationRef,
    *,
    generation: str,
    sequence: int,
) -> ActiveEventCursor:
    """Mint an event cursor bound to one projector generation."""

    return ActiveEventCursor(
        _mint(
            secret,
            ActiveEventCursor.token_prefix,
            _binding_payload("active-event", authorization, identity, generation, sequence),
        )
    )


def decode_event_cursor(
    secret: bytes,
    token: ActiveEventCursor,
    authorization: AuthorizationBinding,
    identity: ActiveConversationRef,
) -> DecodedEventCursor:
    payload = _decode(secret, token.root, ActiveEventCursor.token_prefix, "active-event")
    _require_binding(payload, authorization, identity)
    generation = payload.get("generation")
    sequence = payload.get("position")
    if not isinstance(generation, str) or not generation:
        raise CursorInvalidError("event cursor generation is invalid")
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 0:
        raise CursorInvalidError("event cursor sequence is invalid")
    return DecodedEventCursor(generation=generation, sequence=sequence)


def require_same_generation(decoded: DecodedEventCursor, generation: str) -> None:
    """A projector restart establishes a new generation; old cursors reset."""

    if decoded.generation != generation:
        raise CursorResetRequiredError(
            "event cursor predates the current projector generation",
            reason="generation-changed",
        )


__all__ = [
    "ConversationCursorError",
    "CursorAuthorizationError",
    "CursorConflictError",
    "CursorEpochMismatchError",
    "CursorInvalidError",
    "CursorResetRequiredError",
    "DecodedEventCursor",
    "DecodedPageCursor",
    "decode_event_cursor",
    "decode_page_cursor",
    "mint_event_cursor",
    "mint_page_cursor",
    "require_same_generation",
]
