"""Blocking exact-session client for the hosted harness control socket.

Serving request handlers and MCP payload builders are synchronous today.  This client keeps their
protocol boundary explicit without starting nested event loops or falling back to terminal input.
Every response is validated because the peer is a long-lived subprocess, not trusted in-process
state.
"""

from __future__ import annotations

import json
import socket
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, cast

from agents_remember.errors import HarnessControlClientError, HarnessControlError
from agents_remember.serving.harness_capabilities import (
    CapabilitySnapshot,
    SetResult,
    capability_snapshot_from_json,
    set_result_from_json,
)
from agents_remember.serving.harness_control_ipc import MAX_CONTROL_MESSAGE_BYTES
from agents_remember.serving.harness_control_models import (
    CONTROL_PROTOCOL_VERSION,
    AcceptanceState,
    ActivityState,
    AdapterSnapshot,
    ControlIdentity,
    ControlState,
    PendingInteraction,
    ReconciliationResult,
    ReconciliationState,
    SubmissionReceipt,
    SubmissionSource,
)

SET_CONTROL_TIMEOUT_SECONDS = 35.0
"""Bound a native setter above Claude's 30-second correlated acceptance window."""


class ControlledSession(Protocol):
    @property
    def id(self) -> str: ...

    @property
    def tmux_name(self) -> str: ...

    @property
    def created_at(self) -> str: ...

    @property
    def control_endpoint(self) -> Path | None: ...


def control_identity(entry: ControlledSession) -> ControlIdentity:
    return ControlIdentity(
        ar_session_id=entry.id,
        tmux_name=entry.tmux_name,
        created_at=entry.created_at,
    )


def read_control_snapshot(entry: ControlledSession) -> AdapterSnapshot:
    result = request_control(entry, "snapshot")
    if not isinstance(result, Mapping):
        raise HarnessControlError("control snapshot response must be an object")
    if result.get("protocol") != CONTROL_PROTOCOL_VERSION:
        raise HarnessControlError("control snapshot protocol version mismatch")
    raw_snapshot = result.get("snapshot")
    if not isinstance(raw_snapshot, Mapping):
        raise HarnessControlError("control snapshot response requires snapshot")
    snapshot = _snapshot(raw_snapshot)
    if snapshot.identity != control_identity(entry):
        raise HarnessControlError("control snapshot identity does not match the catalog row")
    return snapshot


def read_control_capabilities(entry: ControlledSession) -> CapabilitySnapshot:
    """Read the live adapter's normalized, model-gated capability snapshot."""

    result = request_control(entry, "advertise")
    try:
        return capability_snapshot_from_json(result)
    except (HarnessControlError, ValueError) as exc:
        raise HarnessControlError(f"control capability response is invalid: {exc}") from exc


def set_control_model(entry: ControlledSession, model_key: str) -> SetResult:
    return _set_control_value(entry, "set-model", "modelKey", model_key)


def set_control_effort(entry: ControlledSession, effort: str) -> SetResult:
    return _set_control_value(entry, "set-effort", "effort", effort)


def submit_control_prompt(
    entry: ControlledSession,
    text: str,
    *,
    source: SubmissionSource,
    request_id: str,
    submitted_at: str | None = None,
) -> SubmissionReceipt:
    stamp = submitted_at or datetime.now(UTC).isoformat()
    try:
        result = request_control(
            entry,
            "submit",
            {
                "requestId": request_id,
                "source": source,
                "text": text,
                "submittedAt": stamp,
            },
        )
    except HarnessControlClientError as exc:
        if not exc.may_have_sent:
            raise
        return SubmissionReceipt(
            request_id=request_id,
            acceptance="unknown",
            submitted_at=stamp,
            detail=f"control submission response was lost after request bytes were sent: {exc}",
        )
    try:
        return _submission_receipt(result, request_id=request_id)
    except HarnessControlError as exc:
        return SubmissionReceipt(
            request_id=request_id,
            acceptance="unknown",
            submitted_at=stamp,
            detail=f"control submission returned incoherent post-dispatch evidence: {exc}",
        )


def reconcile_control_prompt(
    entry: ControlledSession, request_id: str
) -> ReconciliationResult:
    result = request_control(entry, "reconcile", {"requestId": request_id})
    if not isinstance(result, Mapping):
        raise HarnessControlError("control reconciliation response must be an object")
    state = result.get("state")
    if state not in {"accepted", "rejected", "unresolved", "unsupported"}:
        raise HarnessControlError("control reconciliation response has invalid state")
    response_request = _required_text(result, "requestId")
    if response_request != request_id:
        raise HarnessControlError("control reconciliation response request id mismatch")
    return ReconciliationResult(
        request_id=response_request,
        state=cast(ReconciliationState, state),
        reconciled_at=_required_text(result, "reconciledAt"),
        vendor_correlation_id=_optional_text(result, "vendorCorrelationId"),
        detail=_optional_text(result, "detail"),
        raw=_object(result.get("raw")),
    )


