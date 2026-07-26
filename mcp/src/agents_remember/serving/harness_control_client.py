"""Blocking exact-session client for the hosted harness control socket.

Serving request handlers and MCP payload builders are synchronous today.  This client keeps their
protocol boundary explicit without starting nested event loops or falling back to terminal input.
Every response is validated because the peer is a long-lived subprocess, not trusted in-process
state.
"""

from __future__ import annotations

import contextlib
import errno
import json
import socket
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, cast

from agents_remember.errors import (
    HarnessBridgeEpochMismatchError,
    HarnessControlClientError,
    HarnessControlError,
    HarnessInteractionNotPendingError,
    HarnessRequestConflictError,
)
from agents_remember.serving.harness_capabilities import (
    CapabilitySnapshot,
    SetResult,
    capability_snapshot_from_json,
    set_result_from_json,
)
from agents_remember.serving.harness_control_ipc import MAX_CONTROL_MESSAGE_BYTES
from agents_remember.serving.harness_control_models import (
    CONTROL_PROTOCOL_VERSION,
    MAX_OPERATION_TIMELINE_PAGE,
    AcceptanceState,
    ActivityState,
    AdapterSnapshot,
    AssetReference,
    ControlIdentity,
    ControlOperationKind,
    ControlOperationRef,
    ControlState,
    EvidenceFrame,
    EvidencePage,
    InteractionQuestion,
    InteractionQuestionOption,
    InterruptAcknowledgement,
    InterruptResult,
    NativeEvidenceFrame,
    NativeEvidencePage,
    OperationTimeline,
    OperationTimelineItem,
    PendingInteraction,
    ReconciliationResult,
    ReconciliationState,
    SubmissionAuthorityDescriptor,
    SubmissionLifecycleState,
    SubmissionLookup,
    SubmissionProvenance,
    SubmissionProvenanceBatch,
    SubmissionReceipt,
    SubmissionSource,
    SubmissionStatus,
    SubmissionStatusBatch,
    WithdrawalRecovery,
    WithdrawalResult,
)

SET_CONTROL_TIMEOUT_SECONDS = 35.0
"""Bound a native setter above Claude's 30-second correlated acceptance window."""

EVIDENCE_PAGE_TIMEOUT_SECONDS = 35.0
"""Bound a native history page (e.g. Codex thread/read) above the 2-second control default."""

SUBMIT_TIMEOUT_SECONDS = 10.0
"""Bound a prompt submit above the harness CLI's replay echo (measured 2-10s), not the 2s default.

The bridge legitimately waits for the harness CLI's replay echo before answering
a submit; the 2.0s control default timed out first and degraded accepted messages to
acceptance="unknown", pushing the frontend into a spurious 120s reconcile loop. Only submit waits
this long -- snapshot/evidence/capability reads keep failing fast at 2.0s, and >10s still degrades
honestly to unknown -> reconcile, under the adapter's 30s acceptance_timeout.
"""


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


def _wire_asset(asset: object) -> dict[str, object]:
    if not isinstance(asset, Mapping):
        raise HarnessControlError("submit asset must be an object")
    return dict(asset)


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


def read_submission_authority(entry: ControlledSession) -> SubmissionAuthorityDescriptor:
    result = request_control(entry, "submission-authority")
    if not isinstance(result, Mapping):
        raise HarnessControlError("submission authority response must be an object")
    return SubmissionAuthorityDescriptor(bridge_epoch=_required_text(result, "bridgeEpoch"))


def read_submission_status(
    entry: ControlledSession,
    *,
    expected_bridge_epoch: str,
    request_ids: tuple[str, ...],
) -> SubmissionStatusBatch:
    result = request_control(
        entry,
        "submission-status",
        {
            "expectedBridgeEpoch": expected_bridge_epoch,
            "requestIds": list(request_ids),
        },
    )
    return _submission_status_batch(
        result,
        expected_bridge_epoch=expected_bridge_epoch,
        request_ids=request_ids,
    )


