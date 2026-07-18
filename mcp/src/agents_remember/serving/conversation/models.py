"""Stable wire contracts for native-authoritative structured conversations.

This module is deliberately declaration-heavy.  It owns the one normalized grammar shared by
future active, library, control, browser, and orchestration consumers; it does not project vendor
events or implement a conversation store.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Annotated, ClassVar, Generic, Literal, TypeAlias, TypeVar

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    field_validator,
    model_validator,
)
from pydantic.alias_generators import to_camel

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
ProvenanceProducer = Literal[
    "operator", "agent-bus", "controlled-terminal", "harness", "system"
]
CapabilityState = Literal["supported", "partial", "unavailable", "unverified"]
AccessibleLabelProvenance = Literal[
    "supplied-description", "filename-mime-fallback"
]
NonEmptyText = Annotated[str, Field(min_length=1)]
PositiveRevision = Annotated[int, Field(ge=1)]
PositiveOrdinal = Annotated[int, Field(ge=1)]


class WireModel(BaseModel):
    """Strict immutable camel-case wire model used across serving boundaries."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        extra="forbid",
        frozen=True,
        populate_by_name=True,
    )


class _OpaqueToken(RootModel[str]):
    """Purpose-branded opaque value; signing and persistence belong to later services."""

    model_config = ConfigDict(frozen=True)
    token_prefix: ClassVar[str]

    @field_validator("root")
    @classmethod
    def require_purpose_prefix(cls, value: str) -> str:
        suffix = value.removeprefix(cls.token_prefix)
        if not suffix or suffix == value:
            raise ValueError(f"token must use {cls.token_prefix!r} purpose prefix")
        return value

    def __str__(self) -> str:
        return self.root


class ActivePageCursor(_OpaqueToken):
    token_prefix = "ar-apc1."


class ActiveEventCursor(_OpaqueToken):
    token_prefix = "ar-aec1."


class LibraryListCursor(_OpaqueToken):
    token_prefix = "ar-llc1."


class LibraryReadCursor(_OpaqueToken):
    token_prefix = "ar-lrc1."


class LibraryConversationKey(_OpaqueToken):
    token_prefix = "ar-lck1."


class NativeResumeTarget(_OpaqueToken):
    """Server-private exact native resume target; never a public authorization grant."""

    token_prefix = "ar-nrt1."


class OperationFingerprint(_OpaqueToken):
    token_prefix = "sha256:"

    @field_validator("root")
    @classmethod
    def require_canonical_sha256(cls, value: str) -> str:
        digest = value.removeprefix(cls.token_prefix)
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise ValueError("operation fingerprint must be canonical lowercase SHA-256")
        return value


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


class ActiveCursorBinding(WireModel):
    authorization: AuthorizationBinding
    purpose: Literal["active-page", "active-event"]
    identity: ActiveConversationRef
    projector_generation: NonEmptyText
    schema_version: PositiveRevision = 1


class ConversationLibraryScope(WireModel):
    authorization: AuthorizationBinding
    harness_id: HarnessId
    canonical_project_scope: NonEmptyText
    query_digest: NonEmptyText


class LibraryCursorBinding(WireModel):
    scope: ConversationLibraryScope
    purpose: Literal["library-list", "library-read"]
    catalog_generation: PositiveRevision
    schema_version: PositiveRevision = 1


class LibraryKeyBinding(WireModel):
    scope: ConversationLibraryScope
    identity_digest: NonEmptyText
    catalog_generation: PositiveRevision
    schema_version: PositiveRevision = 1


class ActiveEventResume(WireModel):
    """The two SSE resume sources must name one identical event cursor."""

    after: ActiveEventCursor | None = None
    last_event_id: ActiveEventCursor | None = None

    @model_validator(mode="after")
    def require_one_unambiguous_cursor(self) -> ActiveEventResume:
        if self.after is None and self.last_event_id is None:
            raise ValueError("an active event resume cursor is required")
        if (
            self.after is not None
            and self.last_event_id is not None
            and self.after.root != self.last_event_id.root
        ):
            raise ValueError("cursor-conflict: after and Last-Event-ID differ")
        return self

    @property
    def cursor(self) -> ActiveEventCursor:
        cursor = self.after or self.last_event_id
        assert cursor is not None
        return cursor


class ProvenanceEvidence(WireModel):
    strength: ProvenanceStrength
    origin: NonEmptyText
    producer: ProvenanceProducer | None = None
    observed_at: str | None = None
    evidence_ref: str | None = None
    reason: str | None = None


class MarkdownBlock(WireModel):
    block_id: NonEmptyText
    type: Literal["markdown"] = "markdown"
    markdown: str


class TextBlock(WireModel):
    block_id: NonEmptyText
    type: Literal["text"] = "text"
    text: str


class ThinkingBlock(WireModel):
    block_id: NonEmptyText
    type: Literal["thinking"] = "thinking"
    markdown: str


