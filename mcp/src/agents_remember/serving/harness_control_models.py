"""Protocol-neutral models for one exact hosted harness control session."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal, Protocol, cast, runtime_checkable

from agents_remember.errors import HarnessControlError

CONTROL_PROTOCOL_VERSION = "ar-harness-control/v1"

ControlState = Literal["starting", "ready", "disconnected", "failed", "unsupported"]
ActivityState = Literal["idle", "running", "blocked", "settling", "unknown"]
AcceptanceState = Literal["immediate", "queued", "rejected", "unknown", "unsupported"]
SubmissionSource = Literal["cockpit", "terminal", "durable"]
ControlOperationKind = Literal["prompt", "set-model", "set-effort"]
SubmissionLifecycleState = Literal[
    "queued",
    "dispatching",
    "delivered",
    "withdrawn",
    "unknown",
    "rejected",
    "unsupported",
]
WithdrawalOutcome = Literal["withdrawn", "not-withdrawable", "not-found"]
SubmissionLookupOutcome = Literal["found", "not-found"]
TranscriptRole = Literal["user", "assistant", "system", "interaction", "result"]
TerminalOutcome = Literal["completed", "failed", "cancelled"]
ReconciliationState = Literal["accepted", "rejected", "unresolved", "unsupported"]
ShutdownMode = Literal["graceful", "forced"]
AdapterCapability = Literal[
    "state-snapshot",
    "state-subscription",
    "prompt-submission",
    "interaction-response",
    "reconciliation",
    "transcript",
    "graceful-shutdown",
]

REQUIRED_ADAPTER_CAPABILITIES: frozenset[AdapterCapability] = frozenset(
    {
        "state-snapshot",
        "state-subscription",
        "prompt-submission",
        "interaction-response",
        "reconciliation",
        "transcript",
        "graceful-shutdown",
    }
)

AR_EVIDENCE_KEY = "arEvidence"
"""Reserved ``AdapterEvent.raw`` key carrying one full native payload for the evidence buffer.

Mappers place full native frames under this key only; every pre-existing raw key keeps its exact
shape. The control bridge diverts the payload into the bounded evidence deque and republishes the
event without this key, so ``snapshot.raw`` and every projection of it stay byte-identical.
"""

AR_EVIDENCE_METHOD_KEY = "arEvidenceMethod"
"""Reserved ``AdapterEvent.raw`` key carrying the native notification method / frame ``type``.

Some native protocols (notably the Codex app-server) carry the discriminating method name
*outside* the notification ``params`` payload. When an adapter diverts only ``params`` under
``AR_EVIDENCE_KEY`` the method would otherwise be stripped before a projector ever sees it, forcing
the projector to re-guess meaning from the params shape. An adapter that has this fact sets it here
so the bridge preserves it on the diverted :class:`EvidenceFrame` as typed ``native_method``
metadata; like ``AR_EVIDENCE_KEY`` it is stripped from the republished event so ``snapshot.raw``
stays byte-identical.
"""

EVIDENCE_TRUNCATION_MARKER = "…[truncated]"
"""Visible marker appended to every clipped evidence payload preview."""

MAX_PRESERVED_EVIDENCE_SCALAR_CHARS = 256
"""Length ceiling for a terminal-identity scalar re-carried by the truncation envelope.

