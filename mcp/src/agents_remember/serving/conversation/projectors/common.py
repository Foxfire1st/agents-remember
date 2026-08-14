"""Shared frame-mapping infrastructure for the per-harness active projectors.

A projector never guesses vendor semantics. Every native frame is parsed
against its documented schema with exact required keys; a shape that does not
match becomes ``unknown-vendor`` evidence with its raw payload preserved
server-side (the public item carries only an opaque evidence handle), never a
fabricated message, tool, or control meaning. Mappers are pure: the engine
assigns ordinals, revisions, provenance resolution, and envelope minting.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from agents_remember.models.conversations.content import (
    ConversationAgentRef,
    ConversationItem,
)
from agents_remember.models.conversations.identity import (
    ConversationRole,
    ProvenanceEvidence,
)


class UnmappableShape(ValueError):
    """A native frame failed exact schema parsing; it becomes unknown-vendor."""


ItemPhase = Literal[
    "pending", "streaming", "waiting", "completed", "failed", "interrupted", "unknown"
]
TerminalOutcomeValue = Literal["completed", "interrupted", "failed", "unknown"]


def required_object(value: object, context: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise UnmappableShape(f"{context} must be an object")
    return value


def required_list(value: object, context: str) -> list[object]:
    if not isinstance(value, list):
        raise UnmappableShape(f"{context} must be a list")
    return value


def required_text(value: object, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise UnmappableShape(f"{context} must be non-empty text")
    return value


def optional_text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


@dataclass(frozen=True)
class MappedItem:
    """A fully built item; the engine assigns the real ordinal/revision."""

    item: ConversationItem


@dataclass(frozen=True)
class MappedBlockDelta:
    """A streaming text delta targeting one existing item block."""

    item_id: str
    block_id: str
    delta: str


@dataclass(frozen=True)
class MappedTurnOutcome:
    """A native turn settlement observed on the stream."""

    outcome: Literal["completed", "interrupted", "failed", "unknown"]
    turn_id: str | None = None
    stop_reason: str | None = None


@dataclass(frozen=True)
class MappedUnknownVendor:
    """An unrecognized-but-preserved native shape; engine mints the item."""

    item_id: str
    vendor_type: str
    safe_summary: str
    live: bool = True
    turn_id: str | None = None
    created_at: str | None = None
    parent_item_id: str | None = None
    role: ConversationRole = "system"
    # The multiplexed agent the frame demuxed to: a malformed AGENT-thread frame's preserved evidence belongs to that
    # agent's view, never the parent's. None = the parent conversation.
    agent: ConversationAgentRef | None = None


MapperOutput = MappedItem | MappedBlockDelta | MappedTurnOutcome | MappedUnknownVendor


def provenance(
    *,
    strength: Literal["exact", "correlated", "native-only", "unknown"],
    origin: str,
    producer: Literal["operator", "agent-bus", "controlled-terminal", "harness", "system"]
    | None = None,
    observed_at: str | None = None,
    reason: str | None = None,
) -> ProvenanceEvidence:
    return ProvenanceEvidence(
        strength=strength,
        origin=origin,
        producer=producer,
        observed_at=observed_at,
        reason=reason,
    )


def harness_provenance(
    origin: str,
    *,
    observed_at: str | None = None,
    reason: str | None = None,
) -> ProvenanceEvidence:
    """Native harness-produced record evidence."""

    return provenance(
        strength="native-only",
        origin=origin,
        producer="harness",
        observed_at=observed_at,
        reason=reason,
    )


def unknown_input_provenance(origin: str, *, observed_at: str | None = None) -> ProvenanceEvidence:
    """User-role input whose producer cannot be proven; never defaults to anyone."""

    return ProvenanceEvidence(
        strength="native-only",
        origin=origin,
        observed_at=observed_at,
        reason="input source unavailable",
    )


__all__ = [
    "ItemPhase",
    "MappedBlockDelta",
    "MappedItem",
    "MappedTurnOutcome",
    "MappedUnknownVendor",
    "MapperOutput",
    "TerminalOutcomeValue",
    "UnmappableShape",
    "harness_provenance",
    "optional_text",
    "provenance",
    "required_list",
    "required_object",
    "required_text",
    "unknown_input_provenance",
]