class CodeBlock(WireModel):
    block_id: NonEmptyText
    type: Literal["code"] = "code"
    text: str
    language: str | None = None


class ToolInputBlock(WireModel):
    block_id: NonEmptyText
    type: Literal["tool-input"] = "tool-input"
    summary: NonEmptyText
    data: object | None = None


class ToolOutputBlock(WireModel):
    block_id: NonEmptyText
    type: Literal["tool-output"] = "tool-output"
    text: str | None = None
    data: object | None = None


class DiffBlock(WireModel):
    block_id: NonEmptyText
    type: Literal["diff"] = "diff"
    path: str | None = None
    old_text: str | None = None
    new_text: str | None = None
    unified: str | None = None


class ImageReferenceBlock(WireModel):
    block_id: NonEmptyText
    type: Literal["image-ref"] = "image-ref"
    asset_id: NonEmptyText
    alt: NonEmptyText
    alt_provenance: AccessibleLabelProvenance
    mime_type: NonEmptyText


class FileReferenceBlock(WireModel):
    block_id: NonEmptyText
    type: Literal["file-ref", "resource-ref"]
    name: NonEmptyText
    uri: str | None = None
    mime_type: str | None = None


class ChoiceOption(WireModel):
    option_id: NonEmptyText
    label: NonEmptyText
    description: str | None = None


class ChoicesBlock(WireModel):
    block_id: NonEmptyText
    type: Literal["choices"] = "choices"
    interaction_id: NonEmptyText
    options: tuple[ChoiceOption, ...] = Field(min_length=1)


class UnknownVendorBlock(WireModel):
    block_id: NonEmptyText
    type: Literal["unknown-vendor"] = "unknown-vendor"
    vendor_type: NonEmptyText
    safe_summary: NonEmptyText
    evidence_ref: NonEmptyText


ConversationContentBlock: TypeAlias = Annotated[  # noqa: UP040 - Python 3.11 support
    MarkdownBlock
    | TextBlock
    | ThinkingBlock
    | CodeBlock
    | ToolInputBlock
    | ToolOutputBlock
    | DiffBlock
    | ImageReferenceBlock
    | FileReferenceBlock
    | ChoicesBlock
    | UnknownVendorBlock,
    Field(discriminator="type"),
]


class ConversationCorrelation(WireModel):
    request_id: str | None = None
    vendor_correlation_id: str | None = None
    interaction_id: str | None = None
    tool_call_id: str | None = None


class ConversationItem(WireModel):
    item_id: NonEmptyText
    revision: PositiveRevision
    global_ordinal: PositiveOrdinal
    turn_id: str | None = None
    parent_item_id: str | None = None
    lane: ConversationLane
    source: ConversationSource
    provenance: ProvenanceEvidence
    role: ConversationRole
    kind: Literal[
        "message",
        "thinking",
        "plan",
        "tool-call",
        "tool-result",
        "interaction",
        "turn-result",
        "notice",
        "error",
        "telemetry",
        "unknown-vendor",
    ]
    phase: Literal[
        "pending",
        "streaming",
        "waiting",
        "completed",
        "failed",
        "interrupted",
        "unknown",
    ]
    blocks: tuple[ConversationContentBlock, ...]
    correlation: ConversationCorrelation | None = None
    created_at: str | None = None
    updated_at: str | None = None
    evidence_ref: str | None = None

    @model_validator(mode="after")
    def preserve_input_authority(self) -> ConversationItem:
        unique_authorities: Mapping[
            ConversationSource,
            tuple[ConversationLane, ProvenanceProducer, frozenset[ProvenanceStrength]],
        ] = {
            "cockpit-composer": (
                "operator",
                "operator",
                frozenset({"exact", "correlated"}),
            ),
            "durable-inbox": (
                "agent-bus",
                "agent-bus",
                frozenset({"exact", "correlated"}),
            ),
            "terminal-controlled": (
                "operator",
                "controlled-terminal",
                frozenset({"exact", "correlated"}),
            ),
            "interaction-response": (
                "interaction",
                "operator",
                frozenset({"exact"}),
            ),
            "control-authority": (
                "control",
                "system",
                frozenset({"exact", "correlated"}),
            ),
        }
        authority = unique_authorities.get(self.source)
        if authority is not None:
            lane, producer, strengths = authority
            if (
                self.lane != lane
                or self.provenance.producer != producer
                or self.provenance.strength not in strengths
            ):
                raise ValueError(
                    f"{self.source} requires its exact lane/producer/strength authority"
                )
        if self.lane == "unknown-input":
            if self.source != "native-history":
                raise ValueError("unknown-input must remain native-history evidence")
            if self.provenance.strength not in {"native-only", "unknown"}:
                raise ValueError("unknown-input cannot claim exact or correlated provenance")
            if self.provenance.producer is not None:
                raise ValueError("unknown-input cannot claim a producer")
        return self