def withdraw_control_submission(
    entry: ControlledSession,
    *,
    expected_bridge_epoch: str,
    request_id: str,
) -> WithdrawalResult:
    result = request_control(
        entry,
        "withdraw",
        {
            "expectedBridgeEpoch": expected_bridge_epoch,
            "requestId": request_id,
        },
    )
    return _withdrawal_result(result, request_id=request_id)


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
    expected_bridge_epoch: str | None = None,
    assets: Sequence[Mapping[str, object]] | None = None,
) -> SubmissionReceipt:
    stamp = submitted_at or datetime.now(UTC).isoformat()
    payload = _submit_payload(
        text,
        source=source,
        request_id=request_id,
        submitted_at=stamp,
        expected_bridge_epoch=expected_bridge_epoch,
        assets=assets,
    )
    try:
        result = request_control(
            entry,
            "submit",
            payload,
            timeout_seconds=SUBMIT_TIMEOUT_SECONDS,
        )
    except HarnessControlClientError as exc:
        if not exc.may_have_sent:
            raise
        return SubmissionReceipt(
            request_id=request_id,
            acceptance="unknown",
            submitted_at=stamp,
            detail=f"control submission response was lost after request bytes were sent: {exc}",
            bridge_epoch=expected_bridge_epoch,
        )
    try:
        receipt = _submission_receipt(result, request_id=request_id)
        if expected_bridge_epoch is not None and receipt.bridge_epoch != expected_bridge_epoch:
            raise HarnessControlError("control submission response bridge epoch mismatch")
        return receipt
    except HarnessControlError as exc:
        return SubmissionReceipt(
            request_id=request_id,
            acceptance="unknown",
            submitted_at=stamp,
            detail=f"control submission returned incoherent post-dispatch evidence: {exc}",
            bridge_epoch=expected_bridge_epoch,
        )


def _submit_payload(
    text: str,
    *,
    source: SubmissionSource,
    request_id: str,
    submitted_at: str,
    expected_bridge_epoch: str | None,
    assets: Sequence[Mapping[str, object]] | None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "requestId": request_id,
        "source": source,
        "text": text,
        "submittedAt": submitted_at,
    }
    if assets is not None:
        if isinstance(assets, (str, bytes)) or not isinstance(assets, Sequence):
            raise HarnessControlError("submit assets must be a sequence of asset objects")
        payload["assets"] = [_wire_asset(asset) for asset in assets]
    if expected_bridge_epoch is not None:
        payload["expectedBridgeEpoch"] = expected_bridge_epoch
    return payload


