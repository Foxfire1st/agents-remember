"""Shared control-plane wire contracts consumed by conversation services.

Control/activity/acceptance state, identities, pending interactions,
submission evidence, and their wire serializers. Declaration bodies are
unchanged from the pre-split module.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, cast

from agents_remember.errors import HarnessControlError
from agents_remember.models.conversations.evidence import _serialized_size

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
class InteractionQuestionOption:
    """One selectable option of a structured interaction question page."""

    label: str
    description: str | None = None


@dataclass(frozen=True)
class InteractionQuestion:
    """One structured question page of a vendor interaction (e.g. Claude AskUserQuestion).

    The flattened ``prompt``/``choices`` on :class:`PendingInteraction` stay the legacy
    rendering; this per-page structure is what a structured answer map keys on.
    """

    text: str
    header: str
    options: tuple[InteractionQuestionOption, ...] = ()
    multi_select: bool = False


@dataclass(frozen=True)
class PendingInteraction:
    interaction_id: str
    kind: str
    prompt: str
    created_at: str
    choices: tuple[str, ...] = ()
    raw: Mapping[str, object] = field(default_factory=dict)
    questions: tuple[InteractionQuestion, ...] = ()


@dataclass(frozen=True)
class AdapterSnapshot:
    """Orthogonal control, activity, and acceptance state without erasing vendor detail."""

    identity: ControlIdentity
    control: ControlState
    activity: ActivityState
    acceptance: AcceptanceState
    vendor_session_id: str | None = None
    pending_interaction: PendingInteraction | None = None
    pending_interactions: tuple[PendingInteraction, ...] = ()
    """Multiplexed pending interactions across threads.

    Codex sub-agent threads raise their own server->client requests (approvals);
    each entry carries its thread identity in ``raw['threadId']`` plus the agent
    label evidence the adapter could bind. The singular ``pending_interaction``
    stays the parent-thread slot for back-compat; consumers that understand the
    multiplexed form read this tuple.
    """

    last_event_sequence: int = 0
    raw: Mapping[str, object] = field(default_factory=dict)

    @property
    def ar_session_id(self) -> str:
        return self.identity.ar_session_id


@dataclass(frozen=True)
class AssetReference:
    """One staged asset's verified identity; ``spool_path`` is runner-local and never serialized."""

    asset_id: str
    mime_type: str
    byte_size: int
    sha256: str
    spool_path: Path | None = None


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


@dataclass(frozen=True)
class SubmissionAuthorityDescriptor:
    """The one bridge generation the submission authority currently accepts."""

    bridge_epoch: str


def interaction_question_json(value: InteractionQuestion) -> dict[str, object]:
    return {
        "text": value.text,
        "header": value.header,
        "options": [
            {"label": option.label, "description": option.description} for option in value.options
        ],
        "multiSelect": value.multi_select,
    }


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
        "questions": [interaction_question_json(question) for question in value.questions],
    }


def snapshot_json(value: AdapterSnapshot) -> dict[str, object]:
    return {
        "identity": value.identity.to_json(),
        "control": value.control,
        "activity": value.activity,
        "acceptance": value.acceptance,
        "vendorSessionId": value.vendor_session_id,
        "pendingInteraction": pending_interaction_json(value.pending_interaction),
        # Multiplexed sub-agent pendings: additive; the singular
        # slot above stays the parent-thread entry exactly as before.
        "pendingInteractions": [
            pending_interaction_json(pending) for pending in value.pending_interactions
        ],
        "lastEventSequence": value.last_event_sequence,
        "raw": dict(value.raw),
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


@dataclass(frozen=True)
class ControlSubmission:
    """Everything about one submission EXCEPT its text: who sent it, as what, against which epoch.

    The request id makes the submission idempotent, the source decides which authority owns it, the
    epoch is the bridge generation it is valid against, and the assets are the bytes it references.
    They are one envelope: a request id replayed with a different source or epoch is a different
    submission, and the wire payload is built from all of them at once.
    """

    source: SubmissionSource
    request_id: str
    submitted_at: str | None = None
    expected_bridge_epoch: str | None = None
    assets: Sequence[Mapping[str, object]] | None = None
