"""Response models for orchestration helper tools."""

from __future__ import annotations

from typing import Literal

from agents_remember.models.base import ToolResponse
from agents_remember.models.operator_inbox import InboxDeliveryState

NudgeReason = Literal["inactive", "missing-turn-report", "manual"]
NudgeState = Literal["sent", "rate-limited"]


class OrchestrationNudgeManagerResponse(ToolResponse):
    """``orchestration_nudge_manager``: rate-limited manager stdin nudge."""

    operation: Literal["orchestration_nudge_manager"] = "orchestration_nudge_manager"
    status: NudgeState
    reason: NudgeReason
    nudgeId: str
    entryId: str | None = None
    deliveryState: InboxDeliveryState | None = None
    deliveredToSession: str | None = None
    message: str
