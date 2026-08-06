from __future__ import annotations

from typing import Annotated, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel, field_validator, model_validator
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
ProvenanceProducer = Literal["operator", "agent-bus", "controlled-terminal", "harness", "system"]
CapabilityState = Literal["supported", "partial", "unavailable", "unverified"]
AccessibleLabelProvenance = Literal["supplied-description", "filename-mime-fallback"]
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
