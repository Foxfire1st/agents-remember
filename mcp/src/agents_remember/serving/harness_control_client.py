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
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, cast

from agents_remember.errors import (
    HarnessControlClientError,
    HarnessControlError,
)
from agents_remember.models.conversations.control_wire import (
    AdapterSnapshot,
    ControlIdentity,
    ControlSubmission,
    InterruptResult,
    OperationTimeline,
    SubmissionAuthorityDescriptor,
    SubmissionProvenanceBatch,
    SubmissionReceipt,
    WithdrawalResult,
)
from agents_remember.models.conversations.evidence import (
    EvidencePage,
    NativeEvidencePage,
)
from agents_remember.serving._harness_control_parsing import (
    _asset_reference,
    _decode_control_response,
    _evidence_bridge_epoch,
    _evidence_page,
    _interaction_questions,
    _interrupt_result,
    _native_evidence_frame,
    _native_evidence_frames,
    _native_evidence_page,
    _object,
    _operation_timeline,
    _operation_timeline_item,
    _operation_timeline_items,
    _optional_text,
    _pending_interaction,
    _require_coordinate,
    _require_page_limit,
    _required_non_negative_int,
    _required_text,
    _snapshot,
    _submission_lookup,
    _submission_provenance_batch,
    _submission_provenance_item,
    _submission_receipt,
    _submission_state,
    _submission_status_batch,
    _unknown_set_result,
    _withdrawal_recovery,
    _withdrawal_result,
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
    ReconciliationResult,
    ReconciliationState,
    SubmissionStatusBatch,
)
from agents_remember.serving.ports import ControlSessionLike

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


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/serving/harness_control_client.py:126).
def read_control_snapshot(entry: ControlledSession) -> AdapterSnapshot:  # pragma: no cover
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


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/serving/harness_control_client.py:141).
def read_control_capabilities(entry: ControlledSession) -> CapabilitySnapshot:  # pragma: no cover
    """Read the live adapter's normalized, model-gated capability snapshot."""

    result = request_control(entry, "advertise")
    try:
        return capability_snapshot_from_json(result)
    except (HarnessControlError, ValueError) as exc:
        raise HarnessControlError(f"control capability response is invalid: {exc}") from exc


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/serving/harness_control_client.py:151).
def read_submission_authority(
    entry: ControlSessionLike,
) -> SubmissionAuthorityDescriptor:  # pragma: no cover
    result = request_control(entry, "submission-authority")
    if not isinstance(result, Mapping):
        raise HarnessControlError("submission authority response must be an object")
    return SubmissionAuthorityDescriptor(bridge_epoch=_required_text(result, "bridgeEpoch"))


def read_submission_status(
    entry: ControlSessionLike,
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
    entry: ControlSessionLike,
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


def set_control_model(entry: ControlSessionLike, model_key: str) -> SetResult:
    return _set_control_value(entry, "set-model", "modelKey", model_key)


def set_control_effort(entry: ControlSessionLike, effort: str) -> SetResult:
    return _set_control_value(entry, "set-effort", "effort", effort)


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/serving/harness_control_client.py:221).
def submit_control_prompt(  # pragma: no cover
    entry: ControlSessionLike,
    text: str,
    submission: ControlSubmission,
) -> SubmissionReceipt:
    request_id = submission.request_id
    expected_bridge_epoch = submission.expected_bridge_epoch
    stamp = submission.submitted_at or datetime.now(UTC).isoformat()
    payload = _submit_payload(text, replace(submission, submitted_at=stamp))
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