ConversationProcessState = Literal[
    "starting", "connected", "disconnected", "exited", "failed"
]
ConversationTurnState = Literal[
    "ready",
    "working",
    "waiting",
    "needs-input",
    "settling",
    "retrying",
    "compacting",
    "interrupted",
    "failed",
]
CanonicalStatusEvidence = Literal[
    "settled-dispatchable",
    "active-native-turn",
    "declared-external-wait",
    "pending-interaction",
    "native-end-reconciling",
    "native-retry",
    "native-compaction",
    "interrupt-settled",
    "turn-failed",
]

CANONICAL_TURN_STATE_BY_EVIDENCE: Mapping[
    CanonicalStatusEvidence, ConversationTurnState
] = {
    "settled-dispatchable": "ready",
    "active-native-turn": "working",
    "declared-external-wait": "waiting",
    "pending-interaction": "needs-input",
    "native-end-reconciling": "settling",
    "native-retry": "retrying",
    "native-compaction": "compacting",
    "interrupt-settled": "interrupted",
    "turn-failed": "failed",
}


class StatusFreshness(WireModel):
    state: Literal["fresh", "stale", "unknown"]
    last_evidence_at: str | None
    age_ms: int | None = Field(ge=0)
    stale_after_ms: int = Field(gt=0)
    observation_bound: NonEmptyText


class ConversationProcessStatus(WireModel):
    state: ConversationProcessState
    generation: NonEmptyText
    terminal_outcome: Literal["clean-exit", "failed-exit", "signal", "unknown"] | None = None
    detail: str | None = None


class ConversationTurnWaiting(WireModel):
    reason: NonEmptyText
    interaction_id: str | None = None
    operation_ref: str | None = None


class ConversationTurnOutcome(WireModel):
    state: Literal["completed", "interrupted", "failed", "unknown"]
    stop_reason: str | None = None
    operation_ref: str | None = None


class ConversationTurnStatus(WireModel):
    state: ConversationTurnState
    turn_id: str | None
    state_since: str | None
    waiting: ConversationTurnWaiting | None = None
    terminal_outcome: ConversationTurnOutcome | None = None

    @model_validator(mode="after")
    def require_waiting_evidence(self) -> ConversationTurnStatus:
        if self.state == "waiting":
            if self.waiting is None:
                raise ValueError("waiting requires a reason record")
            if self.waiting.interaction_id is not None:
                raise ValueError("waiting cannot carry an interactionId")
        elif self.state == "needs-input":
            if self.waiting is None or not self.waiting.interaction_id:
                raise ValueError("needs-input requires an exact interactionId")
        elif self.waiting is not None:
            raise ValueError(f"{self.state} cannot carry waiting evidence")
        return self

    @model_validator(mode="after")
    def require_terminal_evidence(self) -> ConversationTurnStatus:
        allowed_outcomes: Mapping[
            ConversationTurnState, frozenset[str | None]
        ] = {
            "ready": frozenset({None, "completed"}),
            "working": frozenset({None}),
            "waiting": frozenset({None}),
            "needs-input": frozenset({None}),
            "settling": frozenset({None, "completed", "unknown"}),
            "retrying": frozenset({None}),
            "compacting": frozenset({None}),
            "interrupted": frozenset({"interrupted"}),
            "failed": frozenset({"failed"}),
        }
        outcome = self.terminal_outcome.state if self.terminal_outcome is not None else None
        if outcome not in allowed_outcomes[self.state]:
            raise ValueError(f"{self.state} has contradictory terminal outcome evidence")
        return self


class ConversationStatusEvidence(ProvenanceEvidence):
    adapter_revision: int | None = Field(default=None, ge=0)
    native_event_cursor: ActiveEventCursor | None = None


class ConversationStatus(WireModel):
    identity: ActiveConversationRef
    revision: PositiveRevision
    observed_at: NonEmptyText
    freshness: StatusFreshness
    process: ConversationProcessStatus
    turn: ConversationTurnStatus
    evidence: ConversationStatusEvidence

    @model_validator(mode="after")
    def reject_false_ready(self) -> ConversationStatus:
        if self.turn.state == "ready" and self.evidence.strength == "unknown":
            raise ValueError("unknown evidence cannot establish ready")
        return self


class AppendItemMutation(WireModel):
    op: Literal["append-item"] = "append-item"
    item: ConversationItem


class AppendBlockDeltaMutation(WireModel):
    op: Literal["append-block-delta"] = "append-block-delta"
    item_id: NonEmptyText
    block_id: NonEmptyText
    expected_revision: PositiveRevision
    next_revision: PositiveRevision
    delta: str

    @model_validator(mode="after")
    def require_next_revision(self) -> AppendBlockDeltaMutation:
        if self.next_revision <= self.expected_revision:
            raise ValueError("nextRevision must advance expectedRevision")
        return self


