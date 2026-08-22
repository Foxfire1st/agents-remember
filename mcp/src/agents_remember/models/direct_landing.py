"""Response model for the direct landing operation."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from agents_remember.models.base import ToolResponse
from agents_remember.models.closeout_input import (
    CloseoutCorrectedCall,
    CloseoutInvalidField,
    EffectiveCloseoutInput,
    ResolvedCloseoutPlan,
)


class DirectLandingResponse(ToolResponse):
    """One branch-addressed direct landing result.

    ``state`` names the outcome (``landed`` / ``would-land`` / ``refused``);
    a refusal also carries ``status`` and ``detail`` so the wire shape is
    strict but still reports the fail-closed reason.
    """

    operation: Literal["direct_landing"] = "direct_landing"
    state: str
    summary: str = Field(default="", max_length=8192)
    status: str | None = None
    detail: str | None = Field(default=None, max_length=8192)
    contractPath: str | None = None
    codeCommit: str | None = None
    memoryContentCommit: str | None = None
    ledgerCommit: str | None = None
    dryRun: bool = False
    memory: dict[str, Any] | None = None
    effectiveInput: EffectiveCloseoutInput | None = None
    invalidFields: list[CloseoutInvalidField] | None = None
    resolvedPlan: ResolvedCloseoutPlan | None = None
    correctedCall: CloseoutCorrectedCall | None = None
