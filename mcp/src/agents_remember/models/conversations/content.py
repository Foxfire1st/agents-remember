from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Literal, TypeAlias

from pydantic import Field, model_validator

from agents_remember.models.conversations.identity import (
    AccessibleLabelProvenance,
    ConversationLane,
    ConversationRole,
    ConversationSource,
    ProvenanceEvidence,
    ProvenanceProducer,
    ProvenanceStrength,
)
from agents_remember.models.conversations.primitives import (
    NonEmptyText,
    PositiveOrdinal,
    PositiveRevision,
    WireModel,
)


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


ConversationContentBlock: TypeAlias = Annotated[
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


ConversationAgentStatus = Literal[
    "registered", "running", "completed", "interrupted", "failed", "unknown"
]


class ConversationAgentRef(WireModel):
    """The harness sub-agent one timeline item belongs to.

    Additive and optional: absent means the parent conversation. Identity is
    evidence-bound — codex ``agentThreadId`` (plus ``agentPath``/``nickname``/
    ``role`` once collab evidence binds them), claude ``agentId``/
    ``subagent_type`` joined through the spawning tool call (``join_key`` =
    ``parent_tool_use_id``). Unresolved identity renders as ``agent <short-id>``,
    never a fabricated name; ``status`` tracks the agent's own lifecycle, not
    the item's phase.
    """

    agent_id: NonEmptyText
    agent_path: str | None = None
    nickname: str | None = None
    role: str | None = None
    join_key: str | None = None
    parent_agent_id: str | None = None
    status: ConversationAgentStatus = "unknown"


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
    agent: ConversationAgentRef | None = None
    created_at: str | None = None
    updated_at: str | None = None
    evidence_ref: str | None = None

    @model_validator(mode="after")
    def preserve_input_authority(self) -> ConversationItem:  # pragma: no cover
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
