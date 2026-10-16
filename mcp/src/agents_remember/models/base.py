"""Shared model primitives for public MCP response contracts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

_RESERVED_DECISION_WIRE_KEYS = frozenset({"developer_decision_required", "decision_surface"})


class StrictResponseModel(BaseModel):
    """Base class for public response models with explicit fields."""

    model_config = ConfigDict(extra="forbid")


class FlexibleResponseModel(BaseModel):
    """Base class for intentionally provider-native diagnostic payloads."""

    model_config = ConfigDict(extra="allow")

    @model_validator(mode="before")
    @classmethod
    def _reject_reserved_snake_decision_keys(cls, value: Any) -> Any:
        """Keep camelCase decision vocabulary singular without narrowing other extras."""

        _require_no_reserved_decision_keys(value)
        return value


def _require_no_reserved_decision_keys(value: object) -> None:
    if isinstance(value, Mapping):
        rejected = sorted(_RESERVED_DECISION_WIRE_KEYS.intersection(value))
        if rejected:
            raise ValueError(
                "reserved lifecycle decision keys must use camelCase: " + ", ".join(rejected)
            )
        for nested in value.values():
            _require_no_reserved_decision_keys(nested)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for nested in value:
            _require_no_reserved_decision_keys(nested)


class NextStep(StrictResponseModel):
    """Typed next-step guidance for a tool response.

    Producers and contract guidance use this shape. Strict, so it is a real
    contract; all fields but ``summary`` are optional.
    """

    summary: str
    nextOperation: str | None = None
    nextTool: str | None = None
    nextArgs: dict[str, Any] | None = None
    nextRequiredArgs: list[str] | None = None


class ResponseModel(StrictResponseModel):
    """Common fields for modeled MCP responses."""

    ok: bool
    tokens: int = Field(default=0, ge=0)
    tokenizer: str = ""
    tokenCountExact: bool = False
    # The next-step hint supplied by a producer or resolved from a contract at
    # the tool-answer boundary. Omitted when no guidance applies.
    nextStep: NextStep | None = None
    # The next move the answer carries (MIK-R108): the producers keep writing
    # ``nextStep`` onto the validated response, and the choke point translates it onto the
    # answer as ``next``. Optional (``exclude_none``) so answers without guidance stay
    # unchanged.
    next: dict[str, Any] | None = None
    # The tool-log detail location (MIK-R108) and a one-sentence writer failure. The choke
    # point writes the detail file path onto the answer so the caller holds what it needs
    # next; declared here for the same reason ``nextStep`` is: a key the choke point writes
    # is a key of THIS envelope. Optional -- an answer with no detail file emits neither.
    log: str | None = None
    logError: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)


class ToolResponse(ResponseModel):
    """Common envelope fields emitted by operation-bearing tool responses."""

    operation: str


class FlexibleResponseEnvelope(FlexibleResponseModel):
    """Flexible response envelope for intentionally native/detail payloads."""

    ok: bool
    tokens: int = Field(default=0, ge=0)
    tokenizer: str = ""
    tokenCountExact: bool = False
    # Same lifecycle next-step hint as the strict envelope (task 27).
    nextStep: NextStep | None = None
    # Same answer-carried lifecycle next move as the strict envelope (MIK-R108).
    next: dict[str, Any] | None = None
    # Same tool-log detail location and writer failure as the strict envelope: a
    # tolerated-drift surface tolerates the PROVIDER's fields, not ours.
    log: str | None = None
    logError: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)


class FlexibleToolResponse(FlexibleResponseEnvelope):
    """Flexible operation-bearing response for raw/detail tool surfaces."""

    operation: str


type ResponseEnvelope = ResponseModel | FlexibleResponseEnvelope
"""The two envelope families every registered tool response belongs to.

The strict/flexible split is about ``extra``, not about the envelope: both families
carry the same ``ok``/``tokens``/``nextStep`` header. Naming the
union lets ``models.tool_registry`` say what it holds, which is what lets
``_tool_payload`` set the choke-point field on the validated response *before*
dumping it rather than writing it into the dump afterwards.
"""
