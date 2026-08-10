from __future__ import annotations

from typing import Literal

from agents_remember.models.conversations.primitives import (
    NonEmptyText,
    WireModel,
)

HarnessId = Literal["codex", "claude", "pi"]

ConversationLane = Literal[
    "operator",
    "harness",
    "agent-bus",
    "unknown-input",
    "interaction",
    "control",
    "system",
]

ConversationSource = Literal[
    "cockpit-composer",
    "terminal-controlled",
    "durable-inbox",
    "harness-live",
    "harness-replay",
    "interaction-response",
    "control-authority",
    "native-history",
]

ConversationRole = Literal["user", "assistant", "system", "tool"]

ProvenanceStrength = Literal["exact", "correlated", "native-only", "unknown"]

ProvenanceProducer = Literal["operator", "agent-bus", "controlled-terminal", "harness", "system"]

CapabilityState = Literal["supported", "partial", "unavailable", "unverified"]

AccessibleLabelProvenance = Literal["supplied-description", "filename-mime-fallback"]


class NativeConversationRef(WireModel):
    harness_id: HarnessId
    vendor_conversation_id: NonEmptyText
    project_scope: NonEmptyText
    identity_digest: NonEmptyText


class ActiveConversationRef(NativeConversationRef):
    ar_session_id: NonEmptyText
    bridge_epoch: NonEmptyText


class AuthorizationBinding(WireModel):
    principal_id: NonEmptyText
    tenant_id: NonEmptyText


class ConversationLibraryScope(WireModel):
    authorization: AuthorizationBinding
    harness_id: HarnessId
    canonical_project_scope: NonEmptyText
    query_digest: NonEmptyText


class ProvenanceEvidence(WireModel):
    strength: ProvenanceStrength
    origin: NonEmptyText
    producer: ProvenanceProducer | None = None
    observed_at: str | None = None
    evidence_ref: str | None = None
    reason: str | None = None
