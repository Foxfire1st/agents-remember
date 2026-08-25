"""Public closeout-door response joins, separate from canonical door source schema."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from agents_remember.models.base import ToolResponse
from agents_remember.models.closeout.projection import TaskDocProjectionEffect
from agents_remember.models.lifecycles.door import (
    CloseoutDoorAction,
    CloseoutDoorGeneration,
)
from agents_remember.models.lifecycles.operation import LifecycleOperationProjection


class CloseoutDoorResponse(ToolResponse):
    operation: Literal["closeout_door"] = "closeout_door"
    action: CloseoutDoorAction
    state: str = Field(max_length=256)
    summary: str = Field(max_length=8192)
    contractPath: str = Field(max_length=8192)
    generation: CloseoutDoorGeneration | None = None
    projectionEffects: list[TaskDocProjectionEffect] = Field(default_factory=list, max_length=8)
    status: str | None = Field(default=None, max_length=256)
    detail: str | None = Field(default=None, max_length=8192)
    expected: dict[str, Any] | None = Field(default=None, max_length=32)
    observed: dict[str, Any] | None = Field(default=None, max_length=32)
    developerDecisionRequired: bool | None = None
    decisionSurface: str | None = Field(default=None, max_length=8192)
    nextAction: str | None = Field(default=None, max_length=8192)
    lifecycleOperation: LifecycleOperationProjection | None = None
    lifecycleOperations: list[LifecycleOperationProjection] | None = Field(
        default=None,
        max_length=16,
    )

    @model_validator(mode="after")
    def _failure_shape_is_coherent(self) -> CloseoutDoorResponse:
        if self.ok:
            if self.status is not None or self.detail is not None:
                raise ValueError("successful door response cannot carry refusal fields")
        elif not self.status or not self.detail or self.state != "refused":
            raise ValueError("failed door response requires typed refused status and detail")
        return self
