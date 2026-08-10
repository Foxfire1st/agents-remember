from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from agents_remember.models.conversations.identity import CapabilityState
from agents_remember.models.conversations.primitives import NonEmptyText, WireModel


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

    # There is deliberately no runtime/helper version-demotion here: the contract is the only
    # gate. A capability is never demoted because an installed runtime/helper version drifts
    # from the fixture's captured version; that version survives on ``CapabilityEvidence`` as
    # informational metadata only. A capability demotes solely when its contract fails
    # verification or was never probed, expressed by the builder that mints it.


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
