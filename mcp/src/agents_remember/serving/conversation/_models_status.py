from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Literal, TypeAlias

from pydantic import Field, model_validator

from agents_remember.serving.conversation._models_blocks import ConversationItem
from agents_remember.serving.conversation._models_wire import (
    ActiveConversationRef,
    ActiveEventCursor,
    ActivePageCursor,
    CapabilityState,
    HarnessId,
    LibraryConversationKey,
    LibraryListCursor,
    LibraryReadCursor,
    NativeConversationRef,
    NonEmptyText,
    PositiveOrdinal,
    PositiveRevision,
    ProvenanceEvidence,
    WireModel,
)

ConversationProcessState = Literal["starting", "connected", "disconnected", "exited", "failed"]
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

CANONICAL_TURN_STATE_BY_EVIDENCE: Mapping[CanonicalStatusEvidence, ConversationTurnState] = {
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
    # Nullable AND defaulted, because the active/control serializers dump with
    # ``exclude_none=True``: a null is DROPPED from the wire, so a required-but-nullable
    # field made this model unable to validate its own emitted body -- which the response
    # conformance suite found the moment the routes started declaring these models. The
    # wire is unchanged; the absent key already meant exactly this ``None``.
    last_evidence_at: str | None = None
    age_ms: int | None = Field(default=None, ge=0)
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
    # Nullable AND defaulted, because the active/control serializers dump with
    # ``exclude_none=True``: a null is DROPPED from the wire, so a required-but-nullable
    # field made this model unable to validate its own emitted body -- which the response
    # conformance suite found the moment the routes started declaring these models. The
    # wire is unchanged; the absent key already meant exactly this ``None``.
    turn_id: str | None = None
    state_since: str | None = None
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
        allowed_outcomes: Mapping[ConversationTurnState, frozenset[str | None]] = {
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
    # 260731-EFA-L7 R10: verbatim L7 split (L7-OQ1 Option A serving scope); unchanged edge branch, out of this leaf's behavior scope (mcp/src/agents_remember/serving/conversation/_models_status.py:175).
    def require_next_revision(self) -> AppendBlockDeltaMutation:  # pragma: no cover
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
    # 260731-EFA-L7 R10: verbatim L7 split (L7-OQ1 Option A serving scope); unchanged edge branch, out of this leaf's behavior scope (mcp/src/agents_remember/serving/conversation/_models_status.py:194).
    def require_honest_total(self) -> ReplacePageMutation:  # pragma: no cover
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


ConversationMutation: TypeAlias = Annotated[
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
    # Nullable AND defaulted, because the active/control serializers dump with
    # ``exclude_none=True``: a null is DROPPED from the wire, so a required-but-nullable
    # field made this model unable to validate its own emitted body -- which the response
    # conformance suite found the moment the routes started declaring these models. The
    # wire is unchanged; the absent key already meant exactly this ``None``.
    previous_cursor: ActiveEventCursor | None = None
    sequence: PositiveOrdinal
    event_id: NonEmptyText
    emitted_at: NonEmptyText
    delivery: Literal["live", "resume-replay", "native-rehydrate"]
    mutation: ConversationMutation

    @model_validator(mode="after")
    # 260731-EFA-L7 R10: verbatim L7 split (L7-OQ1 Option A serving scope); unchanged edge branch, out of this leaf's behavior scope (mcp/src/agents_remember/serving/conversation/_models_status.py:246).
    def reject_self_predecessor(self) -> ConversationEventEnvelope:  # pragma: no cover
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

    # NOTE (260718-CHATS-L5F R4, developer ruling 2026-07-21): there is deliberately NO
    # ``for_observed_runtime`` version-demotion here. The contract is the only gate; a capability is
    # never demoted because an installed runtime/helper version drifts from the fixture's captured
    # version. The runtime/helper version survives on ``CapabilityEvidence`` as informational
    # metadata only. A capability demotes solely when its contract fails verification or was never
    # probed — expressed directly by the builder that mints it.


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
    # Nullable AND defaulted, because the active/control serializers dump with
    # ``exclude_none=True``: a null is DROPPED from the wire, so a required-but-nullable
    # field made this model unable to validate its own emitted body -- which the response
    # conformance suite found the moment the routes started declaring these models. The
    # wire is unchanged; the absent key already meant exactly this ``None``.
    older_cursor: ActivePageCursor | None = None
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


class ConversationLibraryAgentRow(WireModel):
    """One harness sub-agent conversation grouped under its parent library row.

    Additive and evidence-bound: the agent opens through its own ``conversation_key`` exactly
    like a top-level row. Identity fields are populated only from native evidence — codex
    ``agentNickname``/``agentRole``/``source.subAgent.thread_spawn.agent_path``, claude the
    ``.meta.json`` ``agentType``/``description``/``model`` (``join_key`` = ``toolUseId``). When
    the wire carries none, ``title`` falls back to ``agent <short-id>``, never a fabricated name.
    """

    conversation_key: LibraryConversationKey
    identity_digest: NonEmptyText
    title: NonEmptyText
    agent_path: str | None = None
    nickname: str | None = None
    role: str | None = None
    model: str | None = None
    join_key: str | None = None
    safe_native_id_suffix: str | None = None
    last_activity_at: str | None = None


class ConversationLibraryRow(WireModel):
    conversation_key: LibraryConversationKey
    identity_digest: NonEmptyText
    title: NonEmptyText
    safe_native_id_suffix: str | None = None
    last_activity_at: str | None = None
    capabilities: HistoryCapabilities
    agents: tuple[ConversationLibraryAgentRow, ...] = ()


class ConversationLibraryPageScope(WireModel):
    harness_id: HarnessId
    canonical_project_scope: NonEmptyText
    query_digest: NonEmptyText


class ConversationLibraryPage(WireModel):
    scope: ConversationLibraryPageScope
    rows: tuple[ConversationLibraryRow, ...]
    next_cursor: LibraryListCursor | None
    # Capability honesty: why sub-agent conversations are (partially)
    # unavailable on this page, when they are — the exact native reason, never silently absent.
    agents_note: str | None = None


class HistoricalConversationPage(WireModel):
    ref: NativeConversationRef
    items: tuple[ConversationItem, ...]
    older_cursor: LibraryReadCursor | None
    has_older: bool
    total_items: int | None = Field(default=None, ge=0)
    historical_capabilities: HistoryCapabilities