def respond_control_interaction(
    entry: ControlledSession,
    *,
    interaction_id: str,
    response: str,
    responded_at: str | None = None,
) -> AdapterSnapshot:
    result = request_control(
        entry,
        "respond",
        {
            "interactionId": interaction_id,
            "response": response,
            "respondedAt": responded_at or datetime.now(UTC).isoformat(),
        },
    )
    if not isinstance(result, Mapping):
        raise HarnessControlError("control interaction response must be an object")
    snapshot = _snapshot(result)
    if snapshot.identity != control_identity(entry):
        raise HarnessControlError("interaction response identity does not match the catalog row")
    return snapshot


def read_control_transcript(
    entry: ControlledSession, *, after_sequence: int = 0, limit: int = 500
) -> tuple[Mapping[str, object], ...]:
    result = request_control(
        entry,
        "transcript",
        {"afterSequence": after_sequence, "limit": limit},
    )
    if not isinstance(result, Mapping) or not isinstance(result.get("entries"), list):
        raise HarnessControlError("control transcript response requires entries")
    entries = result["entries"]
    if not all(isinstance(item, Mapping) for item in entries):
        raise HarnessControlError("control transcript entries must be objects")
    return tuple(cast(Mapping[str, object], item) for item in entries)


def stop_control_session(entry: ControlledSession, *, forced: bool = False) -> None:
    request_control(entry, "stop", {"mode": "forced" if forced else "graceful"})


def request_control(
    entry: ControlledSession,
    action: str,
    payload: Mapping[str, object] | None = None,
    *,
    timeout_seconds: float = 2.0,
) -> object:
    endpoint = entry.control_endpoint
    if endpoint is None:
        raise HarnessControlError("catalog session has no protocol control endpoint")
    encoded = _encode_control_request(entry, action, payload)
    response = _exchange_control(endpoint, encoded, timeout_seconds=timeout_seconds)
    return _decode_control_response(response)


def _encode_control_request(
    entry: ControlledSession,
    action: str,
    payload: Mapping[str, object] | None,
) -> bytes:
    request = {
        "protocol": CONTROL_PROTOCOL_VERSION,
        "identity": control_identity(entry).to_json(),
        "action": action,
        "payload": dict(payload or {}),
    }
    encoded = json.dumps(request, separators=(",", ":"), ensure_ascii=False).encode() + b"\n"
    if len(encoded) > MAX_CONTROL_MESSAGE_BYTES:
        raise HarnessControlError("control request exceeds the message limit")
    return encoded


def _exchange_control(endpoint: Path, encoded: bytes, *, timeout_seconds: float) -> bytes:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(timeout_seconds)
        try:
            client.connect(str(endpoint))
        except (OSError, TimeoutError) as exc:
            raise HarnessControlClientError(
                f"control endpoint unavailable before write: {exc}", may_have_sent=False
            ) from exc
        bytes_handed = False
        try:
            first_write = client.send(encoded)
            if first_write <= 0:
                raise OSError("control socket accepted no request bytes")
            bytes_handed = True
            client.sendall(encoded[first_write:])
            return _read_line(client)
        except (HarnessControlError, OSError, TimeoutError) as exc:
            stage = (
                "after request bytes were sent"
                if bytes_handed
                else "before any request bytes were accepted"
            )
            raise HarnessControlClientError(
                f"control endpoint unavailable {stage}: {exc}",
                may_have_sent=bytes_handed,
            ) from exc


def _decode_control_response(response: bytes) -> object:
    try:
        raw = json.loads(response)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HarnessControlClientError(
            "control endpoint returned malformed JSON", may_have_sent=True
        ) from exc
    if not isinstance(raw, dict):
        raise HarnessControlClientError(
            "control endpoint response must be an object", may_have_sent=True
        )
    if raw.get("ok") is not True:
        raise HarnessControlError(str(raw.get("error") or "control request failed"))
    return raw.get("result")


def _set_control_value(
    entry: ControlledSession,
    action: str,
    payload_key: str,
    requested_value: str,
) -> SetResult:
    try:
        result = request_control(
            entry,
            action,
            {payload_key: requested_value},
            timeout_seconds=SET_CONTROL_TIMEOUT_SECONDS,
        )
    except HarnessControlClientError as exc:
        if not exc.may_have_sent:
            raise
        return _unknown_set_result(requested_value, str(exc))
    try:
        parsed = set_result_from_json(result)
    except (HarnessControlError, ValueError) as exc:
        return _unknown_set_result(requested_value, f"invalid setter response: {exc}")
    if parsed.requested_value != requested_value:
        return _unknown_set_result(
            requested_value,
            f"setter response request mismatch: {parsed.requested_value!r}",
        )
    return parsed


