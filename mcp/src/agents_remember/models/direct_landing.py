"""Response model for the direct landing operation."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from agents_remember.models.base import ToolResponse
from agents_remember.models.closeout.input import (
    CloseoutCorrectedCall,
    CloseoutInvalidField,
    EffectiveCloseoutInput,
    ResolvedCloseoutPlan,
)
from agents_remember.models.closeout.projection import TaskDocProjectionEffect
from agents_remember.models.lifecycles.operation import LifecycleOperationProjection


class DirectLandingResponse(ToolResponse):
    """One branch-addressed direct landing result.

    ``state`` names the outcome (``landed`` / ``would-land`` / ``refused``);
    a refusal also carries ``status`` and ``detail`` so the wire shape is
    strict but still reports the fail-closed reason.
    """

    operation: Literal["direct_landing"] = "direct_landing"
    state: Literal["landed", "would-land", "refused"]
    summary: str = Field(default="", max_length=8192)
    status: str | None = None
    detail: str | None = Field(default=None, max_length=8192)
    contractPath: str | None = None
    doorGenerationId: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    projectionEffects: list[TaskDocProjectionEffect] | None = Field(
        default=None,
        max_length=8,
    )
    codeCommit: str | None = None
    memoryContentCommit: str | None = None
    ledgerCommit: str | None = None
    dryRun: bool = False
    memory: dict[str, Any] | None = None
    effectiveInput: EffectiveCloseoutInput | None = None
    invalidFields: list[CloseoutInvalidField] | None = None
    resolvedPlan: ResolvedCloseoutPlan | None = None
    correctedCall: CloseoutCorrectedCall | None = None
    lifecycleOperation: LifecycleOperationProjection | None = None
    expected: dict[str, Any] | None = None
    observed: dict[str, Any] | None = None
    nextAction: str | None = None
    nextTool: str | None = None
    nextArgs: dict[str, Any] | None = None
    developerDecisionRequired: bool | None = None
    decisionSurface: str | None = Field(default=None, max_length=8192)
