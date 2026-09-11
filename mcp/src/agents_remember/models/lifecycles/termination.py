"""Durable worker lease and termination evidence."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

WorkerTerminationState = Literal["requested", "termination-required", "exited"]


class WorkerTerminationEvidence(BaseModel):
    """One monotonic termination request bound to an exact process instance."""

    model_config = ConfigDict(extra="forbid")

    state: WorkerTerminationState
    pid: int = Field(ge=1)
    lease: str = Field(pattern=r"^[0-9a-f]{64}$")
    processFingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    requestedAt: str
    signal: Literal["SIGTERM", "none"] = "SIGTERM"
    observedAt: str | None = None
    detail: str = Field(default="", max_length=8192)
    failureEvidence: dict[str, object] | None = None

    @model_validator(mode="after")
    def _exit_has_observation(self) -> WorkerTerminationEvidence:
        if self.state == "exited" and self.observedAt is None:
            raise ValueError("worker exit proof requires an observation timestamp")
        if self.state != "exited" and self.observedAt is not None:
            raise ValueError("unproven worker termination cannot claim an exit observation")
        return self


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
