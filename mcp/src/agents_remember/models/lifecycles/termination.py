"""Durable cancellation evidence for a lifecycle generation."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class LifecycleCancellationEvidence(BaseModel):
    """Exact live evidence proving terminal cancellation created no Git output."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    state: Literal["proven-unchanged"] = "proven-unchanged"
    operationKind: Literal["closeout", "integrate", "direct-landing"]
    generation: int = Field(ge=1)
    workerExitProven: bool
    expected: dict[str, str]
    observed: dict[str, str]
    provenAt: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def _requires_exact_unchanged_evidence(self) -> LifecycleCancellationEvidence:
        if not self.expected or self.observed != self.expected:
            raise ValueError("cancellation evidence requires exact expected/observed equality")
        return self