class UpsertItemMutation(WireModel):
    op: Literal["upsert-item"] = "upsert-item"
    item: ConversationItem


class ReplacePageMutation(WireModel):
    op: Literal["replace-page"] = "replace-page"
    items: tuple[ConversationItem, ...]
    total_items: int | None = Field(default=None, ge=0)
    event_cursor: ActiveEventCursor
    reason: Literal["initial", "reset", "native-rehydrate"]

    @model_validator(mode="after")
    def require_honest_total(self) -> ReplacePageMutation:
        if (
            self.total_items is not None
            and self.items
            and self.total_items < max(item.global_ordinal for item in self.items)
        ):
            raise ValueError("totalItems cannot be lower than a returned globalOrdinal")
        return self


class StatusMutation(WireModel):
    op: Literal["status"] = "status"
    status: ConversationStatus


class GapMutation(WireModel):
    op: Literal["gap"] = "gap"
    requested_after: ActiveEventCursor
    reason: Literal[
        "retention-overflow", "generation-changed", "projector-restart", "ordering-fault"
    ]
    requires_repage: Literal[True] = True
    close_after_event: Literal[True] = True


ConversationMutation: TypeAlias = Annotated[  # noqa: UP040 - Python 3.11 support
    AppendItemMutation
    | AppendBlockDeltaMutation
    | UpsertItemMutation
    | ReplacePageMutation
    | StatusMutation
    | GapMutation,
    Field(discriminator="op"),
]


class ConversationEventEnvelope(WireModel):
    identity: ActiveConversationRef
    cursor: ActiveEventCursor
    previous_cursor: ActiveEventCursor | None
    sequence: PositiveOrdinal
    event_id: NonEmptyText
    emitted_at: NonEmptyText
    delivery: Literal["live", "resume-replay", "native-rehydrate"]
    mutation: ConversationMutation

    @model_validator(mode="after")
    def reject_self_predecessor(self) -> ConversationEventEnvelope:
        if self.previous_cursor is not None and self.previous_cursor.root == self.cursor.root:
            raise ValueError("event cursor cannot equal previousCursor")
        return self


class CapabilityEvidence(WireModel):
    runtime_version: NonEmptyText
    helper_version: str | None = None
    fixture_id: str | None = None
    observed_at: NonEmptyText


class FeatureCapability(WireModel):
    state: CapabilityState
    reason: NonEmptyText
    evidence_tier: Literal["runtime-fixture", "native-declaration", "adapter", "none"]
    evidence: CapabilityEvidence | None = None

    @model_validator(mode="after")
    def require_honest_state_evidence(self) -> FeatureCapability:
        if self.evidence_tier == "none":
            if self.evidence is not None:
                raise ValueError("evidenceTier none cannot carry evidence")
            if self.state != "unavailable":
                raise ValueError("only unavailable capability may have no evidence")
            return self

        if self.evidence is None:
            raise ValueError("an evidence tier requires capability evidence")
        if self.evidence_tier == "runtime-fixture" and not self.evidence.fixture_id:
            raise ValueError("runtime-fixture capability evidence requires fixtureId")
        if self.state in {"supported", "partial"} and self.evidence_tier != "runtime-fixture":
            raise ValueError(f"{self.state} requires production runtime-fixture evidence")
        return self

    def for_observed_runtime(
        self,
        runtime_version: str,
        *,
        helper_version: str | None = None,
    ) -> FeatureCapability:
        """Fail closed when runtime evidence no longer matches the observed process."""

        evidence = self.evidence
        if evidence is None:
            # The state/evidence validator permits this only for an explicitly unavailable
            # feature; absence is its authority and cannot be promoted or demoted by a version.
            return self
        mismatch = evidence.runtime_version != runtime_version
        if evidence.helper_version is not None:
            mismatch = mismatch or evidence.helper_version != helper_version
        if not mismatch:
            return self
        return self.model_copy(
            update={
                "state": "unverified",
                "reason": "observed runtime/helper version differs from capability evidence",
            }
        )


class AttachmentCapability(FeatureCapability):
    allowed_mime_types: tuple[str, ...] = ()
    max_bytes: int = Field(ge=0)
    max_count: int = Field(ge=0)
    description: Literal["required", "filename-type-fallback"]

    @model_validator(mode="after")
    def supported_limits_are_actionable(self) -> AttachmentCapability:
        if self.state == "supported" and (
            not self.allowed_mime_types or self.max_bytes < 1 or self.max_count < 1
        ):
            raise ValueError("supported attachment capability requires MIME/count/byte limits")
        return self


class LiveCapabilities(WireModel):
    text: FeatureCapability
    thinking: FeatureCapability
    tools: FeatureCapability
    diffs: FeatureCapability
    interactions: FeatureCapability
    completeness: FeatureCapability


class HistoryCapabilities(WireModel):
    list: FeatureCapability
    read: FeatureCapability
    resume: FeatureCapability
    completeness: FeatureCapability
    tool_completeness: FeatureCapability