def _submit_payload(text: str, submission: ControlSubmission) -> dict[str, object]:
    assets = submission.assets
    expected_bridge_epoch = submission.expected_bridge_epoch
    payload: dict[str, object] = {
        "requestId": submission.request_id,
        "source": submission.source,
        "text": text,
        "submittedAt": submission.submitted_at,
    }
    if assets is not None:
        if isinstance(assets, (str, bytes)) or not isinstance(assets, Sequence):
            raise HarnessControlError("submit assets must be a sequence of asset objects")
        payload["assets"] = [_wire_asset(asset) for asset in assets]
    if expected_bridge_epoch is not None:
        payload["expectedBridgeEpoch"] = expected_bridge_epoch
    return payload


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/serving/harness_control_client.py:280).
def reconcile_control_prompt(  # pragma: no cover
    entry: ControlSessionLike,
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


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/serving/harness_control_client.py:313).
def respond_control_interaction(  # pragma: no cover
    entry: ControlSessionLike,
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


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/serving/harness_control_client.py:337).
def read_control_transcript(  # pragma: no cover
    entry: ControlSessionLike, *, after_sequence: int = 0, limit: int = 500
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
    entry: ControlSessionLike,
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


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/serving/harness_control_client.py:376).
def read_control_native_page(  # pragma: no cover
    entry: ControlSessionLike,
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
    entry: ControlSessionLike,
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
    entry: ControlSessionLike,
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
    entry: ControlSessionLike,
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


def stop_control_session(entry: ControlSessionLike, *, forced: bool = False) -> None:
    request_control(entry, "stop", {"mode": "forced" if forced else "graceful"})


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/serving/harness_control_client.py:486).
def request_control(  # pragma: no cover
    entry: ControlSessionLike,
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


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/serving/harness_control_client.py:501).
def _encode_control_request(  # pragma: no cover
    entry: ControlSessionLike,
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


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/serving/harness_control_client.py:518).
def _connect_unavailable_detail(endpoint: Path, exc: BaseException) -> str:  # pragma: no cover
    """Map a control-socket connect failure without inventing process-liveness evidence.

    ``ENOENT`` is also the ordinary race while a newly spawned runner creates its endpoint, so the
    socket alone cannot prove that the process exited. A refused stale socket is unlinked best-effort
    so a later observation sees the clean absent-endpoint state.
    """

    error_number = getattr(exc, "errno", None)
    if error_number == errno.ECONNREFUSED:
        with contextlib.suppress(OSError):
            endpoint.unlink()
        return "the control socket exists but no runner is listening"
    if error_number == errno.ENOENT:
        return "the control socket is not present"
    if isinstance(exc, TimeoutError):
        return "the control endpoint did not accept a connection within the timeout"
    return "the control endpoint could not be reached"


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/serving/harness_control_client.py:541).
def _exchange_control(
    endpoint: Path, encoded: bytes, *, timeout_seconds: float
) -> bytes:  # pragma: no cover
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
            remainder = encoded[first_write:]
            if remainder:
                # Only write what is actually left. ``sendall`` is a do-while over the buffer, so an
                # empty remainder still issues one zero-length send; once the server has answered and
                # closed with our request drained the peer is gone, and that pointless write raises
                # EPIPE — reporting a disconnect (may_have_sent=True, forcing reconciliation) for an
                # exchange the server in fact completed.
                client.sendall(remainder)
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


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/serving/harness_control_client.py:578).
def _set_control_value(  # pragma: no cover
    entry: ControlSessionLike,
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


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/serving/harness_control_client.py:607).
def _read_line(client: socket.socket) -> bytes:  # pragma: no cover
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


