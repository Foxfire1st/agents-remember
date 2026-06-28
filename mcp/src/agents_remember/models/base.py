"""Shared model primitives for public MCP response contracts."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class StrictResponseModel(BaseModel):
    """Base class for public response models with explicit fields."""

    model_config = ConfigDict(extra="forbid")


class FlexibleResponseModel(BaseModel):
    """Base class for intentionally provider-native diagnostic payloads."""

    model_config = ConfigDict(extra="allow")


class NextStep(StrictResponseModel):
    """The single computed next move for the active lifecycle (task 27).

    Attached to every in-lifecycle tool response at the ``_tool_payload`` choke
    point by the next-step engine (``mcp.tools.next_step``). It mirrors the
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

    def to_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)


class FlexibleToolResponse(FlexibleResponseEnvelope):
    """Flexible operation-bearing response for raw/detail tool surfaces."""

    operation: str
