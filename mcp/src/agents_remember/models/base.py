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


class ResponseModel(StrictResponseModel):
    """Common fields for modeled MCP responses."""

    ok: bool
    tokens: int = Field(default=0, ge=0)
    tokenizer: str = ""
    tokenCountExact: bool = False

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

    def to_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)


class FlexibleToolResponse(FlexibleResponseEnvelope):
    """Flexible operation-bearing response for raw/detail tool surfaces."""

    operation: str
