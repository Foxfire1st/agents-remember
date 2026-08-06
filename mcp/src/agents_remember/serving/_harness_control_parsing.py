"""Wire-response parsing for the hosted harness control client.

The blocking client keeps its protocol boundary explicit: every response is
validated because the peer is a long-lived subprocess, not trusted in-process
state. This module owns the raw-response parsers and shape checks; the client
operations (request/read/submit/interrupt) live in
:mod:`agents_remember.serving.harness_control_client` and re-export these
helpers so the public surface is unchanged.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Literal, cast, overload

from agents_remember.errors import (
    HarnessBridgeEpochMismatchError,
    HarnessControlClientError,
    HarnessControlError,
    HarnessInteractionNotPendingError,
    HarnessRequestConflictError,
    NativeHistoryLimitExceeded,
    NativeHistoryUnavailable,
)
from agents_remember.serving.harness_capabilities import (
    SetResult,
)
from agents_remember.serving.harness_control_models import (
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


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/serving/_harness_control_parsing.py:62).
def _decode_control_response(response: bytes) -> object:  # pragma: no cover
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
        if raw.get("status") == "native-history-limit-exceeded":
            raise NativeHistoryLimitExceeded(
                detail,
                actual_bytes=_required_non_negative_int(raw, "actualBytes"),
                limit_bytes=_required_non_negative_int(raw, "limitBytes"),
            )
        if raw.get("status") == "native-history-unavailable":
            raise NativeHistoryUnavailable(
                detail,
                code=_required_text(raw, "code"),
            )
        raise HarnessControlError(detail)
    return raw.get("result")


def _unknown_set_result(requested_value: str, detail: str) -> SetResult:
    return SetResult(
        ok=False,
        acceptance="unknown",
        requested_value=requested_value,
        detail=f"setter outcome is unknown after request bytes were sent: {detail}",
    )


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/serving/_harness_control_parsing.py:108).
def _submission_receipt(
    result: object, *, request_id: str
) -> SubmissionReceipt:  # pragma: no cover
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


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/serving/_harness_control_parsing.py:129).
def _submission_status_batch(  # pragma: no cover
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
    return SubmissionStatusBatch(
        bridge_epoch=bridge_epoch,
        submissions=tuple(
            _submission_lookup(raw_lookup, expected_id=expected_id)
            for expected_id, raw_lookup in zip(request_ids, raw_submissions, strict=True)
        ),
    )


def _submission_lookup(raw_lookup: object, *, expected_id: str) -> SubmissionLookup:
    """One lookup out of a status batch, verified against the id asked for at that position.

    The batch is positional, so an id that does not match its slot means the response cannot be
    attributed to the request and is rejected rather than re-keyed by whatever the adapter sent.
    """

    if not isinstance(raw_lookup, Mapping):
        raise HarnessControlError("submission lookup must be an object")
    request_id = _required_text(raw_lookup, "requestId")
    if request_id != expected_id:
        raise HarnessControlError("submission lookup request id or order mismatch")
    outcome = raw_lookup.get("outcome")
    if outcome == "not-found":
        return SubmissionLookup(request_id=request_id, outcome="not-found")
    if outcome != "found" or not isinstance(raw_lookup.get("submission"), Mapping):
        raise HarnessControlError("submission lookup has invalid outcome or evidence")
    raw_status = cast(Mapping[str, object], raw_lookup["submission"])
    withdrawable = raw_status.get("withdrawable")
    if not isinstance(withdrawable, bool):
        raise HarnessControlError("submission status withdrawable must be boolean")
    state = _submission_state(raw_status.get("state"))
    return SubmissionLookup(
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


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/serving/_harness_control_parsing.py:189).
def _withdrawal_result(result: object, *, request_id: str) -> WithdrawalResult:  # pragma: no cover
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


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/serving/_harness_control_parsing.py:208).
def _withdrawal_recovery(raw_recovery: object) -> WithdrawalRecovery | None:  # pragma: no cover
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


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/serving/_harness_control_parsing.py:225).
def _asset_reference(raw: object) -> AssetReference:  # pragma: no cover
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


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/serving/_harness_control_parsing.py:239).
def _interrupt_result(
    result: object, *, expected_bridge_epoch: str
) -> InterruptResult:  # pragma: no cover
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


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/serving/_harness_control_parsing.py:261).
def _operation_timeline(
    result: object, *, expected_bridge_epoch: str
) -> OperationTimeline:  # pragma: no cover
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


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/serving/_harness_control_parsing.py:284).
def _operation_timeline_items(  # pragma: no cover
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


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/serving/_harness_control_parsing.py:302).
def _operation_timeline_item(raw_item: object) -> OperationTimelineItem:  # pragma: no cover
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
        if native_method is not None and (not isinstance(native_method, str) or not native_method):
            raise HarnessControlError(
                "control evidence nativeMethod must be non-empty text or absent"
            )
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


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/serving/_harness_control_parsing.py:382).
def _native_evidence_page(  # pragma: no cover
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


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/serving/_harness_control_parsing.py:403).
def _native_evidence_frames(  # pragma: no cover
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
    return tuple(frames)


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/serving/_harness_control_parsing.py:421).
def _native_evidence_frame(raw_frame: object) -> NativeEvidenceFrame:  # pragma: no cover
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


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/serving/_harness_control_parsing.py:439).
def _submission_provenance_batch(  # pragma: no cover
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


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/serving/_harness_control_parsing.py:462).
def _submission_provenance_item(raw_item: object) -> SubmissionProvenance:  # pragma: no cover
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


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/serving/_harness_control_parsing.py:493).
def _require_coordinate(value: object, label: str) -> None:  # pragma: no cover
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise HarnessControlError(f"{label} must be a non-negative integer coordinate")


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/serving/_harness_control_parsing.py:498).
def _require_page_limit(limit: int) -> None:  # pragma: no cover
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise HarnessControlError("evidence page limit must be a positive integer")


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/serving/_harness_control_parsing.py:503).
def _required_non_negative_int(raw: Mapping[str, object], key: str) -> int:  # pragma: no cover
    value = raw.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise HarnessControlError(f"control response {key} must be a non-negative integer")
    return value


@overload
def _submission_state(value: object) -> SubmissionLifecycleState: ...


@overload
def _submission_state(
    value: object, *, optional: Literal[True]
) -> SubmissionLifecycleState | None: ...


def _submission_state(
    value: object,
    *,
    optional: bool = False,
) -> SubmissionLifecycleState | None:
    """The lifecycle state a control response claims, refused if it is not one of the seven.

    ``optional=True`` is the only way to get ``None`` back, and it means the field was
    absent. Without it a missing state is a malformed response like any other value outside
    the set -- ``None`` never survives this call, which is why callers that require a state
    do not re-check for it.
    """

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


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/serving/_harness_control_parsing.py:548).
def _interaction_questions(raw: object) -> tuple[InteractionQuestion, ...]:  # pragma: no cover
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


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/serving/_harness_control_parsing.py:591).
def _pending_interaction(raw: object) -> PendingInteraction:  # pragma: no cover
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


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/serving/_harness_control_parsing.py:610).
def _snapshot(raw: Mapping[str, object]) -> AdapterSnapshot:  # pragma: no cover
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


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/serving/_harness_control_parsing.py:656).
def _optional_text(raw: Mapping[str, object], key: str) -> str | None:  # pragma: no cover
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise HarnessControlError(f"control response {key} must be a string")
    return value


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/serving/_harness_control_parsing.py:665).
def _object(raw: object) -> Mapping[str, object]:  # pragma: no cover
    if raw is None:
        return {}
    if not isinstance(raw, Mapping) or not all(isinstance(key, str) for key in raw):
        raise HarnessControlError("control response raw detail must be an object")
    return cast(Mapping[str, object], raw)
