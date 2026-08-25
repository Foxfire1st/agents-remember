"""Canonical closeout commit-message plans and normalized input."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

CloseoutInputRoute = Literal["worktree", "direct-landing"]
CloseoutCommitLegName = Literal["code", "memory", "ledger"]
CloseoutLegState = Literal["enabled", "not-applicable"]
CloseoutPublicMessageField = Literal[
    "code_commit_message",
    "memory_commit_message",
    "ledger_commit_message",
]
CloseoutMessageObservation = Literal[
    "omitted",
    "empty",
    "whitespace-only",
    "stale-or-forged",
]


class CloseoutMessageInput(BaseModel):
    """Untrusted public message observations before plan resolution."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str | None = None
    memory: str | None = None
    ledger: str | None = None


class CloseoutLegPlan(BaseModel):
    """Whether one commit leg can write during the effective lifecycle."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    state: CloseoutLegState
    reason: str


class ResolvedCloseoutPlan(BaseModel):
    """Contract-derived enabledness, independent of caller message validity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    route: CloseoutInputRoute
    contractKind: Literal["leaf", "series"]
    memoryMode: Literal["internal", "external", "disabled"]
    code: CloseoutLegPlan
    memory: CloseoutLegPlan
    ledger: CloseoutLegPlan


class CloseoutInvalidField(BaseModel):
    """One exact public input cell refused under its resolved plan."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    field: CloseoutPublicMessageField | Literal["effectiveInput"]
    leg: Literal["code", "memory", "ledger", "plan"]
    observation: CloseoutMessageObservation
    code: str


class CloseoutCorrectedCall(BaseModel):
    """Sanitized exact call shape returned with a typed input refusal."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tool: str
    arguments: dict[str, object]


class EnabledCloseoutLeg(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: Literal["enabled"] = "enabled"
    reason: str
    message: str

    @field_validator("message")
    @classmethod
    def _require_normalized_message(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("enabled closeout message must be nonblank")
        if normalized != value:
            raise ValueError("enabled closeout message must already be stripped")
        return value


class NotApplicableCloseoutLeg(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: Literal["not-applicable"] = "not-applicable"
    reason: str


EffectiveCloseoutLeg = Annotated[
    EnabledCloseoutLeg | NotApplicableCloseoutLeg,
    Field(discriminator="state"),
]


class EffectiveCloseoutInput(BaseModel):
    """The sole message-bearing input used after closeout validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    route: CloseoutInputRoute
    contractKind: Literal["leaf", "series"]
    memoryMode: Literal["internal", "external", "disabled"]
    code: EffectiveCloseoutLeg
    memory: EffectiveCloseoutLeg
    ledger: EffectiveCloseoutLeg

    def message_for(self, leg: CloseoutCommitLegName) -> str:
        value = getattr(self, leg)
        if not isinstance(value, EnabledCloseoutLeg):
            raise RuntimeError(f"closeout {leg} commit leg is not applicable")
        return value.message

    def enabled(self, leg: CloseoutCommitLegName) -> bool:
        return isinstance(getattr(self, leg), EnabledCloseoutLeg)