def _unknown_set_result(requested_value: str, detail: str) -> SetResult:
    return SetResult(
        ok=False,
        acceptance="unknown",
        requested_value=requested_value,
        detail=f"setter outcome is unknown after request bytes were sent: {detail}",
    )


def _submission_receipt(result: object, *, request_id: str) -> SubmissionReceipt:
    if not isinstance(result, Mapping):
        raise HarnessControlError("control submission response must be an object")
    acceptance = result.get("acceptance")
    if acceptance not in {"immediate", "queued", "rejected", "unknown", "unsupported"}:
        raise HarnessControlError("control submission response has invalid acceptance")
    response_request = _required_text(result, "requestId")
    if response_request != request_id:
        raise HarnessControlError("control submission response request id mismatch")
    return SubmissionReceipt(
        request_id=response_request,
        acceptance=cast(AcceptanceState, acceptance),
        submitted_at=_required_text(result, "submittedAt"),
        vendor_correlation_id=_optional_text(result, "vendorCorrelationId"),
        accepted_at=_optional_text(result, "acceptedAt"),
        detail=_optional_text(result, "detail"),
        raw=_object(result.get("raw")),
    )


def _read_line(client: socket.socket) -> bytes:
    data = bytearray()
    while b"\n" not in data:
        remaining = MAX_CONTROL_MESSAGE_BYTES + 1 - len(data)
        if remaining <= 0:
            raise HarnessControlError("control response exceeds the message limit")
        chunk = client.recv(min(65536, remaining))
        if not chunk:
            break
        data.extend(chunk)
        if len(data) > MAX_CONTROL_MESSAGE_BYTES:
            raise HarnessControlError("control response exceeds the message limit")
    if b"\n" not in data:
        raise HarnessControlError("control endpoint returned an unterminated response")
    return bytes(data[: data.index(b"\n")])


def _snapshot(raw: Mapping[str, object]) -> AdapterSnapshot:
    identity_raw = raw.get("identity")
    if not isinstance(identity_raw, Mapping):
        raise HarnessControlError("adapter snapshot requires identity")
    control = raw.get("control")
    activity = raw.get("activity")
    acceptance = raw.get("acceptance")
    if control not in {"starting", "ready", "disconnected", "failed", "unsupported"}:
        raise HarnessControlError("adapter snapshot has invalid control state")
    if activity not in {"idle", "running", "blocked", "settling", "unknown"}:
        raise HarnessControlError("adapter snapshot has invalid activity state")
    if acceptance not in {"immediate", "queued", "rejected", "unknown", "unsupported"}:
        raise HarnessControlError("adapter snapshot has invalid acceptance state")
    pending_raw = raw.get("pendingInteraction")
    pending = None
    if pending_raw is not None:
        if not isinstance(pending_raw, Mapping):
            raise HarnessControlError("pending interaction must be an object")
        choices_raw = pending_raw.get("choices", [])
        if not isinstance(choices_raw, list) or not all(
            isinstance(choice, str) for choice in choices_raw
        ):
            raise HarnessControlError("pending interaction choices must be strings")
        pending = PendingInteraction(
            interaction_id=_required_text(pending_raw, "interactionId"),
            kind=_required_text(pending_raw, "kind"),
            prompt=_required_text(pending_raw, "prompt"),
            created_at=_required_text(pending_raw, "createdAt"),
            choices=tuple(choices_raw),
            raw=_object(pending_raw.get("raw")),
        )
    sequence = raw.get("lastEventSequence", 0)
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 0:
        raise HarnessControlError("adapter snapshot sequence must be a non-negative integer")
    return AdapterSnapshot(
        identity=ControlIdentity.from_json(identity_raw),
        control=cast(ControlState, control),
        activity=cast(ActivityState, activity),
        acceptance=cast(AcceptanceState, acceptance),
        vendor_session_id=_optional_text(raw, "vendorSessionId"),
        pending_interaction=pending,
        last_event_sequence=sequence,
        raw=_object(raw.get("raw")),
    )


def _required_text(raw: Mapping[str, object], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise HarnessControlError(f"control response requires non-empty {key}")
    return value


def _optional_text(raw: Mapping[str, object], key: str) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise HarnessControlError(f"control response {key} must be a string")
    return value


def _object(raw: object) -> Mapping[str, object]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping) or not all(isinstance(key, str) for key in raw):
        raise HarnessControlError("control response raw detail must be an object")
    return cast(Mapping[str, object], raw)
