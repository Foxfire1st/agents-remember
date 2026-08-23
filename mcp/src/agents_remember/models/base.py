"""Shared model primitives for public MCP response contracts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, TypeAlias

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
    """The single computed next move for the active lifecycle (task 27).

    Attached to every in-lifecycle tool response at the ``_tool_payload`` choke
    point by the next-step engine (``application.next_step``). It mirrors the
    worktree ``guidance.lifecycle_guidance`` shape so operational hints and
    gate-raise hints share one vocabulary: a gate junction is simply
    ``nextTool="lifecycle_gate"`` with ``nextArgs={"kind": ...}``. Strict, so it
    is a real contract; all fields but ``summary`` are optional because the
    non-linear front half carries only prose.
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
    # The lifecycle next-step hint (task 27): populated at the ``_tool_payload``
    # choke point for every response emitted inside an active lifecycle. Optional
    # (``exclude_none``) so lifecycle-less calls stay unchanged.
    nextStep: NextStep | None = None
    # The stale-agent-notifier banner (260707-HFX2-L2 R5), set at the same choke point when
    # the agent-notifier's heartbeat row has gone quiet past the cutoff. Declared here for
    # the same reason ``nextStep`` is: a key the choke point writes is a key of THIS
    # envelope, so the emitted object stays inside its own contract instead of being
    # stamped onto an already-dumped dict. Optional -- a live agent-notifier emits nothing.
    agentNotifierBanner: str | None = None
    # Legacy alias emitted alongside the current key during the rename window; the choke
    # point writes both, and consumers may read either. Removed with the window.
    supervisorBanner: str | None = None

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
    # Same stale-agent-notifier banner as the strict envelope. ``extra="allow"`` would have
    # accepted it undeclared, which is exactly the hole: a tolerated-drift surface tolerates
    # the PROVIDER's fields, not ours. What this package writes, this package declares.
    agentNotifierBanner: str | None = None
    # Legacy alias during the rename window (same value as ``agentNotifierBanner``).
    supervisorBanner: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)


class FlexibleToolResponse(FlexibleResponseEnvelope):
    """Flexible operation-bearing response for raw/detail tool surfaces."""

    operation: str


ResponseEnvelope: TypeAlias = ResponseModel | FlexibleResponseEnvelope
"""The two envelope families every registered tool response belongs to.

The strict/flexible split is about ``extra``, not about the envelope: both families
carry the same ``ok``/``tokens``/``nextStep``/``agentNotifierBanner`` header (plus the
legacy ``supervisorBanner`` alias during the rename window). Naming the
union lets ``models.tool_registry`` say what it holds, which is what lets
``_tool_payload`` set the two choke-point fields on the validated response *before*
dumping it rather than writing them into the dump afterwards.
"""
