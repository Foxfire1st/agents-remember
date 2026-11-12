"""Strict response model for ``explainer_control`` (MIK-R186 rule 6, MIK-R189 rule 7).

One answer for three actions. It carries the state reached, the seat record the action read or
wrote, the architect id the action was accepted from or refused for, and -- for ``claim`` -- what
the report move did. ``update`` and ``retire`` carry the seat service's own record unchanged; a
refusal names its reason and says how to take the master over.
"""

from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import Field, StrictStr, model_validator

from agents_remember.models.base import StrictResponseModel, ToolResponse

MAX_ARCHITECT_ID = 128
MAX_EXPLAINER_CONTROL_LIST = 200
VALID_EXPLAINER_CONTROL_ACTIONS = ("update", "retire", "claim")


class ExplainerControlRecord(StrictResponseModel):
    """The master-to-architect record: the id it names, the act that wrote it, and when."""

    architectAgentId: StrictStr = Field(min_length=1, max_length=MAX_ARCHITECT_ID)
    act: Literal["manager-start", "claim"]
    at: StrictStr = Field(min_length=1)


class ExplainerControlClaim(StrictResponseModel):
    """What a ``claim`` did with the previous seat's held reports for this master."""

    found: int = Field(default=0, ge=0)
    withdrawn: list[str] = Field(default_factory=list, max_length=MAX_EXPLAINER_CONTROL_LIST)
    sent: int = Field(default=0, ge=0)
    held: int = Field(default=0, ge=0)
    unknown: int = Field(default=0, ge=0)
    notReported: int = Field(default=0, ge=0)
    detail: StrictStr | None = None


class ExplainerControlResponse(ToolResponse):
    """One ``explainer_control`` call: updated, current, retired, claimed or refused."""

    operation: Literal["explainer_control"] = "explainer_control"
    action: StrictStr
    state: Literal["updated", "current", "retired", "claimed", "refused"]
    repository: StrictStr
    master: StrictStr
    leaf: StrictStr | None = None
    #: The seat this call was accepted from, or the one it was refused against.
    architectAgentId: StrictStr | None = Field(default=None, max_length=MAX_ARCHITECT_ID)
    record: ExplainerControlRecord | None = None
    update: dict[str, Any] | None = None
    retire: dict[str, Any] | None = None
    claim: ExplainerControlClaim | None = None
    #: A refusal's own name; the answer's ``code`` carries the same word (MIK-R108 rule 1).
    reason: StrictStr | None = None
    detail: StrictStr | None = None
    code: StrictStr | None = None

    @model_validator(mode="after")
    def _validate_admitted_action(self) -> Self:
        if self.state != "refused" and self.action not in VALID_EXPLAINER_CONTROL_ACTIONS:
            raise ValueError(
                f"admitted explainer_control state {self.state!r} requires action in "
                f"{VALID_EXPLAINER_CONTROL_ACTIONS}, got {self.action!r}"
            )
        return self