def reconcile_control_prompt(
    entry: ControlledSession,
    request_id: str,
    *,
    expected_bridge_epoch: str | None = None,
) -> ReconciliationResult:
    payload: dict[str, object] = {"requestId": request_id}
    if expected_bridge_epoch is not None:
        payload["expectedBridgeEpoch"] = expected_bridge_epoch
    result = request_control(entry, "reconcile", payload)
    if not isinstance(result, Mapping):
        raise HarnessControlError("control reconciliation response must be an object")
    state = result.get("state")
    if state not in {"accepted", "rejected", "unresolved", "unsupported"}:
        raise HarnessControlError("control reconciliation response has invalid state")
    response_request = _required_text(result, "requestId")
    if response_request != request_id:
        raise HarnessControlError("control reconciliation response request id mismatch")
    bridge_epoch = _optional_text(result, "bridgeEpoch")
    if expected_bridge_epoch is not None and bridge_epoch != expected_bridge_epoch:
        raise HarnessControlError("control reconciliation response bridge epoch mismatch")
    return ReconciliationResult(
        request_id=response_request,
        state=cast(ReconciliationState, state),
        reconciled_at=_required_text(result, "reconciledAt"),
        vendor_correlation_id=_optional_text(result, "vendorCorrelationId"),
        detail=_optional_text(result, "detail"),
        raw=_object(result.get("raw")),
        bridge_epoch=bridge_epoch,
        submission_state=_submission_state(result.get("submissionState"), optional=True),
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


def read_control_evidence(
    entry: ControlledSession,
    *,
    after_sequence: int = 0,
    limit: int = 500,
    expected_bridge_epoch: str | None = None,
) -> EvidencePage:
    """Page the deque-domain evidence buffer; rejects native-cursor coordinates typed."""

    if isinstance(after_sequence, str):
        raise HarnessControlError(
            "native-cursor coordinates are invalid in the deque evidence domain"
        )
    _require_coordinate(after_sequence, "evidence after_sequence")
    _require_page_limit(limit)
    result = request_control(
        entry,
        "evidence",
        {"afterSequence": after_sequence, "limit": limit},
    )
    return _evidence_page(result, expected_bridge_epoch=expected_bridge_epoch)


def read_control_native_page(
    entry: ControlledSession,
    *,
    cursor: str | None = None,
    limit: int = 200,
    expected_bridge_epoch: str | None = None,
    thread_id: str | None = None,
) -> NativeEvidencePage:
    """Page harness-native history; rejects adapter-sequence coordinates typed.

    ``thread_id`` selects the native thread on multiplexed harnesses;
    ``None`` reads the parent/session thread exactly as before.
    """

    if isinstance(cursor, int) and not isinstance(cursor, bool):
        raise HarnessControlError(
            "adapter-sequence coordinates are invalid in the native evidence domain"
        )
    if cursor is not None and not isinstance(cursor, str):
        raise HarnessControlError("native evidence cursor must be opaque text or null")
    _require_page_limit(limit)
    payload: dict[str, object] = {"limit": limit}
    if cursor is not None:
        payload["cursor"] = cursor
    if thread_id is not None:
        payload["threadId"] = thread_id
    result = request_control(
        entry,
        "evidence-native-page",
        payload,
        timeout_seconds=EVIDENCE_PAGE_TIMEOUT_SECONDS,
    )
    return _native_evidence_page(result, expected_bridge_epoch=expected_bridge_epoch)


def read_submission_provenance(
    entry: ControlledSession,
    *,
    expected_bridge_epoch: str,
    request_ids: tuple[str, ...],
) -> SubmissionProvenanceBatch:
    result = request_control(
        entry,
        "submission-provenance",
        {
            "expectedBridgeEpoch": expected_bridge_epoch,
            "requestIds": list(request_ids),
        },
    )
    return _submission_provenance_batch(
        result,
        expected_bridge_epoch=expected_bridge_epoch,
        request_ids=request_ids,
    )


def interrupt_control(
    entry: ControlledSession,
    *,
    expected_bridge_epoch: str,
    turn_id: str | None = None,
    expected_operation_id: str | None = None,
) -> InterruptResult:
    """One epoch-guarded native interrupt write; acknowledgement, never settlement."""

    payload: dict[str, object] = {"expectedBridgeEpoch": expected_bridge_epoch}
    if turn_id is not None:
        payload["turnId"] = turn_id
    if expected_operation_id is not None:
        payload["expectedOperationId"] = expected_operation_id
    result = request_control(
        entry,
        "interrupt",
        payload,
        timeout_seconds=SET_CONTROL_TIMEOUT_SECONDS,
    )
    return _interrupt_result(result, expected_bridge_epoch=expected_bridge_epoch)


def read_operation_timeline(
    entry: ControlledSession,
    *,
    expected_bridge_epoch: str,
    after_sequence: int = 0,
    limit: int = MAX_OPERATION_TIMELINE_PAGE,
) -> OperationTimeline:
    """Page the retained ledger; completeness is the union through latestSequence."""

    if isinstance(after_sequence, str):
        raise HarnessControlError(
            "opaque cursor coordinates are invalid in the operation timeline domain"
        )
    _require_coordinate(after_sequence, "operation timeline after_sequence")
    _require_page_limit(limit)
    result = request_control(
        entry,
        "operation-timeline",
        {
            "expectedBridgeEpoch": expected_bridge_epoch,
            "afterSequence": after_sequence,
            "limit": limit,
        },
    )
    return _operation_timeline(result, expected_bridge_epoch=expected_bridge_epoch)


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


def _connect_unavailable_detail(endpoint: Path, exc: BaseException) -> str:
    """Map a control-socket connect failure to an honest lifecycle note (no raw errno surprise).

    A controlled runner that already exited leaves either an absent socket
    (``ENOENT``) or, on an unclean exit, a stale socket file with nothing listening
    (``ECONNREFUSED`` — the ``[Errno 111] Connection refused`` banner). Both mean
    the same designed thing: there is no live control endpoint to stop. The stale socket is unlinked
    best-effort so the next attempt reads the absent (``ENOENT``) case cleanly rather than repeating
    the refused surprise.
    """

    error_number = getattr(exc, "errno", None)
    if error_number == errno.ECONNREFUSED:
        with contextlib.suppress(OSError):
            endpoint.unlink()
        return "the controlled runner already exited (stale control socket, nothing listening)"
    if error_number == errno.ENOENT:
        return "the controlled runner already exited (control socket absent)"
    if isinstance(exc, TimeoutError):
        return "the control endpoint did not accept a connection within the timeout"
    return "the control endpoint could not be reached"


def _exchange_control(endpoint: Path, encoded: bytes, *, timeout_seconds: float) -> bytes:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(timeout_seconds)
        try:
            client.connect(str(endpoint))
        except (OSError, TimeoutError) as exc:
            raise HarnessControlClientError(
                f"control endpoint unavailable before write: {_connect_unavailable_detail(endpoint, exc)}",
                may_have_sent=False,
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
        detail = str(raw.get("error") or "control request failed")
        if raw.get("status") == "bridge-epoch-mismatch":
            raise HarnessBridgeEpochMismatchError(
                _required_text(raw, "expectedBridgeEpoch"),
                _required_text(raw, "actualBridgeEpoch"),
            )
        if raw.get("status") == "request-id-conflict":
            raise HarnessRequestConflictError(detail)
        if raw.get("status") == "interaction-not-pending":
            raise HarnessInteractionNotPendingError(detail)
        raise HarnessControlError(detail)
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
        bridge_epoch=_optional_text(result, "bridgeEpoch"),
    )


def _submission_status_batch(
    result: object,
    *,
    expected_bridge_epoch: str,
    request_ids: tuple[str, ...],
) -> SubmissionStatusBatch:
    if not isinstance(result, Mapping):
        raise HarnessControlError("submission status response must be an object")
    bridge_epoch = _required_text(result, "bridgeEpoch")
    if bridge_epoch != expected_bridge_epoch:
        raise HarnessControlError("submission status response bridge epoch mismatch")
    raw_submissions = result.get("submissions")
    if not isinstance(raw_submissions, list) or len(raw_submissions) != len(request_ids):
        raise HarnessControlError("submission status response has the wrong result count")
    lookups: list[SubmissionLookup] = []
    for expected_id, raw_lookup in zip(request_ids, raw_submissions, strict=True):
        if not isinstance(raw_lookup, Mapping):
            raise HarnessControlError("submission lookup must be an object")
        request_id = _required_text(raw_lookup, "requestId")
        if request_id != expected_id:
            raise HarnessControlError("submission lookup request id or order mismatch")
        outcome = raw_lookup.get("outcome")
        if outcome == "not-found":
            lookups.append(SubmissionLookup(request_id=request_id, outcome="not-found"))
            continue
        if outcome != "found" or not isinstance(raw_lookup.get("submission"), Mapping):
            raise HarnessControlError("submission lookup has invalid outcome or evidence")
        raw_status = cast(Mapping[str, object], raw_lookup["submission"])
        withdrawable = raw_status.get("withdrawable")
        if not isinstance(withdrawable, bool):
            raise HarnessControlError("submission status withdrawable must be boolean")
        state = _submission_state(raw_status.get("state"))
        if state is None:
            raise HarnessControlError("found submission status requires lifecycle state")
        lookups.append(
            SubmissionLookup(
                request_id=request_id,
                outcome="found",
                submission=SubmissionStatus(
                    request_id=request_id,
                    state=state,
                    submitted_at=_required_text(raw_status, "submittedAt"),
                    updated_at=_required_text(raw_status, "updatedAt"),
                    accepted_at=_optional_text(raw_status, "acceptedAt"),
                    withdrawable=withdrawable,
                    detail=_optional_text(raw_status, "detail"),
                ),
            )
        )
    return SubmissionStatusBatch(bridge_epoch=bridge_epoch, submissions=tuple(lookups))


def _withdrawal_result(result: object, *, request_id: str) -> WithdrawalResult:
    if not isinstance(result, Mapping):
        raise HarnessControlError("withdrawal response must be an object")
    response_id = _required_text(result, "requestId")
    if response_id != request_id:
        raise HarnessControlError("withdrawal response request id mismatch")
    outcome = result.get("outcome")
    if outcome not in {"withdrawn", "not-withdrawable", "not-found"}:
        raise HarnessControlError("withdrawal response has invalid outcome")
    return WithdrawalResult(
        request_id=request_id,
        outcome=outcome,
        state=_submission_state(result.get("state"), optional=True),
        withdrawn_at=_optional_text(result, "withdrawnAt"),
        detail=_optional_text(result, "detail"),
        recovery=_withdrawal_recovery(result.get("recovery")),
    )


def _withdrawal_recovery(raw_recovery: object) -> WithdrawalRecovery | None:
    if raw_recovery is None:
        return None
    if not isinstance(raw_recovery, Mapping):
        raise HarnessControlError("withdrawal recovery must be an object when present")
    text = raw_recovery.get("text")
    if text is not None and not isinstance(text, str):
        raise HarnessControlError("withdrawal recovery text must be text or null")
    raw_assets = raw_recovery.get("assets")
    if not isinstance(raw_assets, list):
        raise HarnessControlError("withdrawal recovery requires an assets list")
    return WithdrawalRecovery(
        text=text,
        assets=tuple(_asset_reference(item) for item in raw_assets),
    )


def _asset_reference(raw: object) -> AssetReference:
    if not isinstance(raw, Mapping):
        raise HarnessControlError("asset reference must be an object")
    byte_size = raw.get("byteSize")
    if not isinstance(byte_size, int) or isinstance(byte_size, bool) or byte_size < 1:
        raise HarnessControlError("asset reference byteSize must be a positive integer")
    return AssetReference(
        asset_id=_required_text(raw, "assetId"),
        mime_type=_required_text(raw, "mimeType"),
        byte_size=byte_size,
        sha256=_required_text(raw, "sha256"),
    )


def _interrupt_result(result: object, *, expected_bridge_epoch: str) -> InterruptResult:
    if not isinstance(result, Mapping):
        raise HarnessControlError("control interrupt response must be an object")
    acknowledgement = result.get("acknowledgement")
    if acknowledgement not in {"accepted", "rejected", "unsupported", "unknown"}:
        raise HarnessControlError("control interrupt response has invalid acknowledgement")
    operation = result.get("operation")
    parsed_operation: ControlOperationRef | None = None
    if operation is not None:
        if not isinstance(operation, Mapping):
            raise HarnessControlError("control interrupt operation must be an object or null")
        parsed_operation = ControlOperationRef.from_json(operation)
    return InterruptResult(
        acknowledgement=cast(InterruptAcknowledgement, acknowledgement),
        bridge_epoch=_evidence_bridge_epoch(result, expected_bridge_epoch=expected_bridge_epoch),
        operation=parsed_operation,
        vendor_correlation_id=_optional_text(result, "vendorCorrelationId"),
        detail=_optional_text(result, "detail"),
        raw=_object(result.get("raw")),
    )


def _operation_timeline(result: object, *, expected_bridge_epoch: str) -> OperationTimeline:
    if not isinstance(result, Mapping):
        raise HarnessControlError("operation timeline response must be an object")
    bridge_epoch = _evidence_bridge_epoch(result, expected_bridge_epoch=expected_bridge_epoch)
    latest = _required_non_negative_int(result, "latestSequence")
    evicted = _required_non_negative_int(result, "evictedBeforeSequence")
    if evicted > latest:
        raise HarnessControlError("operation timeline eviction floor exceeds its high-water mark")
    truncated = result.get("truncated")
    if not isinstance(truncated, bool):
        raise HarnessControlError("operation timeline truncated must be boolean")
    items = _operation_timeline_items(result.get("items"), truncated=truncated)
    if items and latest < items[-1].sequence:
        raise HarnessControlError("operation timeline latestSequence precedes its last item")
    return OperationTimeline(
        bridge_epoch=bridge_epoch,
        latest_sequence=latest,
        evicted_before_sequence=evicted,
        truncated=truncated,
        items=items,
    )


def _operation_timeline_items(
    raw_items: object, *, truncated: bool
) -> tuple[OperationTimelineItem, ...]:
    if not isinstance(raw_items, list):
        raise HarnessControlError("operation timeline response requires items")
    if truncated and not raw_items:
        raise HarnessControlError("operation timeline empty page cannot be truncated")
    items: list[OperationTimelineItem] = []
    previous = 0
    for raw_item in raw_items:
        item = _operation_timeline_item(raw_item)
        if item.sequence <= previous:
            raise HarnessControlError("operation timeline items must increase monotonically")
        previous = item.sequence
        items.append(item)
    return tuple(items)


def _operation_timeline_item(raw_item: object) -> OperationTimelineItem:
    if not isinstance(raw_item, Mapping):
        raise HarnessControlError("operation timeline item must be an object")
    kind = raw_item.get("kind")
    if kind not in {"prompt", "set-model", "set-effort"}:
        raise HarnessControlError("operation timeline item has invalid kind")
    source = raw_item.get("source")
    if source is not None and source not in {"cockpit", "terminal", "durable"}:
        raise HarnessControlError("operation timeline item has invalid source")
    sequence = raw_item.get("sequence")
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
        raise HarnessControlError("operation timeline item requires a positive sequence")
    state = _submission_state(raw_item.get("state"))
    if state is None:
        raise HarnessControlError("operation timeline item requires lifecycle state")
    digest_present = raw_item.get("payloadDigestPresent")
    if not isinstance(digest_present, bool):
        raise HarnessControlError("operation timeline payloadDigestPresent must be boolean")
    return OperationTimelineItem(
        operation_id=_required_text(raw_item, "operationId"),
        kind=cast(ControlOperationKind, kind),
        source=cast(SubmissionSource | None, source),
        state=state,
        sequence=sequence,
        submitted_at=_required_text(raw_item, "submittedAt"),
        updated_at=_required_text(raw_item, "updatedAt"),
        accepted_at=_optional_text(raw_item, "acceptedAt"),
        payload_digest_present=digest_present,
        vendor_correlation_id=_optional_text(raw_item, "vendorCorrelationId"),
    )


def _evidence_page(result: object, *, expected_bridge_epoch: str | None) -> EvidencePage:
    if not isinstance(result, Mapping):
        raise HarnessControlError("control evidence response must be an object")
    bridge_epoch = _evidence_bridge_epoch(result, expected_bridge_epoch=expected_bridge_epoch)
    latest = _required_non_negative_int(result, "latestSequence")
    evicted = _required_non_negative_int(result, "evictedBeforeSequence")
    truncated = result.get("truncated")
    if not isinstance(truncated, bool):
        raise HarnessControlError("control evidence response truncated must be boolean")
    raw_frames = result.get("frames")
    if not isinstance(raw_frames, list):
        raise HarnessControlError("control evidence response requires frames")
    frames: list[EvidenceFrame] = []
    previous = 0
    for raw_frame in raw_frames:
        if not isinstance(raw_frame, Mapping):
            raise HarnessControlError("control evidence frame must be an object")
        sequence = _required_non_negative_int(raw_frame, "sequence")
        if sequence <= previous:
            raise HarnessControlError("control evidence frames must increase monotonically")
        previous = sequence
        native_method = raw_frame.get("nativeMethod")
        if native_method is not None and (
            not isinstance(native_method, str) or not native_method
        ):
            raise HarnessControlError("control evidence nativeMethod must be non-empty text or absent")
        thread_id = raw_frame.get("threadId")
        if thread_id is not None and (not isinstance(thread_id, str) or not thread_id):
            raise HarnessControlError("control evidence threadId must be non-empty text or absent")
        frames.append(
            EvidenceFrame(
                sequence=sequence,
                kind=_required_text(raw_frame, "kind"),
                created_at=_required_text(raw_frame, "createdAt"),
                raw=_object(raw_frame.get("raw")),
                native_method=native_method,
                thread_id=thread_id,
            )
        )
    if frames and latest < frames[-1].sequence:
        raise HarnessControlError("control evidence latestSequence precedes its last frame")
    return EvidencePage(
        frames=tuple(frames),
        latest_sequence=latest,
        evicted_before_sequence=evicted,
        truncated=truncated,
        bridge_epoch=bridge_epoch,
    )


def _native_evidence_page(
    result: object, *, expected_bridge_epoch: str | None
) -> NativeEvidencePage:
    if not isinstance(result, Mapping):
        raise HarnessControlError("control native evidence response must be an object")
    bridge_epoch = _evidence_bridge_epoch(result, expected_bridge_epoch=expected_bridge_epoch)
    truncated = result.get("truncated")
    if not isinstance(truncated, bool):
        raise HarnessControlError("control native evidence response truncated must be boolean")
    next_cursor = result.get("nextCursor")
    if next_cursor is not None and not isinstance(next_cursor, str):
        raise HarnessControlError("control native evidence nextCursor must be text or null")
    frames = _native_evidence_frames(result.get("frames"), next_cursor)
    return NativeEvidencePage(
        frames=frames,
        next_cursor=next_cursor,
        truncated=truncated,
        bridge_epoch=bridge_epoch,
    )


def _native_evidence_frames(
    raw_frames: object, next_cursor: str | None
) -> tuple[NativeEvidenceFrame, ...]:
    if not isinstance(raw_frames, list):
        raise HarnessControlError("control native evidence response requires frames")
    frames: list[NativeEvidenceFrame] = []
    seen: set[str] = set()
    for raw_frame in raw_frames:
        frame = _native_evidence_frame(raw_frame)
        if frame.native_id in seen:
            raise HarnessControlError("control native evidence repeated a native id")
        seen.add(frame.native_id)
        frames.append(frame)
    if next_cursor is not None and not frames:
        raise HarnessControlError("control native evidence empty page cannot carry a continuation")
    if next_cursor is not None and next_cursor != frames[-1].native_id:
        raise HarnessControlError(
            "control native evidence nextCursor does not continue its last frame"
        )
    return tuple(frames)


def _native_evidence_frame(raw_frame: object) -> NativeEvidenceFrame:
    if not isinstance(raw_frame, Mapping):
        raise HarnessControlError("control native evidence frame must be an object")
    parent = raw_frame.get("nativeParentId")
    if parent is not None and not isinstance(parent, str):
        raise HarnessControlError("control native evidence nativeParentId must be text or null")
    created_at = raw_frame.get("createdAt")
    if created_at is not None and not isinstance(created_at, str):
        raise HarnessControlError("control native evidence createdAt must be text or null")
    return NativeEvidenceFrame(
        native_id=_required_text(raw_frame, "nativeId"),
        native_parent_id=parent,
        native_type=_required_text(raw_frame, "nativeType"),
        created_at=created_at,
        raw=_object(raw_frame.get("raw")),
    )


def _submission_provenance_batch(
    result: object,
    *,
    expected_bridge_epoch: str,
    request_ids: tuple[str, ...],
) -> SubmissionProvenanceBatch:
    if not isinstance(result, Mapping):
        raise HarnessControlError("submission provenance response must be an object")
    bridge_epoch = _required_text(result, "bridgeEpoch")
    if bridge_epoch != expected_bridge_epoch:
        raise HarnessControlError("submission provenance response bridge epoch mismatch")
    raw_provenance = result.get("provenance")
    if not isinstance(raw_provenance, list) or len(raw_provenance) != len(request_ids):
        raise HarnessControlError("submission provenance response has the wrong result count")
    provenance: list[SubmissionProvenance] = []
    for expected_id, raw_item in zip(request_ids, raw_provenance, strict=True):
        item = _submission_provenance_item(raw_item)
        if item.request_id != expected_id:
            raise HarnessControlError("submission provenance request id or order mismatch")
        provenance.append(item)
    return SubmissionProvenanceBatch(bridge_epoch=bridge_epoch, provenance=tuple(provenance))


def _submission_provenance_item(raw_item: object) -> SubmissionProvenance:
    if not isinstance(raw_item, Mapping):
        raise HarnessControlError("submission provenance item must be an object")
    request_id = _required_text(raw_item, "requestId")
    outcome = raw_item.get("outcome")
    if outcome == "not-found":
        return SubmissionProvenance(request_id=request_id, outcome="not-found")
    if outcome != "found":
        raise HarnessControlError("submission provenance has invalid outcome")
    source = raw_item.get("source")
    if source not in {"cockpit", "terminal", "durable"}:
        raise HarnessControlError("submission provenance has invalid source")
    return SubmissionProvenance(
        request_id=request_id,
        outcome="found",
        source=cast(SubmissionSource, source),
        state=_submission_state(raw_item.get("state")),
        submitted_at=_optional_text(raw_item, "submittedAt"),
        updated_at=_optional_text(raw_item, "updatedAt"),
        accepted_at=_optional_text(raw_item, "acceptedAt"),
        vendor_correlation_id=_optional_text(raw_item, "vendorCorrelationId"),
    )


def _evidence_bridge_epoch(raw: Mapping[str, object], *, expected_bridge_epoch: str | None) -> str:
    bridge_epoch = _required_text(raw, "bridgeEpoch")
    if expected_bridge_epoch is not None and bridge_epoch != expected_bridge_epoch:
        raise HarnessBridgeEpochMismatchError(expected_bridge_epoch, bridge_epoch)
    return bridge_epoch


def _require_coordinate(value: object, label: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise HarnessControlError(f"{label} must be a non-negative integer coordinate")


def _require_page_limit(limit: int) -> None:
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise HarnessControlError("evidence page limit must be a positive integer")


def _required_non_negative_int(raw: Mapping[str, object], key: str) -> int:
    value = raw.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise HarnessControlError(f"control response {key} must be a non-negative integer")
    return value


def _submission_state(
    value: object,
    *,
    optional: bool = False,
) -> SubmissionLifecycleState | None:
    if value is None and optional:
        return None
    if value not in {
        "queued",
        "dispatching",
        "delivered",
        "withdrawn",
        "unknown",
        "rejected",
        "unsupported",
    }:
        raise HarnessControlError("control response has invalid submission lifecycle state")
    return cast(SubmissionLifecycleState, value)


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


def _interaction_questions(raw: object) -> tuple[InteractionQuestion, ...]:
    """Parse the additive structured question pages; absent means a pre-structure peer."""

    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise HarnessControlError("pending interaction questions must be a list")
    pages: list[InteractionQuestion] = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise HarnessControlError("pending interaction question must be an object")
        raw_options = item.get("options", [])
        if not isinstance(raw_options, list):
            raise HarnessControlError("pending interaction question options must be a list")
        options: list[InteractionQuestionOption] = []
        for raw_option in raw_options:
            if not isinstance(raw_option, Mapping):
                raise HarnessControlError("pending interaction question option must be an object")
            description = raw_option.get("description")
            if description is not None and not isinstance(description, str):
                raise HarnessControlError(
                    "pending interaction question option description must be text or null"
                )
            options.append(
                InteractionQuestionOption(
                    label=_required_text(raw_option, "label"),
                    description=description,
                )
            )
        multi_select = item.get("multiSelect", False)
        if not isinstance(multi_select, bool):
            raise HarnessControlError("pending interaction question multiSelect must be boolean")
        pages.append(
            InteractionQuestion(
                text=_required_text(item, "text"),
                header=_required_text(item, "header"),
                options=tuple(options),
                multi_select=multi_select,
            )
        )
    return tuple(pages)


def _pending_interaction(raw: object) -> PendingInteraction:
    if not isinstance(raw, Mapping):
        raise HarnessControlError("pending interaction must be an object")
    choices_raw = raw.get("choices", [])
    if not isinstance(choices_raw, list) or not all(
        isinstance(choice, str) for choice in choices_raw
    ):
        raise HarnessControlError("pending interaction choices must be strings")
    return PendingInteraction(
        interaction_id=_required_text(raw, "interactionId"),
        kind=_required_text(raw, "kind"),
        prompt=_required_text(raw, "prompt"),
        created_at=_required_text(raw, "createdAt"),
        choices=tuple(choices_raw),
        raw=_object(raw.get("raw")),
        questions=_interaction_questions(raw.get("questions")),
    )


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
    pending = None if pending_raw is None else _pending_interaction(pending_raw)
    # Multiplexed sub-agent pendings: additive optional list;
    # absent on pre-multiplex bridges.
    pendings_raw = raw.get("pendingInteractions")
    pendings: tuple[PendingInteraction, ...] = ()
    if pendings_raw is not None:
        if not isinstance(pendings_raw, list):
            raise HarnessControlError("pending interactions must be a list")
        pendings = tuple(_pending_interaction(item) for item in pendings_raw)
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
        pending_interactions=pendings,
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