class AttachmentCapabilities(WireModel):
    image: AttachmentCapability
    file: AttachmentCapability
    resource: AttachmentCapability


class ControlCapabilities(WireModel):
    interrupt: FeatureCapability
    steer: FeatureCapability
    follow_up: FeatureCapability
    attachments: AttachmentCapabilities
    policy_read: FeatureCapability


class TelemetryCapabilities(WireModel):
    context: FeatureCapability
    usage: FeatureCapability
    cost: FeatureCapability
    rate_limit: FeatureCapability
    compaction: FeatureCapability


class ConversationCapabilities(WireModel):
    live: LiveCapabilities
    history: HistoryCapabilities
    controls: ControlCapabilities
    telemetry: TelemetryCapabilities


class ConversationPageWindow(WireModel):
    older_cursor: ActivePageCursor | None
    has_older: bool
    total_items: int | None = Field(default=None, ge=0)


class ConversationPage(WireModel):
    identity: ActiveConversationRef
    items: tuple[ConversationItem, ...]
    page: ConversationPageWindow
    event_cursor: ActiveEventCursor
    hydration_id: NonEmptyText
    status: ConversationStatus
    capabilities: ConversationCapabilities


class ConversationLibraryRow(WireModel):
    conversation_key: LibraryConversationKey
    identity_digest: NonEmptyText
    title: NonEmptyText
    safe_native_id_suffix: str | None = None
    last_activity_at: str | None = None
    capabilities: HistoryCapabilities


class ConversationLibraryPageScope(WireModel):
    harness_id: HarnessId
    canonical_project_scope: NonEmptyText
    query_digest: NonEmptyText


class ConversationLibraryPage(WireModel):
    scope: ConversationLibraryPageScope
    rows: tuple[ConversationLibraryRow, ...]
    next_cursor: LibraryListCursor | None


class HistoricalConversationPage(WireModel):
    ref: NativeConversationRef
    items: tuple[ConversationItem, ...]
    older_cursor: LibraryReadCursor | None
    has_older: bool
    total_items: int | None = Field(default=None, ge=0)
    historical_capabilities: HistoryCapabilities


class OpenConversationOperation(WireModel):
    _phases_by_outcome: ClassVar[Mapping[str, frozenset[str]]] = {
        "pending": frozenset({"requested", "launching", "catalog-wait"}),
        "opened": frozenset({"opened"}),
        "unsupported": frozenset({"failed"}),
        "stale-identity": frozenset({"failed"}),
        "launch-failed": frozenset({"retiring", "failed"}),
        "identity-mismatch": frozenset({"retiring", "failed"}),
        "timeout-unknown": frozenset({"launching", "catalog-wait", "unknown"}),
        "request-conflict": frozenset({"failed"}),
    }
    _failure_rollbacks: ClassVar[
        Mapping[tuple[str, str, bool], frozenset[str]]
    ] = {
        ("launch-failed", "failed", False): frozenset({"not-needed"}),
        ("launch-failed", "retiring", True): frozenset({"retire-pending"}),
        ("launch-failed", "failed", True): frozenset({"retired", "retire-failed"}),
        ("identity-mismatch", "retiring", True): frozenset({"retire-pending"}),
        ("identity-mismatch", "failed", True): frozenset({"retired", "retire-failed"}),
    }

    request_id: NonEmptyText
    request_fingerprint: OperationFingerprint
    revision: PositiveRevision
    phase: Literal[
        "requested", "launching", "catalog-wait", "opened", "retiring", "failed", "unknown"
    ]
    outcome: Literal[
        "pending",
        "opened",
        "unsupported",
        "stale-identity",
        "launch-failed",
        "identity-mismatch",
        "timeout-unknown",
        "request-conflict",
    ]
    ar_session_id: str | None = None
    bridge_epoch: str | None = None
    identity: ActiveConversationRef | None = None
    catalog_generation: int | None = Field(default=None, ge=1)
    rollback: Literal["not-needed", "retired", "retire-pending", "retire-failed"]
    detail: str | None = None

    @model_validator(mode="after")
    def require_phase_outcome_product(self) -> OpenConversationOperation:
        if self.phase not in self._phases_by_outcome[self.outcome]:
            raise ValueError("open phase/outcome product is contradictory")
        return self

    @model_validator(mode="after")
    def require_complete_identity_tuple(self) -> OpenConversationOperation:
        identity_parts = (self.ar_session_id, self.bridge_epoch, self.identity)
        if any(part is not None for part in identity_parts) and not all(
            part is not None for part in identity_parts
        ):
            raise ValueError("open identity tuple must be complete")
        return self

    @model_validator(mode="after")
    def require_matching_identity_tuple(self) -> OpenConversationOperation:
        if self.identity is not None and (
            self.ar_session_id != self.identity.ar_session_id
            or self.bridge_epoch != self.identity.bridge_epoch
        ):
            raise ValueError("open identity tuple must agree exactly")
        return self

    @model_validator(mode="after")
    def require_identity_for_catalog_generation(self) -> OpenConversationOperation:
        if self.catalog_generation is not None and self.identity is None:
            raise ValueError("catalog generation requires an exact open identity")
        return self

    @model_validator(mode="after")
    def forbid_identity_for_no_launch_outcome(self) -> OpenConversationOperation:
        if self.outcome in {"unsupported", "stale-identity"} and self.identity is not None:
            raise ValueError(f"{self.outcome} cannot carry a spawned identity")
        return self

    @model_validator(mode="after")
    def require_opened_catalog_proof(self) -> OpenConversationOperation:
        if self.outcome == "opened" and (
            self.identity is None
            or self.catalog_generation is None
            or self.rollback != "not-needed"
        ):
            raise ValueError("opened requires exact identity, catalog proof, and no rollback")
        return self

    @model_validator(mode="after")
    def require_coherent_rollback(self) -> OpenConversationOperation:
        if self.outcome not in {"launch-failed", "identity-mismatch"}:
            if self.rollback != "not-needed":
                raise ValueError("rollback requires an exact failed spawned identity")
            return self

        product = (self.outcome, self.phase, self.identity is not None)
        allowed_rollbacks = self._failure_rollbacks.get(product, frozenset())
        if self.rollback not in allowed_rollbacks:
            raise ValueError("rollback state must agree with failure identity and phase")
        if self.outcome == "identity-mismatch" and self.catalog_generation is None:
            raise ValueError("identity mismatch requires exact catalog generation")
        return self