class ControlPlaneClient:
    """Structural adapter exposing the control-socket reads as one injected port.

    The conversation tree depends on ``ControlPlanePort`` from
    ``serving.conversation.ports`` and never imports this module; the
    composition root binds this adapter into the conversation runtime.
    """

    def read_snapshot(self, entry: ControlSessionLike) -> AdapterSnapshot:
        return read_control_snapshot(entry)

    def read_submission_authority(self, entry: ControlSessionLike) -> SubmissionAuthorityDescriptor:
        return read_submission_authority(entry)

    def read_evidence(
        self,
        entry: ControlSessionLike,
        *,
        after_sequence: int = 0,
        limit: int = 500,
        expected_bridge_epoch: str | None = None,
    ) -> EvidencePage:
        return read_control_evidence(
            entry,
            after_sequence=after_sequence,
            limit=limit,
            expected_bridge_epoch=expected_bridge_epoch,
        )

    def read_native_page(
        self,
        entry: ControlSessionLike,
        *,
        cursor: str | None = None,
        limit: int = 200,
        expected_bridge_epoch: str | None = None,
        thread_id: str | None = None,
    ) -> NativeEvidencePage:
        return read_control_native_page(
            entry,
            cursor=cursor,
            limit=limit,
            expected_bridge_epoch=expected_bridge_epoch,
            thread_id=thread_id,
        )

    def read_transcript(
        self,
        entry: ControlSessionLike,
        *,
        after_sequence: int = 0,
        limit: int = 500,
    ) -> tuple[Mapping[str, object], ...]:
        return read_control_transcript(entry, after_sequence=after_sequence, limit=limit)

    def read_submission_provenance(
        self,
        entry: ControlSessionLike,
        *,
        expected_bridge_epoch: str,
        request_ids: tuple[str, ...],
    ) -> SubmissionProvenanceBatch:
        return read_submission_provenance(
            entry,
            expected_bridge_epoch=expected_bridge_epoch,
            request_ids=request_ids,
        )

    def interrupt(
        self,
        entry: ControlSessionLike,
        *,
        expected_bridge_epoch: str,
        turn_id: str | None = None,
        expected_operation_id: str | None = None,
    ) -> InterruptResult:
        return interrupt_control(
            entry,
            expected_bridge_epoch=expected_bridge_epoch,
            turn_id=turn_id,
            expected_operation_id=expected_operation_id,
        )

    def withdraw_submission(
        self,
        entry: ControlSessionLike,
        *,
        expected_bridge_epoch: str,
        request_id: str,
    ) -> WithdrawalResult:
        return withdraw_control_submission(
            entry,
            expected_bridge_epoch=expected_bridge_epoch,
            request_id=request_id,
        )

    def submit(
        self,
        entry: ControlSessionLike,
        text: str,
        submission: ControlSubmission,
    ) -> SubmissionReceipt:
        return submit_control_prompt(entry, text, submission)

    def read_operation_timeline(
        self,
        entry: ControlSessionLike,
        *,
        expected_bridge_epoch: str,
        after_sequence: int = 0,
        limit: int = MAX_OPERATION_TIMELINE_PAGE,
    ) -> OperationTimeline:
        return read_operation_timeline(
            entry,
            expected_bridge_epoch=expected_bridge_epoch,
            after_sequence=after_sequence,
            limit=limit,
        )


__all__ = [
    "EVIDENCE_PAGE_TIMEOUT_SECONDS",
    "SET_CONTROL_TIMEOUT_SECONDS",
    "SUBMIT_TIMEOUT_SECONDS",
    "ControlPlaneClient",
    "ControlSubmission",
    "ControlledSession",
    "_asset_reference",
    "_decode_control_response",
    "_evidence_bridge_epoch",
    "_evidence_page",
    "_interaction_questions",
    "_interrupt_result",
    "_native_evidence_frame",
    "_native_evidence_frames",
    "_native_evidence_page",
    "_object",
    "_operation_timeline",
    "_operation_timeline_item",
    "_operation_timeline_items",
    "_optional_text",
    "_pending_interaction",
    "_read_line",
    "_require_coordinate",
    "_require_page_limit",
    "_required_non_negative_int",
    "_required_text",
    "_set_control_value",
    "_snapshot",
    "_submission_lookup",
    "_submission_provenance_batch",
    "_submission_provenance_item",
    "_submission_receipt",
    "_submission_state",
    "_submission_status_batch",
    "_unknown_set_result",
    "_withdrawal_recovery",
    "_withdrawal_result",
    "control_identity",
    "interrupt_control",
    "read_control_capabilities",
    "read_control_evidence",
    "read_control_native_page",
    "read_control_snapshot",
    "read_control_transcript",
    "read_operation_timeline",
    "read_submission_authority",
    "read_submission_provenance",
    "read_submission_status",
    "reconcile_control_prompt",
    "request_control",
    "respond_control_interaction",
    "set_control_effort",
    "set_control_model",
    "stop_control_session",
    "submit_control_prompt",
    "withdraw_control_submission",
]
