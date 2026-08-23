"""Durable lifecycle gate-policy snapshot vocabulary."""

from pydantic import BaseModel, ConfigDict


class GatePolicyRuleSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    delegatedRole: str | None = None
    requireReviewerVerdict: bool = False