class InterruptOperation(WireModel):
    request_id: NonEmptyText
    request_fingerprint: OperationFingerprint
    revision: PositiveRevision
    bridge_epoch: NonEmptyText
    turn_id: NonEmptyText
    acknowledgement: Literal["requested", "accepted", "unknown", "rejected"]
    settlement: Literal["pending", "interrupted", "already-settled", "failed"]
    native_correlation_id: str | None = None
    requested_at: NonEmptyText
    settled_at: str | None = None
    detail: str | None = None

    @model_validator(mode="after")
    def require_coherent_acknowledgement_and_settlement(self) -> InterruptOperation:
        allowed_settlements: Mapping[str, frozenset[str]] = {
            "requested": frozenset({"pending"}),
            "accepted": frozenset(
                {"pending", "interrupted", "already-settled", "failed"}
            ),
            "unknown": frozenset(
                {"pending", "interrupted", "already-settled", "failed"}
            ),
            "rejected": frozenset({"failed"}),
        }
        if self.settlement not in allowed_settlements[self.acknowledgement]:
            raise ValueError("interrupt acknowledgement/settlement product is contradictory")
        if (self.settled_at is not None) != (self.settlement != "pending"):
            raise ValueError("interrupt settledAt must match terminal settlement")
        return self


class CockpitQueueIdentity(WireModel):
    withdrawal_ref: NonEmptyText
    redacted_preview: str = Field(max_length=384)
    preview_truncated: bool
    content_digest: NonEmptyText


class OperationQueueItem(WireModel):
    operation_ref: NonEmptyText
    revision: PositiveRevision
    sequence: PositiveOrdinal
    kind: Literal["prompt", "set-model", "set-effort"]
    source: Literal["cockpit", "terminal", "durable"]
    phase: Literal["queued", "dispatching", "unknown"]
    withdrawable: bool
    safe_label: NonEmptyText
    cockpit: CockpitQueueIdentity | None = None

    @model_validator(mode="after")
    def protect_queue_source_privacy(self) -> OperationQueueItem:
        authorized = self.source == "cockpit" and self.phase == "queued"
        if self.withdrawable != authorized:
            raise ValueError("only a queued cockpit operation can be withdrawable")
        if (self.cockpit is not None) != authorized:
            raise ValueError("cockpit withdrawal identity is private to a withdrawable cockpit row")
        return self


class OperationQueueProjection(WireModel):
    bridge_epoch: NonEmptyText
    revision: PositiveRevision
    items: tuple[OperationQueueItem, ...]


class WithdrawQueueRequest(WireModel):
    operation_ref: NonEmptyText
    withdrawal_ref: NonEmptyText
    withdraw_request_id: NonEmptyText


class AttachmentRecoveryRef(WireModel):
    recovery_asset_ref: NonEmptyText
    revision: PositiveRevision
    kind: Literal["image", "file", "resource"]
    name: NonEmptyText
    mime_type: NonEmptyText
    size_bytes: int = Field(ge=0)
    sha256: NonEmptyText
    alt: NonEmptyText
    alt_provenance: AccessibleLabelProvenance
    expires_at: NonEmptyText


