from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Generic, Literal, TypeVar

from pydantic import Field

from agents_remember.serving.conversation._models_wire import (
    ActiveConversationRef,
    AuthorizationBinding,
    HarnessId,
    NonEmptyText,
    OperationFingerprint,
    PositiveRevision,
    WireModel,
)

T = TypeVar("T")


class MetricScope(WireModel):
    kind: Literal["account", "project", "session", "turn", "conversation"]
    safe_id: str | None = None


class MetricEvidence(WireModel, Generic[T]):
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
