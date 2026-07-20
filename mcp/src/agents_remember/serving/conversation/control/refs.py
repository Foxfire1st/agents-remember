"""Opaque signed control-reference authority (260718-CHATS-L3).

Every control-domain opaque token is a purpose-branded, HMAC-signed payload
binding the caller authorization (principal/tenant), the exact AR session and
bridge epoch, and the operation identity it names. The four brands are
non-interchangeable: a token minted for one purpose fails validation for any
other before any lookup happens.

- ``ar-oqr1.`` operationRef: stable queue-row identity (kind/operationId/sequence).
- ``ar-wdr1.`` withdrawalRef: caller/session/epoch/operation-bound action target
  for one withdrawable cockpit row (adds the withdraw ledger salt).
- ``ar-wrr1.`` recoveryRef: opaque pending-recovery identity; never contains or
  reveals prompt content (adds the withdrawRequestId).
- ``ar-war1.`` recoveryAssetRef: one recoverable staged asset's exchange identity.

The signature is tamper-evidence minted with the app-scoped control secret; the
*binding checks* are the actual authorization mechanism — every decoded field is
re-compared against the authorized request context on every wire. Tokens carry
no content of any kind; identity fields only (the same posture as the landed L1
cursor authority, which this mirrors).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Literal

from agents_remember.errors import AgentsRememberError
from agents_remember.serving.conversation.models import AuthorizationBinding

REF_SCHEMA_VERSION = 1

RefPurpose = Literal["operation-ref", "withdrawal-ref", "recovery-ref", "recovery-asset-ref"]

_PREFIX_BY_PURPOSE: dict[str, str] = {
    "operation-ref": "ar-oqr1.",
    "withdrawal-ref": "ar-wdr1.",
    "recovery-ref": "ar-wrr1.",
    "recovery-asset-ref": "ar-war1.",
}


class ControlRefError(AgentsRememberError):
    """Base for typed control-reference failures; each names its wire status/slug."""

    http_status: int = 400
    status_slug: str = "ref-invalid"


class RefInvalidError(ControlRefError):
    """Malformed, forged, or wrong-purpose reference."""

    http_status = 400
    status_slug = "ref-invalid"


class RefAuthorizationError(ControlRefError):
    """A reference naming another principal, tenant, or AR session."""

    http_status = 403
    status_slug = "ref-authorization"


class RefEpochMismatchError(ControlRefError):
    """A reference minted under another bridge epoch."""

    http_status = 409
    status_slug = "bridge-epoch-mismatch"


class OperationIdentity:
    """The exact queue-row identity a reference names."""

    def __init__(self, *, kind: str, operation_id: str, sequence: int) -> None:
        self.kind = kind
        self.operation_id = operation_id
        self.sequence = sequence

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, OperationIdentity)
            and (self.kind, self.operation_id, self.sequence)
            == (other.kind, other.operation_id, other.sequence)
        )

    def __hash__(self) -> int:
        return hash((self.kind, self.operation_id, self.sequence))


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


def mint_ref(
    secret: bytes,
    purpose: RefPurpose,
    authorization: AuthorizationBinding,
    *,
    ar_session_id: str,
    bridge_epoch: str,
    identity: OperationIdentity,
    withdraw_request_id: str | None = None,
    asset_id: str | None = None,
) -> str:
    """Mint one purpose-branded opaque reference over exact identity fields."""

    payload: dict[str, object] = {
        "v": REF_SCHEMA_VERSION,
        "purpose": purpose,
        "principal": authorization.principal_id,
        "tenant": authorization.tenant_id,
        "session": ar_session_id,
        "epoch": bridge_epoch,
        "kind": identity.kind,
        "operationId": identity.operation_id,
        "sequence": identity.sequence,
    }
    if withdraw_request_id is not None:
        payload["withdrawRequestId"] = withdraw_request_id
    if asset_id is not None:
        payload["assetId"] = asset_id
    body = _b64encode(_canonical(payload))
    return f"{_PREFIX_BY_PURPOSE[purpose]}{body}.{_sign(secret, body)}"


def decode_ref(
    secret: bytes,
    token: str,
    purpose: RefPurpose,
    authorization: AuthorizationBinding,
    *,
    ar_session_id: str,
    bridge_epoch: str,
) -> dict[str, object]:
    """Decode and fully re-bind one opaque reference; every failure is typed."""

    prefix = _PREFIX_BY_PURPOSE[purpose]
    if not token.startswith(prefix):
        raise RefInvalidError("control reference purpose does not match this route")
    body, separator, signature = token[len(prefix) :].rpartition(".")
    if not separator or not body or not signature:
        raise RefInvalidError("control reference is malformed")
    if not hmac.compare_digest(_sign(secret, body), signature):
        raise RefInvalidError("control reference failed integrity verification")
    try:
        payload = json.loads(_b64decode(body))
    except (ValueError, UnicodeDecodeError) as exc:
        raise RefInvalidError("control reference payload is not decodable") from exc
    if not isinstance(payload, dict):
        raise RefInvalidError("control reference payload is not an object")
    _check_payload(
        payload,
        purpose,
        authorization,
        ar_session_id=ar_session_id,
        bridge_epoch=bridge_epoch,
    )
    return payload


def _check_payload(
    payload: dict[str, object],
    purpose: RefPurpose,
    authorization: AuthorizationBinding,
    *,
    ar_session_id: str,
    bridge_epoch: str,
) -> None:
    """Re-validate the decoded payload's full binding on every wire."""

    if payload.get("v") != REF_SCHEMA_VERSION:
        raise RefInvalidError("control reference schema version is unsupported")
    if payload.get("purpose") != purpose:
        raise RefInvalidError("control reference purpose does not match this route")
    if (
        payload.get("principal") != authorization.principal_id
        or payload.get("tenant") != authorization.tenant_id
    ):
        raise RefAuthorizationError("control reference does not belong to this caller")
    if payload.get("session") != ar_session_id:
        raise RefAuthorizationError("control reference does not belong to this AR session")
    if payload.get("epoch") != bridge_epoch:
        raise RefEpochMismatchError("control reference was minted under a different bridge epoch")


def ref_identity(payload: dict[str, object]) -> OperationIdentity:
    """Extract the operation identity from a verified payload (typed)."""

    kind = payload.get("kind")
    operation_id = payload.get("operationId")
    sequence = payload.get("sequence")
    if not isinstance(kind, str) or not kind:
        raise RefInvalidError("control reference kind is invalid")
    if not isinstance(operation_id, str) or not operation_id:
        raise RefInvalidError("control reference operationId is invalid")
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
        raise RefInvalidError("control reference sequence is invalid")
    return OperationIdentity(kind=kind, operation_id=operation_id, sequence=sequence)


__all__ = [
    "ControlRefError",
    "OperationIdentity",
    "RefAuthorizationError",
    "RefEpochMismatchError",
    "RefInvalidError",
    "RefPurpose",
    "decode_ref",
    "mint_ref",
    "ref_identity",
]