class WithdrawalRecovery(WireModel):
    recovery_ref: NonEmptyText
    text: str
    content_digest: NonEmptyText
    submitted_draft_revision: int | None = Field(default=None, ge=0)
    attachments: tuple[AttachmentRecoveryRef, ...]


class WithdrawnQueueResponse(WireModel):
    withdraw_request_id: NonEmptyText
    request_fingerprint: OperationFingerprint
    revision: PositiveRevision
    outcome: Literal["withdrawn"] = "withdrawn"
    operation_ref: NonEmptyText
    withdrawn_at: NonEmptyText
    recovery: WithdrawalRecovery


class FailedWithdrawalResponse(WireModel):
    withdraw_request_id: NonEmptyText
    request_fingerprint: OperationFingerprint
    revision: PositiveRevision
    outcome: Literal[
        "already-dispatching",
        "delivery-unknown",
        "epoch-mismatch",
        "not-found",
        "request-conflict",
    ]
    operation_ref: NonEmptyText
    detail: NonEmptyText


WithdrawQueueResponse: TypeAlias = Annotated[  # noqa: UP040 - Python 3.11 support
    WithdrawnQueueResponse | FailedWithdrawalResponse,
    Field(discriminator="outcome"),
]


class WithdrawalOperationProjection(WireModel):
    _allowed_products: ClassVar[frozenset[tuple[str, str, str]]] = frozenset(
        {
            ("requested", "pending", "none"),
            ("linearizing", "pending", "none"),
            ("unknown", "delivery-unknown", "none"),
            ("settled", "withdrawn", "recovery-unacknowledged"),
            ("settled", "withdrawn", "acknowledged"),
            ("settled", "withdrawn", "expired"),
            ("settled", "already-dispatching", "none"),
            ("settled", "epoch-mismatch", "none"),
            ("settled", "not-found", "none"),
            ("settled", "request-conflict", "none"),
        }
    )

    withdraw_request_id: NonEmptyText
    request_fingerprint: OperationFingerprint
    operation_ref: NonEmptyText
    revision: PositiveRevision
    phase: Literal["requested", "linearizing", "settled", "unknown"]
    outcome: Literal[
        "pending",
        "withdrawn",
        "already-dispatching",
        "delivery-unknown",
        "epoch-mismatch",
        "not-found",
        "request-conflict",
    ]
    recovery_state: Literal[
        "none", "recovery-unacknowledged", "acknowledged", "expired"
    ]
    recovery_expires_at: str | None = None

    @model_validator(mode="after")
    def require_phase_outcome_recovery_product(self) -> WithdrawalOperationProjection:
        product = (self.phase, self.outcome, self.recovery_state)
        if product not in self._allowed_products:
            raise ValueError("withdrawal phase/outcome/recovery product is contradictory")
        return self

    @model_validator(mode="after")
    def require_coherent_recovery_expiry(self) -> WithdrawalOperationProjection:
        if (
            self.recovery_state == "recovery-unacknowledged"
            and self.recovery_expires_at is None
        ):
            raise ValueError("unacknowledged recovery requires expiry")
        if self.recovery_state == "none" and self.recovery_expires_at is not None:
            raise ValueError("non-recovery withdrawal cannot carry recovery expiry")
        return self


class PendingWithdrawalRecoveryProjection(WireModel):
    recovery_ref: NonEmptyText
    operation_ref: NonEmptyText
    withdraw_request_id: NonEmptyText
    revision: PositiveRevision
    state: Literal["recovery-unacknowledged"] = "recovery-unacknowledged"
    recovery_expires_at: NonEmptyText


class PendingWithdrawalRecoveryList(WireModel):
    bridge_epoch: NonEmptyText
    revision: PositiveRevision
    items: tuple[PendingWithdrawalRecoveryProjection, ...]


class AttachmentReceipt(WireModel):
    asset_id: NonEmptyText
    request_id: NonEmptyText
    request_fingerprint: OperationFingerprint
    revision: PositiveRevision
    ar_session_id: NonEmptyText
    bridge_epoch: NonEmptyText
    kind: Literal["image", "file", "resource"]
    name: NonEmptyText
    mime_type: NonEmptyText
    size_bytes: int = Field(ge=0)
    sha256: NonEmptyText
    alt: NonEmptyText
    alt_provenance: AccessibleLabelProvenance
    expires_at: NonEmptyText


class TextSubmitBlock(WireModel):
    type: Literal["text"] = "text"
    text: str


class AssetSubmitBlock(WireModel):
    type: Literal["asset-ref"] = "asset-ref"
    asset_id: NonEmptyText
    kind: Literal["image", "file", "resource"]
    name: NonEmptyText
    mime_type: NonEmptyText
    alt: NonEmptyText
    alt_provenance: AccessibleLabelProvenance
    sha256: NonEmptyText


ComposerSubmitBlock: TypeAlias = Annotated[  # noqa: UP040 - Python 3.11 support
    TextSubmitBlock | AssetSubmitBlock, Field(discriminator="type")
]