Every preserved field is a protocol enum (pi ``stopReason``, codex turn ``status``), a frame
type name, or a vendor turn id — a handful of characters in every real shape, so 256 is orders
of magnitude above any legitimate value while staying tiny against the evidence budget. A scalar
longer than this is a malformed-frame signal, not trustworthy terminal identity: it is dropped
WHOLE (never truncated — a partial id/status could mis-correlate at settlement), degrading the
envelope to the pre-identity total clip for that one field. This keeps an oversized scalar from
ever making the truncation envelope exceed its own byte budget (which would raise instead of
clip, and in the bridge event loop that raise is session-fatal)."""

MAX_NATIVE_EVIDENCE_PAGE = 200
"""Server-side frame cap for one native-domain evidence page."""

EVIDENCE_PAGE_BYTE_BUDGET = 48 * 1024
"""Default serialized-byte budget for one evidence page (bounded below the IPC wire cap)."""

MAX_OPERATION_TIMELINE_PAGE = 256
"""Server-side item cap for one operation-timeline page (the retained ledger's own bound)."""

MAX_SUBMIT_ASSETS = 4
"""Maximum asset references riding one submit payload."""

MAX_SUBMIT_ASSET_BYTES = 5 * 1024 * 1024
"""Maximum declared byte size for one staged asset."""

SUBMIT_ASSET_MIME_TYPES = frozenset({"image/png", "image/jpeg", "image/webp", "image/gif"})
"""MIME allow-list for the asset channel; anything else fails closed at admission."""

InterruptAcknowledgement = Literal["accepted", "rejected", "unsupported", "unknown"]


@dataclass(frozen=True)
class ControlIdentity:
    """Exact catalog identity for one bridge; all IPC requests repeat this tuple."""

    ar_session_id: str
    tmux_name: str
    created_at: str

    def to_json(self) -> dict[str, str]:
        return {
            "arSessionId": self.ar_session_id,
            "tmuxName": self.tmux_name,
            "createdAt": self.created_at,
        }

    @classmethod
    def from_json(cls, raw: Mapping[str, object]) -> ControlIdentity:
        return cls(
            ar_session_id=_required_text(raw, "arSessionId"),
            tmux_name=_required_text(raw, "tmuxName"),
            created_at=_required_text(raw, "createdAt"),
        )


@dataclass(frozen=True)
class LaunchSpec:
    """The fixed argv and installed environment an adapter owns as its one subprocess."""

    identity: ControlIdentity
    harness_id: str
    cwd: Path
    argv: tuple[str, ...]
    env: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class PendingInteraction:
    interaction_id: str
    kind: str
    prompt: str
    created_at: str
    choices: tuple[str, ...] = ()
    raw: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class TerminalResult:
    outcome: TerminalOutcome
    completed_at: str
    detail: str | None = None
    raw: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class TranscriptEntry:
    sequence: int
    role: TranscriptRole
    text: str
    created_at: str
    request_id: str | None = None
    vendor_correlation_id: str | None = None
    terminal_result: TerminalResult | None = None
    raw: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class AdapterSnapshot:
    """Orthogonal control, activity, and acceptance state without erasing vendor detail."""

    identity: ControlIdentity
    control: ControlState
    activity: ActivityState
    acceptance: AcceptanceState
    vendor_session_id: str | None = None
    pending_interaction: PendingInteraction | None = None
    last_event_sequence: int = 0
    raw: Mapping[str, object] = field(default_factory=dict)

    @property
    def ar_session_id(self) -> str:
        return self.identity.ar_session_id


@dataclass(frozen=True)
class AdapterHandshake:
    protocol_version: str
    adapter_id: str
    identity: ControlIdentity
    capabilities: frozenset[AdapterCapability]
    snapshot: AdapterSnapshot
    raw: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class AssetReference:
    """One staged asset's verified identity; ``spool_path`` is runner-local and never serialized."""

    asset_id: str
    mime_type: str
    byte_size: int
    sha256: str
    spool_path: Path | None = None


@dataclass(frozen=True)
class PromptRequest:
    """One immutable whole message; adapters must never merge it at keystroke level."""

    request_id: str
    source: SubmissionSource
    text: str
    submitted_at: str
    operation: ControlOperationRef | None = None
    expected_bridge_epoch: str | None = None
    assets: tuple[AssetReference, ...] = ()


@dataclass(frozen=True)
class UncommittedDraft:
    """Surface-owned human text that has not entered the adapter submission queue.

    Automated delivery cannot inspect or mutate this value. ``revision`` lets the surface retain a
    newer human edit when an explicitly committed draft submission completes asynchronously.
    """

    text: str = ""
    revision: int = 0


@dataclass(frozen=True)
class SubmissionReceipt:
    request_id: str
    acceptance: AcceptanceState
    submitted_at: str
    vendor_correlation_id: str | None = None
    accepted_at: str | None = None
    detail: str | None = None
    raw: Mapping[str, object] = field(default_factory=dict)
    bridge_epoch: str | None = None


@dataclass(frozen=True)
class InteractionResponse:
    interaction_id: str
    response: str
    responded_at: str
    operation: ControlOperationRef | None = None


@dataclass(frozen=True)
class ReconciliationResult:
    request_id: str
    state: ReconciliationState
    reconciled_at: str
    vendor_correlation_id: str | None = None
    detail: str | None = None
    raw: Mapping[str, object] = field(default_factory=dict)
    bridge_epoch: str | None = None
    submission_state: SubmissionLifecycleState | None = None


@dataclass(frozen=True)
class ControlOperationRef:
    """Exact ordinary-operation identity shared by the authority and adapter events."""

    bridge_epoch: str
    sequence: int
    operation_id: str
    kind: ControlOperationKind

    def to_json(self) -> dict[str, object]:
        return {
            "bridgeEpoch": self.bridge_epoch,
            "operationSequence": self.sequence,
            "operationId": self.operation_id,
            "operationKind": self.kind,
        }

    @classmethod
    def from_json(cls, raw: Mapping[str, object]) -> ControlOperationRef:
        sequence = raw.get("operationSequence")
        if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
            raise HarnessControlError("operation ref requires positive operationSequence")
        kind = raw.get("operationKind")
        if kind not in {"prompt", "set-model", "set-effort"}:
            raise HarnessControlError("operation ref has invalid operationKind")
        return cls(
            bridge_epoch=_required_text(raw, "bridgeEpoch"),
            sequence=sequence,
            operation_id=_required_text(raw, "operationId"),
            kind=cast(ControlOperationKind, kind),
        )


@dataclass(frozen=True)
class SubmissionAuthorityDescriptor:
    bridge_epoch: str


@dataclass(frozen=True)
class SubmissionStatus:
    request_id: str
    state: SubmissionLifecycleState
    submitted_at: str
    updated_at: str
    accepted_at: str | None
    withdrawable: bool
    detail: str | None = None


@dataclass(frozen=True)
class SubmissionLookup:
    request_id: str
    outcome: SubmissionLookupOutcome
    submission: SubmissionStatus | None = None


@dataclass(frozen=True)
class SubmissionStatusBatch:
    bridge_epoch: str
    submissions: tuple[SubmissionLookup, ...]


@dataclass(frozen=True)
class WithdrawalRecovery:
    """The exact body the tombstone consumed at one true withdrawal; crosses only then."""

    text: str | None
    assets: tuple[AssetReference, ...] = ()


@dataclass(frozen=True)
class WithdrawalResult:
    request_id: str
    outcome: WithdrawalOutcome
    state: SubmissionLifecycleState | None
    withdrawn_at: str | None = None
    detail: str | None = None
    recovery: WithdrawalRecovery | None = None


@dataclass(frozen=True)
class InterruptResult:
    """One native interrupt acknowledgement; settlement stays with the completion path."""

    acknowledgement: InterruptAcknowledgement
    bridge_epoch: str
    operation: ControlOperationRef | None = None
    vendor_correlation_id: str | None = None
    detail: str | None = None
    raw: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class OperationTimelineItem:
    """One retained ledger row's identity/source/kind/sequence/state — never its body."""

    operation_id: str
    kind: ControlOperationKind
    source: SubmissionSource | None
    state: SubmissionLifecycleState
    sequence: int
    submitted_at: str
    updated_at: str
    accepted_at: str | None
    payload_digest_present: bool
    vendor_correlation_id: str | None = None


@dataclass(frozen=True)
class OperationTimeline:
    """One paged never-bodies enumeration of the retained ledger; union is completeness."""

    bridge_epoch: str
    latest_sequence: int
    evicted_before_sequence: int
    truncated: bool
    items: tuple[OperationTimelineItem, ...]


@dataclass(frozen=True)
class AdapterEvent:
    """One normalized adapter event; unknown kinds are additive vendor-detail events."""

    sequence: int
    kind: str
    identity: ControlIdentity
    created_at: str
    snapshot: AdapterSnapshot | None = None
    transcript: tuple[TranscriptEntry, ...] = ()
    raw: Mapping[str, object] = field(default_factory=dict)
    operation: ControlOperationRef | None = None


@dataclass(frozen=True)
class EvidenceFrame:
    """One diverted native payload in the deque coordinate domain (adapter event sequence)."""

    sequence: int
    kind: str
    created_at: str
    raw: Mapping[str, object] = field(default_factory=dict)
    native_method: str | None = None
    """The native notification method / frame ``type`` when the adapter carries it out of band.

    Preserved verbatim from ``AR_EVIDENCE_METHOD_KEY`` so a projector switches on the real method
    instead of re-guessing meaning from the ``params`` shape; ``None`` when the adapter embeds the
    discriminator inside ``raw`` (as Claude and Pi do with the frame ``type``).
    """


@dataclass(frozen=True)
class EvidencePage:
    """One bounded deque-domain page; every coordinate is an adapter event sequence."""

    frames: tuple[EvidenceFrame, ...]
    latest_sequence: int
    evicted_before_sequence: int
    truncated: bool
    bridge_epoch: str


@dataclass(frozen=True)
class NativeEvidenceFrame:
    """One native-history frame with typed harness identity, never buried in ``raw``."""

    native_id: str
    native_parent_id: str | None
    native_type: str
    created_at: str | None
    raw: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class NativeEvidencePage:
    """One bounded native-domain page continued only by the opaque ``next_cursor``."""

    frames: tuple[NativeEvidenceFrame, ...]
    next_cursor: str | None
    truncated: bool
    bridge_epoch: str


@dataclass(frozen=True)
class SubmissionProvenance:
    """Source/lifecycle evidence for one exact request id across every submission source."""

    request_id: str
    outcome: SubmissionLookupOutcome
    source: SubmissionSource | None = None
    state: SubmissionLifecycleState | None = None
    submitted_at: str | None = None
    updated_at: str | None = None
    accepted_at: str | None = None
    vendor_correlation_id: str | None = None


@dataclass(frozen=True)
class SubmissionProvenanceBatch:
    bridge_epoch: str
    provenance: tuple[SubmissionProvenance, ...]


@runtime_checkable
class NativePageReader(Protocol):
    """Structural native-history read; concrete adapters opt in without a protocol edit."""

    async def read_native_page(
        self,
        *,
        cursor: str | None,
        limit: int,
        byte_budget: int,
    ) -> NativeEvidencePage: ...


def pending_interaction_json(value: PendingInteraction | None) -> dict[str, object] | None:
    if value is None:
        return None
    return {
        "interactionId": value.interaction_id,
        "kind": value.kind,
        "prompt": value.prompt,
        "createdAt": value.created_at,
        "choices": list(value.choices),
        "raw": dict(value.raw),
    }


def terminal_result_json(value: TerminalResult | None) -> dict[str, object] | None:
    if value is None:
        return None
    return {
        "outcome": value.outcome,
        "completedAt": value.completed_at,
        "detail": value.detail,
        "raw": dict(value.raw),
    }


def transcript_entry_json(value: TranscriptEntry) -> dict[str, object]:
    return {
        "sequence": value.sequence,
        "role": value.role,
        "text": value.text,
        "createdAt": value.created_at,
        "requestId": value.request_id,
        "vendorCorrelationId": value.vendor_correlation_id,
        "terminalResult": terminal_result_json(value.terminal_result),
        "raw": dict(value.raw),
    }


def snapshot_json(value: AdapterSnapshot) -> dict[str, object]:
    return {
        "identity": value.identity.to_json(),
        "control": value.control,
        "activity": value.activity,
        "acceptance": value.acceptance,
        "vendorSessionId": value.vendor_session_id,
        "pendingInteraction": pending_interaction_json(value.pending_interaction),
        "lastEventSequence": value.last_event_sequence,
        "raw": dict(value.raw),
    }


def evidence_frame_json(value: EvidenceFrame) -> dict[str, object]:
    payload: dict[str, object] = {
        "sequence": value.sequence,
        "kind": value.kind,
        "createdAt": value.created_at,
        "raw": dict(value.raw),
    }
    if value.native_method is not None:
        payload["nativeMethod"] = value.native_method
    return payload


def evidence_page_json(value: EvidencePage) -> dict[str, object]:
    return {
        "frames": [evidence_frame_json(frame) for frame in value.frames],
        "latestSequence": value.latest_sequence,
        "evictedBeforeSequence": value.evicted_before_sequence,
        "truncated": value.truncated,
        "bridgeEpoch": value.bridge_epoch,
    }


def native_evidence_frame_json(value: NativeEvidenceFrame) -> dict[str, object]:
    return {
        "nativeId": value.native_id,
        "nativeParentId": value.native_parent_id,
        "nativeType": value.native_type,
        "createdAt": value.created_at,
        "raw": dict(value.raw),
    }


def native_evidence_page_json(value: NativeEvidencePage) -> dict[str, object]:
    return {
        "frames": [native_evidence_frame_json(frame) for frame in value.frames],
        "nextCursor": value.next_cursor,
        "truncated": value.truncated,
        "bridgeEpoch": value.bridge_epoch,
    }


def submission_provenance_json(value: SubmissionProvenance) -> dict[str, object]:
    if value.outcome == "not-found":
        return {"requestId": value.request_id, "outcome": "not-found"}
    return {
        "requestId": value.request_id,
        "outcome": "found",
        "source": value.source,
        "state": value.state,
        "submittedAt": value.submitted_at,
        "updatedAt": value.updated_at,
        "acceptedAt": value.accepted_at,
        "vendorCorrelationId": value.vendor_correlation_id,
    }


def submission_provenance_batch_json(value: SubmissionProvenanceBatch) -> dict[str, object]:
    return {
        "bridgeEpoch": value.bridge_epoch,
        "provenance": [submission_provenance_json(item) for item in value.provenance],
    }


def _bounded_identity_scalar(value: object) -> str | None:
    """One preserved scalar if it is a string within the identity length ceiling, else ``None``.

    A non-string or an over-length string is dropped whole (never truncated), so a malformed
    giant scalar can neither cross nor collapse the truncation envelope's own byte budget.
    """

    if isinstance(value, str) and len(value) <= MAX_PRESERVED_EVIDENCE_SCALAR_CHARS:
        return value
    return None


def _preserved_evidence_identity(payload: Mapping[str, object]) -> dict[str, object]:
    """The terminal-identity fields that survive a clip, each at its original payload path.

    Only tiny scalar identity/status enums cross — never text, content blocks, items, or any
    other body. Each scalar is bounded by ``MAX_PRESERVED_EVIDENCE_SCALAR_CHARS`` and dropped
    whole when absent, non-string, or over-length (never invented, never truncated); a value is
    copied only when it is present as the exact bounded scalar the settlement consumers read:

    * top-level ``type`` — the frame kind every clipped frame keeps (pi ``message_end``); the
      pi settlement read is ``frame.raw.get("type") == "message_end"``.
    * ``message.stopReason`` — the pi terminal enum; the read is ``frame.raw["message"]
      ["stopReason"]``. Only ``stopReason`` crosses; the message role and content never do.
    * ``turn.id`` + ``turn.status`` — the codex terminal-turn identity and status enum; the
      read correlates ``frame.raw["turn"]["id"] == turn_id`` before taking ``turn["status"]``,
      so both must survive or the frame is skipped. The turn's items/error never cross.
    """

    preserved: dict[str, object] = {}
    frame_type = _bounded_identity_scalar(payload.get("type"))
    if frame_type is not None:
        preserved["type"] = frame_type
    message = payload.get("message")
    if isinstance(message, Mapping):
        stop_reason = _bounded_identity_scalar(message.get("stopReason"))
        if stop_reason is not None:
            preserved["message"] = {"stopReason": stop_reason}
    turn = payload.get("turn")
    if isinstance(turn, Mapping):
        turn_identity: dict[str, object] = {}
        turn_id = _bounded_identity_scalar(turn.get("id"))
        if turn_id is not None:
            turn_identity["id"] = turn_id
        turn_status = _bounded_identity_scalar(turn.get("status"))
        if turn_status is not None:
            turn_identity["status"] = turn_status
        if turn_identity:
            preserved["turn"] = turn_identity
    return preserved


def clip_evidence_payload(payload: Mapping[str, object], *, max_bytes: int) -> Mapping[str, object]:
    """Bound one JSON evidence payload to ``max_bytes`` serialized, with any clip visible.

    Unclipped payloads are returned as a plain copy. Clipped payloads become an envelope whose
    own serialized size never exceeds ``max_bytes`` and whose preview ends with the truncation
    marker, so consumers never mistake a partial payload for a complete native frame. The
    envelope additionally re-carries the frame's terminal-identity enums at their original
    payload paths (``_preserved_evidence_identity``) so exact-turn interrupt settlement stays
    honest for oversized frames; no other content from the original frame crosses.
    """

    if max_bytes < 1:
        raise HarnessControlError("evidence payload clip requires a positive byte budget")
    try:
        encoded = json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise HarnessControlError("adapter evidence payload must be JSON-serializable") from exc
    if len(encoded.encode("utf-8")) <= max_bytes:
        return dict(payload)
    original_bytes = len(encoded.encode("utf-8"))
    preserved = _preserved_evidence_identity(payload)
    preview = encoded
    for _ in range(64):
        clipped: dict[str, object] = {
            "arEvidenceTruncated": True,
            "originalBytes": original_bytes,
            "preview": preview + EVIDENCE_TRUNCATION_MARKER,
            **preserved,
        }
        if len(json.dumps(clipped, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) <= (
            max_bytes
        ):
            return clipped
        preview = preview[: len(preview) // 2]
    raise HarnessControlError("evidence payload clip budget is below the truncation envelope")


def evidence_frame_wire_bytes(frame: EvidenceFrame) -> int:
    return _serialized_size(evidence_frame_json(frame))


def native_evidence_frame_wire_bytes(frame: NativeEvidenceFrame) -> int:
    return _serialized_size(native_evidence_frame_json(frame))


def window_native_evidence_page(
    frames: tuple[NativeEvidenceFrame, ...],
    *,
    cursor: str | None,
    limit: int,
    byte_budget: int,
) -> NativeEvidencePage:
    """Window one full native read into a bounded page with an opaque native continuation.

    The cursor names the last native id of the previous page; the next page starts strictly
    after it and is minted only from the fresh native read. A single oversized frame is clipped
    so every page makes progress; the bridge stamps ``bridge_epoch`` on the result.
    """

    if limit < 1:
        raise HarnessControlError("native evidence page limit must be positive")
    if byte_budget < 1:
        raise HarnessControlError("native evidence page byte budget must be positive")
    start = _native_window_start(frames, cursor)
    selected: list[NativeEvidenceFrame] = []
    used = 0
    end = start
    while end < len(frames) and len(selected) < limit:
        frame = frames[end]
        if native_evidence_frame_wire_bytes(frame) > byte_budget:
            frame = replace(frame, raw=clip_evidence_payload(frame.raw, max_bytes=byte_budget // 2))
        size = native_evidence_frame_wire_bytes(frame)
        if selected and used + size > byte_budget:
            break
        selected.append(frame)
        used += size
        end += 1
    truncated = end < len(frames)
    next_cursor = selected[-1].native_id if truncated and selected else None
    return NativeEvidencePage(
        frames=tuple(selected),
        next_cursor=next_cursor,
        truncated=truncated,
        bridge_epoch="",
    )


def _native_window_start(frames: tuple[NativeEvidenceFrame, ...], cursor: str | None) -> int:
    if cursor is None:
        return 0
    for index, frame in enumerate(frames):
        if frame.native_id == cursor:
            return index + 1
    raise HarnessControlError("native evidence page cursor is absent from the current native read")


def _serialized_size(value: Mapping[str, object]) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def receipt_json(value: SubmissionReceipt) -> dict[str, object]:
    return {
        "requestId": value.request_id,
        "acceptance": value.acceptance,
        "submittedAt": value.submitted_at,
        "vendorCorrelationId": value.vendor_correlation_id,
        "acceptedAt": value.accepted_at,
        "detail": value.detail,
        "raw": dict(value.raw),
        "bridgeEpoch": value.bridge_epoch,
    }


def reconciliation_json(value: ReconciliationResult) -> dict[str, object]:
    return {
        "requestId": value.request_id,
        "state": value.state,
        "reconciledAt": value.reconciled_at,
        "vendorCorrelationId": value.vendor_correlation_id,
        "detail": value.detail,
        "raw": dict(value.raw),
        "bridgeEpoch": value.bridge_epoch,
        "submissionState": value.submission_state,
    }


def public_receipt_json(value: SubmissionReceipt) -> dict[str, object]:
    """Serialize normalized submission evidence without internal vendor diagnostics."""

    return {
        "requestId": value.request_id,
        "acceptance": value.acceptance,
        "submittedAt": value.submitted_at,
        "vendorCorrelationId": value.vendor_correlation_id,
        "acceptedAt": value.accepted_at,
        "detail": value.detail,
        "bridgeEpoch": value.bridge_epoch,
    }


def public_reconciliation_json(value: ReconciliationResult) -> dict[str, object]:
    """Serialize normalized reconciliation evidence without internal vendor diagnostics."""

    return {
        "requestId": value.request_id,
        "state": value.state,
        "reconciledAt": value.reconciled_at,
        "vendorCorrelationId": value.vendor_correlation_id,
        "detail": value.detail,
        "bridgeEpoch": value.bridge_epoch,
        "submissionState": value.submission_state,
    }


def submission_authority_json(value: SubmissionAuthorityDescriptor) -> dict[str, str]:
    return {"bridgeEpoch": value.bridge_epoch}


def submission_status_json(value: SubmissionStatus) -> dict[str, object]:
    """Raw-free public status for one exact caller-owned cockpit request id."""

    return {
        "state": value.state,
        "submittedAt": value.submitted_at,
        "updatedAt": value.updated_at,
        "acceptedAt": value.accepted_at,
        "withdrawable": value.withdrawable,
        "detail": value.detail,
    }


def submission_lookup_json(value: SubmissionLookup) -> dict[str, object]:
    if value.outcome == "not-found":
        return {"requestId": value.request_id, "outcome": "not-found"}
    if value.submission is None:
        raise HarnessControlError("found submission lookup requires submission evidence")
    return {
        "requestId": value.request_id,
        "outcome": "found",
        "submission": submission_status_json(value.submission),
    }


def submission_status_batch_json(value: SubmissionStatusBatch) -> dict[str, object]:
    return {
        "bridgeEpoch": value.bridge_epoch,
        "submissions": [submission_lookup_json(item) for item in value.submissions],
    }


def withdrawal_result_json(value: WithdrawalResult) -> dict[str, object]:
    result: dict[str, object] = {
        "requestId": value.request_id,
        "outcome": value.outcome,
        "state": value.state,
        "withdrawnAt": value.withdrawn_at,
        "detail": value.detail,
    }
    if value.recovery is not None:
        # Additive optional key: present only at the one true withdrawal transition.
        result["recovery"] = withdrawal_recovery_json(value.recovery)
    return result


def asset_reference_json(value: AssetReference) -> dict[str, object]:
    """Serialize the wire identity; the runner-local spool path never crosses."""

    return {
        "assetId": value.asset_id,
        "mimeType": value.mime_type,
        "byteSize": value.byte_size,
        "sha256": value.sha256,
    }


def withdrawal_recovery_json(value: WithdrawalRecovery) -> dict[str, object]:
    return {
        "text": value.text,
        "assets": [asset_reference_json(asset) for asset in value.assets],
    }


def interrupt_result_json(value: InterruptResult) -> dict[str, object]:
    return {
        "acknowledgement": value.acknowledgement,
        "bridgeEpoch": value.bridge_epoch,
        "operation": value.operation.to_json() if value.operation is not None else None,
        "vendorCorrelationId": value.vendor_correlation_id,
        "detail": value.detail,
        "raw": dict(value.raw),
    }


def operation_timeline_item_json(value: OperationTimelineItem) -> dict[str, object]:
    return {
        "operationId": value.operation_id,
        "kind": value.kind,
        "source": value.source,
        "state": value.state,
        "sequence": value.sequence,
        "submittedAt": value.submitted_at,
        "updatedAt": value.updated_at,
        "acceptedAt": value.accepted_at,
        "payloadDigestPresent": value.payload_digest_present,
        "vendorCorrelationId": value.vendor_correlation_id,
    }


def operation_timeline_json(value: OperationTimeline) -> dict[str, object]:
    return {
        "bridgeEpoch": value.bridge_epoch,
        "latestSequence": value.latest_sequence,
        "evictedBeforeSequence": value.evicted_before_sequence,
        "truncated": value.truncated,
        "items": [operation_timeline_item_json(item) for item in value.items],
    }


def operation_timeline_item_wire_bytes(value: OperationTimelineItem) -> int:
    return _serialized_size(operation_timeline_item_json(value))


def read_asset_bytes(path: Path) -> tuple[str, int, bytes]:
    """Read one confined spool file; return (sha256 hex, size, bytes), typed on any failure."""

    try:
        data = path.read_bytes()
    except (OSError, ValueError) as exc:
        raise HarnessControlError("asset is not readable inside the session asset spool") from exc
    return hashlib.sha256(data).hexdigest(), len(data), data


def _required_text(raw: Mapping[str, object], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise HarnessControlError(f"control payload requires non-empty {key}")
    return value
