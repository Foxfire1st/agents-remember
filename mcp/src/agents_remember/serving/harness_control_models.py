"""Control-plane-only models for one exact hosted harness control session.

The shared conversation/evidence wire contracts that used to live here now
live under ``models/conversations/evidence.py`` and
``models/conversations/control_wire.py``; this module keeps only the
control-plane-only declarations. Declaration bodies are unchanged.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from agents_remember.errors import HarnessControlError
from agents_remember.models.conversations.control_wire import (
    AdapterSnapshot,
    AssetReference,
    ControlIdentity,
    ControlOperationRef,
    SubmissionAuthorityDescriptor,
    SubmissionLifecycleState,
    SubmissionLookupOutcome,
    SubmissionReceipt,
    SubmissionSource,
)

CONTROL_PROTOCOL_VERSION = "ar-harness-control/v1"


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


MAX_OPERATION_TIMELINE_PAGE = 256
"""Server-side item cap for one operation-timeline page (the retained ledger's own bound)."""


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