class ConversationSubmitRequest(WireModel):
    expected_bridge_epoch: NonEmptyText
    request_id: NonEmptyText
    disposition: Literal["next"] = "next"
    content: tuple[ComposerSubmitBlock, ...] = Field(min_length=1)
    draft_revision: int = Field(ge=0)


class AttachmentOperationProjection(WireModel):
    request_id: NonEmptyText
    request_fingerprint: OperationFingerprint
    operation_ref: str | None = None
    revision: PositiveRevision
    phase: Literal[
        "staging",
        "staged",
        "queued",
        "dispatching",
        "accepted",
        "recoverable",
        "failed",
        "expired",
        "unknown",
    ]
    outcome: Literal[
        "pending", "accepted", "rejected", "withdrawn", "failed", "expired", "unknown"
    ]
    asset_ids: tuple[str, ...]
    recovery_expires_at: str | None = None

    @model_validator(mode="after")
    def require_coherent_attachment_state(self) -> AttachmentOperationProjection:
        allowed_outcomes: Mapping[str, frozenset[str]] = {
            "staging": frozenset({"pending"}),
            "staged": frozenset({"pending"}),
            "queued": frozenset({"pending"}),
            "dispatching": frozenset({"pending"}),
            "accepted": frozenset({"accepted"}),
            "recoverable": frozenset({"withdrawn"}),
            "failed": frozenset({"rejected", "failed"}),
            "expired": frozenset({"expired"}),
            "unknown": frozenset({"unknown"}),
        }
        if self.outcome not in allowed_outcomes[self.phase]:
            raise ValueError("attachment phase/outcome product is contradictory")
        if (self.recovery_expires_at is not None) != (self.phase == "recoverable"):
            raise ValueError("attachment recovery expiry requires recoverable phase")
        return self


T = TypeVar("T")


class MetricScope(WireModel):
    kind: Literal["account", "project", "session", "turn", "conversation"]
    safe_id: str | None = None


class MetricEvidence(WireModel, Generic[T]):  # noqa: UP046 - Python 3.11 support
    value: T
    unit: NonEmptyText
    origin: NonEmptyText
    scope: MetricScope
    observed_at: NonEmptyText
    freshness: Literal["fresh", "stale", "unknown"]
    precision: Literal["exact", "estimated", "rounded", "unknown"]
    runtime_version: NonEmptyText
    helper_version: str | None = None
    fixture_id: str | None = None


class ContextMetricValue(WireModel):
    used: int = Field(ge=0)
    limit: int = Field(gt=0)
    percent: float = Field(ge=0)


class UsageMetricValue(WireModel):
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cached_tokens: int | None = Field(default=None, ge=0)


class CostMetricValue(WireModel):
    amount: float = Field(ge=0)
    currency: NonEmptyText


class RateLimitMetricValue(WireModel):
    window_name: NonEmptyText
    remaining: int | None = Field(default=None, ge=0)
    limit: int | None = Field(default=None, ge=0)
    resets_at: str | None = None


class CompactionMetricValue(WireModel):
    phase: Literal["idle", "compacting", "completed"]
    detail: str | None = None


class ConversationTelemetry(WireModel):
    revision: PositiveRevision
    identity: ActiveConversationRef
    context: MetricEvidence[ContextMetricValue] | None = None
    usage: MetricEvidence[UsageMetricValue] | None = None
    cost: MetricEvidence[CostMetricValue] | None = None
    rate_limits: tuple[MetricEvidence[RateLimitMetricValue], ...] | None = None
    compaction: MetricEvidence[CompactionMetricValue] | None = None


class RuntimeFixtureObservation(WireModel):
    operation: NonEmptyText
    shape: tuple[str, ...]
    result: Literal["observed", "partial", "unavailable", "not-exercised"]
    reason: NonEmptyText


class RuntimeFixtureEvidence(WireModel):
    schema_id: Literal["ar-conversation-runtime-fixture/v1"] = Field(alias="schema")
    fixture_id: NonEmptyText
    harness_id: HarnessId
    runtime_version: NonEmptyText
    helper_version: str | None = None
    captured_at: NonEmptyText
    production_seam: NonEmptyText
    redaction_policy: Literal["allowlist-v1"]
    enables_capabilities: Literal[False] = False
    observations: tuple[RuntimeFixtureObservation, ...] = Field(min_length=1)


def operation_fingerprint(
    operation_kind: str,
    authorization: AuthorizationBinding,
    immutable_payload: Mapping[str, object],
) -> OperationFingerprint:
    """Hash canonical immutable request identity without retaining request content."""

    canonical = json.dumps(
        {
            "operationKind": operation_kind,
            "authorization": authorization.model_dump(mode="json", by_alias=True),
            "payload": immutable_payload,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return OperationFingerprint(f"sha256:{hashlib.sha256(canonical).hexdigest()}")
