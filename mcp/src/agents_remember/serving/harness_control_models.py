"""Protocol-neutral models for one exact hosted harness control session."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, cast

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
class PromptRequest:
    """One immutable whole message; adapters must never merge it at keystroke level."""

    request_id: str
    source: SubmissionSource
    text: str
    submitted_at: str
    operation: ControlOperationRef | None = None
    expected_bridge_epoch: str | None = None


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
class WithdrawalResult:
    request_id: str
    outcome: WithdrawalOutcome
    state: SubmissionLifecycleState | None
    withdrawn_at: str | None = None
    detail: str | None = None


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
    return {
        "requestId": value.request_id,
        "outcome": value.outcome,
        "state": value.state,
        "withdrawnAt": value.withdrawn_at,
        "detail": value.detail,
    }


def _required_text(raw: Mapping[str, object], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise HarnessControlError(f"control payload requires non-empty {key}")
    return value
